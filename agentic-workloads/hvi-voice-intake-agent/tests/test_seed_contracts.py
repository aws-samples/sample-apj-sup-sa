#!/usr/bin/env python3
"""Exhaustive offline contracts for every underwriting seed record.

This suite is intentionally pure local. It proves that every active form item,
every seeded condition, every protocol edge, every language rendering, and every
medication relationship is internally coherent before live-service canaries add
Neptune/OpenSearch/DynamoDB deployment evidence.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "common"))

import hvi_state as st  # noqa: E402
import orchestrator as orch  # noqa: E402

FORM_PATH = ROOT / "seed" / "form-arogya-sanjeevani-v1.json"
GRAPH_PATH = ROOT / "seed" / "health-kg.json"
# Every type here must have a branch in extract.coerce. "text" is the permissive
# fallback that accepts any non-empty string, which is correct for a free-text
# answer and wrong for an identifier: A920 was typed "text" and a live call filed
# the caller's own question, 'हुई थी क्या मेरी?', as their national health ID.
SUPPORTED_ANSWER_TYPES = {"boolean", "date", "number", "enum", "list", "text",
                          "email", "abha"}
LANG_PROMPTS = ("prompt_en", "prompt_hi", "prompt_hinglish")

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  <- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


def satisfying_answers(item: dict, by_id: dict[str, dict]) -> dict:
    """Construct the smallest assignment that makes one item's showIf chain true."""
    answers: dict = {}
    current = item
    seen: set[str] = set()
    while current.get("showIf"):
        cond = current["showIf"]
        parent_id = cond.get("itemId")
        if not parent_id or parent_id in seen:
            break
        seen.add(parent_id)
        op = cond.get("operator", "eq")
        expected = cond.get("value")
        if op in ("eq", "in"):
            answers[parent_id] = expected[0] if op == "in" else expected
        elif op == "neq":
            answers[parent_id] = object()
        elif op == "notin":
            answers[parent_id] = "__outside__"
        elif op == "gt":
            answers[parent_id] = float(expected) + 1
        elif op == "exists":
            answers[parent_id] = "present"
        current = by_id.get(parent_id, {})
    return answers


def main() -> int:
    form = json.loads(FORM_PATH.read_text(encoding="utf-8"))
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    items = [i for i in form.get("items", []) if i.get("active", True)]
    conditions = graph.get("conditions") or []
    followups = graph.get("followUpQuestions") or []
    explanations = graph.get("conditionExplanations") or {}
    medications = graph.get("medications") or []
    interactions = graph.get("interactions") or []

    print("[1] Complete active-form contract")
    ids = [i.get("itemId") for i in items]
    by_id = {i["itemId"]: i for i in items}
    check("all active form item IDs are unique", len(ids) == len(set(ids)), str(Counter(ids)))
    check("all active form orders are unique",
          len({i.get('order') for i in items}) == len(items))
    # 30 until the insurer's own telephonic report was compared against this form
    # and four things it asks were found missing here: an abnormal-investigation
    # question, family/hereditary history (which the orchestrator was detecting and
    # discarding, because nothing collected it), a prior adverse underwriting
    # decision, and tobacco split from alcohol so a "yes" is attributable.
    check("form contains the expected 38 active items", len(items) == 38, str(len(items)))
    for item in items:
        iid = item["itemId"]
        check(f"{iid} has all three authored prompts",
              all(str(item.get(k) or "").strip() for k in ("prompt", "promptHi", "promptHinglish")))
        check(f"{iid} has a supported answer type",
              item.get("answerType") in SUPPORTED_ANSWER_TYPES,
              str(item.get("answerType")))
        cond = item.get("showIf")
        if cond:
            check(f"{iid} showIf references an earlier item",
                  cond.get("itemId") in by_id
                  and by_id[cond["itemId"]].get("order", 0) < item.get("order", 0),
                  str(cond))
            check(f"{iid} is reachable under a satisfying assignment",
                  st.show_item(item, satisfying_answers(item, by_id)), str(cond))
            missing_parent = dict(satisfying_answers(item, by_id))
            missing_parent.pop(cond.get("itemId"), None)
            check(f"{iid} stays hidden before its direct parent is answered",
                  not st.show_item(item, missing_parent), str(missing_parent))

    print("\n[2] Every showIf operator has deterministic semantics")
    operator_cases = [
        ({"operator": "eq", "value": True}, True, False),
        ({"operator": "neq", "value": "x"}, "y", "x"),
        ({"operator": "in", "value": ["a", "b"]}, "a", "z"),
        ({"operator": "notin", "value": ["a", "b"]}, "z", "a"),
        ({"operator": "gt", "value": 10}, 11, 10),
        ({"operator": "exists"}, "yes", ""),
    ]
    for spec, passing, failing in operator_cases:
        item = {"showIf": {"itemId": "P", **spec}}
        op = spec["operator"]
        check(f"showIf {op} accepts its positive case", st.show_item(item, {"P": passing}))
        check(f"showIf {op} rejects its negative case", not st.show_item(item, {"P": failing}))

    print("\n[3] Every condition and protocol edge is complete")
    codes = [c.get("icd10") for c in conditions]
    followup_ids = [f.get("id") for f in followups]
    fu_by_id = {f["id"]: f for f in followups}
    check("all condition codes are unique", len(codes) == len(set(codes)), str(Counter(codes)))
    check("all follow-up IDs are unique",
          len(followup_ids) == len(set(followup_ids)), str(Counter(followup_ids)))
    # 37 since K80 Gallstones was added. The graph had no biliary code at all, so
    # 'gallstone surgery' resolved to N20 Kidney Stones at 0.7738 - the wrong organ
    # - while A280 was already asking about "any condition of the liver, gall
    # bladder". The screen expected a code that did not exist.
    # 38 since J40 Bronchitis joined K80 Gallstones. Both were named in a screen's
    # own prompt while having no code, so both resolved to their nearest neighbour:
    # gallstones to kidney stones, bronchitis to COPD and then, after rejections,
    # to hepatitis.
    # 41 since 2026-08-23: G30 Alzheimer's, F03 dementia and G35 multiple
    # sclerosis were named in the A270 prompt with no node behind them, so a
    # caller who asked what one of those words meant could not be answered.
    check("graph contains the expected 41 governed conditions",
          len(conditions) == 41, str(len(conditions)))
    check("graph contains the expected 40 follow-up definitions",
          len(followups) == 40, str(len(followups)))

    alias_owners: dict[str, set[str]] = {}
    for condition in conditions:
        for alias in condition.get("colloquial") or []:
            normalised = str(alias).strip().casefold()
            alias_owners.setdefault(normalised, set()).add(condition["icd10"])
    alias_collisions = {
        alias: sorted(owners)
        for alias, owners in alias_owners.items()
        if len(owners) > 1
    }
    check("every exact colloquial alias maps to one condition",
          not alias_collisions,
          json.dumps(alias_collisions, ensure_ascii=False, sort_keys=True))

    used_followups: set[str] = set()
    mapped_screens: set[str] = set()
    for condition in conditions:
        code = condition["icd10"]
        refs = condition.get("followUps") or []
        used_followups.update(refs)
        mapped_screens.update(condition.get("formItems") or [])
        check(f"{code} has English and Hindi labels",
              bool(condition.get("label_en") and condition.get("label_hi")))
        check(f"{code} has at least one colloquial surface form",
              bool(condition.get("colloquial")))
        check(f"{code} references only active form items",
              all(ref in by_id for ref in condition.get("formItems") or []),
              str(condition.get("formItems")))
        check(f"{code} has an ordered non-empty protocol", bool(refs))
        check(f"{code} protocol references exist",
              all(ref in fu_by_id for ref in refs), str(refs))
        exp = explanations.get(code) or {}
        check(f"{code} has a concise English explanation", bool(exp.get("explain_en")))
        check(f"{code} has a concise Hindi explanation", bool(exp.get("explain_hi")))

        for language, prompt_key in (("en", "prompt_en"),
                                     ("hi", "prompt_hi"),
                                     ("hinglish", "prompt_hinglish")):
            projected = [fu_by_id[r].get(prompt_key) or fu_by_id[r].get("prompt_en")
                         for r in refs if r in fu_by_id]
            check(f"{code}/{language} projects every protocol prompt",
                  len(projected) == len(refs) and all(projected), str(projected))

    check("every follow-up definition is reachable from a condition",
          used_followups == set(followup_ids),
          f"unused={sorted(set(followup_ids) - used_followups)}")

    condition_screens = {i["itemId"] for i in items
                         if i.get("followUpGroup") == "condition_detail"}
    check("all 13 condition screens enter at least one governed graph path",
          condition_screens <= mapped_screens,
          f"unmapped={sorted(condition_screens - mapped_screens)}")

    print("\n[4] Follow-up definitions are valid in all languages")
    for fu in followups:
        qid = fu["id"]
        check(f"{qid} uses a supported answer type",
              fu.get("answerType") in SUPPORTED_ANSWER_TYPES,
              str(fu.get("answerType")))
        check(f"{qid} has EN/HI/Hinglish prompts",
              all(str(fu.get(k) or "").strip() for k in LANG_PROMPTS))
        if fu.get("answerType") == "enum":
            check(f"{qid} enum has options", bool(fu.get("options")))

    print("\n[5] Medication and interaction graph cannot reference ghosts")
    code_set = set(codes)
    med_names = {m.get("name") for m in medications}
    check("all medication names are unique", len(med_names) == len(medications))
    for med in medications:
        name = med["name"]
        check(f"{name} treats only governed conditions",
              set(med.get("treats") or []) <= code_set, str(med.get("treats")))
        policy = orch._MED_CONDITIONS.get(name.lower())  # noqa: SLF001
        if policy == ():
            continue  # explicitly too broad to judge safely
        check(f"{name} exists in the runtime consistency policy", policy is not None)
        if policy is not None:
            missing = [code for code in med.get("treats") or []
                       if not any(code.startswith(prefix) for prefix in policy)]
            check(f"{name} runtime policy covers every TREATS edge",
                  not missing, f"missing={missing}, policy={policy}")
    for interaction in interactions:
        pair = {interaction.get("a"), interaction.get("b")}
        check(f"interaction {interaction.get('a')} + {interaction.get('b')} references medications",
              pair <= med_names, str(pair - med_names))
        check(f"interaction {interaction.get('a')} + {interaction.get('b')} has rationale",
              bool(interaction.get("severity") and interaction.get("effect")))

    print("\n[6] Runtime hard-coded protocol guards cannot drift from seed")
    check("every non-condition follow-up guard references a real question",
          set(orch._NON_CONDITION_FOLLOWUPS) <= set(followup_ids),  # noqa: SLF001
          str(sorted(set(orch._NON_CONDITION_FOLLOWUPS) - set(followup_ids))))  # noqa: SLF001
    check("staged and canonical state modules are byte-identical",
          (ROOT / "common" / "hvi_state.py").read_bytes()
          == (ROOT / "agent" / "common" / "hvi_state.py").read_bytes())

    print("\n" + "=" * 78)
    print(f"Executed {checks} exhaustive seed/schema contract checks.")
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Every active field, condition, protocol, language, and medication edge is coherent.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
