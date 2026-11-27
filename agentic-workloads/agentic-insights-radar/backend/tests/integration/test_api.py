# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from insights_radar.api.app import Application
from insights_radar.core.clock import FixedClock, iso
from insights_radar.core.errors import UnauthorizedError
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from tests.integration.harness import CALLER_ARN, Client, TestContext, ensure_running

URL = "https://news.example.com/motor"
RANK = {"anon": -1, "viewer": 0, "editor": 1, "admin": 2}


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> TestContext:
    return TestContext(graph_table, ops_table, clock)


@pytest.fixture
def api(ctx: TestContext) -> Client:
    return Client(Application(ctx))


@pytest.fixture
def world(api: Client, ctx: TestContext) -> dict[str, Any]:
    s, prod, _ = api.call(
        "POST",
        "/api/products",
        {"name": "DeskPro", "summary": "Standing desk", "doc_urls": ["https://docs.example.com/desk"]},
    )
    assert s == 201, prod
    s, radar, _ = api.call(
        "POST", "/api/radars", {"slug": "rivals", "name": "Rivals", "template": "competitor_landscape"}, role="editor"
    )
    assert s == 201, radar
    s, mod, _ = api.call(
        "POST",
        "/api/modules",
        {
            "slug": "competitor-watch",
            "name": "Competitor watch",
            "insight_kind": "competitor_move",
            "insight_label": "Competitor moves",
            "grants": ["kg_read", "kg_write_output", "web_search"],
        },
    )
    assert s == 201, mod
    fact = ctx.graph.create(Node(id="", type=NodeType.FACT, title="Acme ships 50k-cycle motor", evidence_url=URL), "u")
    sig = ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="Longer-life desk motors"), "module:signal")
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Why motor life matters",
            attributes={"content_type": "blog_post", "audience": "buyers"},
        ),
        "module:content",
    )
    ctx.graph.link(sig.id, EdgeType.SUPPORTS, fact.id, "u")
    ctx.reindex()
    return {"product": prod, "radar": radar, "fact": fact, "signal": sig, "idea": idea}


def _p(node: Node) -> str:
    return node.id


# ---- authorization matrix ----------------------------------------------------------------


def _routes(w: dict[str, Any]) -> list[tuple[str, str, Any, str]]:
    pid = w["product"]["id"]
    sig, idea = _p(w["signal"]), _p(w["idea"])
    return [
        ("GET", "/api/me", None, "viewer"),
        ("GET", "/api/nav", None, "viewer"),
        ("GET", "/api/users", None, "admin"),
        ("POST", "/api/users", {"email": "new@example.com"}, "admin"),
        ("PATCH", "/api/users/someone", {"role": "editor"}, "admin"),
        ("DELETE", "/api/users/someone", None, "admin"),
        ("GET", "/api/products", None, "viewer"),
        ("POST", "/api/products", {"name": "X"}, "admin"),
        ("GET", f"/api/products/{pid}", None, "viewer"),
        ("PATCH", f"/api/products/{pid}", {"version": 1}, "admin"),
        ("POST", f"/api/products/{pid}/sync", None, "admin"),
        # r2 review SR2-12: raw document passages and the document list are for editors and up.
        ("POST", f"/api/products/{pid}/ask", {"query": "motor life"}, "editor"),
        ("GET", f"/api/products/{pid}/documents", None, "editor"),
        ("POST", f"/api/products/{pid}/documents", {"filename": "a.pdf", "size": 10}, "admin"),
        ("GET", "/api/models", None, "viewer"),
        ("GET", "/api/modules", None, "viewer"),
        ("GET", "/api/modules/signal", None, "viewer"),
        ("PATCH", "/api/modules/signal", {"version": 1}, "admin"),
        ("POST", "/api/modules", {"slug": "x"}, "admin"),
        ("POST", "/api/modules/draft/run", {}, "admin"),
        ("GET", "/api/modules/signal/runs", None, "admin"),
        ("GET", "/api/runs", None, "admin"),
        ("GET", "/api/modules/scout/sources", None, "viewer"),
        (
            "POST",
            "/api/modules/scout/sources",
            {"name": "n", "type": "rss", "address": "https://f.example.com"},
            "admin",
        ),
        ("GET", "/api/andon", None, "viewer"),
        ("PUT", "/api/andon", {"genai_enabled": True}, "admin"),
        ("GET", "/api/graph/schema", None, "viewer"),
        ("POST", "/api/graph/search", {"query": "motor"}, "viewer"),
        ("GET", f"/api/graph/nodes/{sig}", None, "viewer"),
        ("GET", f"/api/graph/nodes/{sig}/neighbors", None, "viewer"),
        ("GET", "/api/graph/explore", None, "viewer"),
        ("DELETE", "/api/graph/nodes/topic/0000", None, "admin"),
        ("GET", "/api/insights/signals", None, "viewer"),
        ("GET", f"/api/insights/nodes/{sig}", None, "viewer"),
        ("POST", f"/api/nodes/{sig}/feedback", {"vote": "up"}, "editor"),
        ("DELETE", f"/api/nodes/{sig}/feedback", None, "editor"),
        ("PUT", f"/api/nodes/{idea}/working", None, "editor"),
        ("DELETE", f"/api/nodes/{idea}/working", None, "editor"),
        ("POST", f"/api/nodes/{idea}/draft", {}, "editor"),
        ("GET", "/api/feedback", None, "admin"),
        ("GET", "/api/radar-templates", None, "viewer"),
        ("GET", "/api/radars", None, "viewer"),
        ("POST", "/api/radars", {"slug": "r2", "name": "R2"}, "editor"),
        ("GET", "/api/radars/rivals", None, "viewer"),
        ("GET", "/api/radars/rivals/versions", None, "viewer"),
        ("GET", "/api/radars/rivals/export", None, "viewer"),
        ("GET", "/api/radars/rivals/template", None, "viewer"),
        ("GET", "/api/radars/rivals/suggestions", None, "viewer"),
        ("PUT", "/api/radars/rivals/entries", {"entries": [], "expected_version": 1}, "editor"),
        ("GET", "/api/audit", None, "admin"),
        ("GET", "/api/health", None, "admin"),
        ("POST", "/api/admin/sample-data", {}, "admin"),
        ("GET", "/api/admin/profile", None, "admin"),
        ("PUT", "/api/admin/profile", {"profile": {}, "expected_version": 0}, "admin"),
    ]


@pytest.mark.parametrize("role", ["anon", "viewer", "editor", "admin"])
def test_authorization_matrix(api: Client, world: dict[str, Any], role: str) -> None:
    failures = []
    for method, path, body, minimum in _routes(world):
        status, payload, _ = api.call(method, path, body, role=role)
        if role == "anon":
            ok = status == 401
        elif RANK[role] < RANK[minimum]:
            ok = status == 403
        else:
            ok = status not in (401, 403)
        if not ok:
            failures.append((method, path, status, payload))
    assert failures == []


def test_public_config_and_headers(api: Client) -> None:
    status, body, headers = api.call("GET", "/api/config", role="anon")
    assert status == 200 and body["genai_enabled"] is True and body["chat_runtime_arn"] == "arn:chat"
    assert headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Access-Control-Allow-Origin"] == "https://app.example.com"
    _, _, other = api.call("GET", "/api/config", role="anon", headers={"origin": "https://evil.example.com"})
    assert "Access-Control-Allow-Origin" not in other


def test_federated_sign_in_is_advertised_and_has_no_invitations(api: Client, ctx: TestContext) -> None:
    from dataclasses import replace

    assert api.call("GET", "/api/config", role="anon")[1]["identity_provider"] is None
    ctx.settings = replace(ctx.settings, identity_provider="SSO")
    assert api.call("GET", "/api/config", role="anon")[1]["identity_provider"] == "SSO"
    s, err, _ = api.call("POST", "/api/users", {"email": "new@example.com"})
    assert s == 400 and "first sign-in" in err["message"]
    # Only an administrator learns this: anyone else is still refused for the action itself.
    assert api.call("POST", "/api/users", {"email": "new@example.com"}, role="viewer")[0] == 403


def test_profile_is_public_and_admin_editable(api: Client) -> None:
    _, cfg, _ = api.call("GET", "/api/config", role="anon")
    assert cfg["profile"]["app_name"] == "Agentic Insights Radar"
    assert cfg["profile"]["lists"]["signals"]["label"] == "Signals"
    s, got, _ = api.call("GET", "/api/admin/profile")
    assert s == 200 and got["version"] == 0 and got["profile"]["lists"] == {}
    doc = {
        "app_name": "Trade Radar",
        "lists": {"signals": {"label": "Rule changes", "item": "Rule change"}, "gaps": {"visible": False}},
    }
    s, saved, _ = api.call("PUT", "/api/admin/profile", {"profile": doc, "expected_version": got["version"]})
    assert s == 200 and saved["version"] == 1 and saved["effective"]["lists"]["gaps"]["visible"] is False
    assert saved["updated_by"]
    # A stale editor is refused, not silently overwritten.
    s, err, _ = api.call("PUT", "/api/admin/profile", {"profile": {}, "expected_version": 0})
    assert s == 409 and err["details"]["current_version"] == 1
    assert api.call("PUT", "/api/admin/profile", {"profile": {"app_name": ""}, "expected_version": 1})[0] == 400
    assert api.call("PUT", "/api/admin/profile", {"profile": {}, "expected_version": 1, "x": 1})[0] == 400
    assert api.call("PUT", "/api/admin/profile", {"profile": [], "expected_version": 1})[0] == 400
    s, audit, _ = api.call("GET", "/api/audit")
    assert any(row.get("action") == "profile.set" for row in audit.get("items", []))


def test_errors_are_typed(api: Client) -> None:
    assert api.call("GET", "/api/nope")[0] == 404
    assert api.call("DELETE", "/api/me")[0] == 405
    s, b, _ = api.call("POST", "/api/radars", raw_body="{not json", role="editor")
    assert s == 400 and b["details"]["code"] == "invalid_json"
    s, b, _ = api.call("POST", "/api/radars", raw_body="[1,2]", role="editor")
    assert s == 400
    s, _, h = api.call("OPTIONS", "/api/radars", role="anon")
    assert s == 204 and "GET" in h["Access-Control-Allow-Methods"]
    s, b, _ = api.call("GET", "/api/graph/nodes/planet/x")
    assert s == 404


def test_me_reports_capabilities(api: Client) -> None:
    _, me, _ = api.call("GET", "/api/me", role="editor")
    assert me["role"] == "editor" and "radar.create" in me["can"] and "user.manage" not in me["can"]


# ---- functional journeys ------------------------------------------------------------------


def test_product_journey(api: Client, world: dict[str, Any], ctx: TestContext) -> None:
    pid = world["product"]["id"]
    s, up, _ = api.call("POST", f"/api/products/{pid}/documents", {"filename": "spec sheet.pdf", "size": 12})
    assert s == 201 and up["upload"]["fields"]["Content-Type"] == "application/pdf"
    ctx.s3.put_object(
        Bucket="example-assets-111122223333-us-east-1",
        Key=up["upload"]["fields"]["key"],
        Body=b"%PDF-1.7 abc",
        ContentType="application/pdf",
    )
    s, done, _ = api.call("POST", f"/api/products/{pid}/documents/{up['document']['id']}/complete")
    assert s == 200 and done["status"] == "indexing"
    s, ans, _ = api.call("POST", f"/api/products/{pid}/ask", {"query": "motor life"}, role="editor")
    assert s == 200 and ans["items"][0]["source"] == "document:spec.pdf"
    s, upd, _ = api.call("PATCH", f"/api/products/{pid}", {"version": 1, "capabilities": ["dual motor"]})
    assert s == 200 and upd["capabilities"] == ["dual motor"]
    s, _, _ = api.call("PATCH", f"/api/products/{pid}", {"version": 1, "summary": "stale"})
    assert s == 409
    assert api.call("DELETE", f"/api/products/{pid}/documents/{up['document']['id']}")[0] == 200
    assert api.call("DELETE", f"/api/products/{pid}")[0] == 200
    assert api.call("GET", f"/api/products/{pid}")[0] == 404


def test_module_journey(api: Client, world: dict[str, Any], ctx: TestContext) -> None:
    s, mods, _ = api.call("GET", "/api/modules", role="viewer")
    assert s == 200 and len(mods["items"]) == 8 and all("budget" in m for m in mods["items"])
    s, nav, _ = api.call("GET", "/api/nav", role="viewer")
    assert nav["custom"] == [
        {"kind": "insight:competitor_move", "label": "Competitor moves", "module": "competitor-watch"}
    ]
    s, m, _ = api.call(
        "PATCH",
        "/api/modules/competitor-watch",
        {"version": 1, "enabled": True, "interval_minutes": 60, "grants": ["kg_read", "kg_write_output"]},
    )
    assert s == 200 and m["enabled"] and m["grants"] == ["kg_read", "kg_write_output"]
    s, _b, _ = api.call("PATCH", "/api/modules/competitor-watch", {"version": 2, "template": "scout"})
    assert s == 400
    s, _b, _ = api.call("PATCH", "/api/modules/competitor-watch", {"version": 2, "grants": ["root"]})
    assert s == 400
    s, run, _ = api.call("POST", "/api/modules/competitor-watch/run", {})
    assert s == 202 and run["status"] == "running"
    assert ctx.launcher.payloads[-1]["module"]["slug"] == "competitor-watch"
    s, runs, _ = api.call("GET", "/api/runs")
    assert runs["items"][0]["run_id"] == run["run_id"]
    s, src, _ = api.call(
        "POST",
        "/api/modules/scout/sources",
        {"name": "Desk forum", "type": "web_page", "address": "https://forum.example.com/desks"},
    )
    assert s == 201
    s, _, _ = api.call(
        "POST",
        "/api/modules/scout/sources",
        {"name": "bad", "type": "web_page", "address": "https://169.254.169.254/latest"},
    )
    assert s == 400
    s, _, _ = api.call("POST", "/api/modules/scout/sources", {"name": "bad", "type": "ftp", "address": "https://x.com"})
    assert s == 400
    s, u, _ = api.call("PATCH", f"/api/modules/scout/sources/{src['id']}", {"enabled": False})
    assert s == 200 and not u["enabled"]
    assert api.call("DELETE", f"/api/modules/scout/sources/{src['id']}")[0] == 200
    assert api.call("DELETE", "/api/modules/scout")[0] == 400
    assert api.call("DELETE", "/api/modules/competitor-watch")[0] == 200


def test_andon_blocks_runs_and_hides_reason(api: Client, world: dict[str, Any]) -> None:
    s, st, _ = api.call("PUT", "/api/andon", {"genai_enabled": False, "reason": "incident 42"})
    assert s == 200 and not st["genai_enabled"]
    _, viewer_view, _ = api.call("GET", "/api/andon", role="viewer")
    assert viewer_view == {"genai_enabled": False}
    s, b, h = api.call("POST", "/api/modules/draft/run", {})
    assert s == 503 and b["code"] == "genai_disabled" and "Retry-After" not in h  # r3 B3-063
    pid = world["product"]["id"]
    assert api.call("POST", f"/api/products/{pid}/ask", {"query": "motor"}, role="editor")[0] == 503
    assert api.call("GET", "/api/radars/rivals", role="viewer")[0] == 200
    assert api.call("POST", "/api/graph/search", {"query": "motor"}, role="viewer")[0] == 200
    assert api.call("PUT", "/api/andon", {"genai_enabled": "yes"})[0] == 400


def test_graph_and_insight_journey(api: Client, world: dict[str, Any]) -> None:
    sig = world["signal"].id
    s, hits, _ = api.call("POST", "/api/graph/search", {"query": "desk motors", "types": ["signal"]}, role="viewer")
    assert s == 200 and hits["items"][0]["node"]["id"] == sig
    assert api.call("POST", "/api/graph/search", {"query": "x", "types": ["planet"]}, role="viewer")[0] == 400
    s, hood, _ = api.call("GET", f"/api/graph/nodes/{sig}/neighbors", role="viewer", query={"depth": "2"})
    assert {n["type"] for n in hood["nodes"]} == {"signal", "fact"} and "total" not in hood
    s, paged, _ = api.call("GET", f"/api/graph/nodes/{sig}/neighbors", role="viewer", query={"offset": "0"})
    assert s == 200 and paged["offset"] == 0 and paged["total"] == len(paged["nodes"]) - 1
    assert paged["nodes"][0]["id"] == sig and paged["truncated"] is False
    s, past, _ = api.call("GET", f"/api/graph/nodes/{sig}/neighbors", role="viewer", query={"offset": "50"})
    assert s == 200 and [n["id"] for n in past["nodes"]] == [sig] and past["total"] == paged["total"]
    # Like every integer query, an out-of-range offset is clamped; paging two hops is refused.
    assert api.call("GET", f"/api/graph/nodes/{sig}/neighbors", role="viewer", query={"offset": "-1"})[1]["offset"] == 0
    bad = {"offset": "0", "depth": "2"}
    assert api.call("GET", f"/api/graph/nodes/{sig}/neighbors", role="viewer", query=bad)[0] == 400
    assert api.call("GET", f"/api/graph/nodes/{sig}/neighbors", role="viewer", query={"offset": "x"})[0] == 400
    s, ex, _ = api.call("GET", "/api/graph/explore", role="viewer", query={"limit": "10"})
    assert s == 200 and ex["nodes"][0]["degree"] >= 1
    s, page, _ = api.call("GET", "/api/insights/signals", role="viewer")
    assert page["items"][0]["id"] == sig
    s, _, _ = api.call("GET", "/api/insights/signals", role="viewer", query={"sort": "weird"})
    assert s == 400
    s, page, _ = api.call("GET", "/api/insights/insight:competitor_move", role="viewer")
    assert s == 200 and page["items"] == []
    s, detail, _ = api.call("GET", f"/api/insights/nodes/{sig}", role="viewer")
    assert detail["evidence"]["supports"][0]["node"]["evidence_url"] == URL
    s, fb, _ = api.call("POST", f"/api/nodes/{sig}/feedback", {"vote": "down"}, role="editor")
    assert s == 400
    s, fb, _ = api.call(
        "POST", f"/api/nodes/{sig}/feedback", {"vote": "down", "comment": "single source"}, role="editor"
    )
    assert fb["down"] == 1
    s, fb, _ = api.call("DELETE", f"/api/nodes/{sig}/feedback", role="editor")
    assert s == 200 and fb["down"] == 0 and fb["mine"] is None  # withdrawn, not replaced by another vote
    assert api.call("DELETE", "/api/nodes/signal/does-not-exist/feedback", role="editor")[0] == 404
    idea = world["idea"].id
    s, w, _ = api.call("PUT", f"/api/nodes/{idea}/working", role="editor")
    assert w["working_on"][0]["email"] == "editor@example.com"
    # drafts ship disabled: an editor cannot start one until an administrator enables the module (B-006)
    assert api.call("POST", f"/api/nodes/{idea}/draft", {"brief": "focus on durability"}, role="editor")[0] == 400
    version = api.call("GET", "/api/modules/draft")[1]["version"]
    assert api.call("PATCH", "/api/modules/draft", {"enabled": True, "version": version})[0] == 200
    s, _run, _ = api.call("POST", f"/api/nodes/{idea}/draft", {"brief": "focus on durability"}, role="editor")
    assert s == 202
    assert api.call("POST", f"/api/nodes/{sig}/draft", {}, role="editor")[0] == 400
    s, _, _ = api.call("DELETE", f"/api/graph/nodes/{world['fact'].id}")
    assert s == 200 and api.call("GET", f"/api/graph/nodes/{world['fact'].id}", role="viewer")[0] == 404


def test_radar_journey(api: Client, world: dict[str, Any]) -> None:
    s, r, _ = api.call("GET", "/api/radars/rivals", role="viewer")
    assert r["version"] == 1 and r["entries"] == [] and not r["can_edit"]
    entries = [
        {"key": "k1", "title": "Linak", "sector": "Direct", "ring": "Leader", "marker": "Gaining"},
        {"key": "k2", "title": "FlexiSpot", "sector": "Direct", "ring": "Challenger"},
    ]
    assert api.call("PUT", "/api/radars/rivals/entries", {"entries": entries, "expected_version": 1})[0] == 200
    s, b, _ = api.call("PUT", "/api/radars/rivals/entries", {"entries": entries, "expected_version": 1}, role="editor")
    assert s == 409
    bad = [{"key": "k", "title": "X", "sector": "Nowhere", "ring": "Leader"}]
    s, b, _ = api.call("PUT", "/api/radars/rivals/entries", {"entries": bad, "expected_version": 2}, role="editor")
    assert s == 400 and b["details"]["unmatched"][0]["field"] == "sector"
    s, rep, _ = api.call(
        "POST",
        "/api/radars/rivals/import",
        {"format": "csv", "text": "title,sector,ring\nIKEA,Substitutes,Watch\n", "dry_run": True},
        role="editor",
    )
    assert s == 200 and rep["accepted"]
    s, imp, _ = api.call(
        "POST",
        "/api/radars/rivals/import",
        {
            "format": "csv",
            "mode": "merge",
            "expected_version": 2,
            "text": "title,sector,ring\nIKEA,Substitutes,Watch\n",
        },
        role="editor",
    )
    assert s == 200 and imp["entry_count"] == 3
    s, csv_text, h = api.call("GET", "/api/radars/rivals/export", role="viewer", query={"format": "csv"})
    assert s == 200 and "IKEA" in csv_text and h["Content-Type"].startswith("text/csv")
    s, d, _ = api.call("GET", "/api/radars/rivals/diff", role="viewer", query={"from": "2", "to": "3"})
    assert d["changes"]["added"][0]["title"] == "IKEA"
    s, _, _ = api.call("POST", "/api/radars/rivals/rollback", {"version": 2, "expected_version": 3}, role="editor")
    assert s == 200
    s, meta, _ = api.call("PATCH", "/api/radars/rivals", {"version_lock": 1, "mode": "copilot"}, role="editor")
    assert s == 200 and meta["mode"] == "copilot"
    s, acc, _ = api.call(
        "PUT",
        "/api/radars/rivals/access",
        {"editors": ["viewer@example.com"], "version_lock": meta["version_lock"], "confirm_unknown": True},
        role="editor",
    )
    assert s == 200 and acc["editors"] == ["viewer@example.com"]
    # viewer is listed as an editor but their role is viewer: still cannot edit
    s, _, _ = api.call("PUT", "/api/radars/rivals/entries", {"entries": [], "expected_version": 4}, role="viewer")
    assert s == 403
    s, _sch, _ = api.call(
        "PUT",
        "/api/radars/rivals/schema",
        {
            "schema": {
                "sectors": ["Direct", "Adjacent", "Emerging", "Substitutes"],
                "rings": ["Leader", "Challenger", "Watch"],
            },
            "expected_version": 4,
            "drop_unmatched": True,
        },
        role="editor",
    )
    assert s == 200
    assert api.call("DELETE", "/api/radars/rivals", role="viewer")[0] == 403
    assert api.call("DELETE", "/api/radars/rivals", role="editor")[0] == 200


def test_users_journey(api: Client, ctx: TestContext) -> None:
    s, u, _ = api.call("POST", "/api/users", {"email": "Boss@Example.com", "role": "admin"})
    assert s == 201 and u["role"] == "admin" and u["email"] == "boss@example.com"
    assert api.call("POST", "/api/users", {"email": "boss@example.com"})[0] == 409
    assert api.call("POST", "/api/users", {"email": "nope"})[0] == 400
    assert api.call("POST", "/api/users", {"email": "a@example.com", "role": "god"})[0] == 400
    name = u["username"]
    s, b, _ = api.call("PATCH", f"/api/users/{name}", {"role": "viewer"})
    assert s == 409 and b["details"]["code"] == "last_admin"
    s, u2, _ = api.call("POST", "/api/users", {"email": "two@example.com", "role": "admin"})
    s, d, _ = api.call("PATCH", f"/api/users/{name}", {"role": "editor"})
    assert s == 200 and d["role"] == "editor"
    s, d, _ = api.call("PATCH", f"/api/users/{name}", {"enabled": False})
    assert s == 200 and not d["enabled"]
    assert api.call("PATCH", f"/api/users/{name}", {})[0] == 400
    s, lst, _ = api.call("GET", "/api/users")
    assert {x["email"] for x in lst["items"]} == {"boss@example.com", "two@example.com"}
    assert api.call("DELETE", f"/api/users/{u2['username']}")[0] == 409
    assert api.call("DELETE", f"/api/users/{name}")[0] == 200
    assert api.call("GET", f"/api/users/{name}")[0] in (404, 405)


def test_audit_records_mutations(api: Client, world: dict[str, Any]) -> None:
    _s, log, _ = api.call("GET", "/api/audit")
    actions = {x["action"] for x in log["items"]}
    assert {"product.create", "radar.create", "module.create"} <= actions
    assert api.call("GET", "/api/audit", query={"date": "02-10-2026"})[0] == 400


def test_health(api: Client, world: dict[str, Any]) -> None:
    s, h, _ = api.call("GET", "/api/health")
    assert s == 200 and h["genai_enabled"] and len(h["budgets"]) == 8


# ---- agent tools ------------------------------------------------------------------------------


def _token(ctx: TestContext, slug: str, product: str | None = None, target: str | None = None) -> str:
    """A run token for a running run (tools refuse a run that has ended)."""
    ensure_running(ctx, slug, target_id=target)
    return ctx.tokens.issue(slug, "run-1", ctx.modules.get(slug).tools, 600, product)


def test_a_finished_run_cannot_keep_calling_tools(api: Client, ctx: TestContext, world: dict[str, Any]) -> None:
    from dataclasses import replace

    from insights_radar.modules.repository import RunStatus

    tok = _token(ctx, "signal")
    assert api.tool("kg_schema", tok)[0] == 200
    run = ctx.modules.get_run("signal", "run-1")
    ctx.modules.put_run(replace(run, status=RunStatus.FAILED))  # its task timed out and it was finalized
    status, body = api.tool("kg_schema", tok)
    assert status == 409 and body["code"] == "run_finished"
    assert api.tool("budget_status", tok)[0] == 200  # bookkeeping still answers, so it can stop cleanly


def test_agent_tools_require_role_token_and_grant(api: Client, ctx: TestContext, world: dict[str, Any]) -> None:
    tok = _token(ctx, "signal")
    assert api.tool("kg_schema", tok)[0] == 200
    assert api.tool("kg_schema", tok, iam_arn=None)[0] == 403
    assert api.tool("kg_schema", tok, iam_arn="arn:aws:sts::1:assumed-role/attacker/s")[0] == 403
    assert api.tool("kg_schema", None)[0] == 401
    assert api.tool("kg_schema", "garbage.token")[0] == 401
    assert api.tool("radar_suggest", tok, {"slug": "rivals"})[0] == 403
    assert api.tool("no_such_tool", tok)[0] == 404


def test_scout_writes_evidence_and_cursor(api: Client, ctx: TestContext, world: dict[str, Any]) -> None:
    src = ctx.modules.add_source(
        "scout",
        "Forum",
        __import__("insights_radar.modules.model", fromlist=["SourceType"]).SourceType.WEB_PAGE,
        "https://forum.example.com",
    )
    tok = _token(ctx, "scout")
    s, org = api.tool("kg_upsert_entity", tok, {"type": "organization", "name": "Acme Motors", "aliases": ["ACME"]})
    assert s == 200 and org["result"]["created"]
    s, again = api.tool("kg_upsert_entity", tok, {"type": "organization", "name": "acme-motors"})
    assert not again["result"]["created"] and again["result"]["id"] == org["result"]["id"]
    s, ev = api.tool(
        "kg_add_evidence",
        tok,
        {
            "type": "event",
            "title": "Acme launches brushless desk motor",
            "evidence_url": "https://acme.example.com/news",
            "about": [org["result"]["id"]],
            "reported_by": org["result"]["id"],
            "published_at": "2026-09-30T00:00:00Z",
        },
    )
    assert s == 200 and len(ev["result"]["links"]) == 2
    assert (
        api.tool("kg_add_evidence", tok, {"type": "event", "title": "x", "evidence_url": "http://insecure"})[0] == 400
    )
    assert api.tool("kg_add_evidence", tok, {"type": "signal", "title": "x", "evidence_url": URL})[0] == 400
    assert api.tool("kg_save_output", tok, {"title": "x"})[0] == 403
    s, _cur = api.tool("source_update_cursor", tok, {"source_id": src.id, "cursor": "after 2026-09-30"})
    assert s == 200 and ctx.modules.get_source("scout", src.id).cursor == "after 2026-09-30"
    s, srcs = api.tool("source_list", tok)
    assert [x["id"] for x in srcs["result"]["sources"]] == [src.id]
    assert srcs["result"]["sources"][0]["read_at"] == ""
    assert api.tool("source_list", _token(ctx, "signal"))[0] == 403
    # The runtime stamps a real read; neither the cursor nor the admin edit time moves.
    before = ctx.modules.get_source("scout", src.id)
    s, read = api.tool("source_mark_read", tok, {"source_id": src.id})
    after = ctx.modules.get_source("scout", src.id)
    assert s == 200 and read["result"]["at"] == after.read_at == after.tried_at != ""
    assert (after.cursor, after.updated_at) == (before.cursor, before.updated_at)
    # A failed fetch is an attempt, not a read: tried_at moves, read_at stays.
    ctx.clock.advance(timedelta(seconds=60))
    s, failed = api.tool("source_mark_read", tok, {"source_id": src.id, "ok": False})
    tried = ctx.modules.get_source("scout", src.id)
    assert s == 200 and not failed["result"]["ok"] and tried.tried_at > tried.read_at == after.read_at
    # A new address is a new source: never read, so it sorts first again.
    moved = ctx.modules.update_source("scout", src.id, {"address": "https://moved.example.com/feed"})
    assert (moved.read_at, moved.tried_at) == ("", "")
    renamed = ctx.modules.update_source(
        "scout", src.id, {"name": "Renamed", "address": "https://moved.example.com/feed"}
    )
    assert renamed.name == "Renamed"
    assert api.tool("source_mark_read", tok, {"source_id": "nope000000"})[0] == 404
    assert api.tool("source_mark_read", _token(ctx, "signal"), {"source_id": src.id})[0] == 403
    ctx.modules.update_source("scout", src.id, {"enabled": False})
    assert api.tool("source_mark_read", tok, {"source_id": src.id})[0] == 404  # paused: not stamped


def test_signal_writes_outputs_only_its_own(api: Client, ctx: TestContext, world: dict[str, Any]) -> None:
    tok = _token(ctx, "signal")
    s, out = api.tool(
        "kg_save_output",
        tok,
        {
            "title": "Brushless motors spread",
            "description": "<p>three vendors</p>",
            "confidence": 0.7,
            "supports": [world["fact"].id],
        },
    )
    assert s == 200, out
    node = ctx.graph.get(out["result"]["id"])
    assert node.type is NodeType.SIGNAL and node.module == "signal" and node.created_by == "module:signal"
    s, upd = api.tool("kg_update_output", tok, {"id": node.id, "expected_version": 1, "summary": "now four"})
    assert s == 200 and upd["result"]["version"] == 2
    other = world["signal"].id  # created by "module:signal" but without module field -> not owned
    assert api.tool("kg_update_output", tok, {"id": other, "expected_version": 1, "summary": "x"})[0] == 403
    assert api.tool("kg_link", tok, {"from": other, "type": "supports", "to": world["fact"].id})[0] == 403
    trend_tok = _token(ctx, "trend")
    assert api.tool("kg_update_output", trend_tok, {"id": node.id, "expected_version": 2, "summary": "x"})[0] == 403
    s, t = api.tool("kg_save_output", trend_tok, {"title": "Durability becomes a buying factor", "supports": [node.id]})
    assert s == 200 and ctx.graph.get(t["result"]["id"]).type is NodeType.TREND


def test_gap_requires_request_fields(api: Client, ctx: TestContext, world: dict[str, Any]) -> None:
    # r2 B2-042: the module's own product, not the token's frozen copy, is the authority.
    ctx.modules.update("gap", {"product_id": world["product"]["id"]}, ctx.modules.get("gap").version)
    tok = _token(ctx, "gap", world["product"]["id"])
    assert api.tool("kg_save_output", tok, {"title": "No USB-C port"})[0] == 400
    s, _g = api.tool(
        "kg_save_output",
        tok,
        {
            "title": "No USB-C port",
            "addresses": [world["signal"].id],
            "attributes": {
                "request_title": "USB-C support in DeskPro",
                "problem": "Buyers charge devices at the desk",
                "impact": "Lost sales to rivals",
            },
        },
    )
    assert s == 200
    s, ctxt = api.tool("run_context", tok)
    assert "definition" not in ctxt["result"]  # the model can call run_context; MCP URLs stay out of it
    s, defn = api.tool("run_definition", tok)
    assert defn["result"]["definition"]["slug"] == ctx.modules.get(ctxt["result"]["module"]).slug  # stored copy
    assert "Product: DeskPro" in ctxt["result"]["product"]
    s, pk = api.tool("product_knowledge_search", tok, {"query": "usb port"})
    assert s == 200 and pk["result"]["results"][0]["source"] == "document:spec.pdf"
    s, prof = api.tool("product_profile", tok)
    assert prof["result"]["name"] == "DeskPro"


def test_curator_suggests_but_cannot_write(api: Client, ctx: TestContext, world: dict[str, Any]) -> None:
    tok = _token(ctx, "curator")
    s, lst = api.tool("radar_list", tok)
    assert lst["result"]["radars"] == []  # manual radars are hidden from the curator
    ctx.radars.update_meta(
        ctx.radars.get("rivals").__class__
        and __import__("insights_radar.identity.principal", fromlist=["Principal"]).Principal.from_claims(
            {"sub": "u-editor", "email": "editor@example.com", "cognito:groups": "[editor]"}
        ),
        "rivals",
        {"mode": "copilot"},
        1,
    )
    s, lst = api.tool("radar_list", tok)
    assert [r["slug"] for r in lst["result"]["radars"]] == ["rivals"]
    s, sug = api.tool(
        "radar_suggest",
        tok,
        {
            "slug": "rivals",
            "proposal": {"title": "Acme", "sector": "Emerging", "ring": "Watch"},
            "reason": "Three launch reports this month",
            "evidence": [world["fact"].id],
        },
    )
    assert s == 200 and sug["result"]["is_new"]
    s, got = api.tool("radar_get", tok, {"slug": "rivals"})
    assert got["result"]["open_suggestions"] == [
        {
            "entry_key": sug["result"]["entry_key"],
            "is_new": True,
            "proposal": {"title": "Acme", "sector": "Emerging", "ring": "Watch", "marker": None},
        }
    ]
    assert api.tool("kg_save_output", tok, {"title": "x"})[0] == 403


def test_draft_saves_markdown(api: Client, ctx: TestContext, world: dict[str, Any]) -> None:
    idea = world["idea"].id
    tok = _token(ctx, "draft", target=idea)  # r2 B2-101: a draft run is started for one idea
    assert api.call("PUT", f"/api/nodes/{idea}/working", role="editor")[0] == 200
    s, d = api.tool("draft_get_idea", tok, {"id": idea})
    assert s == 200 and d["result"]["node"]["id"] == idea
    assert d["result"]["working_on"] == 1 and "editor@example.com" not in json.dumps(d)  # no emails to the model
    assert api.tool("draft_save", tok, {"idea_id": idea, "title": "t", "markdown": "too short"})[0] == 400
    md = "# Why motor life matters\n\n" + ("Durable motors reduce returns. " * 20)
    s, saved = api.tool("draft_save", tok, {"idea_id": idea, "title": "Why motor life matters", "markdown": md})
    assert s == 200
    status, text, h = api.call("GET", f"/api/drafts/{saved['result']['id']}", role="editor")
    assert status == 200 and text.startswith("# Why motor life") and h["Content-Type"].startswith("text/markdown")
    assert api.call("GET", f"/api/drafts/{idea}", role="editor")[0] == 404
    # A key planted in the node's attributes (agents can write attributes) is never followed.
    draft = ctx.graph.get(saved["result"]["id"])
    ctx.graph.update(
        draft.id, {"attributes": {**draft.attributes, "markdown_key": "docs/secret.pdf"}}, draft.version, "module:draft"
    )
    status, text, _ = api.call("GET", f"/api/drafts/{draft.id}", role="editor")
    assert status == 200 and text.startswith("# Why motor life")
    detail = ctx.insights.detail(idea)
    assert detail["evidence"]["drafts"][0]["node"]["id"] == saved["result"]["id"]


def test_progress_and_budget_tools(api: Client, ctx: TestContext, world: dict[str, Any]) -> None:
    tok = _token(ctx, "trend")
    s, p = api.tool("progress_update", tok, {"last_processed_at": "2026-10-01T00:00:00Z", "remaining": 7})
    assert s == 200 and p["result"]["remaining_history"] == [7]
    assert api.tool("progress_update", tok, {"last_processed_at": "yesterday"})[0] == 400
    # A planted future cursor is clamped to now; an offset timestamp is stored in UTC.
    s, p = api.tool("progress_update", tok, {"last_processed_at": "9999-12-31T00:00:00Z"})
    assert p["result"]["last_processed_at"] == iso(ctx.clock.now())
    s, p = api.tool("progress_update", tok, {"last_processed_at": "2026-10-01T05:00:00+05:00"})
    assert p["result"]["last_processed_at"] == iso(ctx.clock.now())  # earlier than the stored one: unchanged
    s, b = api.tool("budget_status", tok)
    assert b["result"]["state"] == "open" and b["result"]["guidance"] == "work normally"
    s, r = api.tool("kg_recent", tok, {"type": "signal"})
    assert s == 200 and r["result"]["items"]
    assert api.tool("kg_recent", tok, {"type": "planet"})[0] == 400
    s, srch = api.tool("kg_search", tok, {"query": "desk motors"})
    assert s == 200 and "note" in srch["result"]
    sid = ctx.graph.recent(NodeType.SIGNAL).items[0].id
    s, _g = api.tool("kg_get", tok, {"id": sid})
    assert api.tool("kg_get", tok, {"ids": [sid]})[0] == 200
    assert api.tool("kg_get", tok, {"ids": []})[0] == 400  # an empty list is the agent's mistake, not a 500
    assert s == 200
    s, nb = api.tool("kg_neighbors", tok, {"id": ctx.graph.recent(NodeType.SIGNAL).items[0].id, "depth": 2})
    assert s == 200 and "nodes" in nb["result"]
    assert CALLER_ARN.endswith("session-1")


def test_a_run_sees_its_own_hold_as_its_budget(api: Client, ctx: TestContext) -> None:
    from dataclasses import replace

    from insights_radar.modules.repository import ModuleRun, RunStatus

    m = ctx.modules.get("trend")
    day = ctx.modules.reserve("trend", m.daily_token_budget, m.daily_token_budget)  # this run holds it all
    run = ModuleRun(
        "trend",
        "run-1",
        RunStatus.RUNNING,
        "manual",
        iso(ctx.clock.now()),
        "u",
        budget_hold=m.daily_token_budget,
        hold_day=day,
    )
    ctx.modules.put_run(run)
    s, b = api.tool("budget_status", _token(ctx, "trend"))
    assert s == 200 and b["result"]["state"] == "open" and b["result"]["remaining"] == m.daily_token_budget
    ctx.modules.put_run(replace(run, status=RunStatus.SUCCEEDED))  # finished: its hold is no longer its own
    assert api.tool("budget_status", _token(ctx, "trend"))[1]["result"]["state"] == "closed"


def test_the_last_admin_cannot_be_removed_by_email_alias(api: Client, ctx: TestContext) -> None:
    s, u, _ = api.call("POST", "/api/users", {"email": "solo@example.com", "role": "admin"})
    assert s == 201 and u["username"] != "solo@example.com"  # a UUID, as in the real pool
    for method, body in (("DELETE", None), ("PATCH", {"role": "viewer"}), ("PATCH", {"enabled": False})):
        s, b, _ = api.call(method, "/api/users/solo@example.com", body)
        assert s == 409 and b["details"]["code"] == "last_admin", (method, body)
    assert ctx.users.get(u["username"]).role.value == "admin"


def test_concurrent_demotions_cannot_leave_no_admin(
    api: Client, ctx: TestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both requests passed the guard before either landed: simulate by skipping it.
    s, u, _ = api.call("POST", "/api/users", {"email": "only@example.com", "role": "admin"})
    monkeypatch.setattr(ctx.users, "_guard_last_admin", lambda username: None)
    name = u["username"]
    for method, body in (("PATCH", {"role": "editor"}), ("PATCH", {"enabled": False}), ("DELETE", None)):
        s, b, _ = api.call(method, f"/api/users/{name}", body)
        assert s == 409 and b["details"]["code"] == "last_admin", (method, body)
        view = ctx.users.get(name)
        assert view.role.value == "admin" and view.enabled  # the change undid itself


def test_a_demoted_or_disabled_admin_loses_admin_at_once(api: Client, ctx: TestContext) -> None:
    from insights_radar.identity.principal import Principal, PrincipalKind, Role
    from insights_radar.identity.users import LiveRoles

    _, a, _ = api.call("POST", "/api/users", {"email": "first@example.com", "role": "admin"})
    _, b, _ = api.call("POST", "/api/users", {"email": "second@example.com", "role": "admin"})
    now = [0.0]
    live = LiveRoles(ctx.users, ttl=30, now=lambda: now[0])
    token = Principal(PrincipalKind.USER, b["username"], Role.ADMIN, "second@example.com")
    assert live.confirm(token).role is Role.ADMIN
    ctx.users.set_role(b["username"], Role.EDITOR)
    assert live.confirm(token).role is Role.ADMIN  # cached for up to ttl seconds
    now[0] = 31
    assert live.confirm(token).role is Role.EDITOR  # the old token no longer carries admin
    ctx.users.set_enabled(b["username"], False)
    now[0] = 62
    with pytest.raises(UnauthorizedError, match="disabled"):
        live.confirm(Principal(PrincipalKind.USER, b["username"], Role.EDITOR))
    with pytest.raises(UnauthorizedError, match="no longer exists"):  # viewers are checked too
        live.confirm(Principal(PrincipalKind.USER, "not-a-pool-user", Role.VIEWER))
    _, v, _ = api.call("POST", "/api/users", {"email": "reader@example.com", "role": "viewer"})
    reader = Principal(PrincipalKind.USER, v["username"], Role.VIEWER)
    assert live.confirm(reader) is reader
    ctx.users.set_enabled(v["username"], False)
    now[0] = 93
    with pytest.raises(UnauthorizedError, match="disabled"):  # an offboarded viewer loses reads at once
        live.confirm(reader)
    with pytest.raises(UnauthorizedError, match="no longer exists"):
        live.confirm(Principal(PrincipalKind.USER, "not-a-pool-user", Role.ADMIN))
    module = Principal(PrincipalKind.MODULE, "m/r", Role.VIEWER, module_slug="m", run_id="r")
    assert live.confirm(module) is module
    assert a["role"] == "admin"


def test_routes_use_the_live_role(api: Client, ctx: TestContext) -> None:
    from insights_radar.identity.principal import Principal, Role

    class Demoted:
        def confirm(self, principal: Principal) -> Principal:
            from dataclasses import replace

            return replace(principal, role=Role.VIEWER)

    ctx.live_roles = Demoted()  # type: ignore[assignment]
    assert api.call("GET", "/api/users")[0] == 403  # an admin token, but the live role is viewer


def test_only_module_managers_see_a_source_address(api: Client) -> None:
    body = {
        "name": "Vendor API",
        "type": "public_api",
        "address": "https://api.example.com/v1/feed?api_key=k123",
        "instruction": "newest items first",
    }
    s, _, _ = api.call("POST", "/api/modules/scout/sources", body)
    assert s == 201
    _, full, _ = api.call("GET", "/api/modules/scout/sources")
    assert full["items"][0]["address"].endswith("api_key=k123")
    for role in ("viewer", "editor"):
        s, seen, _ = api.call("GET", "/api/modules/scout/sources", role=role)
        assert s == 200
        assert seen["items"] == [
            {
                "id": full["items"][0]["id"],
                "module": "scout",
                "name": "Vendor API",
                "type": "public_api",
                "enabled": True,
            }
        ]


def test_a_run_with_no_record_holds_nothing(api: Client, ctx: TestContext) -> None:
    tok = ctx.tokens.issue("trend", "unrecorded", ctx.modules.get("trend").tools, 600)
    s, b = api.tool("budget_status", tok)  # bookkeeping answers; the run's hold counts as zero
    assert s == 200 and b["result"]["consumed"] == 0
    assert api.tool("kg_schema", tok)[0] == 404  # anything else needs a recorded, running run


def test_sample_data_fills_the_graph_and_a_radar_once(api: Client) -> None:
    status, first, _ = api.call("POST", "/api/admin/sample-data", {})
    assert status == 200 and first["nodes_created"] == 26 and first["radar"] == "sample-crm-competitors"
    status, again, _ = api.call("POST", "/api/admin/sample-data", {})
    assert status == 200 and again["nodes_created"] == 0 and again["edges_created"] == 0
    _, hood, _ = api.call("GET", "/api/graph/explore", role="editor")
    assert len(hood["nodes"]) >= 26 and hood["edges"]
    # r3 B3-018: a viewer does not see the gap and content idea derived from product documents.
    _, seen, _ = api.call("GET", "/api/graph/explore", role="viewer")
    assert len(seen["nodes"]) == len(hood["nodes"]) - 2
    _, view, _ = api.call("GET", "/api/radars/sample-crm-competitors", role="viewer")
    assert len(view["entries"]) == 4 and all(e["node_id"].startswith("organization/") for e in view["entries"])
    _, log, _ = api.call("GET", "/api/audit")
    assert any(x["action"] == "admin.sample_data" for x in log["items"])


def test_ha076_which_radars_place_an_entry(api: Client, world: dict[str, Any]) -> None:
    node_id = "organization/0190a"
    entries = [
        {"key": "k1", "title": "Linak Desks", "sector": "Direct", "ring": "Leader", "node_id": node_id},
        {"key": "k2", "title": "FlexiSpot", "sector": "Direct", "ring": "Challenger"},
    ]
    assert api.call("PUT", "/api/radars/rivals/entries", {"entries": entries, "expected_version": 1})[0] == 200
    api.call("POST", "/api/radars", {"slug": "empty-one", "name": "Empty", "template": "blank"})
    s, b, _ = api.call("GET", "/api/radars/entries", role="viewer", query={"q": "linak desk"})
    assert s == 200 and [(i["radar"], i["title"], i["ring"]) for i in b["items"]] == [
        ("rivals", "Linak Desks", "Leader")
    ]
    assert b["items"][0]["radar_name"] == "Rivals" and "description" not in b["items"][0]
    s, b, _ = api.call("GET", "/api/radars/entries", query={"q": "flexi"})
    assert [i["key"] for i in b["items"]] == ["k2"]
    s, b, _ = api.call("GET", "/api/radars/entries", query={"node_id": node_id})
    assert [i["key"] for i in b["items"]] == ["k1"]
    assert api.call("GET", "/api/radars/entries")[0] == 400
    assert api.call("GET", "/api/radars/entries", query={"q": "x"})[0] == 400
    assert api.call("GET", "/api/radars/entries", query={"node_id": "nope"})[0] == 400
    s, _, _ = api.call("POST", "/api/radars", {"slug": "entries", "name": "E", "template": "blank"})
    assert s == 400  # the slug is the search's address


def test_favourite_radars_are_per_person_and_follow_the_radar(
    api: Client, ctx: TestContext, world: dict[str, Any]
) -> None:
    assert api.call("GET", "/api/me/favourites", role="viewer")[1] == {"radars": []}
    s, b, _ = api.call("PUT", "/api/me/favourites/radars/rivals", {}, role="viewer")
    assert s == 200 and b == {"slug": "rivals", "favourite": True}
    assert api.call("PUT", "/api/me/favourites/radars/nope", {}, role="viewer")[0] == 404
    assert api.call("GET", "/api/me/favourites", role="viewer")[1] == {"radars": ["rivals"]}
    assert api.call("GET", "/api/me/favourites", role="editor")[1] == {"radars": []}  # another person's
    s, b, _ = api.call("DELETE", "/api/me/favourites/radars/rivals", role="viewer")
    assert s == 200 and b["favourite"] is False
    assert api.call("GET", "/api/me/favourites", role="viewer")[1] == {"radars": []}
    # A deleted radar drops out, and a new radar with its slug does not inherit the star.
    api.call("POST", "/api/radars", {"slug": "gone", "name": "Gone", "template": "blank"})
    api.call("PUT", "/api/me/favourites/radars/gone", {})
    assert api.call("GET", "/api/me/favourites")[1] == {"radars": ["gone"]}
    assert api.call("DELETE", "/api/radars/gone")[0] in (200, 204)
    assert api.call("GET", "/api/me/favourites")[1] == {"radars": []}
    ctx.clock.advance(timedelta(seconds=1))
    api.call("POST", "/api/radars", {"slug": "gone", "name": "Gone again", "template": "blank"})
    assert api.call("GET", "/api/me/favourites")[1] == {"radars": []}


def test_ha003_an_entry_tells_its_own_story(api: Client, world: dict[str, Any]) -> None:
    def save(entries: list[dict[str, Any]], version: int, role: str = "admin") -> None:
        assert (
            api.call("PUT", "/api/radars/rivals/entries", {"entries": entries, "expected_version": version}, role=role)[
                0
            ]
            == 200
        )

    linak = {"key": "k1", "title": "Linak", "sector": "Direct", "ring": "Leader"}
    other = {"key": "k2", "title": "FlexiSpot", "sector": "Direct", "ring": "Challenger"}
    save([linak, other], 1)
    save([{**linak, "ring": "Challenger"}, other], 2)  # moved
    save([other], 3, role="editor")  # removed
    save([{**linak, "sector": "Adjacent", "description": "Back"}, other], 4)  # restored, elsewhere
    save([{**linak, "sector": "Adjacent", "description": "Changed"}, other], 5)  # edited
    save([{**linak, "sector": "Adjacent", "description": "Changed"}, {**other, "ring": "Niche"}], 6)  # not this one
    s, h, _ = api.call("GET", "/api/radars/rivals/entries/k1/history", role="viewer")
    assert s == 200 and h["current"] is True and h["to_version"] == 7
    got = [(e["version"], e["change"]) for e in h["events"]]
    assert got == [(2, "added"), (3, "changed"), (4, "removed"), (5, "restored"), (6, "changed")]
    assert h["events"][1]["ring_from"] == "Leader" and h["events"][1]["ring"] == "Challenger"
    assert h["events"][4]["fields"] == ["description"] and h["events"][2]["ai"] is False
    assert api.call("GET", "/api/radars/rivals/entries/nope/history")[0] == 404
    assert api.call("GET", "/api/radars/none/entries/k1/history")[0] == 404


def test_ha007_versions_are_tagged_and_opened_by_their_tag(api: Client, world: dict[str, Any]) -> None:
    entry = {"key": "k1", "title": "Linak", "sector": "Direct", "ring": "Leader"}
    assert api.call("PUT", "/api/radars/rivals/entries", {"entries": [entry], "expected_version": 1})[0] == 200
    path = "/api/radars/rivals/versions/2/tag"
    assert api.call("PUT", path, {"tag": "Q3 2026"}, role="viewer")[0] == 403
    s, b, _ = api.call("PUT", path, {"tag": "  Q3   2026 "}, role="editor")
    assert s == 200 and b == {"tag": "Q3 2026", "version": 2}
    s, b, _ = api.call("PUT", "/api/radars/rivals/versions/1/tag", {"tag": "q3-2026"}, role="editor")
    assert s == 409 and b["details"] == {"code": "tag_taken", "tag": "Q3 2026", "version": 2}
    for bad in ("2026", "-x", "a" * 41, "x/y"):
        assert api.call("PUT", path, {"tag": bad}, role="editor")[0] == 400
    assert api.call("PUT", "/api/radars/rivals/versions/9/tag", {"tag": "later"}, role="editor")[0] == 404
    assert api.call("PUT", "/api/radars/rivals/versions/x/tag", {"tag": "later"}, role="editor")[0] == 400
    api.call("PUT", "/api/radars/rivals/entries", {"entries": [{**entry, "ring": "Niche"}], "expected_version": 2})
    s, r, _ = api.call("GET", "/api/radars/rivals", role="viewer", query={"tag": "q3.2026"})
    assert s == 200 and r["version"] == 2 and r["entries"][0]["ring"] == "Leader"
    s, out, _ = api.call("GET", "/api/radars/rivals/export", role="viewer", query={"tag": "Q3 2026", "format": "json"})
    assert s == 200 and "Leader" in str(out) and "Niche" not in str(out)  # the tagged version, not the latest
    s, tags, _ = api.call("GET", "/api/radars/rivals/tags", role="viewer")
    assert [t["tag"] for t in tags["items"]] == ["Q3 2026"]
    assert api.call("GET", "/api/radars/rivals", query={"tag": "nope"})[0] == 404
    assert api.call("DELETE", "/api/radars/rivals/tags/q3-2026", role="viewer")[0] == 403
    assert api.call("DELETE", "/api/radars/rivals/tags/q3-2026", role="editor")[0] == 200
    assert api.call("DELETE", "/api/radars/rivals/tags/q3-2026", role="editor")[0] == 404
    assert api.call("GET", "/api/radars/none/tags")[0] == 404


def test_a_deployment_can_turn_the_sample_data_off(api: Client, ctx: TestContext) -> None:
    """A deployment holding real data: a mistaken click on Load sample data mixed fictional companies into its graph."""
    ctx.profile.put({"sections": {"sample_data": False}}, "system:deploy", None)
    status, body, _ = api.call("POST", "/api/admin/sample-data", {})
    assert status == 404 and "not offered" in str(body)
    _, hood, _ = api.call("GET", "/api/graph/explore", role="editor")
    assert not hood["nodes"]


def test_the_sample_data_gate_refuses_when_the_profile_cannot_be_read(api: Client, ctx: TestContext) -> None:
    """Code review r18: a throttled or unreadable profile once read as 'sample data on' (r17 fail-open)."""
    from unittest.mock import patch

    from botocore.exceptions import ClientError

    throttled = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "GetItem")
    with patch.object(ctx.profile._t, "get_item", side_effect=throttled):
        status, _, _ = api.call("POST", "/api/admin/sample-data", {})
    assert status == 503
    with patch.object(ctx.profile._t, "get_item", return_value={"Item": {"doc": {"app_name": 7}, "version": 2}}):
        status, body, _ = api.call("POST", "/api/admin/sample-data", {})
    assert status == 409 and "cannot be read" in str(body)
    _, hood, _ = api.call("GET", "/api/graph/explore", role="editor")
    assert not hood["nodes"]  # nothing was loaded


def test_the_admin_profile_round_trip_survives_fields_a_newer_release_stored(api: Client, ctx: TestContext) -> None:
    """Code review r19: GET echoed kept unknown fields inside profile, and PUT refused them when sent back."""
    newer = {"app_name": "Example", "footer_md": "x", "sections": {"later": False}}
    ctx.profile._t.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": newer, "version": 3})
    s, got, _ = api.call("GET", "/api/admin/profile")
    assert s == 200 and got["unknown"] == ["footer_md", "sections.later"] and "footer_md" not in got["profile"]
    s, saved, _ = api.call("PUT", "/api/admin/profile", {"profile": got["profile"], "expected_version": 3})
    assert s == 200 and saved["unknown"] == ["footer_md", "sections.later"]
    body = {"profile": got["profile"], "expected_version": 4, "drop_unknown": ["footer_md"]}
    s, saved, _ = api.call("PUT", "/api/admin/profile", body)
    assert s == 200 and saved["unknown"] == ["sections.later"]
    body = {"profile": got["profile"], "expected_version": 5, "drop_unknown": ["nope"]}
    assert api.call("PUT", "/api/admin/profile", body)[0] == 400
    _, audit, _ = api.call("GET", "/api/audit")
    assert any(row.get("details", {}).get("dropped") == ["footer_md"] for row in audit.get("items", []))


def test_long_dropped_names_stay_whole_in_the_audit(api: Client, ctx: TestContext) -> None:
    """Code review r23: several 1000-character names pushed the audit row into its truncated marker."""
    names = [f"f{i}" + "x" * 900 for i in range(6)]
    ctx.profile._t.put_item(
        Item={"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": "Ex", **dict.fromkeys(names, "v")}, "version": 1}
    )
    body = {"profile": {"app_name": "Example"}, "expected_version": 1, "drop_unknown": names}
    assert api.call("PUT", "/api/admin/profile", body)[0] == 200
    _, audit, _ = api.call("GET", "/api/audit")
    row = next(r for r in audit["items"] if r.get("action") == "profile.set")
    dropped = row["details"]["dropped"]
    assert row["details"]["version"] == 2 and [n[:2] for n in dropped] == [f"f{i}" for i in range(6)]
    assert len(set(dropped)) == 6 and all(len(n) < 80 and "...#" in n for n in dropped)


def test_a_non_ascii_dropped_name_stays_short_in_the_audit(api: Client, ctx: TestContext) -> None:
    name = "\u0e20" * 80  # Thai letters: six characters each once JSON-escaped
    ctx.profile._t.put_item(
        Item={"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": "Example", name: "v"}, "version": 1}
    )
    body = {"profile": {"app_name": "Example"}, "expected_version": 1, "drop_unknown": [name]}
    assert api.call("PUT", "/api/admin/profile", body)[0] == 200
    _, audit, _ = api.call("GET", "/api/audit")
    shown = next(r for r in audit["items"] if r.get("action") == "profile.set")["details"]["dropped"][0]
    # Cut on whole characters, as written (JSON-escaped): the original letters, not escape text (code review r26).
    assert shown.startswith("\u0e20" * 10) and len(json.dumps(shown)) - 2 <= 76 and "...#" in shown
