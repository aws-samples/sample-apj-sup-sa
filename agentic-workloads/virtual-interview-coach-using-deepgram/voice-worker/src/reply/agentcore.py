"""AgentCore reply generator (T019) — PRIMARY (constitution: AgentCore-first for POC).

Streams the per-turn coach reply via AWS Bedrock AgentCore (bedrock-agent-runtime
InvokeAgent). Implements the ReplyGenerator contract: first chunk ASAP, cancellable, plain
text for TTS, no durable raw-context persistence (Constitution III). Measured against the
gate; if it cannot hold SC-001, the config swaps to bedrock_direct with no other code change.

boto3's InvokeAgent returns a blocking EventStream completion iterator, so it is consumed in
a worker thread that pushes text deltas onto an asyncio.Queue; the async generator drains the
queue and stays cancellable on barge-in (closing the generator signals the thread to stop).
This mirrors bedrock_direct.py so the two providers are measured under identical plumbing.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import AsyncIterator

from .interface import SYSTEM_PROMPT, TurnContext

log = logging.getLogger("voice_worker")

_DONE = object()  # sentinel pushed onto the queue when the stream ends


class AgentCoreReplyGenerator:
    def __init__(self, config) -> None:
        self._region = config.aws_region
        self._agent_id = config.agentcore_agent_id
        self._agent_alias_id = config.agentcore_agent_alias_id
        self._client = None  # lazily created bedrock-agent-runtime client

    def _ensure_client(self):
        if self._client is None:
            import boto3  # lazy: keep import cost out of DRY/test paths

            self._client = boto3.client("bedrock-agent-runtime", region_name=self._region)
        return self._client

    def _build_input(self, ctx: TurnContext) -> str:
        """Compose the generic running transcript into one InvokeAgent inputText (no PII).

        AgentCore keeps server-side session state keyed by sessionId, but the agent alias was
        provisioned with the coach instruction (SYSTEM_PROMPT) baked in, so we send the rolling
        transcript as context + the new student turn. All content is generic (Constitution III).
        """
        lines: list[str] = []
        for u in ctx.history:
            speaker = "Student" if u.speaker == "student" else "Coach"
            lines.append(f"{speaker}: {u.text}")
        lines.append(f"Student: {ctx.student_text}")
        lines.append("Coach:")
        return "\n".join(lines)

    async def stream(self, ctx: TurnContext) -> AsyncIterator[str]:  # type: ignore[override]
        """Yield reply chunks from AgentCore InvokeAgent streaming as they arrive.

        Runs the blocking boto3 EventStream iteration in a thread executor and pushes each
        decoded text chunk onto an asyncio.Queue. On barge-in the caller stops iterating; the
        generator's finally block sets a stop flag so the worker thread aborts promptly.
        """
        if not self._agent_id:
            raise RuntimeError("AGENTCORE_AGENT_ID not set")
        if not self._agent_alias_id:
            raise RuntimeError("AGENTCORE_AGENT_ALIAS_ID not set")

        client = self._ensure_client()
        input_text = self._build_input(ctx)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        def _pump() -> None:
            try:
                resp = client.invoke_agent(
                    agentId=self._agent_id,
                    agentAliasId=self._agent_alias_id,
                    sessionId=ctx.session_id,
                    inputText=input_text,
                )
                for event in resp["completion"]:
                    if stop.is_set():
                        break
                    chunk = event.get("chunk")
                    if chunk:
                        data = chunk.get("bytes")
                        if data:
                            loop.call_soon_threadsafe(queue.put_nowait, data.decode("utf-8"))
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
            # Do not await the worker; it observes `stop` and exits on its own. Awaiting could
            # block the loop on a slow in-flight network read during barge-in.
            del worker
