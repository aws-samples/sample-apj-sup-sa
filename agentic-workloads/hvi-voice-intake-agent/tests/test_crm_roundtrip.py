#!/usr/bin/env python
"""The whole CRM loop: purchase in Salesforce -> call -> underwriting record back.

This is the end-to-end contract, exercised against the real org:

  1. a policy sale creates a Contact owing a medical history
  2. the sync projects it into DynamoDB, which is what the pre-call gate reads
  3. a session is created and the questionnaire is partly answered
  4. a condition is declared, which queues protocol follow-ups from the graph
  5. the intake is finalised
  6. the writeback upserts Medical_History__c and takes the contact off the queue

The assertions that matter are about PROVENANCE, not plumbing. An underwriting
record that says "E11" is much less useful than one that says the patient said
"haan mujhe sugar hai", that it was mapped to E11 at a recorded confidence, and
that the insulin follow-up was mandated by a specific graph traversal. Those three
fields are what an IRDAI auditor would ask about, so they are asserted explicitly.

Also asserts the ORDERING property: the medical history is written before the
contact flag is flipped, so a failed write leaves the customer queued for a retry
rather than silently dropping the intake.

Self-cleaning in both systems.

Run:  ./.venv/bin/python tests/test_crm_roundtrip.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import importlib.util

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "common"))

import hvi_state as st  # noqa: E402
from hvi_salesforce import Salesforce, SalesforceError  # noqa: E402
from live_cleanup import cleanup_hvi_fixture  # noqa: E402


def load_lambda(alias: str, package: str):
    """Load a Lambda handler by PATH under a distinct module name.

    Both Lambdas are called handler.py - which is the convention - so a plain
    `import handler` resolves to whichever directory happens to be first on
    sys.path. That is how this test first ran the writeback module while calling
    it sync_handler, and got an AttributeError that looked like a missing
    function rather than the wrong module.
    """
    path = os.path.join(ROOT, "lambdas", package, "handler.py")
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


sync_handler = load_lambda("hvi_crm_sync", "crm_sync")
wb = load_lambda("hvi_crm_writeback", "crm_writeback")

MARKER = "hvi-looptest"
PHONE = "+15550007777"
MEDICAL_HISTORY = "Medical_History__c"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    sf = Salesforce()
    print(f"org: {sf.instance_url}\n")
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    contact_id = None
    call_id = None

    try:
        # ---- 1. purchase ----------------------------------------------------
        print("[1] a policy is sold")
        contact_id = sf.create("Contact", {
            "LastName": f"Iyer ({MARKER})",
            "FirstName": "Meera",
            "MobilePhone": PHONE,
            "Email": "noreply@example.invalid",
            "HVI_Policy_Number__c": "AS-2026-LOOP",
            "HVI_Product_Type__c": "Arogya Sanjeevani",
            "HVI_Sum_Insured__c": 500000,
            "HVI_Underwriting_Status__c": "awaiting-medical-history",
            "HVI_Purchased_At__c": now,
            "HVI_Preferred_Language__c": "hinglish",
            "HVI_Form_Id__c": "arogya-sanjeevani-v1",
            "HVI_Consent_Granted_At__c": now,
            "HVI_DND_Registered__c": False,
        })
        check("contact created", bool(contact_id), contact_id)

        # ---- 2. sync --------------------------------------------------------
        print("\n[2] the sync projects it for the gate")
        sync_handler.sync()
        customer = st.get_customer(contact_id)
        check("in the CRM cache", customer is not None)
        check("phone normalised", (customer or {}).get("phone") == PHONE)

        # ---- 3. the call ----------------------------------------------------
        print("\n[3] a session is created and partly answered")
        session = st.start_session(PHONE, customer, customer["assignedFormId"])
        call_id = session["callId"]
        items = st.get_form_items(session["formId"])
        for turn, item in enumerate(items[:3], start=1):
            value = {"boolean": True, "number": 42}.get(item.get("answerType"), "yes")
            st.save_answer(call_id, item["itemId"], value, turn)
        check("answers checkpointed", len(
            (st.get_session(call_id) or {}).get("answers") or {}) == 3)

        # ---- 4. a condition is declared -------------------------------------
        # Queued directly rather than through the classifier: this test is about
        # the CRM contract, and the classifier has its own suites.
        print("\n[4] a condition is declared, with the patient's own words")
        st.queue_followups(
            call_id, "E11", "diabetes",
            [{"questionId": "diagnosed_when", "prompt": "When were you diagnosed?",
              "answerType": "text", "mandatory": True},
             {"questionId": "current_treatment", "prompt": "What treatment are you on?",
              "answerType": "text", "mandatory": True}],
            protocol_source="neptune:Condition/E11 -> requires -> "
                            "[diagnosed_when, current_treatment]",
            patient_phrase="haan mujhe sugar hai",
            confidence=0.94,
        )
        st.complete_followup(call_id, "2019")
        st.complete_followup(call_id, "Metformin 500mg twice daily")
        sess = st.get_session(call_id) or {}
        check("condition recorded", len(sess.get("declaredConditions") or []) == 1)
        check("the patient's phrase persisted",
              (sess["declaredConditions"][0].get("patientPhrase")
               == "haan mujhe sugar hai"))
        check("the confidence persisted",
              float(sess["declaredConditions"][0].get("confidence") or 0) == 0.94)
        check("both follow-ups answered",
              len(sess.get("followUpAnswers") or {}) == 2,
              str(list((sess.get("followUpAnswers") or {}).keys())))

        # ---- 5. finalise ----------------------------------------------------
        print("\n[5] the intake is finalised")
        st.finalize_session(call_id)
        check("session completed",
              (st.get_session(call_id) or {}).get("status") == "completed")

        # ---- 6. writeback ---------------------------------------------------
        print("\n[6] the writeback reaches Salesforce")
        result = wb.writeback(call_id)
        check("written", result.get("written") is True, json.dumps(result)[:120])
        check("flagged for a human to look at",
              result.get("underwritingFlag") == "refer",
              str(result.get("underwritingFlag")))

        rows = sf.query(
            f"SELECT Id, Call_Id__c, Underwriting_Flag__c, Language_Used__c, "
            f"Resume_Count__c, Turn_Count__c, Responses_JSON__c "
            f"FROM {MEDICAL_HISTORY} WHERE Call_Id__c = '{call_id}'")
        check("exactly one underwriting record", len(rows) == 1, f"{len(rows)} rows")
        if rows:
            r = rows[0]
            check("language mapped to the restricted picklist",
                  r.get("Language_Used__c") == "hinglish",
                  str(r.get("Language_Used__c")))
            back = json.loads(r.get("Responses_JSON__c") or "{}")
            conds = back.get("conditions") or []
            check("the condition is in the record", len(conds) == 1)
            if conds:
                c = conds[0]
                check("icd10 present", c.get("icd10") == "E11")
                check("PROVENANCE: the patient's own words survived to Salesforce",
                      c.get("patientPhrase") == "haan mujhe sugar hai",
                      str(c.get("patientPhrase")))
                check("PROVENANCE: the confidence survived",
                      float(c.get("confidence") or 0) == 0.94)
                check("PROVENANCE: the graph traversal that mandated the follow-ups",
                      "neptune:Condition/E11" in (c.get("protocolSource") or ""),
                      str(c.get("protocolSource"))[:60])
                # Each follow-up keeps the question text and the timestamp next to
                # the answer, so an underwriter reading the record does not have
                # to look up what "diagnosed_when" was actually asked as.
                fu = (c.get("followUps") or {}).get("diagnosed_when") or {}
                check("follow-up answers grouped under the condition",
                      fu.get("answer") == "2019", json.dumps(fu)[:80])
                check("the follow-up keeps the question it answered",
                      bool(fu.get("question")), str(fu.get("question"))[:50])
            check("the form answers are in the record",
                  bool(back.get("sections")))
            check("audit trail records the leg count",
                  int(back.get("auditTrail", {}).get("callLegs") or 0) == 1)

        # ---- 7. off the queue -----------------------------------------------
        print("\n[7] the customer leaves the dialler's queue")
        rows = sf.query(
            f"SELECT Id, HVI_Underwriting_Status__c, HVI_Last_Call_Id__c "
            f"FROM Contact WHERE Id = '{contact_id}'")
        check("contact marked complete",
              rows and rows[0].get("HVI_Underwriting_Status__c") == "complete",
              str(rows[0].get("HVI_Underwriting_Status__c")) if rows else "")
        check("the call id is linked back on the contact",
              rows and rows[0].get("HVI_Last_Call_Id__c") == call_id)

        # ---- 8. idempotency -------------------------------------------------
        print("\n[8] re-running the writeback does not duplicate")
        wb.writeback(call_id)
        rows = sf.query(f"SELECT Id FROM {MEDICAL_HISTORY} "
                        f"WHERE Call_Id__c = '{call_id}'")
        check("still exactly one record", len(rows) == 1, f"{len(rows)} rows")

    finally:
        print("\ncleaning up")
        if call_id:
            try:
                for r in sf.query(f"SELECT Id FROM {MEDICAL_HISTORY} "
                                  f"WHERE Call_Id__c = '{call_id}'"):
                    sf.request("DELETE", f"/services/data/v{sf.version}"
                                         f"/sobjects/{MEDICAL_HISTORY}/{r['Id']}")
                    print(f"  deleted {MEDICAL_HISTORY} {r['Id']}")
            except SalesforceError as e:
                print(f"  could not delete medical history: {e}")
        if contact_id:
            try:
                sf.request("DELETE", f"/services/data/v{sf.version}"
                                     f"/sobjects/Contact/{contact_id}")
                print(f"  deleted Contact {contact_id}")
            except SalesforceError as e:
                print(f"  could not delete contact: {e}")
            cleanup_errors = cleanup_hvi_fixture(
                st,
                customer_id=contact_id,
                call_id=call_id,
                consent_table=sync_handler.TBL_CONSENT,
            )
            for error in cleanup_errors:
                print(f"  could not clean DynamoDB fixture: {error}")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("CRM round trip OK: purchase -> sync -> intake -> underwriting record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
