"""InterviewDirector — restores the bounded, structured interview the old loop had (Feature 007).

The Pipecat migration dropped the FunnelPlanner question/competency walk and the question-count
bounds, so the LLM free-ran the conversation with a static prompt and never wrapped up (live-test
Issue 2). This processor restores that management layer, ported from
`pipeline.py:on_student_turn` (lines 462-516) + `attach_session_plan` (201-243).

Placement: BETWEEN the user aggregator and the LLM (the pre-LLM position, alongside the pre-LLM
latency probe). Each downstream `LLMContextFrame` means "a student turn just completed; the LLM is
about to generate the next coach turn." On each one the director:

  1. reads the just-finalized student text (last `user` message in the shared LLMContext),
  2. advances `FunnelPlanner.next_turn(student_text)` -> a `TurnPlan`,
  3. stashes the TurnPlan in the SHARED `planner_holder` dict so the reply enricher reads
     `plan.intent` -> `ctx.current_archetype` and `questions_remaining` -> `ctx.questions_remaining`
     (the enricher runs later, inside the LLM adapter's `_process_context`),
  4. counts main questions + coach turns,
  5. if the plan is exhausted OR a bound (main-question / total-coach-turn) is hit: SUPPRESSES the
     `LLMContextFrame` (so the LLM never generates another question) and triggers the pipeline's
     wrap-up; otherwise forwards the frame unchanged.

The wall-clock deadline is a separate, independent backstop (DeadlineProcessor); this is the
question/competency bound. Generic sessions (no plan) get a pass-through director (no bounds).
Everything here is off the response_gap clock (it runs between turns, before the LLM generates).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ..blueprint import BlueprintQueue, FunnelPlanner
from ..config import Config

log = logging.getLogger("voice_worker")

# Coroutine the director calls once when the interview should wrap up (speak debrief/closing + End).
OnWrapUp = Callable[[], Awaitable[None]]


class InterviewDirector(FrameProcessor):
    """Drive the FunnelPlanner per student turn, bound the interview by question count, and trigger
    wrap-up. Pass-through when there is no personalized plan.

    Args:
        session_plan: the minimized prep plan (SessionPlan) or None for a generic session.
        config: worker config (follow-up budgets, max questions).
        planner_holder: a shared mutable dict; the director writes {"plan": TurnPlan,
            "questions_remaining": int} and the reply enricher reads it.
        on_wrap_up: coroutine invoked once when a question/turn bound is hit (speak debrief + End).
    """

    def __init__(
        self,
        *,
        session_plan,
        config: Config,
        planner_holder: dict,
        on_wrap_up: OnWrapUp,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._holder = planner_holder
        self._on_wrap_up = on_wrap_up
        self._planner: FunnelPlanner | None = None
        self._max_main_questions = config.max_main_questions
        self._max_coach_turns: int | None = None
        self._main_questions_asked = 0
        self._coach_turns_asked = 0
        self._wrapped_up = False

        if session_plan is not None and getattr(session_plan, "plan_rows", None):
            # Build the resident queue + planner (ported from pipeline.py:208-235). In-memory only.
            queue = BlueprintQueue.from_plan(
                session_plan.plan_rows, session_plan.opening_archetype_id
            )
            fpq = config.followups_per_question
            self._planner = FunnelPlanner(
                queue,
                session_plan.difficulty_profile,
                followup_ceiling=config.followup_ceiling or None,
                followups_per_question=fpq,
            )
            # The composed plan length IS the question budget (the backend sized it to the chosen
            # duration). max_coach_turns bounds openers + follow-ups so the duration is honored even if
            # the funnel would keep probing.
            n = len(session_plan.plan_rows) or config.max_main_questions
            self._max_main_questions = n
            self._max_coach_turns = n * (1 + fpq)

    @property
    def active(self) -> bool:
        """True when a personalized plan is driving the interview (else pure pass-through)."""
        return self._planner is not None

    @staticmethod
    def _last_user_text(context) -> str:
        """The just-finalized student utterance = the last `user` message in the shared context."""
        text = ""
        for m in context.get_messages():
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                text = " ".join(p for p in parts if p).strip() or text
        return text

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        # Only intercept the downstream context frame that kicks the LLM. Everything else passes.
        if (
            not isinstance(frame, LLMContextFrame)
            or direction != FrameDirection.DOWNSTREAM
            or self._planner is None
            or self._wrapped_up
        ):
            await self.push_frame(frame, direction)
            return

        student_text = self._last_user_text(frame.context)
        plan = self._planner.next_turn(student_text)
        # Stash for the enricher (runs later in the LLM adapter). current_archetype grounds the prompt;
        # questions_remaining lets the coach pace + answer "how many left?" honestly.
        if plan.intent is not None and not plan.is_followup:
            self._main_questions_asked += 1
        if plan.intent is not None:
            self._coach_turns_asked += 1
        questions_remaining = max(0, self._max_main_questions - self._main_questions_asked)
        self._holder["plan"] = plan
        self._holder["questions_remaining"] = questions_remaining

        main_budget_reached = self._main_questions_asked > self._max_main_questions
        turn_budget_reached = (
            self._max_coach_turns is not None and self._coach_turns_asked > self._max_coach_turns
        )
        if plan.exhausted or main_budget_reached or turn_budget_reached:
            # Bound hit: do NOT forward this context frame (so the LLM never generates another
            # question), and wrap up. Set the flag SYNCHRONOUSLY so every subsequent context frame is
            # also suppressed here, and spawn wrap-up as a background task — wrap-up's first action is
            # to mute STT (stop new turns), but the debrief generation is slow, so we must NOT block
            # this frame handler (blocking it would let in-flight turns reach the LLM before the mute
            # lands). create_task is provided by FrameProcessor and runs on the pipeline's task pool.
            self._wrapped_up = True
            log.info(
                "session wrap-up (director): exhausted=%s main=%d/%d coach=%d/%s",
                plan.exhausted,
                self._main_questions_asked,
                self._max_main_questions,
                self._coach_turns_asked,
                self._max_coach_turns if self._max_coach_turns is not None else "-",
            )
            self.create_task(self._run_wrap_up())
            return

        await self.push_frame(frame, direction)

    async def _run_wrap_up(self) -> None:
        try:
            await self._on_wrap_up()
        except Exception as exc:  # noqa: BLE001 - wrap-up failure must not crash the pipeline
            log.warning("director wrap-up failed (%s)", type(exc).__name__)
