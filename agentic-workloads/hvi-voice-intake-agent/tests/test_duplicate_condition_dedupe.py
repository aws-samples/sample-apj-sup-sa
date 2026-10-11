#!/usr/bin/env python3
"""One disclosure must produce one code, however many times it is classified.

THE LIVE CALL THIS IS BUILT FROM
--------------------------------
An abandoned Hindi call was resumed two days later. Between the two legs the
OpenSearch concept index was patched to add the Devanagari synonym for F20, and
the agent was not redeployed. The result, from one sentence:

    F21  Schizotypal disorder   said: "हाँ, स्किजोफ्रेनिया है मुझे"   0.8221
    F20  Schizophrenia          said: "स्किजोफ्रेनिया"                0.8888

Leg 1 classified the disclosure before the synonym existed and banked F21's
protocol in pendingFollowUps. The resume drained that queue - diagnosed_when,
current_treatment, is_controlled - then reclassified the same words against the
patched index, got F20, and minted it as a SECOND condition with a SECOND
identical protocol. The caller answered the same three questions twice and heard
both diagnoses read back to her in the summary.

Order of arrival carried no information here: which code came first depended only
on when the index happened to be patched. So the duplicate is resolved by
evidence instead - see orchestrator._better_evidenced.

AWS-free. Runs the orchestrator against the in-memory state double.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "agent" / "common"))

import orchestrator as orch  # noqa: E402
from test_dialogue_state_model import MemoryState  # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


PROTOCOL = [
    {"questionId": "diagnosed_when", "prompt": "कब पता चला?", "answerType": "text",
     "mandatory": True},
    {"questionId": "current_treatment", "prompt": "कौन सी दवा?", "answerType": "text",
     "mandatory": True},
    {"questionId": "is_controlled", "prompt": "नियंत्रण में है?",
     "answerType": "boolean", "mandatory": True},
]


class _Tools:
    """Only the one call _queue_protocol makes."""

    @staticmethod
    def get_followup_questions(icd10, language):
        return {"found": True, "followUps": PROTOCOL, "mandatoryCount": 3,
                "protocolSource": f"neptune:Condition/{icd10}"}


def _fresh_call(state):
    call_id = "c-dedupe"
    state.sessions[call_id] = {
        "callId": call_id, "createdAt": "2026-09-03T10:00:00+00:00",
        "declaredConditions": [], "pendingFollowUps": [], "followUpAnswers": {},
        "unclassifiedMentions": [], "answers": {},
    }
    return call_id


def _queue(state, call_id, icd10, label, phrase, confidence,
           corroborated=False, confirmed=True):
    trace: list = []
    notes: list = []
    orch._queue_protocol(  # noqa: SLF001
        call_id, icd10, label, "hi", trace, notes,
        patient_phrase=phrase, confidence=confidence,
        corroborated=corroborated, confirmed_by_patient=confirmed)
    return state.sessions[call_id], notes, trace


def main() -> int:
    state = MemoryState()
    orch.st = state
    orch.tools = _Tools()

    print("[1] the live sequence: F21 first, then a better-evidenced F20")
    call_id = _fresh_call(state)
    _queue(state, call_id, "F21", "Schizotypal disorder",
           "हाँ, स्किजोफ्रेनिया है मुझे", 0.8221)
    session = state.sessions[call_id]
    check("F21 is declared on the first pass",
          [r["icd10"] for r in session["declaredConditions"]] == ["F21"],
          str(session["declaredConditions"]))
    check("and its three follow-ups are queued",
          len(session["pendingFollowUps"]) == 3)

    # The caller answers all three, as she did on the live call.
    for value in ("Panzer pelle", "नहीं", True):
        state.complete_followup(call_id, value)
    check("all three F21 follow-ups are answered",
          len(session["followUpAnswers"]) == 3 and not session["pendingFollowUps"],
          str(sorted(session["followUpAnswers"])))

    session, notes, trace = _queue(
        state, call_id, "F20", "Schizophrenia", "स्किजोफ्रेनिया", 0.8888)

    print("\n[2] the record ends up with ONE code, and it is the right one")
    codes = [r["icd10"] for r in session["declaredConditions"]]
    check("only F20 remains declared", codes == ["F20"], str(codes))
    check("F21 moved to supersededConditions",
          [r["icd10"] for r in session.get("supersededConditions") or []] == ["F21"],
          str(session.get("supersededConditions")))
    check("the superseded row records what replaced it",
          (session["supersededConditions"][0].get("supersededBy") == "F20"))
    check("the trace records the supersession",
          any(e.get("tool") == "supersede_condition" and e.get("ok")
              for e in trace), str(trace))

    print("\n[3] THE CALLER IS NOT ASKED THE SAME THREE QUESTIONS AGAIN")
    check("nothing is left queued for F20", not session["pendingFollowUps"],
          str(session["pendingFollowUps"]))
    for qid in ("diagnosed_when", "current_treatment", "is_controlled"):
        check(f"F20::{qid} carries the answer she already gave",
              f"F20::{qid}" in session["followUpAnswers"])
    check("the carried answer keeps its provenance",
          session["followUpAnswers"]["F20::diagnosed_when"].get("carriedFrom")
          == "F21::diagnosed_when",
          str(session["followUpAnswers"]["F20::diagnosed_when"]))
    check("the original F21 answers survive for the audit trail",
          all(f"F21::{q}" in session["followUpAnswers"]
              for q in ("diagnosed_when", "current_treatment", "is_controlled")))

    print("\n[4] the reverse order: a WEAKER code must not displace a stronger one")
    call_id = _fresh_call(state)
    _queue(state, call_id, "F20", "Schizophrenia", "स्किजोफ्रेनिया", 0.8888)
    session, notes, _ = _queue(state, call_id, "F21", "Schizotypal disorder",
                               "हाँ, स्किजोफ्रेनिया है मुझे", 0.8221)
    check("F20 is kept", [r["icd10"] for r in session["declaredConditions"]] == ["F20"],
          str(session["declaredConditions"]))
    check("F21 is never declared", not session.get("supersededConditions"))
    check("only F20's protocol is queued, once",
          len(session["pendingFollowUps"]) == 3)
    check("the discarded code is flagged for a human",
          any(m.get("kind") == "duplicate"
              for m in session.get("unclassifiedMentions") or []),
          str(session.get("unclassifiedMentions")))
    check("a note explains which code was kept",
          any("keeping F20" in n for n in notes), str(notes))

    print("\n[5] graph corroboration outranks a higher similarity score")
    call_id = _fresh_call(state)
    _queue(state, call_id, "E11", "Type 2 diabetes", "sugar", 0.95,
           corroborated=False)
    session, _, _ = _queue(state, call_id, "E10", "Type 1 diabetes", "sugar", 0.71,
                           corroborated=True)
    check("the corroborated code wins despite the lower score",
          [r["icd10"] for r in session["declaredConditions"]] == ["E10"],
          str(session["declaredConditions"]))

    print("\n[6] TWO GENUINELY DIFFERENT DISEASES ARE NOT MERGED")
    # The failure this must never have. Losing a real disclosure is invisible
    # downstream, so _same_disclosure is exact-or-contained and never fuzzy.
    call_id = _fresh_call(state)
    _queue(state, call_id, "J45", "Asthma", "अस्थमा", 0.91)
    session, _, _ = _queue(state, call_id, "E11", "Type 2 diabetes", "मधुमेह", 0.93)
    codes = sorted(r["icd10"] for r in session["declaredConditions"])
    check("both conditions are kept", codes == ["E11", "J45"], str(codes))
    check("neither was superseded", not session.get("supersededConditions"))
    check("both protocols are queued", len(session["pendingFollowUps"]) == 6)

    print("\n[7] re-classifying the SAME code is idempotent, not a supersession")
    call_id = _fresh_call(state)
    _queue(state, call_id, "F20", "Schizophrenia", "स्किजोफ्रेनिया", 0.8888)
    before = len(state.sessions[call_id]["pendingFollowUps"])
    session, _, _ = _queue(state, call_id, "F20", "Schizophrenia",
                           "स्किजोफ्रेनिया", 0.8888)
    check("the same code is not treated as a duplicate disclosure",
          not session.get("supersededConditions"),
          str(session.get("supersededConditions")))
    check("(pre-existing behaviour for a repeat of one code is unchanged)",
          len(session["pendingFollowUps"]) >= before)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("One disclosure yields one code: the better-evidenced one wins, the "
          "caller is not re-interrogated, and distinct diseases stay distinct.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
