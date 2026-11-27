# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Test doubles: a scripted Strands model, a fake backend tool API and a fake Step Functions client.

``ScriptedModel`` drives the real Strands agent loop (hooks, tool execution, metrics), so tests
exercise the runtime's actual wiring rather than a mocked agent.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from botocore.exceptions import ClientError
from strands.models.model import Model

Step = dict[str, Any]


def say(text: str, usage: tuple[int, int] = (100, 20)) -> Step:
    return {"text": text, "usage": usage}


def use(name: str, args: dict[str, Any], tool_id: str | None = None, usage: tuple[int, int] = (100, 20)) -> Step:
    return {"tool": name, "args": args, "id": tool_id or f"t-{name}", "usage": usage}


class ScriptedModel(Model):
    """Plays one scripted step per model call; records what it was sent."""

    def __init__(self, steps: list[Any], cache_read: int = 0) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []
        self._cache_read = cache_read

    def update_config(self, **model_config: Any) -> None:  # pragma: no cover - unused
        pass

    def get_config(self) -> Any:
        return {}

    def structured_output(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:  # pragma: no cover
        raise NotImplementedError

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncIterable[Any]:
        self.calls.append(
            {"messages": messages, "tools": [t["name"] for t in tool_specs or []], "system": system_prompt}
        )
        step: Any = self.steps.pop(0) if self.steps else say("done")
        uses = step if isinstance(step, list) else [step]  # a list: several tool calls in one response
        step = uses[0]
        if step.get("raise"):
            raise step["raise"]
        yield {"messageStart": {"role": "assistant"}}
        if "tool" in step:
            if step.get("preamble"):  # text the model writes before its tool calls
                yield {"contentBlockDelta": {"delta": {"text": step["preamble"]}}}
                yield {"contentBlockStop": {}}
            for one in uses:
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": one["id"], "name": one["tool"]}}}}
                encoded = json.dumps(one["args"])
                half = len(encoded) // 2
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": encoded[:half]}}}}  # input streams in parts
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": encoded[half:]}}}}
                yield {"contentBlockStop": {}}
            stop = "tool_use"
        else:
            yield {"contentBlockDelta": {"delta": {"text": step["text"]}}}
            yield {"contentBlockStop": {}}
            stop = step.get("stop", "end_turn")  # e.g. "guardrail_intervened"
        yield {"messageStop": {"stopReason": stop}}
        i, o = step["usage"]
        yield {
            "metadata": {
                "usage": {
                    "inputTokens": i,
                    "outputTokens": o,
                    "totalTokens": i + o,
                    "cacheReadInputTokens": self._cache_read,
                },
                "metrics": {"latencyMs": 1},
            }
        }


@dataclass
class FakeBackend:
    """``/agent/tools/*`` and ``/api/*`` behind an ``httpx.MockTransport``."""

    tools: list[str] = field(default_factory=lambda: ["kg_search", "kg_save_output", "progress_update"])
    budget: dict[str, Any] = field(
        default_factory=lambda: {"genai_enabled": True, "state": "open", "remaining": 1_000_000}
    )
    product: str | None = None
    definition: dict[str, Any] | None = None  # the module as the backend stores it (run_definition)
    handlers: dict[str, Callable[[dict[str, Any]], tuple[int, Any]]] = field(default_factory=dict)
    requests: list[tuple[str, str, dict[str, Any], dict[str, str]]] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}") if request.content else {}
        path = request.url.path
        self.requests.append((request.method, path, body, dict(request.headers)))
        if path.startswith("/agent/tools/"):
            name = path.rsplit("/", 1)[-1]
            if name in self.handlers:
                status, out = self.handlers[name](body)
                return httpx.Response(status, json=out if status >= 400 else {"result": out})
            if name == "run_context":
                return httpx.Response(200, json={"result": {"tools": self.tools, "product": self.product}})
            if name == "run_definition":
                found = {"definition": self.definition} if self.definition else {}
                return httpx.Response(200, json={"result": found})
            if name == "budget_status":
                return httpx.Response(200, json={"result": self.budget})
            if name not in self.tools:
                return httpx.Response(403, json={"code": "forbidden", "message": f"not granted {name}"})
            return httpx.Response(200, json={"result": {"ok": True, "echo": body}})
        key = f"{request.method} {path}"
        if key in self.handlers:
            status, out = self.handlers[key](body)
            return httpx.Response(status, json=out)
        return httpx.Response(404, json={"code": "not_found", "message": "no route"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def calls_to(self, name: str) -> list[dict[str, Any]]:
        return [b for _, p, b, _ in self.requests if p.endswith(f"/{name}")]


class FakeSfn:
    def __init__(self, fail_success: bool = False, heartbeat_error: str = "TaskTimedOut") -> None:
        self.success: list[dict[str, Any]] = []
        self.failure: list[dict[str, Any]] = []
        self.heartbeats = 0
        self._fail_success = fail_success
        self._heartbeat_error = heartbeat_error

    def send_task_success(self, taskToken: str, output: str) -> None:
        if self._fail_success:
            raise RuntimeError("token closed")
        self.success.append({"token": taskToken, "output": json.loads(output)})

    def send_task_failure(self, taskToken: str, error: str, cause: str) -> None:
        self.failure.append({"token": taskToken, "error": error, "cause": cause})

    def send_task_heartbeat(self, taskToken: str) -> None:
        self.heartbeats += 1
        if self.heartbeats > 1:  # the first beat succeeds, then every one fails with the configured code
            raise ClientError({"Error": {"Code": self._heartbeat_error, "Message": "x"}}, "SendTaskHeartbeat")
