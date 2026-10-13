"""Outside-world knowledge via an Amazon Bedrock AgentCore harness.

The voice agent stays small and fast: it owns the video and delegates anything
beyond it to a managed agent that runs in its own isolated environment, keeps its
own session state, and reaches Amazon's web index through an AgentCore Gateway.
No third-party search API, no keys to rotate, and queries never leave AWS.

``InvokeHarness`` returns an event stream of the harness's whole agent loop, so
the tool call surfaces both the final answer and which tools it used along the
way — useful for showing the agent's work in the UI.
"""

from __future__ import annotations

import functools
import json
import time
import uuid

import boto3
from botocore.config import Config
from loguru import logger

from talk2vid import config

# The harness runs a multi-step loop (search, read, reason), so its ceiling is
# much higher than a single model call.
_CFG = Config(
    region_name=config.AWS_REGION,
    retries={"max_attempts": 2, "mode": "standard"},
    connect_timeout=4,
    read_timeout=60,
)


@functools.lru_cache(maxsize=1)
def client():
    return boto3.client("bedrock-agentcore", config=_CFG)


def available() -> bool:
    return bool(config.AGENTCORE_HARNESS_ARN)


def _session_id(session_id: str | None) -> str:
    """Harness session ids must be at least 33 characters."""
    raw = session_id or f"talk2vid-{uuid.uuid4()}"
    return raw if len(raw) >= 33 else f"{raw}-{uuid.uuid4()}"


def ask_world_sync(question: str, *, session_id: str | None = None, timeout: float = 45.0) -> dict:
    """Ask the harness a question. Blocking — call it from a thread.

    Returns ``{"answer": str, "tools_used": [str], "latency_ms": int}`` or
    ``{"error": str}``.
    """
    if not available():
        return {"error": "the world-knowledge agent is not configured"}

    started = time.time()
    response = client().invoke_harness(
        harnessArn=config.AGENTCORE_HARNESS_ARN,
        runtimeSessionId=_session_id(session_id),
        messages=[{"role": "user", "content": [{"text": question}]}],
        timeoutSeconds=int(timeout),
    )

    answer_parts: list[str] = []
    tools_used: list[str] = []
    tool_args: dict[str, str] = {}
    stop_reason = None
    usage = {}

    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if tool := start.get("toolUse"):
                name = tool.get("name", "tool")
                tools_used.append(name)
                tool_args[str(event["contentBlockStart"]["contentBlockIndex"])] = ""
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if text := delta.get("text"):
                answer_parts.append(text)
            elif tool_delta := delta.get("toolUse"):
                idx = str(event["contentBlockDelta"]["contentBlockIndex"])
                tool_args[idx] = tool_args.get(idx, "") + (tool_delta.get("input") or "")
        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason")
        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
        elif "validationException" in event:
            return {"error": event["validationException"].get("message", "validation error")}
        elif "internalServerException" in event or "runtimeClientError" in event:
            payload = event.get("internalServerException") or event["runtimeClientError"]
            return {"error": payload.get("message", "harness error")}

    answer = "".join(answer_parts).strip()
    latency_ms = int((time.time() - started) * 1000)
    queries = [
        json.loads(raw).get("query")
        for raw in tool_args.values()
        if raw.strip().startswith("{")
    ]
    logger.info(
        f"agentcore harness: {latency_ms}ms · tools={tools_used} · "
        f"stop={stop_reason} · tokens={usage.get('totalTokens')}"
    )
    if not answer:
        return {
            "error": f"the research agent returned nothing (stop reason: {stop_reason})",
            "tools_used": tools_used,
        }
    return {
        "answer": answer,
        "tools_used": tools_used,
        "queries": [q for q in queries if q],
        "latency_ms": latency_ms,
    }


def warmup() -> None:
    """Open the TLS connection so the first real question does not pay for it."""
    if not available():
        return
    try:
        client().meta.events  # touching the client is enough to build it
        logger.debug("agentcore client ready")
    except Exception as exc:
        logger.warning(f"agentcore warmup failed: {exc}")
