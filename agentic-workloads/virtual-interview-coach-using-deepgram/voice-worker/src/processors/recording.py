"""RecordingProcessor — consent-gated per-turn audio capture as a Pipecat tap (Feature 007, T014).

Port of the F006/G6 recording taps in `pipeline.py`. It accumulates each turn's inbound student PCM
and outbound coach PCM into the existing `TurnAudioBuffer` and, AFTER the turn, schedules the async S3
SSE-KMS upload via the existing `upload_turn_audio` — reusing `audio_record.py` UNCHANGED (FR-016).

SC-003 (recording adds no live latency): the tap is a cheap memory copy and the upload is a separate
task scheduled after the turn's first audio is already flowing, so it is OFF the response_gap clock.
The processor merely OBSERVES audio frames and forwards them untouched. The actual buffer->upload
hand-off is driven by `flush_turn(speaker, turn_id)`, which the pipeline calls when a turn is persisted
(it has the turn_id FK and is already off the gap clock).

Consent gate (FR-001/FR-012): if `record_audio` is False or no bucket is configured, the processor
allocates no buffers and schedules no uploads — a pure pass-through (consent OFF -> zero audio).

Privacy (FR-014): logs only counts/ids (delegated to audio_record); never raw PCM, never the uri body.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ..audio_record import TurnAudioBuffer, upload_turn_audio
from ..config import Config

log = logging.getLogger("voice_worker")

# Coroutine invoked after a successful upload to link the object uri to the turn row.
OnAudioUri = Callable[[str, str], Awaitable[None]]  # (turn_id, audio_uri) -> None


class RecordingProcessor(FrameProcessor):
    """Tap inbound + outbound PCM into per-turn buffers; async-upload off the gap clock.

    Recording is gated on THREE things (G6 / FR-001 / FR-002, all required):
      - per-session CONSENT (`consent`, from voice_session.consent_store_materials, decided once per
        session) — no consent => no audio, ever;
      - a configured S3 bucket (`config.audio_bucket`);
      - the operational kill-switch (`config.record_audio`).
    Consent is the load-bearing one: without it, even with a bucket + kill-switch on, NOTHING is
    recorded. When disabled, no buffers are allocated and the processor is a pure pass-through.

    Args:
        config: worker config (record_audio kill-switch, audio_bucket, kms key, region).
        session_id: the session whose turns are recorded.
        consent: the per-session consent decision (default False — fail-closed: no consent => no audio).
        on_audio_uri: coroutine to persist the (turn_id, uri) link after upload (e.g.
            persistence.set_turn_audio_uri). May be None to skip linking.
    """

    def __init__(
        self,
        *,
        config: Config,
        session_id: str,
        consent: bool = False,
        on_audio_uri: OnAudioUri | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._session_id = session_id
        self._on_audio_uri = on_audio_uri
        # FR-002: the consent decision in force when the session begins governs the whole session.
        self._enabled = bool(consent and config.record_audio and config.audio_bucket)
        self._student_buf = TurnAudioBuffer() if self._enabled else None
        self._coach_buf = TurnAudioBuffer() if self._enabled else None
        self._tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self._enabled:
            # Cheap memory copy only — off the gap clock (SC-003).
            if isinstance(frame, InputAudioRawFrame):
                self._student_buf.append(frame.audio)
            elif isinstance(frame, (TTSAudioRawFrame, OutputAudioRawFrame)):
                self._coach_buf.append(frame.audio)
        await self.push_frame(frame, direction)

    def flush_turn(self, speaker: str, turn_id: str | None) -> None:
        """End a turn: ALWAYS drain its PCM buffer (so memory stays bounded per turn), and schedule the
        async S3 upload when there is a turn_id to link it to. Called AFTER the turn (off the gap clock).
        No-op when recording is disabled (no buffers exist)."""
        if not self._enabled:
            return
        buf = self._student_buf if speaker == "student" else self._coach_buf
        if buf is None or len(buf) == 0:
            return
        pcm = buf.take()  # drain regardless — bounds memory even if the turn was not persisted
        if not turn_id:
            return  # nothing to link the object to; drop the drained audio for this turn
        task = self.create_task(self._upload_and_link(turn_id, pcm))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _upload_and_link(self, turn_id: str, pcm: bytes) -> None:
        try:
            uri = await upload_turn_audio(self._config, self._session_id, turn_id, pcm)
            if uri and self._on_audio_uri is not None:
                await self._on_audio_uri(turn_id, uri)
        except Exception as exc:  # noqa: BLE001 - recording must never affect the session
            log.warning(
                "session %s turn-audio task failed (%s)", self._session_id, type(exc).__name__
            )

    async def cleanup(self) -> None:
        for task in list(self._tasks):
            if not task.done():
                await self.cancel_task(task)
        self._tasks.clear()
        await super().cleanup()
