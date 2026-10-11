#!/usr/bin/env python3
"""EVERY governed condition's protocol, driven end to end. All 38, not a sample.

WHY THIS EXISTS
diag_protocol_coverage.py proves every condition HAS follow-ups. That is a
structural check on the seed file and says nothing about whether a caller who
discloses the condition is actually asked them. The classifier sweep
(diag_surface_form_sweep.py) proves every surface form still reaches its own code,
which is also not the same thing: reaching the code is the start of the protocol,
not the end of it.

The gap between those two is where a condition can be recognised and then quietly
under-questioned - the exact shape of the failure that started this work, where a
lung-condition "Yes" produced a record containing only the word Yes.

So for each of the 38 conditions this drives a disclosure and asserts:
  1. the condition reaches declaredConditions under its OWN code
  2. every MANDATORY follow-up the graph defines is queued
  3. the follow-ups are answered and stored against that code, not another
  4. provenance is recorded, so the record says how the code was reached
  5. a second, unrelated disclosure mid-protocol does not derail the first

Sub-conditions are covered by construction rather than by choosing examples: the
graph's own diabetes types (E10/E11), respiratory family (J40/J44/J45), hepatic
family (B18/K74/K80) and digestive family (K50/K51/K85) are all in the 38, so each
sibling is driven separately and must keep its own protocol.

Run:  ./.venv/bin/python tests/test_protocol_paths.py
      ./.venv/bin/python tests/test_protocol_paths.py --verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_dialogue_state_model as harness  # noqa: E402

orch = harness.orch
CONDITIONS = harness.CONDITIONS
FOLLOWUPS = harness.FOLLOWUPS

failures: list[str] = []
checks = 0
VERBOSE = False


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(label)
    if VERBOSE or not ok:
        print(f"    {'PASS' if ok else 'FAIL'}  {label}"
              + (f"  <- {detail[:180]}" if not ok and detail else ""))


def mandatory_ids(code: str) -> list[str]:
    cond = CONDITIONS[code]
    out = []
    for qid in cond.get("followUps") or []:
        row = FOLLOWUPS.get(qid) or {}
        if row.get("mandatory"):
            out.append(qid)
    return out


def graph_classify(phrase: str) -> dict:
    """Resolve a phrase against the REAL graph, the way Neptune's fast path does.

    The shared harness's FakeTools.classify_condition knows a five-entry alias
    dict - diabetes, sugar, breast cancer, asthma, heart - which is enough for the
    suites that use it and useless here: "Malignant Neoplasm of Breast" matches
    none of them, so 34 of 38 conditions never reached their protocol at all and
    the first run of this file reported 107 failures that were entirely its own.

    This suite is about what happens AFTER a condition is identified. The
    classifier has its own coverage in diag_surface_form_sweep.py, which sweeps all
    369 surface forms against the live index. So resolution here is made exact and
    boring on purpose: match the graph's own labels and colloquials, and report it
    as the corroborated exact-synonym match that Neptune would.
    """
    text = (phrase or "").strip().lower()
    for code, cond in CONDITIONS.items():
        forms = [cond.get("label_en", ""), cond.get("label_hi", "")]
        forms += list(cond.get("colloquial") or [])
        if any(f and f.strip().lower() == text for f in forms):
            tier = int(cond.get("severityTier") or 1)
            high = tier >= 4
            return {
                "phrase": phrase, "icd10": code,
                "label_en": cond["label_en"], "label_hi": cond.get("label_hi"),
                "category": cond.get("category"), "severityTier": tier,
                "score": 1.0, "source": "neptune_exact_synonym",
                "confident": True, "ambiguous": False, "corroborated": True,
                "uncorroborated": False,
                "redFlag": high, "needsConfirmation": high,
                "confirmReason": "high severity condition" if high else None,
                "candidates": [],
            }
    return {"phrase": phrase, "icd10": None, "score": 0.2, "candidates": [],
            "confident": False, "corroborated": False, "needsConfirmation": False}


def drive(code: str, state, tools, extract) -> dict:
    """Disclose one condition and answer its protocol to completion."""
    cond = CONDITIONS[code]
    item = next(row for row in harness.ITEMS if row["itemId"] == "A100")
    identity = {"A010": True, "A020": True, "A030": "1987-03-22"}
    call_id, session = state.reset([item], answers=dict(identity))

    # Disclose using the condition's own English label, which the graph resolves
    # as an exact synonym - so this tests the PROTOCOL, not the classifier.
    phrase = cond["label_en"]
    extract.push(harness.reading(
        170, mentionsCondition=True, conditionPhrase=phrase,
        conditionMentions=[{"phrase": phrase, "scope": "self_current"}]))
    reply = orch.handle_turn(call_id, f"170 and {phrase}", 1, "en")

    # A tier-4+ condition is read back before it is recorded. Confirm it.
    turn = 2
    if (session.get("awaitingConfirmation") or {}).get("icd10"):
        extract.push(harness.reading(True))
        reply = orch.handle_turn(call_id, "yes", turn, "en")
        turn += 1

    # Answer follow-ups until the queue drains.
    asked_refs: list[str] = []
    for _ in range(30):
        pending = session.get("pendingFollowUps") or []
        if not pending:
            break
        ref = reply.get("askedRef") or ""
        if ref:
            asked_refs.append(ref)
        head = pending[0]
        atype = head.get("answerType", "text")
        value = {"boolean": True, "number": 3, "date": "2019-06-01"}.get(atype, "2019")
        extract.push(harness.reading(value))
        reply = orch.handle_turn(call_id, str(value), turn, "en")
        turn += 1
    return {"call_id": call_id, "session": session, "askedRefs": asked_refs}


def main() -> int:  # noqa: PLR0915
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    VERBOSE = args.verbose

    original = (orch.st, orch.tools, orch.extract)
    state = harness.MemoryState()
    tools = harness.FakeTools(state)
    extract = harness.ScriptedExtract()
    tools.classify_condition = graph_classify  # type: ignore[method-assign]
    orch.st, orch.tools, orch.extract = state, tools, extract

    codes = sorted(CONDITIONS)
    print(f"driving all {len(codes)} governed conditions through their protocols\n")
    summary: list[tuple[str, int, int, str]] = []
    try:
        for code in codes:
            cond = CONDITIONS[code]
            want = mandatory_ids(code)
            out = drive(code, state, tools, extract)
            session = out["session"]

            declared = {r.get("icd10") for r in session.get("declaredConditions") or []}
            answers = session.get("followUpAnswers") or {}
            mine = {k: v for k, v in answers.items() if k.startswith(f"{code}::")}
            answered_ids = {k.split("::", 1)[1] for k in mine}
            leftover = session.get("pendingFollowUps") or []

            label = f"{code} {cond['label_en'][:26]}"
            check(f"{label}: recorded under its own code", code in declared,
                  f"declared={declared}")
            check(f"{label}: every mandatory follow-up answered",
                  set(want) <= answered_ids,
                  f"missing={sorted(set(want) - answered_ids)} got={sorted(answered_ids)}")
            check(f"{label}: protocol queue drained", not leftover,
                  f"{len(leftover)} left")
            check(f"{label}: no answers filed against another code",
                  all(k.startswith(f"{code}::") for k in answers),
                  f"foreign={[k for k in answers if not k.startswith(f'{code}::')]}")
            row = next((r for r in session.get("declaredConditions") or []
                        if r.get("icd10") == code), {})
            check(f"{label}: provenance recorded",
                  "corroborated" in row and "confirmedByPatient" in row,
                  str(sorted(row))[:120])

            verdict = "ok" if (code in declared and set(want) <= answered_ids
                               and not leftover) else "PROBLEM"
            summary.append((code, len(want), len(answered_ids), verdict))

        print(f"{'CODE':<7} {'MANDATORY':<10} {'ANSWERED':<9} VERDICT")
        print("-" * 42)
        for code, n_want, n_got, verdict in summary:
            flag = "" if verdict == "ok" else "   <-- "
            print(f"{code:<7} {n_want:<10} {n_got:<9} {verdict}{flag}")

        # TWO PROTOCOLS ALIVE AT ONCE. Every case above discloses ONE condition,
        # so none of them can catch answers being filed against the wrong code -
        # and a real caller volunteers a second disease while working through the
        # first. followUpAnswers is keyed "{icd10}::{questionId}", so a
        # cross-filing bug puts a diabetes answer under an asthma code and the
        # underwriting record reads as though the wrong disease was investigated.
        print("\ntwo conditions in one call, each keeping its own protocol:")
        item = next(row for row in harness.ITEMS if row["itemId"] == "A100")
        for first, second in (("E11", "J45"), ("I25", "N18"), ("J44", "K74")):
            call_id, session = state.reset(
                [item], answers={"A010": True, "A020": True, "A030": "1987-03-22"})
            f_label = CONDITIONS[first]["label_en"]
            s_label = CONDITIONS[second]["label_en"]

            extract.push(harness.reading(
                170, mentionsCondition=True, conditionPhrase=f_label,
                conditionMentions=[{"phrase": f_label, "scope": "self_current"}]))
            reply = orch.handle_turn(call_id, f"170 and {f_label}", 1, "en")
            turn = 2
            if (session.get("awaitingConfirmation") or {}).get("icd10"):
                extract.push(harness.reading(True))
                reply = orch.handle_turn(call_id, "yes", turn, "en")
                turn += 1

            # Disclose the SECOND condition mid-protocol, then answer everything.
            injected = False
            for _ in range(40):
                pending = session.get("pendingFollowUps") or []
                if not pending:
                    break
                head = pending[0]
                atype = head.get("answerType", "text")
                value = {"boolean": True, "number": 3,
                         "date": "2019-06-01"}.get(atype, "2019")
                if not injected:
                    injected = True
                    extract.push(harness.reading(
                        value, mentionsCondition=True, conditionPhrase=s_label,
                        conditionMentions=[{"phrase": s_label,
                                            "scope": "self_current"}]))
                    reply = orch.handle_turn(
                        call_id, f"{value}, and I also have {s_label}", turn, "en")
                else:
                    extract.push(harness.reading(value))
                    reply = orch.handle_turn(call_id, str(value), turn, "en")
                turn += 1
                if (session.get("awaitingConfirmation") or {}).get("icd10"):
                    extract.push(harness.reading(True))
                    reply = orch.handle_turn(call_id, "yes", turn, "en")
                    turn += 1

            declared = {r.get("icd10") for r in session.get("declaredConditions") or []}
            answers = session.get("followUpAnswers") or {}
            pair = f"{first}+{second}"
            check(f"{pair}: both conditions recorded",
                  {first, second} <= declared, f"declared={sorted(declared)}")
            for code in (first, second):
                want = set(mandatory_ids(code))
                got = {k.split("::", 1)[1] for k in answers
                       if k.startswith(f"{code}::")}
                check(f"{pair}: {code} kept its own mandatory follow-ups",
                      want <= got, f"missing={sorted(want - got)}")
            stray = [k for k in answers
                     if not k.startswith((f"{first}::", f"{second}::"))]
            check(f"{pair}: no answer filed against a third code", not stray,
                  str(stray)[:120])
            print(f"  {'PASS' if not stray and {first, second} <= declared else 'FAIL'}"
                  f"  {pair:<10} declared={sorted(declared)} "
                  f"answers={len(answers)}")

        # AN UNGOVERNED CODE, which is a path this work created. A reference code
        # has no graph protocol, so _queue_protocol must fall back to the default
        # three questions AND flag the record - otherwise a correctly identified
        # condition is recorded with no questions behind it and the file still
        # reads as complete.
        print("\nan ungoverned reference code still gets questioned and flagged:")
        call_id, session = state.reset(
            [item], answers={"A010": True, "A020": True, "A030": "1987-03-22"})
        extract.push(harness.reading(
            170, mentionsCondition=True, conditionPhrase="sarcoidosis",
            conditionMentions=[{"phrase": "sarcoidosis", "scope": "self_current"}]))

        def reference_classify(phrase: str) -> dict:
            # D86 as the live classifier now returns it: right code, no graph
            # protocol, therefore uncorroborated.
            return {"phrase": phrase, "icd10": "D86", "label_en": "Sarcoidosis",
                    "label_hi": "", "category": "haematological", "severityTier": 1,
                    "score": 0.8442, "source": "opensearch_knn", "confident": True,
                    "ambiguous": False, "corroborated": False, "uncorroborated": True,
                    "redFlag": False, "needsConfirmation": True,
                    "confirmReason": "not corroborated", "candidates": []}

        saved_classify = tools.classify_condition
        tools.classify_condition = reference_classify  # type: ignore[method-assign]
        try:
            orch.handle_turn(call_id, "170 and sarcoidosis", 1, "en")
            if (session.get("awaitingConfirmation") or {}).get("icd10"):
                extract.push(harness.reading(True))
                orch.handle_turn(call_id, "yes", 2, "en")
            declared = {r.get("icd10") for r in session.get("declaredConditions") or []}
            pending = session.get("pendingFollowUps") or []
            unclassified = session.get("unclassifiedMentions") or []
            row = next((r for r in session.get("declaredConditions") or []
                        if r.get("icd10") == "D86"), {})
            check("ungoverned code is recorded", "D86" in declared, str(declared))
            check("it received the default protocol rather than no questions",
                  len(pending) >= 3, f"{len(pending)} queued")
            check("its protocolSource says default, not a graph traversal",
                  "default" in str((pending[0] if pending else {})
                                   .get("protocolSource") or "").lower(),
                  str((pending[0] if pending else {}).get("protocolSource")))
            check("it is flagged for human review", bool(unclassified),
                  str(unclassified)[:120])
            check("and recorded as uncorroborated",
                  row.get("corroborated") is False, str(row)[:140])
            print(f"  declared={sorted(declared)} queued={len(pending)} "
                  f"flagged={len(unclassified)}")
        finally:
            tools.classify_condition = saved_classify

        print("\nsub-condition families, each sibling driven separately:")
        for name, family in (("diabetes", ["E10", "E11"]),
                             ("thyroid", ["E03", "E05"]),
                             ("respiratory", ["J40", "J44", "J45"]),
                             ("hepatic", ["B18", "K74", "K80"]),
                             ("digestive", ["K50", "K51", "K85"]),
                             ("cardiac", ["I21", "I25", "I50"]),
                             ("neuro", ["G20", "G40", "G45", "I63"]),
                             ("musculoskeletal", ["M06", "M15"])):
            got = {c: v for c, n1, n2, v in
                   [(s[0], s[1], s[2], s[3]) for s in summary] if c in family}
            ok = all(v == "ok" for v in got.values())
            print(f"  {'PASS' if ok else 'FAIL'}  {name:<16} "
                  f"{ {c: v for c, v in got.items()} }")
    finally:
        orch.st, orch.tools, orch.extract = original

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures[:25]:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} protocol-path checks passed across "
          f"{len(codes)} conditions.")
    print("  Every governed condition is recognised, fully questioned, recorded")
    print("  under its own code, and carries its provenance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
