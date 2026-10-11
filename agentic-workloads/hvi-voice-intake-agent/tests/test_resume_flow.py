#!/usr/bin/env python
"""
Exercises the checkpoint-and-resume path against the real DynamoDB tables.

Scenario
  1. Start a call for the seeded CRM customer.
  2. Answer the first few items, checkpointing each turn.
  3. Simulate the line dropping mid-question.
  4. Look the caller up by phone number, as a callback would.
  5. Start a resumed call and assert it continues at the first unanswered item
     with the earlier answers intact.
  6. Finish the remaining items and finalise.

Run:  ./.venv/bin/python tests/test_resume_flow.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

import hvi_state as st  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import _phones  # noqa: E402

# This suite's OWN number. It asserts that the session it dropped is the one
# resume offers, which a shared number cannot guarantee - see tests/_phones.py.
# TEST_PHONE still overrides, for pointing the suite at a real record by hand.
PHONE = os.environ.get("TEST_PHONE") or _phones.reserve("test_resume_flow")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        failures.append(label)


def answer(session_id: str, items: list[dict], answers: dict, value, turn: int) -> tuple[dict, int]:
    """Answer whatever item comes next, writing both turn rows and the checkpoint."""
    item = st.next_item(items, answers)
    assert item is not None, "no next item"
    iid = item["itemId"]

    st.record_turn(session_id, turn, "agent", item_id=iid, text=item["prompt"])
    turn += 1
    st.record_turn(session_id, turn, "customer", item_id=iid, text=str(value),
                   stt_confidence=0.94, extracted=value, valid=True)
    updated = st.save_answer(session_id, iid, value, turn)
    turn += 1
    print(f"        answered {iid} = {value!r}")
    return updated, turn


def main() -> int:
    print("\n[1] CRM lookup by phone")
    cust = st.get_customer_by_phone(PHONE)
    check("customer found", cust is not None, f"phone={PHONE}")
    if not cust:
        return 1
    print(f"        {cust['customerId']} / {cust['customerName']} / {cust['customerEmail']}")

    print("\n[2] Load dynamic form schema")
    items = st.get_form_items(FORM_ID)
    check("form items loaded", len(items) >= 25, f"got {len(items)}")
    first = st.next_item(items, {})
    check("first item is A010", first and first["itemId"] == "A010",
          f"got {first and first['itemId']}")
    check("Hindi prompts present", all(i.get("promptHi") for i in items if i.get("required")),
          "some required items missing promptHi")
    check("Hinglish prompts present",
          all(i.get("promptHinglish") for i in items if i.get("required")),
          "some required items missing promptHinglish")

    print("\n[3] Start original call")
    s1 = st.start_session(PHONE, cust, FORM_ID)
    call1 = s1["callId"]
    print(f"        callId={call1}")
    check("status in_progress", s1["status"] == "in_progress")
    check("no inherited answers", s1["answers"] == {})

    print("\n[4] Answer first four items, checkpointing each turn")
    answers: dict = {}
    turn = 0
    for value in [True, True, "1987-03-22", 165]:
        s, turn = answer(call1, items, answers, value, turn)
        answers = s["answers"]
    check("four answers checkpointed", len(answers) == 4, f"got {len(answers)}")

    nxt = st.next_item(items, answers)
    check("next item is A110 (weight)", nxt and nxt["itemId"] == "A110",
          f"got {nxt and nxt['itemId']}")

    print("\n[5] Simulate the call dropping mid-question")
    st.mark_status(call1, st.STATUS_DROPPED, note="simulated carrier drop")
    dropped = st.get_session(call1)
    check("status dropped", dropped["status"] == "dropped")
    turns = st.get_turns(call1)
    # Two rows per answered item: the agent's prompt, then the customer's reply.
    check("turn history persisted", len(turns) == 8, f"got {len(turns)}")
    check("turns ordered by index", [t["turnIndex"] for t in turns] == list(range(8)))
    check("roles alternate", [t["role"] for t in turns] == ["agent", "customer"] * 4)

    print("\n[6] Callback: find the unfinished session by phone")
    resumable = st.find_resumable_session(PHONE)
    check("resumable session found", resumable is not None)
    check("it is the dropped call", resumable and resumable["callId"] == call1)

    print("\n[7] Start resumed call inheriting prior answers")
    s2 = st.start_session(PHONE, cust, FORM_ID, resume_from=resumable)
    call2 = s2["callId"]
    print(f"        callId={call2}")
    check("four answers inherited", len(s2["answers"]) == 4, f"got {len(s2['answers'])}")
    check("resumeCount incremented", s2["resumeCount"] == 1, f"got {s2['resumeCount']}")
    check("prior call linked", call1 in s2["priorCallIds"])

    resumed_next = st.next_item(items, s2["answers"])
    check("resumes at A110, not A010", resumed_next and resumed_next["itemId"] == "A110",
          f"got {resumed_next and resumed_next['itemId']}")

    old = st.get_session(call1)
    check("old session retired", old["status"] == "abandoned", f"got {old['status']}")
    check("no longer resumable", (st.find_resumable_session(PHONE) or {}).get("callId") == call2)

    print("\n[8] Nested hospitalisation follow-ups gate correctly")
    answers = dict(s2["answers"])
    # A500 = no -> the whole A510..A550 chain must be skipped
    probe = dict(answers)
    probe["A500"] = False
    for iid in ("A510", "A520", "A530", "A540", "A550"):
        item = next(i for i in items if i["itemId"] == iid)
        check(f"{iid} gated out when A500=no", st.show_item(item, probe) is False)

    # A500 = yes -> reason/when/resolved/still-medicated open up
    probe["A500"] = True
    for iid in ("A510", "A520", "A530", "A540"):
        item = next(i for i in items if i["itemId"] == iid)
        check(f"{iid} opens when A500=yes", st.show_item(item, probe) is True)

    # A550 is second-level: only when still on medication
    a550 = next(i for i in items if i["itemId"] == "A550")
    probe["A540"] = False
    check("A550 gated when not on medication", st.show_item(a550, probe) is False)
    probe["A540"] = True
    check("A550 opens when still on medication", st.show_item(a550, probe) is True)

    print("\n[9] Answer through to the end, declaring diabetes and a hospitalisation")
    answers = dict(s2["answers"])
    turn = 0
    positives = {"A250": True, "A500": True, "A540": True}   # diabetes + hospitalised + medicated
    guard = 0
    while True:
        guard += 1
        if guard > 80:
            failures.append("runaway loop")
            break
        item = st.next_item(items, answers)
        if item is None:
            break
        iid = item["itemId"]
        t = item.get("answerType")
        if iid in positives:
            val: object = positives[iid]
        elif t == "boolean":
            val = False
        elif t == "number":
            val = 62
        elif t == "enum":
            val = (item.get("options") or ["unknown"])[0]
        elif t == "email":
            val = cust["customerEmail"]
        elif t == "list":
            val = ["Metformin 500mg"] if iid == "A550" else ["none"]
        else:
            val = "gallstone surgery" if iid == "A510" else "2019"
        s, turn = answer(call2, items, answers, val, turn)
        answers = s["answers"]

    check("diabetes recorded", answers.get("A250") is True)
    check("hospitalisation chain answered", all(k in answers for k in ("A510", "A520", "A530", "A540")))
    check("medication list captured", answers.get("A550") == ["Metformin 500mg"],
          f"got {answers.get('A550')}")

    check("all visible items answered", st.next_item(items, answers) is None)

    print("\n[10] Finalise")
    record = st.finalize_session(call2)
    check("response record written", record["answerCount"] == len(answers),
          f"{record['answerCount']} vs {len(answers)}")
    check("session completed", st.get_session(call2)["status"] == "completed")
    check("resume lineage preserved", record["resumeCount"] == 1)

    # Scoped to this test's own calls. Other suites share the seed phone number
    # and leave in-progress sessions behind, so a global "nothing resumable"
    # assertion would fail on test ordering rather than on behaviour.
    still = st.find_resumable_session(PHONE)
    check("neither of this test's calls is resumable",
          (still or {}).get("callId") not in (call1, call2),
          f"got {(still or {}).get('callId')}")
    check("completed session is not resumable",
          st.get_session(call2)["status"] == st.STATUS_COMPLETED)

    print(f"\n        finalised {record['answerCount']} answers across "
          f"{len(record['priorCallIds']) + 1} call leg(s)")
    print("        sections:", sorted({r["section"] for r in record["responses"]}))

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        return 1
    print("All checks passed.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
