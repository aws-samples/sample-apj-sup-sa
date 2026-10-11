"""
Projects Salesforce Contacts into the DynamoDB CRM cache.

Salesforce is the SYSTEM OF RECORD. This is the only component that reads it on
the trigger side, and it exists so that nothing else has to.

WHY A PROJECTION AND NOT DIRECT READS
The pre-call gate and the in-call agent must never take a runtime dependency on
Salesforce. Two reasons, both of which bite in production rather than in testing:
a Salesforce outage or a slow API call part-way through a live call would break a
call already in progress, and Salesforce API limits are per-org per-24h, so an
agent doing per-turn reads would exhaust them for no benefit. The cache is
explicitly a read projection - it is never authoritative, and completion writes
back to Salesforce.

WHY POLLING AND NOT CHANGE DATA CAPTURE
The SLA is 48 hours. A five-minute poll is three orders of magnitude inside the
requirement. Platform Events, CDC and the Pub/Sub API all buy near-real-time
delivery we have no use for, at the cost of a gRPC subscriber, replay-id
bookkeeping and Salesforce-side configuration. Choosing push here would be
optimising the one dimension that does not matter.

WHAT IT WILL NOT DO
Guess a country code. A phone number without a leading + is skipped with a
reason, rather than being decorated with +91 and dialled - a wrong guess means
calling a stranger and reading them someone else's policy number.

Run locally:  ./.venv/bin/python lambdas/crm_sync/handler.py
              ./.venv/bin/python lambdas/crm_sync/handler.py --dry-run
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import sys

import boto3

# common/ is bundled beside the handler in the Lambda package; locally it is at
# the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "common"))

import hvi_state as st  # noqa: E402
from hvi_salesforce import SalesforceError, salesforce_client  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("hvi-crm-sync")

DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")
TBL_CRM = os.environ.get("TBL_CRM", "hvi-crm-cache")
TBL_CONSENT = os.environ.get("TBL_CONSENT", "hvi-consent-ledger")
DEFAULT_FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")
SLA_HOURS = int(os.environ.get("SLA_HOURS_FROM_PURCHASE", "48"))

# Not a Salesforce field: the agent says it out loud ("calling from ..."), and it
# is a property of the deployment rather than of the customer.
INSURER_NAME = os.environ.get("INSURER_NAME", "Meridian Health Cover")

ddb = boto3.resource("dynamodb", region_name=DATA_REGION)

FIELDS = [
    "Id", "FirstName", "LastName", "Phone", "MobilePhone", "Email",
    "Birthdate", "LastModifiedDate",
    "HVI_Policy_Number__c", "HVI_Product_Type__c", "HVI_Sum_Insured__c",
    "HVI_Underwriting_Status__c", "HVI_Purchased_At__c",
    "HVI_Preferred_Language__c", "HVI_Form_Id__c", "HVI_Gender__c",
    "HVI_Consent_Granted_At__c", "HVI_DND_Registered__c", "HVI_Last_Call_Id__c",
]

# Fields the projection CANNOT work without, so a missing one is a misconfigured
# org and must fail loudly rather than be dropped. Id/Phone/MobilePhone decide
# who gets called; the policy number and purchase date appear in the WHERE and
# ORDER BY, so removing them from the SELECT would not even make the query valid.
REQUIRED_FIELDS = frozenset({
    "Id", "Phone", "MobilePhone", "HVI_Policy_Number__c", "HVI_Purchased_At__c",
})

# "No such column 'HVI_Gender__c' on entity 'Contact'." - the message Salesforce
# returns alongside errorCode INVALID_FIELD.
_MISSING_COLUMN = re.compile(r"No such column '([^']+)'")


def _query_contacts(sf: Salesforce, fields: list[str]) -> tuple[list[dict], list[str]]:
    """Query the contacts under management, tolerating columns the org lacks.

    WHY THIS IS NOT JUST A TRY/EXCEPT AROUND A QUERY.

    Deploying a new optional field in this list before it exists in Salesforce
    takes the ENTIRE projection down: one INVALID_FIELD and no customer is synced
    at all, including the ones that have nothing to do with the new field. That
    happened - HVI_Gender__c shipped here before tools/sf_schema.py had created
    it, and the sync returned nothing but an error until the field was added.

    A code-before-schema window is normal (two systems, two deploys), so the sync
    now degrades instead of stopping: the unknown column is dropped, the query is
    retried, and the loss is reported so it is visible rather than silent. Fields
    the projection genuinely depends on are exempt - see REQUIRED_FIELDS.
    """
    remaining = list(fields)
    dropped: list[str] = []
    # One field can be resolved per round trip, so this is bounded by the list.
    for _ in range(len(fields) + 1):
        soql = (f"SELECT {', '.join(remaining)} FROM Contact "
                "WHERE HVI_Policy_Number__c != null "
                "ORDER BY HVI_Purchased_At__c ASC")
        try:
            return sf.query(soql), dropped
        except SalesforceError as e:
            if e.errorCode != "INVALID_FIELD":
                raise
            match = _MISSING_COLUMN.search(json.dumps(e.body, default=str))
            missing = match.group(1) if match else ""
            if not missing or missing not in remaining:
                # An INVALID_FIELD we cannot attribute to a column in this list is
                # not something to paper over.
                raise
            if missing in REQUIRED_FIELDS:
                log.error("Contact.%s is missing and the projection needs it", missing)
                raise
            remaining.remove(missing)
            dropped.append(missing)
            log.warning("Contact.%s does not exist in this org yet; syncing "
                        "without it", missing)
    raise SalesforceError(0, {"raw": "could not resolve the field list"},
                          "query")


def normalise_phone(mobile: str | None, phone: str | None) -> tuple[str | None, str]:
    """Return (e164, reason_if_rejected).

    Mobile is preferred over the main phone: an intake call to a landline is far
    more likely to reach a receptionist than the policyholder.

    Punctuation is stripped, but a missing country code is a HARD REJECT. It is
    tempting to assume +91 for an Indian insurer, and that assumption is exactly
    how an automated system reads one customer's policy details to another.
    """
    for raw in (mobile, phone):
        if not raw:
            continue
        cleaned = re.sub(r"[^\d+]", "", raw.strip())
        if cleaned.startswith("+") and len(cleaned) >= 8:
            return cleaned, ""
    present = mobile or phone
    if not present:
        return None, "no phone number on the contact"
    return None, (f"phone {present!r} is not E.164 - it needs a country code. "
                  "Refusing to guess one.")


def iso_utc(sf_datetime: str | None) -> str | None:
    """Salesforce returns 2026-08-07T12:34:56.000+0000; normalise the offset.

    Written out rather than passed through because the pre-call gate parses this
    with fromisoformat to compute the SLA, and the compact +0000 offset is not
    accepted by every Python version we might run on.
    """
    if not sf_datetime:
        return None
    s = sf_datetime.strip()
    if s.endswith("+0000"):
        return s[:-5] + "+00:00"
    if s.endswith("Z"):
        return s[:-1] + "+00:00"
    return s


def project(contact: dict) -> tuple[dict | None, str]:
    """Map a Salesforce Contact to the CRM cache shape the gate already reads."""
    e164, reason = normalise_phone(contact.get("MobilePhone"), contact.get("Phone"))
    if not e164:
        return None, reason

    purchased = iso_utc(contact.get("HVI_Purchased_At__c"))
    sla_deadline = None
    if purchased:
        try:
            sla_deadline = (_dt.datetime.fromisoformat(purchased)
                            + _dt.timedelta(hours=SLA_HOURS)).isoformat()
        except ValueError:
            log.warning("unparseable HVI_Purchased_At__c %r on %s",
                        purchased, contact.get("Id"))

    name = " ".join(p for p in (contact.get("FirstName"),
                                contact.get("LastName")) if p).strip()

    record = {
        # The Salesforce record Id IS the customerId. It is the natural key and it
        # is what the writeback needs to close the loop, so inventing a separate
        # CUST-nnnn identifier would only create a mapping to maintain.
        "customerId": contact["Id"],
        "salesforceId": contact["Id"],
        "phone": e164,
        "customerName": name or "Customer",
        "customerEmail": contact.get("Email"),
        "dateOfBirth": contact.get("Birthdate"),
        # Printed in the transcript header, never asked on the call. Absent is fine:
        # the header omits the row rather than guessing or showing "unknown".
        "gender": contact.get("HVI_Gender__c"),
        "policyNumber": contact.get("HVI_Policy_Number__c"),
        "productType": contact.get("HVI_Product_Type__c"),
        "sumInsured": contact.get("HVI_Sum_Insured__c"),
        "insurerName": INSURER_NAME,
        "preferredLanguage": contact.get("HVI_Preferred_Language__c") or "hinglish",
        "assignedFormId": contact.get("HVI_Form_Id__c") or DEFAULT_FORM_ID,
        "underwritingStatus": contact.get("HVI_Underwriting_Status__c"),
        "purchasedAt": purchased,
        "slaDeadline": sla_deadline,
        "dndRegistered": bool(contact.get("HVI_DND_Registered__c")),
        # Presence of a consent timestamp only. The authoritative check is the
        # consent ledger, which the gate queries separately.
        "consentOnFile": bool(contact.get("HVI_Consent_Granted_At__c")),
        "lastCallId": contact.get("HVI_Last_Call_Id__c"),
        "syncedAt": st.now_iso(),
        "sourceSystem": "salesforce",
        "sourceLastModified": iso_utc(contact.get("LastModifiedDate")),
    }
    return record, ""


def sync(dry_run: bool = False) -> dict:
    sf = salesforce_client()
    log.info("querying salesforce: %s", sf.instance_url)
    contacts, dropped_fields = _query_contacts(sf, FIELDS)
    log.info("%d contact(s) under management", len(contacts))

    crm = ddb.Table(TBL_CRM)
    consent_tbl = ddb.Table(TBL_CONSENT)

    written = consented = skipped = 0
    due = 0
    problems: list[dict] = []

    for c in contacts:
        record, reason = project(c)
        if not record:
            skipped += 1
            problems.append({"salesforceId": c.get("Id"), "reason": reason})
            log.warning("skipping %s: %s", c.get("Id"), reason)
            continue

        if record["underwritingStatus"] == "awaiting-medical-history":
            due += 1

        if not dry_run:
            crm.put_item(Item=st.to_ddb(record))
        written += 1

        # DPDP consent. Written only when Salesforce actually carries a grant
        # timestamp - a missing one must leave the ledger empty so the consent
        # gate blocks the call, which is the correct outcome, not a bug.
        granted = iso_utc(c.get("HVI_Consent_Granted_At__c"))
        if granted:
            if not dry_run:
                # (customerId, grantedAt) is the key, so re-syncing the same grant
                # is idempotent rather than appending a duplicate.
                consent_tbl.put_item(Item=st.to_ddb({
                    "customerId": record["customerId"],
                    "grantedAt": granted,
                    "status": "ACTIVE",
                    "channel": "salesforce",
                    "purpose": "health-underwriting",
                    "capturedBy": "salesforce-crm",
                    "syncedAt": st.now_iso(),
                }))
            consented += 1

    result = {
        "contactsFound": len(contacts),
        "projected": written,
        "consentRecords": consented,
        "skipped": skipped,
        "dueForCall": due,
        "problems": problems[:20],
        # Non-empty means the org is behind this code: everything else synced, but
        # these attributes are absent from every projected record. Reported rather
        # than logged only, so a smoke test or dashboard shows the degradation.
        "droppedFields": dropped_fields,
        "dryRun": dry_run,
    }
    log.info("sync complete: %s", result)
    return result


def lambda_handler(event, _context):
    return sync(dry_run=bool((event or {}).get("dryRun")))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="sync Salesforce into the CRM cache")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(json.dumps(sync(dry_run=args.dry_run), indent=2, default=str))
