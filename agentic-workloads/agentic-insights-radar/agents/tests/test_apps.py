# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import httpx
import pytest
from botocore.credentials import Credentials
from insights_radar.modules.catalog import ModelCatalog

from insights_radar_agents import chat_app as chat_mod
from insights_radar_agents.chat_app import ChatApp
from insights_radar_agents.models import Guardrail, ModelFactory
from insights_radar_agents.module_app import ModuleApp
from insights_radar_agents.module_runtime import RunOutcome
from insights_radar_agents.settings import RuntimeSettings
from tests.fakes import FakeSfn

SETTINGS = RuntimeSettings(
    "us-east-1", "https://api.example.com", "https://gw.example.com/mcp", Guardrail("gr-1", "3"), "mem-1", "haiku-4-5"
)


class Session:
    def __init__(self) -> None:
        self.clients: list[str] = []

    def get_credentials(self) -> Credentials:
        return Credentials("AKIAEXAMPLE", "secret", "tok")

    def client(self, name: str) -> Any:
        self.clients.append(name)
        return FakeSfn()


def test_settings_from_env() -> None:
    s = RuntimeSettings.from_env(
        {
            "AWS_REGION": "eu-west-1",
            "AIR_API_URL": "https://a/",
            "AIR_GUARDRAIL_ID": "g",
            "AIR_MEMORY_ID": "m",
            "AIR_CHAT_MODEL_KEY": "opus-5-5",
        }
    )
    assert s.region == "eu-west-1" and s.api_url == "https://a" and s.guardrail == Guardrail("g", "DRAFT")
    assert s.gateway_url is None and s.memory_id == "m" and s.chat_model_key == "opus-5-5"
    blank = RuntimeSettings.from_env({})
    assert blank.region == "us-east-1" and blank.guardrail is None and blank.memory_id is None
    assert RuntimeSettings.from_env({"AWS_DEFAULT_REGION": "ap-southeast-1"}).region == "ap-southeast-1"


def test_model_factory_configures_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    model = ModelFactory("us-east-1", Guardrail("gr", "1")).build("opus-5-5", 1000)
    cfg = model.get_config()
    assert cfg["model_id"] == "global.anthropic.claude-opus-5-5" and cfg["max_tokens"] == 1000
    assert cfg["guardrail_id"] == "gr" and cfg["guardrail_latest_message"] is True and cfg["cache_config"]
    assert ModelFactory.spec("nope").key == "sonnet-5-5"
    spec = ModelCatalog.get("haiku-4-5")
    monkeypatch.setattr(
        ModelCatalog,
        "get",
        classmethod(
            lambda cls, key: spec.__class__(
                **{**{f: getattr(spec, f) for f in spec.__dataclass_fields__}, "supports_cache": False}
            )
        ),
    )
    plain = ModelFactory("us-east-1").build("haiku-4-5", 10).get_config()
    assert "cache_config" not in plain and "guardrail_id" not in plain


def test_module_app_accepts_and_reports() -> None:
    session = Session()
    sfn = FakeSfn()
    app = ModuleApp(SETTINGS, session=session, sfn=sfn)
    assert app.invoke({"run_token": "t"}) == {
        "accepted": False,
        "error": "run_token, task_token and run_id are required",
    }
    assert app.invoke("nope")["accepted"] is False  # type: ignore[arg-type]
    app.runner.run = lambda payload: RunOutcome("succeeded", "done")  # type: ignore[method-assign]
    out = app.invoke({"run_token": "t", "task_token": "TT", "run_id": "r1", "module": {"slug": "signal"}})
    assert out == {"accepted": True, "run_id": "r1"}
    for _ in range(100):
        if sfn.success:
            break
        time.sleep(0.01)
    assert sfn.success[0]["output"]["status"] == "succeeded"
    assert ModuleApp(SETTINGS, session=Session())._sfn is not None


def test_p3_089_a_retried_invoke_of_a_running_run_starts_no_second_runner() -> None:
    sfn = FakeSfn()
    app = ModuleApp(SETTINGS, session=Session(), sfn=sfn)
    release = threading.Event()
    started: list[str] = []

    def run(payload: dict[str, Any]) -> RunOutcome:
        started.append(payload["task_token"])
        release.wait(5)
        return RunOutcome("succeeded", "done")

    app.runner.run = run  # type: ignore[method-assign]
    first = app.invoke({"run_token": "t", "task_token": "TT1", "run_id": "r1"})
    for _ in range(100):
        if started:
            break
        time.sleep(0.01)
    again = app.invoke({"run_token": "t", "task_token": "TT2", "run_id": "r1"})
    assert first == {"accepted": True, "run_id": "r1"} and again["already_running"] is True
    release.set()
    for _ in range(200):
        if sfn.success:
            break
        time.sleep(0.01)
    assert started == ["TT1"] and sfn.success[0]["token"] == "TT2"  # one runner, reporting to the new token
    for _ in range(100):
        if not app._active:
            break
        time.sleep(0.01)
    assert app._active == {}


def test_chat_app_reports_usage_with_sigv4_and_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"weighted_tokens": 1})

    app = ChatApp(SETTINGS, session=Session(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    app.settle("u-1", "t1", {"input_tokens": 5})
    assert seen[0].url.path == "/agent/chat/usage" and json.loads(seen[0].content) == {
        "subject": "u-1",
        "turn_id": "t1",
        "input_tokens": 5,
    }
    captured: dict[str, Any] = {}

    class Memory:
        def __init__(self, config: Any, region_name: str) -> None:
            captured.update(config=config, region=region_name)

    monkeypatch.setattr(chat_mod, "AgentCoreMemorySessionManager", Memory)
    app.memory("u-1", "s" * 40)
    assert captured["config"].actor_id == "u-1" and captured["config"].filter_restored_tool_context is True

    async def fake_stream(payload: Any, headers: Any, session_id: Any) -> Any:
        yield {"data": f"{payload.get('prompt')}|{headers.get('authorization')}|{session_id}"}

    app.runner.stream = fake_stream  # type: ignore[method-assign]

    class Ctx:
        request_headers = {"authorization": "Bearer x"}
        session_id = "sess"

    async def collect(payload: Any, ctx: Any) -> list[Any]:
        return [f async for f in app.invoke(payload, ctx)]

    assert asyncio.run(collect({"prompt": "hi"}, Ctx())) == [{"data": "hi|Bearer x|sess"}]
    assert asyncio.run(collect("bad", None)) == [{"data": "None|None|None"}]
    no_memory = ChatApp(RuntimeSettings("us-east-1", "https://a", None, None, None, None), session=Session())
    assert no_memory.runner._session_manager is None


def _chat_app(responses: list[Any], sleeps: list[float]) -> tuple[ChatApp, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        nxt = responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ChatApp(SETTINGS, session=Session(), http=http, sleep=sleeps.append, jitter=lambda: 0.5), seen


def test_chat_app_reserves_once_and_settles_with_retries() -> None:
    sleeps: list[float] = []
    app, seen = _chat_app([httpx.Response(200, json={"reserved": 300})], sleeps)
    assert app.reserve("u-1", "t1", 500) == 300 and json.loads(seen[0].content)["reserve_tokens"] == 500
    app, _ = _chat_app([httpx.Response(503)], sleeps)
    with pytest.raises(httpx.HTTPStatusError):
        app.reserve("u-1", "t1", 500)  # no retry: the reservation may have run
    app, _ = _chat_app([httpx.ReadTimeout("slow")], sleeps)
    with pytest.raises(httpx.ReadTimeout):
        app.reserve("u-1", "t1", 500)  # sent, so it may have run: not retried either
    assert sleeps == []
    flaky = [httpx.ConnectError("down"), httpx.Response(429), httpx.Response(502), httpx.Response(200, json={})]
    app, seen = _chat_app(flaky, sleeps)
    app.settle("u-1", "t1", {"input_tokens": 5})
    assert len(seen) == 4 and sleeps == [0.5, 1.0, 2.0]


def test_chat_app_settlement_gives_up_after_its_attempts_or_on_a_client_error() -> None:
    sleeps: list[float] = []
    app, seen = _chat_app([httpx.ConnectError("down")] * 4, sleeps)
    with pytest.raises(httpx.ConnectError):
        app.settle("u-1", "t1", {})
    assert len(seen) == 4
    app, seen = _chat_app([httpx.Response(500)] * 4, sleeps)
    with pytest.raises(httpx.HTTPStatusError):
        app.settle("u-1", "t1", {})
    app, seen = _chat_app([httpx.Response(400)], sleeps)
    with pytest.raises(httpx.HTTPStatusError):
        app.settle("u-1", "t1", {})
    assert len(seen) == 1  # a rejected report is not retried


def test_http_client_loggers_never_log_request_urls() -> None:
    import logging

    from insights_radar_agents.settings import QUIET_LOGGERS, configure_logging

    configure_logging()
    for name in QUIET_LOGGERS:  # an MCP URL can carry an API key; chat URLs carry search text
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)


def test_chat_app_retries_a_reservation_that_never_reached_the_backend() -> None:
    sleeps: list[float] = []
    responses = [httpx.Response(429), httpx.ConnectError("down"), httpx.Response(200, json={"reserved": 7})]
    app, seen = _chat_app(responses, sleeps)
    assert app.reserve("u-1", "t1", 500) == 7
    assert len(seen) == 3 and sleeps == [0.5, 1.0]
    app, _ = _chat_app([httpx.Response(429)] * 3, sleeps)
    with pytest.raises(httpx.HTTPStatusError):
        app.reserve("u-1", "t1", 500)


def test_retries_are_jittered() -> None:
    sleeps: list[float] = []
    app, _ = _chat_app([httpx.Response(429), httpx.Response(200, json={})], sleeps)
    app._jitter = lambda: 1.0
    app.settle("u-1", "t1", {})
    assert sleeps == [0.75]


def test_runtimes_use_their_function_urls_signed_for_lambda() -> None:
    settings = RuntimeSettings.from_env(
        {
            "AIR_API_URL": "https://api.example.com",
            "AIR_AGENT_TOOLS_URL": "https://tools.lambda-url.us-east-1.on.aws/",
            "AIR_CHAT_USAGE_URL": "https://usage.lambda-url.us-east-1.on.aws/",
        }
    )
    assert settings.agent_tools_url == "https://tools.lambda-url.us-east-1.on.aws"
    module = ModuleApp(settings, session=Session(), sfn=FakeSfn())
    assert module._api_auth._service == "lambda"
    api = module.runner._api_factory("tok")
    assert api._base == "https://tools.lambda-url.us-east-1.on.aws"
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={})

    chat = ChatApp(settings, session=Session(), http=httpx.Client(transport=httpx.MockTransport(handler)))
    chat.settle("u-1", "t1", {})
    assert str(seen[0].url) == "https://usage.lambda-url.us-east-1.on.aws/agent/chat/usage"
    signed = ChatApp(settings, session=Session())
    assert signed._usage_client.auth._service == "lambda"  # type: ignore[union-attr]
    legacy = ModuleApp(SETTINGS, session=Session(), sfn=FakeSfn())
    assert legacy._api_auth._service == "execute-api"
