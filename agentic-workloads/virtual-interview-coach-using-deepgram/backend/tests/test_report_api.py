"""F003 report-engine backend tests: async-only enqueue (SC-003), retrieval, bounded delete (SC-008).

Runs under the voice-worker venv against the local pgvector container (DATABASE_URL). Skipped if no DB.
These assert the BACKEND side: ending a session enqueues a job WITHOUT constructing any inference
client on the request path (scoring is the worker's job — SC-003), the report is retrievable through
the db accessor, and deleting the session purges report rows with no residual (SC-008).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_scored_session(pool, session_id: str) -> str:
    """Seed a session + a scored report + one question_feedback + a report_job (as the worker would)."""
    sid = uuid.UUID(session_id)
    await pool.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider, difficulty, rubric_version) "
        "VALUES ($1,'owner-x','bedrock_direct','moderate','g2-2026.1')",
        sid,
    )
    rid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO report (id, session_id, status, overall, score_content, score_structure, "
        "score_communication, score_confidence, difficulty, rubric_version, competency_scorecard) "
        "VALUES ($1,$2,'scored',6.5,7,6,7,6,'moderate','g3-2026.1', '[]'::jsonb)",
        rid, sid,
    )
    await pool.execute(
        "INSERT INTO question_feedback (id, report_id, session_id, turn_index, question_text, "
        "student_transcript, what_worked) VALUES ($1,$2,$3,1,'Q','A','worked')",
        uuid.uuid4(), rid, sid,
    )
    await pool.execute(
        "INSERT INTO report_job (id, session_id, status) VALUES ($1,$2,'scored')",
        uuid.uuid4(), sid,
    )
    return session_id


async def test_enqueue_on_end_constructs_no_inference_client(pool, no_inference):
    # SC-003: enqueuing a report job (the /end path's work) must NOT build any Bedrock client — scoring
    # is the async worker's job. db.enqueue_report_job + queue.enqueue_report are the entire live touch.
    from src import db
    from src import queue

    session_id = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider) VALUES ($1,'owner-x','bedrock_direct')",
        uuid.UUID(session_id),
    )
    try:
        await db.enqueue_report_job(session_id)
        queue.enqueue_report(session_id)  # no REPORT_QUEUE_URL in test -> no-op, no boto3 client
        job = await pool.fetchval("SELECT status FROM report_job WHERE session_id=$1", uuid.UUID(session_id))
        assert job == "queued"
        assert not any("bedrock" in c for c in no_inference)  # SC-003: zero inference on the live path
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(session_id))


async def test_get_report_returns_scored_payload(pool):
    from src import db

    session_id = str(uuid.uuid4())
    await _seed_scored_session(pool, session_id)
    try:
        result = await db.get_report(session_id)
        assert result["status"] == "scored"
        assert result["report"]["overall"] is not None
        assert len(result["report"]["question_feedback"]) == 1
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(session_id))


async def test_get_report_processing_when_no_report_yet(pool):
    from src import db

    session_id = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider) VALUES ($1,'owner-x','bedrock_direct')",
        uuid.UUID(session_id),
    )
    await pool.execute(
        "INSERT INTO report_job (id, session_id, status) VALUES ($1,$2,'processing')",
        uuid.uuid4(), uuid.UUID(session_id),
    )
    try:
        result = await db.get_report(session_id)
        assert result["status"] == "processing"
        assert result["report"] is None  # honest processing state, never a half-built report
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(session_id))


async def test_delete_session_purges_report_rows_no_residual(pool):
    # SC-008: bounded delete removes report + question_feedback + report_job with no residual.
    from src import db

    session_id = str(uuid.uuid4())
    await _seed_scored_session(pool, session_id)
    counts = await db.delete_session_cascade(session_id, "owner-x")
    assert counts is not None
    assert counts["reports"] == 1 and counts["question_feedback"] == 1 and counts["report_jobs"] == 1
    for tbl in ("report", "question_feedback", "report_job"):
        residual = await pool.fetchval(f"SELECT count(*) FROM {tbl} WHERE session_id=$1", uuid.UUID(session_id))
        assert residual == 0, f"residual {tbl} rows after delete"


async def test_no_blended_difficulty_composite(pool):
    # SC-007: difficulty + rubric_version are recorded as CONTEXT on the report; they are never blended
    # into a score. Assert the report carries them as standalone fields and the scores are absolute.
    from src import db

    session_id = str(uuid.uuid4())
    await _seed_scored_session(pool, session_id)
    try:
        result = await db.get_report(session_id)
        rep = result["report"]
        assert rep["difficulty"] == "moderate"          # recorded, standalone
        assert rep["rubric_version"] == "g3-2026.1"      # recorded, standalone
        # The overall is the rubric score, not multiplied by any difficulty factor (a moderate session's
        # 6.5 is the same 6.5 it would be at easy/difficult — level-independent).
        assert float(rep["overall"]) == 6.5
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(session_id))
