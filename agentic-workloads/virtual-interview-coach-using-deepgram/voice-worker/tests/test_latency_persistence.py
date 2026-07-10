"""Tests for the turn_latency RDS attach chain (Feature 007 metrics-persistence fix).

The live Pipecat path emitted CloudWatch + the gate log but NEVER wrote the durable turn_latency
row: _record_turn passed turn_id="" and MetricsSink skips the DB write without one. The fix stashes
the LatencyRecord at first audio and writes the row from _on_coach_persisted once PersistenceWriter
has the coach turn_id (the FK). These drive the two callbacks directly with stubs (no pipeline).
"""

from __future__ import annotations

import pytest

pytest.importorskip("pipecat")

from src.pipecat_pipeline import InterviewPipeline
from src.persistence import LatencyRecord


def _rec(gap=300) -> LatencyRecord:
    return LatencyRecord(
        response_gap_ms=gap,
        stt_finalization_ms=100,
        reply_ttft_ms=50,
        tts_first_audio_ms=80,
        orchestration_ms=10,
        reply_provider="bedrock_direct",
    )


class _StubMetrics:
    def __init__(self):
        self.calls = []

    async def record_turn(self, session_id, turn_id, rec, network_path=None):
        self.calls.append((session_id, turn_id, rec))


class _StubPersistence:
    def __init__(self):
        self.latency_rows = []

    async def record_latency(self, session_id, turn_id, rec, measured_at):
        self.latency_rows.append((session_id, turn_id, rec))


class _StubLatency:
    def substantive_reply_ms(self):
        return None


def _ip(persistence):
    """A minimally-initialized InterviewPipeline carrying only what the two callbacks touch."""
    ip = InterviewPipeline.__new__(InterviewPipeline)
    ip.session_id = "sess-1"
    ip._metrics = _StubMetrics()
    ip._persistence = persistence
    ip._pending_latency_rec = None
    ip._pending_coach_turn_id = None
    ip.latency = _StubLatency()
    return ip


@pytest.mark.asyncio
async def test_latency_row_written_once_coach_turn_persists():
    p = _StubPersistence()
    ip = _ip(p)
    rec = _rec()
    await ip._record_turn(rec)           # first audio: CW emitted, row deferred
    assert p.latency_rows == []          # not yet — no coach turn_id exists
    await ip._on_coach_persisted("turn-42")
    assert p.latency_rows == [("sess-1", "turn-42", rec)]
    # the metrics emission still happened immediately (empty turn_id -> CW only)
    assert ip._metrics.calls == [("sess-1", "", rec)]


@pytest.mark.asyncio
async def test_pending_record_consumed_only_once():
    p = _StubPersistence()
    ip = _ip(p)
    await ip._record_turn(_rec())
    await ip._on_coach_persisted("turn-1")
    await ip._on_coach_persisted("turn-2")  # a coach turn with no measured gap (e.g. after barge-in)
    assert [row[1] for row in p.latency_rows] == ["turn-1"]


@pytest.mark.asyncio
async def test_unattached_record_replaced_by_next_turn():
    # The measured reply was interrupted (its coach row never persisted); the NEXT turn's record
    # must not attach the stale measurement to the new turn — the stale one is dropped.
    p = _StubPersistence()
    ip = _ip(p)
    await ip._record_turn(_rec(gap=999))   # turn A measured, never persisted
    rec_b = _rec(gap=250)
    await ip._record_turn(rec_b)           # turn B replaces the stale pending record
    await ip._on_coach_persisted("turn-b")
    assert p.latency_rows == [("sess-1", "turn-b", rec_b)]


@pytest.mark.asyncio
async def test_no_persistence_is_safe():
    ip = _ip(persistence=None)
    await ip._record_turn(_rec())
    await ip._on_coach_persisted("turn-1")  # must not raise; row simply not written


@pytest.mark.asyncio
async def test_record_latency_failure_does_not_raise():
    class _Boom:
        async def record_latency(self, *a, **k):
            raise RuntimeError("db down")

    ip = _ip(_Boom())
    await ip._record_turn(_rec())
    await ip._on_coach_persisted("turn-1")  # must not raise (measurement never crashes the pipeline)
