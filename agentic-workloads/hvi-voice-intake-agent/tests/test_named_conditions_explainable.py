#!/usr/bin/env python3
"""Every disease we say out loud must be findable and explainable.

call-4475670b: A270 read "अल्जाइमर" to the caller, the caller asked what it
meant, and the agent asked "which condition would you like me to explain?" three
times in identical words before the caller hung up. Two independent gaps caused
it, and this suite exists so neither can come back.

  Gap 1  resolve_condition lowercased and whitespace-stripped the phrase but left
         punctuation attached. STT ends a one-word reply with a full stop, so
         "Alzheimer's." missed the exact index, missed TextP.containing (the
         stored term has no full stop) and missed the token pass (one token, still
         carrying the stop). Locked here by asserting every stored surface form
         still resolves with a trailing full stop and with a danda.

  Gap 2  G30, F03 and G35 were named in the A270 prompt but had no node at all,
         so there was no synonym to match and no explanation to read. Locked here
         by asserting every condition name the questionnaire speaks resolves to a
         node that carries an explanation in both languages.

Pure offline. It reads the seed files and reimplements resolve_condition's three
passes, so it describes what the next seed run will produce rather than what is
currently in Neptune - which is what makes it useful before a deploy.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "seed" / "health-kg.json"
FORM_PATH = ROOT / "seed" / "form-arogya-sanjeevani-v1.json"

# The normalisation under test is imported from the graph Lambda rather than
# copied here. A copy would keep passing after someone reverted the real function,
# which is the one thing this suite is for. Loaded by path because several
# lambdas/*/handler.py files share the module name.
os.environ.setdefault("NEPTUNE_ENDPOINT", "offline-test.invalid")
_spec = importlib.util.spec_from_file_location(
    "hvi_graph_handler_under_test", ROOT / "lambdas" / "graph" / "handler.py")
graph_handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(graph_handler)

# Suffixes a caller's speech-to-text output adds to a one-word answer. Each stored
# form is required to survive all of them.
NOISY_SUFFIXES = ("", ".", "\u0964", "?", " ", ".\n")

# The condition names A270 speaks, per language, taken verbatim from the prompt.
# A270 is the screen that broke and it is the most sensitive one in the form
# (sensitivity: high), so its coverage is asserted name by name. Anything added to
# this prompt has to be added here too, and the containment check below fails if
# this list drifts away from what the prompt actually says.
A270_SPOKEN = {
    "prompt": ["stroke", "paralysis", "transient ischaemic attack",
               "multiple sclerosis", "epilepsy", "Parkinson's", "Alzheimer's",
               "dementia", "depression"],
    "promptHi": ["\u0938\u094d\u091f\u094d\u0930\u094b\u0915",
                 "\u092a\u0915\u094d\u0937\u093e\u0918\u093e\u0924",
                 "\u092e\u093f\u0930\u094d\u0917\u0940",
                 "\u092e\u0932\u094d\u091f\u0940\u092a\u0932 \u0938\u094d\u0915\u094d\u0932\u0947\u0930\u094b\u0938\u093f\u0938",
                 "\u092a\u093e\u0930\u094d\u0915\u093f\u0902\u0938\u0928",
                 "\u0905\u0932\u094d\u091c\u093e\u0907\u092e\u0930",
                 "\u0921\u093f\u092e\u0947\u0902\u0936\u093f\u092f\u093e",
                 "\u0905\u0935\u0938\u093e\u0926"],
    "promptHinglish": ["Stroke", "paralysis", "mirgi", "multiple sclerosis",
                       "Parkinson's", "Alzheimer's", "dementia", "depression"],
}

# Named in A270 and previously absent from the graph entirely.
PREVIOUSLY_MISSING = {"G30": "Alzheimer's", "F03": "dementia",
                      "G35": "multiple sclerosis"}

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


def norm(raw) -> str:
    """The graph Lambda's own normalisation, not a reimplementation of it."""
    return graph_handler._normalise_phrase(raw)


def build_index(graph: dict) -> dict[str, set[str]]:
    """Mirror the seeder: ColloquialTerm vertices come from colloquial[] only."""
    index: dict[str, set[str]] = {}
    for cond in graph.get("conditions", []):
        code = cond.get("icd10")
        if not code:
            continue
        for term in surface_forms(cond):
            index.setdefault(norm(term), set()).add(code)
    return index


def surface_forms(cond: dict) -> list[str]:
    """Only colloquial[] is searchable. Labels are not indexed, on purpose - see
    the comment in lambdas/graph/handler.py where the terms are built."""
    return [t for t in (cond.get("colloquial") or []) if t]


def resolve(phrase: str, index: dict[str, set[str]]) -> set[str]:
    """Reproduce resolve_condition's three passes, in order, returning codes."""
    p = norm(phrase)
    if not p:
        return set()
    if p in index:
        return index[p]
    for term, codes in index.items():
        if p in term:
            return codes
    for tok in [t for t in p.split() if len(t) > 2]:
        if tok in index:
            return index[tok]
    return set()


def main() -> int:
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    form = json.loads(FORM_PATH.read_text(encoding="utf-8"))
    index = build_index(graph)
    explanations = graph.get("conditionExplanations") or {}
    conditions = {c["icd10"]: c for c in graph.get("conditions", [])
                  if c.get("icd10")}
    screens = {i.get("itemId"): i for i in form.get("items", [])
               if isinstance(i, dict)}

    print(f"\n{len(conditions)} conditions, {len(index)} searchable terms\n")

    print("the graph Lambda still strips what speech-to-text adds")
    check("resolve_condition normalises through _normalise_phrase",
          "_normalise_phrase" in
          (GRAPH_PATH.parent.parent / "lambdas" / "graph" / "handler.py")
          .read_text(encoding="utf-8").split("def resolve_condition")[1][:400],
          "resolve_condition no longer calls _normalise_phrase")
    for raw, expected in (("Alzheimer's.", "alzheimer's"),
                          ("  Stroke  ", "stroke"),
                          ("\u0905\u0932\u094d\u091c\u093e\u0907\u092e\u0930\u0964",
                           "\u0905\u0932\u094d\u091c\u093e\u0907\u092e\u0930"),
                          ("dementia?", "dementia")):
        check(f"_normalise_phrase({raw!r}) -> {expected!r}",
              norm(raw) == expected, f"got {norm(raw)!r}")

    print("\nevery stored surface form resolves, punctuation and all")
    for code, cond in sorted(conditions.items()):
        for term in surface_forms(cond):
            for suffix in NOISY_SUFFIXES:
                got = resolve(f"{term}{suffix}", index)
                if code not in got:
                    check(f"{code} form {term!r} + {suffix!r} resolves to {code}",
                          False, f"got {sorted(got) or 'nothing'}")
                    break
            else:
                check(f"{code} form {term!r} resolves through every STT suffix",
                      True)

    print("\nA270 speaks nine conditions; each must resolve and be explainable")
    a270 = screens.get("A270") or {}
    check("A270 exists and is active",
          bool(a270) and a270.get("active") is not False)
    for key, names in A270_SPOKEN.items():
        prompt = a270.get(key) or ""
        check(f"A270 has {key}", bool(prompt))
        for name in names:
            # Anti-drift: the curated name must still be in the prompt text.
            check(f"A270/{key} still speaks {name!r}",
                  norm(name) in norm(prompt) or name.lower() in prompt.lower(),
                  prompt[:70])
            codes = resolve(name, index)
            check(f"A270/{key} {name!r} resolves to a condition",
                  bool(codes), f"got {sorted(codes) or 'nothing'}")
            for code in codes:
                exp = explanations.get(code) or {}
                check(f"A270/{key} {name!r} -> {code} has both explanations",
                      bool(exp.get("explain_en") and exp.get("explain_hi")))

    print("\nthe three conditions that were named but missing")
    for code, spoken in sorted(PREVIOUSLY_MISSING.items()):
        cond = conditions.get(code)
        check(f"{code} has a node", bool(cond))
        if not cond:
            continue
        check(f"{code} is attached to A270", "A270" in (cond.get("formItems") or []))
        exp = explanations.get(code) or {}
        check(f"{code} has an English explanation", bool(exp.get("explain_en")))
        check(f"{code} has a Hindi explanation", bool(exp.get("explain_hi")))
        check(f"{code} resolves from the word the prompt speaks ({spoken!r})",
              code in resolve(spoken, index),
              f"got {sorted(resolve(spoken, index)) or 'nothing'}")

    print(f"\n{checks} checks, {len(failures)} failed")
    for f in failures:
        print(f"  FAILED  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
