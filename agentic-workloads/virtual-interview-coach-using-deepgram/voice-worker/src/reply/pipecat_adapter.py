"""ReplyGenerator -> Pipecat LLMService adapter (Feature 007, T009).

Bridges our tuned, gate-proven reply seam (`ReplyGenerator.stream(TurnContext) -> AsyncIterator[str]`,
with the G2 minimized-fact personalization payloads) onto Pipecat's `LLMService`, so the existing
`bedrock_direct` / `agentcore` providers and the `persona.build_system_prompt` grounding are reused
UNCHANGED inside a Pipecat pipeline (FR-016 / research R4). We deliberately do NOT route through
Pipecat's native `AWSBedrockLLMService` for the default path: that would discard our prompt grounding
and the minimized-fact discipline (Constitution III). The native service is evaluated separately in
the spike for the A/B (T010).

The real Pipecat custom-LLM contract (mirrored from `AWSBedrockLLMService._process_context`, verified
against pipecat 1.3.0 — see specs/007-pipecat-adoption/pipecat-api-notes.md):

  - subclass `LLMService`;
  - override `process_frame` to catch `LLMContextFrame` and call `_process_context(frame.context)`;
  - in `_process_context`: push `LLMFullResponseStartFrame()`, then one `LLMTextFrame(chunk)` per
    streamed chunk, then `LLMFullResponseEndFrame()` in a `finally`;
  - re-raise `asyncio.CancelledError` so barge-in cancels cleanly.

Personalization: Pipecat's `LLMContext` only carries OpenAI-shaped role/content messages, which is the
generic transcript. The optional `enrich_turn_context` callable lets the pipeline inject the per-session
minimized grounding (resume highlights, job scope, difficulty profile, current archetype) onto the
reconstructed `TurnContext` WITHOUT putting raw PII into the shared context (it stays worker-local).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from .interface import ReplyGenerator, TurnContext, Utterance

log = logging.getLogger("voice_worker")

# A hook the pipeline sets so the per-turn TurnContext is widened with the session's minimized
# grounding payload (G2). Default is identity (generic G1 path). Worker-local; never logged.
TurnContextEnricher = Callable[[TurnContext], TurnContext]


def _identity(ctx: TurnContext) -> TurnContext:
    return ctx


class ReplyGeneratorLLMService(LLMService):
    """A Pipecat LLMService that delegates token generation to one of our ReplyGenerators.

    Keeps the gate-proven provider + persona grounding intact; only the transport of the resulting
    token stream changes (queue -> Pipecat frames).
    """

    def __init__(
        self,
        reply: ReplyGenerator,
        *,
        session_id: str = "",
        enrich_turn_context: TurnContextEnricher | None = None,
        turn_budget_s: float | None = None,
        fallback_text: Callable[[], str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._reply = reply
        # The session this adapter serves; stamped onto every reconstructed TurnContext so providers
        # that key server-side state on it (AgentCore's sessionId) get the right, per-session value.
        self._session_id = session_id
        self._enrich = enrich_turn_context or _identity
        # Graceful degradation (FR-221 / FR-006): if the live reply errors, yields nothing, or stalls
        # past turn_budget_s, speak a CONTAINED fallback probe instead of going silent. None budget
        # disables the timeout (errors/empty still trigger the fallback). fallback_text() returns the
        # probe to speak (stays within the current competency — SC-005); None disables the fallback.
        self._turn_budget_s = turn_budget_s
        self._fallback_text = fallback_text

    def set_enricher(self, enrich: TurnContextEnricher | None) -> None:
        """Let the pipeline (re)bind the per-session personalization enricher."""
        self._enrich = enrich or _identity

    def _turn_context_from_llm_context(self, context: LLMContext) -> TurnContext:
        """Reconstruct our TurnContext from Pipecat's OpenAI-shaped messages.

        The last `user` message is the just-finalized student utterance; everything before it is the
        running transcript. `system` messages are ignored here (our persona builds the system prompt).
        No raw PII is read from the shared context — only the generic transcript text.
        """
        messages = context.get_messages()
        history: list[Utterance] = []
        student_text = ""
        # Walk all but treat the final user message as the current student turn.
        last_user_idx = -1
        for i, m in enumerate(messages):
            if m.get("role") == "user":
                last_user_idx = i
        for i, m in enumerate(messages):
            role = m.get("role")
            content = m.get("content")
            text = content if isinstance(content, str) else _flatten_content(content)
            if not text:
                continue
            if role == "user" and i == last_user_idx:
                student_text = text
                continue
            if role == "user":
                history.append(Utterance(speaker="student", text=text))
            elif role == "assistant":
                history.append(Utterance(speaker="coach", text=text))
            # system messages: skipped (persona owns the system prompt)
        return TurnContext(
            session_id=self._session_id, student_text=student_text, history=history
        )

    async def _process_context(self, context: LLMContext) -> None:
        """Stream our ReplyGenerator's tokens out as Pipecat LLM frames.

        Graceful degradation (FR-221 / FR-006): if the provider errors, stalls past the turn budget,
        or yields nothing, speak a contained fallback probe rather than leaving the coach silent."""
        ctx = self._enrich(self._turn_context_from_llm_context(context))
        await self.push_frame(LLMFullResponseStartFrame())
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()
        agen = self._reply.stream(ctx)
        first = True
        emitted_any = False
        degraded = False
        cancelled = False
        try:
            while True:
                try:
                    if self._turn_budget_s is not None:
                        chunk = await asyncio.wait_for(agen.__anext__(), timeout=self._turn_budget_s)
                    else:
                        chunk = await agen.__anext__()
                except StopAsyncIteration:
                    break
                if not chunk:
                    continue
                if first:
                    await self.stop_ttfb_metrics()
                    first = False
                emitted_any = True
                await self.push_frame(LLMTextFrame(chunk))
        except asyncio.CancelledError:
            # Barge-in / shutdown: abort the provider stream and propagate so the task cancels.
            # NOT a degradation — the student interrupted on purpose, so the contained fallback
            # must NOT speak (live-session bug: every barge-in landed before the first token,
            # emitted_any stayed False, and the finally spoke an unwanted canned probe).
            cancelled = True
            await _aclose(agen)
            raise
        except asyncio.TimeoutError:
            log.warning("reply stalled past turn budget (%ss); contained fallback", self._turn_budget_s)
            degraded = True
        except Exception as exc:  # noqa: BLE001 - one turn must not kill the pipeline
            log.warning("reply stream errored (%s); contained fallback", type(exc).__name__)
            degraded = True
        finally:
            await _aclose(agen)
            # Speak a contained probe if the turn degraded or produced nothing (never go silent) —
            # but never on cancellation (the student took the floor; silence is correct).
            if not cancelled and (degraded or not emitted_any) and self._fallback_text is not None:
                try:
                    await self.push_frame(LLMTextFrame(self._fallback_text()))
                except Exception:  # noqa: BLE001 - even the fallback must not crash the turn
                    pass
            await self.stop_processing_metrics()
            await self.push_frame(LLMFullResponseEndFrame())

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            await self._process_context(frame.context)
        else:
            await self.push_frame(frame, direction)


def _flatten_content(content) -> str:
    """OpenAI content can be a list of parts; join any text parts into a plain string."""
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts).strip()
    return ""


async def _aclose(agen) -> None:
    """Close an async generator, swallowing the expected close-time exceptions."""
    aclose = getattr(agen, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
