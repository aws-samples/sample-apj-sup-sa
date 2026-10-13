"""Anti-IDOR tests: session-scoped reads/writes must be owner-scoped (code-review CRITICAL #1/#2).

Exercises db.session_owned_by directly against the local DB (the authorization primitive the API
endpoints gate on). Proves: the owner is recognized, a different user is NOT, a missing/garbage
session id is not-owned (no 500). Runs under the voice-worker venv; skipped if no DB.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed(pool, owner: str) -> str:
    sid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider) VALUES ($1,$2,'bedrock_direct')",
        uuid.UUID(sid), owner,
    )
    return sid


async def test_owner_recognized_non_owner_denied(pool):
    from src import db

    sid = await _seed(pool, "owner-alice")
    try:
        assert await db.session_owned_by(sid, "owner-alice") is True
        assert await db.session_owned_by(sid, "intruder-bob") is False
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))


async def test_missing_session_is_not_owned(pool):
    from src import db

    assert await db.session_owned_by(str(uuid.uuid4()), "anyone") is False


async def test_malformed_session_id_is_not_owned_not_error(pool):
    from src import db

    # A non-UUID id must be treated as not-found (False), never raise a 500.
    assert await db.session_owned_by("not-a-uuid", "anyone") is False
