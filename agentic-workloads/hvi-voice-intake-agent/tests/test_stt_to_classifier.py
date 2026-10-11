#!/usr/bin/env python
"""Integration test across the STT -> extract -> classifier seam.

WHY THIS EXISTS
---------------
Every component passed its own tests while the seam between them was broken.
The KG's colloquial synonyms were seeded largely in romanised Latin ("sugar",
"seene mein dard"), but the STT bake-off measured what actually arrives: Voxtral
returns DEVANAGARI for both Hindi and Hinglish speech. So the Neptune
exact-synonym fast path was missing on the highest-frequency utterances and
falling through to the slower kNN path. That matters most for I25 (severityTier
4), because the fast path is what forces read-back confirmation on a red flag.

HOW THE REAL PIPELINE WORKS
---------------------------
The classifier never sees a whole utterance. extract.py pulls out a short
conditionPhrase, and orchestrator._handle_condition passes only that to
classify_condition. So section [1] tests SHORT extracted phrases in the script
the STT actually emits - an earlier version of this file fed full sentences to
the graph and reported 0/6, which was measuring something the system never does.

Run:  ./.venv/bin/python tests/test_stt_to_classifier.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "agent"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import tools as agent_tools  # noqa: E402
from tools import is_blanket_denial  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# Short Devanagari condition phrases, as extract.py would hand them over after
# an STT pass. These are the forms the KG fix added.
EXTRACTED = [
    ("शुगर", "E11", True),
    ("बीपी", "I10", True),
    ("ब्लड प्रेशर", "I10", True),
    ("सीने में दर्द", "I25", True),
    ("हार्ट की बीमारी", "I25", True),
    ("थायराइड", "E03", True),
    ("साँस की तकलीफ", "J45", True),
    ("पथरी", "N20", True),
    # Formal medical Hindi, which is what Voxtral produced for spoken "BP high".
    ("उच्च रक्तचाप", "I10", False),
]

# Blanket denials must never yield a code.
DENIALS = [
    "नहीं, मुझे कोई मेडिकल प्रॉब्लम नहीं है.",
    "नहीं, मुझे कोई बीमारी नहीं है.",
    "No, I don't have any medical conditions.",
    "mujhe koi bimari nahi hai",
]

# Negation inside a genuine POSITIVE declaration. The guard must not fire on
# these - suppressing them would hide a stroke and two chronic conditions.
NEGATION_BUT_POSITIVE = [
    ("ek taraf ka haath pair kaam nahi kar raha", "I63"),   # stroke red flag
    ("sugar control nahi hota", "E11"),
    ("मेरी शुगर कंट्रोल नहीं होती", "E11"),
    ("saans nahi aati", "J45"),
]


def main() -> int:
    print("\n[1] Extracted Devanagari phrases resolve, preferring the fast path")
    graph_hits = 0
    for phrase, expect, require_graph in EXTRACTED:
        c = agent_tools.classify_condition(phrase)
        got = c.get("icd10")
        src = c.get("source") or "-"
        on_graph = "neptune_exact_synonym" in src
        if on_graph:
            graph_hits += 1
        ok = got == expect and (on_graph or not require_graph)
        note = "" if (on_graph or not require_graph) else "expected the fast path"
        check(f"{phrase:<22} -> {got or '-':<5} via {src[:28]:<28}", ok, note)

    print("\n[1b] Duplicate exact aliases fail closed instead of using graph order")
    original_graph = agent_tools._graph
    try:
        agent_tools._graph = lambda action, **kwargs: {
            "found": True,
            "matchType": "exact_synonym",
            "matches": [
                {"icd10": "E11", "label_en": "Type 2 Diabetes Mellitus",
                 "category": "endocrine", "severityTier": 2},
                {"icd10": "E10", "label_en": "Type 1 Diabetes Mellitus",
                 "category": "endocrine", "severityTier": 3},
            ],
        }
        ambiguous = agent_tools.classify_condition("ambiguous exact alias")
    finally:
        agent_tools._graph = original_graph
    check("multi-code exact alias returns no arbitrary diagnosis",
          ambiguous.get("icd10") is None
          and ambiguous.get("ambiguous") is True
          and ambiguous.get("confident") is False,
          str(ambiguous))
    check("multi-code exact alias preserves deterministic candidates",
          [c.get("icd10") for c in ambiguous.get("candidates", [])]
          == ["E10", "E11"],
          str(ambiguous.get("candidates")))

    print("\n[2] Red flag still forces confirmation from a Devanagari phrase")
    c = agent_tools.classify_condition("सीने में दर्द")
    check("सीने में दर्द -> I25 with needsConfirmation",
          c.get("icd10") == "I25" and c.get("needsConfirmation"),
          f"tier={c.get('severityTier')} needsConfirmation={c.get('needsConfirmation')}")

    print("\n[3] Blanket denials return no code at all")
    # Measured before the guard existed: these scored J45 at 0.810 and I10 at
    # 0.800, both above the 0.75 confidence threshold.
    for phrase in DENIALS:
        c = agent_tools.classify_condition(phrase)
        ok = c.get("icd10") is None and not c.get("confident")
        check(f"{phrase[:44]:<46} -> {c.get('icd10') or 'none'}", ok,
              f"source={c.get('source')}")

    print("\n[4] Negation inside a positive declaration must still classify")
    # The guard is narrow on purpose. Over-triggering here would suppress a
    # stroke, which is worse than the bug it fixes.
    for phrase, expect in NEGATION_BUT_POSITIVE:
        denied = is_blanket_denial(phrase)
        c = agent_tools.classify_condition(phrase)
        got = c.get("icd10")
        ok = not denied and got == expect
        check(f"{phrase[:40]:<42} -> {got or '-':<5}", ok,
              f"guardFired={denied}")

    print("\n[5] Denial of medication is not denial of the condition")
    # "koi dawa nahi leti" has a quantifier and a negation but no generic
    # illness word, so the guard must stay out of the way.
    check("'mujhe koi dawa nahi leti' does not trip the guard",
          not is_blanket_denial("mujhe koi dawa nahi leti"))
    check("'मुझे कोई दवा नहीं लेती' does not trip the guard",
          not is_blanket_denial("मुझे कोई दवा नहीं लेती"))

    print("\n" + "=" * 78)
    print(f"Graph fast path served {graph_hits}/{len(EXTRACTED)} extracted phrases.")
    if failures:
        print(f"FAILED: {len(failures)} case(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("STT -> extract -> classifier seam is clean.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
