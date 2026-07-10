"""Approval-semantics gate evidence (T040) — US4 scenarios 1 & 3 / FR-206.

End-to-end through the real components: seed an archetype, drive it with bank/review.py's transition,
then ask the prep selection query (src.prep.retrieval) what it would serve. This proves the operator
gate and the live selection agree on what "vetted" means:

  Scenario 1 — a `draft` is never served. An embedded draft (and a matching approved row) are seeded;
               only the approved row is selectable.
  Scenario 3 — a retired archetype is excluded from all future plans. An approved+embedded row is
               retired via the gate; it then never appears in any subsequent selection.

Also asserts the gate refuses illegal transitions (no approved->draft, no resurrecting a retired
row), so a rejected question cannot quietly come back.

Offline (synthetic embeddings); local pgvector container only; never touches deployed RDS.
"""

from __future__ import annotations

import pytest

from conftest import archetype_id  # type: ignore[import-not-found]

pytestmark = pytest.mark.asyncio

DIFFICULTY = "moderate"
JD_TEXT = "Backend software engineer who debugs incidents and designs APIs."

_DRAFT = archetype_id("review-draft")        # never served while draft
_APPROVED = archetype_id("review-approved")  # the vetted control that should be served
_RETIRE = archetype_id("review-retire")      # approved, then retired -> excluded forever
_ALL = [_DRAFT, _APPROVED, _RETIRE]


async def _ins(pool, aid, status, vec_literal, competency="teamwork"):
    await pool.execute(
        """
        INSERT INTO question_archetype
            (id, category, competency, question_type, industry, role_family, seniority,
             difficulty, prompt_template, follow_up_prompts, scoring_guidance,
             embedding, embedding_model, source, status, version, active)
        VALUES ($1,'general',$2,'behavioral',NULL,NULL,NULL,
                $3,'A seeded review prompt','[]'::jsonb,'{}'::jsonb,
                $4::vector,'synthetic-sha256-1024','generated',$5,1,($5<>'retired'))
        ON CONFLICT (id) DO UPDATE SET
            status=EXCLUDED.status, embedding=EXCLUDED.embedding, active=EXCLUDED.active,
            competency=EXCLUDED.competency
        """,
        aid, competency, DIFFICULTY, vec_literal, status,
    )


@pytest.fixture
async def seeded(pool):
    from bank.embeddings import synthetic_embedding, to_pgvector

    vec = to_pgvector(synthetic_embedding(JD_TEXT))
    # Draft + approved + (to-be-retired) approved, all embedded on the JD so only status decides.
    await _ins(pool, _DRAFT, "draft", vec, competency="problem_solving")
    await _ins(pool, _APPROVED, "approved", vec, competency="communication")
    await _ins(pool, _RETIRE, "approved", vec, competency="leadership")
    yield pool
    await pool.execute("DELETE FROM question_archetype WHERE id = ANY($1::uuid[])", _ALL)


async def _selectable_ids(pool) -> set[str]:
    from bank.embeddings import synthetic_embedding
    from src.prep import retrieval

    jd = synthetic_embedding(JD_TEXT)
    rows = await retrieval.retrieve_ranked(jd, DIFFICULTY, include_domain=True, limit=100)
    return {r["id"] for r in rows}


async def test_draft_is_never_served(seeded):
    """US4-1: a draft archetype never appears in a plan even when embedded and JD-matching."""
    ids = await _selectable_ids(seeded)
    assert str(_APPROVED) in ids, "the approved control should be selectable"
    assert str(_DRAFT) not in ids, "a draft must never be served"


async def test_approve_makes_selectable(seeded):
    """Approving a draft (gate transition) makes it selectable — the positive control for the gate."""
    from bank import review

    async with seeded.acquire() as conn:
        line = await review._transition(conn, _DRAFT, "approved")
    assert "draft -> approved" in line
    ids = await _selectable_ids(seeded)
    assert str(_DRAFT) in ids, "an approved (embedded) archetype must become selectable"


async def test_retired_is_excluded_forever(seeded):
    """US4-3: retiring an approved archetype removes it from all subsequent plans."""
    from bank import review

    before = await _selectable_ids(seeded)
    assert str(_RETIRE) in before, "row should be selectable while approved"

    async with seeded.acquire() as conn:
        line = await review._transition(conn, _RETIRE, "retired")
    assert "approved -> retired" in line

    after = await _selectable_ids(seeded)
    assert str(_RETIRE) not in after, "a retired archetype must be excluded from future plans"
    # and it cannot be resurrected by the gate
    async with seeded.acquire() as conn:
        refused = await review._transition(conn, _RETIRE, "approved")
    assert "refused" in refused, "retired -> approved must be refused (no resurrection)"
    assert str(_RETIRE) not in await _selectable_ids(seeded)


async def test_illegal_transitions_refused(seeded):
    """The gate refuses approved->draft so a vetted/un-vetted boundary cannot be crossed backwards."""
    from bank import review

    async with seeded.acquire() as conn:
        refused = await review._transition(conn, _APPROVED, "draft")
    assert "refused" in refused
    # the approved control is still served, unchanged
    assert str(_APPROVED) in await _selectable_ids(seeded)
