"""Tests for the DB-authoritative end_reason on the session summary (review fix #3).

The metric dimension must match RDS: finalize_session atomically closes a still-open row with
the worker fallback and RETURNS the winning terminal state — so a voluntary SPA hang-up (backend
already wrote student_ended) is no longer reported as 'dropped', and a true drop finally closes
its row. These drive SignalingServer._emit_session_summary with stubs (no DB, no pipeline run).
"""

from __future__ import annotations

import pytest

pytest.importorskip("pipecat")

from src.config import Config
from src.server import SignalingServer


class _StubPersistence:
    def __init__(self, db_reason):
        self.db_reason = db_reason
        self.calls = []

    async def finalize_session(self, session_id, fallback_reason):
        self.calls.append((session_id, fallback_reason))
        # The real UPDATE...RETURNING: COALESCE keeps an existing terminal state, else writes
        # the fallback and returns it.
        return self.db_reason or fallback_reason


class _StubMetrics:
    def __init__(self):
        self.sessions = []

    async def record_session(self, stats, end_reason, reply_provider):
        self.sessions.append((end_reason, reply_provider))


class _StubStats:
    pass


class _StubPipeline:
    def __init__(self, end_reason="dropped"):
        self.end_reason = end_reason
        self.summary_emitted = False
        self.stats = _StubStats()


def _server(persistence) -> SignalingServer:
    srv = SignalingServer.__new__(SignalingServer)
    srv._config = Config.load()
    srv._persistence = persistence
    srv._metrics = _StubMetrics()
    srv._sessions = {}
    return srv


@pytest.mark.asyncio
async def test_backend_student_ended_wins_over_worker_dropped(monkeypatch):
    monkeypatch.setenv("REPLY_PROVIDER", "bedrock_direct")
    p = _StubPersistence(db_reason="student_ended")  # backend already closed the row
    srv = _server(p)
    pipe = _StubPipeline(end_reason="dropped")  # worker-local fallback says dropped
    await srv._emit_session_summary("s1", pipe)
    assert srv._metrics.sessions == [("student_ended", srv._config.reply_provider)]
    assert p.calls == [("s1", "dropped")]


@pytest.mark.asyncio
async def test_true_drop_closes_row_with_fallback():
    p = _StubPersistence(db_reason=None)  # nobody wrote a terminal state: a real drop
    srv = _server(p)
    await srv._emit_session_summary("s1", _StubPipeline(end_reason="dropped"))
    assert srv._metrics.sessions[0][0] == "dropped"


@pytest.mark.asyncio
async def test_no_db_uses_worker_local_reason():
    srv = _server(persistence=None)
    await srv._emit_session_summary("s1", _StubPipeline(end_reason="error"))
    assert srv._metrics.sessions[0][0] == "error"


@pytest.mark.asyncio
async def test_db_failure_degrades_to_worker_local():
    class _Boom:
        async def finalize_session(self, *a):
            raise RuntimeError("db down")

    srv = _server(_Boom())
    pipe = _StubPipeline(end_reason="completed")
    await srv._emit_session_summary("s1", pipe)
    assert srv._metrics.sessions == [("completed", srv._config.reply_provider)]


@pytest.mark.asyncio
async def test_summary_emitted_once():
    srv = _server(persistence=None)
    pipe = _StubPipeline()
    await srv._emit_session_summary("s1", pipe)
    await srv._emit_session_summary("s1", pipe)
    assert len(srv._metrics.sessions) == 1
