"""PersistenceWriter — incremental per-turn persistence + recording flush (Feature 007).

Restores the FR-003 contract the old VoiceLoop._persist_turn provided: write each conversation_turn
to RDS as it completes (transcript + turn_count), get a turn_id, and hand that turn_id to the
RecordingProcessor so the buffered PCM uploads to S3 and the buffer drains. Without this, the Pipecat
pipeline produced NO transcript rows (G3 scoring had no input) and NO audio (G6 broken), and the
per-turn audio buffers grew unbounded.

Frame visibility makes a single processor position impossible (Pipecat consumes frames at the stage
that handles them):
  - the student's `TranscriptionFrame` is consumed by the user aggregator, so it is only visible
    BEFORE that aggregator -> a `StudentTurnTap` placed between STT and the user aggregator feeds it;
  - the coach's `LLMTextFrame`s are consumed by TTS, so they are only visible BETWEEN the LLM and TTS
    -> a `CoachTurnTap` placed there feeds it (accumulating text between the LLM full-response
    start/end frames).

Both taps forward every frame untouched and call into the SHARED PersistenceWriter, which owns the
turn counter, the DB writes, and the recording-flush hand-off. All writes are off the response_gap
clock (they happen as a turn completes, after first audio is already flowing). Persistence failures
never crash the pipeline (degrade, log only — Constitution II / III: counts/ids only, never raw PII).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger("voice_worker")


class PersistenceWriter:
    """Owns per-turn persistence + the recording-flush hand-off (shared by the two taps).

    Args:
        session_id: the session whose turns are written.
        persistence: the Persistence instance (None disables DB writes — recording also no-ops then,
            since flush_turn needs a turn_id).
        recording: the RecordingProcessor whose per-turn buffers are flushed once a turn_id exists.
        planner_holder: the SHARED dict the InterviewDirector writes ({"plan": TurnPlan, ...}); a
            PLANNED coach turn is persisted with that plan's structural facts (archetype_id,
            is_followup, targeted_star_element — FR-212a / SC-005), exactly as the old
            VoiceLoop._persist_turn did. None / empty -> all NULL (generic G1 parity).
        on_coach_persisted: awaited with the turn_id after a coach turn row lands in RDS. The
            pipeline uses it to attach the measured LatencyRecord to its coach turn (the
            turn_latency FK row — metrics-contract.md).
    """

    def __init__(
        self,
        *,
        session_id: str,
        persistence,
        recording,
        planner_holder: dict | None = None,
        on_coach_persisted: Callable[[str], Awaitable[None]] | None = None,
        stats=None,
    ) -> None:
        self._session_id = session_id
        self._p = persistence
        self._recording = recording
        self._planner_holder = planner_holder
        self._on_coach_persisted = on_coach_persisted
        self._stats = stats  # SessionStats: per-session turn counters (evidence loop)
        self._turn_index = 0

    async def on_student_turn(self, transcript: str) -> None:
        if not transcript.strip():
            return
        await self._persist_and_flush("student", transcript)

    async def on_coach_turn(self, transcript: str, planned: bool = True) -> None:
        """Persist a coach turn. `planned=False` for turns outside the archetype walk (the opening
        question, the closing/debrief) so they never carry the LAST planned turn's structural facts
        (parity with the old _emit_coach_turn, which persisted those with plan=None)."""
        if not transcript.strip():
            return
        await self._persist_and_flush("coach", transcript, planned=planned)

    def _structural_facts(self, speaker: str, planned: bool):
        """The (archetype_id, is_followup, targeted_star_element) for this turn — set only on a
        PLANNED coach turn with an active archetype; student/generic/unplanned turns carry NULLs."""
        if speaker != "coach" or not planned or not self._planner_holder:
            return None, None, None
        plan = self._planner_holder.get("plan")
        if plan is None or getattr(plan, "intent", None) is None:
            return None, None, None
        return plan.intent.archetype_id, plan.is_followup, plan.targeted_star_element

    async def _persist_and_flush(self, speaker: str, transcript: str, planned: bool = True) -> None:
        idx = self._turn_index
        self._turn_index += 1
        if self._stats is not None:
            if speaker == "student":
                self._stats.student_turns += 1
            else:
                self._stats.coach_turns += 1
        turn_id: str | None = None
        archetype_id, is_followup, targeted_star_element = self._structural_facts(speaker, planned)
        if self._p is not None:
            try:
                now = datetime.now(timezone.utc)
                turn_id = await self._p.append_turn(
                    self._session_id, idx, speaker, transcript, started_at=now, ended_at=now,
                    archetype_id=archetype_id, is_followup=is_followup,
                    targeted_star_element=targeted_star_element,
                )
            except Exception as exc:  # noqa: BLE001 - persistence failure must not crash the pipeline
                log.warning(
                    "session %s append_turn failed (%s); turn not persisted",
                    self._session_id,
                    type(exc).__name__,
                )
        # Hand the turn_id to the recorder so the buffered PCM uploads + the buffer drains. flush_turn
        # is a no-op when recording is off or there is no turn_id (so the buffer still drains via take()
        # only when enabled — see RecordingProcessor). Even with no DB, drain the buffer to bound memory.
        if self._recording is not None:
            self._recording.flush_turn(speaker, turn_id)
        # Tell the pipeline a coach turn row exists so the pending LatencyRecord can FK onto it.
        # PLANNED turns only: the opening/closing lines are unmeasured by design, and the wrap-up
        # closing follows a suppressed student turn whose stale gap must not attach to it.
        if speaker == "coach" and planned and turn_id and self._on_coach_persisted is not None:
            try:
                await self._on_coach_persisted(turn_id)
            except Exception as exc:  # noqa: BLE001 - latency attach must not crash persistence
                log.warning("on_coach_persisted failed (%s)", type(exc).__name__)


class StudentTurnTap(FrameProcessor):
    """Tap placed BETWEEN STT and the user aggregator: persists the student turn on each final
    TranscriptionFrame (the only place it is visible). Forwards every frame untouched."""

    def __init__(self, writer: PersistenceWriter, **kwargs) -> None:
        super().__init__(**kwargs)
        # NB: distinct name — FrameProcessor.setup() assigns self._observer; avoid any clash.
        self._writer = writer

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
            await self._writer.on_student_turn(frame.text)
        await self.push_frame(frame, direction)


class CoachTurnTap(FrameProcessor):
    """Tap placed BETWEEN the LLM and TTS: accumulates the coach reply text from LLMTextFrames between
    the LLM full-response start/end frames and persists the coach turn on end. Forwards every frame."""

    def __init__(self, writer: PersistenceWriter, **kwargs) -> None:
        super().__init__(**kwargs)
        self._writer = writer
        self._parts: list[str] = []
        self._active = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            if self._active and self._parts:
                # A new response started while a prior one was still accumulating — the prior reply's
                # End frame was dropped (e.g. barge-in clears the cancellable queue). That partial
                # coach turn is intentionally NOT persisted (parity with the old loop, which also
                # dropped interrupted-but-unpersisted coach turns); log it so the drop is observable.
                log.info("coach turn interrupted before completion; partial reply not persisted")
            self._parts = []
            self._active = True
        elif isinstance(frame, LLMTextFrame):
            if self._active and frame.text:
                self._parts.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._parts).strip()
            self._parts = []
            self._active = False
            if text:
                await self._writer.on_coach_turn(text)
        await self.push_frame(frame, direction)
