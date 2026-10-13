"""Anthropic Messages API (``POST /anthropic/v1/messages``) on either Bedrock endpoint.

Verified live (Oct 2026): the path is ``/anthropic/v1/messages`` on both
``bedrock-runtime`` and ``bedrock-mantle``; other paths fail (runtime answers
HTTP 200 with a Coral ``UnknownOperationException`` body, Mantle answers 404).

The stream is Server-Sent Events. Usage arrives in ``message_start`` (input and
cache tokens) and ``message_delta`` (output tokens).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from .base import Measurement, Request, StreamRecorder, Usage, as_int

MESSAGES_PATH = "/anthropic/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def host_for(endpoint: str, region: str) -> str:
    if endpoint == "mantle":
        return f"https://bedrock-mantle.{region}.api.aws"
    return f"https://bedrock-runtime.{region}.amazonaws.com"


def messages_usage(u: dict | None) -> Usage:
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


class MessagesError(RuntimeError):
    """Non-success HTTP response from the Messages API."""


def iter_sse(lines) -> Any:
    """Yield ``(event, data_dict)`` pairs from an SSE line iterator."""
    event = None
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                payload = "\n".join(data)
                try:
                    yield event, json.loads(payload)
                except json.JSONDecodeError:
                    yield event, {"_raw": payload}
            event, data = None, []
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if data:
        try:
            yield event, json.loads("\n".join(data))
        except json.JSONDecodeError:
            yield event, {"_raw": "\n".join(data)}


class Messages:
    """Streaming Anthropic Messages API."""

    def __init__(
        self,
        endpoint: str,
        region: str,
        token_provider: Callable[[], str],
        client: httpx.Client | None = None,
    ):
        self._url = host_for(endpoint, region) + MESSAGES_PATH
        self._token = token_provider
        self._client = client or httpx.Client()

    def body(self, req: Request) -> dict[str, Any]:
        doc: dict[str, Any] = {"type": "text", "text": req.document}
        if req.explicit_cache:
            doc["cache_control"] = {"type": "ephemeral"}
        body: dict[str, Any] = {
            "model": req.model_id,
            "max_tokens": req.max_tokens,
            "stream": True,
            "messages": [
                {"role": "user", "content": [doc, {"type": "text", "text": req.question}]}
            ],
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.tier:
            body["service_tier"] = req.tier
        return body

    def send(self, req: Request) -> Measurement:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        payload = json.dumps(self.body(req))
        rec = StreamRecorder()
        rec.start(req.timeout)
        try:
            with self._client.stream(
                "POST", self._url, headers=headers, content=payload, timeout=req.timeout
            ) as r:
                if r.status_code != 200:
                    r.read()
                    raise MessagesError(f"HTTP {r.status_code}: {r.text[:300]}")
                ctype = r.headers.get("content-type", "")
                if "text/event-stream" not in ctype:
                    r.read()
                    raise MessagesError(f"unexpected content-type {ctype!r}: {r.text[:300]}")
                rec.m.served_tier = r.headers.get("x-amzn-bedrock-service-tier")
                for event, data in iter_sse(r.iter_lines()):
                    rec.check()
                    self._on_event(rec, event or data.get("type"), data)
        except Exception as e:  # noqa: BLE001 - every failure becomes a recorded sample
            return rec.fail(e)
        return rec.m

    @staticmethod
    def _on_event(rec: StreamRecorder, etype: str | None, data: dict) -> None:
        if etype == "content_block_delta":
            d = data.get("delta") or {}
            rec.reasoning(d.get("thinking"))
            rec.answer(d.get("text"))
        elif etype == "message_start":
            msg = data.get("message") or {}
            rec.m.usage = messages_usage(msg.get("usage"))
            rec.m.served_tier = msg.get("service_tier") or rec.m.served_tier
        elif etype == "message_delta":
            out = as_int((data.get("usage") or {}).get("output_tokens"))
            if out is not None:
                rec.m.usage.output_tokens = out
            rec.m.finish_reason = (data.get("delta") or {}).get("stop_reason")
        elif etype == "error":
            err = data.get("error") or {}
            rec.m.error = f"StreamError: {err.get('type')}: {err.get('message')}"
            rec.m.error_kind = "server"
