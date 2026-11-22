# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat clients for the two Bedrock endpoints the models use.

- converse: bedrock-runtime Converse API (GPT-6 Astra, GPT-6.1 Sol, Claude Opus 5.5)
- mantle:   bedrock-mantle OpenAI-compatible Chat Completions, signed with SigV4 (GLM 5)
"""

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "us-east-1")
MAX_OUTPUT_TOKENS = int(os.environ.get("MAX_OUTPUT_TOKENS", "16000"))
LLM_TIMEOUT_SECONDS = 600

_session = boto3.Session(region_name=REGION)
_runtime = _session.client(
    "bedrock-runtime",
    config=Config(read_timeout=LLM_TIMEOUT_SECONDS, connect_timeout=10, retries={"mode": "adaptive", "max_attempts": 6}),
)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0
    seconds: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.calls += other.calls
        self.seconds += other.seconds

    def as_dict(self) -> dict:
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cacheReadTokens": self.cache_read_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
            "totalTokens": self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens,
            "llmCalls": self.calls,
            "llmSeconds": round(self.seconds, 2),
        }


@dataclass
class Conversation:
    """Provider-neutral message history: [{"role": "user"|"assistant", "text": str}]."""
    system: str
    messages: list = field(default_factory=list)


class LlmError(RuntimeError):
    pass


# bedrock-mantle serves most models under /v1; some (Gemma 4) only under /openai/v1.
MANTLE_PATHS = {"/v1", "/openai/v1"}


def chat(api: str, model_id: str, convo: Conversation, request_fields: dict | None = None,
         max_tokens: int | None = None, mantle_path: str | None = None) -> tuple[str, Usage]:
    # A model's own output cap wins, but never above the agent's limit.
    limit = min(max_tokens, MAX_OUTPUT_TOKENS) if max_tokens else MAX_OUTPUT_TOKENS
    if api == "converse":
        return _converse(model_id, convo, request_fields, limit)
    if api == "mantle":
        path = mantle_path or "/v1"
        if path not in MANTLE_PATHS:
            raise LlmError(f"Unsupported bedrock-mantle path '{path}'")
        return _mantle(model_id, convo, limit, path)
    raise LlmError(f"Unknown api '{api}'")


def _converse(model_id: str, convo: Conversation, request_fields: dict | None, max_tokens: int) -> tuple[str, Usage]:
    started = time.monotonic()
    kwargs = {}
    if request_fields:
        # Model-specific options, for example {"reasoning": {"effort": "low"}} for Grok 4.7.
        kwargs["additionalModelRequestFields"] = request_fields
    try:
        resp = _runtime.converse(
            modelId=model_id,
            system=[{"text": convo.system}],
            messages=[{"role": m["role"], "content": [{"text": m["text"]}]} for m in convo.messages],
            inferenceConfig={"maxTokens": max_tokens},
            **kwargs,
        )
    except Exception as exc:  # botocore ClientError, timeouts
        raise LlmError(f"Bedrock Converse call failed: {exc}") from exc
    seconds = time.monotonic() - started
    blocks = resp.get("output", {}).get("message", {}).get("content", [])
    text = "".join(b.get("text", "") for b in blocks if "text" in b)
    u = resp.get("usage", {})
    usage = Usage(
        input_tokens=u.get("inputTokens", 0),
        output_tokens=u.get("outputTokens", 0),
        cache_read_tokens=u.get("cacheReadInputTokens", 0),
        cache_write_tokens=u.get("cacheWriteInputTokens", 0),
        calls=1,
        seconds=seconds,
    )
    return text, usage


def _mantle(model_id: str, convo: Conversation, max_tokens: int, path: str = "/v1") -> tuple[str, Usage]:
    url = f"https://bedrock-mantle.{REGION}.api.aws{path}/chat/completions"
    body = json.dumps({
        "model": model_id,
        "messages": [{"role": "system", "content": convo.system}]
        + [{"role": m["role"], "content": m["text"]} for m in convo.messages],
        "max_tokens": max_tokens,
    })
    last_error = None
    for attempt in range(5):
        creds = _session.get_credentials().get_frozen_credentials()
        req = AWSRequest(method="POST", url=url, data=body, headers={"Content-Type": "application/json"})
        SigV4Auth(creds, "bedrock-mantle", REGION).add_auth(req)
        http_req = urllib.request.Request(url, data=body.encode(), headers=dict(req.headers), method="POST")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(http_req, timeout=LLM_TIMEOUT_SECONDS) as r:
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:500].decode(errors="replace")
            last_error = f"bedrock-mantle returned HTTP {exc.code}: {detail}"
            if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise LlmError(last_error) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LlmError(f"bedrock-mantle call failed: {exc}") from exc
    else:
        raise LlmError(last_error or "bedrock-mantle call failed")
    seconds = time.monotonic() - started
    text = data["choices"][0]["message"].get("content") or ""
    u = data.get("usage", {})
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    usage = Usage(
        input_tokens=u.get("prompt_tokens", 0) - cached,
        output_tokens=u.get("completion_tokens", 0),
        cache_read_tokens=cached,
        calls=1,
        seconds=seconds,
    )
    return text, usage
