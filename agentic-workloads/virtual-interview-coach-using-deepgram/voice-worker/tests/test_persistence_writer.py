"""Tests for incremental per-turn persistence + recording flush (Feature 007 CRITICAL-2 fix).

The Pipecat pipeline must persist each conversation_turn as it completes (FR-003) and hand the
turn_id to the RecordingProcessor so audio uploads + the PCM buffer drains. These tests drive the
StudentTurnTap / CoachTurnTap and the shared PersistenceWriter with stubs (no DB, no real frames
needing a pipeline) and assert the writes + flushes happen with the right turn_index/speaker.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from src.processors.persistence_writer import CoachTurnTap, PersistenceWriter, StudentTurnTap


class _StubPersistence:
    def __init__(self) -> None:
        self.turns: list = []
        self.facts: list = []  # (archetype_id, is_followup, targeted_star_element) per turn

    async def append_turn(self, session_id, turn_index, speaker, transcript, started_at,
                          ended_at=None, **kwargs):
        self.turns.append((turn_index, speaker, transcript))
        self.facts.append((kwargs.get("archetype_id"), kwargs.get("is_followup"),
                           kwargs.get("targeted_star_element")))
        return f"turn-{turn_index}"


class _StubRecording:
    def __init__(self) -> None:
        self.flushed: list = []

    def flush_turn(self, speaker, turn_id):
        self.flushed.append((speaker, turn_id))


def _capture(proc):
    pushed: list = []

    async def fake_push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    proc.push_frame = fake_push  # type: ignore[assignment]
    return pushed


@pytest.mark.asyncio
async def test_student_turn_persisted_and_flushed():
    p, r = _StubPersistence(), _StubRecording()
    writer = PersistenceWriter(session_id="s1", persistence=p, recording=r)
    tap = StudentTurnTap(writer)
    pushed = _capture(tap)
    await tap.process_frame(TranscriptionFrame("I built a payments pipeline.", "u", "t"),
                            FrameDirection.DOWNSTREAM)
    assert p.turns == [(0, "student", "I built a payments pipeline.")]
    assert r.flushed == [("student", "turn-0")]
    # frame forwarded untouched
    assert len(pushed) == 1 and isinstance(pushed[0], TranscriptionFrame)


@pytest.mark.asyncio
async def test_blank_transcription_not_persisted():
    p, r = _StubPersistence(), _StubRecording()
    tap = StudentTurnTap(PersistenceWriter(session_id="s1", persistence=p, recording=r))
    _capture(tap)
    await tap.process_frame(TranscriptionFrame("   ", "u", "t"), FrameDirection.DOWNSTREAM)
    assert p.turns == []
    assert r.flushed == []


@pytest.mark.asyncio
async def test_coach_turn_accumulates_and_persists_on_end():
    p, r = _StubPersistence(), _StubRecording()
    tap = CoachTurnTap(PersistenceWriter(session_id="s1", persistence=p, recording=r))
    _capture(tap)
    await tap.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    for t in ["Right. ", "Tell me ", "about a challenge."]:
        await tap.process_frame(LLMTextFrame(t), FrameDirection.DOWNSTREAM)
    # nothing persisted until the response ends
    assert p.turns == []
    await tap.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    assert p.turns == [(0, "coach", "Right. Tell me about a challenge.")]
    assert r.flushed == [("coach", "turn-0")]


@pytest.mark.asyncio
async def test_coach_turn_interrupted_partial_not_persisted():
    # A new LLM response starts while a prior one was still accumulating (its End was dropped on
    # barge-in) -> the partial reply is dropped, only the completed second reply is persisted.
    p, r = _StubPersistence(), _StubRecording()
    tap = CoachTurnTap(PersistenceWriter(session_id="s1", persistence=p, recording=r))
    _capture(tap)
    await tap.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await tap.process_frame(LLMTextFrame("This is a partial reply that gets"), FrameDirection.DOWNSTREAM)
    # no End (interrupted) — a new Start arrives
    await tap.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await tap.process_frame(LLMTextFrame("Fresh reply."), FrameDirection.DOWNSTREAM)
    await tap.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    # only the fresh, completed reply is persisted — no stale-text bleed
    assert p.turns == [(0, "coach", "Fresh reply.")]


@pytest.mark.asyncio
async def test_coach_end_without_start_is_safe():
    p, r = _StubPersistence(), _StubRecording()
    tap = CoachTurnTap(PersistenceWriter(session_id="s1", persistence=p, recording=r))
    _capture(tap)
    # End with no active window (e.g. Start dropped on barge-in) -> no spurious empty persist
    await tap.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    assert p.turns == []
    assert r.flushed == []


@pytest.mark.asyncio
async def test_interleaved_turns_get_monotonic_indices():
    p, r = _StubPersistence(), _StubRecording()
    writer = PersistenceWriter(session_id="s1", persistence=p, recording=r)
    s_tap, c_tap = StudentTurnTap(writer), CoachTurnTap(writer)
    _capture(s_tap); _capture(c_tap)
    # student turn 0
    await s_tap.process_frame(TranscriptionFrame("Hello there.", "u", "t"), FrameDirection.DOWNSTREAM)
    # coach turn 1
    await c_tap.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await c_tap.process_frame(LLMTextFrame("Tell me about yourself."), FrameDirection.DOWNSTREAM)
    await c_tap.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    # student turn 2
    await s_tap.process_frame(TranscriptionFrame("I am a CS student.", "u", "t"), FrameDirection.DOWNSTREAM)
    assert [t[0] for t in p.turns] == [0, 1, 2]
    assert [t[1] for t in p.turns] == ["student", "coach", "student"]


@pytest.mark.asyncio
async def test_no_db_still_drains_buffer_via_flush():
    # persistence=None -> append_turn skipped, turn_id None, but flush_turn STILL called (drains buffer)
    r = _StubRecording()
    writer = PersistenceWriter(session_id="s1", persistence=None, recording=r)
    tap = StudentTurnTap(writer)
    _capture(tap)
    await tap.process_frame(TranscriptionFrame("hi", "u", "t"), FrameDirection.DOWNSTREAM)
    assert r.flushed == [("student", None)]  # flush called so the RecordingProcessor drains its buffer


@pytest.mark.asyncio
async def test_append_turn_failure_does_not_raise():
    class _BoomPersistence:
        async def append_turn(self, *a, **k):
            raise RuntimeError("db down")

    r = _StubRecording()
    writer = PersistenceWriter(session_id="s1", persistence=_BoomPersistence(), recording=r)
    tap = StudentTurnTap(writer)
    _capture(tap)
    # must not raise; flush still called with None turn_id so the buffer drains
    await tap.process_frame(TranscriptionFrame("hi", "u", "t"), FrameDirection.DOWNSTREAM)
    assert r.flushed == [("student", None)]


# --- archetype structural facts (FR-212a regression: the Pipecat path wrote all-NULL) ----------

class _FakeIntent:
    archetype_id = "arch-7"


class _FakePlan:
    intent = _FakeIntent()
    is_followup = True
    targeted_star_element = "result"


@pytest.mark.asyncio
async def test_planned_coach_turn_carries_archetype_facts():
    p, r = _StubPersistence(), _StubRecording()
    holder = {"plan": _FakePlan()}
    writer = PersistenceWriter(session_id="s1", persistence=p, recording=r, planner_holder=holder)
    await writer.on_coach_turn("Which result did that produce?")
    assert p.facts == [("arch-7", True, "result")]


@pytest.mark.asyncio
async def test_student_turn_never_carries_archetype_facts():
    p, r = _StubPersistence(), _StubRecording()
    holder = {"plan": _FakePlan()}
    writer = PersistenceWriter(session_id="s1", persistence=p, recording=r, planner_holder=holder)
    await writer.on_student_turn("I led the migration.")
    assert p.facts == [(None, None, None)]


@pytest.mark.asyncio
async def test_unplanned_coach_turn_carries_no_facts():
    # The opening question / closing line must NOT inherit the last planned archetype.
    p, r = _StubPersistence(), _StubRecording()
    holder = {"plan": _FakePlan()}
    writer = PersistenceWriter(session_id="s1", persistence=p, recording=r, planner_holder=holder)
    await writer.on_coach_turn("That wraps up our interview.", planned=False)
    assert p.facts == [(None, None, None)]


@pytest.mark.asyncio
async def test_no_holder_or_exhausted_plan_writes_nulls():
    p, r = _StubPersistence(), _StubRecording()
    # generic session: no holder at all
    w1 = PersistenceWriter(session_id="s1", persistence=p, recording=r)
    await w1.on_coach_turn("Tell me about yourself.")
    # exhausted plan: intent is None
    class _Exhausted:
        intent = None
        is_followup = False
        targeted_star_element = None
    w2 = PersistenceWriter(session_id="s1", persistence=p, recording=r,
                           planner_holder={"plan": _Exhausted()})
    await w2.on_coach_turn("Anything to add?")
    assert p.facts == [(None, None, None), (None, None, None)]


# --- coach turn_id feedback (turn_latency FK attach) -------------------------------------------

@pytest.mark.asyncio
async def test_planned_coach_turn_reports_turn_id():
    p, r = _StubPersistence(), _StubRecording()
    seen: list = []

    async def on_persisted(turn_id):
        seen.append(turn_id)

    writer = PersistenceWriter(session_id="s1", persistence=p, recording=r,
                               on_coach_persisted=on_persisted)
    await writer.on_student_turn("hello")     # student: no callback
    await writer.on_coach_turn("A question.")  # planned coach: callback fires
    await writer.on_coach_turn("Bye.", planned=False)  # unplanned: no callback
    assert seen == ["turn-1"]


@pytest.mark.asyncio
async def test_no_turn_id_no_callback_and_callback_failure_safe():
    r = _StubRecording()
    calls: list = []

    async def boom(turn_id):
        calls.append(turn_id)
        raise RuntimeError("attach failed")

    # no DB -> no turn_id -> callback never fires
    w1 = PersistenceWriter(session_id="s1", persistence=None, recording=r, on_coach_persisted=boom)
    await w1.on_coach_turn("A question.")
    assert calls == []
    # with DB, a raising callback must not propagate
    w2 = PersistenceWriter(session_id="s1", persistence=_StubPersistence(), recording=r,
                           on_coach_persisted=boom)
    await w2.on_coach_turn("A question.")
    assert calls == ["turn-0"]
