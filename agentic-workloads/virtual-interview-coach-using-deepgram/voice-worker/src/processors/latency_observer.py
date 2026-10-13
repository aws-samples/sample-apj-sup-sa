"""LatencyObserver — reconstruct the response_gap contract from Pipecat frames (Feature 007, T013).

The SC-001 gate metric (`specs/001-voice-interview-loop/contracts/metrics-contract.md`) is IMMUTABLE.
This processor measures it from the Pipecat frame stream and emits the SAME `LatencyRecord` /
`turn_latency` row + CloudWatch metric the hand loop did (reusing `metrics.MetricsSink` /
`persistence.LatencyRecord`), so `harness/aggregate.py` reads it unchanged.

Clock map (single monotonic clock in the worker, no cross-host skew):

    response_gap_ms   = first coach AUDIO frame  -  student end-of-speech
    stt_finalization  = TranscriptionFrame (final)  -  end-of-speech     [+ constant fallback]
    reply_ttft        = first LLMTextFrame  -  LLMContextFrame (reply requested)
    tts_first_audio   = first coach audio frame  -  first text handed to TTS
    orchestration     = max(0, gap - stt - ttft)   (residual; diagnostic)

Frame instants (verified pipecat 1.3.0 frame names — see pipecat-api-notes.md):
  - end-of-speech     : UserStoppedSpeakingFrame / VADUserStoppedSpeakingFrame
  - stt final         : TranscriptionFrame
  - reply requested   : LLMContextFrame (the aggregator's "kick the LLM" frame)
  - reply first token : first LLMTextFrame of the turn
  - tts requested     : first LLMTextFrame OR TTSSpeakFrame (whichever first reaches TTS)
  - first coach audio : first TTSAudioRawFrame after end-of-speech (== the gate end instant)

Lead-clause note: with the LeadClauseProcessor injecting a TTSSpeakFrame backchannel, the FIRST coach
audio is the backchannel — exactly what closes the gate gap (LLM off the critical path). The
substantive reply's first audio is tracked separately and reported as `substantive_reply_ms` so the
trade is never hidden (Constitution II), matching the hand loop and `aggregate.py`.

Placement: as far downstream as possible (right before/after transport.output) so a "coach audio
frame" is real outbound audio. It observes and forwards every frame unchanged.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ..persistence import LatencyRecord

log = logging.getLogger("voice_worker")

# Coroutine invoked once per completed coach turn with the finished LatencyRecord.
OnTurnLatency = Callable[[LatencyRecord], Awaitable[None]]


def _now_ms() -> float:
    return time.monotonic() * 1000.0


class LatencyObserver(FrameProcessor):
    """Measure response_gap + sub-components per coach turn and emit a LatencyRecord.

    Args:
        reply_provider: recorded on the LatencyRecord (CloudWatch dimension).
        stt_finalization_ms: the acoustic-offset constant added when a precise stt-final instant is
            not observable (matches the hand loop's honest accounting; metrics-contract.md).
        on_turn: coroutine invoked when a coach turn's first audio lands (one record per turn).
        is_substantive_audio: optional predicate to distinguish the lead-in backchannel audio from
            the substantive reply audio (used only to populate substantive_reply_ms). Default: the
            first audio after the first LLMTextFrame of the turn is "substantive".
    """

    def __init__(
        self,
        *,
        reply_provider: str,
        stt_finalization_ms: int,
        on_turn: OnTurnLatency,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._reply_provider = reply_provider
        self._stt_const = stt_finalization_ms
        self._on_turn = on_turn
        self._reset_turn()

    def _reset_turn(self) -> None:
        self._t_eos: float | None = None
        self._t_stt_final: float | None = None
        self._t_reply_requested: float | None = None
        self._t_reply_first_token: float | None = None
        self._t_tts_requested: float | None = None
        self._t_first_audio: float | None = None
        self._t_substantive_audio: float | None = None
        self._seen_llm_text = False
        self._emitted = False

    # --- upstream probe hooks ------------------------------------------------------------
    # The observer sits downstream of TTS so a "coach audio frame" is real outbound audio (the gate
    # end instant). But the LLM-request / first-token / tts-request DataFrames are consumed UPSTREAM
    # of it, so it never sees them. A LatencyProbe placed before the LLM calls these to feed the
    # sub-component instants. (The gate GAP — eos -> first audio — is measured here regardless.)

    # NB: the mark_* hooks do NOT gate on _t_eos. The probes that call them sit UPSTREAM of this
    # processor, so an LLMContextFrame can reach the pre-LLM probe before UserStoppedSpeakingFrame
    # has propagated downstream to set _t_eos here — gating on _t_eos would drop the instant and
    # leave reply_ttft at 0. Each mark records the first occurrence per turn; _reset_turn clears them.
    #
    # Honesty note (Constitution II): response_gap (the GATE metric — eos -> first coach audio) is
    # measured precisely at this processor. The sub-component BREAKDOWN (reply_ttft etc.) is
    # best-effort: Pipecat's smart-turn VAD fires multiple start/stop events per logical turn, so the
    # cross-position mark pairing can occasionally mis-attribute a sub-component across a turn
    # boundary. The breakdown is diagnostic, not gate-deciding; treat individual sub-component values
    # as indicative.

    def mark_reply_requested(self) -> None:
        if self._t_reply_requested is None:
            self._t_reply_requested = _now_ms()

    def mark_reply_first_token(self) -> None:
        now = _now_ms()
        if self._t_reply_first_token is None:
            self._t_reply_first_token = now
        if self._t_tts_requested is None:
            self._t_tts_requested = now
        self._seen_llm_text = True

    def mark_tts_requested(self) -> None:
        if self._t_tts_requested is None:
            self._t_tts_requested = _now_ms()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Only the frames that ACTUALLY reach this position (downstream of TTS) are handled here:
        # the speaking SystemFrames (broadcast everywhere), the transcription, and the outbound audio.
        # The LLM-request / first-token / lead-in instants are DataFrames consumed upstream of TTS, so
        # they never arrive here — they are fed via the LatencyProbe mark_* hooks instead.
        if isinstance(frame, UserStartedSpeakingFrame):
            # Arm a fresh measurement window for a NEW turn — but only when the prior turn was already
            # emitted, or nothing is in flight yet. Pipecat's smart-turn VAD fires multiple
            # UserStartedSpeakingFrames per logical turn; resetting on a mid-turn re-trigger would clear
            # an in-flight measurement (e.g. reply_requested already marked) and zero out reply_ttft.
            if self._emitted or self._t_eos is None:
                self._reset_turn()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            # End-of-speech: the gate clock origin.
            if self._t_eos is None:
                self._t_eos = _now_ms()
        elif isinstance(frame, TranscriptionFrame):
            if self._t_eos is not None and self._t_stt_final is None:
                self._t_stt_final = _now_ms()
        elif isinstance(frame, (TTSAudioRawFrame, OutputAudioRawFrame)):
            await self._on_audio_frame()

        await self.push_frame(frame, direction)

    async def _on_audio_frame(self) -> None:
        if self._t_eos is None:
            return  # audio outside a measured turn (e.g. the opening question) — ignore
        now = _now_ms()
        if self._t_first_audio is None:
            self._t_first_audio = now
            await self._emit_record()
        # The first audio AFTER the LLM started producing text is the substantive reply's first audio.
        if self._seen_llm_text and self._t_substantive_audio is None:
            self._t_substantive_audio = now

    async def _emit_record(self) -> None:
        if self._emitted or self._t_eos is None or self._t_first_audio is None:
            return
        self._emitted = True
        gap = int(round(self._t_first_audio - self._t_eos))
        # stt finalization: prefer the observed instant; else the honest constant (metrics-contract.md).
        if self._t_stt_final is not None:
            stt = int(round(self._t_stt_final - self._t_eos))
        else:
            stt = self._stt_const
        if self._t_reply_requested is not None and self._t_reply_first_token is not None:
            ttft = int(round(self._t_reply_first_token - self._t_reply_requested))
        else:
            ttft = 0
        if self._t_tts_requested is not None:
            tts = int(round(self._t_first_audio - self._t_tts_requested))
        else:
            tts = 0
        orchestration = max(0, gap - stt - ttft)
        rec = LatencyRecord(
            response_gap_ms=max(0, gap),
            stt_finalization_ms=max(0, stt),
            reply_ttft_ms=max(0, ttft),
            tts_first_audio_ms=max(0, tts),
            orchestration_ms=orchestration,
            reply_provider=self._reply_provider,
        )
        try:
            await self._on_turn(rec)
        except Exception as exc:  # noqa: BLE001 - measurement must never crash the pipeline
            log.warning("latency on_turn handler failed (%s)", type(exc).__name__)

    def substantive_reply_ms(self) -> int | None:
        """Time from end-of-speech to the substantive reply's first audio (lead-clause trade)."""
        if self._t_eos is None or self._t_substantive_audio is None:
            return None
        return int(round(self._t_substantive_audio - self._t_eos))


class LatencyProbe(FrameProcessor):
    """A tap that feeds the LatencyObserver the sub-component instants it cannot see from its position
    downstream of TTS. Two instances are used (each only sees the frames passing its position):
      - pre-LLM: LLMContextFrame (reply requested) + the lead-in TTSSpeakFrame (tts requested).
      - post-LLM (before TTS): the first LLMTextFrame (reply first token) before TTS consumes it.
    Forwards every frame untouched; the gate GAP is still measured by the observer at the output."""

    def __init__(self, observer: LatencyObserver, **kwargs) -> None:
        super().__init__(**kwargs)
        # NB: FrameProcessor.setup() assigns self._observer (the pipeline's WorkerObserver), so this
        # MUST use a distinct name or it gets clobbered — hence _latency_observer.
        self._latency_observer = observer

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            self._latency_observer.mark_reply_requested()
        elif isinstance(frame, LLMTextFrame):
            self._latency_observer.mark_reply_first_token()
        elif isinstance(frame, TTSSpeakFrame):
            self._latency_observer.mark_tts_requested()
        await self.push_frame(frame, direction)
