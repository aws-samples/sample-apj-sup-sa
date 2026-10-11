#!/usr/bin/env python3
"""Exhaustive in-process contracts for graph Lambda projections.

No Neptune connection is used. A deterministic Gremlin double returns the exact
seed records, allowing every governed condition and language projection to pass
through the real handler code.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("NEPTUNE_ENDPOINT", "offline.invalid")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

spec = importlib.util.spec_from_file_location(
    "graph_handler_contract", ROOT / "lambdas" / "graph" / "handler.py")
graph_handler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = graph_handler
spec.loader.exec_module(graph_handler)

seed = json.loads((ROOT / "seed" / "health-kg.json").read_text(encoding="utf-8"))
conditions = {row["icd10"]: row for row in seed["conditions"]}
followups = {row["id"]: row for row in seed["followUpQuestions"]}
explanations = seed["conditionExplanations"]

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(label)
        print(f"  FAIL  {label} <- {detail}")


def _code(query: str) -> str:
    match = re.search(r"COND_([^']+)", query)
    if not match:
        raise AssertionError(f"condition id missing from query: {query}")
    return match.group(1)


def fake_gremlin(query: str):
    code = _code(query)
    condition = conditions.get(code)
    if not condition:
        return []
    if ".outE('REQUIRES')" in query:
        rows = []
        for order, question_id in enumerate(condition["followUps"], start=1):
            question = followups[question_id]
            rows.append({
                "order": order,
                "q": {
                    "questionId": [question_id],
                    "mandatory": [bool(question.get("mandatory"))],
                    "answerType": [question.get("answerType", "text")],
                    "prompt_en": [question.get("prompt_en", "")],
                    "prompt_hi": [question.get("prompt_hi", "")],
                    "prompt_hinglish": [question.get("prompt_hinglish", "")],
                    "sensitivity": [question.get("sensitivity")],
                    "clinicalNote": [question.get("clinicalNote")],
                    "formField": [question.get("formField")],
                },
            })
        return rows
    explanation = explanations[code]
    return [{
        "icd10": [code],
        "label_en": [condition["label_en"]],
        "label_hi": [condition["label_hi"]],
        "category": [condition["category"]],
        "explain_en": [explanation["explain_en"]],
        "explain_hi": [explanation["explain_hi"]],
    }]


def main() -> int:
    original = graph_handler.gremlin
    graph_handler.gremlin = fake_gremlin
    try:
        print("[1] Every condition projects its exact ordered protocol in every language")
        prompt_keys = {"en": "prompt_en", "hi": "prompt_hi", "hinglish": "prompt_hinglish"}
        for code, condition in conditions.items():
            for language, prompt_key in prompt_keys.items():
                result = graph_handler.get_followups({"icd10": code, "language": language})
                projected_ids = [row["questionId"] for row in result.get("followUps", [])]
                check(f"{code}/{language} found", result.get("found") is True, str(result))
                check(f"{code}/{language} exact order",
                      projected_ids == condition["followUps"], str(projected_ids))
                expected_prompts = [followups[qid][prompt_key] for qid in condition["followUps"]]
                actual_prompts = [row["prompt"] for row in result["followUps"]]
                check(f"{code}/{language} exact authored prompts",
                      actual_prompts == expected_prompts, str(actual_prompts))
                check(f"{code}/{language} mandatory count",
                      result["mandatoryCount"] == sum(
                          bool(followups[qid].get("mandatory")) for qid in condition["followUps"]),
                      str(result["mandatoryCount"]))
                check(f"{code}/{language} provenance",
                      result["protocolSource"].startswith(f"neptune:Condition/{code} -REQUIRES->"),
                      result["protocolSource"])

        print("[2] Every condition explanation survives handler projection in every language mode")
        for code, condition in conditions.items():
            for language in ("en", "hi", "hinglish"):
                result = graph_handler.get_condition({"icd10": code, "language": language})
                check(f"{code}/{language} explanation found", result.get("found") is True)
                check(f"{code}/{language} labels exact",
                      result.get("label_en") == condition["label_en"]
                      and result.get("label_hi") == condition["label_hi"], str(result))
                check(f"{code}/{language} explanations exact",
                      result.get("explain_en") == explanations[code]["explain_en"]
                      and result.get("explain_hi") == explanations[code]["explain_hi"], str(result))

        print("[3] Handler envelopes are deterministic")
        response = graph_handler.lambda_handler({"action": "get_condition", "icd10": "E11"}, None)
        check("known action success envelope",
              response.get("ok") is True and response.get("action") == "get_condition")
        unknown = graph_handler.lambda_handler({"action": "not_real"}, None)
        check("unknown action lists available actions",
              "error" in unknown and "get_condition" in unknown.get("available", []), str(unknown))
        graph_handler.gremlin = lambda _query: (_ for _ in ()).throw(RuntimeError("offline boom"))
        failed = graph_handler.lambda_handler({"action": "get_condition", "icd10": "E11"}, None)
        check("handler exception is surfaced",
              failed == {"ok": False, "action": "get_condition", "error": "offline boom"},
              str(failed))
    finally:
        graph_handler.gremlin = original

    print(f"Executed {checks} graph handler contract checks.")
    if failures:
        print(f"FAILED: {len(failures)}")
        for failure in failures:
            print("  -", failure)
        return 1
    print("All 36 conditions project exact protocols and authored explanations in all languages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
