"""Tests for InterviewPipeline._wrap_up termination sequence (Feature 007 wrap-up-termination fix).

The live test found the interview kept generating questions after the director fired wrap-up, and the
session was never marked ended. _wrap_up must now, in order: mute STT (stop new turns), speak the
debrief/closing, record end_reason, and queue EndFrame — once (idempotent). These drive _wrap_up
directly with stubs (no live services / no real pipeline / no heavy __init__).
"""

from __future__ import annotations

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import EndFrame, STTMuteFrame, TTSSpeakFrame

from src.config import Config
from src.pipecat_pipeline import InterviewPipeline


class _StubWorker:
    def __init__(self):
        self.frames = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


class _StubWriter:
    def __init__(self):
        self.coach = []

    async def on_coach_turn(self, text, planned=True):
        self.coach.append((text, planned))


class _StubPersistence:
    def __init__(self):
        self.ended = []

    async def end_session(self, session_id, end_reason):
        self.ended.append((session_id, end_reason))


def _ip(persistence=None, debrief="Strength X. Improve Y."):
    """A minimally-initialized InterviewPipeline carrying only what _wrap_up touches (bypasses the
    heavy __init__ that builds the real VAD/transport/pipeline)."""
    from src.metrics import SessionStats

    ip = InterviewPipeline.__new__(InterviewPipeline)
    # dataclasses.replace keeps the test hermetic regardless of WRAP_UP_DEBRIEF env leaked by another
    # test — _wrap_up only generates a debrief when config.wrap_up_debrief is True.
    import dataclasses

    ip.config = dataclasses.replace(Config.load(), wrap_up_debrief=True)
    ip.session_id = "sess-1"
    ip._persistence = persistence
    ip._session_plan = object()  # truthy so the debrief path runs
    ip._wrapped_up = False
    ip.worker = _StubWorker()
    ip.persistence_writer = _StubWriter()
    ip.stats = SessionStats()
    ip.end_reason = "dropped"

    async def _fake_debrief():
        return debrief

    ip._generate_debrief = _fake_debrief  # type: ignore[assignment]
    return ip


@pytest.mark.asyncio
async def test_wrapup_mutes_then_speaks_then_ends():
    from pipecat.frames.frames import InterruptionFrame

    p = _StubPersistence()
    ip = _ip(persistence=p)
    await ip._wrap_up()
    frames = ip.worker.frames
    # 1) STT muted FIRST so no new turn can start while we wrap up, then any in-flight coach
    #    reply is CANCELLED (the live-session "two endings back-to-back" fix)
    assert isinstance(frames[0], STTMuteFrame) and frames[0].mute is True
    assert isinstance(frames[1], InterruptionFrame)
    # 2) the closing (with debrief) is spoken
    spoken = " ".join(f.text for f in frames if isinstance(f, TTSSpeakFrame))
    assert "Strength X" in spoken
    # 3) EndFrame terminates the pipeline, last
    assert isinstance(frames[-1], EndFrame)
    # session recorded as ended (end_reason was never written before this fix)
    assert p.ended == [("sess-1", "completed")]
    # the wrap-up's own InterruptionFrame must not count as a student barge-in
    assert ip.stats.wrap_up_started is True


@pytest.mark.asyncio
async def test_question_like_debrief_rejected():
    # The debrief is LLM output; if it comes back as ANOTHER interview question (the live bug),
    # speak the fixed closing only — never ask the candidate something and then hang up.
    ip = _ip(persistence=None,
             debrief="Tell me about a time you disagreed with a senior engineer. How did you handle it?")
    await ip._wrap_up()
    spoken = " ".join(f.text for f in ip.worker.frames if isinstance(f, TTSSpeakFrame))
    assert "disagreed" not in spoken
    assert "wraps up our interview" in spoken  # the fixed _CLOSING line


@pytest.mark.asyncio
async def test_wrapup_idempotent():
    p = _StubPersistence()
    ip = _ip(persistence=p)
    await ip._wrap_up()
    n = len(ip.worker.frames)
    await ip._wrap_up()  # no-op
    assert len(ip.worker.frames) == n
    assert p.ended == [("sess-1", "completed")]


@pytest.mark.asyncio
async def test_wrapup_without_persistence_still_mutes_and_ends():
    ip = _ip(persistence=None)
    await ip._wrap_up()  # must not raise without a DB
    kinds = [type(f).__name__ for f in ip.worker.frames]
    assert "STTMuteFrame" in kinds
    assert "EndFrame" in kinds


@pytest.mark.asyncio
async def test_wrapup_degrades_to_fixed_closing_when_debrief_empty():
    ip = _ip(persistence=None, debrief="")
    await ip._wrap_up()
    spoken = " ".join(f.text for f in ip.worker.frames if isinstance(f, TTSSpeakFrame))
    assert "wraps up our interview" in spoken  # the fixed _CLOSING line
