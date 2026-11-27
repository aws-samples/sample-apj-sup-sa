# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from strands.tools.tools import PythonAgentTool

from insights_radar_agents import module_runtime, tool_policy
from insights_radar_agents.models import DRAFT_MAX_TOKENS, MODULE_MAX_TOKENS
from insights_radar_agents.module_runtime import ModuleRunner, max_tokens_for
from insights_radar_agents.tool_api import ToolApi, ToolCallError
from insights_radar_agents.tool_policy import LiveDefinition, RuntimeToolPolicy, browser_refusal
from tests.fakes import FakeBackend, ScriptedModel, say, use
from tests.test_module_runtime import MODULE, StubConnectors, StubModels, httpx_auth, payload


def _browse(action: dict[str, Any]) -> dict[str, Any]:
    return {"browser_input": {"action": action}}


@pytest.mark.parametrize(
    ("action", "refused"),
    [
        ({"type": "navigate", "url": "https://example.com/blog"}, None),
        ({"type": "click", "selector": "a"}, None),
        ({"type": "navigate", "url": "http://example.com/"}, "public https"),
        ({"type": "navigate", "url": "https://169.254.169.254/latest/"}, "public https"),
        ({"type": "navigate", "url": "https://x.example/?d=" + "a" * 600}, "too long"),
        ({"type": "new_tab", "url": "https://x.example/" + "a" * 2100}, "too long"),
        # SR-04: the path and the host name have a budget too.
        ({"type": "navigate", "url": "https://x.example/" + "a" * 600}, "too long"),
        ({"type": "navigate", "url": "https://" + "a" * 60 + "." + "b" * 60 + ".attacker.example/"}, "too long"),
        ({"type": "navigate", "url": "https://x.example/" + "a" * 400 + "?q=" + "b" * 400}, None),
        ({"type": "evaluate", "script": "fetch('https://attacker.example')"}, "not allowed"),
        ({"type": "type", "selector": "input", "text": "secret"}, "not allowed"),
        ({"type": "execute_cdp", "method": "Network.enable"}, "not allowed"),
        # A3-004: a screenshot writes a file wherever the model says and the image never reaches it.
        ({"type": "screenshot", "path": "/app/.venv/lib/python3.12/site-packages/certifi/cacert.pem"}, "not allowed"),
        ({"type": "screenshot"}, "not allowed"),
    ],
)
def test_browser_actions_and_addresses(action: dict[str, Any], refused: str | None) -> None:
    reason = browser_refusal(_browse(action))
    assert (reason is None) if refused is None else (refused in str(reason))


def test_malformed_browser_input_is_left_to_the_tool() -> None:
    assert browser_refusal("nope") is None
    assert browser_refusal({"browser_input": {"action": "navigate"}}) is None


class Event:
    count = 0

    def __init__(self, name: str, tool_input: Any = None) -> None:
        Event.count += 1
        self.tool_use = {"name": name, "input": tool_input, "toolUseId": f"t{Event.count}"}
        self.cancel_tool: bool | str = False
        self.result: Any = None


def done(policy: RuntimeToolPolicy, event: Event, status: str = "success") -> Event:
    """The tool ran (unless refused) and returned ``status``."""
    event.result = {"status": "error" if event.cancel_tool else status, "content": []}
    policy.after_tool(event)  # type: ignore[arg-type]
    return event


def call(policy: RuntimeToolPolicy, name: str, tool_input: Any = None, status: str = "success") -> Event:
    event = Event(name, tool_input)
    policy.before_tool(event)  # type: ignore[arg-type]
    return done(policy, event, status)


def test_caps_runtime_side_tools_per_run() -> None:
    policy = RuntimeToolPolicy({"search": "web_search"}, lambda: {"web_search"}, caps={"web_search": 2})
    events = [Event("search") for _ in range(3)]
    for e in events:
        policy.before_tool(e)  # type: ignore[arg-type]
    assert [bool(e.cancel_tool) for e in events] == [False, False, True]
    assert "used its 2 web_search calls" in str(events[2].cancel_tool)
    assert policy.calls == {"web_search": 2} and policy.refused == {"web_search": 1}
    backend_tool = Event("kg_search")
    policy.before_tool(backend_tool)  # type: ignore[arg-type]
    assert backend_tool.cancel_tool is False  # the backend gates its own tools


def test_a_grant_removed_mid_run_stops_its_tool_within_a_minute() -> None:
    now = [0.0]
    current = [{"browser", "mcp"}]
    lookups: list[float] = []

    def grants() -> set[str]:
        lookups.append(now[0])
        if current[0] == "boom":
            raise RuntimeError("backend down")
        return set(current[0])

    policy = RuntimeToolPolicy({"browser": "browser", "mcp_x_y": "mcp"}, grants, clock=lambda: now[0])
    first = Event("mcp_x_y")
    policy.before_tool(first)  # type: ignore[arg-type]
    current[0] = {"browser"}  # an administrator removes the mcp grant
    now[0] = 30.0
    cached = Event("mcp_x_y")
    policy.before_tool(cached)  # type: ignore[arg-type]
    assert not first.cancel_tool and not cached.cancel_tool and lookups == [0.0]
    now[0] = 61.0
    late = Event("mcp_x_y")
    policy.before_tool(late)  # type: ignore[arg-type]
    assert "no longer granted mcp" in str(late.cancel_tool)
    current[0] = "boom"  # type: ignore[assignment]
    now[0] = 200.0
    still = Event("mcp_x_y")
    policy.before_tool(still)  # type: ignore[arg-type]
    assert "no longer granted" in str(still.cancel_tool)  # an unreachable backend keeps the last answer


def test_browser_navigation_is_checked_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    policy = RuntimeToolPolicy({"browser": "browser"}, lambda: {"browser"})
    caplog.set_level(logging.INFO, logger=tool_policy.__name__)
    ok = Event("browser", _browse({"type": "navigate", "url": "https://example.com/a?b=1"}))
    bad = Event("browser", _browse({"type": "navigate", "url": "https://10.0.0.1/"}))
    click = Event("browser", _browse({"type": "click", "selector": "a"}))
    for e in (ok, bad, click):
        policy.before_tool(e)  # type: ignore[arg-type]
    assert not ok.cancel_tool and bad.cancel_tool and not click.cancel_tool
    logged = [json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]
    import hashlib

    digest = hashlib.sha256(b"https://example.com/a?b=1").hexdigest()[:32]  # SR-04: the full address, hashed
    assert {"event": "browser_navigate", "host": "example.com", "path": "/a", "url_sha256": digest} in logged
    assert any(e["event"] == "tool_refused" and e["grant"] == "browser" for e in logged)


# ---- wired into a run -----------------------------------------------------------------------------


class SearchConnectors(StubConnectors):
    def web_search(self, stack: Any) -> list[Any]:
        spec = {"name": "web_search_tool", "description": "search", "inputSchema": {"json": {"type": "object"}}}

        def run(tool_use: Any, **_: Any) -> Any:
            return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": [{"text": "hits"}]}

        return [PythonAgentTool("web_search_tool", spec, run)]  # type: ignore[arg-type]


def test_a_run_caps_web_search_and_logs_its_metrics(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(tool_policy, "CAPS", {**tool_policy.CAPS, "web_search": 1})
    module = {**MODULE, "grants": [*MODULE["grants"], "web_search"]}
    backend = FakeBackend(definition=module)
    model = ScriptedModel(
        [use("web_search_tool", {}, "a"), use("web_search_tool", {}, "b"), use("kg_search", {"query": "x"}), say("ok")]
    )
    built = ModuleRunner(
        api_factory=lambda token: ToolApi("https://api.example.com", token, httpx_auth(), backend.client()),
        models=StubModels(model),
        connectors=SearchConnectors(),
    )
    caplog.set_level(logging.INFO, logger=module_runtime.__name__)
    out = built.run(payload(module=module))
    assert out.status == "succeeded"
    refused = [b for b in model.calls[2]["messages"][-1]["content"] if "toolResult" in b]
    assert "used its 1 web_search calls" in json.dumps(refused)
    (line,) = [json.loads(r.getMessage()) for r in caplog.records if '"run_metrics"' in r.getMessage()]
    assert line["module"] == "signal" and line["status"] == "succeeded"
    assert line["runtime_tool_calls"] == {"web_search": 1} and line["refused"] == {"web_search": 1}
    assert line["tool_calls"]["kg_search"] == 1 and line["model_calls"] == 4 and line["fenced"] >= 2


def test_a_failed_setup_still_logs_metrics(caplog: pytest.LogCaptureFixture) -> None:
    backend = FakeBackend(definition={})
    built = ModuleRunner(
        api_factory=lambda token: ToolApi("https://api.example.com", token, httpx_auth(), backend.client()),
        models=StubModels(ScriptedModel([])),
        connectors=StubConnectors(),
    )
    caplog.set_level(logging.INFO, logger=module_runtime.__name__)
    assert built.run(payload()).status == "failed"
    (line,) = [json.loads(r.getMessage()) for r in caplog.records if '"run_metrics"' in r.getMessage()]
    assert line["runtime_tool_calls"] == {} and line["model_calls"] == 0


def test_runtime_grants_are_read_from_the_backend_definition() -> None:
    assert module_runtime._grants_of({"definition": {"grants": ["mcp"]}}) == {"mcp"}
    assert module_runtime._grants_of({"definition": None}) == set()
    assert module_runtime._grants_of(None) == set()


def test_drafts_get_room_for_a_whole_article() -> None:
    assert max_tokens_for({"template": "draft", "model_key": "sonnet-5-5"}) == DRAFT_MAX_TOKENS
    assert max_tokens_for({"template": "draft"}) == DRAFT_MAX_TOKENS
    assert max_tokens_for({"template": "draft", "model_key": "nova-2-lite"}) == MODULE_MAX_TOKENS
    assert max_tokens_for({"template": "signal"}) == MODULE_MAX_TOKENS


def test_sr04_address_budget() -> None:
    from insights_radar_agents.tool_policy import address_budget_refusal

    assert address_budget_refusal("https://x.example/a?b=1") is None
    assert "too long" in str(address_budget_refusal("https://x.example/" + "p" * 513))
    assert address_budget_refusal("https://[::1/a") is None  # malformed: UrlGuard explains it


def test_p2003_a_tool_that_is_neither_backend_gated_nor_registered_is_refused() -> None:
    policy = RuntimeToolPolicy({"search": "web_search"}, lambda: {"web_search"})
    sneaky = Event("mystery_tool")
    policy.before_tool(sneaky)  # type: ignore[arg-type]
    assert "not a tool this run may use" in str(sneaky.cancel_tool)
    assert policy.refused == {"unknown": 1} and policy.calls == {}


def test_p2003_http_fetch_has_a_cap_and_a_live_grant_check_of_its_own() -> None:
    assert tool_policy.CAPS["web_fetch"] > 0
    revoked = RuntimeToolPolicy({"http_fetch": "web_fetch"}, set)
    gone = Event("http_fetch", {"url": "https://a.example.com/x"})
    revoked.before_tool(gone)  # type: ignore[arg-type]
    assert "no longer granted web_fetch" in str(gone.cancel_tool)
    capped = RuntimeToolPolicy({"http_fetch": "web_fetch"}, lambda: {"web_fetch"}, caps={"web_fetch": 1})
    events = [Event("http_fetch", {"url": "https://a.example.com/x"}) for _ in range(2)]
    for e in events:
        capped.before_tool(e)  # type: ignore[arg-type]
    assert [bool(e.cancel_tool) for e in events] == [False, True]
    assert "used its 1 web_fetch calls" in str(events[1].cancel_tool)


def test_p2021_a_refused_grant_recheck_revokes_every_runtime_side_grant() -> None:
    now = [0.0]
    answer: list[Any] = [ToolCallError(404, "not_found", "the module is gone")]

    def grants() -> set[str]:
        if isinstance(answer[0], Exception):
            raise answer[0]
        return set(answer[0])

    def click(policy: RuntimeToolPolicy) -> Event:
        event = Event("browser", _browse({"type": "click", "selector": "a"}))
        policy.before_tool(event)  # type: ignore[arg-type]
        return event

    policy = RuntimeToolPolicy({"browser": "browser"}, grants, clock=lambda: now[0])
    assert "no longer granted browser" in str(click(policy).cancel_tool)
    answer[0] = ToolCallError(503, "run_finished", "run r1 has ended")
    now[0] = 60.0
    assert "no longer granted browser" in str(click(policy).cancel_tool)
    answer[0] = {"browser"}  # the backend answers again
    now[0] = 120.0
    assert not click(policy).cancel_tool


def test_p2021_repeated_recheck_failures_fail_closed() -> None:
    now = [0.0]
    answer: list[Any] = [{"browser"}]

    def grants() -> set[str]:
        if isinstance(answer[0], Exception):
            raise answer[0]
        return set(answer[0])

    policy = RuntimeToolPolicy({"browser": "browser"}, grants, clock=lambda: now[0])
    answer[0] = ToolCallError(500, "error", "backend down")  # not a refusal: a transient failure
    refusals = []
    for attempt in range(tool_policy.MAX_RECHECK_ERRORS):
        now[0] = 60.0 * (attempt + 1)
        event = Event("browser", _browse({"type": "click", "selector": "a"}))
        policy.before_tool(event)  # type: ignore[arg-type]
        refusals.append(bool(event.cancel_tool))
    assert refusals == [False] * (tool_policy.MAX_RECHECK_ERRORS - 1) + [True]


def _ci(action: dict[str, Any]) -> dict[str, Any]:
    return {"code_interpreter_input": {"action": action}}


def test_p2023_a_run_may_have_only_a_couple_of_sessions_open() -> None:
    """Each AgentCore Browser or Code Interpreter session is billed per minute and counts against the
    account's concurrent-session quota, and a model retrying pages can call init_session dozens of
    times (P2-023)."""
    now = [0.0]
    policy = RuntimeToolPolicy({"browser": "browser"}, lambda: {"browser"}, clock=lambda: now[0])
    opened = [call(policy, "browser", _browse({"type": "init_session", "session_name": f"s{i}"})) for i in range(3)]
    assert [bool(e.cancel_tool) for e in opened] == [False] * tool_policy.MAX_OPEN_SESSIONS + [True]
    assert "sessions open" in str(opened[-1].cancel_tool)
    assert not call(policy, "browser", _browse({"type": "get_text", "session_name": "s0"})).cancel_tool
    # Closing one frees its slot.
    call(policy, "browser", _browse({"type": "close", "session_name": "s0"}))
    assert not call(policy, "browser", _browse({"type": "init_session", "session_name": "s3"})).cancel_tool


def test_a3_027_an_expired_session_frees_its_slot_and_the_run_total_is_capped() -> None:
    now = [0.0]
    policy = RuntimeToolPolicy({"browser": "browser"}, lambda: {"browser"}, clock=lambda: now[0])
    refused = []
    for i in range(tool_policy.MAX_SESSIONS_PER_RUN + 1):
        now[0] = 1000.0 * i  # each earlier session has passed its lifetime
        event = call(policy, "browser", _browse({"type": "init_session", "session_name": f"s{i}"}))
        refused.append(bool(event.cancel_tool))
    assert refused == [False] * tool_policy.MAX_SESSIONS_PER_RUN + [True]
    assert tool_policy.SESSION_SECONDS * tool_policy.MAX_SESSIONS_PER_RUN >= 2 * 3600  # a long Scout run


def test_p3_062_a_session_counts_only_once_the_call_that_opens_it_succeeded() -> None:
    policy = RuntimeToolPolicy({"browser": "browser"}, lambda: {"browser"})
    for name in ("main", "session1", "x"):  # the tool rejects each name as invalid
        assert not call(policy, "browser", _browse({"type": "init_session", "session_name": name}), "error").cancel_tool
    assert not call(policy, "browser", _browse({"type": "init_session", "session_name": "good-session-1"})).cancel_tool
    # Two pending openings already fill the open-session limit.
    one, two = (Event("browser", _browse({"type": "init_session", "session_name": n})) for n in ("p-1", "p-2"))
    policy.before_tool(one)  # type: ignore[arg-type]
    policy.before_tool(two)  # type: ignore[arg-type]
    assert not one.cancel_tool and "sessions open" in str(two.cancel_tool)


def test_p3_015_the_code_interpreter_opens_a_session_for_init_and_for_every_new_session_name() -> None:
    policy = RuntimeToolPolicy({"ci": "code_interpreter"}, lambda: {"code_interpreter"})
    assert not call(policy, "ci", _ci({"type": "initSession", "description": "d", "session_name": "a"})).cancel_tool
    assert not call(policy, "ci", _ci({"type": "executeCode", "session_name": "a", "code": "1"})).cancel_tool
    assert not call(policy, "ci", _ci({"type": "executeCode", "session_name": "b", "code": "1"})).cancel_tool
    third = call(policy, "ci", _ci({"type": "executeCode", "session_name": "c", "code": "1"}))
    assert "sessions open" in str(third.cancel_tool)  # executeCode on a new name would start a third
    assert not call(policy, "ci", _ci({"type": "listLocalSessions"})).cancel_tool
    assert tool_policy._starts_a_session("code_interpreter", _ci({"type": "initSession"}))


def test_p2023_only_a_real_init_session_action_counts_as_a_session() -> None:
    assert not tool_policy._starts_a_session("web_search", {"query": "x"})
    assert not tool_policy._starts_a_session("browser", "not a dict")
    assert not tool_policy._starts_a_session("browser", {"browser_input": "nope"})
    assert not tool_policy._starts_a_session("browser", {"browser_input": {"action": "nope"}})
    assert tool_policy._starts_a_session("browser", _browse({"type": "init_session"}))


def test_a3_008_backend_writes_have_a_per_run_cap(caplog: pytest.LogCaptureFixture) -> None:
    policy = RuntimeToolPolicy({}, set)
    cap = tool_policy.WRITE_CAPS["kg_add_evidence"]
    events = [call(policy, "kg_add_evidence", {}) for _ in range(cap + 2)]
    assert [bool(e.cancel_tool) for e in events] == [False] * cap + [True, True]
    assert f"made its {cap} kg_add_evidence calls" in str(events[-1].cancel_tool)
    assert policy.writes == {"kg_add_evidence": cap} and policy.refused == {"write": 2}
    assert caplog.text.count("write_cap_reached") == 1  # logged once, not per refusal
    assert not call(policy, "kg_search", {}).cancel_tool  # reads are not capped here


def test_a3_023_a_refused_backend_tool_is_refused_whatever_the_grants() -> None:
    policy = RuntimeToolPolicy({}, set, refused_tools={"kg_update_output": "drafts change only with draft_save"})
    assert "drafts change only with draft_save" in str(call(policy, "kg_update_output", {}).cancel_tool)
    assert not call(policy, "draft_save", {}).cancel_tool


def test_a3_007_a_removed_mcp_server_stops_its_tools_while_the_grant_stays() -> None:
    now = [0.0]
    live = [LiveDefinition({"mcp"}, {"https://a.example.com/mcp", "https://b.example.com/mcp"})]
    policy = RuntimeToolPolicy(
        {"mcp_a_x": "mcp", "mcp_b_y": "mcp"},
        lambda: live[0],
        clock=lambda: now[0],
        server_of={"mcp_a_x": "https://a.example.com/mcp", "mcp_b_y": "https://b.example.com/mcp"},
    )
    assert not call(policy, "mcp_a_x").cancel_tool and not call(policy, "mcp_b_y").cancel_tool
    live[0] = LiveDefinition({"mcp"}, {"https://b.example.com/mcp"})  # an administrator removes server a
    now[0] = 61.0
    assert "no longer attached" in str(call(policy, "mcp_a_x").cancel_tool)
    assert not call(policy, "mcp_b_y").cancel_tool
    # A definition that does not list servers leaves the last answer as it is.
    live[0] = LiveDefinition({"mcp"})
    now[0] = 130.0
    assert "no longer attached" in str(call(policy, "mcp_a_x").cancel_tool)


def test_a3_007_the_live_definition_carries_the_module_s_mcp_servers() -> None:
    definition = {"definition": {"grants": ["mcp"], "mcp_servers": [{"url": "https://a.example.com/mcp"}, "x"]}}
    live = module_runtime._live_of(definition)
    assert live.grants == {"mcp"} and live.mcp_servers == {"https://a.example.com/mcp"}
    assert module_runtime._live_of({"definition": {"grants": []}}).mcp_servers == set()
    assert module_runtime._live_of(None).grants == set()


class GatewaySearchConnectors(StubConnectors):
    """Web search the way the real connector builds it: wrapped by ``_untrusted_tools`` with no server."""

    def web_search(self, stack: Any) -> list[Any]:
        from insights_radar_agents.connectors import _untrusted_tools

        spec = {"name": "web_search_tool", "description": "search", "inputSchema": {"json": {"type": "object"}}}

        def run(tool_use: Any, **_: Any) -> Any:
            return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": [{"text": "hits"}]}

        return _untrusted_tools("web search", [PythonAgentTool("web_search_tool", spec, run)])  # type: ignore[arg-type]


def test_cr3_02_gateway_web_search_survives_the_live_re_check() -> None:
    module = {**MODULE, "grants": [*MODULE["grants"], "web_search"]}
    backend = FakeBackend(definition=module)
    model = ScriptedModel(
        [use("web_search_tool", {"query": "x"}, "a"), use("web_search_tool", {"query": "y"}, "b"), say("ok")]
    )
    built = ModuleRunner(
        api_factory=lambda token: ToolApi("https://api.example.com", token, httpx_auth(), backend.client()),
        models=StubModels(model),
        connectors=GatewaySearchConnectors(),
    )
    out = built.run(payload(module=module))
    assert out.status == "succeeded"
    results = json.dumps([m["content"] for m in model.calls[1]["messages"] + model.calls[2]["messages"]])
    assert "no longer attached" not in results and "hits" in results


def test_cr3_02_a_tool_with_an_empty_server_is_never_a_removed_server() -> None:
    policy = RuntimeToolPolicy(
        {"web_search_tool": "web_search"},
        lambda: LiveDefinition({"web_search"}, set()),
        server_of={"web_search_tool": ""},
    )
    assert not call(policy, "web_search_tool").cancel_tool
    assert not call(policy, "web_search_tool").cancel_tool
