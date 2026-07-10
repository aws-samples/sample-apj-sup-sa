"""F008 US4: cross-session coaching guidance generation (report-worker side).

Offline — the Bedrock call is injected. Proves: grounding material flows into the digest the
model sees; the contract is validated/clamped (next_actions 2-3); ONE retry then None on
malformed output (keep-previous semantics); single-session material still generates; bookkeeping
(sessions_analyzed, rubric_versions, model_id) is stamped.
"""

from __future__ import annotations

import pytest

from src.config import Config
from src.guidance import _material_digest, build_guidance


def _material(n: int = 2) -> list[dict]:
    return [
        {
            "overall": 6.0 + i, "score_content": 6.0, "score_structure": 5.5 + i,
            "score_communication": 7.0, "score_confidence": 6.5,
            "difficulty": "moderate", "rubric_version": "g3-2026.1",
            "summary_strengths": [f"Concrete project examples (session {i + 1})"],
            "summary_improvements": ["Results lack metrics"],
            "competency_scorecard": [
                {"competency": "Leadership", "score_1_5": 3 + i, "assessed": True,
                 "evidence_quote": "I paired with them live"},
            ],
            "created_at": f"2026-06-{10 + i}T10:00:00", "job_title": "Cloud Engineer",
        }
        for i in range(n)
    ]


def _good_output() -> dict:
    return {
        "strengths": ["You consistently anchor answers in real projects."],
        "improvement_areas": ["Your Result statements stay unquantified across sessions."],
        "trend_note": "Structure improved from your first to your latest session.",
        "next_actions": ["Prepare two metrics for your pipeline project.",
                         "Close each answer with one outcome sentence."],
    }


def _cfg() -> Config:
    return Config.load()


def test_digest_carries_grounding_material():
    digest = _material_digest(_material())
    assert "Cloud Engineer" in digest
    assert "I paired with them live" in digest       # evidence quotes reach the model
    assert "rubric: g3-2026.1" in digest             # honesty context
    assert "Session 1" in digest and "Session 2" in digest


def test_build_guidance_stamps_bookkeeping():
    seen: list = []

    def fake_generate(material, config):
        seen.append(material)
        return _good_output()

    payload = build_guidance(_material(3), _cfg(), generate_fn=fake_generate)
    assert payload is not None
    assert payload["sessions_analyzed"] == 3
    assert payload["rubric_versions"] == ["g3-2026.1"]
    assert payload["model_id"] == _cfg().bedrock_model_id
    assert len(seen) == 1 and len(seen[0]) == 3      # the material reached the generator


def test_next_actions_clamped_to_three():
    out = _good_output()
    out["next_actions"] = ["a", "b", "c", "d", "e"]
    payload = build_guidance(_material(), _cfg(), generate_fn=lambda m, c: out)
    assert len(payload["next_actions"]) == 3


def test_malformed_output_retries_once_then_none():
    calls = {"n": 0}

    def bad_generate(material, config):
        calls["n"] += 1
        raise ValueError("not json")

    assert build_guidance(_material(), _cfg(), generate_fn=bad_generate) is None
    assert calls["n"] == 2  # exactly one retry


def test_invalid_shape_rejected_then_recovered_on_retry():
    outputs = [{"strengths": []}, _good_output()]  # first invalid (empty), second good

    def gen(material, config):
        return outputs.pop(0)

    payload = build_guidance(_material(), _cfg(), generate_fn=gen)
    assert payload is not None and payload["sessions_analyzed"] == 2


def test_single_session_material_generates():
    payload = build_guidance(_material(1), _cfg(), generate_fn=lambda m, c: _good_output())
    assert payload is not None and payload["sessions_analyzed"] == 1


def test_empty_material_yields_none():
    assert build_guidance([], _cfg(), generate_fn=lambda m, c: _good_output()) is None
