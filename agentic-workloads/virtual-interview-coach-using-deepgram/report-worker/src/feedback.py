"""Per-question feedback (FR-306 / SC-004 / Principle V).

For each assessed question, produce: what worked, what to improve (naming the specific missing STAR
element on a vague answer), and a strong-answer example BUILT FROM THE STUDENT'S OWN confirmed resume
material arranged into the STAR structure — never a generic script and never invented experience.

Runs in the async worker (off the live path). No raw transcript/resume text is logged (Principle III).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .config import Config

log = logging.getLogger("report_worker")

_STAR = ("situation", "task", "action", "result")

_FEEDBACK_SYSTEM = (
    "You are an encouraging but honest interview coach giving per-answer feedback. Work ONLY from the "
    "candidate's actual answer and their confirmed resume facts provided to you. Be specific and "
    "self-referential — reference what THIS candidate said and what is on THEIR resume; never give "
    "generic advice.\n\n"
    "For the answer, return ONLY valid JSON, no prose:\n"
    "{\n"
    '  "what_worked": string,            // concrete, references their words\n'
    '  "what_to_improve": string,        // if vague, name the missing STAR element specifically\n'
    '  "star_coverage": {"situation": bool, "task": bool, "action": bool, "result": bool},\n'
    '  "strong_answer_example": string   // a model answer built ONLY from the resume facts given, '
    "arranged into Situation-Task-Action-Result. Do NOT invent experience the candidate does not have.\n"
    "}"
)


@dataclass
class QuestionFeedback:
    turn_index: int | None
    archetype_id: str | None
    competency: str | None
    question_text: str
    student_transcript: str
    what_worked: str | None
    what_to_improve: str | None
    strong_answer_example: str | None
    star_coverage: dict = field(default_factory=dict)
    evidence_quote: str | None = None


def _resume_brief(resume_facts: dict | None) -> str:
    """A compact, model-facing brief of the student's confirmed resume facts for grounding the strong
    answer. Minimized — only what's needed to arrange a STAR example (Principle V)."""
    if not resume_facts:
        return "(no confirmed resume facts available)"
    parts: list[str] = []
    if resume_facts.get("summary"):
        parts.append(f"Summary: {resume_facts['summary']}")
    skills = resume_facts.get("skills") or []
    if skills:
        parts.append("Skills: " + ", ".join(str(s) for s in skills[:15]))
    for exp in (resume_facts.get("experience") or [])[:5]:
        title = exp.get("title", "")
        org = exp.get("organization") or ""
        highlights = "; ".join(exp.get("highlights") or [])
        parts.append(f"Experience: {title} at {org} — {highlights}")
    return "\n".join(parts)


def _coerce_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    return json.loads(text)


def _feedback_once(question: str, answer: str, resume_brief: str, config: Config) -> dict:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=config.aws_region)
    user = (
        f"Confirmed resume facts:\n{resume_brief}\n\n"
        f"Interview question:\n{question}\n\n"
        f"Candidate's answer:\n{answer[:6000]}"
    )
    resp = client.converse(
        modelId=config.bedrock_model_id,
        system=[{"text": _FEEDBACK_SYSTEM}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": 1200, "temperature": config.scoring_temperature},
    )
    return _coerce_json(resp["output"]["message"]["content"][0]["text"])


def build_question_feedback(
    qa_pairs: list[dict],
    resume_facts: dict | None,
    config: Config,
    *,
    feedback_fn=None,
) -> list[QuestionFeedback]:
    """Produce feedback for each (question, answer) pair. `qa_pairs` items:
    {question_text, student_transcript, turn_index, archetype_id, competency}. `feedback_fn` is
    injectable for tests."""
    fn = feedback_fn or _feedback_once
    brief = _resume_brief(resume_facts)
    out: list[QuestionFeedback] = []
    for qa in qa_pairs:
        question = qa.get("question_text", "")
        answer = qa.get("student_transcript", "")
        if not answer.strip():
            continue
        try:
            data = fn(question, answer, brief, config)
        except Exception as exc:  # noqa: BLE001 - one question's feedback failing is non-fatal
            log.warning("question feedback failed (%s); emitting minimal entry", type(exc).__name__)
            data = {}
        cov = data.get("star_coverage") or {}
        out.append(
            QuestionFeedback(
                turn_index=qa.get("turn_index"),
                archetype_id=qa.get("archetype_id"),
                competency=qa.get("competency"),
                question_text=question,
                student_transcript=answer,
                what_worked=data.get("what_worked"),
                what_to_improve=data.get("what_to_improve"),
                strong_answer_example=data.get("strong_answer_example"),
                star_coverage={k: bool(cov.get(k, False)) for k in _STAR},
            )
        )
    return out
