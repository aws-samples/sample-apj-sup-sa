"""F008 US4: the coaching-guidance read API + its place in the delete fan-out.

DB-backed (local pgvector container; skipped without DATABASE_URL). The endpoint is keyed by
the caller's own sub — owner scoping is structural (no foreign id to probe, SC-006).
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_guidance(pool, user_sub: str) -> None:
    await pool.execute(
        """
        INSERT INTO coaching_guidance
            (user_sub, generated_at, sessions_analyzed, rubric_versions,
             strengths, improvement_areas, trend_note, next_actions, model_id)
        VALUES ($1, now(), 3, ARRAY['g3-2026.1'], $2, $3, 'Improving steadily.', $4, 'test-model')
        """,
        user_sub,
        json.dumps(["Grounded answers"]),
        json.dumps(["Quantify results"]),
        json.dumps(["Prepare two metrics", "Practice closing statements"]),
    )


async def test_get_guidance_returns_decoded_row(pool):
    from src import db

    sub = f"owner-{uuid.uuid4()}"
    await _seed_guidance(pool, sub)
    try:
        row = await db.get_guidance(sub)
        assert row is not None
        assert row["sessions_analyzed"] == 3
        assert row["strengths"] == ["Grounded answers"]          # JSONB decoded to real lists
        assert row["next_actions"] == ["Prepare two metrics", "Practice closing statements"]
        assert row["trend_note"] == "Improving steadily."
    finally:
        await pool.execute("DELETE FROM coaching_guidance WHERE user_sub=$1", sub)


async def test_get_guidance_none_for_unknown_user(pool):
    from src import db

    assert await db.get_guidance(f"owner-{uuid.uuid4()}") is None


async def test_delete_fanout_removes_guidance(pool, monkeypatch):
    """Constitution III: the derived guidance row is inside the account-delete blast radius."""
    from src import audio_store, db

    monkeypatch.setattr(audio_store, "delete_objects", lambda uris: len(uris))
    monkeypatch.setattr(audio_store, "delete_session_prefix", lambda sid: 0)
    sub = f"owner-{uuid.uuid4()}"
    await _seed_guidance(pool, sub)
    await db.delete_user_cascade(sub)
    assert await pool.fetchval(
        "SELECT count(*) FROM coaching_guidance WHERE user_sub=$1", sub
    ) == 0
