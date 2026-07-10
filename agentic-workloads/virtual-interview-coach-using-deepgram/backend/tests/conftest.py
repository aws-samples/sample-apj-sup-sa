"""Pytest path + fixture setup for the backend gate-evidence tests (T039/T040).

The backend has no venv of its own; these tests run under the voice-worker venv against the local
pgvector container (DATABASE_URL pointed at it). They never touch deployed RDS. Two import roots are
needed: `backend/` for the `src.*` package and the repo root for `bank.*` (the synthetic embedder is
reused so the tests stay offline / Bedrock-free).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]      # backend/
_REPO = _BACKEND.parent                              # repo root
for p in (str(_BACKEND), str(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)


# Stable namespace so seeded archetypes get deterministic ids and a failed run cleans up reliably.
_TEST_NS = uuid.UUID("6f4c0d2e-0000-4002-a010-0000000390ff")


def archetype_id(key: str) -> uuid.UUID:
    return uuid.uuid5(_TEST_NS, key)


@pytest.fixture
def db_required():
    """Skip the test if no local DATABASE_URL is configured (so CI without a DB is not a hard fail)."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set; the prep gate tests need the local pgvector container")


@pytest.fixture
async def pool(db_required):
    from src import db

    pool = await db.get_pool()
    yield pool
    await db.close_pool()


@pytest.fixture
def no_inference(monkeypatch):
    """Fail the test if any Bedrock/inference client is constructed on the selection path (SC-003).

    The selection primitives (retrieval.retrieve_ranked, count_domain_matches) must touch no model.
    This spy patches boto3.client so any 'bedrock*' service construction raises — turning an
    accidental live LLM call on the selection path into a hard test failure.
    """
    import boto3

    calls: list[str] = []
    real_client = boto3.client

    def spy_client(service_name, *args, **kwargs):
        calls.append(service_name)
        if "bedrock" in service_name:
            raise AssertionError(
                f"selection path constructed an inference client ({service_name!r}) — "
                "SC-003 requires zero live LLM on selection"
            )
        return real_client(service_name, *args, **kwargs)

    monkeypatch.setattr(boto3, "client", spy_client)
    return calls
