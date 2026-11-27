# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import asyncio
import base64
import json
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from insights_radar_agents.chat_runtime import (
    CHAT_CALL_RESERVE,
    CHAT_TURN_RESERVE,
    SYSTEM_PROMPT,
    ChatRunner,
    ChatToolError,
    UserApi,
    _node_path,
    chat_tools,
    subject_of,
)
from insights_radar_agents.models import CHAT_MAX_TOKENS, ModelFactory
from insights_radar_agents.module_runtime import RunGuard
from tests.fakes import FakeBackend, ScriptedModel, say, use


def jwt(claims: dict[str, Any]) -> str:
    def part(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"Bearer {part({'alg': 'RS256'})}.{part(claims)}.sig"


TOKEN = jwt({"sub": "u-1", "email": "viewer@example.com"})
OPEN = {
    "genai_enabled": True,
    "allowed": True,
    "shared": {"state": "open", "remaining": 1_000_000},
    "user": {"state": "open", "remaining": 200_000},
}


class Models(ModelFactory):
    def __init__(self, model: ScriptedModel) -> None:
        super().__init__("us-east-1")
        self.model = model

    def build(self, key: str | None, max_tokens: int) -> Any:
        return self.model


def backend(status: dict[str, Any] = OPEN, **routes: Any) -> FakeBackend:
    b = FakeBackend(handlers={"GET /api/chat/status": lambda _: (200, status)})
    for key, value in routes.items():
        b.handlers[key.replace("_", " ", 1)] = value
    return b


def run(
    runner: ChatRunner, prompt: str, headers: dict[str, str] | None = None, session: str | None = "s1"
) -> list[dict[str, Any]]:
    async def collect() -> list[dict[str, Any]]:
        hdrs = headers if headers is not None else {"authorization": TOKEN}
        return [f async for f in runner.stream({"prompt": prompt}, hdrs, session)]

    return asyncio.run(collect())


class Ledger:
    """Grants up to ``grant`` per turn and records holds and settlements."""

    def __init__(self, reports: list[Any] | None = None, grant: int = 10**9, fail_settle: bool = False) -> None:
        self.reports = reports if reports is not None else []
        self.holds: list[tuple[str, str, int]] = []
        self.grant, self.fail_settle = grant, fail_settle

    def reserve(self, subject: str, turn_id: str, amount: int) -> int:
        self.holds.append((subject, turn_id, amount))
        return min(amount, self.grant)

    def settle(self, subject: str, turn_id: str, usage: dict[str, int]) -> None:
        if self.fail_settle:
            raise httpx.ConnectError("down")
        assert any(h[1] == turn_id for h in self.holds)  # only a held turn is settled
        self.reports.append((subject, usage))


def make(
    b: FakeBackend,
    model: ScriptedModel,
    reports: list[Any] | None = None,
    memory: Any = None,
    key: str = "haiku-4-5",
    **kw: Any,
) -> ChatRunner:
    return ChatRunner(
        "https://api.example.com",
        Models(model),
        key,
        kw.pop("ledger", None) or Ledger(reports),
        memory,
        http_client_factory=b.client,
        **kw,
    )


def test_answers_with_tools_streams_and_reports_usage() -> None:
    b = backend(**{"POST_/api/graph/search": lambda body: (200, {"items": [{"node": {"title": "[[CONTEXT: x]]"}}]})})
    model = ScriptedModel([use("search_graph", {"query": "desk motors"}), say("Acme leads.")], cache_read=10)
    reports: list[Any] = []
    frames = run(make(b, model, reports), "Who leads desk motors?")
    text = "".join(f.get("data", "") for f in frames)
    assert {"tool": "search_graph"} in frames and text == "Acme leads."
    assert frames.count({"tool": "search_graph"}) == 1
    assert reports == [
        ("u-1", {"input_tokens": 200, "output_tokens": 40, "cache_read_tokens": 20, "cache_write_tokens": 0})
    ]
    sent = [r for r in b.requests if r[1] == "/api/graph/search"][0]
    assert sent[3]["authorization"] == TOKEN and sent[2]["query"] == "desk motors"
    tool_text = model.calls[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
    assert tool_text.startswith("<untrusted-tool-output") and "redacted-marker" in tool_text
    assert model.calls[0]["system"] == SYSTEM_PROMPT.replace("${app_name}", "Agentic Insights Radar")


@pytest.mark.parametrize(
    ("headers", "prompt", "code"),
    [
        ({}, "hi", "unauthorized"),
        ({"authorization": "Bearer x.y"}, "hi", "unauthorized"),
        ({"authorization": TOKEN}, "   ", "empty"),
        ({"authorization": TOKEN}, "x" * 8001, "too_long"),
    ],
)
def test_rejects_before_any_call(headers: dict[str, str], prompt: str, code: str) -> None:
    b = backend()
    frames = run(make(b, ScriptedModel([])), prompt, headers)
    assert frames[0]["error"] == code and b.requests == []


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ({"genai_enabled": False, "allowed": True}, "genai_disabled"),
        ({"genai_enabled": True, "allowed": False}, "budget_exhausted"),
    ],
)
def test_gates(status: dict[str, Any], code: str) -> None:
    model = ScriptedModel([say("never")])
    frames = run(make(backend(status), model), "hi")
    assert frames == [{"error": code, "message": frames[0]["message"]}] and model.calls == []


def test_status_failure_and_model_failure_are_reported() -> None:
    b = FakeBackend(handlers={"GET /api/chat/status": lambda _: (401, {"message": "expired"})})
    assert run(make(b, ScriptedModel([])), "hi")[0]["error"] == "failed"
    reports: list[Any] = []
    crash = ScriptedModel([use("list_radars", {}), {"raise": RuntimeError("boom")}])
    frames = run(make(backend(), crash, reports), "hi")
    assert frames[-1]["error"] == "failed" and reports and reports[0][1]["input_tokens"] == 100


def test_usage_report_failure_does_not_break_the_turn() -> None:
    assert run(make(backend(), ScriptedModel([say("ok")]), ledger=Ledger(fail_settle=True)), "hi")[-1] == {"data": "ok"}


def test_each_turn_holds_its_ceiling_and_a_refused_hold_ends_the_turn() -> None:
    ledger = Ledger()
    small = {**OPEN, "user": {"state": "warn", "remaining": 50_000}}
    run(make(backend(small), ScriptedModel([say("ok")]), ledger=ledger), "hi")
    ((subject, turn_id, amount),) = ledger.holds
    assert subject == "u-1" and len(turn_id) == 32 and amount == 50_000  # the smaller budget left
    rich = {**OPEN, "user": {"state": "open", "remaining": 900_000}}
    capped = Ledger()
    run(make(backend(rich), ScriptedModel([say("ok")]), ledger=capped), "hi")
    assert capped.holds[0][2] == CHAT_TURN_RESERVE
    model = ScriptedModel([say("never")])
    refused = Ledger(grant=0)
    frames = run(make(backend(), model, ledger=refused), "hi")
    assert frames[0]["error"] == "budget_exhausted" and model.calls == [] and refused.reports == []
    drained = {**OPEN, "user": {"state": "open", "remaining": 0}}
    untouched = Ledger()
    frames = run(make(backend(drained), model, ledger=untouched), "hi")
    assert frames[0]["error"] == "budget_exhausted" and untouched.holds == []
    # A hold too small for even one answer ends the turn, and is given back.
    stingy = Ledger(grant=CHAT_CALL_RESERVE - 1)
    frames = run(make(backend(), model, ledger=stingy), "hi")
    assert frames[0]["error"] == "budget_exhausted" and model.calls == []
    assert len(stingy.reports) == 1 and not any(stingy.reports[0][1].values())


def test_a_lost_reserve_reply_gives_any_hold_back() -> None:
    class Flaky(Ledger):
        def reserve(self, subject: str, turn_id: str, amount: int) -> int:
            self.holds.append((subject, turn_id, amount))
            raise httpx.ReadTimeout("slow")

    flaky = Flaky()
    frames = run(make(backend(), ScriptedModel([say("never")]), ledger=flaky), "hi")
    assert frames[-1]["error"] == "failed"
    assert len(flaky.reports) == 1 and not any(flaky.reports[0][1].values())


def test_a_client_that_leaves_mid_stream_is_charged_a_full_answer() -> None:
    reports: list[Any] = []
    runner = make(backend(), ScriptedModel([say("a long answer")]), reports)

    async def leave_early() -> None:
        stream = runner.stream({"prompt": "hi"}, {"authorization": TOKEN}, "s1")
        async for frame in stream:
            if "data" in frame:
                break
        await stream.aclose()  # what a disconnect does to the generator

    asyncio.run(leave_early())
    assert len(reports) == 1 and reports[0][1]["output_tokens"] >= CHAT_MAX_TOKENS


def test_an_unused_hold_is_settled_when_the_agent_cannot_be_built() -> None:
    reports: list[Any] = []

    def no_agent(**kwargs: Any) -> Any:
        raise RuntimeError("bad model id")

    assert run(make(backend(), ScriptedModel([]), reports, agent_builder=no_agent), "hi")[-1]["error"] == "failed"
    zero = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    assert reports == [("u-1", zero)]


def test_memory_is_keyed_by_verified_subject_and_session() -> None:
    seen: list[tuple[str, str]] = []

    def memory(sub: str, session: str) -> Any:
        seen.append((sub, session))
        return None

    run(make(backend(), ScriptedModel([say("ok")]), memory=memory), "hi")
    run(make(backend(), ScriptedModel([say("ok")]), memory=memory), "hi", session=None)
    assert seen == [("u-1", "s1")]


def test_subject_parsing() -> None:
    assert subject_of({"Authorization": TOKEN}) == "u-1"
    assert subject_of({"authorization": "Basic abc"}) is None
    assert subject_of({"authorization": "Bearer a.!!!.c"}) is None
    assert subject_of({"authorization": jwt({"email": "x"})}) is None
    bad = "Bearer a." + base64.urlsafe_b64encode(b"[1]").decode().rstrip("=") + ".c"
    assert subject_of({"authorization": bad}) is None


def _tools(b: FakeBackend) -> dict[str, Any]:
    return {t.tool_name: t for t in chat_tools(UserApi("https://api.example.com", TOKEN, b.client()))}


def call(tools: dict[str, Any], name: str, args: Any) -> Any:
    return tools[name]._tool_func({"toolUseId": "1", "name": name, "input": args})


def test_every_chat_tool_hits_its_route() -> None:
    hits: list[str] = []

    def ok(key: str) -> Any:
        return lambda body: (hits.append(key), (200, {"ok": True}))[1]

    routes = [
        "GET /api/graph/nodes/signal/abc",
        "GET /api/graph/nodes/signal/abc/neighbors",
        "GET /api/insights/insight:competitor_move",
        "GET /api/insights/nodes/gap/g1",
        "GET /api/radars",
        "GET /api/radars/my-radar",
        "GET /api/products",
        "POST /api/products/p1/ask",
        "GET /api/modules",
        "POST /api/graph/search",
    ]
    b = FakeBackend(handlers={r: ok(r) for r in routes})
    t = _tools(b)
    assert call(t, "get_node", {"id": "signal/abc"})["status"] == "success"
    call(t, "node_neighbors", {"id": "signal/abc", "depth": 9})
    call(t, "list_insights", {"kind": "insight:competitor_move", "sort": "relevance", "q": "x"})
    call(t, "insight_detail", {"id": "gap/g1"})
    call(t, "list_radars", {})
    call(t, "get_radar", {"slug": "my-radar"})
    call(t, "list_products", {})
    call(t, "ask_product", {"product_id": "p1", "query": "usb"})
    call(t, "list_modules", "not a dict")
    call(t, "search_graph", {"query": "q"})
    assert hits == routes[:6] + routes[6:]
    neighbors = [r for r in b.requests if r[1].endswith("/neighbors")][0]
    assert neighbors[0] == "GET"


def test_chat_tool_errors() -> None:
    b = FakeBackend(
        handlers={"GET /api/radars": lambda _: (403, {"message": "nope"}), "GET /api/products": lambda _: (500, ["x"])}
    )
    t = _tools(b)
    assert "403: nope" in call(t, "list_radars", {})["content"][0]["text"]
    assert "500: request failed" in call(t, "list_products", {})["content"][0]["text"]
    assert "<type>/<id>" in call(t, "get_node", {"id": "nope"})["content"][0]["text"]

    def boom(_: Any) -> Any:
        raise httpx.ReadTimeout("slow")

    b.handlers["GET /api/modules"] = boom
    assert call(t, "list_modules", {})["content"][0]["text"] == "list_modules is unavailable right now"
    b.handlers["GET /api/products"] = lambda _: (200, "not json obj")
    with pytest.raises(ChatToolError):
        _node_path("/x")


def test_non_json_error_body() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(502, text="<html>bad</html>")))
    with pytest.raises(ChatToolError, match="502: <html>bad</html>"):
        UserApi("https://api.example.com", TOKEN, client).request("GET", "/api/radars")


def test_failure_before_any_usage_gives_the_whole_hold_back() -> None:
    reports: list[Any] = []
    frames = run(make(backend(), ScriptedModel([{"raise": RuntimeError("cold start")}]), reports), "hi")
    assert frames[-1]["error"] == "failed" and len(reports) == 1 and not any(reports[0][1].values())


def test_one_turn_cannot_spend_more_than_the_budget_left(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RunGuard, "estimate_input", staticmethod(lambda agent: 10))
    tight = {**OPEN, "user": {"state": "warn", "remaining": CHAT_CALL_RESERVE + 10 + 150}}
    search = {"POST_/api/graph/search": lambda body: (200, {"items": []})}
    model = ScriptedModel([use("search_graph", {"query": "a"}, usage=(100, 20)), use("search_graph", {"query": "b"})])
    reports: list[Any] = []
    frames = run(make(backend(tight, **search), model, reports, key="sonnet-5-5"), "Keep searching")
    assert len(model.calls) == 1  # the second model call is never made
    assert reports and reports[0][1]["input_tokens"] == 100
    assert not any("error" in f for f in frames)
    assert any("within today's chat budget" in f.get("data", "") for f in frames)  # not a silent stop


def test_one_turn_is_capped_in_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    import insights_radar_agents.chat_runtime as chat

    monkeypatch.setattr(chat, "MAX_TURN_MODEL_CALLS", 2)
    search = {"POST_/api/graph/search": lambda body: (200, {"items": []})}
    model = ScriptedModel([use("search_graph", {"query": str(i)}) for i in range(5)])
    run(make(backend(**search), model), "Loop forever")
    assert len(model.calls) == 2


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_node", {"node_id": "../explore"}),
        ("get_node", {"node_id": "signal/.."}),
        ("get_node", {"node_id": "Signal/1"}),
        ("list_insights", {"kind": ".."}),
        ("list_insights", {"kind": "insight:../x"}),
        ("get_radar", {"slug": ".."}),
        ("get_radar", {"slug": "a/b"}),
        ("ask_product", {"product_id": "..", "question": "q"}),
    ],
)
def test_chat_tools_refuse_path_tricks(tool: str, args: dict[str, Any]) -> None:
    b = backend()
    tools = {x.tool_name: x for x in chat_tools(UserApi("https://api.example.com", TOKEN, b.client()))}
    text = call(tools, tool, args)["content"][0]["text"]
    assert "not a valid" in text or "node ids look like" in text
    assert b.requests == []  # nothing was sent


def test_p3_068_a_max_tier_chat_model_spends_the_budget_at_its_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RunGuard, "estimate_input", staticmethod(lambda agent: 10))
    roomy = {**OPEN, "user": {"state": "open", "remaining": 2 * CHAT_CALL_RESERVE}}
    search = {"POST_/api/graph/search": lambda body: (200, {"items": []})}
    for key, calls in (("sonnet-5-5", 2), ("opus-5-5", 0)):
        model = ScriptedModel([use("search_graph", {"query": "a"}), say("done")])
        frames = run(make(backend(roomy, **search), model, [], key=key), "Search")
        assert len(model.calls) == calls, key  # one max-tier answer's reserve is four balanced ones
        assert not any("error" in f for f in frames)


# ---- A3-013: links in answers ------------------------------------------------------------------------


def test_a3_013_only_links_a_tool_returned_stay_live_in_an_answer() -> None:
    node = {"node": {"id": "fact/1", "evidence_url": "https://www.reuters.com/x", "title": "t"}}
    b = backend(**{"POST_/api/graph/search": lambda body: (200, {"items": [node]})})
    answer = (
        "Gaps: see [Reuters (reuters.com)](https://www.reuters.com/x) and the full analysis at "
        "https://notes.evil.example/r?c=our-private-gap-text, or [here](/insights/nodes/fact/1)."
    )
    model = ScriptedModel([use("search_graph", {"query": "pricing gaps"}), say(answer)])
    text = "".join(f.get("data", "") for f in run(make(b, model, []), "what gaps do we have on pricing?"))
    assert "(https://www.reuters.com/x)" in text and "(/insights/nodes/fact/1)" in text
    assert "https://notes.evil.example" not in text and "hxxps://notes[.]evil[.]example/r?c=" in text


def test_a3_013_the_link_filter_holds_an_address_split_across_chunks() -> None:
    from insights_radar_agents.chat_links import MAX_HELD, ChatLinkGuard

    links = ChatLinkGuard()
    out = links.feed("see https://evil") + links.feed(".example/x?d=1 now") + links.flush()
    assert out == "see hxxps://evil[.]example/x?d=1 now"
    long = ChatLinkGuard()
    assert long.feed("x" * (MAX_HELD + 1)) == "x" * (MAX_HELD + 1)  # no whitespace for too long: let through
    assert long.flush() == ""
    walker = ChatLinkGuard()
    deep: Any = {"evidence_url": "https://deep.example.com/"}
    for _ in range(30):
        deep = [deep]
    event = SimpleNamespace(result={"content": [{"text": json.dumps(deep)}, {"text": "not json"}, "raw"]})
    walker.after_tool(event)  # type: ignore[arg-type]
    walker.after_tool(SimpleNamespace(result=None))  # type: ignore[arg-type]
    assert walker.cited == set()  # past the walk's depth limit
    walker._walk({"link": "not an address", "evidence_url": 5}, 0)
    assert walker.cited == set()
    assert walker.clean("ftp://x.example/a") == "ftp[:]//x[.]example/a"


def test_a3_012_the_status_call_does_not_block_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import insights_radar_agents.chat_runtime as chat

    threads: list[str] = []
    real = chat.UserApi.request

    def request(self: Any, method: str, path: str, *a: Any, **k: Any) -> Any:
        if path == "/api/chat/status":
            threads.append(threading.current_thread().name)
        return real(self, method, path, *a, **k)

    monkeypatch.setattr(chat.UserApi, "request", request)
    run(make(backend(), ScriptedModel([say("hi")]), []), "hello")
    assert threads and threads[0] != threading.main_thread().name


def test_a3_013_text_before_a_tool_call_is_sent_before_the_tool_frame() -> None:
    b = backend(**{"POST_/api/graph/search": lambda body: (200, {"items": []})})
    first = {**use("search_graph", {"query": "a"}), "preamble": "Checking https://evil.example/x"}
    frames = run(make(b, ScriptedModel([first, say("none")]), []), "q")
    tool_at = frames.index({"tool": "search_graph"})
    before_tool = "".join(f.get("data", "") for f in frames[:tool_at])
    assert before_tool == "Checking hxxps://evil[.]example/x"  # all of it, checked, before the tool frame


def test_sr3_03_and_sr3_04_chat_links_with_userinfo_or_an_invisible_first_character_are_defanged() -> None:
    from insights_radar_agents.chat_links import ChatLinkGuard

    links = ChatLinkGuard()
    links.cited.add("https://notes.evil.example/r")
    out = links.clean(
        "[a](https://Gap-text:x@notes.evil.example/r) [b](https://\u00adevil.com/gap) https://notes.evil.example/r"
    )
    assert "(https://Gap-text" not in out and "(https://\u00adevil" not in out
    assert out.count("hxxps://") == 2 and out.endswith("https://notes.evil.example/r")


def test_sr3_05_a_mailto_link_in_an_answer_is_defanged() -> None:
    from insights_radar_agents.chat_links import ChatLinkGuard

    links = ChatLinkGuard()
    out = links.clean("[r](mailto:SECRET@evil.example) ok")
    assert "mailto:" not in out and "mailto[:]SECRET@evil[.]example" in out and links.defanged == 1


# ---- port of CTR QA-070 (7a72c5f0, ca800701): radar totals come from the tool, not the model -------------


def test_qa070_get_radar_returns_totals_and_short_descriptions() -> None:
    rings = ["Adopt", "Trial", "Assess", "Hold"]
    entries = [
        {
            "key": f"e{i}",
            "title": f"Entry {i}",
            "ring": rings[i % 4],
            "sector": "Tools",
            "marker": "",
            "description": "x" * 4000,
        }
        for i in range(300)
    ]
    body = {"radar": {"slug": "big"}, "entries": entries, "schema": {}}
    b = FakeBackend(handlers={"GET /api/radars/big": lambda _: (200, body)})
    out = call(_tools(b), "get_radar", {"slug": "big"})
    text = out["content"][0]["text"]
    assert out["status"] == "success" and "[truncated" not in text  # the whole digest fits
    parsed = json.loads(text)
    assert parsed["totals"] == {
        "entries": 300,
        "by_ring": dict.fromkeys(rings, 75),
        "by_sector": {"Tools": 300},
        "by_marker": {},
    }
    assert len(parsed["entries"]) == 300 and parsed["entries"][0]["description"] == "x" * 200 + "..."


def test_qa070_a_result_cut_at_the_limit_says_so() -> None:
    from insights_radar_agents.tool_api import MAX_RESULT_CHARS

    b = FakeBackend(handlers={"GET /api/radars": lambda _: (200, {"items": ["y" * (MAX_RESULT_CHARS + 50)]})})
    text = call(_tools(b), "list_radars", {})["content"][0]["text"]
    assert text.endswith("more characters not shown]") and "[truncated: " in text


def test_qa070_an_unexpected_radar_body_passes_through() -> None:
    from insights_radar_agents.chat_runtime import radar_digest

    assert radar_digest(["not", "a", "radar"]) == ["not", "a", "radar"]
    assert (
        radar_digest({"radar": {}, "entries": [{"ring": "", "description": "short"}, "junk"]})["totals"]["entries"] == 1
    )


def test_the_assistant_speaks_the_deployment_profile() -> None:
    from insights_radar_agents.chat_runtime import deployment_prompt

    profile = {
        "app_name": "Example Radar <script>",
        "lists": {
            "signals": {"label": "Regulatory changes", "item": "Regulatory change", "visible": True},
            "gaps": {"label": "Gaps", "item": "Gap", "visible": False},
            "trends": {"label": "Themes", "item": ""},
            "bogus kind": {"label": "x", "item": "y"},
            "drafts": "not an object",
        },
    }
    prompt = deployment_prompt(profile)
    assert prompt.startswith("You are the Example Radar script assistant.")
    assert '- the signals list is called "Regulatory changes"; one entry in it is a "Regulatory change"' in prompt
    assert "- not used in this deployment: gaps; do not offer them" in prompt
    assert "Themes" not in prompt and "bogus" not in prompt
    # No profile, or one with nothing to say: the plain prompt under the default name.
    assert deployment_prompt(None) == SYSTEM_PROMPT.replace("${app_name}", "Agentic Insights Radar")
    assert deployment_prompt({"app_name": "", "lists": []}) == deployment_prompt(None)
    hidden_only = deployment_prompt({"lists": {"content": {"visible": False}}})
    assert "not used in this deployment: content" in hidden_only and "is called" not in hidden_only
    sections = deployment_prompt({"sections": {"radars": False, "products": True, "bad key!": False}, "lists": {}})
    assert "- not used in this deployment: radars; do not offer them" in sections and "bad key" not in sections
    assert deployment_prompt({"sections": ["radars"]}) == deployment_prompt(None)
    shown_only = deployment_prompt({"lists": {"trends": {"label": "Themes", "item": "Theme"}}})
    assert '"Themes"' in shown_only and "not used" not in shown_only
