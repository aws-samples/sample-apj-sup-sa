#!/usr/bin/env python3
"""The classifier can now say "that is not a condition" - and still hears real ones.

WHY THIS EXISTS
The classifier was retrieval pretending to be classification. A k-NN index that
contains only diseases can only ever answer with a disease, so every piece of
conversational noise a call produced was mapped to whichever diagnosis was least
dissimilar, at scores overlapping genuine disclosures:

    "Here they are."   -> Epilepsy            (live call, 2026-08-10)
    "नहीं"             -> Parkinson's Disease (live call, 2026-08-10)
    "Jardiance."       -> Osteoarthritis      (live call, 2026-08-10)
    "White Golden."    -> Chronic Kidney Dis. (live call, 2026-08-09)

Thresholds could not fix this: garbage and cross-lingual disclosures overlap in
COSINE SCORE. They do not overlap in WHAT THEIR NEAREST NEIGHBOUR IS, once the
index contains a null class of ordinary conversation (seed/distractors.json).
This test pins both directions against the LIVE index and classifier.

THE ASYMMETRY: a missed disclosure loses underwriting information the caller
tried to give, while accepted noise fabricates a diagnosis they then argue
with. Both lists below are verbatim from real calls wherever possible.

Run:  ./.venv/bin/python tests/test_classifier_null_class.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

import tools  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# Conversational noise and STT garbage, verbatim from live calls. None of these
# may produce an ICD-10 code.
GARBAGE = [
    "Here they are.", "नहीं", "Jardiance.", "White Golden.", "My code.",
    "Why coding?", "The age.", "And so then.", "Enceladus?", "Seaman.",
    "ठीक है", "हाँ", "किस चीज़ के लिए भाई?",
    "किस बीमारी के बारे में बात कर रही हैं आप", "Bye, Josh.",
    "क्या आप कैप लिस्ट दोबारा बोल सकते हैं?", "I said yes.",
    "5 September 2000", "Fifty feet.",
]

# Real disclosures, verbatim from live calls and the fixture set. Every one must
# still classify to the right family.
REAL = [
    ("mujhe sugar hai", ("E10", "E11")),
    ("mujhe sugar ki bimari hai", ("E10", "E11")),
    ("कैंसर था पाँच साल पहले", ("C34", "C50", "D25")),
    ("I have bronchitis for sure", ("J40", "J41", "J42", "J44", "J45")),
    ("It was breast cancer, in 2019", ("C50",)),
    ("chalne par seene mein dard hota hai", ("I20", "I21", "I25")),
    ("thyroid ki problem hai", ("E03", "E05")),
    ("हड्डी का टूटना", ("M80", "M84", "S72", "T14")),
    ("high blood pressure rehta hai", ("I10",)),
]


def main() -> int:
    print("[1] Conversational noise produces NO diagnosis")
    for phrase in GARBAGE:
        c = tools.classify_condition(phrase)
        ok = not c.get("icd10") or not c.get("confident")
        why = (f"null ({c.get('matchedVia')!r})" if c.get("nullMatch")
               else f"{c.get('icd10')} conf={c.get('confident')} "
                    f"score={c.get('score')}")
        check(f"{phrase[:44]!r} -> no confident code", ok, why)

    print("\n[2] Real disclosures still classify to the right family")
    for phrase, families in REAL:
        c = tools.classify_condition(phrase)
        code = c.get("icd10") or ""
        ok = any(code.startswith(f) for f in families)
        check(f"{phrase[:44]!r} -> {'/'.join(families)}", ok,
              f"got {code or 'nothing'} score={c.get('score')} "
              f"null={c.get('nullMatch', False)}")

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("Noise retrieves the null class and real disclosures retrieve their "
          "condition - the classifier can finally tell the difference.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
