"""LLM-as-judge: rank model responses against each other on 4-5 dimensions.

When a `reference_response` is provided, the judge also scores how well each candidate
matches the reference (use case: customer has a Gemini Flash response they like, wants
to see which Bedrock model best matches or improves on it).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

DIMENSIONS_BASE = ["correctness", "instruction_following", "completeness", "clarity"]
DIMENSION_LABELS = {
    "correctness": "Correctness",
    "instruction_following": "Instruction-following",
    "completeness": "Completeness",
    "clarity": "Clarity",
    "match_to_reference": "Match to reference",
}


def dimensions_for(has_reference: bool) -> list[str]:
    return DIMENSIONS_BASE + (["match_to_reference"] if has_reference else [])


@dataclass
class JudgeScore:
    model_id: str
    ranks: dict[str, int]      # dimension -> rank (1 = best)
    scores: dict[str, float]   # dimension -> 1-10 absolute score
    rationale: str


@dataclass
class JudgeResult:
    judge_model_id: str
    scores: list[JudgeScore]
    overall_winner: Optional[str]
    raw_response: str
    has_reference: bool = False
    error: Optional[str] = None


def _build_prompt(user_prompt: str, responses: list[tuple[str, str]], reference: Optional[str]) -> str:
    """Build the judging prompt. `responses` is a list of (label, text)."""
    blocks = []
    for label, text in responses:
        snippet = text if text.strip() else "(empty response)"
        blocks.append(f"### Response {label}\n{snippet}")
    response_section = "\n\n".join(blocks)

    dim_descriptions = [
        ("correctness", "factual accuracy and freedom from errors"),
        ("instruction_following", "adherence to the format, constraints, and structure requested in the prompt"),
        ("completeness", "whether every part of the prompt is addressed"),
        ("clarity", "writing quality, structure, readability"),
    ]
    if reference:
        dim_descriptions.append((
            "match_to_reference",
            "how closely this candidate matches or improves on the reference response in substance and tone — high score for matching or beating the reference, lower for missing key points or being noticeably worse",
        ))

    dim_list = "\n".join(f"  - {d}: {desc}" for d, desc in dim_descriptions)
    n_dims = len(dim_descriptions)

    schema_ranks = {d: 1 for d, _ in dim_descriptions}
    schema_scores = {d: 9.0 for d, _ in dim_descriptions}
    schema_example = {
        "rankings": [
            {
                "label": "A",
                "ranks": schema_ranks,
                "scores": schema_scores,
                "rationale": "one short sentence on why this response ranks where it does",
            }
        ],
        "overall_winner": "A",
    }

    reference_section = ""
    reference_instruction = ""
    if reference:
        reference_section = f'\n\nREFERENCE RESPONSE (target quality — what a good answer looks like):\n"""\n{reference}\n"""'
        reference_instruction = (
            "\n7. The reference response represents the target quality the user is aiming for. "
            "When scoring `match_to_reference`, give 10 to a candidate that fully matches or improves on the reference, "
            "5-7 to one that is similar in substance but weaker in tone or detail, and 1-4 to one that misses key points "
            "the reference covers or contradicts the reference."
        )

    return f"""You are an impartial evaluator. Compare the candidate responses below to the user's prompt and rank them on {n_dims} dimensions.

USER PROMPT:
\"\"\"
{user_prompt}
\"\"\"{reference_section}

CANDIDATE RESPONSES TO EVALUATE:
{response_section}

DIMENSIONS:
{dim_list}

INSTRUCTIONS:
1. For each candidate, assign a RANK (1 = best, 2 = next, ... ties allowed only if responses are truly indistinguishable) on each dimension independently.
2. Also assign an ABSOLUTE SCORE from 1.0 to 10.0 on each dimension.
3. Pick an overall_winner label based on aggregate quality.
4. Each rationale must be ONE short sentence (under 25 words).
5. Be strict and discriminating. Avoid giving everything the same rank.
6. Do not be biased by response length or verbosity.{reference_instruction}

Return ONLY valid JSON in this exact shape (no markdown fences, no commentary before or after):

{json.dumps(schema_example, indent=2)}

The "rankings" array must contain exactly {len(responses)} entries, one per response label: {", ".join(label for label, _ in responses)}."""


def _strip_code_fences(text: str) -> str:
    """Pull JSON out of ```json ... ``` if a model wraps it despite being told not to."""
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def _parse_response(raw: str) -> tuple[list[dict], Optional[str]]:
    cleaned = _strip_code_fences(raw)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in judge response")
    obj = json.loads(cleaned[start : end + 1])
    rankings = obj.get("rankings", [])
    if not isinstance(rankings, list):
        raise ValueError("'rankings' is not a list")
    return rankings, obj.get("overall_winner")


def evaluate(
    region: str,
    judge_model_id: str,
    user_prompt: str,
    responses: list[tuple[str, str, str]],
    reference_response: Optional[str] = None,
    max_tokens: int = 2000,
) -> JudgeResult:
    """`responses` is a list of (label, model_id, text). Returns judge scores keyed by model_id.

    When `reference_response` is provided, an additional `match_to_reference` dimension is scored.
    """
    runtime = boto3.client("bedrock-runtime", region_name=region)
    payload = [(label, text) for label, _, text in responses]
    has_ref = bool(reference_response and reference_response.strip())
    judge_prompt = _build_prompt(user_prompt, payload, reference_response if has_ref else None)
    dims = dimensions_for(has_ref)

    raw = ""
    try:
        resp = runtime.converse(
            modelId=judge_model_id,
            messages=[{"role": "user", "content": [{"text": judge_prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
        )
        for block in resp["output"]["message"]["content"]:
            if "text" in block:
                raw += block["text"]

        rankings, winner = _parse_response(raw)
        label_to_model = {label: model_id for label, model_id, _ in responses}

        scores: list[JudgeScore] = []
        for entry in rankings:
            label = entry.get("label")
            model_id = label_to_model.get(label)
            if model_id is None:
                continue
            ranks_in = entry.get("ranks", {}) or {}
            scores_in = entry.get("scores", {}) or {}
            scores.append(
                JudgeScore(
                    model_id=model_id,
                    ranks={d: int(ranks_in.get(d, 0) or 0) for d in dims},
                    scores={d: float(scores_in.get(d, 0) or 0) for d in dims},
                    rationale=str(entry.get("rationale", ""))[:300],
                )
            )

        winner_model = label_to_model.get(winner) if winner else None
        return JudgeResult(
            judge_model_id=judge_model_id,
            scores=scores,
            overall_winner=winner_model,
            raw_response=raw,
            has_reference=has_ref,
        )

    except (ClientError, BotoCoreError) as e:
        return JudgeResult(
            judge_model_id=judge_model_id,
            scores=[],
            overall_winner=None,
            raw_response=raw,
            has_reference=has_ref,
            error=str(e),
        )
    except (ValueError, json.JSONDecodeError, KeyError) as e:
        return JudgeResult(
            judge_model_id=judge_model_id,
            scores=[],
            overall_winner=None,
            raw_response=raw,
            has_reference=has_ref,
            error=f"failed to parse judge response: {e}",
        )
