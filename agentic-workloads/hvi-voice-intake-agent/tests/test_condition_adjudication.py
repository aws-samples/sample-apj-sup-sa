#!/usr/bin/env python3
"""The classifier may answer "not in my vocabulary", and must not overuse it.

WHAT THIS GUARDS
Vector search reports what is CLOSEST, never whether anything is right. It has no
way to say "the condition this patient named is not in my index", so a disclosure
outside the vocabulary is handed the least dissimilar code that is in it. Measured
before adjudication existed:

    "I have coeliac disease"             -> E11 Type 2 Diabetes      0.786
    "मुझे सोरायसिस नहीं, सारकॉइडोसिस है"  -> L40 Psoriasis            0.826

The second is the sharper harm: the sentence DENIES psoriasis, and psoriasis is
what got recorded. Neither is reachable by more vocabulary - K90 is in the index
but WHO titles it "Intestinal malabsorption" and files the word coeliac at K90.0 -
and neither is reachable by a threshold, because 0.786 and 0.826 sit inside the
range genuine disclosures occupy. Only a judgement about MEANING separates them.

THE TWO FAILURE DIRECTIONS ARE NOT SYMMETRIC, and both are tested here.
A missed veto files a wrong disease on an underwriting record. A FALSE veto
discards a real disclosure, which is worse: the record would then under-report the
patient's history. So the suite asserts the vetoes fire on the two known-bad cases
AND that they do not fire on correct reference codes or on graph-corroborated
conditions.

It also asserts the latency guard. Adjudication is a model call on the live call
path, so it must fire ONLY where the evidence is thin - never on a corroborated
match, which is the common case.

Run:  ./.venv/bin/python tests/test_condition_adjudication.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "common"))

import tools  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail[:200]}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


# (phrase, expect_veto, expect_adjudicated, note)
CASES = [
    # THE VETO CASE. A scoped denial cannot be fixed by any amount of vocabulary,
    # because the words of the wrong condition are present in the sentence: the
    # caller says psoriasis in order to rule it out. Retrieval reads the word and
    # ranks L40, and only a judgement about meaning can see the "नहीं".
    ("\u092e\u0941\u091d\u0947 \u0938\u094b\u0930\u093e\u092f\u0938\u093f\u0938 "
     "\u0928\u0939\u0940\u0902, \u0938\u093e\u0930\u0915\u0949\u0907\u0921\u094b"
     "\u0938\u093f\u0938 \u0939\u0948", True, True,
     "denies psoriasis and asserts sarcoidosis"),
    # GRADUATED FROM VETO TO CORRECT. This phrase used to reach E11 Type 2
    # Diabetes, and adjudication vetoed it - the right call on a wrong code, but
    # only ever a floor. WHO titles K90 "Intestinal malabsorption" and files the
    # word coeliac at K90.0, so the disclosure was unreachable until that alias
    # was indexed. Now it resolves to K90 and there is nothing to veto, which is
    # strictly better: a correct code read back beats an honest refusal.
    ("I have coeliac disease", False, True, "K90 via the curated alias"),
    # Correct reference codes. A veto here would LOSE a real condition.
    ("I have sarcoidosis", False, True, "D86 is exactly right"),
    ("I was diagnosed with lupus a few years ago", False, True, "M32 is exactly right"),
    ("mujhe migraine bahut hota hai", False, True, "G43 is exactly right"),
    # Corroborated by the graph. Must not be adjudicated at all.
    ("mujhe sugar hai", False, False, "E11 corroborated"),
    ("I had gallstones", False, False, "K80 corroborated"),
    ("\u092e\u0941\u091d\u0947 \u0905\u0938\u094d\u0925\u092e\u093e \u0939\u0948",
     False, False, "J45 corroborated"),
]


def main() -> int:
    print(f"[0] adjudication configuration")
    check("adjudication is enabled by default", tools.ADJUDICATE is True,
          f"ADJUDICATE={tools.ADJUDICATE}")
    check("it uses the in-region reasoning model, not a global profile",
          not tools.REASONING_MODEL.startswith(("global.", "apac.")),
          tools.REASONING_MODEL)

    print("\n[1] vetoes fire on what the vocabulary cannot express, and only there")
    corroborated_times, adjudicated_times = [], []
    for phrase, expect_veto, expect_adj, note in CASES:
        t0 = time.time()
        res = tools.classify_condition(phrase)
        dt = time.time() - t0
        vetoed = bool(res.get("unmappable"))
        check(f"{phrase[:40]!r} veto={expect_veto}", vetoed == expect_veto,
              f"got veto={vetoed} code={res.get('icd10')} ({note})")
        if vetoed:
            # A veto must leave NO code behind. A veto that still recorded a code
            # would be the worst of both worlds: flagged and wrong.
            check(f"{phrase[:28]!r} veto carries no code",
                  res.get("icd10") is None, str(res.get("icd10")))
            check(f"{phrase[:28]!r} veto asks for no confirmation",
                  res.get("needsConfirmation") is False,
                  str(res.get("needsConfirmation")))
            check(f"{phrase[:28]!r} veto names the code it refused",
                  bool(res.get("rejectedCandidate")),
                  str(res.get("rejectedCandidate")))
        if res.get("corroborated"):
            corroborated_times.append(dt)
        elif res.get("adjudicated"):
            adjudicated_times.append(dt)

    print("\n[2] the latency guard: corroborated matches never call the model")
    for phrase, _, expect_adj, note in CASES:
        res = tools.classify_condition(phrase)
        was = bool(res.get("adjudicated")) or bool(res.get("unmappable"))
        check(f"{phrase[:40]!r} adjudicated={expect_adj}", was == expect_adj,
              f"got adjudicated={res.get('adjudicated')} "
              f"corroborated={res.get('corroborated')} ({note})")

    if corroborated_times and adjudicated_times:
        c = sum(corroborated_times) / len(corroborated_times)
        a = sum(adjudicated_times) / len(adjudicated_times)
        print(f"\n    corroborated path {c:.2f}s   adjudicated path {a:.2f}s")
        check("the corroborated path stays the faster one", c < a,
              f"corroborated {c:.2f}s vs adjudicated {a:.2f}s")

    print("\n[3] a yes never upgrades the match")
    res = tools.classify_condition("I have sarcoidosis")
    check("an adjudicated-plausible match is still uncorroborated",
          res.get("corroborated") is False, str(res.get("corroborated")))
    check("and is still read back to the patient",
          res.get("needsConfirmation") is True, str(res.get("needsConfirmation")))

    print("\n[4] the curated alias, not the veto, is what fixed coeliac disease")
    res = tools.classify_condition("I have coeliac disease")
    check("it now resolves to K90 rather than being refused",
          res.get("icd10") == "K90", str(res.get("icd10")))
    check("reached through the indexed alias, not the WHO title",
          "coeliac" in str((res.get("candidates") or [{}])[0]
                           .get("matchedVia") or "").lower(),
          str((res.get("candidates") or [{}])[0].get("matchedVia")))
    check("still uncorroborated, so it is read back",
          res.get("corroborated") is False and res.get("needsConfirmation") is True,
          f"corroborated={res.get('corroborated')} "
          f"needsConfirmation={res.get('needsConfirmation')}")

    print("\n[5] the kill switch is honoured")
    denial = ("\u092e\u0941\u091d\u0947 \u0938\u094b\u0930\u093e\u092f\u0938\u093f"
              "\u0938 \u0928\u0939\u0940\u0902, \u0938\u093e\u0930\u0915\u0949\u0907"
              "\u0921\u094b\u0938\u093f\u0938 \u0939\u0948")
    saved = tools.ADJUDICATE
    try:
        tools.ADJUDICATE = False
        off = tools.classify_condition(denial)
        check("with adjudication off, the wrong code comes back",
              off.get("unmappable") is not True and off.get("icd10") is not None,
              f"code={off.get('icd10')} unmappable={off.get('unmappable')}")
        check("and nothing claims to have been adjudicated",
              not off.get("adjudicated"), str(off.get("adjudicated")))
    finally:
        tools.ADJUDICATE = saved

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} adjudication checks passed.")
    print("  The classifier can now decline to answer, and declines only where")
    print("  its vocabulary genuinely cannot express what the patient said.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
