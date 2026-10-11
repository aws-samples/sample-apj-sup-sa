#!/usr/bin/env python
"""
Admin CLI for the dynamic health-intake form schema and the CRM table.

The whole point of this tool: a new health question becomes live on the *next*
call with a single DynamoDB PutItem. No Lambda redeploy, no agent rebuild, no
container push. The agent reads the active item set at the start of every call
and again on every resume.

Usage
  python tools/form_admin.py load-form   seed/form-medical-history-v1.json
  python tools/form_admin.py load-crm    seed/crm-customers.json
  python tools/form_admin.py list        --form-id medical-history-v1
  python tools/form_admin.py add-item    --form-id medical-history-v1 \
                                         --item-id Q450 --order 450 \
                                         --section medications \
                                         --label uses_insulin \
                                         --answer-type boolean \
                                         --prompt "Do you use insulin?" \
                                         --required
  python tools/form_admin.py disable     --form-id medical-history-v1 --item-id Q450

  # a short rehearsal form that reaches the respiratory and diabetes screens,
  # derived from the production seed so its prompts cannot drift:
  python tools/form_admin.py demo-form
  python tools/form_admin.py assign-form --phone +91XXXXXXXXXX \
                                         --form-id arogya-sanjeevani-demo-v1
  python tools/form_admin.py assign-form --phone +91XXXXXXXXXX --reset
"""
from __future__ import annotations

import argparse
import decimal
import json
import os
import sys
from typing import Any

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
TBL_FORM_SCHEMA = os.environ.get("TBL_FORM_SCHEMA", "hvi-form-schema")
TBL_CRM = os.environ.get("TBL_CRM", "hvi-crm-cache")
TBL_CONSENT = os.environ.get("TBL_CONSENT", "hvi-consent-ledger")

ddb = boto3.resource("dynamodb", region_name=REGION)


def _to_ddb(obj: Any) -> Any:
    """Recursively convert floats to Decimal, which is what DynamoDB accepts."""
    if isinstance(obj, float):
        return decimal.Decimal(str(obj))
    if isinstance(obj, list):
        return [_to_ddb(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_ddb(v) for k, v in obj.items()}
    return obj


def _from_ddb(obj: Any) -> Any:
    if isinstance(obj, decimal.Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, list):
        return [_from_ddb(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _from_ddb(v) for k, v in obj.items()}
    return obj


def load_form(path: str) -> None:
    with open(path) as fh:
        doc = json.load(fh)
    form_id = doc["formId"]
    table = ddb.Table(TBL_FORM_SCHEMA)
    count = 0
    with table.batch_writer() as batch:
        for item in doc["items"]:
            record = _to_ddb({**item, "formId": form_id})
            batch.put_item(Item=record)
            count += 1
    # Store a form-level metadata row so the agent can read the description/version.
    table.put_item(
        Item=_to_ddb(
            {
                "formId": form_id,
                "itemId": "__META__",
                "description": doc.get("description", ""),
                "itemCount": count,
                "active": True,
            }
        )
    )
    print(f"loaded {count} items into {TBL_FORM_SCHEMA} for formId={form_id}")


# A SHORT FORM THAT IS STILL A REAL INTAKE, in the production form's own order.
#
# What actually made a live call run out of time was not the NUMBER of questions,
# it was the condition screens: A200 cancer, A210 cardiac and A220 cholesterol
# each open a six-question clinical protocol from the graph on a "yes", and two of
# those consumed the whole call - asthma and diabetes were never reached.
#
# So the cut is by COST, not by section. Only the two condition screens a demo is
# actually about are kept. Everything that costs one question when answered "no"
# stays in, because dropping it buys nothing and makes the demo look like a toy:
# an underwriter would notice immediately that nobody was asked about smoking.
#
#   A010/A020/A030  identity, consent, DOB - hardcoded gates, not optional
#   A100/A110       height and weight - the pair the BMI cross-check needs
#   A230            respiratory: asthma, TB, COPD        } the two screens that
#   A250            diabetes                             } open graph protocols
#   A400/A410       tobacco - which form, how much, how long, on a yes
#   A420/A430       alcohol, asked separately because the loadings differ
#   A500-A550       hospitalisation, investigations, prolonged treatment: reason,
#                   when, resolved, still medicated, which medicines - the deepest
#                   conditional branch in the form, and free unless answered yes
#   A600/A610       an abnormal test result nobody has yet named as a diagnosis
#   A620/A630       family / hereditary history, including carrier state
#   A640/A650       a prior declined, postponed, loaded or excluded proposal
#   A900/A910       the open "anything else?" catch-all and its detail
#   A920            ABHA id - optional, demonstrates an offered escape word
#   A990            closing email confirmation
#
# Dropped: eleven condition screens (A200, A210, A220, A240, A260-A320), every one
# of which can open a six-question protocol mid-call.
#
# Answered "no" throughout this is 12 spoken questions; the conditional children
# only appear when their parent is a yes.
DEMO_KEEP = ("A010", "A020", "A030", "A100", "A110",
             "A230", "A250",
             "A400", "A410", "A420", "A430",
             "A500", "A510", "A520", "A530", "A540", "A550",
             "A600", "A610", "A620", "A630", "A640", "A650",
             "A900", "A910", "A920", "A990")


def demo_form(source: str, form_id: str, keep: tuple[str, ...]) -> None:
    """Write a SHORTENED COPY of a form under a new formId.

    Derived from the production seed at load time rather than committed as a
    second schema file: a hand-maintained copy drifts the moment a prompt is
    reworded, and a demo that asks last month's questions is worse than no demo.
    The production form is not read from or written to at runtime by this - only
    the new formId is written, so switching back is a single assign-form --reset.
    """
    with open(source) as fh:
        doc = json.load(fh)

    by_id = {item["itemId"]: item for item in doc["items"]}
    missing = [item_id for item_id in keep if item_id not in by_id]
    if missing:
        raise SystemExit(f"source form has no item(s): {', '.join(missing)}")

    items = [by_id[item_id] for item_id in keep]

    # A trimmed form must stay structurally closed. A kept child whose parent was
    # dropped can never be shown (showIf cannot evaluate against an answer that
    # is never collected), so it would silently disappear rather than fail - the
    # kind of thing you discover mid-demo.
    kept = set(keep)
    for item in items:
        for ref_field, ref in (("parentItemId", item.get("parentItemId")),
                               ("showIf", (item.get("showIf") or {}).get("itemId"))):
            if ref and ref not in kept:
                raise SystemExit(
                    f"{item['itemId']} references {ref} via {ref_field}, but "
                    f"{ref} is not in the demo set - the item would never be asked")

    table = ddb.Table(TBL_FORM_SCHEMA)

    # REGENERATION MUST BE A REPLACEMENT, NOT A MERGE. Writing only the kept items
    # leaves anything from a previous, longer keep list still sitting under this
    # formId - and get_form_items serves every active row it finds, so a question
    # supposedly removed from the demo would still be asked. Prune first so the
    # stored form is exactly the requested set.
    existing = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("formId").eq(form_id))
    stale = [row["itemId"] for row in existing.get("Items", [])
             if row["itemId"] != "__META__" and row["itemId"] not in kept]
    if stale:
        with table.batch_writer() as batch:
            for item_id in stale:
                batch.delete_item(Key={"formId": form_id, "itemId": item_id})
        print(f"removed {len(stale)} item(s) no longer in the demo set: "
              f"{', '.join(sorted(stale))}")

    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=_to_ddb({**item, "formId": form_id,
                                         "active": True}))
    table.put_item(Item=_to_ddb({
        "formId": form_id, "itemId": "__META__",
        "description": (f"DEMO subset of {doc['formId']}: "
                        f"{', '.join(keep)}. Generated by form_admin.py "
                        f"demo-form; not a hand-maintained schema."),
        "itemCount": len(items), "active": True,
    }))
    print(f"wrote {len(items)} items to {TBL_FORM_SCHEMA} for formId={form_id}")
    for item in items:
        print(f"  {item['itemId']:<6} order={item.get('order'):<4} "
              f"{str(item.get('label'))[:28]}")
    print(f"\nthe source form {doc['formId']} was not modified.")
    print("point a caller at this with:")
    print(f"  python tools/form_admin.py assign-form --phone +91XXXXXXXXXX "
          f"--form-id {form_id}")


def _customer_by_phone(phone: str) -> dict:
    """The CRM-cache record for a phone number, newest first if duplicated."""
    resp = ddb.Table(TBL_CRM).query(
        IndexName="phone-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("phone").eq(phone),
    )
    rows = resp.get("Items") or []
    if not rows:
        raise SystemExit(f"no customer in {TBL_CRM} with phone {phone}")
    if len(rows) > 1:
        # A hash-only GSI has no defined order, so picking the first row picks an
        # arbitrary customer. Same rule the runtime lookup uses.
        print(f"note: {len(rows)} records share {phone}; using the most recently "
              "modified")
    return _from_ddb(max(rows, key=lambda r: (str(r.get("sourceLastModified") or ""),
                                              str(r.get("projectedAt") or ""))))


def _assign_in_salesforce(customer_id: str, form_id: str | None) -> None:
    """Set Contact.HVI_Form_Id__c, which is what the CRM projection reads.

    THE CACHE ALONE DOES NOT HOLD. crm_sync recomputes assignedFormId as
    `HVI_Form_Id__c or DEFAULT_FORM_ID` on every projection, so a DynamoDB-only
    assignment survives exactly until the next sync - which the regression suite
    itself runs. Writing the Salesforce field is what makes the choice stick,
    because it changes the source rather than the copy.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
    from hvi_salesforce import Salesforce  # noqa: PLC0415

    sf = Salesforce()
    field = "HVI_Form_Id__c"
    rows = sf.query(f"SELECT Id, {field} FROM Contact WHERE Id = '{customer_id}'")
    if not rows:
        raise SystemExit(f"no Salesforce Contact {customer_id}")
    before = rows[0].get(field)
    status, body = sf._raw(  # noqa: SLF001
        "PATCH", f"/services/data/v{sf.version}/sobjects/Contact/{customer_id}",
        {field: form_id})
    if status not in (200, 201, 204):
        raise SystemExit(f"could not set {field} on {customer_id}: {status} {body}")
    after = sf.query(
        f"SELECT {field} FROM Contact WHERE Id = '{customer_id}'")[0].get(field)
    print(f"  Salesforce Contact {customer_id}: {field} {before!r} -> {after!r}")
    if after != form_id:
        raise SystemExit(f"{field} did not take the new value (got {after!r})")


def assign_form(phone: str, form_id: str | None, salesforce: bool = False) -> None:
    """Point one caller at a different form, or back at the default.

    Writes the CRM cache, which is what the next call reads. Add --salesforce to
    also write Contact.HVI_Form_Id__c, without which the next CRM sync silently
    reverts the choice - see _assign_in_salesforce.
    """
    customer = _customer_by_phone(phone)
    customer_id = customer["customerId"]
    was = customer.get("assignedFormId")
    table = ddb.Table(TBL_CRM)
    if form_id:
        table.update_item(
            Key={"customerId": customer_id},
            UpdateExpression="SET assignedFormId = :f",
            ExpressionAttributeValues={":f": form_id})
        print(f"{customer_id} ({phone}): assignedFormId {was!r} -> {form_id!r}")
    else:
        table.update_item(
            Key={"customerId": customer_id},
            UpdateExpression="REMOVE assignedFormId")
        print(f"{customer_id} ({phone}): assignedFormId {was!r} removed; the "
              "caller falls back to the deployed FORM_ID default")

    if salesforce:
        _assign_in_salesforce(customer_id, form_id)
    else:
        print("  NOTE: cache only. The next CRM sync will recompute this from "
              "Salesforce HVI_Form_Id__c and may revert it; re-run with "
              "--salesforce to make it durable.")
    print("takes effect on the NEXT call. An unfinished session already in "
          "progress keeps the form it started with - retire it with "
          "'tools/dial.py --phone <phone> --retire-sessions'.")


def load_crm(path: str) -> None:
    with open(path) as fh:
        doc = json.load(fh)

    table = ddb.Table(TBL_CRM)
    for cust in doc["customers"]:
        table.put_item(Item=_to_ddb({k: v for k, v in cust.items() if not k.startswith("_")}))
        print(f"loaded customer {cust['customerId']} ({cust.get('phone')}) -> {TBL_CRM}")

    # DPDP consent must exist before any processing, so seed the ledger too.
    consent = doc.get("consentRecords") or []
    if consent:
        ledger = ddb.Table(TBL_CONSENT)
        for rec in consent:
            ledger.put_item(Item=_to_ddb(rec))
            print(f"loaded consent {rec['customerId']} @ {rec['grantedAt']} -> {TBL_CONSENT}")


def list_items(form_id: str) -> None:
    table = ddb.Table(TBL_FORM_SCHEMA)
    resp = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("formId").eq(form_id)
    )
    items = [i for i in resp["Items"] if i["itemId"] != "__META__"]
    items.sort(key=lambda i: int(i.get("order", 0)))
    print(f"\n{len(items)} item(s) for formId={form_id}\n")
    print(f"{'ITEM':<8}{'ORDER':<8}{'ACTIVE':<9}{'TYPE':<10}{'SECTION':<18}PROMPT")
    print("-" * 110)
    for i in items:
        active = "yes" if i.get("active") else "NO"
        gated = " (conditional)" if i.get("showIf") else ""
        prompt = str(i.get("prompt", ""))[:52]
        print(
            f"{i['itemId']:<8}{int(i.get('order',0)):<8}{active:<9}"
            f"{str(i.get('answerType','')):<10}{str(i.get('section','')):<18}{prompt}{gated}"
        )
    print()


def add_item(args: argparse.Namespace) -> None:
    item: dict[str, Any] = {
        "formId": args.form_id,
        "itemId": args.item_id,
        "order": args.order,
        "section": args.section,
        "label": args.label,
        "answerType": args.answer_type,
        "required": bool(args.required),
        "prompt": args.prompt,
        "active": True,
    }
    if args.reprompt:
        item["reprompt"] = args.reprompt
    if args.options:
        item["options"] = args.options
    if args.show_if:
        item["showIf"] = json.loads(args.show_if)
    if args.parent_item_id:
        item["parentItemId"] = args.parent_item_id
    if args.knowledge_graph:
        item["knowledgeGraph"] = {
            "enabled": True,
            "nodeType": args.knowledge_graph,
            "normalise": True,
        }
    ddb.Table(TBL_FORM_SCHEMA).put_item(Item=_to_ddb(item))
    print(f"added {args.item_id} to formId={args.form_id}")
    print("This item is live on the next call. No redeployment required.")


def disable_item(form_id: str, item_id: str) -> None:
    ddb.Table(TBL_FORM_SCHEMA).update_item(
        Key={"formId": form_id, "itemId": item_id},
        UpdateExpression="SET active = :f",
        ExpressionAttributeValues={":f": False},
    )
    print(f"disabled {item_id} (formId={form_id}); it will be skipped on the next call")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    lf = sub.add_parser("load-form"); lf.add_argument("path")
    lc = sub.add_parser("load-crm"); lc.add_argument("path")

    ls = sub.add_parser("list"); ls.add_argument("--form-id", default="medical-history-v1")

    ai = sub.add_parser("add-item")
    ai.add_argument("--form-id", default="medical-history-v1")
    ai.add_argument("--item-id", required=True)
    ai.add_argument("--order", type=int, required=True)
    ai.add_argument("--section", required=True)
    ai.add_argument("--label", required=True)
    ai.add_argument("--answer-type", required=True,
                    choices=["boolean", "text", "number", "enum", "list", "date", "email", "consent"])
    ai.add_argument("--prompt", required=True)
    ai.add_argument("--reprompt")
    ai.add_argument("--options", nargs="*")
    ai.add_argument("--show-if", help='JSON, e.g. \'{"itemId":"Q400","operator":"eq","value":true}\'')
    ai.add_argument("--parent-item-id")
    ai.add_argument("--knowledge-graph", choices=["Condition", "Medication", "Procedure"])
    ai.add_argument("--required", action="store_true")

    di = sub.add_parser("disable")
    di.add_argument("--form-id", default="medical-history-v1")
    di.add_argument("--item-id", required=True)

    df = sub.add_parser("demo-form",
                        help="write a shortened copy of a form under a new formId")
    df.add_argument("--source", default="seed/form-arogya-sanjeevani-v1.json")
    df.add_argument("--form-id", default="arogya-sanjeevani-demo-v1")
    df.add_argument("--keep", default=",".join(DEMO_KEEP),
                    help="comma-separated itemIds, in the order they are asked")

    af = sub.add_parser("assign-form",
                        help="point one caller at a formId, or back at the default")
    af.add_argument("--phone", required=True)
    group = af.add_mutually_exclusive_group(required=True)
    group.add_argument("--form-id")
    group.add_argument("--reset", action="store_true",
                       help="remove assignedFormId so the deployed default applies")
    af.add_argument("--salesforce", action="store_true",
                    help="also set Contact.HVI_Form_Id__c, so a CRM sync does "
                         "not revert the choice")

    a = p.parse_args()
    if a.cmd == "load-form":
        load_form(a.path)
    elif a.cmd == "load-crm":
        load_crm(a.path)
    elif a.cmd == "list":
        list_items(a.form_id)
    elif a.cmd == "add-item":
        add_item(a)
    elif a.cmd == "disable":
        disable_item(a.form_id, a.item_id)
    elif a.cmd == "demo-form":
        keep = tuple(s.strip() for s in a.keep.split(",") if s.strip())
        demo_form(a.source, a.form_id, keep)
    elif a.cmd == "assign-form":
        assign_form(a.phone, None if a.reset else a.form_id, a.salesforce)
    return 0


if __name__ == "__main__":
    sys.exit(main())
