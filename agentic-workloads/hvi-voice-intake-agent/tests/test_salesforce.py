#!/usr/bin/env python
"""Round-trip the Salesforce contract: purchase -> poll -> writeback.

Exercises the two directions the integration depends on, against the real org.

  TRIGGER   a policy sale marks a Contact as owing a medical history, and the
            dialler's SOQL finds exactly that contact
  WRITEBACK the completed intake upserts into Medical_History__c

The assertion that matters most is IDEMPOTENCY. The writeback upserts on
Call_Id__c, an External Id, so a retry after a timeout must update the existing
row rather than write a second medical history for the same call. A duplicate
underwriting record is not a cosmetic bug - it is two different answers to
"what did this patient declare". So the test upserts twice and asserts one row.

Self-cleaning: everything it creates is deleted at the end, including on failure,
so it can run repeatedly against a live org without leaving test contacts behind.

Run:  ./.venv/bin/python tests/test_salesforce.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

from hvi_salesforce import Salesforce, SalesforceError  # noqa: E402

MEDICAL_HISTORY = "Medical_History__c"
MARKER = "hvi-selftest"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    sf = Salesforce()
    print(f"org: {sf.instance_url}  api v{sf.version}\n")

    call_id = f"call-{uuid.uuid4()}"
    phone = "+15550009999"
    now = dt.datetime.now(dt.timezone.utc)
    contact_id: str | None = None
    created_mh: list[str] = []

    try:
        # ---- 1. the trigger: a policy was sold -----------------------------
        print("[1] a policy sale creates a Contact owing a medical history")
        contact_id = sf.create("Contact", {
            "LastName": f"Desai ({MARKER})",
            "FirstName": "Anita",
            "Phone": phone,
            "Email": "noreply@example.invalid",
            "HVI_Policy_Number__c": "AS-2026-SELFTEST",
            "HVI_Product_Type__c": "Arogya Sanjeevani",
            "HVI_Sum_Insured__c": 500000,
            "HVI_Underwriting_Status__c": "awaiting-medical-history",
            "HVI_Purchased_At__c": now.isoformat().replace("+00:00", "Z"),
            "HVI_Preferred_Language__c": "en-IN",
            "HVI_Form_Id__c": "arogya-sanjeevani-v1",
            "HVI_Consent_Granted_At__c": now.isoformat().replace("+00:00", "Z"),
            "HVI_DND_Registered__c": False,
        })
        check("contact created", bool(contact_id), contact_id or "")

        # ---- 2. the dialler's query ----------------------------------------
        # This is the exact shape the dialler runs, so if the field names or the
        # picklist value are wrong the test fails here rather than in production.
        print("\n[2] the dialler finds it by polling")
        rows = sf.query(
            "SELECT Id, Phone, FirstName, LastName, HVI_Policy_Number__c, "
            "HVI_Preferred_Language__c, HVI_Purchased_At__c, HVI_DND_Registered__c, "
            "HVI_Consent_Granted_At__c, HVI_Form_Id__c "
            "FROM Contact "
            "WHERE HVI_Underwriting_Status__c = 'awaiting-medical-history' "
            "AND Phone != null "
            "ORDER BY HVI_Purchased_At__c ASC"
        )
        mine = [r for r in rows if r["Id"] == contact_id]
        check("appears in the dialler's queue", len(mine) == 1,
              f"{len(rows)} due overall")
        if mine:
            r = mine[0]
            check("phone is readable", r.get("Phone") == phone, str(r.get("Phone")))
            check("language is readable",
                  r.get("HVI_Preferred_Language__c") == "en-IN",
                  str(r.get("HVI_Preferred_Language__c")))
            check("consent timestamp is readable",
                  bool(r.get("HVI_Consent_Granted_At__c")))

        # ---- 3. the writeback ----------------------------------------------
        print("\n[3] completion upserts the medical history")
        responses = {
            "biometrics": {"height_cm": 165, "weight_kg": 62, "bmi": 22.8},
            "conditions": [{
                "itemId": "A250", "label": "diabetes", "answer": True,
                "icd10": "E11", "patientPhrase": "haan mujhe sugar hai",
                "classifiedBy": "opensearch-knn", "confidence": 0.94,
                "followUps": {"diagnosed_when": "2019",
                              "current_treatment": "Metformin 500mg twice daily",
                              "is_controlled": True},
                "protocolSource": "neptune:Condition/E11 -> requires -> "
                                  "[diagnosed_when, current_treatment, is_controlled]",
            }],
        }
        payload = {
            "Policy_Number__c": "AS-2026-SELFTEST",
            "Form_Id__c": "arogya-sanjeevani-v1",
            "Submitted_At__c": now.isoformat().replace("+00:00", "Z"),
            "Resume_Count__c": 1,
            "Language_Used__c": "hinglish",
            "Underwriting_Flag__c": "refer",
            "Turn_Count__c": 48,
            "Call_Legs__c": 2,
            "Responses_JSON__c": json.dumps(responses, ensure_ascii=False),
        }
        sf.upsert(MEDICAL_HISTORY, "Call_Id__c", call_id, payload)
        check("upsert accepted", True)

        rows = sf.query(
            f"SELECT Id, Call_Id__c, Underwriting_Flag__c, Resume_Count__c, "
            f"Language_Used__c, Responses_JSON__c FROM {MEDICAL_HISTORY} "
            f"WHERE Call_Id__c = '{call_id}'")
        check("record is readable", len(rows) == 1, f"{len(rows)} rows")
        if rows:
            created_mh.append(rows[0]["Id"])
            check("underwriting flag round-tripped",
                  rows[0].get("Underwriting_Flag__c") == "refer")
            check("resume count round-tripped",
                  int(rows[0].get("Resume_Count__c") or 0) == 1)
            # The nested JSON is the part an underwriter actually reads, and Long
            # Text Area has a size limit, so confirm it came back whole.
            back = json.loads(rows[0].get("Responses_JSON__c") or "{}")
            check("nested responses survived intact",
                  back.get("conditions", [{}])[0].get("icd10") == "E11")
            check("the patient's own words are preserved",
                  back.get("conditions", [{}])[0].get("patientPhrase")
                  == "haan mujhe sugar hai")
            check("the protocol source is preserved",
                  "neptune:Condition/E11" in
                  (back.get("conditions", [{}])[0].get("protocolSource") or ""))

        # ---- 4. idempotency ------------------------------------------------
        print("\n[4] a retry must not create a second record")
        payload["Underwriting_Flag__c"] = "clear"
        sf.upsert(MEDICAL_HISTORY, "Call_Id__c", call_id, payload)
        rows = sf.query(
            f"SELECT Id, Underwriting_Flag__c FROM {MEDICAL_HISTORY} "
            f"WHERE Call_Id__c = '{call_id}'")
        check("still exactly one record", len(rows) == 1, f"{len(rows)} rows")
        if rows:
            check("the retry updated rather than inserted",
                  rows[0].get("Underwriting_Flag__c") == "clear",
                  str(rows[0].get("Underwriting_Flag__c")))

        # ---- 5. close the loop on the contact ------------------------------
        print("\n[5] the contact is marked done so it leaves the queue")
        sf.request("PATCH",
                   f"/services/data/v{sf.version}/sobjects/Contact/{contact_id}",
                   {"HVI_Underwriting_Status__c": "complete",
                    "HVI_Last_Call_Id__c": call_id},
                   context="close contact")
        rows = sf.query(
            f"SELECT Id FROM Contact WHERE Id = '{contact_id}' "
            f"AND HVI_Underwriting_Status__c = 'awaiting-medical-history'")
        check("no longer in the dialler's queue", len(rows) == 0,
              "still queued" if rows else "")

    finally:
        print("\ncleaning up")
        for rid in created_mh:
            try:
                sf.request("DELETE",
                           f"/services/data/v{sf.version}/sobjects/{MEDICAL_HISTORY}/{rid}")
                print(f"  deleted {MEDICAL_HISTORY} {rid}")
            except SalesforceError as e:
                print(f"  could not delete {rid}: {e}")
        if contact_id:
            try:
                sf.request("DELETE",
                           f"/services/data/v{sf.version}/sobjects/Contact/{contact_id}")
                print(f"  deleted Contact {contact_id}")
            except SalesforceError as e:
                print(f"  could not delete contact: {e}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Salesforce round trip OK: trigger, poll, writeback, idempotent retry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
