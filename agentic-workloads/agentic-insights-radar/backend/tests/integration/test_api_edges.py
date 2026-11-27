# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Rejection paths of the HTTP API and agent tools, one case per rule."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from insights_radar.api.app import Application
from insights_radar.core.clock import FixedClock
from insights_radar.graph.indexer import StreamIndexer
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.graph.search import HashEmbedder
from insights_radar.modules.model import ModuleTemplate, ToolGrant
from tests.integration.harness import Client, TestContext, ensure_running

URL = "https://news.example.com/a"


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> TestContext:
    return TestContext(graph_table, ops_table, clock)


@pytest.fixture
def api(ctx: TestContext) -> Client:
    return Client(Application(ctx))


def _tok(ctx: TestContext, slug: str, product: str | None = None, tools: frozenset[str] | None = None) -> str:
    ensure_running(ctx, slug)
    return ctx.tokens.issue(slug, "run-1", tools if tools is not None else ctx.modules.get(slug).tools, 600, product)


@pytest.fixture
def nodes(ctx: TestContext) -> dict[str, Node]:
    org, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    fact = ctx.graph.create(Node(id="", type=NodeType.FACT, title="Acme ships", evidence_url=URL), "u")
    sig = ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="Motors", module="signal"), "module:signal")
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Idea",
            module="content",
            attributes={"content_type": "blog", "audience": "buyers"},
        ),
        "module:content",
    )
    return {"org": org, "fact": fact, "signal": sig, "idea": idea}


# ---- agent tools -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "tool", "args", "status"),
    [
        ("scout", "kg_upsert_entity", {"type": "fact", "name": "x"}, 400),
        ("scout", "kg_upsert_entity", {"type": "planet", "name": "x"}, 400),
        ("scout", "kg_add_evidence", {"type": "fact", "title": "x", "evidence_url": URL, "published_at": "soon"}, 400),
        ("scout", "kg_add_evidence", {"type": "fact", "title": "x", "evidence_url": URL, "about": ["fact/1"]}, 400),
        ("scout", "kg_add_evidence", {"type": "fact", "title": "x", "evidence_url": URL, "about": ["bogus"]}, 400),
        ("draft", "draft_get_idea", {"id": "signal/1"}, 400),
        ("draft", "draft_save", {"idea_id": "signal/1", "title": "t", "markdown": "m"}, 400),
        ("signal", "kg_search", {"query": "x", "types": ["planet"]}, 400),
        ("signal", "kg_neighbors", {"id": "signal/1", "edge_types": ["likes"]}, 400),
        ("signal", "kg_link", {"from": "signal/1", "type": "likes", "to": "fact/1"}, 400),
    ],
)
def test_tool_rejections(
    api: Client, ctx: TestContext, slug: str, tool: str, args: dict[str, Any], status: int
) -> None:
    assert api.tool(tool, _tok(ctx, slug), args)[0] == status


def test_evidence_module_links_entities_but_output_module_cannot(
    api: Client, ctx: TestContext, nodes: dict[str, Node]
) -> None:
    own = ctx.graph.create(Node(id="", type=NodeType.FACT, title="Scout fact", evidence_url=URL, module="scout"), "m")
    s, out = api.tool(
        "kg_link",
        _tok(ctx, "scout"),
        {"from": own.id, "type": "about", "to": nodes["org"].id, "confidence": 0.5},
    )
    assert s == 200 and out["result"]["created"]
    # B-017: evidence someone else recorded is theirs to link
    s, _ = api.tool("kg_link", _tok(ctx, "scout"), {"from": nodes["fact"].id, "type": "about", "to": nodes["org"].id})
    assert s == 403
    s, _ = api.tool("kg_link", _tok(ctx, "trend"), {"from": nodes["fact"].id, "type": "about", "to": nodes["org"].id})
    assert s == 403


def test_signal_links_from_its_own_output(api: Client, ctx: TestContext, nodes: dict[str, Node]) -> None:
    s, out = api.tool(
        "kg_link", _tok(ctx, "signal"), {"from": nodes["signal"].id, "type": "supports", "to": nodes["fact"].id}
    )
    assert s == 200, out


def test_update_output_relinks_and_keeps_fields(api: Client, ctx: TestContext, nodes: dict[str, Node]) -> None:
    tok = _tok(ctx, "signal")
    s, out = api.tool(
        "kg_update_output", tok, {"id": nodes["signal"].id, "expected_version": 1, "supports": [nodes["fact"].id]}
    )
    assert s == 200 and len(out["result"]["links"]) == 1
    assert ctx.graph.get(nodes["signal"].id).title == "Motors"


def test_signal_without_evidence_is_saved_with_warning(
    api: Client, ctx: TestContext, caplog: pytest.LogCaptureFixture
) -> None:
    s, _ = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "Unbacked"})
    assert s == 200 and "without supporting evidence" in caplog.text


def test_module_without_output_kind_cannot_save(api: Client, ctx: TestContext) -> None:
    scout = ctx.modules.get("scout")
    tools = frozenset(scout.tools | {"kg_save_output"})
    assert api.tool("kg_save_output", _tok(ctx, "scout", tools=tools), {"title": "x"})[0] == 403


def test_product_tools_without_product_or_kb(api: Client, ctx: TestContext) -> None:
    from insights_radar.product.model import ProductProfile

    ctx.products.create(ProductProfile("", "DeskPro", "Desk"), "u")
    s, prof = api.tool("product_profile", _tok(ctx, "gap"))
    assert s == 200 and [p["name"] for p in prof["result"]["profiles"]] == ["DeskPro"]
    s, ctxt = api.tool("run_context", _tok(ctx, "gap", "missing-product"))
    assert s == 200 and ctxt["result"]["product"] is None
    ctx.knowledge = None  # type: ignore[assignment]
    s, res = api.tool("product_knowledge_search", _tok(ctx, "gap"), {"query": "x"})
    assert s == 200 and res["result"]["results"] == []


def test_agent_tools_fail_closed_without_a_configured_runtime_role(api: Client, ctx: TestContext) -> None:
    ctx.settings = replace(ctx.settings, module_runtime_role_arn="")
    assert api.tool("kg_schema", _tok(ctx, "signal"))[0] == 403
    assert "kg_schema" in api.app.tools.names


def test_a_grant_removed_mid_run_stops_working(api: Client, ctx: TestContext) -> None:
    token = _tok(ctx, "gap")
    assert api.tool("product_knowledge_search", token, {"query": "x"})[0] != 403  # granted (400: no product)
    module = ctx.modules.get("gap")
    kept = [g for g in module.grants if g is not ToolGrant.PRODUCT_KNOWLEDGE]
    ctx.modules.update("gap", {"grants": kept}, module.version)
    s, body = api.tool("product_knowledge_search", token, {"query": "x"})
    assert s == 403 and "no longer granted" in json.dumps(body)


# ---- routes ------------------------------------------------------------------------------------


def test_module_create_rejections(api: Client) -> None:
    base = {"slug": "w", "name": "W", "insight_kind": "w_item", "insight_label": "W", "grants": ["kg_read"]}
    assert api.call("POST", "/api/modules", {**base, "grants": ["kg_read", "kg_read", "kg_write_output"]})[0] == 201
    assert (
        api.call("POST", "/api/modules", {**base, "slug": "x", "insight_kind": "x_item", "grants": ["nope"]})[0] == 400
    )
    s, b, _ = api.call(
        "POST",
        "/api/modules",
        {
            **base,
            "slug": "y",
            "insight_kind": "y_item",
            "grants": ["kg_read", "kg_write_output", "mcp"],
            "mcp_servers": ["not-an-object"],
        },
    )
    assert s == 400 and "name" in b["message"]
    s, m, _ = api.call(
        "POST",
        "/api/modules",
        {
            **base,
            "slug": "z",
            "insight_kind": "z_item",
            "grants": ["kg_read", "kg_write_output", "mcp"],
            "input_types": ["fact"],
            "mcp_servers": [{"name": "docs", "url": "https://mcp.example.com"}],
        },
    )
    assert s == 201 and m["mcp_servers"][0]["name"] == "docs"
    # Only module managers see where the server is (its address can carry a credential).
    assert api.call("GET", "/api/modules/z")[1]["mcp_servers"] == [{"name": "docs", "url": "https://mcp.example.com"}]
    assert api.call("GET", "/api/modules/z", role="viewer")[1]["mcp_servers"] == [{"name": "docs"}]
    listed = {x["slug"]: x for x in api.call("GET", "/api/modules", role="editor")[1]["items"]}
    assert listed["z"]["mcp_servers"] == [{"name": "docs"}]


def test_module_update_fields(api: Client) -> None:
    s, m, _ = api.call(
        "PATCH",
        "/api/modules/signal",
        {"version": 1, "name": "Signals+", "product_id": None, "input_types": ["fact", "event"], "max_concurrency": 2},
    )
    assert s == 200 and m["name"] == "Signals+" and m["input_types"] == ["fact", "event"]
    assert api.call("PATCH", "/api/modules/signal", {"version": 2, "grants": ["nope"]})[0] == 400
    s, m, _ = api.call(
        "PATCH",
        "/api/modules/signal",
        {
            "version": 2,
            "grants": ["kg_read", "kg_write_output", "mcp"],
            "mcp_servers": [{"name": "d", "url": "https://m.example.com"}],
        },
    )
    assert s == 200 and m["mcp_servers"] == [{"name": "d", "url": "https://m.example.com"}]


def test_route_rejections(api: Client, ctx: TestContext, nodes: dict[str, Node]) -> None:
    api.call("POST", "/api/radars", {"slug": "r", "name": "R", "template": "competitor_landscape"})
    assert api.call("PUT", "/api/radars/r/entries", {"entries": ["x"], "expected_version": 1})[0] == 400
    assert (
        api.call(
            "PUT",
            "/api/radars/r/schema",
            {"schema": {"sectors": ["A"], "rings": ["B"]}, "expected_version": 1, "renames": {"sector": "x"}},
        )[0]
        == 400
    )
    s, u, _ = api.call("POST", "/api/users", {"email": "a@example.com"})
    assert api.call("PATCH", f"/api/users/{u['username']}", {"role": "god"})[0] == 400
    src = ctx.modules.add_source(
        "scout",
        "F",
        __import__("insights_radar.modules.model", fromlist=["x"]).SourceType.RSS,
        "https://f.example.com/rss",
    )
    assert api.call("PATCH", f"/api/modules/scout/sources/{src.id}", {"type": "ftp"})[0] == 400
    s, ex, _ = api.call("GET", "/api/graph/explore", role="viewer", query={"types": "signal,insight"})
    assert s == 200 and {n["type"] for n in ex["nodes"]} == {"signal"}
    _, twice, _ = api.call("GET", "/api/graph/explore", role="viewer", query={"types": "signal,signal,signal"})
    ids = [n["id"] for n in twice["nodes"]]
    assert len(ids) == len(set(ids)) == len([n for n in ex["nodes"] if n["type"] == "signal"])  # read once


def test_product_ask_without_knowledge_base(api: Client, ctx: TestContext) -> None:
    from insights_radar.product.model import ProductProfile

    p = ctx.products.create(ProductProfile("", "DeskPro", "Desk"), "u")
    ctx.knowledge = None  # type: ignore[assignment]
    assert api.call("POST", f"/api/products/{p.id}/ask", {"query": "x"}, role="editor")[0] == 400


def test_suggestion_review_routes(api: Client, ctx: TestContext) -> None:
    api.call(
        "POST",
        "/api/radars",
        {"slug": "r", "name": "R", "template": "competitor_landscape"},
        role="editor",
    )
    lock = ctx.radars.get("r").version_lock
    api.call("PATCH", "/api/radars/r", {"version_lock": lock, "mode": "assist"}, role="editor")
    one = ctx.radars.propose(
        "r",
        "module:curator",
        "run",
        {"title": "Acme", "sector": "Direct", "ring": "Leader"},
        "Three launch reports this month",
    )
    two = ctx.radars.propose(
        "r",
        "module:curator",
        "run",
        {"title": "Beta", "sector": "Direct", "ring": "Leader"},
        "Two analyst notes this month",
    )
    s, _, _ = api.call(
        "POST", f"/api/radars/r/suggestions/{one['entry_key']}/accept", {"edits": {"color": "x"}}, role="editor"
    )
    assert s == 400
    s, r, _ = api.call(
        "POST", f"/api/radars/r/suggestions/{one['entry_key']}/accept", {"edits": {"ring": "Challenger"}}, role="editor"
    )
    assert s == 200 and r["entry_count"] == 1
    assert api.call("DELETE", f"/api/radars/r/suggestions/{two['entry_key']}", role="editor")[0] == 200
    assert api.call("GET", "/api/radars/r/suggestions", role="viewer")[1]["items"] == []


def test_feedback_by_type_and_unknown_type(api: Client, nodes: dict[str, Node]) -> None:
    api.call("POST", f"/api/nodes/{nodes['signal'].id}/feedback", {"vote": "up"}, role="editor")
    s, fb, _ = api.call("GET", "/api/feedback", query={"type": "signal"})
    assert s == 200 and fb["items"][0]["node_id"] == nodes["signal"].id
    assert api.call("GET", "/api/feedback", query={"type": "planet"})[0] == 400


def test_custom_module_grants_are_bounded(ctx: TestContext) -> None:
    from insights_radar.modules.model import ModuleDefinition

    m = ModuleDefinition(
        slug="c",
        name="C",
        template=ModuleTemplate.CUSTOM,
        output_kind=NodeType.INSIGHT,
        insight_kind="c_item",
        insight_label="C",
        grants=frozenset({ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT}),
    )
    assert "kg_save_output" in m.tools and "radar_suggest" not in m.tools
    assert EdgeType.SUPPORTS.value == "supports"


def test_module_patch_rejects_non_object_mcp_server(api: Client) -> None:
    s, b, _ = api.call(
        "PATCH",
        "/api/modules/signal",
        {"version": 1, "grants": ["kg_read", "kg_write_output", "mcp"], "mcp_servers": ["https://m.example.com"]},
    )
    assert s == 400 and "must be an object" in b["message"]


def test_boolean_confidence_is_ignored(api: Client, ctx: TestContext) -> None:
    s, ev = api.tool(
        "kg_add_evidence", _tok(ctx, "scout"), {"type": "fact", "title": "x", "evidence_url": URL, "confidence": True}
    )
    assert s == 200 and ctx.graph.get(ev["result"]["id"]).confidence is None


def test_kill_switch_stops_runs_in_flight(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "signal")
    ctx.modules.set_andon(False, "incident", "u")
    s, b = api.tool("kg_search", tok, {"query": "motors"})
    assert s == 503 and b["code"] == "genai_disabled"
    s, b = api.tool("budget_status", tok)
    assert s == 200 and b["result"]["genai_enabled"] is False
    assert api.tool("progress_update", tok, {"remaining": 1})[0] == 200


def test_links_to_missing_nodes_are_rejected_before_writing(
    api: Client, ctx: TestContext, nodes: dict[str, Node]
) -> None:
    before = len(list(ctx.graph.iterate(NodeType.SIGNAL)))
    s, b = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "X", "supports": ["fact/does-not-exist"]})
    assert s == 400 and b["details"]["missing"] == ["fact/does-not-exist"]
    assert len(list(ctx.graph.iterate(NodeType.SIGNAL))) == before
    s, b = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "X", "about": [nodes["fact"].id]})
    assert s == 400  # signal -about-> fact is not an allowed edge


def test_failed_link_removes_the_new_node(
    api: Client, ctx: TestContext, nodes: dict[str, Node], monkeypatch: pytest.MonkeyPatch
) -> None:
    from insights_radar.core.errors import ConflictError

    def busy(*a: Any, **kw: Any) -> Any:
        raise ConflictError("the graph is busy with concurrent changes; retry shortly")

    monkeypatch.setattr(ctx.graph, "link", busy)
    before = {n.id for n in ctx.graph.iterate(NodeType.FACT)}
    s, b = api.tool(
        "kg_add_evidence",
        _tok(ctx, "scout"),
        {"type": "fact", "title": "Acme ships v2", "evidence_url": URL, "about": [nodes["org"].id]},
    )
    assert s == 409 and "busy" in b["message"]
    assert {n.id for n in ctx.graph.iterate(NodeType.FACT)} == before


def test_output_tools_need_an_output_kind_even_when_granted(api: Client, ctx: TestContext) -> None:
    scout = ctx.modules.get("scout")
    assert scout.output_kind is None
    ctx.modules.update("scout", {"grants": [*scout.grants, ToolGrant.KG_WRITE_OUTPUT]}, scout.version)
    s, body = api.tool("kg_save_output", _tok(ctx, "scout"), {"title": "x"})
    assert s == 403 and "does not produce outputs" in json.dumps(body)


def test_kg_link_checks_the_modules_current_grants_not_the_tokens(
    api: Client, ctx: TestContext, nodes: dict[str, Node]
) -> None:
    trend = ctx.modules.get("trend")  # an output module: it may not link evidence
    ensure_running(ctx, "trend")
    stale = ctx.tokens.issue("trend", "run-1", trend.tools | {"kg_add_evidence"}, 600)  # a wider, older token
    body = {"from": nodes["fact"].id, "type": "about", "to": nodes["org"].id}
    assert api.tool("kg_link", stale, body)[0] == 403


def test_a_draft_without_its_text_is_not_found(api: Client, ctx: TestContext) -> None:
    from insights_radar.graph.model import Node as GraphNode
    from insights_radar.graph.registry import NodeType

    draft = ctx.graph.create(GraphNode(id="", type=NodeType.CONTENT_DRAFT, title="Half saved"), "module:draft")
    assert api.call("GET", f"/api/drafts/{draft.id}", role="editor")[0] == 404


def test_a_draft_read_surfaces_other_storage_errors(
    api: Client, ctx: TestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.graph.model import Node as GraphNode
    from insights_radar.graph.registry import NodeType

    draft = ctx.graph.create(GraphNode(id="", type=NodeType.CONTENT_DRAFT, title="t"), "module:draft")

    def denied(**kw: Any) -> Any:
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    monkeypatch.setattr(ctx.s3, "get_object", denied)
    assert api.call("GET", f"/api/drafts/{draft.id}", role="editor")[0] == 500


SCHEMA = [
    {
        "key": "urgency",
        "label": "Urgency",
        "type": "enum",
        "required": True,
        "filter": True,
        "values": [
            {"value": "act_now", "label": "Act now", "rank": 1},
            {"value": "watch", "label": "Watch", "rank": 3},
        ],
    },
    {"key": "effective_date", "label": "Effective date", "type": "date", "sort": True},
]


def test_outputs_are_checked_against_the_module_schema(api: Client, ctx: TestContext) -> None:
    from insights_radar.modules.attributes import parse_schema

    m = ctx.modules.get("signal")
    ctx.modules.update("signal", {"attribute_schema": parse_schema(SCHEMA)}, m.version)
    assert [f.key for f in ctx.modules.get("signal").attribute_schema.fields] == ["urgency", "effective_date"]
    tok = _tok(ctx, "signal")
    s, err = api.tool(
        "kg_save_output", tok, {"title": "Rule", "attributes": {"urgency": "soon", "effective_date": "x"}}
    )
    assert s == 400
    joined = json.dumps(err)
    assert "must be one of" in joined and "YYYY-MM-DD" in joined
    s, out = api.tool("kg_save_output", tok, {"title": "Rule", "attributes": {"urgency": " act_now ", "note": "kept"}})
    assert s == 200
    node = ctx.graph.get(out["result"]["id"])
    assert node.attributes == {"urgency": "act_now", "note": "kept"}
    # An update that leaves attributes alone is not re-checked; one that sends them is.
    s, _ = api.tool("kg_update_output", tok, {"id": node.id, "expected_version": node.version, "title": "Rule 2"})
    assert s == 200
    s, _ = api.tool("kg_update_output", tok, {"id": node.id, "expected_version": node.version + 1, "attributes": {}})
    assert s == 400


def test_schema_is_refused_on_a_module_without_outputs(api: Client) -> None:
    s, body, _ = api.call("PATCH", "/api/modules/scout", {"version": 1, "attribute_schema": SCHEMA})
    assert s == 400 and "attribute_schema" in json.dumps(body)
    s, body, _ = api.call("PATCH", "/api/modules/signal", {"version": 1, "attribute_schema": [{"key": "x"}]})
    assert s == 400
    s, body, _ = api.call("PATCH", "/api/modules/signal", {"version": 1, "attribute_schema": SCHEMA})
    assert s == 200 and body["attribute_schema"][0]["key"] == "urgency"
    s, body, _ = api.call("PATCH", "/api/modules/signal", {"version": body["version"], "attribute_schema": None})
    assert s == 200 and body["attribute_schema"] == []


LIST_SCHEMA = [
    {
        "key": "urgency",
        "label": "Urgency",
        "type": "enum",
        "filter": True,
        "sort": True,
        "values": [
            {"value": "act_now", "label": "Act now", "rank": 1},
            {"value": "plan", "label": "Plan", "rank": 2},
            {"value": "watch", "label": "Watch", "rank": 3},
        ],
    },
    {"key": "countries", "label": "Countries", "type": "string_list", "filter": True},
    {"key": "effective_date", "label": "Effective date", "type": "date", "sort": True},
    {"key": "score", "label": "Score", "type": "number", "sort": True},
    {"key": "note", "label": "Note", "type": "text"},
]


def test_lists_filter_and_sort_on_schema_fields(api: Client, ctx: TestContext, clock: FixedClock) -> None:
    from datetime import timedelta

    from insights_radar.modules.attributes import parse_schema

    m = ctx.modules.get("signal")
    ctx.modules.update("signal", {"attribute_schema": parse_schema(LIST_SCHEMA)}, m.version)
    rows = [
        ("A", {"urgency": "watch", "countries": ["ID"], "effective_date": "2026-12-01", "score": 1}),
        ("B", {"urgency": "act_now", "countries": ["IN", "ID"], "effective_date": "2026-11-01", "score": 3}),
        ("C", {"urgency": "plan", "countries": ["VN"], "score": 2}),
        ("D", {"countries": ["ID"], "urgency": "bogus", "score": True}),
    ]
    for title, attrs in rows:
        ctx.graph.create(
            Node(id="", type=NodeType.SIGNAL, title=title, module="signal", attributes=attrs), "module:signal"
        )
        clock.advance(timedelta(minutes=1))

    def titles(**q: str) -> list[str]:
        s, body, _ = api.call("GET", "/api/insights/signals", query=q, role="viewer")
        assert s == 200, body
        return [i["title"] for i in body["items"]]

    _, body, _ = api.call("GET", "/api/insights/signals", role="viewer")
    assert [f["key"] for f in body["schema"]] == ["urgency", "countries", "effective_date", "score", "note"]
    assert titles(**{"filter.countries": "ID"}) == ["D", "B", "A"]  # newest first
    assert titles(**{"filter.countries": "ID", "filter.urgency": "act_now,watch"}) == ["B", "A"]
    assert titles(sort="attr:urgency") == ["B", "C", "A", "D"]  # rank order; an unknown value sorts last
    assert titles(sort="attr:urgency", order="desc") == ["A", "C", "B", "D"]
    assert titles(sort="attr:effective_date") == ["B", "A", "D", "C"]  # missing dates last, newest first
    assert titles(sort="attr:score", order="desc") == ["B", "C", "A", "D"]
    assert titles(sort="title", **{"filter.urgency": "plan"}) == ["C"]
    _, page, _ = api.call("GET", "/api/insights/signals", query={"filter.countries": "ID"}, role="viewer")
    assert page["total"] == 3 and page["truncated"] is False
    # A value of the wrong shape never matches; too many values are refused.
    ctx.graph.create(
        Node(id="", type=NodeType.SIGNAL, title="E", module="signal", attributes={"countries": 7}), "module:signal"
    )
    assert "E" not in titles(**{"filter.countries": "ID"})
    many = {"filter.countries": ",".join(f"C{i}" for i in range(51))}
    assert api.call("GET", "/api/insights/signals", query=many, role="viewer")[0] == 400
    for bad in (
        {"filter.note": "x"},
        {"filter.nope": "x"},
        {"filter.countries": " , "},
        {"sort": "attr:note"},
        {"sort": "attr:nope"},
        {"sort": "loudest"},
        {"order": "sideways"},
    ):
        assert api.call("GET", "/api/insights/signals", query=bad, role="viewer")[0] == 400, bad


def test_lists_without_a_schema_refuse_field_filters(api: Client) -> None:
    s, body, _ = api.call("GET", "/api/insights/trends", query={"filter.urgency": "plan"}, role="viewer")
    assert s == 400
    s, body, _ = api.call("GET", "/api/insights/trends", role="viewer")
    assert s == 200 and body["schema"] == []


def test_run_definition_carries_the_deployment_names(api: Client, ctx: TestContext) -> None:
    s, out = api.tool("run_definition", _tok(ctx, "signal", tools=frozenset({"run_definition"})), {})
    assert s == 200
    assert out["result"]["definition"]["deployment"] == {
        "app_name": "Agentic Insights Radar",
        "list_label": "Signals",
        "item_label": "Signal",
    }
    ctx.profile.put(
        {"app_name": "Trade Radar", "lists": {"signals": {"label": "Regulatory changes", "item": "Regulatory change"}}},
        "seed",
        expected_version=None,
    )
    _, out = api.tool("run_definition", _tok(ctx, "signal", tools=frozenset({"run_definition"})), {})
    assert out["result"]["definition"]["deployment"]["list_label"] == "Regulatory changes"
    assert out["result"]["definition"]["prompt_mode"] == "extend"
    _, out = api.tool("run_definition", _tok(ctx, "scout", tools=frozenset({"run_definition"})), {})
    assert out["result"]["definition"]["deployment"]["list_label"] == "Scout"  # no list: the module's name


def test_custom_module_names_come_from_its_list(api: Client, ctx: TestContext) -> None:
    body = {
        "slug": "watch",
        "name": "Watch",
        "insight_kind": "watch_item",
        "insight_label": "Watchlist",
        "grants": ["kg_read", "kg_write_output"],
    }
    assert api.call("POST", "/api/modules", body)[0] == 201
    _, out = api.tool("run_definition", _tok(ctx, "watch", tools=frozenset({"run_definition"})), {})
    assert out["result"]["definition"]["deployment"]["list_label"] == "Watchlist"
    ctx.profile.put(
        {"lists": {"insight:watch_item": {"label": "Watched rules", "item": "Watched rule"}}},
        "seed",
        expected_version=None,
    )
    _, out = api.tool("run_definition", _tok(ctx, "watch", tools=frozenset({"run_definition"})), {})
    assert out["result"]["definition"]["deployment"]["item_label"] == "Watched rule"


def test_prompt_mode_is_validated(api: Client) -> None:
    s, body, _ = api.call("PATCH", "/api/modules/signal", {"version": 1, "prompt_mode": "rewrite"})
    assert s == 400 and "prompt_mode must be extend or replace" in json.dumps(body)
    s, body, _ = api.call(
        "PATCH", "/api/modules/signal", {"version": 1, "prompt_mode": "replace", "instructions": "short"}
    )
    assert s == 400 and "at least 200 characters" in json.dumps(body)
    s, body, _ = api.call(
        "PATCH", "/api/modules/signal", {"version": 1, "prompt_mode": "replace", "instructions": "y" * 250}
    )
    assert s == 200 and body["prompt_mode"] == "replace"


def test_facets_count_field_values_and_list_what_is_coming_up(api: Client, ctx: TestContext, clock: FixedClock) -> None:
    from datetime import timedelta

    from insights_radar.modules.attributes import parse_schema

    m = ctx.modules.get("signal")
    ctx.modules.update("signal", {"attribute_schema": parse_schema(LIST_SCHEMA)}, m.version)
    today = clock.now().date()

    def day(n: int) -> str:
        return (today + timedelta(days=n)).isoformat()

    rows = [
        ("A", {"urgency": "watch", "countries": ["ID"], "effective_date": day(100)}),
        ("B", {"urgency": "act_now", "countries": ["IN", "ID", "ID"], "effective_date": day(5)}),
        ("C", {"urgency": "plan", "countries": ["VN"], "effective_date": day(0)}),
        ("D", {"urgency": "bogus", "countries": "ID", "effective_date": day(-1)}),
        ("E", {"urgency": "plan", "effective_date": day(90)}),
    ]
    for title, attrs in rows:
        ctx.graph.create(
            Node(id="", type=NodeType.SIGNAL, title=title, module="signal", attributes=attrs), "module:signal"
        )
    s, body, _ = api.call("GET", "/api/insights/signals/facets", role="viewer")
    assert s == 200, body
    assert body["total"] == 5 and body["truncated"] is False and body["days"] == 90
    # Every declared value is listed; an undeclared value ("bogus") is not counted.
    assert body["counts"]["urgency"] == {"act_now": 1, "plan": 2, "watch": 1}
    # A free list counts each record once per value it holds; a plain string counts too.
    assert body["counts"]["countries"] == {"ID": 3, "IN": 1, "VN": 1}
    # Today through 90 days ahead, soonest first.
    assert [(u["node"]["title"], u["date"]) for u in body["upcoming"]] == [
        ("C", day(0)),
        ("B", day(5)),
        ("E", day(90)),
    ]
    assert all(u["field"] == "effective_date" for u in body["upcoming"])
    _, wide, _ = api.call("GET", "/api/insights/signals/facets", query={"days": "366"}, role="viewer")
    assert [u["node"]["title"] for u in wide["upcoming"]] == ["C", "B", "E", "A"]
    assert wide["schema"][0]["key"] == "urgency"
    for bad in ("0", "367", "abc", "-1"):
        assert api.call("GET", "/api/insights/signals/facets", query={"days": bad}, role="viewer")[0] == 400
    # A list without a schema has nothing to count.
    s, plain, _ = api.call("GET", "/api/insights/trends/facets", role="viewer")
    assert s == 200 and plain["counts"] == {} and plain["upcoming"] == []
    # The same access rules as the list: private kinds need product access, unknown kinds are not found.
    assert api.call("GET", "/api/insights/gaps/facets", role="viewer")[0] == 403
    assert api.call("GET", "/api/insights/gaps/facets", role="admin")[0] == 200
    assert api.call("GET", "/api/insights/insight:nothing/facets", role="viewer")[0] == 404
    assert api.call("GET", "/api/insights/signals/facets", role="anon")[0] == 401


def test_facets_cap_the_upcoming_list(api: Client, ctx: TestContext, clock: FixedClock) -> None:
    from insights_radar.insights.service import UPCOMING_MAX
    from insights_radar.modules.attributes import parse_schema

    m = ctx.modules.get("signal")
    ctx.modules.update("signal", {"attribute_schema": parse_schema(LIST_SCHEMA)}, m.version)
    when = clock.now().date().isoformat()
    for i in range(UPCOMING_MAX + 3):
        ctx.graph.create(
            Node(id="", type=NodeType.SIGNAL, title=f"S{i}", module="signal", attributes={"effective_date": when}),
            "module:signal",
        )
    _, body, _ = api.call("GET", "/api/insights/signals/facets", role="viewer")
    assert len(body["upcoming"]) == UPCOMING_MAX and body["total"] == UPCOMING_MAX + 3


def test_a_list_exports_as_csv_or_markdown_with_its_filters(api: Client, ctx: TestContext, clock: FixedClock) -> None:
    import csv
    import io
    from datetime import timedelta

    from insights_radar.modules.attributes import parse_schema

    m = ctx.modules.get("signal")
    ctx.modules.update("signal", {"attribute_schema": parse_schema(LIST_SCHEMA)}, m.version)
    for title, attrs in [
        ("A", {"urgency": "watch", "countries": ["ID"], "effective_date": "2026-12-01"}),
        ("=B", {"urgency": "act_now", "countries": ["IN"], "effective_date": "2026-11-01"}),
    ]:
        ctx.graph.create(
            Node(id="", type=NodeType.SIGNAL, title=title, module="signal", attributes=attrs), "module:signal"
        )
        clock.advance(timedelta(minutes=1))
    s, body, headers = api.call("GET", "/api/insights/signals/export", role="viewer")
    assert s == 200 and headers["Content-Type"].startswith("text/csv")
    assert headers["Content-Disposition"] == f'attachment; filename="signals-{clock.now().date().isoformat()}.csv"'
    rows = list(csv.reader(io.StringIO(body)))
    assert rows[0][:4] == ["Title", "Summary", "Urgency", "Countries"]
    assert [r[0] for r in rows[1:]] == ["'=B", "A"]  # newest first; a formula start is defused
    s, body, _ = api.call(
        "GET", "/api/insights/signals/export", query={"filter.countries": "ID", "format": "md"}, role="viewer"
    )
    assert s == 200 and body.startswith("# Signals\n") and "| A |" in body and "=B" not in body
    assert "Agentic Insights Radar, " in body
    s, body, _ = api.call("GET", "/api/insights/signals/export", query={"sort": "attr:effective_date"}, role="viewer")
    assert [r[0] for r in list(csv.reader(io.StringIO(body)))[1:]] == ["'=B", "A"]
    for bad in ({"format": "pdf"}, {"sort": "title"}, {"filter.note": "x"}):
        assert api.call("GET", "/api/insights/signals/export", query=bad, role="viewer")[0] == 400, bad
    assert api.call("GET", "/api/insights/signals/export", query={"sort": "newest"}, role="viewer")[0] == 200
    assert api.call("GET", "/api/insights/gaps/export", role="viewer")[0] == 403
    assert api.call("GET", "/api/insights/insight:nothing/export", role="viewer")[0] == 404
    assert api.call("GET", "/api/insights/signals/export", role="anon")[0] == 401
    # The profile's list name names the file and the Markdown title.
    ctx.profile.put({"lists": {"signals": {"label": "Regulatory changes", "item": "Regulatory change"}}}, "user:x", 0)
    s, body, headers = api.call("GET", "/api/insights/signals/export", query={"format": "md"}, role="viewer")
    assert body.startswith("# Regulatory changes\n") and "regulatory-changes-" in headers["Content-Disposition"]


def test_an_export_says_when_it_was_cut_short(api: Client, ctx: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from insights_radar.insights import export

    monkeypatch.setattr(export, "EXPORT_MAX", 1)
    for title in ("A", "B"):
        ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title=title, module="signal"), "module:signal")
    _, body, _ = api.call("GET", "/api/insights/signals/export", query={"format": "md"}, role="viewer")
    assert "Only the first records are included." in body and body.count("\n| ") == 2  # header and one row


def test_fields_of_an_odd_shape_never_break_filters_counts_or_exports(api: Client, ctx: TestContext) -> None:
    """A value an agent wrote before the field had a type (a number, a flag) counts as nothing. The graph
    keeps lists to strings, so nothing unhashable reaches these paths."""
    from insights_radar.modules.attributes import parse_schema

    m = ctx.modules.get("signal")
    ctx.modules.update("signal", {"attribute_schema": parse_schema(LIST_SCHEMA)}, m.version)
    for title, countries in (("A", ["ID"]), ("B", 7), ("C", True)):
        ctx.graph.create(
            Node(id="", type=NodeType.SIGNAL, title=title, module="signal", attributes={"countries": countries}),
            "module:signal",
        )
    s, body, _ = api.call("GET", "/api/insights/signals", query={"filter.countries": "ID"}, role="viewer")
    assert s == 200 and [i["title"] for i in body["items"]] == ["A"]
    s, facets, _ = api.call("GET", "/api/insights/signals/facets", role="viewer")
    assert s == 200 and facets["counts"]["countries"] == {"ID": 1}
    assert api.call("GET", "/api/insights/signals/export", query={"filter.countries": "ID"}, role="viewer")[0] == 200


def test_facets_leave_out_closed_gaps_as_the_gap_list_does(api: Client, ctx: TestContext) -> None:
    from insights_radar.modules.attributes import parse_schema

    m = ctx.modules.get("gap")
    schema = [{"key": "severity", "label": "Severity", "type": "enum", "filter": True, "values": [{"value": "high"}]}]
    ctx.modules.update("gap", {"attribute_schema": parse_schema(schema)}, m.version)
    for status in ("open", "closed", "closed"):
        attrs = {"severity": "high", "status": status, "request_title": "x", "problem": "y", "impact": "z"}
        ctx.graph.create(Node(id="", type=NodeType.GAP, title=status, module="gap", attributes=attrs), "module:gap")
    _, facets, _ = api.call("GET", "/api/insights/gaps/facets", role="admin")
    _, page, _ = api.call("GET", "/api/insights/gaps", query={"filter.severity": "high"}, role="admin")
    assert facets["counts"]["severity"] == {"high": 1} == {"high": page["total"]}
    assert facets["scanned"] == 3 and facets["total"] == 1
    # A viewer learns nothing about a private list's fields: refused before the filter is read.
    for field in ("severity", "no_such_field"):
        assert api.call("GET", "/api/insights/gaps", query={f"filter.{field}": "x"}, role="viewer")[0] == 403
    assert api.call("GET", "/api/insights/insight:nothing", query={"filter.x": "y"}, role="viewer")[0] == 404


def test_facets_are_shared_for_a_minute_by_callers_who_see_the_same_records(
    api: Client, ctx: TestContext, clock: FixedClock
) -> None:
    from datetime import timedelta

    from insights_radar.modules.attributes import parse_schema

    m = ctx.modules.get("signal")
    ctx.modules.update("signal", {"attribute_schema": parse_schema(LIST_SCHEMA)}, m.version)

    def add(title: str) -> None:
        ctx.graph.create(
            Node(id="", type=NodeType.SIGNAL, title=title, module="signal", attributes={"urgency": "plan"}),
            "module:signal",
        )

    add("A")
    assert api.call("GET", "/api/insights/signals/facets", role="viewer")[1]["counts"]["urgency"]["plan"] == 1
    add("B")
    # Within the minute the viewer's answer is reused; an editor (who may see more) has its own.
    assert api.call("GET", "/api/insights/signals/facets", role="viewer")[1]["counts"]["urgency"]["plan"] == 1
    assert api.call("GET", "/api/insights/signals/facets", role="editor")[1]["counts"]["urgency"]["plan"] == 2
    clock.advance(timedelta(seconds=61))
    assert api.call("GET", "/api/insights/signals/facets", role="viewer")[1]["counts"]["urgency"]["plan"] == 2
    assert api.call("GET", "/api/insights/signals/facets", query={"days": "\u00b2"}, role="viewer")[0] == 400


def test_facets_cache_keeps_only_the_newest_answers(
    api: Client, ctx: TestContext, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from insights_radar.api import routes

    monkeypatch.setattr(routes, "FACETS_CACHE_MAX", 1)
    ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="A", module="signal"), "module:signal")
    assert api.call("GET", "/api/insights/signals/facets", query={"days": "30"}, role="viewer")[1]["total"] == 1
    ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="B", module="signal"), "module:signal")
    # A second key pushes the first out, so asking for it again counts afresh within the minute.
    assert api.call("GET", "/api/insights/signals/facets", query={"days": "60"}, role="viewer")[1]["total"] == 2
    clock.advance(timedelta(seconds=1))
    assert api.call("GET", "/api/insights/signals/facets", query={"days": "30"}, role="viewer")[1]["total"] == 2


def test_facets_keep_the_most_common_values_of_a_free_form_field(api: Client, ctx: TestContext) -> None:
    from insights_radar.insights.service import FACET_VALUES_MAX
    from insights_radar.modules.attributes import parse_schema

    m = ctx.modules.get("signal")
    ctx.modules.update("signal", {"attribute_schema": parse_schema(LIST_SCHEMA)}, m.version)
    for i in range(FACET_VALUES_MAX + 5):
        ctx.graph.create(
            Node(
                id="",
                type=NodeType.SIGNAL,
                title=f"S{i}",
                module="signal",
                attributes={"countries": [f"C{i:03d}", "ZZ"]},
            ),
            "module:signal",
        )
    _, body, _ = api.call("GET", "/api/insights/signals/facets", role="viewer")
    counts = body["counts"]["countries"]
    assert len(counts) == FACET_VALUES_MAX and counts["ZZ"] == FACET_VALUES_MAX + 5
    assert body["counts_cut"] == ["countries"] and "C054" not in counts


def test_search_says_when_it_answers_with_keywords_only(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    ctx.graph.create(Node(id="", type=NodeType.TREND, title="Rotterdam port congestion", summary="Queues"), "u")

    class Broken:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("model down")

    s, b, _ = api.call("POST", "/api/graph/search", {"query": "congestion"}, role="viewer")
    assert s == 200 and "degraded" not in b
    monkeypatch.setattr(ctx.search, "_embedder", Broken())
    s, b, _ = api.call("POST", "/api/graph/search", {"query": "congestion"}, role="viewer")
    assert s == 200 and b["degraded"] is True and b["items"][0]["node"]["title"] == "Rotterdam port congestion"
    # Keyword matches only: still judged by shared words (one of four is not enough), but there was no
    # meaning search to name (round 34 and 35 code reviews).
    s, b, _ = api.call("POST", "/api/graph/search", {"query": "congestion acme motor tariffs"}, role="viewer")
    assert s == 200 and b["degraded"] is True and b["items"]
    assert b["note"] == "No result contains most of the query's words."
    s, b = api.tool("kg_search", _tok(ctx, "signal", "run-9"), {"query": "congestion acme motor tariffs"})
    assert "keyword matches only" in b["result"]["note"] and "none of these shares" in b["result"]["note"]
    s, b, _ = api.call("POST", "/api/graph/ask", {"question": "congestion acme motor tariffs"}, role="viewer")
    assert s == 200 and b["degraded"] is True and b["coverage"] in ("loose", "no_sources")
    s, b = api.tool("kg_search", _tok(ctx, "signal"), {"query": "congestion"})
    assert s == 200 and "keyword matches only" in b["result"]["note"] and b["result"]["results"]


def test_hc126_hc127_a_same_title_save_folds_into_the_output_and_reopens_it(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    from insights_radar.core.errors import ConflictError

    tok = _tok(ctx, "gap")
    attrs = {"request_title": "SSO", "problem": "No SSO", "impact": "Lost deals"}
    # Another writer's gap with the same title is not this module's to fold into.
    other = ctx.graph.create(Node(id="", type=NodeType.GAP, title="No SSO", attributes=attrs), "u")
    s, first = api.tool("kg_save_output", tok, {"title": "No SSO", "summary": "s", "attributes": attrs})
    assert s == 200 and first["result"]["id"] != other.id and "existing" not in first["result"]
    gap_id = first["result"]["id"]
    s, again = api.tool("kg_save_output", tok, {"title": " no  sso ", "summary": "s", "attributes": attrs})
    assert s == 200 and again["result"]["id"] == gap_id
    assert again["result"]["existing"] is True and again["result"]["reopened"] is False
    node = ctx.graph.get(gap_id)
    ctx.graph.update(gap_id, {"attributes": {**node.attributes, "status": "closed"}}, node.version, "module:gap")
    s, back = api.tool("kg_save_output", tok, {"title": "No SSO", "summary": "s", "attributes": attrs})
    assert back["result"]["reopened"] is True and ctx.graph.get(gap_id).attributes["status"] == "open"
    # A reopen that loses a race keeps the other writer's status; the save still answers.
    node = ctx.graph.get(gap_id)
    ctx.graph.update(gap_id, {"attributes": {**node.attributes, "status": "closed"}}, node.version, "module:gap")

    def racing(*_a: Any, **_k: Any) -> Any:
        raise ConflictError("version moved")

    monkeypatch.setattr(ctx.graph, "update", racing)
    s, raced = api.tool("kg_save_output", tok, {"title": "No SSO", "summary": "s", "attributes": attrs})
    assert s == 200 and raced["result"]["reopened"] is False


def test_hc024_an_administrator_cancels_a_running_run(api: Client, ctx: TestContext) -> None:
    from dataclasses import replace as _replace

    from insights_radar.modules.repository import ModuleRun, RunStatus

    tok = _tok(ctx, "signal")  # stores signal/run-1 as running, with no execution yet
    run = ctx.modules.get_run("signal", "run-1")
    ctx.modules.put_run(_replace(run, execution_arn="arn:aws:states:us-east-1:111122223333:execution:mr:run-1"))
    path = "/api/modules/signal/runs/run-1/cancel"
    assert api.call("POST", path, {}, role="editor")[0] == 403
    assert api.call("POST", path, {"why": "x"})[0] == 400
    s, done, _ = api.call("POST", path, {})
    assert s == 200 and done["status"] == "cancelled" and done["summary"].startswith("Cancelled by ")
    assert ctx.launcher.stopped and ctx.launcher.stopped[-1][0].endswith(":run-1")
    # Its tools refuse now, and a second cancel says it has finished.
    assert api.tool("kg_search", tok, {"query": "motors"})[0] == 409
    s, again, _ = api.call("POST", path, {})
    assert s == 409 and again["details"]["status"] == "cancelled"
    assert api.call("POST", "/api/modules/signal/runs/nope/cancel", {})[0] == 404
    # A queued run has no execution to stop.
    ctx.modules.put_run(ModuleRun("signal", "run-q", RunStatus.QUEUED, "manual", "2026-10-05T00:00:00Z", "u-admin"))
    before = len(ctx.launcher.stopped)
    s, queued, _ = api.call("POST", "/api/modules/signal/runs/run-q/cancel", {})
    assert s == 200 and queued["status"] == "cancelled" and len(ctx.launcher.stopped) == before
    s, log, _ = api.call("GET", "/api/audit")
    assert any(i["action"] == "module.run_cancel" for i in log["items"])


def test_hc105_an_administrator_merges_a_duplicate_entity(api: Client, ctx: TestContext) -> None:
    keep, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme Motors"), "u")
    dup, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="ACME Motor Co"), "u")
    path = f"/api/graph/nodes/{dup.id}/merge"
    assert api.call("POST", path, {"into": keep.id}, role="editor")[0] == 403
    assert api.call("POST", path, {"into": "nope"})[0] == 400
    assert api.call("POST", path, {"into": keep.id, "force": True})[0] == 400
    s, dry, _ = api.call("POST", path, {"into": keep.id})
    assert s == 200 and dry["applied"] is False and dry["aliases_added"] == ["ACME Motor Co"]
    s, done, _ = api.call("POST", path, {"into": keep.id, "apply": True})
    assert s == 200 and done["applied"] is True
    assert api.call("GET", f"/api/graph/nodes/{dup.id}")[0] == 404
    s, log, _ = api.call("GET", "/api/audit")
    assert any(i["action"] == "graph.merge" for i in log["items"])


def test_hc105_a_merge_too_large_for_one_request_is_refused(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from insights_radar.api import routes

    keep, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Tariffs"), "u")
    dup, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Duties"), "u")
    ctx.graph.link(
        ctx.graph.create(Node(id="", type=NodeType.FACT, title="f", evidence_url=URL), "u").id,
        EdgeType.ABOUT,
        dup.id,
        "u",
    )
    monkeypatch.setattr(routes, "MAX_DELETE_EDGES", 0)
    s, b, _ = api.call("POST", f"/api/graph/nodes/{dup.id}/merge", {"into": keep.id})
    assert s == 409 and b["details"]["limit"] == 0


def test_hc120_the_hygiene_report_is_for_administrators(api: Client, ctx: TestContext) -> None:
    ctx.graph.upsert_entity(Node(id="", type=NodeType.PERSON, title="Ada"), "u")
    assert api.call("GET", "/api/graph/hygiene", role="editor")[0] == 403
    s, b, _ = api.call("GET", "/api/graph/hygiene")
    assert s == 200 and b["orphan_count"] >= 1 and "duplicates" in b


def test_hc098_a_question_comes_back_with_its_evidence_and_how_well_it_is_backed(api: Client, ctx: TestContext) -> None:
    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme Motors"), "u")
    f1 = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Acme cuts motor prices", evidence_url="https://a.example.com/1"), "u"
    )
    trend = ctx.graph.create(Node(id="", type=NodeType.TREND, title="Motor price war", summary="Prices fall"), "u")
    ctx.graph.link(f1.id, EdgeType.ABOUT, acme.id, "u")
    ctx.graph.link(trend.id, EdgeType.SUPPORTS, f1.id, "u")
    for n in (acme, f1, trend):
        ctx.index.put(
            n.id, HashEmbedder().embed(ctx.graph.get(n.id).search_text), StreamIndexer.metadata(ctx.graph.get(n.id))
        )
    s, b, _ = api.call("POST", "/api/graph/ask", {"question": "Acme Motors"}, role="viewer")
    assert s == 200 and b["coverage"] == "single_source" and b["sources"][0]["url"] == "https://a.example.com/1"
    hit = next(h for h in b["hits"] if h["id"] == acme.id)
    assert hit["evidence"] == [f1.id]
    f2 = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Motor price war deepens", evidence_url="https://b.example.org/2"), "u"
    )
    ctx.graph.link(trend.id, EdgeType.SUPPORTS, f2.id, "u")
    s, b, _ = api.call("POST", "/api/graph/ask", {"question": "motor price war", "types": ["trend"]})
    assert b["coverage"] == "sourced" and {x["id"] for x in b["sources"]} == {f1.id, f2.id}
    # An evidence record walks out to the outputs it supports.
    s, b, _ = api.call("POST", "/api/graph/ask", {"question": "cuts", "types": ["fact"]})
    assert next(h for h in b["hits"] if h["id"] == f1.id)["evidence"] == [trend.id]
    gap = ctx.graph.create(Node(id="", type=NodeType.GAP, title="No warranty plan", summary="s"), "u")
    s, b, _ = api.call("POST", "/api/graph/ask", {"question": "warranty plan", "types": ["gap"]})
    assert b["coverage"] == "no_sources" and "not backed" in b["note"] and b["hits"][0]["id"] == gap.id
    s, b, _ = api.call("POST", "/api/graph/ask", {"question": "zeppelins", "types": ["gap"]})
    assert b["coverage"] == "none" and b["hits"] == [] and "general knowledge" in b["note"]
    assert api.call("POST", "/api/graph/ask", {"question": "x"})[0] == 400
    assert api.call("POST", "/api/graph/ask", {"question": "ok ok", "why": 1})[0] == 400


def test_hc100_ask_says_when_it_could_only_match_words(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    ctx.graph.create(Node(id="", type=NodeType.TREND, title="Rotterdam port congestion", summary="Queues"), "u")

    class Broken:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("model down")

    monkeypatch.setattr(ctx.search, "_embedder", Broken())
    s, b, _ = api.call("POST", "/api/graph/ask", {"question": "congestion", "types": ["trend"]})
    assert s == 200 and b["degraded"] is True and "keyword matches" in b["note"]


def test_research_brief_gathers_one_record(api: Client, ctx: TestContext) -> None:
    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme Motors"), "u")
    f1 = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Acme cuts prices", evidence_url="https://a.example.com/1"), "u"
    )
    trend = ctx.graph.create(Node(id="", type=NodeType.TREND, title="Price war", summary="s" * 600), "u")
    ctx.graph.link(f1.id, EdgeType.ABOUT, acme.id, "u")
    ctx.graph.link(trend.id, EdgeType.ABOUT, acme.id, "u")
    path = f"/api/graph/nodes/{acme.id}/brief"
    s, b, _ = api.call("GET", path, role="viewer")
    assert s == 200 and b["node"]["id"] == acme.id
    assert [n["id"] for n in b["evidence"]] == [f1.id] and [n["id"] for n in b["conclusions"]] == [trend.id]
    assert b["coverage"] == "single_source" and b["radars"] == [] and b["sources"][0]["id"] == f1.id
    # The trend's own brief keeps its whole summary and has no sourced record.
    s, b, _ = api.call("GET", f"/api/graph/nodes/{trend.id}/brief")
    assert s == 200 and len(b["node"]["summary"]) == 600 and b["coverage"] == "no_sources"
    assert api.call("GET", "/api/graph/nodes/trend/nope/brief")[0] == 404
    # A viewer cannot research a gap (written from private product documents), nor see it linked.
    gap = ctx.graph.create(Node(id="", type=NodeType.GAP, title="No warranty plan", summary="s"), "u")
    ctx.graph.link(gap.id, EdgeType.ABOUT, acme.id, "u")
    assert api.call("GET", f"/api/graph/nodes/{gap.id}/brief", role="viewer")[0] == 404
    s, b, _ = api.call("GET", f"/api/graph/nodes/{acme.id}/brief", role="viewer")
    assert gap.id not in {n["id"] for n in b["conclusions"]}
    s, b, _ = api.call("GET", f"/api/graph/nodes/{acme.id}/brief")
    assert gap.id in {n["id"] for n in b["conclusions"]}


def test_a_brief_leaves_out_draft_working_copies(api: Client, ctx: TestContext) -> None:
    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    draft = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="Draft post", summary="s"), "u")
    idea = ctx.graph.create(Node(id="", type=NodeType.CONTENT_IDEA, title="Post idea", summary="s"), "u")
    ctx.graph.link(draft.id, EdgeType.ABOUT, acme.id, "u")
    ctx.graph.link(idea.id, EdgeType.ABOUT, acme.id, "u")
    b = api.call("GET", f"/api/graph/nodes/{acme.id}/brief")[1]
    assert [n["id"] for n in b["conclusions"]] == [idea.id]


def test_a_question_nothing_in_the_graph_words_is_loose_not_sourced(api: Client, ctx: TestContext) -> None:
    """MCP test (2026-11-03): semantic search always returns its nearest records, so a question the graph does
    not cover (or an instruction aimed at the model) came back "sourced" with "answer from these records"."""
    f1 = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Acme cuts motor prices", evidence_url="https://a.example.com/1"), "u"
    )
    ctx.index.put(f1.id, HashEmbedder().embed("population of mars"), StreamIndexer.metadata(ctx.graph.get(f1.id)))
    s, b, _ = api.call("POST", "/api/graph/ask", {"question": "population of mars"}, role="viewer")
    assert s == 200 and b["hits"] and b["coverage"] == "loose" and "may not cover" in b["note"]
    s, b, _ = api.call("POST", "/api/graph/search", {"query": "population of mars"}, role="viewer")
    assert s == 200 and b["items"] and "only near it in meaning" in b["note"]
    s, b, _ = api.call("POST", "/api/graph/search", {"query": "Acme motor"}, role="viewer")
    assert s == 200 and b["items"] and "note" not in b
    # Module agents are warned too: kg_search is how Scout decides whether an entity already exists.
    s, b = api.tool("kg_search", _tok(ctx, "signal"), {"query": "population of mars"})
    assert s == 200 and b["result"]["results"] and "only near it in meaning" in b["result"]["note"]
    s, b = api.tool("kg_search", _tok(ctx, "signal"), {"query": "Acme motor"})
    assert s == 200 and "reciprocal-rank-fusion" in b["result"]["note"]
    s, b, _ = api.call("GET", "/api/insights/radars")
    assert s == 400 and "signals" in b["message"] and "insight:<kind>" in b["message"]


def test_a_hidden_record_changes_nothing_a_viewer_sees_in_search_or_ask(api: Client, ctx: TestContext) -> None:
    """Round 33 security review: the hits were filtered after ranking, so the ranks, fused scores and the "no
    result has the query's words" note still counted a gap a viewer may not read; word by word a viewer could
    test which words a private gap's title contains."""
    fact = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Acme cuts motor prices", evidence_url="https://a.example.com/1"), "u"
    )
    ctx.index.put(fact.id, HashEmbedder().embed("zephyr deal"), StreamIndexer.metadata(ctx.graph.get(fact.id)))
    search, ask = {"query": "zephyr deal"}, {"question": "zephyr deal"}
    before = [api.call("POST", "/api/graph/search", search, role="viewer")[1]]
    before.append(api.call("POST", "/api/graph/ask", ask, role="viewer")[1])
    gap = ctx.graph.create(Node(id="", type=NodeType.GAP, title="Zephyr deal lost", summary="zephyr deal"), "u")
    ctx.index.put(gap.id, HashEmbedder().embed("zephyr deal"), StreamIndexer.metadata(ctx.graph.get(gap.id)))
    after = [api.call("POST", "/api/graph/search", search, role="viewer")[1]]
    after.append(api.call("POST", "/api/graph/ask", ask, role="viewer")[1])
    assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)
    assert "only near it in meaning" in after[0]["note"] and after[1]["coverage"] == "loose"
    # An admin, who may read the gap, gets it ranked first by its words.
    s, b, _ = api.call("POST", "/api/graph/search", search)
    assert s == 200 and b["items"][0]["node"]["id"] == gap.id and "note" not in b


def test_an_unsourced_record_near_a_question_keeps_the_no_sources_warning(api: Client, ctx: TestContext) -> None:
    """Round 33 security review: "loose" replaced "no_sources", dropping its "not backed by evidence" line."""
    trend = ctx.graph.create(Node(id="", type=NodeType.TREND, title="Motors get cheaper", summary="s"), "u")
    ctx.index.put(trend.id, HashEmbedder().embed("population of mars"), StreamIndexer.metadata(ctx.graph.get(trend.id)))
    s, b, _ = api.call("POST", "/api/graph/ask", {"question": "population of mars"}, role="viewer")
    assert s == 200 and b["hits"] and b["coverage"] == "no_sources"


def test_kg_search_answers_the_same_whether_or_not_a_private_name_exists(api: Client, ctx: TestContext) -> None:
    """Round 34 security review: with limit 1 an exact-name private entity took the top slot, so an injected
    outbound module saw `hidden: 1`, and the visible scores showed where hidden records ranked."""
    public = ctx.graph.create(Node(id="", type=NodeType.FACT, title="Falcon class ships", evidence_url=URL), "u")
    ctx.index.put(public.id, HashEmbedder().embed("project falcon"), StreamIndexer.metadata(ctx.graph.get(public.id)))
    ask = {"query": "Project Falcon", "limit": 1}
    before = api.tool("kg_search", _tok(ctx, "scout"), ask)[1]["result"]
    secret, _ = ctx.graph.upsert_entity(
        Node(id="", type=NodeType.PRODUCT, title="Project Falcon", summary="roadmap", private=True), "u"
    )
    ctx.index.put(secret.id, HashEmbedder().embed("project falcon"), StreamIndexer.metadata(ctx.graph.get(secret.id)))
    after = api.tool("kg_search", _tok(ctx, "scout"), ask)[1]["result"]
    assert json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True)
    assert [r["id"] for r in after["results"]] == [public.id]
