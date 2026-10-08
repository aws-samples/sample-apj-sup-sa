"""OpenAI-compatible APIs (Chat Completions, Responses) on either Bedrock endpoint.

Base URLs:

* ``bedrock-runtime``: ``https://bedrock-runtime.{region}.amazonaws.com/openai/v1``
* ``bedrock-mantle``:  ``https://bedrock-mantle.{region}.api.aws/v1`` — some models
  are served under ``/openai/v1`` instead (the registry records the path).

Authentication is a short-lived Bedrock bearer token, refreshed by the caller's
``token_provider`` *before* the timer starts.

Responses requests always send ``store=False``: Bedrock otherwise retains the
request and response for 30 days.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openai import OpenAI

from .base import Measurement, Request, StreamRecorder, Usage, as_int

RUNTIME_OPENAI_PATH = "/openai/v1"
MANTLE_DEFAULT_PATH = "/v1"


def runtime_base_url(region: str) -> str:
    return f"https://bedrock-runtime.{region}.amazonaws.com{RUNTIME_OPENAI_PATH}"


def mantle_base_url(region: str, path: str = MANTLE_DEFAULT_PATH) -> str:
    return f"https://bedrock-mantle.{region}.api.aws{path}"


def _dump(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    return dump() if callable(dump) else {}


def chat_usage(u: Any) -> Usage:
    u = _dump(u)
    pd = u.get("prompt_tokens_details") or {}
    cd = u.get("completion_tokens_details") or {}
    return Usage(
        input_tokens=as_int(u.get("prompt_tokens")),
        output_tokens=as_int(u.get("completion_tokens")),
        reasoning_tokens=as_int(cd.get("reasoning_tokens")),
        cache_read_tokens=as_int(pd.get("cached_tokens")),
        cache_write_tokens=as_int(pd.get("cache_write_tokens")),
    )


def responses_usage(u: Any) -> Usage:
    u = _dump(u)
    idt = u.get("input_tokens_details") or {}
    odt = u.get("output_tokens_details") or {}
    return Usage(
        input_tokens=as_int(u.get("input_tokens")),
        output_tokens=as_int(u.get("output_tokens")),
        reasoning_tokens=as_int(odt.get("reasoning_tokens")),
        cache_read_tokens=as_int(idt.get("cached_tokens")),
        cache_write_tokens=as_int(idt.get("cache_write_tokens")),
    )


def _cache_extra(req: Request) -> dict[str, Any]:
    if not req.explicit_cache:
        return {}
    return {"prompt_cache_options": {"mode": "explicit", "ttl": "30m"}}


class _OpenAIBase:
    def __init__(self, base_url: str, token_provider: Callable[[], str]):
        self._token = token_provider
        self._client = OpenAI(api_key=token_provider(), base_url=base_url, max_retries=0)

    def _prepare(self, req: Request) -> OpenAI:
        # Untimed: refresh the bearer token and apply the per-request timeout.
        self._client.api_key = self._token()
        return self._client.with_options(timeout=req.timeout)


class ChatCompletions(_OpenAIBase):
    """Streaming ``POST /chat/completions``."""

    def body(self, req: Request) -> dict[str, Any]:
        part: dict[str, Any] = {"type": "text", "text": req.document}
        if req.explicit_cache:
            part["prompt_cache_breakpoint"] = {"mode": "explicit"}
        kw: dict[str, Any] = {
            "model": req.model_id,
            "messages": [
                {"role": "user", "content": [part, {"type": "text", "text": req.question}]}
            ],
            "max_tokens": req.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if req.temperature is not None:
            kw["temperature"] = req.temperature
        if req.tier:
            kw["service_tier"] = req.tier
        if req.reasoning_effort:
            kw["reasoning_effort"] = req.reasoning_effort
        extra = _cache_extra(req)
        if extra:
            kw["extra_body"] = extra
        return kw

    def send(self, req: Request) -> Measurement:
        client = self._prepare(req)
        kw = self.body(req)
        rec = StreamRecorder()
        rec.start(req.timeout)
        try:
            served = None
            for chunk in client.chat.completions.create(**kw):
                rec.check()
                served = getattr(chunk, "service_tier", None) or served
                for choice in chunk.choices or []:
                    d = choice.delta
                    rec.reasoning(
                        getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None)
                    )
                    rec.answer(getattr(d, "content", None))
                    if choice.finish_reason:
                        rec.m.finish_reason = choice.finish_reason
                if getattr(chunk, "usage", None) is not None:
                    rec.m.usage = chat_usage(chunk.usage)
        except Exception as e:  # noqa: BLE001 - every failure becomes a recorded sample
            return rec.fail(e)
        rec.m.served_tier = served
        return rec.m


_REASONING_EVENTS = (
    "response.reasoning.delta",
    "response.reasoning_text.delta",
    "response.reasoning_summary_text.delta",
)
_TERMINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed")


class Responses(_OpenAIBase):
    """Streaming ``POST /responses`` with ``store=False``."""

    def body(self, req: Request) -> dict[str, Any]:
        block: dict[str, Any] = {"type": "input_text", "text": req.document}
        if req.explicit_cache:
            block["prompt_cache_breakpoint"] = {"mode": "explicit"}
        kw: dict[str, Any] = {
            "model": req.model_id,
            "input": [
                {
                    "role": "user",
                    "content": [block, {"type": "input_text", "text": req.question}],
                }
            ],
            "max_output_tokens": req.max_tokens,
            "stream": True,
            "store": False,
        }
        if req.temperature is not None:
            kw["temperature"] = req.temperature
        if req.tier:
            kw["service_tier"] = req.tier
        if req.reasoning_effort:
            kw["reasoning"] = {"effort": req.reasoning_effort}
        extra = _cache_extra(req)
        if extra:
            kw["extra_body"] = extra
        return kw

    def send(self, req: Request) -> Measurement:
        client = self._prepare(req)
        kw = self.body(req)
        rec = StreamRecorder()
        rec.start(req.timeout)
        try:
            for event in client.responses.create(**kw):
                rec.check()
                etype = getattr(event, "type", "")
                if etype == "response.output_text.delta":
                    rec.answer(getattr(event, "delta", None))
                elif etype in _REASONING_EVENTS:
                    rec.reasoning(getattr(event, "delta", None))
                elif etype in _TERMINAL_EVENTS:
                    resp = getattr(event, "response", None)
                    rec.m.usage = responses_usage(getattr(resp, "usage", None))
                    rec.m.served_tier = getattr(resp, "service_tier", None)
                    rec.m.finish_reason = etype.split(".", 1)[1]
                    if etype == "response.failed":
                        err = getattr(resp, "error", None)
                        rec.m.error = f"ResponseFailed: {err}"
                        rec.m.error_kind = "server"
                elif etype == "error":
                    rec.m.error = f"StreamError: {getattr(event, 'message', '')}"
                    rec.m.error_kind = "server"
        except Exception as e:  # noqa: BLE001
            return rec.fail(e)
        return rec.m
