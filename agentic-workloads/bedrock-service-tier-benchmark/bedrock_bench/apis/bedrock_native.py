"""Bedrock-native APIs on ``bedrock-runtime``: Converse(Stream) and InvokeModel(WithResponseStream).

These go through boto3 (SigV4). The service tier is a top-level parameter:
``serviceTier={"type": tier}`` for Converse and ``serviceTier=tier`` for
InvokeModel. Omitting it selects the Standard tier.

Field shapes below were verified against live Bedrock (GLM 5.3, Oct 2026):

* Converse reports the served tier in the response (or stream ``metadata``)
  under ``serviceTier.type``, and cache usage as ``cacheReadInputTokens`` /
  ``cacheWriteInputTokens``; ``inputTokens`` counts only the *uncached* part.
* InvokeModel (OpenAI-shaped body) streams ``chat.completion.chunk`` objects;
  the first chunk may carry empty content, the last carries ``usage`` and
  ``amazon-bedrock-invocationMetrics`` (server-side ``firstByteLatency`` in ms).
"""

from __future__ import annotations

import json
from typing import Any

from .base import Measurement, Request, StreamRecorder, Usage, as_int

#: Body schema InvokeModel expects for a model (from the registry).
BODY_OPENAI = "openai"
BODY_NOVA = "nova"
BODY_ANTHROPIC = "anthropic"

_TIER_HEADER = "x-amzn-bedrock-service-tier"


def _converse_usage(u: dict | None) -> Usage:
    u = u or {}
    uncached = as_int(u.get("inputTokens")) or 0
    read = as_int(u.get("cacheReadInputTokens"))
    write = as_int(u.get("cacheWriteInputTokens"))
    return Usage(
        input_tokens=uncached + (read or 0) + (write or 0),
        output_tokens=as_int(u.get("outputTokens")),
        cache_read_tokens=read if read is not None else 0,
        cache_write_tokens=write if write is not None else 0,
    )


def _openai_usage(u: dict | None) -> Usage:
    u = u or {}
    pd = u.get("prompt_tokens_details") or {}
    cd = u.get("completion_tokens_details") or {}
    return Usage(
        input_tokens=as_int(u.get("prompt_tokens")),
        output_tokens=as_int(u.get("completion_tokens")),
        reasoning_tokens=as_int(cd.get("reasoning_tokens")),
        cache_read_tokens=as_int(pd.get("cached_tokens")),
        cache_write_tokens=as_int(pd.get("cache_write_tokens")),
    )


def _anthropic_usage(u: dict | None) -> Usage:
    u = u or {}
    base = as_int(u.get("input_tokens")) or 0
    read = as_int(u.get("cache_read_input_tokens")) or 0
    write = as_int(u.get("cache_creation_input_tokens")) or 0
    return Usage(
        input_tokens=base + read + write,
        output_tokens=as_int(u.get("output_tokens")),
        cache_read_tokens=read,
        cache_write_tokens=write,
    )


_nova_usage = _converse_usage  # Nova's InvokeModel body uses Converse-style usage keys


def _served(resp: dict) -> str | None:
    tier = resp.get("serviceTier")
    if isinstance(tier, dict):
        return tier.get("type")
    if isinstance(tier, str):
        return tier
    return resp.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get(_TIER_HEADER)


# --------------------------------------------------------------------------- Converse
def converse_messages(req: Request) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"text": req.document}]
    if req.explicit_cache:
        content.append({"cachePoint": {"type": "default"}})
    content.append({"text": req.question})
    return [{"role": "user", "content": content}]


def _converse_kwargs(req: Request) -> dict[str, Any]:
    cfg: dict[str, Any] = {"maxTokens": req.max_tokens}
    if req.temperature is not None:
        cfg["temperature"] = req.temperature
    kw: dict[str, Any] = {
        "modelId": req.model_id,
        "messages": converse_messages(req),
        "inferenceConfig": cfg,
    }
    if req.tier:
        kw["serviceTier"] = {"type": req.tier}
    if req.reasoning_effort:
        kw["additionalModelRequestFields"] = {"reasoning_effort": req.reasoning_effort}
    return kw


class ConverseStream:
    """``ConverseStream`` on bedrock-runtime."""

    def __init__(self, client: Any):
        self._client = client

    def send(self, req: Request) -> Measurement:
        rec = StreamRecorder()
        kw = _converse_kwargs(req)
        rec.start()
        try:
            resp = self._client.converse_stream(**kw)
            served = _served(resp)
            for event in resp["stream"]:
                if "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"].get("delta", {})
                    if "reasoningContent" in delta:
                        rec.reasoning(delta["reasoningContent"].get("text"))
                    rec.answer(delta.get("text"))
                elif "messageStop" in event:
                    rec.m.finish_reason = event["messageStop"].get("stopReason")
                elif "metadata" in event:
                    meta = event["metadata"]
                    rec.m.usage = _converse_usage(meta.get("usage"))
                    # metrics.latencyMs is total server latency, not first byte,
                    # so it is not used as server_first_byte.
                    served = _served(meta) or served
        except Exception as e:  # noqa: BLE001 - every failure becomes a recorded sample
            return rec.fail(e)
        rec.m.served_tier = served
        return rec.m


class Converse:
    """Non-streaming ``Converse`` (E2E only)."""

    def __init__(self, client: Any):
        self._client = client

    def send(self, req: Request) -> Measurement:
        rec = StreamRecorder()
        kw = _converse_kwargs(req)
        rec.start()
        try:
            resp = self._client.converse(**kw)
            rec.finish()
        except Exception as e:  # noqa: BLE001
            return rec.fail(e)
        rec.m.usage = _converse_usage(resp.get("usage"))
        rec.m.served_tier = _served(resp)
        rec.m.finish_reason = resp.get("stopReason")
        return rec.m


# --------------------------------------------------------------------------- InvokeModel
def invoke_body(req: Request, style: str, *, stream: bool) -> dict[str, Any]:
    """Request body for InvokeModel in the model's native schema."""
    if style == BODY_NOVA:
        content: list[dict[str, Any]] = [{"text": req.document}]
        if req.explicit_cache:
            content.append({"cachePoint": {"type": "default"}})
        content.append({"text": req.question})
        cfg: dict[str, Any] = {"maxTokens": req.max_tokens}
        if req.temperature is not None:
            cfg["temperature"] = req.temperature
        return {"messages": [{"role": "user", "content": content}], "inferenceConfig": cfg}
    if style == BODY_ANTHROPIC:
        doc: dict[str, Any] = {"type": "text", "text": req.document}
        if req.explicit_cache:
            doc["cache_control"] = {"type": "ephemeral"}
        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": req.max_tokens,
            "messages": [
                {"role": "user", "content": [doc, {"type": "text", "text": req.question}]}
            ],
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        return body
    # OpenAI ChatCompletions-shaped body (open-weight and OpenAI models).
    part: dict[str, Any] = {"type": "text", "text": req.document}
    if req.explicit_cache:
        part["prompt_cache_breakpoint"] = {"mode": "explicit"}
    body = {
        "messages": [{"role": "user", "content": [part, {"type": "text", "text": req.question}]}],
        "max_tokens": req.max_tokens,
    }
    if req.explicit_cache:
        body["prompt_cache_options"] = {"mode": "explicit", "ttl": "30m"}
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.reasoning_effort:
        body["reasoning_effort"] = req.reasoning_effort
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


def _invoke_kwargs(req: Request, style: str, *, stream: bool) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "modelId": req.model_id,
        "body": json.dumps(invoke_body(req, style, stream=stream)).encode("utf-8"),
        "contentType": "application/json",
        "accept": "application/json",
    }
    if req.tier:
        kw["serviceTier"] = req.tier
    return kw


def _apply_invocation_metrics(m: Measurement, chunk: dict) -> None:
    im = chunk.get("amazon-bedrock-invocationMetrics")
    if isinstance(im, dict) and im.get("firstByteLatency") is not None:
        m.server_first_byte = float(im["firstByteLatency"]) / 1000.0


class InvokeStream:
    """``InvokeModelWithResponseStream`` on bedrock-runtime."""

    def __init__(self, client: Any, body_style: str = BODY_OPENAI):
        self._client = client
        self._style = body_style

    def send(self, req: Request) -> Measurement:
        rec = StreamRecorder()
        kw = _invoke_kwargs(req, self._style, stream=True)
        rec.start()
        try:
            resp = self._client.invoke_model_with_response_stream(**kw)
            served = _served(resp)
            for event in resp["body"]:
                raw = (event.get("chunk") or {}).get("bytes")
                if not raw:
                    continue
                chunk = json.loads(raw)
                self._parse(rec, chunk)
                served = chunk.get("service_tier") or served
                _apply_invocation_metrics(rec.m, chunk)
        except Exception as e:  # noqa: BLE001
            return rec.fail(e)
        rec.m.served_tier = served
        return rec.m

    def _parse(self, rec: StreamRecorder, chunk: dict) -> None:
        if self._style == BODY_ANTHROPIC:
            t = chunk.get("type")
            if t == "content_block_delta":
                d = chunk.get("delta", {})
                rec.reasoning(d.get("thinking"))
                rec.answer(d.get("text"))
            elif t == "message_start":
                rec.m.usage = _anthropic_usage(chunk.get("message", {}).get("usage"))
            elif t == "message_delta":
                out = as_int((chunk.get("usage") or {}).get("output_tokens"))
                if out is not None:
                    rec.m.usage.output_tokens = out
                rec.m.finish_reason = (chunk.get("delta") or {}).get("stop_reason")
            return
        if self._style == BODY_NOVA:
            if "contentBlockDelta" in chunk:
                d = chunk["contentBlockDelta"].get("delta", {})
                if "reasoningContent" in d:
                    rec.reasoning(d["reasoningContent"].get("text"))
                rec.answer(d.get("text"))
            elif "metadata" in chunk:
                rec.m.usage = _nova_usage(chunk["metadata"].get("usage"))
            elif "messageStop" in chunk:
                rec.m.finish_reason = chunk["messageStop"].get("stopReason")
            return
        for choice in chunk.get("choices") or []:
            d = choice.get("delta") or {}
            rec.reasoning(d.get("reasoning_content") or d.get("reasoning"))
            rec.answer(d.get("content"))
            if choice.get("finish_reason"):
                rec.m.finish_reason = choice["finish_reason"]
        if chunk.get("usage"):
            rec.m.usage = _openai_usage(chunk["usage"])


class Invoke:
    """Non-streaming ``InvokeModel`` (E2E only)."""

    def __init__(self, client: Any, body_style: str = BODY_OPENAI):
        self._client = client
        self._style = body_style

    def send(self, req: Request) -> Measurement:
        rec = StreamRecorder()
        kw = _invoke_kwargs(req, self._style, stream=False)
        rec.start()
        try:
            resp = self._client.invoke_model(**kw)
            body = json.loads(resp["body"].read())
            rec.finish()
        except Exception as e:  # noqa: BLE001
            return rec.fail(e)
        if self._style == BODY_ANTHROPIC:
            rec.m.usage = _anthropic_usage(body.get("usage"))
        elif self._style == BODY_NOVA:
            rec.m.usage = _nova_usage(body.get("usage"))
        else:
            rec.m.usage = _openai_usage(body.get("usage"))
        rec.m.served_tier = body.get("service_tier") or _served(resp)
        _apply_invocation_metrics(rec.m, body)
        return rec.m
