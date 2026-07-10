"""SC-003 session-prep gate evidence (T039) — contracts/session-prep-contract.md.

Proves the selection path is bank-served and LLM-free:
  - zero live LLM on the selection primitives (a boto3 spy fails the test if a bedrock client is built);
  - only `status='approved' AND embedding IS NOT NULL` rows are ever returned (a draft + a retired
    row, both embedded, never appear);
  - results are JD-ranked (a row embedded close to the JD outranks a far one);
  - assembly is ready inside the prep window (< ~2s from the DB), off the gap clock.

Offline by construction: embeddings are the deterministic synthetic vectors from bank.embeddings, so
no Bedrock call is needed (and, on the selection path, none is allowed). Runs against the local
pgvector container only.
"""

from __future__ import annotations

import time
import uuid

import pytest

from conftest import archetype_id  # type: ignore[import-not-found]

pytestmark = pytest.mark.asyncio

DIFFICULTY = "easy"

# A JD whose role classifies as software_engineering (so the domain filter has a target).
JD_TITLE = "Software Engineer"
JD_TEXT = "Backend software engineer: debug production incidents, design APIs, own microservices."

# Seed keys -> deterministic ids, so cleanup is reliable even on assertion failure.
_NEAR = archetype_id("prep-near")          # embedded ON the JD vector -> distance ~0, ranks first
_FAR = archetype_id("prep-far")            # embedded on unrelated text -> ranks after near
_DRAFT = archetype_id("prep-draft")        # approved? no -> must never be served
_RETIRED = archetype_id("prep-retired")    # retired -> excluded
_ALL = [_NEAR, _FAR, _DRAFT, _RETIRED]


async def _seed(pool, jd_vec_literal: str, far_vec_literal: str) -> None:
    async def ins(aid, status, vec_literal, competency="teamwork"):
        await pool.execute(
            """
            INSERT INTO question_archetype
                (id, category, competency, question_type, industry, role_family, seniority,
                 difficulty, prompt_template, follow_up_prompts, scoring_guidance,
                 embedding, embedding_model, source, status, version, active)
            VALUES ($1,'general',$2,'behavioral',NULL,NULL,NULL,
                    $3,'A seeded prep prompt','["probe one"]'::jsonb,
                    '{"strong":"specifics","weak":"vague"}'::jsonb,
                    $4::vector,'synthetic-sha256-1024','generated',$5,1,($5<>'retired'))
            ON CONFLICT (id) DO UPDATE SET
                status=EXCLUDED.status, embedding=EXCLUDED.embedding, active=EXCLUDED.active,
                competency=EXCLUDED.competency
            """,
            aid, competency, DIFFICULTY, vec_literal, status,
        )

    # NEAR + FAR are both approved; the draft and retired are decoys that must be filtered out.
    await ins(_NEAR, "approved", jd_vec_literal, competency="problem_solving")
    await ins(_FAR, "approved", far_vec_literal, competency="communication")
    await ins(_DRAFT, "draft", jd_vec_literal)
    await ins(_RETIRED, "retired", jd_vec_literal)


async def _cleanup(pool) -> None:
    await pool.execute("DELETE FROM question_archetype WHERE id = ANY($1::uuid[])", _ALL)


@pytest.fixture
async def seeded(pool):
    from bank.embeddings import synthetic_embedding, to_pgvector

    jd_vec = synthetic_embedding(f"{JD_TITLE}\n\n{JD_TEXT}".strip())
    far_vec = synthetic_embedding("completely unrelated topic about gardening and houseplants")
    await _seed(pool, to_pgvector(jd_vec), to_pgvector(far_vec))
    yield pool
    await _cleanup(pool)


async def test_selection_is_llm_free(seeded, no_inference):
    """SC-003: the selection primitives touch no inference client."""
    from bank.embeddings import synthetic_embedding
    from src.prep import retrieval

    jd = synthetic_embedding(f"{JD_TITLE}\n\n{JD_TEXT}".strip())
    rows = await retrieval.retrieve_ranked(jd, DIFFICULTY, include_domain=True, limit=50)
    assert rows, "expected approved rows from the seeded bank"
    # The spy raises inside retrieve_ranked if a bedrock client is constructed; reaching here is the pass.
    assert all("bedrock" not in c for c in no_inference)


async def test_only_approved_selected(seeded):
    """Draft + retired rows are never served; only approved+embedded appear (FR-206 / AC-2d)."""
    from bank.embeddings import synthetic_embedding
    from src.prep import retrieval

    jd = synthetic_embedding(f"{JD_TITLE}\n\n{JD_TEXT}".strip())
    ids = {r["id"] for r in await retrieval.retrieve_ranked(jd, DIFFICULTY, include_domain=True, limit=50)}
    assert str(_NEAR) in ids
    assert str(_FAR) in ids
    assert str(_DRAFT) not in ids, "a draft archetype must never be selected"
    assert str(_RETIRED) not in ids, "a retired archetype must be excluded from all plans"


async def test_jd_ranked_order(seeded):
    """The row embedded ON the JD ranks above the unrelated row (pgvector cosine order)."""
    from bank.embeddings import synthetic_embedding
    from src.prep import retrieval

    jd = synthetic_embedding(f"{JD_TITLE}\n\n{JD_TEXT}".strip())
    rows = await retrieval.retrieve_ranked(jd, DIFFICULTY, include_domain=True, limit=50)
    order = [r["id"] for r in rows]
    assert order.index(str(_NEAR)) < order.index(str(_FAR)), "JD-near archetype must outrank JD-far"
    near = next(r for r in rows if r["id"] == str(_NEAR))
    far = next(r for r in rows if r["id"] == str(_FAR))
    assert near["distance"] < far["distance"]
    # Probes + guidance travel with the selected row (FR-205).
    assert near["follow_up_prompts"] == ["probe one"]
    assert near["scoring_guidance"] == {"strong": "specifics", "weak": "vague"}


async def test_blueprint_ready_in_prep_window(seeded, monkeypatch, no_inference):
    """End-to-end assemble_blueprint completes in the prep window (< ~2s) with zero selection LLM.

    A real voice_session row is created for the FK. Step 1 (the one-time JD embedding) is allowed to
    use Bedrock per the contract but is NOT the selection step; here we patch it to a precomputed
    synthetic vector (simulating the completed prep-window embedding) so the no_inference spy can
    strictly guard steps 2-4 (retrieval + persistence) — the part that must be LLM-free.
    """
    from bank.embeddings import synthetic_embedding
    from src.prep import blueprint

    monkeypatch.setattr(
        blueprint, "_embed_jd",
        lambda title, desc: synthetic_embedding(f"{title}\n\n{desc}".strip()),
    )

    session_id = str(uuid.uuid4())
    pool = seeded
    await pool.execute(
        """
        INSERT INTO voice_session (session_id, user_sub, created_at, reply_provider, difficulty)
        VALUES ($1,'test-sub',now(),'bedrock_direct',$2)
        """,
        uuid.UUID(session_id), DIFFICULTY,
    )
    try:
        start = time.monotonic()
        result = await blueprint.assemble_blueprint(session_id, JD_TITLE, JD_TEXT, DIFFICULTY)
        prep_ms = (time.monotonic() - start) * 1000.0

        assert prep_ms < 2000.0, f"prep took {prep_ms:.0f}ms; SC-003 wants < ~2s"
        assert result["ordered_archetype_ids"], "blueprint must carry a non-empty plan"
        assert str(_DRAFT) not in result["ordered_archetype_ids"]
        assert str(_RETIRED) not in result["ordered_archetype_ids"]
        assert str(_NEAR) in result["ordered_archetype_ids"]
        assert 1 <= len(result["target_competencies"]) <= 6
        assert result["opening_archetype_id"] in result["ordered_archetype_ids"]
        # blueprint persisted + linked onto the session
        bp = await pool.fetchval(
            "SELECT blueprint_id FROM voice_session WHERE session_id = $1", uuid.UUID(session_id)
        )
        assert bp is not None
    finally:
        # voice_session cascade removes the blueprint; archetypes cleaned by the `seeded` fixture.
        await pool.execute("DELETE FROM voice_session WHERE session_id = $1", uuid.UUID(session_id))
