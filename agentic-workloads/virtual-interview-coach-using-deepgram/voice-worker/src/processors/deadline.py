"""DeadlineProcessor — the independent wall-clock interview deadline (Feature 007, T012).

Port of `pipeline.py:_start_deadline_timer` / `_deadline_watch`. This is the architectural fix for
the thrice-recurring "the interviewer runs over the chosen time" bug: a bound that depends on the
student speaking again is not a bound. The timer fires wrap-up ON ITS OWN after the chosen duration,
regardless of whether the student says anything else.

It runs OFF the response_gap clock (the interview is ending), so it never affects SC-001. It starts
when the pipeline starts (the coach's opening question anchors the clock — duration includes the dead
time while the student thinks, matching FR-221 duration gating), and on expiry it lets any in-flight
turn finish, then invokes the injected `on_deadline` coroutine (the pipeline wires this to speak the
score-free debrief + closing and push an EndFrame).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger("voice_worker")

# Coroutine invoked once when the deadline fires (after any in-flight bot turn finishes).
OnDeadline = Callable[[], Awaitable[None]]


class DeadlineProcessor(FrameProcessor):
    """Fire a wrap-up callback once, `budget_s` after the pipeline starts.

    Args:
        budget_s: the wall-clock interview budget in seconds. None or <= 0 disables the deadline
            (generic G1 session with no chosen duration) — the processor is then a pure pass-through.
        on_deadline: coroutine to invoke when the deadline fires (speak debrief/closing + EndFrame).
        settle_grace_s: after the budget elapses, if the bot is mid-utterance, wait up to this long
            for it to finish so the coach is never cut off mid-sentence.
        stats: optional SessionStats; this processor already tracks bot-speaking state, so it is
            the natural place to count TRUE barge-ins (an InterruptionFrame that lands while the
            coach is actually speaking — not every push-to-talk press).
    """

    def __init__(
        self,
        *,
        budget_s: float | None,
        on_deadline: OnDeadline,
        settle_grace_s: float = 12.0,
        stats=None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._budget_s = budget_s if (budget_s and budget_s > 0) else None
        self._on_deadline = on_deadline
        self._settle_grace_s = settle_grace_s
        self._stats = stats
        self._timer: asyncio.Task | None = None
        self._fired = False
        self._bot_speaking = False
        self._bot_idle = asyncio.Event()
        self._bot_idle.set()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame) and self._budget_s is not None and self._timer is None:
            self._timer = self.create_task(self._watch(self._budget_s))
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._bot_idle.clear()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._bot_idle.set()
        elif isinstance(frame, InterruptionFrame):
            # Count TRUE student barge-ins only: the wrap-up path injects its own InterruptionFrame
            # to cancel an in-flight coach reply, which must not inflate the metric.
            if (
                self._bot_speaking
                and self._stats is not None
                and not getattr(self._stats, "wrap_up_started", False)
            ):
                self._stats.barge_ins += 1
        elif isinstance(frame, EndFrame):
            await self._cancel_timer()

        await self.push_frame(frame, direction)

    async def _watch(self, budget_s: float) -> None:
        try:
            await asyncio.sleep(budget_s)
            if self._fired:
                return
            # Let an in-flight coach utterance finish so wrap-up doesn't truncate it.
            if self._bot_speaking:
                try:
                    await asyncio.wait_for(self._bot_idle.wait(), timeout=self._settle_grace_s)
                except asyncio.TimeoutError:
                    pass
            if self._fired:
                return
            self._fired = True
            log.info("interview duration deadline reached (%.0fs budget) — wrapping up", budget_s)
            await self._on_deadline()
        except asyncio.CancelledError:
            pass  # normal teardown / wrap-up via another path cancels us
        except Exception as exc:  # noqa: BLE001 - the deadline timer must never crash the pipeline
            log.warning("deadline timer error (%s)", type(exc).__name__)

    async def _cancel_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            await self.cancel_task(self._timer)
        self._timer = None

    async def cleanup(self) -> None:
        await self._cancel_timer()
        await super().cleanup()
