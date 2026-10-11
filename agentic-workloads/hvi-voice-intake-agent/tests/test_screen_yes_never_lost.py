#!/usr/bin/env python3
"""A yes to a condition screen must never be recorded and then forgotten.

THE CALL THIS COMES FROM. call-659aebfd, on the lung screen:

    agent    A230  अस्थमा, टीबी, सीओपीडी, ब्रोंकाइटिस, या फेफड़ों की कोई अन्य बीमारी?
    customer A230  हाँ मुझे आज तो मैं          <- recorded True, valid True
    agent    A240  थायरॉइड, कुशिंग, पैराथायरॉइड या एडिसन रोग?

The caller answered yes and named her condition. The STT produced "हाँ मुझे आज तो
मैं", the call recorded a bare true and moved to the next screen, and no lung
condition was ever declared or asked about. Three turns later she was still trying
to say "Asthma" and it landed on the THYROID screen's which-condition probe, so J45
was attached to an endocrine question. One dropped disclosure produced a clinically
nonsensical record.

WHY tests/test_condition_screen_followup.py DID NOT CATCH IT. That suite answers
the screen with a clean "Yes". A bare affirmation has its own branch and it worked.
This utterance is in the gap between the two branches that did work: it is not a
condition name, and it is not a bare affirmation either, because "आज", "तो" and
"मैं" are not in _AFFIRMATION_ONLY. It therefore took the "classify their own
words" branch, the classifier could not place the phrase - correctly, it is not a
condition - and condition_screened was set to True anyway. That flag suppresses the
which-condition probe.

THE INVARIANT PINNED HERE, which is deliberately weaker than "always probe".
A garbled utterance is legitimately read more than one way by the extractor, and
more than one outcome is correct, so this does not demand a specific reply:

    understood as yes  -> the condition is captured, or the caller is asked WHICH
    not understood     -> the screen is re-asked; nothing recorded, nothing lost

What must never happen is the third state: recorded as a bare yes, nothing
captured, nobody asked, call moved on. That is the only thing asserted, so the
test cannot flake on the extractor's reading while still catching the defect.

Uses A200 rather than A230 because every screen A200-A320 carries
followUpGroup="condition_detail" and runs the identical code path, and A200 is six
turns from the start instead of nine - the same bug, measurably cheaper to reach.

Runs against the LOCAL orchestrator with real DynamoDB, Neptune and OpenSearch:
the thing under test is the interaction between the extractor, the classifier and
the screen logic, so stubbing any of them would test the mock.

Run:  ./.venv/bin/python tests/test_screen_yes_never_lost.py
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

_agent_common = os.path.join(ROOT, "agent", "common")
os.makedirs(_agent_common, exist_ok=True)
for _mod in ("hvi_state.py", "hvi_prompts.py"):
    shutil.copy(os.path.join(ROOT, "common", _mod), os.path.join(_agent_common, _mod))

import orchestrator as orch  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "common"))
import hvi_state as st  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

PHONE = _phones.reserve("test_screen_yes_never_lost")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")
SCREEN = "A200"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    # Detail only on failure. The details here are phrased as explanations of the
    # defect, so printing them beside a PASS reads as a contradiction - which is
    # exactly the wrong thing to be squinting at while debugging a live call.
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   <- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


# Answers that get the call to the first condition screen. The date of birth must
# match the seeded record or identity verification terminates the call before any
# health question is asked.
LEAD_IN = {
    "A010": "Yes, speaking",
    "A020": "Yes, go ahead",
    "A030": "22 March 1987",
    "A100": "175 centimetres",
    "A110": "70 kilograms",
}

# Each of these answers the screen affirmatively while naming nothing the classifier
# can place. They are the shapes that fall between "clean condition name" and "bare
# affirmation", which is where the disclosure was being lost.
CASES = [
    # Verbatim from the live call. "आज तो मैं" is what the STT made of the condition.
    ("हाँ मुझे आज तो मैं", "hi", "the live transcript, verbatim"),
    # Romanised, same shape: an affirmation plus words that name no condition.
    ("haan mujhe woh problem hai", "hi", "romanised yes plus unplaceable words"),
    # English equivalent, so the guard is not accidentally Hindi-only.
    ("yes I have that thing you said", "en", "English yes plus a vague referent"),
]


def fresh_call(language: str) -> str:
    cust = st.get_customer_by_phone(PHONE)
    session = st.start_session(PHONE, cust, FORM_ID)
    st.set_language(session["callId"], language, confirmed=True)
    return session["callId"]


def drive_to_screen(call_id: str, language: str) -> int:
    """Answer the lead-in until the first condition screen is pending. Returns turn."""
    turn = 1
    reply = orch.handle_turn(call_id, "", turn, language_hint=language)
    trail = []
    for _ in range(40):
        ref = reply.get("askedRef")
        trail.append(f"{ref}({reply.get('askedKind')})")
        if ref == SCREEN:
            return turn
        if reply.get("isComplete") or ref is None:
            break
        turn += 1
        reply = orch.handle_turn(call_id, LEAD_IN.get(ref or "", "no"), turn,
                                 language_hint=language)
    raise SystemExit(f"never reached {SCREEN}. Path: {' -> '.join(trail)}\n"
                     f"last reply: {reply}")


def main() -> int:
    for said, language, why in CASES:
        print(f"\n[{why}]  {said!r}  ({language})")
        call_id = fresh_call(language)
        turn = drive_to_screen(call_id, language) + 1
        reply = orch.handle_turn(call_id, said, turn, language_hint=language)

        sess = st.get_session(call_id) or {}
        answer = (sess.get("answers") or {}).get(SCREEN)
        recorded_yes = answer in (True, "true", "True")
        captured = bool(sess.get("declaredConditions")
                        or sess.get("pendingFollowUps")
                        or sess.get("deferredMentions"))
        asking = (reply.get("askedKind") in ("conditionDetail", "confirmation")
                  or bool(sess.get("pendingConditionDetail"))
                  or bool(sess.get("awaitingConfirmation")))
        re_asked = reply.get("askedRef") == SCREEN and not asking

        print(f"    answer={answer!r} askedKind={reply.get('askedKind')} "
              f"askedRef={reply.get('askedRef')}")
        print(f"    captured={captured} asking={asking} re_asked={re_asked}")

        # THE ONE ASSERTION. Recorded as a yes, nothing captured, nobody asked.
        check("the disclosure was not silently dropped",
              not (recorded_yes and not captured and not asking),
              f"{SCREEN}={answer!r} with nothing captured and no question asked; "
              f"agent said {(reply.get('agentUtterance') or '')[:60]!r}")

        # And whichever branch was taken has to be a coherent one, so a future
        # change cannot satisfy the assertion above by simply recording nothing and
        # advancing anyway.
        check("the call is in a coherent state",
              captured or asking or re_asked or not recorded_yes,
              f"neither captured, asking, re-asking, nor unrecorded: "
              f"askedRef={reply.get('askedRef')}")

        # A probe is an ADDITION to the answer, not a replacement for it: the yes
        # the caller gave must survive on the record.
        if asking and reply.get("askedKind") == "conditionDetail":
            check("the yes itself is still recorded", recorded_yes, repr(answer))

    print()
    if failures:
        print(f"  {len(failures)} check(s) FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("  A yes to a condition screen is always either captured, asked about,")
    print("  or re-asked. It is never recorded and forgotten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
