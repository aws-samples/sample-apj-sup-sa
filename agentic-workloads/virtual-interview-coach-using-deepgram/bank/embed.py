"""Embed-on-approve for the question bank (T037) — research.md R3 / bank-generation-contract.md.

The final offline stage: take APPROVED archetypes that are not yet embedded with the current model
and write their `embedding` + `embedding_model`, then rebuild the IVFFlat index so `lists` reflects
the real approved+embedded cardinality. Only after this does an approved row become selectable by
the session-prep query (status='approved' AND embedding IS NOT NULL).

Embed-on-approve, never embed a draft: drafts are not vetted, so embedding them would waste spend
and risk a half-vetted row leaking into selection if the filter ever regressed. We embed exactly the
approved population.

Model-pin (R3): each row records the `embedding_model` that produced its vector. A row whose pinned
model differs from the model we are embedding with is RE-EMBEDDED — a model change is never silent.
This also re-embeds the synthetic placeholders the T010 fixture loader may have written
(embedding_model='synthetic-sha256-1024') once a real Titan run is performed.

Entirely OFF the response_gap clock — an operator step run after bank/review.py, before any session.

Run:
  python -m bank.embed                       # Titan-embed approved rows needing it; rebuild index
  python -m bank.embed --embed synthetic     # local pseudo-vectors (no Bedrock) for plumbing tests
  python -m bank.embed --reembed-all         # re-embed every approved row regardless of pin
  python -m bank.embed --dry-run             # report what would be embedded; no Bedrock/writes
"""

from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

from .embeddings import (
    SYNTHETIC_MODEL_ID,
    embed_text,
    synthetic_embedding,
    to_pgvector,
)

load_dotenv()


async def _rows_needing_embedding(
    conn: asyncpg.Connection, target_model: str, reembed_all: bool
) -> list[asyncpg.Record]:
    """Approved rows to embed: those with no embedding, or whose pinned model != target (R3).

    With --reembed-all, every approved row is returned regardless of its current pin.
    """
    if reembed_all:
        return await conn.fetch(
            "SELECT id, prompt_template FROM question_archetype "
            "WHERE status = 'approved' ORDER BY id"
        )
    return await conn.fetch(
        """
        SELECT id, prompt_template
        FROM question_archetype
        WHERE status = 'approved'
          AND (embedding IS NULL OR embedding_model IS DISTINCT FROM $1)
        ORDER BY id
        """,
        target_model,
    )


async def _write_embedding(
    conn: asyncpg.Connection, archetype_id, vector_literal: str, embedding_model: str
) -> None:
    """Set embedding + model pin on one approved row. The vector crosses the wire as a pgvector
    text literal cast in SQL (asyncpg has no native vector codec). The WHERE guard keeps us from
    ever embedding a non-approved row even if the selection above changes."""
    await conn.execute(
        """
        UPDATE question_archetype
        SET embedding = $2::vector, embedding_model = $3
        WHERE id = $1 AND status = 'approved'
        """,
        archetype_id,
        vector_literal,
        embedding_model,
    )


async def _rebuild_ivfflat(conn: asyncpg.Connection) -> tuple[int, int]:
    """Rebuild the partial IVFFlat index so `lists` ~ sqrt(approved+embedded rows) (R3).

    The partial predicate matches the selection filter exactly, keeping the index aligned with the
    rows the prep query can actually return.
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


async def embed(
    database_url: str,
    embed_mode: str,
    model_id: str,
    region: str,
    reembed_all: bool,
    dry_run: bool,
) -> None:
    embedding_model = model_id if embed_mode == "titan" else SYNTHETIC_MODEL_ID
    conn = await asyncpg.connect(database_url)
    loop = asyncio.get_running_loop()
    try:
        rows = await _rows_needing_embedding(conn, embedding_model, reembed_all)
        if dry_run:
            total = await conn.fetchval(
                "SELECT count(*) FROM question_archetype WHERE status = 'approved'"
            )
            print(
                f"dry-run: {len(rows)} of {total} approved archetype(s) would be embedded "
                f"with model={embedding_model!r} (mode={embed_mode}"
                f"{', reembed-all' if reembed_all else ''}). No Bedrock calls, no writes."
            )
            return
        if not rows:
            count = await conn.fetchval(
                "SELECT count(*) FROM question_archetype "
                "WHERE status = 'approved' AND embedding IS NOT NULL"
            )
            print(f"Nothing to embed: all approved rows already carry model={embedding_model!r} "
                  f"({count} approved+embedded).")
            return
        embedded = 0
        for row in rows:
            text = row["prompt_template"]
            if embed_mode == "titan":
                vec = await loop.run_in_executor(None, embed_text, text, model_id, region)
            else:
                vec = synthetic_embedding(text)
            await _write_embedding(conn, row["id"], to_pgvector(vec), embedding_model)
            embedded += 1
            print(f"  + embedded {row['id']}: {text[:70]}")
        count, lists = await _rebuild_ivfflat(conn)
        print(
            f"\nEmbedded {embedded} approved archetype(s) with model={embedding_model!r}; "
            f"IVFFlat rebuilt over {count} approved+embedded rows (lists={lists}). "
            f"These rows are now selectable by session-prep."
        )
    finally:
        await conn.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Embed approved question-bank archetypes + rebuild IVFFlat (T037).")
    p.add_argument("--embed", choices=("titan", "synthetic"), default="titan",
                   help="titan = real Bedrock Titan (needs creds); synthetic = local pseudo-vectors.")
    p.add_argument("--reembed-all", action="store_true",
                   help="re-embed every approved row regardless of its current model pin")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be embedded without calling Bedrock or writing")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set; cannot embed the bank.")
    model_id = os.environ.get("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
    region = os.environ.get("AWS_REGION", "us-east-1")
    await embed(database_url, args.embed, model_id, region, args.reembed_all, args.dry_run)


if __name__ == "__main__":
    asyncio.run(_main())
