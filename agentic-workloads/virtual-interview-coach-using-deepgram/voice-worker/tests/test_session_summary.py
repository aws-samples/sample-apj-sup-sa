"""Tests for the per-session product-quality summary (evidence loop).

One summary batch per session: turns / fallbacks / barge-ins / duration, dimensioned by
end_reason + reply_provider. Counters are fed by PersistenceWriter (turns) and DeadlineProcessor
(true barge-ins: InterruptionFrame while the bot speaks); MetricsSink.record_session emits the
CloudWatch batch + a counts-only log line. No PII anywhere.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from src.metrics import (
    M_SESSION_BARGE_INS,
    M_SESSION_COUNT,
    M_SESSION_DURATION,
    M_SESSION_FALLBACKS,
    M_SESSION_TURNS,
    MetricsSink,
    SessionStats,
)
from src.processors.deadline import DeadlineProcessor
from src.processors.persistence_writer import PersistenceWriter, StudentTurnTap


class _StubCw:
    def __init__(self):
        self.batches = []

    def put_metric_data(self, Namespace, MetricData):
        self.batches.append((Namespace, MetricData))


class _StubRecording:
    def flush_turn(self, speaker, turn_id):
        pass


def _capture(proc):
    async def fake_push(frame, direction=FrameDirection.DOWNSTREAM):
        pass

    proc.push_frame = fake_push  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_record_session_emits_one_batch_with_dimensions():
    cw = _StubCw()
    sink = MetricsSink(None, namespace="Test/NS", cw_client=cw)
    stats = SessionStats(student_turns=4, coach_turns=5, fallbacks=1, barge_ins=2)
    await sink.record_session(stats, end_reason="completed", reply_provider="bedrock_direct")

    assert len(cw.batches) == 1
    ns, data = cw.batches[0]
    assert ns == "Test/NS"
    by_name = {m["MetricName"]: m for m in data}
    assert by_name[M_SESSION_COUNT]["Value"] == 1
    assert by_name[M_SESSION_TURNS]["Value"] == 9
    assert by_name[M_SESSION_FALLBACKS]["Value"] == 1
    assert by_name[M_SESSION_BARGE_INS]["Value"] == 2
    assert by_name[M_SESSION_DURATION]["Unit"] == "Seconds"
    dims = {d["Name"]: d["Value"] for d in by_name[M_SESSION_COUNT]["Dimensions"]}
    assert dims == {"end_reason": "completed", "reply_provider": "bedrock_direct"}


@pytest.mark.asyncio
async def test_record_session_logs_counts_without_cw(caplog):
    # No CloudWatch client (local harness): the same evidence lands in the log, counts only.
    sink = MetricsSink(None, cw_client=None)
    stats = SessionStats(student_turns=2, coach_turns=3)
    import logging

    with caplog.at_level(logging.INFO, logger="voice_worker"):
        await sink.record_session(stats, end_reason="dropped", reply_provider="agentcore")
    line = next(r.message for r in caplog.records if "session summary" in r.message)
    assert "end_reason=dropped" in line and "turns=5" in line


@pytest.mark.asyncio
async def test_record_session_cw_failure_never_raises():
    class _Boom:
        def put_metric_data(self, **k):
            raise RuntimeError("cw down")

    sink = MetricsSink(None, cw_client=_Boom())
    await sink.record_session(SessionStats(), end_reason="error", reply_provider="bedrock_direct")


@pytest.mark.asyncio
async def test_persistence_writer_counts_turns_into_stats():
    stats = SessionStats()
    writer = PersistenceWriter(
        session_id="s1", persistence=None, recording=_StubRecording(), stats=stats
    )
    tap = StudentTurnTap(writer)
    _capture(tap)
    await tap.process_frame(TranscriptionFrame("hello", "u", "t"), FrameDirection.DOWNSTREAM)
    await writer.on_coach_turn("A question.")
    await writer.on_coach_turn("Closing.", planned=False)  # unplanned still counts as a turn
    assert stats.student_turns == 1
    assert stats.coach_turns == 2
    assert stats.turns_total == 3


@pytest.mark.asyncio
async def test_turn_emission_runs_off_loop_and_still_delivers():
    # Review fix #1: record_turn's CloudWatch emission must NOT block the caller (it fires on the
    # live audio path at first-audio time). It is scheduled fire-and-forget; the batch still
    # arrives once the loop drains.
    import asyncio

    from src.persistence import LatencyRecord

    cw = _StubCw()
    sink = MetricsSink(None, namespace="Test/NS", cw_client=cw)
    rec = LatencyRecord(
        response_gap_ms=300, stt_finalization_ms=100, reply_ttft_ms=50,
        tts_first_audio_ms=80, orchestration_ms=10, reply_provider="bedrock_direct",
    )
    await sink.record_turn("s1", "", rec)
    # Not emitted synchronously — scheduled as a background task.
    assert cw.batches == []
    assert len(sink._bg_tasks) == 1
    await asyncio.gather(*sink._bg_tasks)
    assert len(cw.batches) == 1
    names = {m["MetricName"] for m in cw.batches[0][1]}
    assert "response_gap_ms" in names


@pytest.mark.asyncio
async def test_turn_emission_failure_logged_not_raised():
    import asyncio

    from src.persistence import LatencyRecord

    class _Boom:
        def put_metric_data(self, **k):
            raise RuntimeError("cw down")

    sink = MetricsSink(None, cw_client=_Boom())
    rec = LatencyRecord(
        response_gap_ms=1, stt_finalization_ms=1, reply_ttft_ms=1,
        tts_first_audio_ms=1, orchestration_ms=0, reply_provider="bedrock_direct",
    )
    await sink.record_turn("s1", "", rec)
    await asyncio.gather(*sink._bg_tasks)  # must not raise


def test_mark_started_reanchors_duration():
    # Review fix #4: duration must measure the SPOKEN session from media-connect, not from
    # pipeline construction during /offer (ICE negotiation takes seconds).
    import time

    stats = SessionStats()
    stats.started_monotonic -= 100.0  # pretend construction happened 100s ago
    assert stats.duration_s() >= 100
    stats.mark_started()
    assert stats.duration_s() <= 1


@pytest.mark.asyncio
async def test_deadline_counts_true_barge_ins_only(init_processor):
    # An InterruptionFrame while the bot speaks is a barge-in; while idle (e.g. a ptt press
    # between coach turns) it is not.
    stats = SessionStats()

    async def _noop():
        pass

    proc = DeadlineProcessor(budget_s=None, on_deadline=_noop, stats=stats)
    await init_processor(proc)
    _capture(proc)

    await proc.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)  # bot idle: no count
    await proc.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)  # true barge-in
    await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)  # idle again

    assert stats.barge_ins == 1

    # Wrap-up injects its own InterruptionFrame to cancel an in-flight reply — must NOT count.
    await proc.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    stats.wrap_up_started = True
    await proc.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    assert stats.barge_ins == 1
