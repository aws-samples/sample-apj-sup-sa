#!/usr/bin/env python
"""
Verifies the Neptune knowledge graph behaves as the architecture claims.

Three claims under test:

  ENFORCES   - a confirmed condition yields a deterministic, ordered set of
               mandatory follow-ups, and the traversal path is returned so
               "why was this question asked?" has a concrete answer.
  INTERPRETS - colloquial Hindi/Hinglish phrases resolve to the right ICD-10
               node via stored synonyms.
  SAFETY     - drug interactions across declared medications are surfaced.

Run:  ./.venv/bin/python tests/test_graph.py
"""
from __future__ import annotations

import json
import os
import sys

import boto3

REGION = os.environ.get("DATA_REGION", "ap-south-1")
FN = os.environ.get("GRAPH_FN", "hvi-graph")

lam = boto3.client("lambda", region_name=REGION)
failures: list[str] = []


def call(payload: dict) -> dict:
    r = lam.invoke(
        FunctionName=FN,
        Payload=json.dumps(payload).encode(),
    )
    body = json.loads(r["Payload"].read())
    if not body.get("ok"):
        raise RuntimeError(f"lambda error: {body.get('error')}")
    return body["result"]


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


def seed_counts() -> dict[str, int]:
    """Expected vertex counts, read from the seed file rather than hardcoded.

    A frozen number is the wrong invariant here. The requirement is that new
    health items can be added without redeployment, so the KG is expected to
    grow - and a hardcoded count turns every legitimate addition into a test
    failure. This assertion previously read '== 33' and broke when a 34th
    condition (S72, femur fracture) was seeded.

    What actually matters is that everything in the seed file reached the graph,
    which is what comparing against the file checks.
    """
    path = os.path.join(os.path.dirname(__file__), "..", "seed", "health-kg.json")
    doc = json.loads(open(path, encoding="utf-8").read())
    return {
        "conditions": len(doc.get("conditions") or []),
        "followUps": len(doc.get("followUpQuestions") or []),
        "medications": len(doc.get("medications") or []),
    }


def main() -> int:
    print("\n[1] Graph contents")
    s = call({"action": "stats"})
    v, e = s["vertices"], s["edges"]
    want = seed_counts()
    print(f"        vertices: {v}")
    print(f"        edges:    {e}")
    print(f"        seed file expects: {want}")
    check(f"all {want['conditions']} seeded conditions loaded",
          v.get("Condition") == want["conditions"],
          f"graph has {v.get('Condition')}, seed file has {want['conditions']}")
    check(f"all {want['followUps']} seeded follow-up questions loaded",
          v.get("FollowUpQuestion") == want["followUps"],
          f"graph has {v.get('FollowUpQuestion')}, seed file has {want['followUps']}")
    check("colloquial synonyms present", v.get("ColloquialTerm", 0) >= 140, str(v.get("ColloquialTerm")))
    check("REQUIRES edges define the protocol", e.get("REQUIRES", 0) >= 150, str(e.get("REQUIRES")))
    check("drug interactions loaded", e.get("INTERACTS_WITH") == 5, str(e.get("INTERACTS_WITH")))

    print("\n[2] ENFORCES: diabetes (E11) mandates a fixed follow-up set")
    r = call({"action": "get_followups", "icd10": "E11", "language": "hinglish"})
    ids = [f["questionId"] for f in r["followUps"]]
    print(f"        {r['conditionLabel']}  ->  {len(ids)} follow-ups")
    for f in r["followUps"]:
        flag = "*" if f["mandatory"] else " "
        print(f"        {flag} {f['questionId']:<24} {f['prompt']}")
    check("E11 found", r["found"])
    check("returns 6 follow-ups", len(ids) == 6, str(len(ids)))
    check("diabetic complications screened", "FU_DIAB_COMPLICATIONS" in ids, str(ids))
    check("insulin status asked", "FU_ON_INSULIN" in ids, str(ids))
    check("HbA1c asked", "FU_HBA1C" in ids, str(ids))
    check("prompts came back in Hinglish",
          any("aap" in (f["prompt"] or "").lower() for f in r["followUps"]),
          "no Hinglish prompt detected")
    check("audit path present", r["protocolSource"].startswith("neptune:Condition/E11"),
          r["protocolSource"])
    check("follow-ups are ordered", [f["order"] for f in r["followUps"]] == sorted(f["order"] for f in r["followUps"]))

    print("\n[3] ENFORCES is deterministic: same query, same answer")
    again = call({"action": "get_followups", "icd10": "E11", "language": "hinglish"})
    check("identical on repeat", [f["questionId"] for f in again["followUps"]] == ids)

    print("\n[4] ENFORCES is condition-specific, not one generic list")
    tb = call({"action": "get_followups", "icd10": "A15", "language": "en"})
    tb_ids = [f["questionId"] for f in tb["followUps"]]
    print(f"        {tb['conditionLabel']}  ->  {tb_ids}")
    check("TB asks about course completion", "FU_TB_COMPLETED" in tb_ids, str(tb_ids))
    check("TB asks about drug resistance", "FU_TB_DRUG_RESISTANT" in tb_ids, str(tb_ids))
    check("TB does not ask about insulin", "FU_ON_INSULIN" not in tb_ids, str(tb_ids))

    cancer = call({"action": "get_followups", "icd10": "C50", "language": "en"})
    c_ids = [f["questionId"] for f in cancer["followUps"]]
    check("cancer asks staging", "FU_CANCER_STAGE" in c_ids, str(c_ids))
    check("cancer asks remission", "FU_REMISSION" in c_ids, str(c_ids))

    print("\n[5] Sensitive questions carry handling guidance")
    dep = call({"action": "get_followups", "icd10": "F32", "language": "en"})
    sh = [f for f in dep["followUps"] if f["questionId"] == "FU_SELF_HARM"]
    check("self-harm question present for depression", len(sh) == 1)
    if sh:
        check("marked maximum sensitivity", sh[0]["sensitivity"] == "maximum", str(sh[0]["sensitivity"]))
        check("carries a clinical instruction", bool(sh[0]["clinicalNote"]))

    print("\n[6] INTERPRETS: colloquial phrases resolve to ICD-10")
    cases = [
        ("sugar ki bimari", "E11"),
        ("madhumeh", "E11"),
        ("BP ki problem", "I10"),
        ("lakwa", "I63"),
        ("mirgi", "G40"),
        ("dama", "J45"),
        ("TB", "A15"),
        ("pathri", "N20"),
        ("मधुमेह", "E11"),
        ("thyroid", "E03"),
    ]
    for phrase, expect in cases:
        r = call({"action": "resolve_condition", "phrase": phrase})
        got = [m["icd10"] for m in r.get("matches", [])] if r.get("found") else []
        ok = expect in got
        print(f"        {'PASS' if ok else 'FAIL'}  {phrase!r:<22} -> {got or 'not found'} "
              f"({r.get('matchType','-')})")
        if not ok:
            failures.append(f"resolve {phrase}")

    print("\n[7] SAFETY: drug interactions surface")
    r = call({"action": "check_interactions",
              "medications": ["Aspirin 75mg", "Warfarin 5mg", "Metformin 500mg"]})
    print(f"        resolved: {r['resolved']}")
    for ix in r["interactions"]:
        print(f"        {ix['severity'].upper():<9} {ix['drugA']} + {ix['drugB']}: {ix['effect'][:70]}")
    check("aspirin/warfarin interaction found", r["interactionCount"] >= 1, str(r["interactionCount"]))
    check("major severity flagged", any(i["severity"] == "major" for i in r["interactions"]))

    r2 = call({"action": "check_interactions", "medications": ["Metformin 500mg"]})
    check("single medication returns nothing to compare", r2["interactions"] == [])

    print("\n" + "=" * 62)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All graph checks passed.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
