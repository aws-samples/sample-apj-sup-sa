"""Barge-in grace window (ISSUE: false barge-in cut off the coach reply).

Symptom (live session f95f79ba): the coach started its reply ("Thanks, that gives me a good
picture —") and was cancelled ~0.3s in, so the student only heard the first word. Root cause: when
Deepgram STT lagged (processing_time ~4s), a LATE transcript of the student's OWN, already-finished
self-introduction arrived AFTER the coach had started speaking. `MinWordsUserTurnStartStrategy`
counts transcribed words regardless of when the speech actually happened, saw >= min_words while the
bot was speaking, and raised an interruption — a FALSE barge-in triggered by stale audio. The same
fragmentation also split one answer into 3 "student turns", which is why the report showed 3
duplicate per-question feedback cards.

Fix: a thin subclass of MinWordsUserTurnStartStrategy that, for a short GRACE WINDOW right after the
bot starts speaking, refuses to treat transcripts as a barge-in. Residual/late transcripts of the
student's previous turn land inside this window and are ignored; a GENUINE barge-in (the student
keeps talking past the window) still interrupts normally. The window is wall-clock based
(time.monotonic), independent of the latency contract — it only affects the START decision while the
bot speaks, never the normal (bot-silent) turn-taking path (Constitution I unaffected: no LLM, no
change to the response_gap clock).
"""

from __future__ import annotations

import time

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)


class GraceWindowMinWordsUserTurnStartStrategy(MinWordsUserTurnStartStrategy):
    """MinWords barge-in, but suppressed for `grace_secs` after the bot starts speaking.

    Within the grace window, transcripts cannot start a (barge-in) user turn — they only reset
    aggregation — so a lagging transcript of the student's own finished turn cannot cancel the
    coach's reply. After the window elapses, behavior is identical to the base strategy: sustained
    real speech (>= min_words) still interrupts.
    """

    def __init__(self, *, min_words: int, grace_secs: float = 1.5, use_interim: bool = True, **kwargs):
        super().__init__(min_words=min_words, use_interim=use_interim, **kwargs)
        self._grace_secs = grace_secs
        self._bot_started_monotonic: float | None = None

    async def reset(self):
        await super().reset()
        self._bot_started_monotonic = None

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        # Stamp when the bot starts speaking so we can measure the grace window. Defer to the base
        # class for the actual _bot_speaking bookkeeping + all other frame handling.
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_started_monotonic = time.monotonic()
        return await super().process_frame(frame)

    async def _handle_transcription(
        self, frame: TranscriptionFrame | InterimTranscriptionFrame
    ) -> ProcessFrameResult:
        # Only intercept while the bot is speaking AND we are still inside the grace window.
        if self._bot_speaking and self._bot_started_monotonic is not None:
            elapsed = time.monotonic() - self._bot_started_monotonic
            if elapsed < self._grace_secs:
                logger.debug(
                    f"{self} suppressing barge-in inside grace window "
                    f"(elapsed={elapsed:.2f}s < {self._grace_secs:.2f}s, "
                    f"words={len(frame.text.split())}) — likely a stale/late transcript"
                )
                # Treat as no-turn: reset aggregation, do not start a turn / raise an interruption.
                await self.trigger_reset_aggregation()
                return ProcessFrameResult.CONTINUE
        return await super()._handle_transcription(frame)
