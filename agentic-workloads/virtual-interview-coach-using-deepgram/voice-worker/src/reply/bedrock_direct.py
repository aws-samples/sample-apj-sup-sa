"""Direct-Bedrock reply generator (T020) — FALLBACK / latency escape hatch.

A lean direct Bedrock Runtime streaming call (converse_stream). Same ReplyGenerator
contract as AgentCore so swapping is a one-line config change (FR-014). This is the
documented mitigation if AgentCore cannot hold the SC-001 latency gate (Flags F7/F15).

boto3's event stream is a blocking iterator, so it is consumed in a worker thread that
pushes text deltas onto an asyncio.Queue; the async generator drains the queue and stays
cancellable on barge-in (closing the generator signals the thread to stop).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import AsyncIterator

from .interface import TurnContext
from .persona import build_system_prompt

log = logging.getLogger("voice_worker")

_DONE = object()  # sentinel pushed onto the queue when the stream ends


class BedrockDirectReplyGenerator:
    def __init__(self, config) -> None:
        self._region = config.aws_region
        self._model_id = config.bedrock_model_id
        self._client = None  # lazily created bedrock-runtime client

    def _ensure_client(self):
        if self._client is None:
            import boto3  # lazy: keep import cost out of DRY/test paths

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def _build_messages(self, ctx: TurnContext) -> list[dict]:
        """Map the generic running transcript to Bedrock Converse messages (no PII).

        Converse rejects empty-text content blocks AND a conversation that does not END on a `user`
        turn. The end-of-interview DEBRIEF turn (F004) carries student_text="" (it is built from the
        history via the system prompt, not a reply to a student utterance), and its history ends on a
        coach turn — both would trip a ValidationException. So: skip empty-text utterances, append the
        final user turn only when there is real student text, and if the result is empty or ends on an
        assistant turn, append a minimal user nudge so Converse always gets a valid trailing user turn.
        """
        messages: list[dict] = []
        for u in ctx.history:
            text = (u.text or "").strip()
            if not text:
                continue
            role = "user" if u.speaker == "student" else "assistant"
            messages.append({"role": role, "content": [{"text": text}]})
        student_text = (ctx.student_text or "").strip()
        if student_text:
            messages.append({"role": "user", "content": [{"text": student_text}]})
        # Ensure a valid trailing user turn (debrief / empty-history edge cases). The system prompt
        # fully specifies the content; this is just a structurally-required, benign user message.
        if not messages or messages[-1]["role"] != "user":
            nudge = "Please share your closing thoughts now." if ctx.is_debrief else "Please continue."
            messages.append({"role": "user", "content": [{"text": nudge}]})
        return messages

    async def stream(self, ctx: TurnContext) -> AsyncIterator[str]:  # type: ignore[override]
        """Yield reply chunks from Bedrock Converse streaming as they arrive.

        Runs the blocking boto3 EventStream iteration in a thread executor and pushes
        contentBlockDelta text onto an asyncio.Queue. On barge-in the caller stops iterating;
        the generator's finally block sets a stop flag so the worker thread aborts promptly.
        """
        client = self._ensure_client()
        messages = self._build_messages(ctx)
        system_prompt = build_system_prompt(ctx)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        def _pump() -> None:
            try:
                resp = client.converse_stream(
                    modelId=self._model_id,
                    system=[{"text": system_prompt}],
                    messages=messages,
                    inferenceConfig={"maxTokens": 256, "temperature": 0.7},
                )
                for event in resp["stream"]:
                    if stop.is_set():
                        break
                    delta = event.get("contentBlockDelta")
                    if delta:
                        text = delta.get("delta", {}).get("text")
                        if text:
                            loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as exc:  # surface to the consumer, never crash the loop
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)

        worker = loop.run_in_executor(None, _pump)
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            stop.set()  # tell the worker to abort the EventStream (barge-in / early close)
            # Do not await the worker here; it observes `stop` and exits on its own. Awaiting
            # could block the loop on a slow in-flight network read during barge-in.
            del worker
