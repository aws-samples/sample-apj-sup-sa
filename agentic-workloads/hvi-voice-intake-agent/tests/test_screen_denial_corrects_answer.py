#!/usr/bin/env python3
"""An explicitly denied condition screen must not leave a "yes" on the form.

MEASURED ON call-63112b8a. The caller's "नहीं" transcribed as "हाँ" on the
neuromuscular screen A260. The agent asked which condition it was, she answered
"नहीं नहीं मुझे नहीं है", and the agent apologised and moved on - but A260 stayed
True. No condition was declared, so declaredConditions looked clean and the mistake
was invisible there, while the underwriting FORM went on asserting a neuromuscular
disease with nothing attached. She reported it as "i did not say yes to [the
neuromuscular screen] still it took yes", which is an accurate description.

THE FORM NOW FOLLOWS THE CALLER'S LAST WORD, and the asymmetry is the point:

  - an EXPLICIT denial - "I didn't say yes" (_SCREEN_ANSWER_DENIAL) or "I don't have
    any of these" (_DENIES_POSSESSION) - CORRECTS the screen answer to no. A caller
    who says that has answered the screen twice, consistently, and deferring to the
    transcription over her own correction is the agent not listening.

  - a BARE "नहीं" does NOT, and test_screen_yes_never_lost covers that. A lone no is
    ambiguous - "no, I won't name it" / "no, I misheard you" / "no, nothing" - and on
    an underwriting form a lost disclosure is the expensive error, so an ambiguous
    retraction is not allowed to move the record.

What must hold on the explicit path, and why each half matters:
  - the screen answer becomes False, because the form must not assert a disease the
    caller denied - that can load a premium or attach an exclusion
  - no condition is invented
  - the contradiction is STILL flagged with her verbatim words, because a caller who
    says yes then denies is worth a human look and silently rewriting history would
    hide it
  - the probe closes and the call moves on without pressing her for a name

    source config/env.sh && ./.venv/bin/python tests/test_screen_denial_corrects_answer.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "common"))

import _phones  # noqa: E402
import hvi_state as st  # noqa: E402
import orchestrator as orch  # noqa: E402

PHONE = _phones.reserve("test_screen_denial_corrects_answer")
SCREEN = "A260"  # motor neuron / muscular dystrophy / neuromuscular

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{label}{': ' + detail if detail else ''}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          f"{'' if ok else '  <-- ' + detail}")


def run_bare_no_keeps_the_yes(language: str = "hi") -> None:
    """The other half of the asymmetry: an AMBIGUOUS retraction must not move the form.

    A lone "नहीं" to "which condition was it?" can mean "no, I won't name it", "no, I
    misheard you", or "no, nothing". On an underwriting form a lost disclosure is the
    expensive error, so the recorded yes stays and a human decides. Kept in this file
    next to the explicit case, because the two are only defensible together - reading
    either one alone makes the orchestrator look inconsistent.
    """
    print("\n[bare no keeps the yes] denial: 'नहीं'")
    cust = st.get_customer_by_phone(PHONE)
    session = st.start_session(PHONE, cust, "arogya-sanjeevani-v1")
    cid = session["callId"]
    st.set_language(cid, language, confirmed=True)
    st.save_answer(cid, SCREEN, True, 10)
    st.set_pending_condition_detail(cid, SCREEN, "कौन सी बीमारी थी?")

    orch.handle_turn(cid, "नहीं", 11, language_hint=language)
    sess = st.get_session(cid) or {}
    answers = sess.get("answers") or {}

    check("[bare no] an ambiguous retraction leaves the recorded yes in place",
          answers.get(SCREEN) is True,
          f"got {answers.get(SCREEN)!r} - a bare no must not move the form; only an "
          f"explicit denial does")
    check("[bare no] no condition is invented from a bare no",
          not [d.get("icd10") for d in sess.get("declaredConditions") or []],
          str(sess.get("declaredConditions")))
    check("[bare no] it is still flagged for review",
          any((m.get("itemId") == SCREEN)
              for m in sess.get("unclassifiedMentions") or []),
          str(sess.get("unclassifiedMentions")))
    check("[bare no] the probe closes so the call moves on",
          not sess.get("pendingConditionDetail"))


def run_case(name: str, denial: str, language: str = "hi") -> None:
    print(f"\n[{name}] denial: {denial!r}")
    cust = st.get_customer_by_phone(PHONE)
    session = st.start_session(PHONE, cust, "arogya-sanjeevani-v1")
    cid = session["callId"]
    st.set_language(cid, language, confirmed=True)

    # Put the call directly into the state the live call reached: the screen answered
    # yes, and the "which condition was it?" probe open. Driving 26 turns of
    # questionnaire to get here would make this a slow test of the wrong thing.
    st.save_answer(cid, SCREEN, True, 10)
    st.set_pending_condition_detail(
        cid, SCREEN,
        "ठीक है। आपने हाँ कहा। कृपया बताइए यह कौन सी बीमारी थी, "
        "और पहली बार कब पता चली?")

    before = (st.get_session(cid) or {}).get("answers") or {}
    check(f"[{name}] the screen starts recorded as yes",
          before.get(SCREEN) is True, f"got {before.get(SCREEN)!r}")
    check(f"[{name}] the naming probe is open",
          bool((st.get_session(cid) or {}).get("pendingConditionDetail")))

    reply = orch.handle_turn(cid, denial, 11, language_hint=language)
    sess = st.get_session(cid) or {}
    after = sess.get("answers") or {}

    check(f"[{name}] the screen answer is corrected to no",
          after.get(SCREEN) is False,
          f"got {after.get(SCREEN)!r} - the form is still asserting a disease the "
          f"caller denied")

    codes = [d.get("icd10") for d in sess.get("declaredConditions") or []]
    check(f"[{name}] no condition is invented from a denial", not codes, str(codes))

    # The underwriter has to be able to see the contradiction. The reason text is not
    # asserted because more than one branch can flag the same phrase and they
    # deduplicate; what matters is that the caller's own words are on the record.
    mentions = sess.get("unclassifiedMentions") or []
    check(f"[{name}] the caller's verbatim denial is flagged for review",
          any(denial in str(m.get("phrase") or "") for m in mentions),
          str([m.get("phrase") for m in mentions]))

    check(f"[{name}] the probe is closed so the call moves on",
          not sess.get("pendingConditionDetail"),
          str(sess.get("pendingConditionDetail")))
    check(f"[{name}] the caller is not pressed for a name again",
          (reply.get("askedKind") or "") != "conditionDetail",
          str(reply.get("askedKind")))
    check(f"[{name}] the call is not stopped",
          not reply.get("stopQuestionnaire"), str(reply.get("stopQuestionnaire")))
    check(f"[{name}] the agent acknowledges rather than arguing",
          bool(reply.get("agentUtterance")), "no utterance returned")


def main() -> int:
    if not os.environ.get("OPENSEARCH_ENDPOINT"):
        print("OPENSEARCH_ENDPOINT is not set. Run:\n  source config/env.sh && "
              "./.venv/bin/python tests/test_screen_denial_corrects_answer.py")
        return 2
    # The live wording, plus the transcription-correction shape and English.
    run_case("denies possession", "नहीं नहीं मुझे नहीं है")
    run_case("denies saying yes", "नहीं, मैंने हाँ नहीं बोला")
    run_case("english denies possession", "No, I do not have any of those",
             language="en")
    # And the half that must NOT change.
    run_bare_no_keeps_the_yes()

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} checks passed.")
    print("  An EXPLICIT denial corrects the screen to no and still flags the")
    print("  contradiction; a BARE no leaves the yes for a human to judge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
