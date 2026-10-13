"""Unit tests for the custom Pipecat FrameProcessors (Feature 007, T011-T015).

All offline (no live services, no real pipeline). Each test drives a processor's process_frame /
control surface and asserts the pushed-frame behavior, mirroring the hand-loop semantics these
processors port. Run in the isolated venv:
    source .venv-pipecat/bin/activate && pytest tests/test_processors.py
"""

from __future__ import annotations

import asyncio

import pytest

# These tests exercise the Pipecat processors; skip the whole module in a venv without pipecat (the
# G1 rollback venv) instead of erroring during collection.
pytest.importorskip("pipecat")

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from src.processors import (
    DeadlineProcessor,
    LatencyObserver,
    LeadClauseProcessor,
    RecordingProcessor,
    TurnGateProcessor,
    STRATEGY_NATIVE,
    STRATEGY_PROCESSOR,
    MODE_AUTO,
    MODE_PTT,
)


def _capture(proc):
    """Replace push_frame with a recorder and return the list it appends to."""
    pushed: list = []

    async def fake_push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = fake_push  # type: ignore[assignment]
    return pushed


def _ctx_frame():
    c = LLMContext()
    c.set_messages([{"role": "user", "content": "hi"}])
    return LLMContextFrame(context=c)


# --- LeadClauseProcessor -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_clause_injects_backchannel_before_context():
    proc = LeadClauseProcessor(strategy=STRATEGY_PROCESSOR)
    pushed = _capture(proc)
    await proc.process_frame(_ctx_frame(), FrameDirection.DOWNSTREAM)
    # backchannel TTSSpeakFrame FIRST, then the context frame forwarded
    from src.processors import LEAD_INS

    assert isinstance(pushed[0], TTSSpeakFrame)
    assert pushed[0].text in LEAD_INS  # a real backchannel lead-in
    assert isinstance(pushed[1], LLMContextFrame)


@pytest.mark.asyncio
async def test_lead_clause_cycles_lead_ins():
    proc = LeadClauseProcessor(strategy=STRATEGY_PROCESSOR)
    pushed = _capture(proc)
    for _ in range(3):
        await proc.process_frame(_ctx_frame(), FrameDirection.DOWNSTREAM)
    leads = [f.text for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(leads) == 3
    assert len(set(leads)) == 3  # cycled, not repeated


@pytest.mark.asyncio
async def test_native_strategy_is_passthrough():
    proc = LeadClauseProcessor(strategy=STRATEGY_NATIVE)
    pushed = _capture(proc)
    await proc.process_frame(_ctx_frame(), FrameDirection.DOWNSTREAM)
    assert len(pushed) == 1
    assert isinstance(pushed[0], LLMContextFrame)
    assert not any(isinstance(f, TTSSpeakFrame) for f in pushed)


# --- DeadlineProcessor -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deadline_fires_independently_after_budget(init_processor):
    fired = asyncio.Event()

    async def on_deadline():
        fired.set()

    proc = await init_processor(
        DeadlineProcessor(budget_s=0.05, on_deadline=on_deadline, settle_grace_s=0.1)
    )
    _capture(proc)
    await proc.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
    # NO further frames (no student turn) — the deadline must still fire on its own.
    await asyncio.wait_for(fired.wait(), timeout=1.0)
    assert fired.is_set()
    await proc.cleanup()


@pytest.mark.asyncio
async def test_deadline_waits_for_in_flight_bot_utterance(init_processor):
    order: list[str] = []

    async def on_deadline():
        order.append("deadline")

    proc = await init_processor(
        DeadlineProcessor(budget_s=0.05, on_deadline=on_deadline, settle_grace_s=1.0)
    )
    _capture(proc)
    await proc.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.15)  # budget elapses while the bot is "speaking"
    assert order == []  # must NOT fire mid-utterance
    await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.1)
    assert order == ["deadline"]
    await proc.cleanup()


@pytest.mark.asyncio
async def test_deadline_disabled_when_no_budget(init_processor):
    fired = asyncio.Event()

    async def on_deadline():
        fired.set()

    proc = await init_processor(DeadlineProcessor(budget_s=None, on_deadline=on_deadline))
    _capture(proc)
    await proc.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.1)
    assert not fired.is_set()
    await proc.cleanup()


# --- LatencyObserver ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latency_observer_reconstructs_gap():
    records: list = []

    async def on_turn(rec):
        records.append(rec)

    proc = LatencyObserver(
        reply_provider="bedrock_direct", stt_finalization_ms=280, on_turn=on_turn
    )
    _capture(proc)
    # Simulate a turn: user stops -> stt final -> context (reply requested) -> first text -> audio.
    await proc.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(UserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.sleep(0.01)
    await proc.process_frame(TranscriptionFrame("hello", "u", "t"), FrameDirection.DOWNSTREAM)
    await proc.process_frame(_ctx_frame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.01)
    await proc.process_frame(LLMTextFrame("Right."), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.01)
    await proc.process_frame(
        TTSAudioRawFrame(b"\x00\x00", sample_rate=16000, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    assert len(records) == 1
    rec = records[0]
    assert rec.response_gap_ms > 0
    assert rec.reply_provider == "bedrock_direct"
    # only ONE record per turn even if more audio frames arrive
    await proc.process_frame(
        TTSAudioRawFrame(b"\x00\x00", sample_rate=16000, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    assert len(records) == 1


@pytest.mark.asyncio
async def test_latency_observer_ignores_audio_outside_turn():
    records: list = []

    async def on_turn(rec):
        records.append(rec)

    proc = LatencyObserver(reply_provider="x", stt_finalization_ms=280, on_turn=on_turn)
    _capture(proc)
    # Opening-question audio with no preceding end-of-speech must NOT be measured.
    await proc.process_frame(
        TTSAudioRawFrame(b"\x00\x00", sample_rate=16000, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    assert records == []


# --- RecordingProcessor ------------------------------------------------------------------


class _Cfg:
    record_audio = False
    audio_bucket = None
    audio_kms_key_id = None
    aws_region = "us-west-2"


class _CfgRecOn(_Cfg):
    record_audio = True
    audio_bucket = "bucket"


@pytest.mark.asyncio
async def test_recording_disabled_is_passthrough_no_buffers():
    # No bucket + kill-switch off -> disabled regardless of consent.
    proc = RecordingProcessor(config=_Cfg(), session_id="s1", consent=True, on_audio_uri=None)
    assert proc.enabled is False
    # flush_turn is a safe no-op when disabled
    proc.flush_turn("coach", "turn-1")
    await proc.cleanup()


@pytest.mark.asyncio
async def test_recording_requires_consent_even_when_configured():
    # G6 / FR-001: bucket + kill-switch on, but NO consent -> still disabled (no audio, ever).
    no_consent = RecordingProcessor(config=_CfgRecOn(), session_id="s1", consent=False)
    assert no_consent.enabled is False
    # consent ON + configured -> enabled
    with_consent = RecordingProcessor(config=_CfgRecOn(), session_id="s1", consent=True)
    assert with_consent.enabled is True


@pytest.mark.asyncio
async def test_recording_consent_default_is_fail_closed():
    # consent defaults to False (fail-closed) — omitting it must NOT record.
    proc = RecordingProcessor(config=_CfgRecOn(), session_id="s1")
    assert proc.enabled is False


@pytest.mark.asyncio
async def test_recording_enabled_schedules_upload(monkeypatch, init_processor):
    class _CfgOn(_Cfg):
        record_audio = True
        audio_bucket = "bucket"

    uploaded: list = []

    async def fake_upload(config, session_id, turn_id, pcm):
        uploaded.append((turn_id, len(pcm)))
        return f"s3://bucket/audio/{session_id}/{turn_id}.wav"

    linked: list = []

    async def on_uri(turn_id, uri):
        linked.append((turn_id, uri))

    import src.processors.recording as R

    monkeypatch.setattr(R, "upload_turn_audio", fake_upload)
    proc = await init_processor(
        RecordingProcessor(config=_CfgOn(), session_id="s1", consent=True, on_audio_uri=on_uri)
    )
    _capture(proc)
    # feed an outbound audio frame, then flush the coach turn
    from pipecat.frames.frames import TTSAudioRawFrame as _Audio

    await proc.process_frame(
        _Audio(b"\x01\x02" * 100, sample_rate=16000, num_channels=1), FrameDirection.DOWNSTREAM
    )
    proc.flush_turn("coach", "turn-1")
    await asyncio.sleep(0.05)  # let the scheduled task run
    assert uploaded == [("turn-1", 200)]
    assert linked == [("turn-1", "s3://bucket/audio/s1/turn-1.wav")]
    await proc.cleanup()


# --- TurnGateProcessor -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_mode_passes_vad_stop_through():
    proc = TurnGateProcessor(mode=MODE_AUTO)
    pushed = _capture(proc)
    await proc.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert len(pushed) == 1
    assert isinstance(pushed[0], UserStoppedSpeakingFrame)


@pytest.mark.asyncio
async def test_ptt_mode_suppresses_vad_stop():
    proc = TurnGateProcessor(mode=MODE_PTT)
    pushed = _capture(proc)
    await proc.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert pushed == []  # neither VAD stop survives in ptt mode


@pytest.mark.asyncio
async def test_ptt_turn_end_emits_stop_frame():
    proc = TurnGateProcessor(mode=MODE_PTT)
    pushed = _capture(proc)
    await proc.on_control({"type": "turn_end"})
    assert len(pushed) == 1
    assert isinstance(pushed[0], UserStoppedSpeakingFrame)


@pytest.mark.asyncio
async def test_turn_start_emits_interruption_for_bargein():
    proc = TurnGateProcessor(mode=MODE_PTT)
    pushed = _capture(proc)
    await proc.on_control({"type": "turn_start"})
    assert len(pushed) == 1
    assert isinstance(pushed[0], InterruptionFrame)


@pytest.mark.asyncio
async def test_mode_switch_via_control():
    proc = TurnGateProcessor(mode=MODE_AUTO)
    _capture(proc)
    await proc.on_control({"type": "mode", "value": "ptt"})
    assert proc.mode == MODE_PTT
    await proc.on_control({"type": "mode", "value": "auto"})
    assert proc.mode == MODE_AUTO


@pytest.mark.asyncio
async def test_lead_in_buckets_match_student_text():
    # Live-use fix: the bridge must fit WHAT THE STUDENT JUST SAID (a clarification request must
    # not get "That's great."), and every bridge ends with a continuing contour (em-dash), never a
    # sentence-final period that reads as a disconnect.
    from src.processors.lead_clause import (
        ACK_LEAD_INS, LEAD_INS, QUESTION_LEAD_INS, REPEAT_LEAD_INS, SHORT_LEAD_INS, pick_lead_in,
    )

    assert pick_lead_in("Sorry, can you repeat that?", 0) in REPEAT_LEAD_INS
    assert pick_lead_in("How many questions are left?", 0) in QUESTION_LEAD_INS
    assert pick_lead_in("yes", 0) in SHORT_LEAD_INS
    full = "I led the migration end to end, planned the cutover, and we shipped with zero downtime."
    assert pick_lead_in(full, 0) in ACK_LEAD_INS
    # Continuing contour, not sentence-final: every bridge ends with an em-dash.
    assert all(lead.rstrip().endswith("—") for lead in LEAD_INS)
