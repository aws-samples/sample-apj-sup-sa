"""Tests for just-in-time domain question generation (uncovered-role JD generation).

_coerce is pure (no DB/Bedrock). generate_domain_questions is exercised against the local DB with the
Bedrock generate + embed calls monkeypatched, proving: rows persist as source='generated'
status='approved' with an embedding, are returned plan-ready, and re-prep is idempotent (upsert).
"""

from __future__ import annotations

import uuid

import pytest

from src.prep import jit_generate


def test_coerce_parses_and_drops_empty():
    raw = (
        '```json\n{"questions":[{"prompt_template":"Walk me through designing a CI/CD pipeline.",'
        '"follow_up_prompts":["What did you automate?","How did you handle rollback?"]},'
        '{"prompt_template":"  ","follow_up_prompts":[]}]}\n```'
    )
    out = jit_generate._coerce(raw)
    assert len(out) == 1  # the empty prompt_template is dropped
    assert out[0]["prompt_template"].startswith("Walk me through")
    assert len(out[0]["follow_up_prompts"]) == 2


@pytest.mark.asyncio
async def test_generate_persists_embeds_and_returns_plan_rows(pool, monkeypatch):
    # Stub the two Bedrock calls so the test is offline + deterministic.
    monkeypatch.setattr(jit_generate, "_generate_questions", lambda jt, jd, diff, n: [
        {"prompt_template": "Tell me about a time you owned infrastructure as code on AWS.",
         "follow_up_prompts": ["Which modules did you build?", "How did you test the IaC?"]},
        {"prompt_template": "Describe debugging a production incident in a cloud environment.",
         "follow_up_prompts": ["How did you find root cause?"]},
    ])
    monkeypatch.setattr(jit_generate, "_embed", lambda text: [0.01] * 1024)

    rows = await jit_generate.generate_domain_questions(
        "Senior Cloud Engineer", "AWS, Terraform, CI/CD, EKS, observability.", "moderate",
        role_family=None, n=2,
    )
    assert len(rows) == 2
    for r in rows:
        assert r["category"] == "domain" and r["competency"] == "role_specific"
        assert r["difficulty"] == "moderate"
        assert r["prompt_template"]
        # persisted + embedded + approved -> selectable shape
        row = await pool.fetchrow(
            "SELECT category, competency, difficulty, source, status, role_family, "
            "(embedding IS NOT NULL) AS embedded FROM question_archetype WHERE id=$1",
            uuid.UUID(r["id"]),
        )
        assert row["status"] == "approved" and row["source"] == "generated"
        assert row["embedded"] is True
        assert row["role_family"].startswith("jit_")  # novel role -> synthesized family

    # Idempotent: re-prep the same role upserts (no duplicate rows).
    rows2 = await jit_generate.generate_domain_questions(
        "Senior Cloud Engineer", "AWS, Terraform, CI/CD, EKS, observability.", "moderate",
        role_family=None, n=2,
    )
    ids1 = {r["id"] for r in rows}
    ids2 = {r["id"] for r in rows2}
    assert ids1 == ids2, "re-prep should upsert the same ids, not duplicate"

    # cleanup
    for rid in ids1:
        await pool.execute("DELETE FROM question_archetype WHERE id=$1", uuid.UUID(rid))


@pytest.mark.asyncio
async def test_generation_failure_returns_empty_keeps_fallback(pool, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bedrock down")
    monkeypatch.setattr(jit_generate, "_generate_questions", boom)
    rows = await jit_generate.generate_domain_questions("X", "y", "easy", None, n=3)
    assert rows == []  # never raises — caller keeps the General-only fallback
