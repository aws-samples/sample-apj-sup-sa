"""End-to-end persistence tests against a local Postgres (report-job lifecycle, idempotency, delete).

Requires a local pgvector/postgres with the F003 migration applied, reachable via DATABASE_URL
(defaults to the local test container). Skipped if no DB is reachable.

Validates: a session scores into report + question_feedback rows; re-scoring is idempotent (one report
per session, no duplicate feedback); deleting the session purges report + feedback + job with no
residual (SC-008); the report_job claim is guarded (second claim skips).
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

from src.config import Config
from src import persistence
from src.consume import process_session

pytestmark = pytest.mark.asyncio

_DEFAULT_DB = "postgres://postgres:test@localhost:55432/interviewcoach"


def _config() -> Config:
    os.environ.setdefault("DATABASE_URL", _DEFAULT_DB)
    os.environ["SCORING_SAMPLES"] = "1"
    os.environ.setdefault("AWS_REGION", "us-west-2")
    return Config.load()


async def _db_or_skip(cfg: Config) -> asyncpg.Connection:
    try:
        return await asyncpg.connect(cfg.database_url, timeout=3)
    except Exception:  # noqa: BLE001
        pytest.skip("no local Postgres reachable for persistence test")


def _stub_score(transcript, competencies, config):
    return {
        "content": 7, "structure": 6, "communication": 7, "confidence": 7, "overall": 6.75,
        "strengths": ["specific example"], "improvements": ["quantify impact"],
        "competencies": [
            {"competency": "problem_solving", "score_1_5": 4,
             "evidence_quote": "i reproduced the mismatch locally and added structured logging",
             "star_element": "action"},
        ],
    }


def _stub_feedback(question, answer, resume_brief, config):
    return {
        "what_worked": "Clear, specific action.",
        "what_to_improve": "State the measurable result.",
        "star_coverage": {"situation": True, "task": True, "action": True, "result": False},
        "strong_answer_example": "Building on your reconciliation pipeline experience, ...",
    }


async def _seed_session(conn, session_id: str) -> None:
    sid = uuid.UUID(session_id)
    await conn.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider, difficulty, rubric_version) "
        "VALUES ($1,$2,'bedrock_direct','moderate','g2-2026.1') ON CONFLICT (session_id) DO NOTHING",
        sid, "test-user",
    )
    turns = [
        (0, "coach", "Tell me about a hard problem you solved."),
        (1, "student", "I reproduced the mismatch locally and added structured logging to trace it."),
        (2, "coach", "What was the result?"),
        (3, "student", "Reconciliation matched to the cent after I fixed the timezone bug."),
    ]
    for idx, who, txt in turns:
        await conn.execute(
            "INSERT INTO conversation_turn (turn_id, session_id, turn_index, speaker, transcript, started_at) "
            "VALUES ($1,$2,$3,$4,$5, now()) ON CONFLICT (session_id, turn_index) DO NOTHING",
            uuid.uuid4(), sid, idx, who, txt,
        )


async def _cleanup(conn, session_id: str) -> None:
    await conn.execute("DELETE FROM voice_session WHERE session_id = $1", uuid.UUID(session_id))


async def test_generate_persists_report_and_feedback_then_delete_purges():
    cfg = _config()
    conn = await _db_or_skip(cfg)
    session_id = str(uuid.uuid4())
    try:
        await _seed_session(conn, session_id)

        status = await process_session(session_id, cfg, score_fn=_stub_score, feedback_fn=_stub_feedback)
        assert status == "scored"

        rep = await conn.fetchrow("SELECT overall, status, competency_scorecard FROM report WHERE session_id=$1",
                                  uuid.UUID(session_id))
        assert rep is not None and rep["status"] == "scored"
        assert float(rep["overall"]) == 6.75
        qf = await conn.fetchval("SELECT count(*) FROM question_feedback WHERE session_id=$1", uuid.UUID(session_id))
        assert qf == 2  # two student answers

        # Idempotent re-score: still one report, feedback replaced not duplicated.
        await process_session(session_id, cfg, score_fn=_stub_score, feedback_fn=_stub_feedback)
        # claim is guarded -> second run skips (job already 'scored'); report count stays 1.
        nrep = await conn.fetchval("SELECT count(*) FROM report WHERE session_id=$1", uuid.UUID(session_id))
        assert nrep == 1

        # Bounded delete: removing the session purges report + feedback + job (SC-008).
        await conn.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(session_id))
        for tbl in ("report", "question_feedback", "report_job"):
            residual = await conn.fetchval(f"SELECT count(*) FROM {tbl} WHERE session_id=$1", uuid.UUID(session_id))
            assert residual == 0, f"residual rows in {tbl} after session delete"
    finally:
        await _cleanup(conn, session_id)
        await conn.close()


async def test_claim_is_guarded_against_double_processing():
    cfg = _config()
    conn = await _db_or_skip(cfg)
    session_id = str(uuid.uuid4())
    try:
        await _seed_session(conn, session_id)
        first = await persistence.claim_job(conn, session_id)
        second = await persistence.claim_job(conn, session_id)
        assert first is True and second is False  # only the first claim wins
    finally:
        await _cleanup(conn, session_id)
        await conn.close()
