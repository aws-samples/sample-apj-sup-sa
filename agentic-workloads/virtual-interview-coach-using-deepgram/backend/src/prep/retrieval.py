"""Bank retrieval (T015) — the pure-DB selection primitive (contracts/session-prep-contract.md).

Given a job-description embedding, return approved archetypes filtered by difficulty + role and
ranked by pgvector cosine distance (IVFFlat). This is the part of the selection path that MUST
stay LLM-free (SC-003 / FR-207): the only inputs are the precomputed JD embedding and SQL filters;
no inference client is touched here. The JD is embedded ONCE upstream (blueprint.py, prep window) —
that embedding call is not part of "selection" and never recurs per turn.

The niche-role fallback decision (FR-222) lives in blueprint.py; this module just exposes the
primitives it composes: `retrieve_ranked` (the filtered+ranked query) and `count_domain_matches`
(so the blueprint can detect a zero-domain JD and flag domain_coverage_reduced honestly).
"""

from __future__ import annotations

from .. import db


def _to_pgvector(vec: list[float]) -> str:
    """Render a float list as a pgvector literal for a `$n::vector` cast.

    asyncpg has no native pgvector codec, so the embedding crosses the wire as text and is cast
    in SQL. (Mirrors bank/embeddings.to_pgvector; inlined to avoid a cross-subtree import in the
    deployed backend image.)
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# The columns the live queue + later G3 scoring need loaded with the plan (FR-205). No score
# columns are read/applied here (F002). `distance` is the cosine distance to the JD (smaller = closer).
_SELECT_COLS = """
    id, category, competency, question_type, industry, role_family, seniority,
    difficulty, prompt_template, follow_up_prompts, scoring_guidance,
    (embedding <=> $1::vector) AS distance
"""


async def retrieve_ranked(
    jd_embedding: list[float],
    difficulty: str,
    *,
    role_family: str | None = None,
    industry: str | None = None,
    include_domain: bool = True,
    limit: int = 12,
) -> list[dict]:
    """Approved archetypes for this difficulty, JD-ranked by cosine distance. Zero LLM.

    Always includes General-competency archetypes. Domain archetypes are included only when
    `include_domain` is True AND they match the JD's role_family/industry. Ordering is the pgvector
    `embedding <=> jd` cosine distance (IVFFlat), closest first.
    """
    pool = await db.get_pool()
    jd = _to_pgvector(jd_embedding)
    rows = await pool.fetch(
        f"""
        SELECT {_SELECT_COLS}
          FROM question_archetype
         WHERE status = 'approved'
           AND embedding IS NOT NULL
           AND active = TRUE
           AND difficulty = $2
           AND (
                category = 'general'
                OR ($3::boolean = TRUE
                    AND category = 'domain'
                    AND ($4::text IS NOT NULL AND role_family = $4::text
                         OR $5::text IS NOT NULL AND industry = $5::text))
               )
         ORDER BY embedding <=> $1::vector
         LIMIT $6
        """,
        jd,
        difficulty,
        include_domain,
        role_family,
        industry,
        limit,
    )
    return [_row_to_dict(r) for r in rows]


async def count_domain_matches(
    difficulty: str,
    *,
    role_family: str | None = None,
    industry: str | None = None,
) -> int:
    """Number of approved, embedded DOMAIN archetypes matching the JD's role at this difficulty.

    Zero means the JD's role is not covered by the bank => blueprint.py drops to the General-only
    plan and sets voice_session.domain_coverage_reduced (FR-222). Pure DB, no LLM.
    """
    pool = await db.get_pool()
    return int(
        await pool.fetchval(
            """
            SELECT count(*)
              FROM question_archetype
             WHERE status = 'approved'
               AND embedding IS NOT NULL
               AND active = TRUE
               AND difficulty = $1
               AND category = 'domain'
               AND ($2::text IS NOT NULL AND role_family = $2::text
                    OR $3::text IS NOT NULL AND industry = $3::text)
            """,
            difficulty,
            role_family,
            industry,
        )
        or 0
    )


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    # follow_up_prompts / scoring_guidance come back as JSON text from asyncpg; decode lazily so
    # the queue carries native structures (the live loop reads follow_up_prompts; G3 reads guidance).
    import json

    for k in ("follow_up_prompts", "scoring_guidance"):
        v = d.get(k)
        if isinstance(v, str):
            d[k] = json.loads(v)
    if d.get("distance") is not None:
        d["distance"] = float(d["distance"])
    return d
