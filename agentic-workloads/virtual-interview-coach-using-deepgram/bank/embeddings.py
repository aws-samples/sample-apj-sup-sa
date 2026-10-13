"""Embedding helper for the offline bank (shared by T010 loader, T037 bank/embed.py).

OFF the response_gap clock by construction: every call here runs in the offline bank pipeline
or the session-prep window, never on the live turn loop (Latency invariant). Titan Text
Embeddings v2 produces 1024-dim vectors (matches `question_archetype.embedding vector(1024)`).

Two modes:
  - `embed_text(...)` — the real Bedrock Titan call (production / when AWS creds are present).
  - `synthetic_embedding(...)` — a DETERMINISTIC, normalized 1024-dim vector derived from the
    text hash, for local schema/retrieval testing WITHOUT a Bedrock dependency. It is NOT
    semantic; it only lets the pgvector plumbing be exercised offline. Synthetic rows are pinned
    with a distinct `embedding_model` so a later real embed (T037) re-embeds them (model mismatch
    is never silent — R3).
"""

from __future__ import annotations

import hashlib
import json
import math
import struct

# Titan Text Embeddings v2 native dimension (pinned to the vector(1024) column).
EMBED_DIM = 1024

# Sentinel model id stamped on synthetic vectors so they are distinguishable from real Titan
# embeddings and get re-embedded when the real pipeline runs (R3 embedding_model pin).
SYNTHETIC_MODEL_ID = "synthetic-sha256-1024"


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def synthetic_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic normalized pseudo-vector from the text (local testing only; not semantic)."""
    out: list[float] = []
    counter = 0
    # Expand SHA-256 digests into `dim` floats in [-1, 1); deterministic for a given text.
    while len(out) < dim:
        digest = hashlib.sha256(f"{text}:{counter}".encode("utf-8")).digest()
        # 8 floats per 32-byte digest (4 bytes each -> uint32 -> [-1, 1)).
        for i in range(0, len(digest), 4):
            (val,) = struct.unpack(">I", digest[i : i + 4])
            out.append((val / 0xFFFFFFFF) * 2.0 - 1.0)
            if len(out) >= dim:
                break
        counter += 1
    return _normalize(out)


def embed_text(text: str, model_id: str, region: str) -> list[float]:
    """Embed `text` with Bedrock Titan; returns a normalized 1024-dim vector.

    Lazy boto3 import keeps the dependency out of synthetic/offline test paths. Titan v2 is
    asked to normalize at the source so cosine and inner-product agree (R3).
    """
    import boto3  # lazy

    client = boto3.client("bedrock-runtime", region_name=region)
    resp = client.invoke_model(
        modelId=model_id,
        body=json.dumps({"inputText": text, "normalize": True}),
        accept="application/json",
        contentType="application/json",
    )
    payload = json.loads(resp["body"].read())
    vec = payload["embedding"]
    if len(vec) != EMBED_DIM:
        raise ValueError(
            f"Titan returned {len(vec)}-dim embedding; expected {EMBED_DIM} "
            f"(model {model_id!r}). Check BEDROCK_EMBEDDING_MODEL_ID and the vector column type."
        )
    return vec


def to_pgvector(vec: list[float]) -> str:
    """Render a float list as a pgvector literal, e.g. '[0.1,0.2,...]', for `$n::vector` casts.

    asyncpg has no native pgvector codec, so vectors cross the wire as text and are cast in SQL.
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
