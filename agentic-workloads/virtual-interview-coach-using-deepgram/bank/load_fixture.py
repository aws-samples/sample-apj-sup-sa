"""Load the minimal approved + embedded archetype fixture (T010).

A bootstrap so US1-US3 (grounding, prep, difficulty) can run against a real, approved,
JD-rankable bank BEFORE the full US4 generate -> review -> embed pipeline exists. It loads
`bank/seed/minimal.json` (1 General + 2 Domain families), inserts each row as an APPROVED,
curated archetype, sets its embedding, then rebuilds the IVFFlat index so the prep selection
query (status='approved' AND embedding IS NOT NULL, ORDER BY embedding <=> jd) works at once.

Entirely OFF the response_gap clock — this is an operator step run before any session.

Idempotent: each fixture row's stable `key` maps to a deterministic uuid5, so re-running
upserts in place rather than duplicating.

Embedding modes (R3):
  --embed titan       real Bedrock Titan v2 (needs AWS creds + Bedrock access) [default]
  --embed synthetic   deterministic local pseudo-vectors (no Bedrock) — for schema/retrieval
                      testing only; pinned with a sentinel embedding_model so a later real
                      embed (T037) re-embeds them.

Run:
  python -m bank.load_fixture                 # titan embeddings, DATABASE_URL from env
  python -m bank.load_fixture --embed synthetic
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from .embeddings import (
    SYNTHETIC_MODEL_ID,
    embed_text,
    synthetic_embedding,
    to_pgvector,
)

load_dotenv()

# Stable namespace so a fixture `key` always maps to the same archetype id (idempotent reloads).
_FIXTURE_NS = uuid.UUID("6f4c0d2e-0000-4002-a010-000000000010")

_SEED_PATH = Path(__file__).parent / "seed" / "minimal.json"


def _archetype_id(key: str) -> str:
    return str(uuid.uuid5(_FIXTURE_NS, key))


def _load_seed() -> list[dict]:
    data = json.loads(_SEED_PATH.read_text())
    rows = data["archetypes"]
    if not rows:
        raise SystemExit("minimal.json has no archetypes")
    return rows


async def _upsert_row(
    conn: asyncpg.Connection, row: dict, vector_literal: str, embedding_model: str
) -> None:
    """Insert/refresh one approved, embedded archetype. Embedding crosses the wire as a
    pgvector text literal and is cast in SQL (asyncpg has no native vector codec)."""
    await conn.execute(
        """
        INSERT INTO question_archetype
            (id, category, competency, question_type, industry, role_family, seniority,
             difficulty, prompt_template, follow_up_prompts, scoring_guidance,
             embedding, embedding_model, source, status, version, active)
        VALUES
            ($1, $2, $3, $4, $5, $6, $7,
             $8, $9, $10::jsonb, $11::jsonb,
             $12::vector, $13, $14, 'approved', 1, TRUE)
        ON CONFLICT (id) DO UPDATE SET
            category         = EXCLUDED.category,
            competency       = EXCLUDED.competency,
            question_type    = EXCLUDED.question_type,
            industry         = EXCLUDED.industry,
            role_family      = EXCLUDED.role_family,
            seniority        = EXCLUDED.seniority,
            difficulty       = EXCLUDED.difficulty,
            prompt_template  = EXCLUDED.prompt_template,
            follow_up_prompts = EXCLUDED.follow_up_prompts,
            scoring_guidance = EXCLUDED.scoring_guidance,
            embedding        = EXCLUDED.embedding,
            embedding_model  = EXCLUDED.embedding_model,
            source           = EXCLUDED.source,
            status           = 'approved',
            active           = TRUE
        """,
        _archetype_id(row["key"]),
        row["category"],
        row["competency"],
        row["question_type"],
        row.get("industry"),
        row.get("role_family"),
        row.get("seniority"),
        row["difficulty"],
        row["prompt_template"],
        json.dumps(row.get("follow_up_prompts", [])),
        json.dumps(row.get("scoring_guidance", {})),
        vector_literal,
        embedding_model,
        row.get("source", "curated"),
    )


async def _rebuild_ivfflat(conn: asyncpg.Connection) -> None:
    """Rebuild the partial IVFFlat index so `lists` reflects the loaded cardinality (R3).

    At fixture scale (a dozen rows) `lists` is tiny; we floor at 1 and cap near sqrt(rows).
    Dropping and recreating keeps the index aligned with the approved+embedded population.
    """
    count = await conn.fetchval(
        "SELECT count(*) FROM question_archetype "
        "WHERE status = 'approved' AND embedding IS NOT NULL"
    )
    lists = max(1, int(count ** 0.5))
    await conn.execute("DROP INDEX IF EXISTS question_archetype_embedding_ivf")
    await conn.execute(
        f"""
        CREATE INDEX question_archetype_embedding_ivf
            ON question_archetype USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = {lists})
            WHERE status = 'approved' AND embedding IS NOT NULL
        """
    )
    return count, lists


async def load(database_url: str, embed_mode: str, model_id: str, region: str) -> None:
    rows = _load_seed()
    conn = await asyncpg.connect(database_url)
    try:
        embedding_model = model_id if embed_mode == "titan" else SYNTHETIC_MODEL_ID
        for row in rows:
            text = row["prompt_template"]
            if embed_mode == "titan":
                vec = embed_text(text, model_id=model_id, region=region)
            else:
                vec = synthetic_embedding(text)
            await _upsert_row(conn, row, to_pgvector(vec), embedding_model)
        count, lists = await _rebuild_ivfflat(conn)
        general = sum(1 for r in rows if r["category"] == "general")
        domains = sorted({r["role_family"] for r in rows if r["category"] == "domain"})
        print(
            f"Loaded {len(rows)} approved archetypes "
            f"({general} general + {len(domains)} domain families: {', '.join(domains)}); "
            f"embed_mode={embed_mode} model={embedding_model}; "
            f"IVFFlat rebuilt over {count} approved+embedded rows (lists={lists})."
        )
    finally:
        await conn.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Load the minimal approved+embedded archetype fixture (T010).")
    p.add_argument(
        "--embed",
        choices=("titan", "synthetic"),
        default="titan",
        help="titan = real Bedrock Titan (needs creds); synthetic = local pseudo-vectors.",
    )
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set; cannot load the fixture.")
    model_id = os.environ.get("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
    region = os.environ.get("AWS_REGION", "us-east-1")
    await load(database_url, args.embed, model_id, region)


if __name__ == "__main__":
    asyncio.run(_main())
