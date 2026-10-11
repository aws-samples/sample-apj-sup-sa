#!/usr/bin/env python3
"""Answering "yes" to a condition screen must open the protocol.

WHY THIS EXISTS
Reported from a live call: "when it asks me do i have cancer, i say yes and then it
just skips to next question, does not followup on cancer at all."

That is a different path from volunteering a condition, which was already covered
and already worked. A200 is a yes/no screen. A bare "yes" is a material disclosure
that names nothing, so there was no phrase to classify, so the Neptune protocol
never opened and the call advanced to the next screen. All thirteen screens
A200-A320 carry followUpGroup="condition_detail" in the form schema and the code
referenced that field nowhere - it was dead schema for the entire condition
section, not a one-item slip.

The fix cannot be to assume the condition from the item, and the reason is
measurable. Through the real classifier:

    "I have cancer"     -> C34 (lung),   confident=True, 0.8226
    "mujhe cancer hai"  -> C50 (breast), confident=True, 0.8485

Same disclosure, different site, decided by which language it was said in, on a
0.0037 margin that ambiguous=False did not flag. Assuming a site would write the
wrong tumour type into an underwriting record and then ask a breast-cancer patient
the smoking-link follow-up. So the caller is asked which, and this test pins that
behaviour down.

Runs against the LOCAL orchestrator with real DynamoDB, Neptune and OpenSearch,
because the thing under test is the interaction between the form schema, the
classifier and the graph. Stubbing any of those would test the mock.

Run:  ./.venv/bin/python tests/test_condition_screen_followup.py
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

# Stage the shared modules the agent imports flat, exactly as
# scripts/40-agentcore.sh does. Copied unconditionally: an "only if missing" guard
# here previously left a stale copy in place and tested code that was no longer the
# code being shipped.
_agent_common = os.path.join(ROOT, "agent", "common")
os.makedirs(_agent_common, exist_ok=True)
for _mod in ("hvi_state.py", "hvi_prompts.py"):
    shutil.copy(os.path.join(ROOT, "common", _mod), os.path.join(_agent_common, _mod))

import orchestrator as orch  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "common"))
import hvi_state as st  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# Same fixture as tests/test_orchestrator.py, so this exercises a customer that
# actually exists in the seeded CRM rather than an unidentified caller.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

# This suite's OWN number, still the seeded "Priya Sharma" record but cloned onto
# a phone nobody else uses. Twelve suites shared one number, and the unfinished
# sessions they leave behind leaked between them through find_resumable_session.
# See tests/_phones.py; reserve() also clears leftovers from a previous run.
PHONE = _phones.reserve("test_condition_screen_followup")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")


def fresh_call() -> str:
    """A session with the language already settled, as test_orchestrator does.

    Confirmed up front so the two opening turns go to the questionnaire rather than
    the language menu; the menu is covered by tests/test_language_selection.py.
    """
    cust = st.get_customer_by_phone(PHONE)
    session = st.start_session(PHONE, cust, FORM_ID)
    st.set_language(session["callId"], "en", confirmed=True)
    return session["callId"]


def drive_to_a200(call_id: str) -> tuple[dict, int]:
    """Answer whatever comes until the cancer screen is the pending question.

    Deliberately drives the real questionnaire rather than seeking directly to
    A200: the bug is about what happens to the NEXT question after a screen, so
    arriving there the way a caller does is the point.
    """
    turn = 1
    reply = orch.handle_turn(call_id, "", turn, language_hint="en")
    answers = {
        "A010": "Yes, speaking",
        "A020": "Yes, go ahead",
        "A030": "22 March 1987",
        "A100": "175 centimetres",
        "A110": "70 kilograms",
    }
    trail = []
    for _ in range(40):
        ref = reply.get("askedRef")
        trail.append(f"{ref}({reply.get('askedKind')})")
        if ref == "A200":
            return reply, turn
        if reply.get("isComplete"):
            break
        turn += 1
        said = answers.get(ref or "", "no")
        reply = orch.handle_turn(call_id, said, turn, language_hint="en")
    raise SystemExit(f"never reached A200. Path taken: {' -> '.join(trail)}\n"
                     f"last reply: {reply}")


def main() -> int:
    print("[1] Reach the cancer screen")
    call_id = fresh_call()
    reply, turn = drive_to_a200(call_id)
    check("A200 is the pending question", reply.get("askedRef") == "A200",
          (reply.get("agentUtterance") or "")[:70])

    print("\n[2] A bare 'yes' asks WHICH condition instead of moving on")
    turn += 1
    reply = orch.handle_turn(call_id, "Yes", turn, language_hint="en")
    utt = reply.get("agentUtterance") or ""
    check("did not silently advance to the next screen",
          reply.get("askedKind") == "conditionDetail",
          f"askedKind={reply.get('askedKind')} ref={reply.get('askedRef')}")
    # The probe now asks "which condition was it" rather than "what was it" - the
    # older wording invited another "haan" on a live call. Same intent (name the
    # condition), clearer ask; accept the wording in any of the three languages.
    check("asked which condition it was",
          any(s in utt.lower() for s in ("which condition", "kaun si"))
          or "कौन सी" in utt,
          utt[:90])
    # The yes itself must still be on the record. Asking for detail is an addition
    # to the answer, not a replacement for it.
    sess = st.get_session(call_id) or {}
    check("the yes was still recorded as the A200 answer",
          (sess.get("answers") or {}).get("A200") in (True, "true", "True"),
          repr((sess.get("answers") or {}).get("A200")))

    print("\n[3] Naming the condition opens the Neptune protocol")
    turn += 1
    reply = orch.handle_turn(call_id, "It was breast cancer, in 2019", turn,
                             language_hint="en")
    check("the named condition is read back for confirmation",
          reply.get("askedKind") == "confirmation",
          f"askedKind={reply.get('askedKind')}: "
          f"{(reply.get('agentUtterance') or '')[:70]}")

    # Cancer is a red-flag classification, so the graph asks the caller to confirm
    # the reading before it is written to the underwriting record. Answering that
    # is part of the path, not an extra step to skip: without it nothing is
    # declared, which is the correct conservative behaviour.
    turn += 1
    reply = orch.handle_turn(call_id, "Yes that's right", turn, language_hint="en")
    sess = st.get_session(call_id) or {}
    declared = [d.get("icd10") for d in (sess.get("declaredConditions") or [])]
    owed = [f.get("questionId") for f in (sess.get("pendingFollowUps") or [])]
    check("a condition was declared", bool(declared), str(declared))
    check("it is breast cancer, not lung", "C50" in declared, str(declared))
    check("follow-ups were queued", bool(owed), f"{len(owed)} owed: {owed[:6]}")
    check("the pending probe was consumed",
          not sess.get("pendingConditionDetail"),
          repr(sess.get("pendingConditionDetail")))

    print("\n[3b] Rejecting the read-back walks the graph, it does not drop the disclosure")
    # The live failure, verbatim: "कैंसर था पाँच साल पहले" -> breast-cancer guess
    # -> caller says no -> the call moved on to heart disease with the cancer
    # nowhere in the record. The caller noticed: "आपने कैंसर का पूछताछ ही नहीं की."
    # Neptune is deterministic; the ENTRY was one nearest-neighbour guess, and a
    # rejection must advance through the candidates, not abandon the disclosure.
    call3 = fresh_call()
    reply3, turn3 = drive_to_a200(call3)
    turn3 += 1
    reply3 = orch.handle_turn(call3, "Yes", turn3, language_hint="en")
    turn3 += 1
    reply3 = orch.handle_turn(call3, "It was cancer, five years back", turn3,
                              language_hint="en")
    check("generic cancer is read back for confirmation",
          reply3.get("askedKind") == "confirmation",
          (reply3.get("agentUtterance") or "")[:70])
    first_guess = (reply3.get("askedRef") or "")
    turn3 += 1
    reply3 = orch.handle_turn(call3, "No", turn3, language_hint="en")
    utt3 = reply3.get("agentUtterance") or ""
    kept_going = (reply3.get("askedKind") == "confirmation"
                  and reply3.get("askedRef") != first_guess) \
        or reply3.get("askedKind") == "conditionDetail"
    check("the disclosure survives the rejection "
          "(next candidate offered, or asked in their own words)",
          kept_going,
          f"askedKind={reply3.get('askedKind')} ref={reply3.get('askedRef')}: "
          f"{utt3[:70]}")
    sess3 = st.get_session(call3) or {}
    check("the rejected code is remembered",
          bool(sess3.get("rejectedCodes")), str(sess3.get("rejectedCodes")))

    print("\n[4] Naming it up front is NOT asked twice")
    # "yes, breast cancer" already names the condition, so the screening path
    # handles it and a "which was it?" probe would be the agent not listening.
    call2 = fresh_call()
    reply2, turn2 = drive_to_a200(call2)
    turn2 += 1
    reply2 = orch.handle_turn(call2, "Yes, I had breast cancer in 2019", turn2,
                              language_hint="en")
    check("does not ask which when the customer already said which",
          reply2.get("askedKind") != "conditionDetail",
          f"askedKind={reply2.get('askedKind')}")
    sess2 = st.get_session(call2) or {}
    declared2 = [d.get("icd10") for d in (sess2.get("declaredConditions") or [])]
    # Either already declared, or parked awaiting the read-back - both mean the
    # disclosure was picked up. What must NOT happen is neither.
    check("the disclosure was picked up rather than dropped",
          bool(declared2) or bool(sess2.get("awaitingConfirmation")),
          f"declared={declared2} awaiting={bool(sess2.get('awaitingConfirmation'))}")

    # ---- clean up after ourselves ------------------------------------------
    # These sessions are left mid-questionnaire on the SHARED fixture phone, which
    # makes them resumable. tests/test_resume_flow.py asserts that no session for
    # that number is resumable, so leaving them behind fails a suite that has nothing
    # to do with this one - and only when the two run in the same pass, which is the
    # most confusing way for a test to fail.
    for cid in (call_id, call2, call3):
        try:
            st.mark_status(cid, st.STATUS_ABANDONED, note="test cleanup")
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask a real result
            print(f"  note: could not clean up {cid}: {exc}")

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("A yes to a condition screen opens the protocol, and the site comes from "
          "the customer rather than from a 0.0037 margin in the classifier.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
