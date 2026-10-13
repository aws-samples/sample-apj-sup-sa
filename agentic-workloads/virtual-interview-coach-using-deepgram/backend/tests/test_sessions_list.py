"""F008 US1: the owner's session list (the report picker's data source).

DB-backed (local pgvector container; skipped without DATABASE_URL — the established idiom).
Proves: newest-first ordering, report_status derivation from report_job, strict owner filtering
(SC-006: user B never sees user A's sessions — the query has no foreign id to probe), and the
omission of abandoned zero-turn/no-job rows.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_session(pool, owner: str, *, title: str | None = None, turns: int = 1,
                        job_status: str | None = None, minutes_ago: int = 0) -> str:
    sid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider, job_title, turn_count, "
        "created_at) VALUES ($1,$2,'bedrock_direct',$3,$4, now() - make_interval(mins => $5))",
        sid, owner, title, turns, minutes_ago,
    )
    if job_status:
        await pool.execute(
            "INSERT INTO report_job (id, session_id, status) VALUES ($1,$2,$3)",
            uuid.uuid4(), sid, job_status,
        )
    return str(sid)


async def test_list_newest_first_with_report_status(pool):
    from src import db

    owner = f"owner-{uuid.uuid4()}"
    old = await _seed_session(pool, owner, title="Old role", job_status="scored", minutes_ago=60)
    new = await _seed_session(pool, owner, title="New role", job_status="processing", minutes_ago=1)
    try:
        rows = await db.list_sessions_for_owner(owner)
        assert [r["session_id"] for r in rows] == [new, old]
        assert rows[0]["report_status"] == "processing"
        assert rows[1]["report_status"] == "scored"
        assert rows[1]["job_title"] == "Old role"
    finally:
        await pool.execute("DELETE FROM voice_session WHERE user_sub=$1", owner)


async def test_no_job_yields_status_none_and_abandoned_rows_omitted(pool):
    from src import db

    owner = f"owner-{uuid.uuid4()}"
    kept = await _seed_session(pool, owner, turns=2, job_status=None)
    # Abandoned setup attempt: zero turns AND no report job -> omitted from the list.
    await _seed_session(pool, owner, turns=0, job_status=None)
    try:
        rows = await db.list_sessions_for_owner(owner)
        assert [r["session_id"] for r in rows] == [kept]
        assert rows[0]["report_status"] == "none"
    finally:
        await pool.execute("DELETE FROM voice_session WHERE user_sub=$1", owner)


async def test_owner_filtering_is_absolute(pool):
    from src import db

    a, b = f"owner-{uuid.uuid4()}", f"owner-{uuid.uuid4()}"
    await _seed_session(pool, a, title="A's session")
    try:
        assert await db.list_sessions_for_owner(b) == []  # B sees nothing of A's
    finally:
        await pool.execute("DELETE FROM voice_session WHERE user_sub=$1", a)


async def test_empty_list_for_new_user(pool):
    from src import db

    assert await db.list_sessions_for_owner(f"owner-{uuid.uuid4()}") == []
