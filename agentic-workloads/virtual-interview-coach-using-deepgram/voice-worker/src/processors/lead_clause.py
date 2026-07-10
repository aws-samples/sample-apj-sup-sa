"""LeadClauseProcessor — the SC-001 latency lever as a Pipecat FrameProcessor (Feature 007, T011).

Port of `pipeline.py:_run_lead_turn`. The gate-passing trick (gate-decision.md): the instant the
student stops, the coach speaks a short bridge phrase via TTS while the reply LLM generates
CONCURRENTLY behind it — so the LLM's time-to-first-token is OFF the response_gap critical path.
The gate gap becomes `stt_finalization + lead-in TTS first-audio`.

Placement in the pipeline: BETWEEN the user context-aggregator and the LLM service. When the
aggregator finishes a user turn it emits an `LLMContextFrame` (verified: llm_response_universal.py
~L908 "emits LLMContextFrame, which kicks LLM"). This processor:

  - strategy "processor" (lead-clause ON, the default if it wins the A/B): on each LLMContextFrame,
    push a `TTSSpeakFrame(bridge)` FIRST (it flows downstream THROUGH the LLM service untouched
    — TTSSpeakFrame is a DataFrame the LLM ignores — straight to TTS, producing first-audio fast),
    THEN forward the LLMContextFrame so the LLM streams the substantive reply behind the bridge.
  - strategy "native" (lead-clause OFF): pure pass-through; the LLM is on the critical path (the A/B
    comparison arm).

Per-turn TTS socket rotation (open_spare/rotate) is intentionally NOT here: under Pipecat the
DeepgramTTSService owns its socket. This processor is purely the "speak-a-lead-in-first" timing lever.
"""

from __future__ import annotations

import logging

from pipecat.frames.frames import Frame, LLMContextFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger("voice_worker")

# Bridge lead-ins, cycled within content-aware buckets (live-use feedback: a terse "Right." both
# finished ~1s before the LLM's reply audio arrived — dead air that read as a disconnection — and
# was content-blind, e.g. "That's great." after a clarification request). Three design rules:
#   1. LENGTH covers the LLM TTFT window (~5-8 words ~= 1.5-2.5s of speech), so the substantive
#      reply's audio is typically ready right as the bridge ends — one continuous utterance.
#   2. SELECTION is a cheap deterministic heuristic over the student's just-finalized text (string
#      checks only — nothing on the gap clock): a repeat/clarify request gets "No problem —", a
#      question gets "Good question —", a very short answer gets a neutral segue (praising a
#      two-word answer with "That's great." was the live tell), a full answer gets a real
#      acknowledgement.
#   3. INTONATION: every bridge ends with an em-dash, not a period, so TTS speaks a continuing
#      contour instead of a sentence-final drop (the "full stop then silence" disconnect).
ACK_LEAD_INS: tuple[str, ...] = (
    "Thanks, that gives me a good picture —",
    "Okay, I can see how you approached that —",
    "Right, thanks for walking me through that —",
    "Mm-hmm, that's helpful context —",
    "I see, thanks for laying that out —",
)
SHORT_LEAD_INS: tuple[str, ...] = (
    "Okay, let me ask you this —",
    "Alright, let's dig into that a little —",
    "Got it, so building on that —",
)
QUESTION_LEAD_INS: tuple[str, ...] = (
    "Good question —",
    "Sure, happy to clarify —",
)
REPEAT_LEAD_INS: tuple[str, ...] = (
    "No problem, let me say that again —",
    "Of course —",
)

# The full set (compat: tests/persona only need "is this one of ours" / variety).
LEAD_INS: tuple[str, ...] = ACK_LEAD_INS + SHORT_LEAD_INS + QUESTION_LEAD_INS + REPEAT_LEAD_INS

_REPEAT_MARKERS = (
    "repeat", "say that again", "say it again", "pardon", "didn't catch", "did not catch",
    "come again", "one more time",
)

STRATEGY_PROCESSOR = "processor"
STRATEGY_NATIVE = "native"


def pick_lead_in(student_text: str, turn_index: int) -> str:
    """Choose a bridge bucket from the just-finalized student text (deterministic, string checks
    only — runs in microseconds, nothing touches the gap clock), then cycle within the bucket."""
    text = (student_text or "").strip().lower()
    if any(m in text for m in _REPEAT_MARKERS):
        bucket = REPEAT_LEAD_INS
    elif text.endswith("?"):
        bucket = QUESTION_LEAD_INS
    elif len(text.split()) < 8:
        bucket = SHORT_LEAD_INS
    else:
        bucket = ACK_LEAD_INS
    return bucket[turn_index % len(bucket)]


def _last_user_text(context) -> str:
    """The just-finalized student utterance from the shared LLMContext (same walk the
    InterviewDirector uses)."""
    text = ""
    try:
        for m in context.get_messages():
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                text = " ".join(p for p in parts if p).strip() or text
    except Exception:  # noqa: BLE001 - lead-in selection must never break the turn
        return ""
    return text


class LeadClauseProcessor(FrameProcessor):
    """Speak a bridge phrase the instant a user turn completes, ahead of the LLM reply.

    Injects a lead-in before EVERY downstream LLMContextFrame. The opening question is spoken via a
    TTSSpeakFrame (not an LLMContextFrame), so it never triggers this path — no special-casing needed.

    Args:
        strategy: "processor" to inject lead-ins (LLM off the gap clock); "native" to pass through
            (LLM on the gap clock — the A/B comparison arm).
    """

    def __init__(
        self,
        *,
        strategy: str = STRATEGY_PROCESSOR,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        if strategy not in (STRATEGY_PROCESSOR, STRATEGY_NATIVE):
            raise ValueError(f"strategy must be {STRATEGY_PROCESSOR!r} or {STRATEGY_NATIVE!r}")
        self._strategy = strategy
        self._turn_index = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        inject = (
            isinstance(frame, LLMContextFrame)
            and self._strategy == STRATEGY_PROCESSOR
            and direction == FrameDirection.DOWNSTREAM
        )
        if inject:
            lead = pick_lead_in(_last_user_text(frame.context), self._turn_index)
            self._turn_index += 1
            # Push the bridge FIRST so it reaches TTS ahead of any LLM output. append_to_context
            # is False: the lead-in is a spoken acknowledgement, not part of the assistant's logical
            # reply (the persona is told not to also acknowledge — TurnContext.lead_clause).
            await self.push_frame(TTSSpeakFrame(lead, append_to_context=False), direction)

        # Always forward the original frame (the context frame kicks the LLM to generate behind the
        # lead-in; every other frame passes through untouched).
        await self.push_frame(frame, direction)
