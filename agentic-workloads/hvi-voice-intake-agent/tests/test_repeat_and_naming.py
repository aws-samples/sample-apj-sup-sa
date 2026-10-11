#!/usr/bin/env python3
"""Asking us to repeat is not an answer, and a follow-up must name its condition.

WHY THIS EXISTS
Both come from one live call, and one of them was a bug introduced while fixing
another.

REPEAT REQUESTS. The caller answered a condition-detail probe with "Can you repeat
the list?" That sentence was passed to the condition classifier, which returns its
nearest neighbour for any input whatsoever, and it returned Chronic Ischaemic Heart
Disease. The reading was parked for confirmation, and because next_question serves a
parked confirmation ahead of everything else, the rest of the call went:

    agent  "Just so I record it correctly, is that Chronic Ischaemic Heart Disease?"
    caller "No, no, you are asking me about thyroid. Can you repeat that?"
    agent  "Sorry, I didn't quite catch that. Just so I record it correctly, is
            that Chronic Ischaemic Heart Disease?"

Two failures from one cause: a reasonable request could not be honoured, and a
diagnosis the caller never mentioned was one "yes" away from the record.

AMBIGUOUS FOLLOW-UPS. The graph phrases protocol questions relative to a condition
- "What treatment or medication are you taking for it?" - which is unambiguous in
the graph and not on a call. Asked straight after an unrelated screen the caller had
answered "no" to, they replied "Which disease?" and "For what?", and the agent, not
recognising either as a repeat request, asked the same ambiguous question again.

Run:  ./.venv/bin/python tests/test_repeat_and_naming.py
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

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    print("[1] Requests to repeat are recognised, verbatim from the failed call")
    for said in ("Can you repeat the list?",
                 "No, no, you are asking me about thyroid. Can you repeat that?",
                 "Which disease?",
                 "For what?",
                 "Sorry, what was that?",
                 "phir se boliye",
                 "क्या कहा",
                 # From a live HINDI call. None of these matched originally, so the
                 # first one reached the condition classifier and came back as HIV
                 # Disease - a stigmatised diagnosis, one "yes" from the record,
                 # generated from a request for clarification.
                 "मैंने हाँ कहा, लेकिन किस चीज के लिए हाँ कहा? सूची बताइए.",
                 "क्या आप कैप लिस्ट दोबारा बोल सकते हैं?",
                 "हाँ, बीमारी का नाम फिर से बोलिए।",
                 "Repeat the list.",
                 # Corrections. "I didn't say that" was read as a REFUSAL and
                 # triggered "would you like to end the call?" - a bewildering reply
                 # to someone trying to correct a machine.
                 "मैंने नहीं बोला।",
                 "नहीं, आपने कैंसर के बारे में पूछा था।"):
        check(f"{said[:44]!r}", orch.is_repeat_request(said))

    print("\n[2] Real answers are NOT mistaken for repeat requests")
    # False positives here are the expensive direction: a real answer discarded is a
    # missing or wrong entry in a medical record.
    for said in ("I have bronchitis for sure",
                 "It's resolved now. I'm not taking any medication.",
                 "Maybe once a week",
                 "Two years back",
                 "yes",
                 "no",
                 "What I take is metformin",       # contains "what"
                 "I had it again last winter"):    # contains "again"
        check(f"{said[:44]!r} is an answer", not orch.is_repeat_request(said))

    print("\n[3] A follow-up that says only 'it' gets the condition named")
    fu = {"prompt": "What treatment or medication are you taking for it?",
          "conditionLabel": "Bronchitis", "icd10": "J40"}
    named = orch._name_condition(fu, "en")  # noqa: SLF001
    check("the condition appears in the question", "Bronchitis" in named, named)
    check("still reads as a question", named.strip().endswith("?"), named)

    print("\n[4] Prompts that are already specific are left alone")
    # Naming it twice would be worse than not naming it at all.
    fu2 = {"prompt": "Is your Bronchitis well controlled at the moment?",
           "conditionLabel": "Bronchitis", "icd10": "J40"}
    out2 = orch._name_condition(fu2, "en")  # noqa: SLF001
    check("not repeated when already present", out2.count("Bronchitis") == 1, out2)

    fu3 = {"prompt": "What was your last blood pressure reading?",
           "conditionLabel": "Essential Hypertension", "icd10": "I10"}
    out3 = orch._name_condition(fu3, "en")  # noqa: SLF001
    check("an already-unambiguous prompt is untouched",
          out3 == fu3["prompt"], out3)

    print("\n[5] Missing data degrades quietly")
    check("no label leaves the prompt unchanged",
          orch._name_condition({"prompt": "For it?"}, "en") == "For it?")  # noqa: SLF001
    check("no prompt yields empty",
          orch._name_condition({"conditionLabel": "X"}, "en") == "")  # noqa: SLF001

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("Repeat requests are honoured instead of classified, and follow-ups say "
          "which condition they are about.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
