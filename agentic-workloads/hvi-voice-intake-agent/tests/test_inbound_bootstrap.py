#!/usr/bin/env python
"""
Exercises inbound session bootstrap: the path that turns a ringing phone into a
session, which nothing did until now.

WHY THIS TEST EXISTS
/invocations used to require a callId whose session already existed, on the
assumption that an outbound dialler had created it. That left two real paths
broken with the same symptom - a caller hearing "I'm having a technical problem"
- because orchestrator.handle_turn raises ValueError on an unknown callId:
  1. any inbound call at all, since nothing creates a session for one
  2. a customer ringing back to finish an interrupted questionnaire

Scenario
  1. An unrecognised number is DECLINED. This is the correct behaviour, not a
     gap: the call collects medical history, and taking it from an unverified
     caller is both a privacy failure and a worthless record.
  2. A known number gets a fresh session with the customer attached.
  3. The same number calling again RESUMES rather than starting over, carrying
     answers and outstanding follow-ups, and retires the old session so a later
     callback cannot pick it up twice.

Self-contained: creates its own CRM fixture on a dedicated number and cleans up,
so it never leaves a resumable session on the demo caller's number - which would
silently turn the next live demo call into a resumed one.

Run:  ./.venv/bin/python tests/test_inbound_bootstrap.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

import hvi_state as st  # noqa: E402

TEST_PHONE = "+15550001111"
TEST_CUSTOMER_ID = "CUST-TEST-INBOUND"
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def seed_fixture() -> None:
    st._ddb.Table(st.TBL_CRM).put_item(Item=st.to_ddb({
        "customerId": TEST_CUSTOMER_ID,
        "phone": TEST_PHONE,
        "customerName": "Inbound Test",
        "customerEmail": "noreply@example.invalid",
        "dateOfBirth": "1987-03-22",
        "policyNumber": "AS-TEST-0001",
        "insurerName": "Meridian Health Cover",
        "preferredLanguage": "en-IN",
        "assignedFormId": FORM_ID,
        "underwritingStatus": "awaiting-medical-history",
        "consentOnFile": True,
        "dndRegistered": False,
    }))


def cleanup(call_ids: list[str]) -> None:
    for cid in call_ids:
        try:
            st.mark_status(cid, st.STATUS_ABANDONED, note="inbound bootstrap test cleanup")
        except Exception as e:  # noqa: BLE001
            print(f"  (cleanup: could not retire {cid}: {e})")
    try:
        st._ddb.Table(st.TBL_CRM).delete_item(Key={"customerId": TEST_CUSTOMER_ID})
    except Exception as e:  # noqa: BLE001
        print(f"  (cleanup: could not delete CRM fixture: {e})")


def main() -> int:
    import app  # noqa: PLC0415 - after sys.path is set up

    created: list[str] = []
    print("\n[1] An unrecognised caller is declined, not put through")
    call_id, refusal = app._bootstrap_session("+15559999999")
    check("no session created", call_id is None)
    check("a refusal is returned", bool(refusal))
    check("the refusal does not leak policy detail",
          bool(refusal) and "policy details" not in (refusal or "").lower()
          or "don't recognise" in (refusal or "").lower())

    print("\n[2] A known caller gets a session")
    seed_fixture()
    call_id, refusal = app._bootstrap_session(TEST_PHONE)
    check("no refusal", refusal is None, refusal or "")
    check("a callId was issued", bool(call_id), call_id or "")
    if not call_id:
        cleanup(created)
        return 1
    created.append(call_id)

    session = st.get_session(call_id)
    check("session exists in DynamoDB", session is not None)
    check("customer is attached", (session or {}).get("customerId") == TEST_CUSTOMER_ID,
          str((session or {}).get("customerId")))
    check("authoritative DOB is attached for verification",
          (session or {}).get("dateOfBirth") == "1987-03-22",
          str((session or {}).get("dateOfBirth")))
    check("phone is attached for callback lookup", (session or {}).get("phone") == TEST_PHONE)
    check("form is assigned", bool((session or {}).get("formId")))
    check("first call is not a resume", int((session or {}).get("resumeCount") or 0) == 0)

    print("\n[3] The same caller ringing back resumes instead of restarting")
    # Put an answer on the record so there is something for the resume to carry.
    items = st.get_form_items((session or {})["formId"])
    first_item = items[0]["itemId"]
    st.save_answer(call_id, first_item, "yes", 1)

    second_id, refusal = app._bootstrap_session(TEST_PHONE)
    check("no refusal on callback", refusal is None)
    check("a new callId was issued", bool(second_id) and second_id != call_id)
    if second_id:
        created.append(second_id)
        resumed = st.get_session(second_id)
        check("resumeCount incremented", int((resumed or {}).get("resumeCount") or 0) == 1,
              str((resumed or {}).get("resumeCount")))
        check("earlier answer carried across",
              first_item in ((resumed or {}).get("answers") or {}),
              str(list(((resumed or {}).get("answers") or {}).keys())[:3]))
        check("prior call recorded in the chain",
              call_id in ((resumed or {}).get("priorCallIds") or []))
        old = st.get_session(call_id)
        check("old session retired so a later callback cannot reuse it",
              (old or {}).get("status") == st.STATUS_ABANDONED,
              str((old or {}).get("status")))
        check("only the newest session is resumable",
              (st.find_resumable_session(TEST_PHONE) or {}).get("callId") == second_id)

    print("\ncleaning up")
    cleanup(created)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("inbound bootstrap OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
