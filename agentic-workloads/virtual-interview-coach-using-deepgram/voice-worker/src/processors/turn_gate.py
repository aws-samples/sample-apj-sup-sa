"""TurnGateProcessor — push-to-talk vs hands-free turn boundaries (Feature 007, T015).

Port of `pipeline.py:on_control` turn-taking semantics onto Pipecat. In Pipecat, the end of a student
turn is signalled by `UserStoppedSpeakingFrame` / `VADUserStoppedSpeakingFrame` (VAD-driven), which the
downstream context-aggregator turns into the LLM kick. This processor sits just AFTER the transport
input (before the aggregator) and gates those frames by mode:

  - "auto" (hands-free, default): pass VAD start/stop frames through unchanged — the coach takes the
    turn when the student pauses (the patience is tuned via the STT/VAD settings, FR-004).
  - "ptt" (push-to-talk): SUPPRESS VAD stop frames so a natural mid-thought pause never ends the turn;
    the turn ends ONLY when the SPA sends a `turn_end` control message (button release). On `turn_start`
    the user is taking the floor — emit an InterruptionFrame so an in-flight coach reply is cancelled
    (barge-in), matching the original `_bargein_if_speaking`.

Mode and button events arrive on the WebRTC data channel; the transport surfaces them as app-message
frames. The server wires those messages to `on_control(msg)` here (kept as a method so the data-channel
handler is transport-agnostic and unit-testable without a live channel).
"""

from __future__ import annotations

import logging

from pipecat.frames.frames import (
    Frame,
    InputTransportMessageFrame,
    InterruptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger("voice_worker")

MODE_AUTO = "auto"
MODE_PTT = "ptt"

_VAD_STOP = (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)
_VAD_START = (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)


class TurnGateProcessor(FrameProcessor):
    """Gate student-turn-end frames by turn-taking mode; handle button + mode control messages.

    Args:
        mode: initial turn-taking mode ("auto" or "ptt").
    """

    def __init__(self, *, mode: str = MODE_AUTO, **kwargs) -> None:
        super().__init__(**kwargs)
        self._mode = mode if mode in (MODE_AUTO, MODE_PTT) else MODE_AUTO

    @property
    def mode(self) -> str:
        return self._mode

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Data-channel control messages from the SPA arrive as InputTransportMessageFrame. Handle
        # them here (mode toggle / push-to-talk button) and do not forward them downstream.
        if isinstance(frame, InputTransportMessageFrame):
            msg = frame.message
            if isinstance(msg, dict):
                await self.on_control(msg)
            return

        # In push-to-talk, the VAD's automatic end-of-turn is ignored — only the button ends a turn.
        # Suppress the stop frame so the aggregator does not finalize the turn on a natural pause.
        if self._mode == MODE_PTT and isinstance(frame, _VAD_STOP):
            return
        # VAD start frames in ptt are harmless (they don't finalize a turn), but suppress them too so
        # the downstream sees a clean button-only turn lifecycle.
        if self._mode == MODE_PTT and isinstance(frame, _VAD_START):
            return

        await self.push_frame(frame, direction)

    async def on_control(self, msg: dict) -> None:
        """Handle a data-channel control message from the SPA.

        {"type":"mode","value":"auto"|"ptt"} — switch turn-taking mode live.
        {"type":"turn_start"}                — button pressed: take the floor (barge in if coach speaking).
        {"type":"turn_end"}                  — button released: end the student's turn now.
        """
        mtype = msg.get("type")
        if mtype == "mode":
            value = msg.get("value")
            if value in (MODE_AUTO, MODE_PTT):
                self._mode = value
                log.info("turn-taking mode set to %s", value)
        elif mtype == "turn_start":
            # User is taking the floor — cancel any in-flight coach reply (barge-in).
            await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        elif mtype == "turn_end":
            # Button released: synthesize the end-of-turn the aggregator needs to kick the LLM.
            await self.push_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
