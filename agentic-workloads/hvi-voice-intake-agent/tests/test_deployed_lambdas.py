#!/usr/bin/env python
"""Do the Lambdas the code invokes actually exist, and do they work when invoked?

THE BUG THIS EXISTS TO PREVENT

Every other suite loads Lambda handlers in-process with importlib:

    wb = load_lambda("hvi_crm_writeback", "crm_writeback")
    result = wb.writeback(call_id)

That verifies the CONTRACT and it is the right way to test the logic - it is fast
and it does not need a deploy to iterate. But it never crosses the DEPLOYMENT
seam, and for a while nothing did. The result was 16 green suites sitting on top
of a completion path that could not work: agent/tools.py invoked
FunctionName="hvi-crm-writeback", which had never been deployed in either region.

orchestrator._complete() wraps that invoke in try/except and logs the failure, so
a real call would have finished the questionnaire, checkpointed every answer to
DynamoDB, and then not reached Salesforce - without failing the call and without
failing a test. "Medical info reflects in CRM" would simply not have happened.

So this suite asserts two different things:

  1. EXISTENCE. Every Lambda name referenced anywhere in the code resolves to a
     deployed function. This is the generalisable guard - the failure was a name
     with no function behind it, and that class of mistake recurs every time a
     handler is written before a deploy script is.
  2. BEHAVIOUR. The deployed writeback, invoked as a Lambda over the wire, puts a
     row in Salesforce. Same assertion as the in-process test, different seam.

Run:  ./.venv/bin/python tests/test_deployed_lambdas.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time

import boto3

from live_cleanup import cleanup_hvi_fixture

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "common"))

REGION = os.environ.get("DATA_REGION", "ap-south-1")
MEDICAL_HISTORY = "Medical_History__c"

_lambda = boto3.client("lambda", region_name=REGION)
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  <- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


def load_lambda(alias: str, package: str):
    path = os.path.join(ROOT, "lambdas", package, "handler.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


def referenced_function_names() -> dict[str, str]:
    """Scrape FunctionName defaults out of the source.

    Deliberately reads the source rather than importing and inspecting, because
    the point is to catch a name that is only ever a string until the moment it
    is invoked at runtime - which is precisely how this got missed.
    """
    found: dict[str, str] = {}
    pattern = re.compile(
        r'FunctionName\s*=\s*os\.environ\.get\(\s*["\'][A-Z_]+["\']\s*,\s*["\']([^"\']+)["\']'
        r'|FunctionName\s*=\s*["\']([^"\']+)["\']')
    for sub in ("agent", "lambdas", "common", "media", "tools"):
        base = os.path.join(ROOT, sub)
        for dirpath, _dirs, files in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    src = open(p, encoding="utf-8").read()
                except Exception:  # noqa: BLE001
                    continue
                for m in pattern.finditer(src):
                    name = m.group(1) or m.group(2)
                    if name and not name.startswith(("arn:", "$", "{")):
                        found.setdefault(name, os.path.relpath(p, ROOT))
    return found


def main() -> int:
    print("=" * 78)
    print("DEPLOYED LAMBDAS: existence, then behaviour over the wire")
    print("=" * 78)

    # ---- 1. every referenced name resolves ---------------------------------
    print("\n[1] every FunctionName in the source resolves to a deployed function")
    refs = referenced_function_names()
    if not refs:
        check("found at least one FunctionName reference", False,
              "the scraper matched nothing, so it is not testing anything")
    for name, where in sorted(refs.items()):
        try:
            cfg = _lambda.get_function_configuration(FunctionName=name)
            check(f"{name} exists  (referenced by {where})", True)
            check(f"{name} is Active",
                  cfg.get("State") == "Active", str(cfg.get("State")))
        except _lambda.exceptions.ResourceNotFoundException:
            check(f"{name} exists  (referenced by {where})", False,
                  "NOT DEPLOYED - this invoke fails at runtime and is swallowed "
                  "by a try/except")

    # ---- 1b. the outbound dialler ------------------------------------------
    # The scraper above cannot find this one: nothing in the source invokes
    # hvi-outbound by name, because it is triggered externally. So it went
    # undeployed for a long time while every suite stayed green - the exact
    # failure this file was written for, one function further along.
    print("\n[1b] the outbound dialler is deployed, and a dry run neither "
          "dials nor writes")
    try:
        cfg = _lambda.get_function_configuration(FunctionName="hvi-outbound")
        check("hvi-outbound exists", True)
        check("hvi-outbound is Active", cfg.get("State") == "Active",
              str(cfg.get("State")))

        env = (cfg.get("Environment") or {}).get("Variables") or {}
        # OUTBOUND_CALLER_ID is read with os.environ[...], so a missing value is
        # an import-time crash. ASTERISK_INSTANCE_ID missing means it falls back
        # to a tag lookup, which is slower and can resolve nothing.
        check("OUTBOUND_CALLER_ID is set", bool(env.get("OUTBOUND_CALLER_ID")),
              str(sorted(env)))
        check("ASTERISK_INSTANCE_ID is set",
              bool(env.get("ASTERISK_INSTANCE_ID")), str(sorted(env)))
        # A demo override that silently persisted would put calls outside the
        # TRAI window on a normal run.
        check("ALLOW_WINDOW_OVERRIDE is not left on",
              str(env.get("ALLOW_WINDOW_OVERRIDE", "false")).lower() != "true",
              str(env.get("ALLOW_WINDOW_OVERRIDE")))

        # A number with no customer must be refused at the first gate. This
        # proves the wire works without going near a real handset.
        ob = _lambda.invoke(
            FunctionName="hvi-outbound",
            InvocationType="RequestResponse",
            Payload=json.dumps({"phone": "+910000000000", "dryRun": True}).encode(),
        )
        ob_raw = ob["Payload"].read().decode()
        check("the dialler ran without erroring", not ob.get("FunctionError"),
              ob_raw[:200])
        ob_body = json.loads(ob_raw) if ob_raw.strip().startswith("{") else {}
        check("an unknown number is refused at the policy gate",
              ob_body.get("placed") is False and ob_body.get("blockedBy") == "policy",
              ob_raw[:200])
        check("and no session was created for it",
              ob_body.get("callId") in (None, ""), ob_raw[:200])
    except _lambda.exceptions.ResourceNotFoundException:
        check("hvi-outbound exists", False,
              "NOT DEPLOYED - run scripts/33-outbound-lambda.sh. Until then the "
              "product path (a new policy triggers a call) does not exist")

    # The order matters more than the response: start_session used to run BEFORE
    # the dry_run return, so the smoke test that proves the deployment left a
    # resumable session behind and the next call opened with "we were cut off
    # earlier" about a call that never happened.
    with open(os.path.join(ROOT, "lambdas", "outbound", "handler.py"),
              encoding="utf-8") as fh:
        ob_src = fh.read()
    check("dry_run returns BEFORE start_session",
          ob_src.index("if dry_run:") < ob_src.index("st.start_session("),
          "a dry run writes a session, so verifying the deployment poisons the "
          "next real call")

    # The permission whose absence lets every gate pass and the call still fail.
    try:
        pol = boto3.client("iam").get_role_policy(
            RoleName="hvi-lambda-role", PolicyName="hvi-lambda-inline",
        )["PolicyDocument"]
        acts = {a for s in pol["Statement"]
                for a in (s["Action"] if isinstance(s["Action"], list)
                          else [s["Action"]])}
        for need in ("ssm:SendCommand", "ssm:GetCommandInvocation"):
            check(f"lambda role grants {need}", need in acts,
                  "the dialler cannot reach Asterisk, so origination fails at "
                  "the last step")
    except Exception as e:  # noqa: BLE001 - no iam:GetRolePolicy is not a failure
        print(f"  SKIP  IAM check ({type(e).__name__})")

    # ---- 2. the deployed writeback actually writes -------------------------
    print("\n[2] the DEPLOYED writeback puts a row in Salesforce")
    import hvi_state as st
    from hvi_salesforce import Salesforce

    sf = Salesforce()
    sync_handler = load_lambda("dep_crm_sync", "crm_sync")

    phone = "+919867000042"
    fixture_email = "hvi-deployed-seam@example.invalid"
    contact_id = None
    call_id = None

    # Remove any fixture left behind by an earlier failed run. Without this, one
    # crashed run poisons every future run: Salesforce's fuzzy duplicate rule
    # rejects the second create with DUPLICATES_DETECTED, so the suite fails for a
    # reason that has nothing to do with what it is testing. A self-healing
    # fixture is worth more here than a clean-up that is assumed to have worked.
    try:
        stale = sf.query(f"SELECT Id FROM Contact WHERE Email = '{fixture_email}'")
        for row in stale:
            sf.request("DELETE", f"/services/data/v{sf.version}"
                                 f"/sobjects/Contact/{row['Id']}")
            print(f"  removed stale fixture Contact {row['Id']}")
    except Exception as e:  # noqa: BLE001
        print(f"  could not pre-clean fixtures: {e}")

    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        contact_id = sf.create("Contact", {
            "LastName": "DeployedSeam",
            "FirstName": "HVI",
            "Phone": phone,
            "Email": "hvi-deployed-seam@example.invalid",
            "HVI_Policy_Number__c": f"AS-DEP-{int(time.time())}",
            "HVI_Product_Type__c": "Arogya Sanjeevani",
            "HVI_Sum_Insured__c": 500000,
            "HVI_Underwriting_Status__c": "awaiting-medical-history",
            "HVI_Purchased_At__c": now,
            "HVI_Preferred_Language__c": "hinglish",
            "HVI_Form_Id__c": "arogya-sanjeevani-v1",
            "HVI_Consent_Granted_At__c": now,
            "HVI_DND_Registered__c": False,
        })
        check("fixture contact created", bool(contact_id))

        sync_handler.sync()
        customer = st.get_customer(contact_id)
        check("projected into the CRM cache", customer is not None)
        if not customer:
            return 1

        session = st.start_session(phone, customer, customer["assignedFormId"])
        call_id = session["callId"]
        items = st.get_form_items(session["formId"])
        for turn, item in enumerate(items[:3], start=1):
            value = {"boolean": True, "number": 42}.get(item.get("answerType"), "yes")
            st.save_answer(call_id, item["itemId"], value, turn)
        st.finalize_session(call_id)
        check("session finalised",
              (st.get_session(call_id) or {}).get("status") == "completed")

        # THE SEAM. RequestResponse rather than Event, because a fire-and-forget
        # invoke returns 202 whether or not the function works - which is exactly
        # how production would hide this failure. Production keeps Event so that a
        # Salesforce outage cannot fail a completed call; the test needs the
        # answer, so it asks synchronously.
        r = _lambda.invoke(
            FunctionName="hvi-crm-writeback",
            InvocationType="RequestResponse",
            Payload=json.dumps({"action": "submit", "callId": call_id}).encode(),
        )
        raw = r["Payload"].read().decode()
        check("invoke returned 200", r["StatusCode"] == 200, str(r["StatusCode"]))
        check("no unhandled error in the function",
              "FunctionError" not in r, r.get("FunctionError", "") + " " + raw[:200])

        body = {}
        try:
            body = json.loads(raw)
        except Exception:  # noqa: BLE001
            pass
        check("function reported a write", bool(body.get("written")), raw[:200])

        rows = sf.query(
            f"SELECT Id, Call_Id__c FROM {MEDICAL_HISTORY} "
            f"WHERE Call_Id__c = '{call_id}'")
        check("exactly one row in Salesforce, written by the DEPLOYED function",
              len(rows) == 1, f"{len(rows)} rows")

        # Confirmation delivery is synchronous in the agent so a failed sender
        # cannot be announced as successful. Dry-run renders from the deployed
        # function's DynamoDB view without sending private health data by email.
        try:
            email_invoke = _lambda.invoke(
                FunctionName="hvi-send-confirmation",
                InvocationType="RequestResponse",
                Payload=json.dumps({"callId": call_id, "dryRun": True}).encode(),
            )
            email_raw = email_invoke["Payload"].read().decode()
            check("confirmation dry-run invoke returned 200",
                  email_invoke["StatusCode"] == 200, str(email_invoke["StatusCode"]))
            check("confirmation function had no unhandled error",
                  "FunctionError" not in email_invoke, email_raw[:200])
            try:
                email_body = json.loads(email_raw)
            except Exception:  # noqa: BLE001
                email_body = {}
            check("confirmation dry-run rendered the finalised intake",
                  email_body.get("dryRun") is True
                  and email_body.get("answerCount") == len(items[:3]), email_raw[:200])
        except _lambda.exceptions.ResourceNotFoundException:
            # Existence was already recorded in section 1. Keep running so cleanup
            # and the final aggregate report still happen when deployment drifts.
            check("confirmation dry-run rendered the finalised intake", False,
                  "hvi-send-confirmation is not deployed")

    finally:
        print("\ncleaning up")
        try:
            # There is no sf.delete(); the client exposes request() and the REST
            # DELETE verb, which is what test_crm_roundtrip.py uses.
            if call_id:
                rows = sf.query(f"SELECT Id FROM {MEDICAL_HISTORY} "
                                f"WHERE Call_Id__c = '{call_id}'")
                for row in rows:
                    sf.request("DELETE", f"/services/data/v{sf.version}"
                                         f"/sobjects/{MEDICAL_HISTORY}/{row['Id']}")
                    print(f"  deleted {MEDICAL_HISTORY} {row['Id']}")
            if contact_id:
                sf.request("DELETE", f"/services/data/v{sf.version}"
                                     f"/sobjects/Contact/{contact_id}")
                print(f"  deleted Contact {contact_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  cleanup problem: {e}")
        if contact_id:
            cleanup_errors = cleanup_hvi_fixture(
                st,
                customer_id=contact_id,
                call_id=call_id,
                consent_table=sync_handler.TBL_CONSENT,
            )
            for error in cleanup_errors:
                print(f"  could not clean DynamoDB fixture: {error}")

    print("\n" + "=" * 78)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Deployed Lambdas exist and the writeback works over the wire.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
