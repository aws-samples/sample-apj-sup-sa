"""Scoring core (FR-302/303/304/305/308 — the heart of Gate G3).

A completed interview is scored against a FIXED, LEVEL-INDEPENDENT rubric: four 0-10 sub-scores
(Content/Relevance, Structure/STAR, Communication/Clarity, Confidence) + an overall, and each assessed
competency on a 1-5 anchored scale. To meet NFR-8 (< 0.5 pt variance) the model runs at temperature 0
and each scoring is repeated N times then aggregated by MEDIAN (self-consistency — R2). Every evidence
quote the model returns is validated against the student transcript (evidence.py); unvalidated quotes
are dropped and that competency is marked not-assessed (FR-305 / SC-002) — never fabricated.

The difficulty tier is NOT a scoring input: a score means the same thing at every tier (Principle II).
The tier is recorded on the report as context only (persistence.py), never blended into a number.

No raw transcript text is logged here (Principle III) — only structural counts.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field

from .config import Config
from .evidence import build_student_corpus, validate_quote

log = logging.getLogger("report_worker")

# The four headline sub-scores (0-10), fixed and level-independent (FR-302).
SUBSCORES = ("content", "structure", "communication", "confidence")

# The fixed rubric system prompt. Level-independent by construction: it never mentions difficulty and
# anchors each band to observable answer properties, so the same answer maps to the same score
# regardless of the session's tier (Principle II). Versioned via Config.rubric_version.
_RUBRIC_SYSTEM = (
    "You are a strict, consistent interview assessor applying a FIXED rubric. Score ONLY on what the "
    "candidate actually said. A score means the same thing regardless of interview difficulty — do "
    "NOT inflate or deflate for difficulty. Be deterministic: identical input yields identical output.\n\n"
    "Score four dimensions on an integer-friendly 0-10 scale (decimals allowed):\n"
    "  - content: relevance and substance of the answers to the questions asked.\n"
    "  - structure: STAR completeness (Situation, Task, Action, Result) and logical organization.\n"
    "  - communication: clarity, conciseness, and absence of hedging/filler.\n"
    "  - confidence: assured, specific, ownership language vs. vague/uncertain phrasing.\n"
    "0-2 = absent/very weak, 3-4 = weak, 5-6 = adequate, 7-8 = strong, 9-10 = excellent.\n\n"
    "Also score each assessed COMPETENCY on a 1-5 anchored scale (1 = not demonstrated, 3 = partially, "
    "5 = clearly demonstrated with specifics), and for each provide a VERBATIM quote copied EXACTLY "
    "from the candidate's own words that best evidences the score, plus which STAR element it shows. "
    "If you cannot find a real verbatim quote for a competency, set its quote to null — do NOT invent "
    "or paraphrase one.\n\n"
    "Return ONLY valid JSON, no prose:\n"
    "{\n"
    '  "content": number, "structure": number, "communication": number, "confidence": number,\n'
    '  "overall": number,\n'
    '  "strengths": [string, ...], "improvements": [string, ...],\n'
    '  "competencies": [\n'
    '     {"competency": string, "score_1_5": number, "evidence_quote": string|null, '
    '"star_element": "situation"|"task"|"action"|"result"|null}\n'
    "  ]\n"
    "}"
)


@dataclass
class CompetencyScore:
    competency: str
    score_1_5: float
    evidence_quote: str | None
    star_element: str | None
    turn_index: int | None
    assessed: bool


@dataclass
class ScoreResult:
    content: float
    structure: float
    communication: float
    confidence: float
    overall: float
    strengths: list[str]
    improvements: list[str]
    competencies: list[CompetencyScore] = field(default_factory=list)


def build_transcript_text(turns: list[dict]) -> str:
    """Render the ordered turns as a labelled transcript for the scoring prompt (coach/student)."""
    lines = []
    for t in turns:
        who = "Interviewer" if t.get("speaker") == "coach" else "Candidate"
        lines.append(f"{who}: {t.get('transcript', '').strip()}")
    return "\n".join(lines)


def _coerce_json(raw: str) -> dict:
    """Parse the model's JSON, tolerating a ```json fence."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    return json.loads(text)


def _score_once(transcript: str, competencies: list[str], config: Config) -> dict:
    """One Bedrock scoring pass at temperature 0. Returns the parsed JSON dict (or raises)."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name=config.aws_region)
    comp_hint = (
        f"Assess these competencies: {', '.join(competencies)}.\n\n" if competencies else ""
    )
    resp = client.converse(
        modelId=config.bedrock_model_id,
        system=[{"text": _RUBRIC_SYSTEM}],
        messages=[{"role": "user", "content": [{"text": comp_hint + "Transcript:\n" + transcript[:20000]}]}],
        inferenceConfig={"maxTokens": config.scoring_max_tokens, "temperature": config.scoring_temperature},
    )
    return _coerce_json(resp["output"]["message"]["content"][0]["text"])


def _median(values: list[float]) -> float:
    return round(float(statistics.median(values)), 2)


def score_session(
    turns: list[dict],
    competencies: list[str],
    config: Config,
    *,
    score_fn=None,
) -> ScoreResult:
    """Score a completed session with self-consistency (median of N temp-0 passes) and validated
    evidence quotes. `score_fn` is injectable for tests (defaults to the live Bedrock call).

    Aggregation (R2): numeric sub-scores + overall + per-competency 1-5 are the MEDIAN across N runs;
    competency evidence quotes are taken from the run whose 1-5 equals the median (first such), then
    validated against the transcript — an unvalidated quote drops the competency to not-assessed."""
    fn = score_fn or _score_once
    transcript = build_transcript_text(turns)
    corpus = build_student_corpus(turns)

    runs: list[dict] = []
    for i in range(max(1, config.scoring_samples)):
        try:
            runs.append(fn(transcript, competencies, config))
        except Exception as exc:  # noqa: BLE001 - a failed sample is skipped, not fatal
            log.warning("scoring sample %d failed (%s)", i, type(exc).__name__)
    if not runs:
        raise RuntimeError("all scoring samples failed")

    def med(key: str) -> float:
        vals = [float(r[key]) for r in runs if isinstance(r.get(key), (int, float))]
        return _median(vals) if vals else 0.0

    sub = {k: med(k) for k in SUBSCORES}
    # Overall is the median of the model's own overall when present, else the mean of the four
    # sub-scores — a level-independent absolute, never blended with difficulty.
    overall_vals = [float(r["overall"]) for r in runs if isinstance(r.get("overall"), (int, float))]
    overall = _median(overall_vals) if overall_vals else round(sum(sub.values()) / len(sub), 2)

    # Strengths/improvements: take from the first run (qualitative, not aggregated numerically).
    first = runs[0]
    strengths = [str(s) for s in (first.get("strengths") or [])][:5]
    improvements = [str(s) for s in (first.get("improvements") or [])][:5]

    competencies_out = _aggregate_competencies(runs, corpus, turns)

    return ScoreResult(
        content=sub["content"],
        structure=sub["structure"],
        communication=sub["communication"],
        confidence=sub["confidence"],
        overall=overall,
        strengths=strengths,
        improvements=improvements,
        competencies=competencies_out,
    )


def _aggregate_competencies(runs: list[dict], corpus: str, turns: list[dict]) -> list[CompetencyScore]:
    """Per competency: median 1-5 across runs; evidence quote from a run at the median score, validated
    against the transcript (dropped if not present -> assessed=False). FR-304/305."""
    # Gather per-competency scores + candidate quotes across runs.
    by_comp: dict[str, list[dict]] = {}
    for r in runs:
        for c in r.get("competencies") or []:
            name = c.get("competency")
            if not name:
                continue
            by_comp.setdefault(str(name), []).append(c)

    out: list[CompetencyScore] = []
    for name, entries in by_comp.items():
        scores = [float(e["score_1_5"]) for e in entries if isinstance(e.get("score_1_5"), (int, float))]
        if not scores:
            continue
        median_score = _median(scores)
        # pick the candidate quote from an entry whose score is closest to the median
        best = min(entries, key=lambda e: abs(float(e.get("score_1_5", 0)) - median_score))
        validated = validate_quote(best.get("evidence_quote"), corpus)
        assessed = validated is not None
        out.append(
            CompetencyScore(
                competency=name,
                score_1_5=median_score,
                evidence_quote=validated,
                star_element=best.get("star_element") if assessed else None,
                turn_index=_find_turn_index(validated, turns) if assessed else None,
                assessed=assessed,
            )
        )
    log.info(
        "scored %d competenc(ies): %d assessed with validated evidence, %d not-assessed",
        len(out), sum(1 for c in out if c.assessed), sum(1 for c in out if not c.assessed),
    )
    return out


def _find_turn_index(quote: str | None, turns: list[dict]) -> int | None:
    """Best-effort: which student turn contains the validated quote (for per-answer linkage)."""
    if not quote:
        return None
    from .evidence import _normalize

    nq = _normalize(quote)
    for t in turns:
        if t.get("speaker") == "student" and nq in _normalize(t.get("transcript", "")):
            return t.get("turn_index")
    return None
