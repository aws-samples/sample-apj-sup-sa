"""SC-008 no-score gate evidence (T043) — FR-212a / Principle II.

F002 records STRUCTURAL facts only — never a constructed score. This test asserts the guarantee at
the schema level (the durable surface) so a future migration that adds a score column would fail it:

  - No column on any F002-relevant table is named like a score/rating/grade/assessment, EXCEPT the
    two explicitly-allowed G3 reference fields that are STORED but never applied in F002:
    question_archetype.scoring_guidance (rubric anchors) and difficulty_profile.scoring_strictness
    (a lever stored for G3). Neither is a per-session/per-turn score.
  - conversation_turn — the only per-turn record F002 writes — carries the structural columns
    (archetype_id, is_followup, targeted_star_element) and no score column.
  - The session records its difficulty + rubric_version as their own columns (never blended into a
    composite score).

Runs against the live schema in the local pgvector container (information_schema), so it reflects
exactly what db_migrate.py produced.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

# Score-ish name fragments that must NOT appear as F002 columns.
_SCORE_LIKE = ("score", "rating", "grade", "points", "assessment", "evaluation", "verdict")

# The only allowed matches: G3 reference data stored but not applied in F002.
_ALLOWED = {
    ("question_archetype", "scoring_guidance"),
    ("difficulty_profile", "scoring_strictness"),
}

_F002_TABLES = (
    "voice_session", "conversation_turn", "users",
    "question_archetype", "difficulty_profile", "interview_blueprint",
)


async def test_no_score_columns_on_f002_tables(pool):
    rows = await pool.fetch(
        """
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = ANY($1::text[])
        """,
        list(_F002_TABLES),
    )
    offenders = [
        (r["table_name"], r["column_name"])
        for r in rows
        if any(frag in r["column_name"].lower() for frag in _SCORE_LIKE)
        and (r["table_name"], r["column_name"]) not in _ALLOWED
    ]
    assert offenders == [], f"unexpected score-like column(s) found (SC-008 violation): {offenders}"


async def test_conversation_turn_has_structural_facts_only(pool):
    cols = {
        r["column_name"]
        for r in await pool.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='conversation_turn'"
        )
    }
    # The structural facts exist...
    for c in ("archetype_id", "is_followup", "targeted_star_element"):
        assert c in cols, f"conversation_turn is missing structural column {c!r}"
    # ...and nothing score-like does.
    assert not any(frag in c.lower() for c in cols for frag in _SCORE_LIKE)


async def test_session_records_difficulty_and_rubric_not_a_score(pool):
    cols = {
        r["column_name"]
        for r in await pool.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='voice_session'"
        )
    }
    assert "difficulty" in cols and "rubric_version" in cols
    # No composite/blended score column on the session.
    assert not any(frag in c.lower() for c in cols for frag in _SCORE_LIKE)
