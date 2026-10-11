"""Stop the agent talking when the caller starts talking.

WHY THIS FILE EXISTS AT ALL. Barge-in was assumed to work and did not. The VAD is
tuned for it - `start_secs` is 0.2 specifically so "barge-in feels immediate" - and
on a live 45-turn call the VAD fired 45 times, so detection was never the problem.
Tracing it through pipecat 1.7:

    VADProcessor        emits VADUserStartedSpeakingFrame, UserSpeakingFrame
    base_output         handles InterruptionFrame -> sender.handle_interruptions()
    base_input          emits no interruption at all
    nothing anywhere    emits InterruptionFrame in this pipeline

So the output transport was listening for a signal that nobody sent. There is no
`allow_interruptions` flag in 1.7 to switch on - the wiring is simply absent, and
`InterruptionFrame`'s own docstring sanctions the fix: "It can also be pushed by
any processor."

WHY NOT INTERRUPT THE INSTANT THE VAD FIRES, which is the obvious implementation:
this is a phone bridge, and the agent's own audio can come back down the inbound
leg. `scripts/73-echo-test.sh` exists because one-way and echoed audio have both
happened here. An interruption fires BEFORE any transcription, so the echo-
rejection that protects the transcript cannot protect this - the agent would hear
itself, stop mid-sentence, and the caller would get a machine that cannot finish a
question. That is a far worse failure than the dead air it is meant to fix, and it
would appear only on a real call.

So an interruption needs SUSTAINED speech, not a blip. A returning echo of our own
speech is loud but it stops the moment we stop; a caller who means to interrupt
keeps going. Waiting BARGE_IN_MIN_SPEECH_SECS costs a fraction of a second of
overlap and removes the whole class of single-frame false positives.

AND ONLY WHILE THE AGENT IS ACTUALLY SPEAKING. An interruption pushed into an idle
pipeline is close to a no-op, but "close to" is not a guarantee: the caller
finishing a long answer while the next question is being synthesised should not
cancel that question before it is heard. TTS start/stop frames are tracked to tell
the difference. If those frames never arrive - a pipeline shape where they do not
pass through here - the gate opens rather than blocking barge-in entirely, because
the measured fault is that interruption never happens.

KILL SWITCH. BARGE_IN_ENABLED=0 disables this without a code change, because the
echo behaviour of a real carrier trunk is not yet known and the failure mode is
audible to a customer mid-question.
"""
from __future__ import annotations

import logging
import os
import time

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger("hvi.media.bargein")

ENABLED = os.environ.get("BARGE_IN_ENABLED", "1").lower() not in ("0", "false", "no")

# How long the caller must keep speaking before the agent stops. Long enough that
# a returning echo of our own audio, which dies with our own speech, cannot reach
# it; short enough that a caller who interrupts is not talked over.
MIN_SPEECH_SECS = float(os.environ.get("BARGE_IN_MIN_SPEECH_SECS", "0.4"))


def should_interrupt(*, speech_started_at: float | None, now: float,
                     already_fired: bool, bot_speaking: bool,
                     seen_tts_frames: bool,
                     min_speech_secs: float = MIN_SPEECH_SECS) -> bool:
    """The whole decision, as a function of state, so it can be tested directly.

    Kept separate from frame handling because the interesting part is the policy,
    and a policy buried in an async frame handler can only be tested by
    reconstructing a pipeline.
    """
    if not ENABLED or already_fired or speech_started_at is None:
        return False
    if now - speech_started_at < min_speech_secs:
        return False
    # Never seeing TTS frames means this processor cannot observe the agent's
    # speech, not that the agent is silent. Failing open is right: the defect
    # being fixed is that interruption never fired.
    return bot_speaking or not seen_tts_frames


class BargeInProcessor(FrameProcessor):
    """Push InterruptionFrame when the caller talks over the agent."""

    def __init__(self, min_speech_secs: float = MIN_SPEECH_SECS, **kwargs):
        super().__init__(**kwargs)
        self._min_speech_secs = min_speech_secs
        self._speech_started_at: float | None = None
        self._already_fired = False
        self._bot_speaking = False
        self._seen_tts_frames = False
        self._interruptions = 0

    @property
    def interruptions(self) -> int:
        """How many times the caller has cut in. Read by the call summary."""
        return self._interruptions

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSStartedFrame):
            self._seen_tts_frames = True
            self._bot_speaking = True
        elif isinstance(frame, (TTSStoppedFrame, BotStoppedSpeakingFrame)):
            self._seen_tts_frames = True
            self._bot_speaking = False
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._speech_started_at = time.monotonic()
            self._already_fired = False
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speech_started_at = None
            self._already_fired = False

        # The original frame always continues on its way; this processor observes
        # and adds, and must never swallow VAD or audio frames.
        await self.push_frame(frame, direction)

        # Evaluated on EVERY frame rather than only on speech frames, because
        # audio frames are the one thing guaranteed to keep arriving while the
        # caller talks - so the elapsed-time check cannot stall waiting for a
        # particular VAD frame to be forwarded by an intermediate service.
        if should_interrupt(speech_started_at=self._speech_started_at,
                            now=time.monotonic(),
                            already_fired=self._already_fired,
                            bot_speaking=self._bot_speaking,
                            seen_tts_frames=self._seen_tts_frames,
                            min_speech_secs=self._min_speech_secs):
            self._already_fired = True
            self._interruptions += 1
            self._bot_speaking = False
            log.info("barge-in: caller has been speaking for %.2fs while the "
                     "agent was talking - interrupting (%d this call)",
                     time.monotonic() - (self._speech_started_at or 0),
                     self._interruptions)
            # Broadcast, not push: the output transport downstream stops the
            # audio, and the services upstream stop producing more of it. This is
            # the same shape VADProcessor uses for its own frames.
            await self.broadcast_frame_instance(InterruptionFrame())
