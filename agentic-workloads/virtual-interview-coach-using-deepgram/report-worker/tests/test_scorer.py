"""Scoring-core unit tests (SC-001 consistency + SC-002 evidence) — no Bedrock, no DB.

These prove the two Gate G3 mechanics offline with an injected stub model:
  - median-of-N self-consistency keeps the overall stable (< 0.5 pt) even when individual runs jitter;
  - every competency evidence quote is validated against the transcript — a fabricated quote is dropped
    and that competency is marked not-assessed (never invented).
"""

from __future__ import annotations

from src.config import Config
from src.scorer import score_session, build_transcript_text


TURNS = [
    {"turn_index": 0, "speaker": "coach", "transcript": "Tell me about a hard problem you solved."},
    {"turn_index": 1, "speaker": "student",
     "transcript": "At my fintech internship I built an event-driven payments reconciliation pipeline "
                   "in Python that processed a terabyte of data daily and cut manual checks by half."},
    {"turn_index": 2, "speaker": "coach", "transcript": "How did you debug it in production?"},
    {"turn_index": 3, "speaker": "student",
     "transcript": "I reproduced the mismatch locally, added structured logging, and traced it to a "
                   "timezone bug in the batch window."},
]


def _config(samples: int) -> Config:
    import os
    os.environ["SCORING_SAMPLES"] = str(samples)
    os.environ["SCORING_TEMPERATURE"] = "0"
    os.environ.setdefault("AWS_REGION", "us-west-2")
    return Config.load()


def _stub(jitter: list[float]):
    """Return a score_fn that yields slightly different overalls per call (simulating residual model
    nondeterminism), with one real and one fabricated competency quote."""
    calls = {"i": 0}

    def fn(transcript, competencies, config):
        i = calls["i"]
        calls["i"] += 1
        overall = 7.0 + jitter[i % len(jitter)]
        return {
            "content": overall, "structure": overall - 0.5, "communication": overall,
            "confidence": overall, "overall": overall,
            "strengths": ["clear ownership"], "improvements": ["quantify results more"],
            "competencies": [
                # real verbatim span from the student transcript -> must validate
                {"competency": "problem_solving", "score_1_5": 4,
                 "evidence_quote": "built an event-driven payments reconciliation pipeline",
                 "star_element": "action"},
                # fabricated quote the student never said -> must be dropped
                {"competency": "leadership", "score_1_5": 3,
                 "evidence_quote": "I personally managed a team of twelve engineers across three offices",
                 "star_element": "situation"},
            ],
        }

    return fn


def test_median_self_consistency_keeps_overall_stable():
    # Individual runs jitter by +/-0.4 but the MEDIAN is rock-stable; repeated scoring varies < 0.5 pt.
    cfg = _config(samples=3)
    overalls = []
    for _ in range(5):  # score the same transcript 5 separate times
        res = score_session(TURNS, ["problem_solving", "leadership"], cfg, score_fn=_stub([-0.4, 0.0, 0.4]))
        overalls.append(res.overall)
    spread = max(overalls) - min(overalls)
    assert spread < 0.5, f"overall varied by {spread} >= 0.5 (NFR-8 violated)"


def test_fabricated_quote_dropped_and_competency_not_assessed():
    cfg = _config(samples=3)
    res = score_session(TURNS, ["problem_solving", "leadership"], cfg, score_fn=_stub([0.0]))
    by = {c.competency: c for c in res.competencies}
    # the real quote validated -> assessed with evidence
    assert by["problem_solving"].assessed is True
    assert by["problem_solving"].evidence_quote is not None
    assert by["problem_solving"].turn_index == 1
    # the fabricated quote dropped -> not-assessed, no invented quote
    assert by["leadership"].assessed is False
    assert by["leadership"].evidence_quote is None


def test_every_assessed_quote_is_present_in_transcript():
    # SC-002: 100% of assessed competency quotes are verbatim substrings of the student's words.
    cfg = _config(samples=1)
    res = score_session(TURNS, ["problem_solving", "leadership"], cfg, score_fn=_stub([0.0]))
    corpus = " ".join(t["transcript"].lower() for t in TURNS if t["speaker"] == "student")
    for c in res.competencies:
        if c.assessed:
            assert c.evidence_quote.lower() in corpus, f"{c.competency} quote not in transcript"


def test_transcript_render_labels_speakers():
    text = build_transcript_text(TURNS)
    assert "Interviewer:" in text and "Candidate:" in text
