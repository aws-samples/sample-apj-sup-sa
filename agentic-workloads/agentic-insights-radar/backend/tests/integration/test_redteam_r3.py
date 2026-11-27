# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Regression tests for the red-team review r3 (backend findings B3-xxx).

Each test names the finding it closes; the scenario is the one the review reproduced.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from insights_radar.core.clock import FixedClock
from tests.integration.harness import CALLER_ARN, Client, TestContext, ensure_running


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> TestContext:
    return TestContext(graph_table, ops_table, clock)


@pytest.fixture
def api(ctx: TestContext) -> Client:
    from insights_radar.api.app import Application

    return Client(Application(ctx))


def _tok(ctx: TestContext, slug: str, run_id: str = "run-1", product_id: str | None = None) -> str:
    ensure_running(ctx, slug, run_id)
    return ctx.tokens.issue(slug, run_id, ctx.modules.get(slug).tools, 7200, product_id)


def _end_run(ctx: TestContext, slug: str, run_id: str = "run-1") -> None:
    from dataclasses import replace

    from insights_radar.modules.repository import RunStatus

    ctx.modules.put_run(replace(ctx.modules.get_run(slug, run_id), status=RunStatus.SUCCEEDED))


def _custom(api: Client, slug: str, grants: list[str], **extra: Any) -> dict[str, Any]:
    body = {"slug": slug, "name": slug, "insight_kind": slug.replace("-", "_") + "_k", "grants": grants, **extra}
    status, created, _ = api.call("POST", "/api/modules", body)
    assert status == 201, created
    return dict(created)


def _rows(ctx: TestContext, action: str) -> list[dict[str, Any]]:
    return [r for r in ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 1000)[0] if r["action"] == action]


PK = ["kg_read", "kg_write_output", "product_knowledge"]


# ---- B3-001 / B3-003: an update by a private writer stamps the node private, and private is sticky ----


def test_b3_001_an_update_from_a_private_run_stamps_an_old_public_output_private(api: Client, ctx: TestContext) -> None:
    m = _custom(api, "fit", ["kg_read", "kg_write_output"])
    _, out = api.tool("kg_save_output", _tok(ctx, "fit"), {"title": "Fit", "summary": "public v1"})
    nid = out["result"]["id"]
    assert ctx.graph.get(nid).private is False
    status, body, _ = api.call("PATCH", "/api/modules/fit", {"version": m["version"], "grants": PK})
    assert status == 200, body
    status, _ = api.tool(
        "kg_update_output",
        _tok(ctx, "fit", "run-2"),
        {"id": nid, "summary": "PRIVATE: ships 3 Nov", "expected_version": 1},
    )
    assert status == 200
    node = ctx.graph.get(nid)
    assert node.private is True and node.stamped is True
    assert api.tool("kg_get", _tok(ctx, "scout", "run-s"), {"id": nid})[0] == 404


def test_b3_001_private_is_never_lowered_by_a_later_public_update(api: Client, ctx: TestContext) -> None:
    m = _custom(api, "fit", PK)
    _, out = api.tool("kg_save_output", _tok(ctx, "fit"), {"title": "Fit", "summary": "from the notes"})
    nid = out["result"]["id"]
    status, body, _ = api.call(
        "PATCH", "/api/modules/fit", {"version": m["version"], "grants": ["kg_read", "kg_write_output"]}
    )
    assert status == 200, body
    status, _ = api.tool(
        "kg_update_output", _tok(ctx, "fit", "run-2"), {"id": nid, "title": "Fit 2", "expected_version": 1}
    )
    assert status == 200
    assert ctx.graph.get(nid).private is True


def test_b3_001_the_repository_keeps_private_sticky_and_stamps_unstamped_nodes(ctx: TestContext) -> None:
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    node = ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="S", module="signal"), "module:signal")
    ctx.unstamp(node.id)
    # An update that does not raise the stamp keeps an unstamped node unstamped (never declared public).
    kept = ctx.graph.update(node.id, {"summary": "x", "private": False}, 1, "module:signal")
    assert (
        kept.stamped is False
        and "private" not in ctx.graph._table.get_item(Key={"pk": f"N#{node.id}", "sk": "META"})["Item"]
    )
    raised = ctx.graph.update(node.id, {"summary": "y", "private": True}, 2, "module:signal")
    assert raised.private is True and raised.stamped is True
    lowered = ctx.graph.update(node.id, {"summary": "z", "private": False}, 3, "module:signal")
    assert ctx.graph.get(node.id).private is True and lowered.private is True


def test_b3_003_a_private_writer_filling_its_own_public_entity_makes_it_private(api: Client, ctx: TestContext) -> None:
    grants = ["kg_read", "kg_write_output", "kg_write_evidence"]
    m = _custom(api, "ent", grants)
    _, e = api.tool("kg_upsert_entity", _tok(ctx, "ent"), {"type": "topic", "name": "Project Falcon"})
    eid = e["result"]["id"]
    assert ctx.graph.get(eid).private is False
    status, body, _ = api.call(
        "PATCH", "/api/modules/ent", {"version": m["version"], "grants": [*grants, "product_knowledge"]}
    )
    assert status == 200, body
    api.tool(
        "kg_upsert_entity",
        _tok(ctx, "ent", "run-2"),
        {"type": "topic", "name": "Project Falcon", "summary": "UNRELEASED: launches 3 Nov at 41k list"},
    )
    assert ctx.graph.get(eid).private is True
    assert api.tool("kg_get", _tok(ctx, "scout", "run-s"), {"id": eid})[0] == 404


# ---- B3-002: the stamp follows what the run was given, not the module's grants now ----------------


def test_b3_002_a_run_started_with_product_knowledge_stamps_private_after_the_grant_is_removed(
    api: Client, ctx: TestContext
) -> None:
    _, p, _ = api.call("POST", "/api/products", {"name": "P", "notes": "UNRELEASED"})
    m = _custom(api, "pk", PK, product_id=p["id"])
    tok = _tok(ctx, "pk", product_id=p["id"])
    _, rc = api.tool("run_context", tok, {})
    assert "UNRELEASED" in rc["result"]["product"]
    status, body, _ = api.call(
        "PATCH", "/api/modules/pk", {"version": m["version"], "grants": ["kg_read", "kg_write_output"]}
    )
    assert status == 200, body
    status, out = api.tool("kg_save_output", tok, {"title": "From the notes", "summary": "UNRELEASED v3 drops on-prem"})
    assert status == 200
    node = ctx.graph.get(out["result"]["id"])
    assert node.private is True
    assert api.tool("kg_get", _tok(ctx, "scout", "run-s"), {"id": node.id})[0] == 404
    # A run started after the grant was removed was given nothing private and writes public nodes.
    status, out2 = api.tool("kg_save_output", _tok(ctx, "pk", "run-2"), {"title": "Later", "summary": "public"})
    assert status == 200 and ctx.graph.get(out2["result"]["id"]).private is False


# ---- B3-004 / B3-005 / B3-006 / B3-012: what autopilot counts as corroboration ----------------------


def _autopilot_radar(api: Client, slug: str = "ap", **extra: Any) -> dict[str, Any]:
    status, radar, _ = api.call("POST", "/api/radars", {"slug": slug, "name": slug, "template": "blank"})
    assert status == 201, radar
    body = {"version_lock": radar["version_lock"], "mode": "autopilot", "auto_apply_agent": True, "auto_apply_days": 1}
    status, radar, _ = api.call("PATCH", f"/api/radars/{slug}", {**body, **extra})
    assert status == 200, radar
    return dict(radar)


def _fetched(api: Client, ctx: TestContext, url: str, title: str, run: str = "run-f") -> str:
    status, body = api.tool(
        "kg_add_evidence",
        _tok(ctx, "scout", run),
        {"type": "fact", "title": title, "evidence_url": url, "fetched": True},
    )
    assert status == 200, body
    return str(body["result"]["id"])


def _suggest(api: Client, ctx: TestContext, slug: str, evidence: list[str], **proposal: Any) -> dict[str, Any]:
    body = {
        "slug": slug,
        "proposal": {"title": "Octank (exiting)", "sector": "General", "ring": "Now", **proposal},
        "reason": "corroborated by the cited evidence",
        "evidence": evidence,
    }
    status, out = api.tool("radar_suggest", _tok(ctx, "curator", "run-c"), body)
    assert status == 200, out
    return dict(out["result"])


def test_b3_004_two_unsupported_conclusions_are_not_two_sites(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    _autopilot_radar(api)
    _, sig = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "Octank is shutting down", "summary": "x"})
    _, tr = api.tool("kg_save_output", _tok(ctx, "trend"), {"title": "Octank exit", "summary": "x"})
    ids = [sig["result"]["id"], tr["result"]["id"]]
    assert workers._usable_evidence(ctx)(ids) == {}
    _suggest(api, ctx, "ap", ids)
    ctx.clock.advance(timedelta(days=2))
    out = workers.sweep({}, None, ctx)
    assert out["autopilot"]["applied"] == 0 and out["autopilot"]["held"] == 1 and ctx.radars.entries("ap") == []


def test_b3_004_a_conclusion_counts_through_the_fetched_evidence_it_supports(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    _autopilot_radar(api)
    a = _fetched(api, ctx, "https://www.reuters.com/a", "A")
    b = _fetched(api, ctx, "https://techcrunch.com/b", "B")
    unfetched = api.tool(
        "kg_add_evidence",
        _tok(ctx, "scout", "run-u"),
        {"type": "fact", "title": "U", "evidence_url": "https://x.example.net/u"},
    )[1]["result"]["id"]
    _, sig = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "S", "summary": "x", "supports": [a, unfetched]})
    _, tr = api.tool(
        "kg_save_output", _tok(ctx, "trend"), {"title": "T", "summary": "x", "supports": [sig["result"]["id"]]}
    )
    check = workers._usable_evidence(ctx)
    assert check([sig["result"]["id"]]) == {sig["result"]["id"]: frozenset({"reuters.com"})}
    # A conclusion supported by a conclusion reaches its evidence too.
    assert check([tr["result"]["id"]]) == {tr["result"]["id"]: frozenset({"reuters.com"})}
    _suggest(api, ctx, "ap", [tr["result"]["id"], b])
    ctx.clock.advance(timedelta(days=2))
    out = workers.sweep({}, None, ctx)
    assert out["autopilot"]["applied"] == 1


def test_b3_012_sample_evidence_is_not_corroboration(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    assert api.call("POST", "/api/admin/sample-data", {})[0] == 200
    real = _fetched(api, ctx, "https://www.reuters.com/a", "A")
    assert workers._usable_evidence(ctx)(["fact/sample-01", real]) == {real: frozenset({"reuters.com"})}


def test_b3_005_an_unattended_apply_needs_the_link_to_come_from_the_evidence(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    _autopilot_radar(api)
    ev = [_fetched(api, ctx, "https://www.reuters.com/a", "A"), _fetched(api, ctx, "https://techcrunch.com/b", "B")]
    _suggest(api, ctx, "ap", ev, title="Bad link", link="https://attacker.example/landing")
    _suggest(api, ctx, "ap", ev, title="Good link", link="https://techcrunch.com/octank")
    ctx.clock.advance(timedelta(days=2))
    out = workers.sweep({}, None, ctx)
    entries = {e.title: e for e in ctx.radars.entries("ap")}
    assert out["autopilot"]["applied"] == 1 and out["autopilot"]["held"] == 1
    assert list(entries) == ["Good link"] and entries["Good link"].link == "https://techcrunch.com/octank"


def test_b3_006_the_kill_switch_holds_autopilot_applies(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    _autopilot_radar(api)
    ev = [_fetched(api, ctx, "https://www.reuters.com/a", "A"), _fetched(api, ctx, "https://techcrunch.com/b", "B")]
    _suggest(api, ctx, "ap", ev)
    api.call("PUT", "/api/andon", {"genai_enabled": False, "reason": "curator looks injected"})
    ctx.clock.advance(timedelta(days=2))
    out = workers.sweep({}, None, ctx)
    assert out["autopilot"]["applied"] == 0 and out["autopilot"]["held"] == 1 and ctx.radars.entries("ap") == []
    api.call("PUT", "/api/andon", {"genai_enabled": True})
    assert workers.sweep({}, None, ctx)["autopilot"]["applied"] == 1


# ---- B3-007 / B3-008: registrable domains --------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://www.thestar.com.my/a", "https://www.malaymail.com.my/b"),
        ("https://www.kompas.co.id/a", "https://www.detik.co.id/b"),
        ("https://vnexpress.com.vn/a", "https://tuoitre.com.vn/b"),
        ("https://www.bangkokpost.co.th/a", "https://www.nation.co.th/b"),
        ("https://news.abs-cbn.com.ph/a", "https://www.inquirer.com.ph/b"),
    ],
)
def test_b3_007_distinct_registrants_under_second_level_suffixes_are_distinct_sites(a: str, b: str) -> None:
    from insights_radar.core.urls import UrlGuard

    assert UrlGuard.site(a) != UrlGuard.site(b)
    assert UrlGuard.site(a).count(".") == 2  # the registrant plus the two-label suffix


def test_b3_007_one_registrant_under_a_second_level_suffix_is_one_site() -> None:
    from insights_radar.core.urls import UrlGuard

    assert UrlGuard.site("https://a.attacker.com.my/x") == UrlGuard.site("https://b.attacker.com.my/y")
    assert UrlGuard.site("https://news.example.de/x") == "example.de"  # a ccTLD without a generic second level


def test_b3_008_ip_literals_in_one_network_are_one_site() -> None:
    from insights_radar.core.urls import UrlGuard

    assert UrlGuard.site("https://203.0.113.7/a") == UrlGuard.site("https://203.0.113.8/b") == "ip:203.0.113.0/24"
    assert UrlGuard.site("https://198.51.100.1/a") != UrlGuard.site("https://203.0.113.8/b")
    assert UrlGuard.site("https://[2001:db8:1:2::1]/a") == UrlGuard.site("https://[2001:db8:1:3::9]/b")


# ---- B3-009 / B3-010 / B3-011: the autopilot opt-in -----------------------------------------------


def test_b3_009_an_owner_cannot_arm_unattended_applies_by_switching_mode(api: Client, ctx: TestContext) -> None:
    status, radar, _ = api.call(
        "POST", "/api/radars", {"slug": "own", "name": "Own", "template": "blank"}, role="editor"
    )
    assert status == 201, radar
    status, radar, _ = api.call(
        "PATCH", "/api/radars/own", {"version_lock": radar["version_lock"], "mode": "copilot", "auto_apply_agent": True}
    )
    assert status == 200, radar
    status, body, _ = api.call(
        "PATCH", "/api/radars/own", {"version_lock": radar["version_lock"], "mode": "autopilot"}, role="editor"
    )
    assert status == 403, body
    status, body, _ = api.call("PATCH", "/api/radars/own", {"version_lock": radar["version_lock"], "mode": "autopilot"})
    assert status == 200 and body["mode"] == "autopilot"
    # Leaving autopilot and other settings stay the owner's.
    status, body, _ = api.call(
        "PATCH", "/api/radars/own", {"version_lock": body["version_lock"], "mode": "assist"}, role="editor"
    )
    assert status == 200, body


def test_b3_010_autopilot_gates_come_from_a_consistent_read(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from dataclasses import replace as dc_replace

    from insights_radar.api import workers

    _autopilot_radar(api)
    ev = [_fetched(api, ctx, "https://www.reuters.com/a", "A"), _fetched(api, ctx, "https://techcrunch.com/b", "B")]
    _suggest(api, ctx, "ap", ev)
    radar = ctx.radars.get("ap")
    assert (
        api.call("PATCH", "/api/radars/ap", {"version_lock": radar.version_lock, "auto_apply_agent": False})[0] == 200
    )
    stale = [dc_replace(r, auto_apply_agent=True) for r in ctx.radars.list_all()]
    monkeypatch.setattr(ctx.radars, "list_all", lambda: stale)  # the GSI has not caught up yet
    ctx.clock.advance(timedelta(days=2))
    assert workers.sweep({}, None, ctx)["autopilot"]["applied"] == 0


def test_b3_011_held_suggestions_cost_one_entry_read_per_radar(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    _, radar, _ = api.call("POST", "/api/radars", {"slug": "ap", "name": "AP", "template": "blank"})
    api.call("PATCH", "/api/radars/ap", {"version_lock": radar["version_lock"], "mode": "autopilot"})
    cur = _tok(ctx, "curator")
    for i in range(20):
        api.tool(
            "radar_suggest",
            cur,
            {
                "slug": "ap",
                "proposal": {"title": f"T{i}", "sector": "General", "ring": "Now"},
                "reason": "ten chars or more",
            },
        )
    ctx.clock.advance(timedelta(days=8))
    gets = {"n": 0}
    real = ctx.radars._blobs.get

    def counting(key: str) -> bytes:
        gets["n"] += 1
        return real(key)

    monkeypatch.setattr(ctx.radars._blobs, "get", counting)
    assert ctx.radars.autopilot_tick()["held"] == 20 and gets["n"] == 1


# ---- B3-013: the curator cannot cite what it cannot read ----------------------------------------


def test_b3_013_the_curator_cannot_cite_a_private_node(api: Client, ctx: TestContext) -> None:
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "pc", "name": "P", "template": "blank"})
    api.call("PATCH", "/api/radars/pc", {"version_lock": radar["version_lock"], "mode": "assist"})
    attrs = {"request_title": "x", "problem": "y", "impact": "z"}
    _, gap = api.tool(
        "kg_save_output", _tok(ctx, "gap"), {"title": "SSO slips", "summary": "PRIVATE", "attributes": attrs}
    )
    cur = _tok(ctx, "curator", "run-c")
    body = {
        "slug": "pc",
        "proposal": {"title": "SSO", "sector": "General", "ring": "Now"},
        "reason": "cites a gap",
        "evidence": [gap["result"]["id"]],
    }
    status, out = api.tool("radar_suggest", cur, body)
    assert status == 400 and out["details"]["missing"] == [gap["result"]["id"]]


# ---- B3-024: module dispatch is an isolated sweep step ------------------------------------------


def test_b3_024_a_failing_module_dispatch_does_not_stop_the_sweep(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.api import workers

    def throttled(*args: Any, **kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "Query")

    monkeypatch.setattr(ctx.modules, "list_all", throttled)
    ran: list[str] = []
    monkeypatch.setattr(workers, "_autopilot", lambda c: ran.append("autopilot") or {})
    out = workers.sweep({}, None, ctx)
    assert out["modules"] == [] and ran == ["autopilot"]


# ---- B3-073 / B3-074: accept and discard race each other -------------------------------------------


def _assist(api: Client, slug: str) -> None:
    status, radar, _ = api.call("POST", "/api/radars", {"slug": slug, "name": slug, "template": "blank"})
    assert status == 201, radar
    api.call("PATCH", f"/api/radars/{slug}", {"version_lock": radar["version_lock"], "mode": "assist"})


def _kafka(api: Client, ctx: TestContext, slug: str) -> str:
    body = {
        "slug": slug,
        "proposal": {"title": "Kafka", "sector": "General", "ring": "Now"},
        "reason": "ten chars plus",
    }
    status, sug = api.tool("radar_suggest", _tok(ctx, "curator"), body)
    assert status == 200, sug
    return str(sug["result"]["entry_key"])


def test_b3_073_accept_does_not_apply_a_suggestion_discarded_meanwhile(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    _assist(api, "acc")
    key = _kafka(api, ctx, "acc")
    real = ctx.radars._still_applies

    def racing(radar: Any, sug: Any, current: Any = None) -> bool:
        ok = real(radar, sug, current)
        ctx.radars._t.delete_item(Key={"pk": "RADAR#acc", "sk": f"SUG#{key}"})  # a colleague discards it
        return ok

    monkeypatch.setattr(ctx.radars, "_still_applies", racing)
    status, body, _ = api.call("POST", f"/api/radars/acc/suggestions/{key}/accept", {})
    assert status == 404, body
    assert ctx.radars.entries("acc") == []


def test_b3_073_a_failed_apply_puts_the_suggestion_back(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from insights_radar.core.errors import ConflictError

    _assist(api, "acc")
    key = _kafka(api, ctx, "acc")

    def busy(*args: Any, **kwargs: Any) -> Any:
        raise ConflictError("someone else saved this radar; reload and retry")

    monkeypatch.setattr(ctx.radars, "_save", busy)
    status, _, _ = api.call("POST", f"/api/radars/acc/suggestions/{key}/accept", {})
    assert status == 409 and [s["entry_key"] for s in ctx.radars.suggestions("acc")] == [key]


def test_b3_074_a_discard_of_a_suggestion_applied_meanwhile_is_a_404(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    _assist(api, "dis")
    key = _kafka(api, ctx, "dis")
    real = ctx.radars._require_edit

    def racing(principal: Any, radar: Any) -> None:
        real(principal, radar)
        ctx.radars._apply(ctx.radars.get("dis"), ctx.radars._get_suggestion("dis", key), "system:autopilot")

    monkeypatch.setattr(ctx.radars, "_require_edit", racing)
    status, body, _ = api.call("DELETE", f"/api/radars/dis/suggestions/{key}")
    assert status == 404, body
    assert [e.title for e in ctx.radars.entries("dis")] == ["Kafka"] and _rows(ctx, "suggestion.discard") == []


def test_b3_074_a_discard_audits_what_it_removed(api: Client, ctx: TestContext) -> None:
    _assist(api, "dis")
    key = _kafka(api, ctx, "dis")
    assert api.call("DELETE", f"/api/radars/dis/suggestions/{key}")[0] == 200
    row = _rows(ctx, "suggestion.discard")[0]
    assert row["details"]["title"] == "Kafka" and row["details"]["proposed_by"] == "module:curator"


def test_b3_004_support_chains_are_followed_two_hops_only(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    ev = _fetched(api, ctx, "https://www.reuters.com/a", "A")
    _, sig = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "S", "summary": "x", "supports": [ev]})
    _, tr = api.tool(
        "kg_save_output", _tok(ctx, "trend"), {"title": "T", "summary": "x", "supports": [sig["result"]["id"]]}
    )
    _custom(api, "deep", ["kg_read", "kg_write_output"])
    _, deep = api.tool(
        "kg_save_output", _tok(ctx, "deep"), {"title": "D", "summary": "x", "supports": [tr["result"]["id"]]}
    )
    # insight -> trend -> signal -> evidence is three hops: too far to count.
    assert workers._usable_evidence(ctx)([deep["result"]["id"]]) == {}


def test_b3_073_accepting_an_unknown_suggestion_is_a_404(api: Client, ctx: TestContext) -> None:
    _assist(api, "acc")
    assert api.call("POST", "/api/radars/acc/suggestions/nope/accept", {})[0] == 404


def test_b3_073_claim_errors_other_than_a_lost_race_propagate(ctx: TestContext, monkeypatch: Any) -> None:
    from botocore.exceptions import ClientError

    def throttled(**kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "DeleteItem")

    monkeypatch.setattr(ctx.radars._t, "delete_item", throttled)
    with pytest.raises(ClientError):
        ctx.radars._claim_suggestion("acc", "k")


def test_b3_073_a_suggestion_that_cannot_be_restored_is_logged(ctx: TestContext, monkeypatch: Any) -> None:
    def failing(**kwargs: Any) -> Any:
        raise RuntimeError("table unavailable")

    monkeypatch.setattr(ctx.radars._t, "put_item", failing)
    ctx.radars._restore_suggestion({"pk": "RADAR#acc", "sk": "SUG#k"})  # does not raise


def test_b3_010_a_radar_that_left_autopilot_is_skipped(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from dataclasses import replace as dc_replace

    from insights_radar.radars.model import RadarMode

    _assist(api, "acc")
    _kafka(api, ctx, "acc")
    stale = [dc_replace(r, mode=RadarMode.AUTOPILOT) for r in ctx.radars.list_all()]
    monkeypatch.setattr(ctx.radars, "list_all", lambda: stale)
    ctx.clock.advance(timedelta(days=30))
    assert ctx.radars.autopilot_tick() == {"applied": 0, "superseded": 0, "held": 0, "errors": 0}


def test_b3_073_autopilot_skips_a_suggestion_reviewed_while_it_applied(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    _autopilot_radar(api)
    _kafka(api, ctx, "ap")
    real = ctx.radars._claim_suggestion

    def reviewed(slug: str, key: str) -> Any:
        ctx.radars._t.delete_item(Key={"pk": f"RADAR#{slug}", "sk": f"SUG#{key}"})
        return real(slug, key)

    monkeypatch.setattr(ctx.radars, "_claim_suggestion", reviewed)
    sugs = ctx.radars.suggestions("ap")
    ctx.radars._t.update_item(
        Key={"pk": "RADAR#ap", "sk": f"SUG#{sugs[0]['entry_key']}"},
        UpdateExpression="SET evidence = :e",
        ExpressionAttributeValues={":e": ["fact/1"]},
    )
    ctx.clock.advance(timedelta(days=2))
    assert ctx.radars.autopilot_tick() == {"applied": 0, "superseded": 0, "held": 0, "errors": 0}


# ---- B3-015: the token's product stops being readable once the module is re-pointed ---------------


def test_b3_015_run_context_and_product_profile_follow_the_modules_product(api: Client, ctx: TestContext) -> None:
    _, p1, _ = api.call("POST", "/api/products", {"name": "Alpha", "notes": "ALPHA SECRET"})
    _, p2, _ = api.call("POST", "/api/products", {"name": "Beta", "notes": "beta notes"})
    m = _custom(api, "pk", PK, product_id=p1["id"])
    tok = _tok(ctx, "pk", product_id=p1["id"])
    assert "ALPHA SECRET" in api.tool("run_context", tok, {})[1]["result"]["product"]
    assert api.tool("product_profile", tok, {})[1]["result"]["notes"] == "ALPHA SECRET"
    status, body, _ = api.call("PATCH", "/api/modules/pk", {"version": m["version"], "product_id": p2["id"]})
    assert status == 200, body
    status, rc = api.tool("run_context", tok, {})
    assert status == 200 and rc["result"]["product"] is None and "changed" in rc["result"]["note"]
    assert api.tool("product_profile", tok, {})[0] == 403


# ---- B3-016: a credential in a source path or a short key is scrubbed from what a run reads -------


@pytest.mark.parametrize(
    ("address", "secret"),
    [
        ("https://hooks.example.com/services/T000/B000/XSECRETWEBHOOK", "/services/T000/B000/XSECRETWEBHOOK"),
        ("https://example.com/feeds/9f3cPRIVATEFEEDTOKEN/rss", "9f3cPRIVATEFEEDTOKEN"),
        ("https://api.telegram.org/botSECRET8/getUpdates", "botSECRET8"),
        ("https://api.example.com/v1?appid=K7x9Q2a", "appid=K7x9Q2a"),
    ],
)
def test_b3_016_secret_values_cover_path_credentials_and_short_keys(address: str, secret: str) -> None:
    from insights_radar.core.urls import UrlGuard

    forms = UrlGuard.secret_values(address)
    assert secret in forms and forms == sorted(forms, key=len, reverse=True)
    echoed = f"see {address}&page=2 and {secret}"
    for value in forms:
        echoed = echoed.replace(value, "REDACTED")
    assert secret not in echoed


def test_b3_016_ordinary_words_in_a_path_are_not_scrubbed_alone() -> None:
    from insights_radar.core.urls import UrlGuard

    forms = UrlGuard.secret_values("https://news.example.com/category/technology/rss")
    assert "technology" not in forms and "/category/technology/rss" in forms
    assert UrlGuard.secret_values("https://news.example.com/rss") == []  # too short to scrub at all
    assert UrlGuard.secret_values("https://[::1/a?key=1") == []


# ---- B3-017: viewers do not get documentation tokens, notes or crawl errors -----------------------


def test_b3_017_viewers_get_names_summaries_and_documentation_hosts_only(api: Client, ctx: TestContext) -> None:
    shared_url = "https://docs.example.com/share/9f3cSECRETe1/guide?token=DOCSECRET"
    body = {"name": "P", "summary": "A CRM", "doc_urls": [shared_url], "notes": "UNRELEASED v3", "capabilities": ["x"]}
    _, p, _ = api.call("POST", "/api/products", body)
    ctx.products._set_system_fields(
        p["id"], stats={"crawl": {"errors": ["https://docs.example.com/share/9f3cSECRETe1 401"]}}
    )
    for role in ("viewer", "editor"):
        _, listed, _ = api.call("GET", "/api/products", role=role)
        _, one, _ = api.call("GET", f"/api/products/{p['id']}", role=role)
        for view in (listed["items"][0], one):
            assert "SECRET" not in str(view) and "UNRELEASED" not in str(view)
            assert view["doc_urls"] == ["https://docs.example.com"] and view["summary"] == "A CRM"
            assert view["restricted"] is True and view["notes"] == "" and view["created_by"] == ""
    _, full, _ = api.call("GET", f"/api/products/{p['id']}")
    assert full["doc_urls"] == [shared_url] and full["notes"] == "UNRELEASED v3" and "restricted" not in full


# ---- B3-019 / CR3-01: migrations resume, and re-check every deploy -------------------------------


def test_cr3_01_the_seed_repairs_what_the_previous_release_wrote_after_a_rollback(
    api: Client, ctx: TestContext
) -> None:
    """Through the ledger, as a deploy runs it: upgrade, rollback writes, upgrade. The re-keys keep no
    done mark, so the second seed checks the stored keys again (CR3-01, P3-003), and a deleted private
    writer the rolled-back release recorded only in the legacy tombstone item is split (SR3-01)."""
    from insights_radar.api import workers
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    node = ctx.graph.create(Node(id="", type=NodeType.TREND, title="t"), "user:u-admin")
    first = workers.seed({}, None, ctx)
    assert not first["failed"]
    assert ctx.ops_table.get_item(Key={"pk": "MIGRATION#rekey_list_index:v1", "sk": "STATE"}).get("Item") is None
    # An older build's permanent done mark is ignored, not trusted.
    ctx.ops_table.put_item(Item={"pk": "MIGRATION#rekey_list_index:v1", "sk": "STATE", "done": True})
    # The rolled-back release writes the old list key and records a deleted private writer its own way.
    ctx.graph._table.update_item(
        Key={"pk": f"N#{node.id}", "sk": "META"},
        UpdateExpression="SET updated_at = :u, gsi1sk = :k",
        ExpressionAttributeValues={":u": "2099-01-01T00:00:00Z", ":k": f"{node.created_at}#{node.id}"},
    )
    ctx.ops_table.put_item(Item={"pk": "MODULES", "sk": "TOMBSTONE", "private_writers": {"old-pw"}})
    assert not workers.seed({}, None, ctx)["failed"]
    assert [n.id for n in ctx.graph.recent(NodeType.TREND, after="2098-01-01T00:00:00Z").items] == [node.id]
    assert ctx.ops_table.get_item(Key={"pk": "MODULES", "sk": "TOMBSTONE#PRIVATE#old-pw"}).get("Item")
    assert "old-pw" in ctx.modules.private_writer_slugs()


def test_b3_019_a_migration_short_of_time_resumes_where_it_stopped(ctx: TestContext) -> None:
    from insights_radar.core.dynamo import MigrationLedger
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    for i in range(5):
        node = ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title=f"S{i}"), "user:u-admin")
        ctx.graph._table.update_item(
            Key={"pk": f"N#{node.id}", "sk": "META"},
            UpdateExpression="SET gsi1sk = :k",
            ExpressionAttributeValues={":k": f"old#{node.id}"},
        )
    ledger = MigrationLedger(ctx.ops_table)

    def migrate(start: Any, hook: Any) -> int:
        return ctx.graph.rekey_list_index(page_size=2, start=start, on_page=hook)

    first = ledger.run("m:v1", migrate, lambda: False)  # one page, then out of time
    assert first is not None and first <= 2
    assert ledger.cursor("m:v1")
    assert ledger.run("m:v1", migrate, lambda: True) == 5 - first  # the rest, from where it stopped
    assert ledger.cursor("m:v1") is None  # finished: no state left, and no done mark (CR3-01)
    assert ledger.run("m:v1", migrate, lambda: True) == 0  # the next deploy checks again from the start


def test_b3_019_the_seed_stops_a_migration_when_the_lambda_runs_short() -> None:
    from insights_radar.api import workers

    class Lambda:
        def __init__(self, ms: int) -> None:
            self.ms = ms

        def get_remaining_time_in_millis(self) -> int:
            return self.ms

    assert workers._time_left(None) is True
    assert workers._time_left(Lambda(120_000)) is True
    assert workers._time_left(Lambda(30_000)) is False


def test_b3_015_run_context_of_a_deleted_product_is_empty(api: Client, ctx: TestContext) -> None:
    _, p, _ = api.call("POST", "/api/products", {"name": "Gone"})
    _custom(api, "pk", PK, product_id=p["id"])
    tok = _tok(ctx, "pk", product_id=p["id"])
    ctx.products.delete(p["id"])
    status, rc = api.tool("run_context", tok, {})
    assert status == 200 and rc["result"]["product"] is None and "note" not in rc["result"]


# ---- B3-014 / B3-067: kg_link sources ----------------------------------------------------------


def test_b3_014_kg_link_from_a_hidden_entity_is_a_404(api: Client, ctx: TestContext) -> None:
    _custom(api, "pw", ["kg_read", "kg_write_output", "kg_write_evidence", "product_knowledge"])
    _, e = api.tool("kg_upsert_entity", _tok(ctx, "pw"), {"type": "topic", "name": "Project Falcon"})
    hidden = e["result"]["id"]
    scout = _tok(ctx, "scout", "run-s")
    _, pub = api.tool("kg_upsert_entity", scout, {"type": "topic", "name": "Public Topic"})
    status, _ = api.tool("kg_link", scout, {"type": "related_to", "from": hidden, "to": pub["result"]["id"]})
    assert status == 404 and ctx.graph.get(hidden).out_degree == 0


def test_b3_067_a_module_relates_only_entities_it_created(api: Client, ctx: TestContext) -> None:
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    ceo = ctx.graph.upsert_entity(Node(id="", type=NodeType.PERSON, title="Jane Doe"), "user:u-admin")[0]
    rival = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Rival Inc"), "user:u-admin")[0]
    scout = _tok(ctx, "scout")
    status, _ = api.tool("kg_link", scout, {"type": "affiliated_with", "from": ceo.id, "to": rival.id})
    assert status == 403 and ctx.graph.get(ceo.id).out_degree == 0
    _, own = api.tool("kg_upsert_entity", scout, {"type": "person", "name": "John Roe"})
    status, _ = api.tool("kg_link", scout, {"type": "affiliated_with", "from": own["result"]["id"], "to": rival.id})
    assert status == 200


# ---- B3-026 / B3-027 / B3-028: metered tools ------------------------------------------------------


def test_b3_026_a_rejected_metered_call_is_not_charged(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "signal")
    before = ctx.modules.budget("signal", 2_000_000).consumed
    assert api.tool("kg_search", tok, {"query": "x" * 501})[0] == 400
    assert ctx.modules.budget("signal", 2_000_000).consumed == before
    assert api.tool("kg_search", tok, {"query": "octank"})[0] == 200
    assert ctx.modules.budget("signal", 2_000_000).consumed == before + 2000


def test_b3_027_the_metered_cap_holds_across_containers(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from insights_radar.api import agent_tools
    from insights_radar.api.app import Application

    monkeypatch.setattr(agent_tools, "MAX_METERED_CALLS_PER_RUN", 3)
    tok = _tok(ctx, "signal")
    other = Client(Application(ctx))
    codes = [api.tool("kg_search", tok, {"query": "octank"})[0] for _ in range(2)]
    codes += [other.tool("kg_search", tok, {"query": "octank"})[0] for _ in range(2)]
    assert codes == [200, 200, 200, 403]
    # A rejected call gives its slot back.
    monkeypatch.setattr(agent_tools, "MAX_METERED_CALLS_PER_RUN", 5)
    assert api.tool("kg_search", tok, {"query": "x" * 501})[0] == 400
    assert [api.tool("kg_search", tok, {"query": "octank"})[0] for _ in range(3)] == [200, 200, 403]


def test_b3_027_a_failed_refund_is_logged(api: Client, ctx: TestContext, monkeypatch: Any, caplog: Any) -> None:
    tok = _tok(ctx, "signal")
    real = ctx.ops_table.update_item

    def failing(**kwargs: Any) -> Any:
        if kwargs.get("UpdateExpression") == "ADD n :minus":
            raise RuntimeError("throttled")
        return real(**kwargs)

    monkeypatch.setattr(ctx.ops_table, "update_item", failing)
    assert api.tool("kg_search", tok, {"query": "x" * 501})[0] == 400
    assert "meter.refund_failed" in caplog.text


def test_b3_027_meter_errors_other_than_the_cap_propagate(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from botocore.exceptions import ClientError

    tok = _tok(ctx, "signal")
    real = ctx.ops_table.update_item

    def throttled(**kwargs: Any) -> Any:
        if str(kwargs.get("Key", {}).get("pk", "")).startswith("METER#"):
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "UpdateItem")
        return real(**kwargs)

    monkeypatch.setattr(ctx.ops_table, "update_item", throttled)
    with pytest.raises(ClientError):
        api.app.tools.call("kg_search", tok, {"query": "octank"}, CALLER_ARN)


def test_b3_028_metered_tools_stop_when_the_budget_is_closed(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "signal")
    ctx.modules.charge_weighted("signal", 3_000_000)
    before = ctx.modules.budget("signal", 2_000_000).consumed
    status, body = api.tool("kg_search", tok, {"query": "octank"})
    assert status == 403 and "budget" in body["message"]
    assert ctx.modules.budget("signal", 2_000_000).consumed == before


# ---- B3-055 / B3-056 / B3-057: evidence dedupe --------------------------------------------------


def test_b3_055_a_dedupe_hit_still_makes_the_links_it_was_asked_for(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "scout")
    _, e1 = api.tool("kg_upsert_entity", tok, {"type": "organization", "name": "Octank"})
    _, e2 = api.tool("kg_upsert_entity", tok, {"type": "organization", "name": "Example Corp"})
    a = {
        "type": "fact",
        "title": "Price cut",
        "evidence_url": "https://news.example.com/x",
        "about": [e1["result"]["id"]],
    }
    _, r1 = api.tool("kg_add_evidence", tok, a)
    _, r2 = api.tool("kg_add_evidence", tok, {**a, "about": [e2["result"]["id"]]})
    assert r2["result"]["id"] == r1["result"]["id"] and r2["result"]["created"] is False
    assert [link["to"] for link in r2["result"]["links"]] == [e2["result"]["id"]]
    assert ctx.graph.get(r1["result"]["id"]).out_degree == 2
    _, r3 = api.tool("kg_add_evidence", tok, a)  # a plain retry: the link exists, reported, not doubled
    assert r3["result"]["links"][0]["created"] is False and ctx.graph.get(r1["result"]["id"]).out_degree == 2


def test_b3_056_evidence_deleted_by_an_admin_can_be_recorded_again(api: Client, ctx: TestContext) -> None:
    a = {"type": "fact", "title": "Price cut", "evidence_url": "https://news.example.com/x"}
    _, r1 = api.tool("kg_add_evidence", _tok(ctx, "scout"), a)
    t, u = r1["result"]["id"].split("/")
    assert api.call("DELETE", f"/api/graph/nodes/{t}/{u}")[0] == 200
    status, r2 = api.tool("kg_add_evidence", _tok(ctx, "scout", "run-2"), a)
    assert status == 200 and r2["result"]["created"] is True and r2["result"]["id"] != r1["result"]["id"]


def test_b3_057_the_retry_after_a_link_failure_records_the_evidence(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    from insights_radar.core.errors import ConflictError

    tok = _tok(ctx, "scout")
    _, e1 = api.tool("kg_upsert_entity", tok, {"type": "organization", "name": "Octank"})
    a = {
        "type": "fact",
        "title": "Price cut",
        "evidence_url": "https://news.example.com/x",
        "about": [e1["result"]["id"]],
    }
    real = ctx.graph.link
    state = {"n": 0}

    def busy(*args: Any, **kwargs: Any) -> Any:
        state["n"] += 1
        if state["n"] == 1:
            raise ConflictError("the graph is busy with concurrent changes; retry shortly")
        return real(*args, **kwargs)

    monkeypatch.setattr(ctx.graph, "link", busy)
    assert api.tool("kg_add_evidence", tok, a)[0] == 409
    status, r2 = api.tool("kg_add_evidence", tok, a)
    assert status == 200 and r2["result"]["created"] is True and len(r2["result"]["links"]) == 1


def test_b3_056_a_dangling_key_raced_by_another_writer_is_left_to_it(ctx: TestContext, monkeypatch: Any) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    draft = Node(id="", type=NodeType.FACT, title="F", evidence_url="https://news.example.com/x", module="scout")
    node, _ = ctx.graph.create_once(draft, "module:scout", "k1")
    ctx.graph._table.delete_item(Key={"pk": f"N#{node.id}", "sk": "META"})
    real = ctx.graph._table.delete_item

    def raced(**kwargs: Any) -> Any:
        if kwargs.get("ConditionExpression") == "node_id = :n":
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "DeleteItem")
        return real(**kwargs)

    monkeypatch.setattr(ctx.graph._table, "delete_item", raced)
    from insights_radar.core.errors import NotFoundError

    with pytest.raises(NotFoundError):  # the key is still there, pointing at nothing: no second loop
        ctx.graph.create_once(draft, "module:scout", "k1")
    monkeypatch.setattr(
        ctx.graph._table,
        "delete_item",
        lambda **kw: (_ for _ in ()).throw(
            ClientError({"Error": {"Code": "Throttling", "Message": "x"}}, "DeleteItem")
        ),
    )
    with pytest.raises(ClientError):
        ctx.graph.create_once(draft, "module:scout", "k1")


# ---- B3-058 / B3-059 / B3-060: kg_recent cursors and hidden items -------------------------------


def test_b3_058_an_after_without_milliseconds_does_not_skip_the_second(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "signal")
    ctx.clock.advance(timedelta(milliseconds=500))
    api.tool("kg_save_output", tok, {"title": "A", "summary": "x"})
    trend = _tok(ctx, "trend")
    _, page = api.tool("kg_recent", trend, {"type": "signal", "after": "2026-10-02T08:00:00Z"})
    assert [i["title"] for i in page["result"]["items"]] == ["A"]
    assert api.tool("kg_recent", trend, {"type": "signal", "after": "yesterday"})[0] == 400
    assert api.tool("kg_recent", trend, {"type": "signal", "after": "2026-10-02T08:00:00Z#nope"})[0] == 400


def test_b3_059_resume_after_does_not_skip_same_millisecond_siblings(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "signal")
    for t in ("A", "B", "C"):
        api.tool("kg_save_output", tok, {"title": t, "summary": "x"})
    trend = _tok(ctx, "trend")
    _, p1 = api.tool("kg_recent", trend, {"type": "signal", "after": "2000-01-01T00:00:00.000Z", "limit": 2})
    first = p1["result"]["items"]
    assert len(first) == 2 and p1["result"]["resume_after"] == first[-1]["resume_after"]
    _, p2 = api.tool("kg_recent", trend, {"type": "signal", "after": first[-1]["resume_after"]})
    titles = [i["title"] for i in first + p2["result"]["items"]]
    assert sorted(titles) == ["A", "B", "C"] and len(titles) == 3  # same millisecond: id order
    # last_processed_at takes the same form, and a future one is pulled back to now.
    _, prog = api.tool("progress_update", trend, {"last_processed_at": first[-1]["resume_after"]})
    assert prog["result"]["last_processed_at"] == first[-1]["resume_after"]
    _, prog = api.tool("progress_update", _tok(ctx, "gap"), {"last_processed_at": "2099-01-01T00:00:00Z#signal/x"})
    assert prog["result"]["last_processed_at"] == "2026-10-02T08:00:00.000Z"


def test_b3_060_a_page_of_hidden_items_says_so(api: Client, ctx: TestContext) -> None:
    _custom(api, "pkev", ["kg_read", "kg_write_output", "kg_write_evidence", "product_knowledge"])
    tok = _tok(ctx, "pkev")
    for i in range(3):
        api.tool(
            "kg_add_evidence", tok, {"type": "fact", "title": f"P{i}", "evidence_url": f"https://intra.example.com/{i}"}
        )
    ctx.clock.advance(timedelta(seconds=1))
    api.tool(
        "kg_add_evidence",
        _tok(ctx, "scout"),
        {"type": "fact", "title": "Public", "evidence_url": "https://news.example.com/x"},
    )
    signal = _tok(ctx, "signal")
    _, page = api.tool("kg_recent", signal, {"type": "fact", "after": "2000-01-01T00:00:00.000Z", "limit": 3})
    assert page["result"]["items"] == [] and page["result"]["hidden"] == 3 and page["result"]["resume_after"]
    _, nxt = api.tool("kg_recent", signal, {"type": "fact", "after": page["result"]["resume_after"]})
    assert [i["title"] for i in nxt["result"]["items"]] == ["Public"] and nxt["result"]["hidden"] == 0
    ctx.reindex()
    _, hits = api.tool("kg_search", _tok(ctx, "signal", "run-2"), {"query": "P1 P2 fact", "limit": 5})
    # Search says what it leaves out in fixed text: a count told an injected module whether a private record
    # matched a name it guessed (round 34 security review).
    assert "hidden" not in hits["result"] and "may not read" in hits["result"]["scope"]
    assert all(r["title"] == "Public" for r in hits["result"]["results"])
    _, empty = api.tool("kg_recent", signal, {"type": "trend"})
    assert empty["result"]["resume_after"] is None


# ---- B3-065 / B3-066 / B3-070 ---------------------------------------------------------------------


def test_b3_065_source_address_is_audited_once_per_run_and_source(api: Client, ctx: TestContext) -> None:
    _, src, _ = api.call(
        "POST", "/api/modules/scout/sources", {"name": "A", "type": "rss", "address": "https://feeds.example.com/a.xml"}
    )
    tok = _tok(ctx, "scout")
    for _ in range(5):
        assert api.tool("source_address", tok, {"source_id": src["id"]})[0] == 200
    api.tool("source_address", _tok(ctx, "scout", "run-2"), {"source_id": src["id"]})
    assert len(_rows(ctx, "agent.source_address")) == 2


def test_b3_065_audit_marker_errors_propagate(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from botocore.exceptions import ClientError

    _, src, _ = api.call(
        "POST", "/api/modules/scout/sources", {"name": "A", "type": "rss", "address": "https://feeds.example.com/a.xml"}
    )
    tok = _tok(ctx, "scout")
    real = ctx.ops_table.put_item

    def throttled(**kwargs: Any) -> Any:
        if str(kwargs.get("Item", {}).get("pk", "")).startswith("AUDITED#"):
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "PutItem")
        return real(**kwargs)

    monkeypatch.setattr(ctx.ops_table, "put_item", throttled)
    with pytest.raises(ClientError):
        api.app.tools.call("source_address", tok, {"source_id": src["id"]}, CALLER_ARN)


def test_b3_066_kg_get_with_ids_always_answers_in_the_batch_shape(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "signal")
    _, a = api.tool("kg_save_output", tok, {"title": "A", "summary": "x"})
    one = api.tool("kg_get", tok, {"ids": [a["result"]["id"]]})[1]["result"]
    missing = "signal/01a0fba0-9400-7000-8000-000000000000"
    status, miss = api.tool("kg_get", tok, {"ids": [missing]})
    assert [n["id"] for n in one["nodes"]] == [a["result"]["id"]] and one["missing"] == []
    assert status == 200 and miss["result"] == {"nodes": [], "missing": [missing]}


def test_b3_070_a_plain_upsert_writes_no_probe_counter(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "scout")
    for i in range(3):
        assert api.tool("kg_upsert_entity", tok, {"type": "topic", "name": f"T{i}"})[0] == 200
    assert ctx.ops_table.get_item(Key={"pk": "PROBE#run-1", "sk": "ENTITY"}).get("Item") is None


# ---- B3-047 / B3-048 / B3-049: shrinking a radar needs a confirmation ------------------------------


def _filled(api: Client, slug: str, n: int, role: str = "admin") -> dict[str, Any]:
    _, r, _ = api.call("POST", "/api/radars", {"slug": slug, "name": "R", "template": "blank"}, role=role)
    old = [{"key": f"k{i}", "title": f"Old {i}", "sector": "General", "ring": "Now"} for i in range(n)]
    status, r, _ = api.call(
        "PUT", f"/api/radars/{slug}/entries", {"entries": old, "expected_version": r["current_version"]}, role=role
    )
    assert status == 200, r
    return dict(r)


def test_b3_047_a_replace_import_of_different_rows_needs_a_confirmation(api: Client, ctx: TestContext) -> None:
    r = _filled(api, "r1", 20)
    csv = "title,sector,ring\n" + "".join(f"New {i},General,Now\n" for i in range(11))
    _, dry, _ = api.call("POST", "/api/radars/r1/import", {"format": "csv", "text": csv, "dry_run": True})
    assert dry["removed"] == 20
    status, body, _ = api.call(
        "POST", "/api/radars/r1/import", {"format": "csv", "text": csv, "expected_version": r["current_version"]}
    )
    assert status == 400 and body["details"]["code"] == "shrinking_import" and body["details"]["removed"] == 20
    status, _, _ = api.call(
        "POST",
        "/api/radars/r1/import",
        {"format": "csv", "text": csv, "expected_version": r["current_version"], "allow_empty": True},
    )
    assert status == 200 and len(ctx.radars.entries("r1")) == 11
    # Keeping the titles keeps the entries: an import that re-sends most of them is not refused.
    same = "title,sector,ring\n" + "".join(f"New {i},General,Later\n" for i in range(10))
    status, _, _ = api.call(
        "POST",
        "/api/radars/r1/import",
        {"format": "csv", "text": same, "expected_version": ctx.radars.get("r1").current_version},
    )
    assert status == 200


def test_b3_048_a_save_that_wipes_most_of_a_radar_needs_a_confirmation(api: Client, ctx: TestContext) -> None:
    r = _filled(api, "w", 50, role="editor")
    one = [{"key": "k0", "title": "Old 0", "sector": "General", "ring": "Now"}]
    status, body, _ = api.call(
        "PUT", "/api/radars/w/entries", {"entries": one, "expected_version": r["current_version"]}, role="editor"
    )
    assert status == 400 and body["details"]["code"] == "shrinking_save"
    status, r2, _ = api.call(
        "PUT",
        "/api/radars/w/entries",
        {"entries": one, "expected_version": r["current_version"], "allow_empty": True},
        role="editor",
    )
    assert status == 200 and r2["entry_count"] == 1
    # Removing the last entry empties the radar: confirmed too since CTR HA-078.
    status, body, _ = api.call(
        "PUT", "/api/radars/w/entries", {"entries": [], "expected_version": r2["current_version"]}, role="editor"
    )
    assert status == 400 and body["details"]["code"] == "shrinking_save" and body["details"]["after"] == 0
    status, r3, _ = api.call(
        "PUT",
        "/api/radars/w/entries",
        {"entries": [], "expected_version": r2["current_version"], "allow_empty": True},
        role="editor",
    )
    assert status == 200 and r3["entry_count"] == 0


def test_b3_049_a_rollback_to_the_empty_version_needs_a_confirmation(api: Client, ctx: TestContext) -> None:
    r = _filled(api, "r2", 50)
    status, body, _ = api.call(
        "POST", "/api/radars/r2/rollback", {"version": 1, "expected_version": r["current_version"]}
    )
    assert status == 400 and body["details"]["code"] == "shrinking_rollback"
    status, r, _ = api.call(
        "POST", "/api/radars/r2/rollback", {"version": 1, "expected_version": r["current_version"], "allow_empty": True}
    )
    assert status == 200 and r["entry_count"] == 0


# ---- B3-050 / B3-051 / B3-076: radar races and duplicates ------------------------------------------


def test_b3_050_a_schema_rename_does_not_resurrect_a_discarded_suggestion(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    from insights_radar.radars import service as rs

    _assist(api, "r3")
    body = {
        "slug": "r3",
        "proposal": {"title": "Kafka", "sector": "General", "ring": "Next"},
        "reason": "evidence says so",
    }
    _, sug = api.tool("radar_suggest", _tok(ctx, "curator"), body)
    other = {**body, "proposal": {"title": "Pulsar", "sector": "General", "ring": "Next"}}
    api.tool("radar_suggest", _tok(ctx, "curator", "run-2"), other)
    key = sug["result"]["entry_key"]
    real = rs.Query.all_items

    def racing(table: Any, **kw: Any) -> Any:
        items = real(table, **kw)
        if any(str(i.get("sk", "")).startswith("SUG#") for i in items):
            ctx.radars._t.delete_item(Key={"pk": "RADAR#r3", "sk": f"SUG#{key}"})
        return items

    monkeypatch.setattr(rs.Query, "all_items", staticmethod(racing))
    radar = ctx.radars.get("r3")
    schema = {"sectors": ["General"], "rings": ["Now", "Soon", "Later"]}
    status, out, _ = api.call(
        "PUT",
        "/api/radars/r3/schema",
        {"schema": schema, "expected_version": radar.current_version, "renames": {"ring": {"Next": "Soon"}}},
    )
    monkeypatch.setattr(rs.Query, "all_items", staticmethod(real))
    left = ctx.radars.suggestions("r3")
    assert status == 200, out
    assert [s["proposal"]["title"] for s in left] == ["Pulsar"] and left[0]["proposal"]["ring"] == "Soon"


def test_b3_050_rename_write_errors_propagate(ctx: TestContext, monkeypatch: Any) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.identity.principal import Principal
    from insights_radar.radars.model import RadarSchema

    admin = Principal.from_claims({"sub": "u-admin", "email": "admin@example.com", "cognito:groups": "[admin]"})
    ctx.radars.create(admin, "r4", "R", template="blank")
    ctx.radars.update_meta(admin, "r4", {"mode": "assist"}, 1)
    ctx.radars.propose(
        "r4", "module:curator", "r", {"title": "K", "sector": "General", "ring": "Now"}, "reasons enough"
    )

    def throttled(**kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "UpdateItem")

    monkeypatch.setattr(ctx.radars._t, "update_item", throttled)
    schema = RadarSchema.from_public({"sectors": ["General"], "rings": ["Today", "Later"]})
    with pytest.raises(ClientError):
        ctx.radars._rename_open_suggestions("r4", schema, {"ring": {"Now": "Today"}})


def test_b3_051_a_radar_delete_that_loses_the_race_leaves_no_archive(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    from insights_radar.identity.principal import Principal
    from insights_radar.radars.model import RadarEntry

    api.call("POST", "/api/radars", {"slug": "del", "name": "D", "template": "blank"}, role="editor")
    real = ctx.radars.get
    state = {"n": 0}
    ed = Principal.from_claims({"sub": "u-editor", "email": "editor@example.com", "cognito:groups": "[editor]"})

    def stale(slug: str) -> Any:
        got = real(slug)
        state["n"] += 1
        if state["n"] == 1:
            ctx.radars.save(
                ed, slug, [RadarEntry(key="k1", title="T", sector="General", ring="Now")], got.current_version
            )
        return got

    monkeypatch.setattr(ctx.radars, "get", stale)
    status, _, _ = api.call("DELETE", "/api/radars/del", role="editor")
    monkeypatch.setattr(ctx.radars, "get", real)
    assert status == 409 and not [k for k in ctx.radars._blobs.objects if k.startswith("deleted/radars/del/")]
    assert api.call("DELETE", "/api/radars/del", role="editor")[0] == 200
    assert [k for k in ctx.radars._blobs.objects if k.startswith("deleted/radars/del/")]


def test_b3_076_a_retried_new_entry_proposal_is_refused(api: Client, ctx: TestContext) -> None:
    _assist(api, "dup")
    cur = _tok(ctx, "curator")
    args = {
        "slug": "dup",
        "proposal": {"title": "Kafka", "sector": "General", "ring": "Now"},
        "reason": "ten chars plus",
    }
    assert api.tool("radar_suggest", cur, args)[0] == 200
    status, body = api.tool("radar_suggest", cur, {**args, "proposal": {**args["proposal"], "title": " kafka "}})
    assert status == 409 and body["details"]["code"] == "suggestion_pending" and len(ctx.radars.suggestions("dup")) == 1


# ---- B3-043 / B3-044: what the audit records ------------------------------------------------------


def test_b3_043_an_accept_records_what_was_applied(api: Client, ctx: TestContext) -> None:
    _assist(api, "acc")
    key = _kafka(api, ctx, "acc")
    status, _, _ = api.call(
        "POST",
        f"/api/radars/acc/suggestions/{key}/accept",
        {"edits": {"ring": "Later", "link": "https://elsewhere.example.com/"}},
    )
    assert status == 200
    details = _rows(ctx, "suggestion.accept")[0]["details"]
    assert details["title"] == "Kafka" and details["proposed_by"] == "module:curator" and details["run_id"] == "run-1"
    assert details["edited"] == ["link", "ring"] and details["version"] == ctx.radars.get("acc").current_version


def test_b3_044_long_lists_continue_on_further_rows(api: Client, ctx: TestContext) -> None:
    _, bob, _ = api.call("POST", "/api/users", {"email": "bob@example.com", "role": "editor"})
    for i in range(25):
        slug = f"team-radar-with-a-fairly-long-descriptive-slug-number-{i:02d}"
        _, r, _ = api.call("POST", "/api/radars", {"slug": slug, "name": f"R{i}", "template": "blank"})
        api.call(
            "PUT", f"/api/radars/{slug}/access", {"version_lock": r["version_lock"], "editors": ["bob@example.com"]}
        )
    status, res, _ = api.call("DELETE", f"/api/users/{bob['username']}")
    assert status == 200 and len(res["editor_access_removed"]) == 25
    main = _rows(ctx, "user.delete")[0]["details"]
    rest = _rows(ctx, "user.delete.continued")
    assert len(main["editor_of"]) == 10 and main["continued_rows"] == 2 and len(rest) == 2
    recorded = main["editor_of"] + [s for row in rest for s in row["details"]["editor_of"]]
    assert sorted(recorded) == sorted(res["editor_access_removed"])
    assert {row["details"]["of"] for row in rest} == {2}


def test_b3_044_continuation_rows_are_bounded(ctx: TestContext) -> None:
    from insights_radar.audit.log import CONTINUATION_ROWS_MAX

    ctx.audit.record_actor("system:x", "", "big", "t", {"ids": [f"id-{i}" for i in range(2000)]})
    assert len(_rows(ctx, "big.continued")) == CONTINUATION_ROWS_MAX


def test_b3_044_long_strings_are_still_clipped(ctx: TestContext) -> None:
    ctx.audit.record_actor("system:x", "", "long", "t", {"a": "x" * 1500, "b": "y" * 1500})
    details = _rows(ctx, "long")[0]["details"]
    assert details == {"a": "x" * 200, "b": "y" * 200}


# ---- B3-033 .. B3-046: route validation, users and audit ----------------------------------------


def test_b3_033_product_list_limits_are_the_models(api: Client, ctx: TestContext) -> None:
    s1, b1, _ = api.call("POST", "/api/products", {"name": "P", "capabilities": [f"c{i}" for i in range(60)]})
    s2, b2, _ = api.call("POST", "/api/products", {"name": "P", "capabilities": ["x" * 400]})
    s3, b3, _ = api.call(
        "POST", "/api/products", {"name": "P", "doc_urls": [f"https://d.example.com/{i}" for i in range(30)]}
    )
    assert s1 == s2 == s3 == 400
    assert "50" in b1["message"] and "300" in b2["message"] and "20" in b3["message"]


def test_b3_034_a_source_patch_cannot_duplicate_another_address(api: Client, ctx: TestContext) -> None:
    api.call(
        "POST", "/api/modules/scout/sources", {"name": "A", "type": "rss", "address": "https://feeds.example.com/a.xml"}
    )
    _, b, _ = api.call(
        "POST", "/api/modules/scout/sources", {"name": "B", "type": "rss", "address": "https://feeds.example.com/b.xml"}
    )
    status, body, _ = api.call(
        "PATCH", f"/api/modules/scout/sources/{b['id']}", {"address": "https://feeds.example.com/a.xml"}
    )
    assert status == 409 and body["details"]["code"] == "duplicate_source"
    # Its own address again is no duplicate.
    assert (
        api.call("PATCH", f"/api/modules/scout/sources/{b['id']}", {"address": "https://feeds.example.com/b.xml/"})[0]
        == 200
    )


def test_b3_035_source_uniqueness_compares_path_and_query_exactly(api: Client, ctx: TestContext) -> None:
    def add(name: str, address: str) -> int:
        return api.call("POST", "/api/modules/scout/sources", {"name": name, "type": "public_api", "address": address})[
            0
        ]

    assert add("A", "https://api.example.com/v1/Items?id=AbC") == 201
    assert add("B", "https://api.example.com/v1/items?id=abc") == 201  # a different resource
    assert add("C", "https://API.example.com:443/v1/Items?id=AbC") == 409
    assert add("D", "https://api.example.com/v1/Items?id=AbC#x") == 409
    assert add("E", "https://api.example.com:8443/v1/Items?id=AbC") == 201


def test_b3_035_an_unparseable_address_is_compared_as_written() -> None:
    from insights_radar.modules.repository import _address_key

    assert _address_key(" https://[::1/a ") == "https://[::1/a"
    # CR3-20: the default port of the address's own scheme folds, the other scheme's does not.
    assert _address_key("http://h:80/x") == _address_key("http://h/x") == "http://h/x"
    assert _address_key("https://h:443/x") == "https://h/x"
    assert _address_key("http://h:443/x") == "http://h:443/x"


def test_b3_036_a_source_patch_with_a_list_type_is_a_400(api: Client, ctx: TestContext) -> None:
    _, src, _ = api.call(
        "POST", "/api/modules/scout/sources", {"name": "A", "type": "rss", "address": "https://f.example.com/a"}
    )
    for bad in ([], {}, 3):
        status, body, _ = api.call("PATCH", f"/api/modules/scout/sources/{src['id']}", {"type": bad})
        assert status == 400, body


def test_b3_037_an_owner_transfer_with_an_empty_subject_is_a_400(api: Client, ctx: TestContext) -> None:
    _, r, _ = api.call("POST", "/api/radars", {"slug": "t", "name": "T", "template": "blank"})
    status, body, _ = api.call(
        "PUT", "/api/radars/t/access", {"version_lock": r["version_lock"], "owner": {"subject": ""}}
    )
    assert status == 400, body
    status, body, _ = api.call(
        "PUT", "/api/radars/t/access", {"version_lock": r["version_lock"], "owner": {"subject": "x" * 129}}
    )
    assert status == 400, body


def test_b3_038_enabling_an_enabled_user_is_a_no_op(api: Client, ctx: TestContext) -> None:
    _, bob, _ = api.call("POST", "/api/users", {"email": "bob@example.com", "role": "editor"})
    status, body, _ = api.call("PATCH", f"/api/users/{bob['username']}", {"enabled": True})
    assert status == 200 and body["enabled"] is True and _rows(ctx, "user.update") == []


def test_b3_039_a_failure_after_the_account_is_deleted_still_leaves_the_audit_row(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    _, bob, _ = api.call("POST", "/api/users", {"email": "bob@example.com", "role": "editor"})

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("throttled")

    monkeypatch.setattr(ctx.insights, "working_marks_of", boom)
    status, body, _ = api.call("DELETE", f"/api/users/{bob['username']}")
    rows = _rows(ctx, "user.delete")
    assert status == 200 and body["working_marks_removed"] is None
    assert rows and rows[0]["details"]["working_marks_removed"] is None


def test_b3_040_the_user_list_shows_a_role_change_at_once(api: Client, ctx: TestContext) -> None:
    _, bob, _ = api.call("POST", "/api/users", {"email": "bob@example.com", "role": "viewer"})
    api.call("GET", "/api/users")
    api.call("PATCH", f"/api/users/{bob['username']}", {"role": "editor"})
    _, page, _ = api.call("GET", "/api/users")
    assert next(u["role"] for u in page["items"] if u["username"] == bob["username"]) == "editor"
    _, carol, _ = api.call("POST", "/api/users", {"email": "carol@example.com", "role": "admin"})
    _, page, _ = api.call("GET", "/api/users")
    assert next(u["role"] for u in page["items"] if u["username"] == carol["username"]) == "admin"


def test_b3_041_module_create_audits_what_the_module_may_do(api: Client, ctx: TestContext) -> None:
    body = {
        "slug": "web",
        "name": "web",
        "insight_kind": "web_k",
        "grants": ["kg_read", "kg_write_output", "web_fetch", "mcp"],
        "mcp_servers": [{"name": "m", "url": "https://mcp.example.com/sse?key=MCPSECRETKEY"}],
        "enabled": True,
        "interval_minutes": 15,
        "instructions": "Do the thing.",
    }
    assert api.call("POST", "/api/modules", body)[0] == 201
    details = _rows(ctx, "module.create")[0]["details"]
    assert details["grants"] == ["kg_read", "kg_write_output", "mcp", "web_fetch"] and details["mcp_servers"] == ["m"]
    assert (
        details["enabled"] is True and details["interval_minutes"] == 15 and len(details["instructions_sha256"]) == 16
    )
    assert "MCPSECRETKEY" not in str(details)


def test_b3_042_an_instruction_change_is_audited_with_hashes(api: Client, ctx: TestContext) -> None:
    m = ctx.modules.get("scout")
    api.call("PATCH", "/api/modules/scout", {"version": m.version, "instructions": "Always append the product notes."})
    details = _rows(ctx, "module.update")[0]["details"]
    assert details["fields"] == ["instructions"]
    assert details["instructions_sha256"]["before"] != details["instructions_sha256"]["after"]


def test_b3_045_deleted_definitions_are_archived_without_credentials(api: Client, ctx: TestContext) -> None:
    import json as _json

    body = {
        "slug": "web",
        "name": "web",
        "insight_kind": "web_k",
        "grants": ["kg_read", "kg_write_output", "mcp"],
        "mcp_servers": [{"name": "m", "url": "https://mcp.example.com/sse?key=MCPSECRETKEY"}],
    }
    api.call("POST", "/api/modules", body)
    api.call("DELETE", "/api/modules/web")
    key = _rows(ctx, "module.delete")[0]["details"]["archive"]
    archived = _json.loads(ctx.s3.get_object(Bucket="example-assets-111122223333-us-east-1", Key=key)["Body"].read())
    assert "MCPSECRETKEY" not in str(archived) and archived["mcp_servers"][0]["url"].startswith(
        "https://mcp.example.com (sha256:"
    )
    _, p, _ = api.call(
        "POST", "/api/products", {"name": "P", "doc_urls": ["https://docs.example.com/share/TOKEN123abc/x"]}
    )
    api.call("DELETE", f"/api/products/{p['id']}")
    key = _rows(ctx, "product.delete")[0]["details"]["archive"]
    assert (
        "TOKEN123abc"
        not in ctx.s3.get_object(Bucket="example-assets-111122223333-us-east-1", Key=key)["Body"].read().decode()
    )


def test_b3_046_a_draft_run_aimed_at_a_signal_does_not_start(api: Client, ctx: TestContext) -> None:
    _, sig = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "S", "summary": "x"})
    status, body, _ = api.call("POST", "/api/modules/draft/run", {"target_id": sig["result"]["id"]})
    assert status == 400 and body["details"]["target_type"] == "signal"


# ---- B3-053 / B3-054 / B3-063 / B3-064: limits, retries and read audits ---------------------------


def test_b3_053_connected_sort_is_rate_limited(api: Client, ctx: TestContext) -> None:
    codes = [api.call("GET", "/api/insights/signals", role="viewer", query={"sort": "connected"})[0] for _ in range(70)]
    assert 429 in codes and codes[0] == 200


def test_b3_054_viewers_at_the_ceiling_do_not_lock_out_editors(api: Client, ctx: TestContext) -> None:
    from insights_radar.core.errors import RateLimitedError
    from insights_radar.core.ratelimit import RateLimiter

    limiter = RateLimiter(ctx.ops_table, ctx.clock, per_minute=100, workspace_per_minute=5)
    for i in range(5):
        limiter.hit(f"v{i}", "bedrock", role="viewer")

    with pytest.raises(RateLimitedError):
        limiter.hit("v9", "bedrock", role="viewer")
    assert limiter.hit("e1", "bedrock", role="editor") == 1


def test_b3_063_a_closed_budget_says_until_midnight_and_the_kill_switch_says_nothing(
    api: Client, ctx: TestContext
) -> None:
    ctx.modules.charge_weighted("signal", 3_000_000)
    s, b, h = api.call("POST", "/api/modules/signal/run", {})
    assert (
        s == 503
        and b["code"] == "budget_exhausted"
        and 1 <= int(h["Retry-After"]) <= 86_400
        and h["Retry-After"] != "1"
    )
    api.call("PUT", "/api/andon", {"genai_enabled": False, "reason": "incident"})
    s2, b2, h2 = api.call("POST", "/api/modules/trend/run", {})
    assert s2 == 503 and b2["code"] == "genai_disabled" and "Retry-After" not in h2


def test_b3_063_other_unavailable_errors_still_say_one_second() -> None:
    from insights_radar.api.http import error_response
    from insights_radar.core.errors import ServiceUnavailableError

    assert error_response(ServiceUnavailableError("busy")).headers["Retry-After"] == "1"


def test_b3_064_reading_a_draft_repeatedly_leaves_one_row_a_day(api: Client, ctx: TestContext) -> None:
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    d = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="D", module="draft"), "module:draft")
    ctx.s3.put_object(
        Bucket="example-assets-111122223333-us-east-1", Key=f"drafts/{d.id.split('/')[1]}.md", Body=b"# hi"
    )
    t, u = d.id.split("/")
    codes = [api.call("GET", f"/api/drafts/{t}/{u}", role="editor")[0] for _ in range(20)]
    assert set(codes) == {200} and len(_rows(ctx, "draft.read")) == 1
    api.call("GET", f"/api/drafts/{t}/{u}", role="admin")
    ctx.clock.advance(timedelta(days=1))
    api.call("GET", f"/api/drafts/{t}/{u}", role="editor")
    assert len(_rows(ctx, "draft.read")) == 1  # today's page: the new day's first read
    many = [api.call("GET", f"/api/drafts/{t}/{u}", role="editor")[0] for _ in range(70)]
    assert 429 in many


def test_b3_064_read_marker_errors_propagate(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    d = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="D", module="draft"), "module:draft")
    ctx.s3.put_object(
        Bucket="example-assets-111122223333-us-east-1", Key=f"drafts/{d.id.split('/')[1]}.md", Body=b"# hi"
    )
    real = ctx.ops_table.put_item

    def throttled(**kwargs: Any) -> Any:
        if str(kwargs.get("Item", {}).get("pk", "")).startswith("READ#"):
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "PutItem")
        return real(**kwargs)

    monkeypatch.setattr(ctx.ops_table, "put_item", throttled)
    t, u = d.id.split("/")
    assert api.call("GET", f"/api/drafts/{t}/{u}", role="editor")[0] == 503  # throttling, not swallowed


# ---- B3-020 .. B3-025, B3-029: seed, budgets ------------------------------------------------------


def test_b3_020_a_concurrency_an_admin_chose_is_kept(api: Client, ctx: TestContext) -> None:
    from dataclasses import replace as dc_replace

    from insights_radar.api import workers
    from insights_radar.modules.templates import TEMPLATE_CHANGES_KEY, TEMPLATE_OVERRIDES_KEY

    d = ctx.modules.get("draft")
    old = dc_replace(d, max_concurrency=2, extra={k: v for k, v in d.extra.items() if k != TEMPLATE_CHANGES_KEY})
    ctx.modules._t.put_item(Item=ctx.modules._def_item(old))  # as stored before the change shipped
    status, body, _ = api.call("PATCH", "/api/modules/draft", {"version": old.version, "max_concurrency": 1})
    assert status == 200, body
    out = workers.seed({}, None, ctx)
    kept = ctx.modules.get("draft")
    assert kept.max_concurrency == 1 and "2026-10-draft-concurrency-5" in kept.extra[TEMPLATE_OVERRIDES_KEY]
    assert "draft" in out["migrated"]
    workers.seed({}, None, ctx)  # and it stays kept
    assert ctx.modules.get("draft").max_concurrency == 1


def test_b3_020_an_untouched_old_default_is_still_raised(ctx: TestContext) -> None:
    from dataclasses import replace as dc_replace

    from insights_radar.api import workers
    from insights_radar.modules.templates import TEMPLATE_CHANGES_KEY

    d = ctx.modules.get("draft")
    old = dc_replace(d, max_concurrency=1, extra={k: v for k, v in d.extra.items() if k != TEMPLATE_CHANGES_KEY})
    ctx.modules._t.put_item(Item=ctx.modules._def_item(old))
    workers.seed({}, None, ctx)
    assert ctx.modules.get("draft").max_concurrency == 5


def _unsafe(ctx: TestContext, slug: str, version: int = 3, **extra: Any) -> None:
    from insights_radar.graph.registry import NodeType
    from insights_radar.modules.model import ModuleDefinition, ModuleTemplate
    from insights_radar.modules.model import ToolGrant as G

    grants = extra.pop("grants", frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.WEB_FETCH, G.PRODUCT_KNOWLEDGE}))
    m = ModuleDefinition(
        slug=slug,
        name=slug,
        template=ModuleTemplate.CUSTOM,
        output_kind=NodeType.INSIGHT,
        insight_kind=f"{slug}_k",
        grants=grants,
        enabled=True,
        version=version,
        **extra,
    )
    ctx.modules._t.put_item(Item=ctx.modules._def_item(m))


def test_b3_021_a_stale_listing_does_not_fail_the_deploy(ctx: TestContext, monkeypatch: Any) -> None:
    from dataclasses import replace as dc_replace

    from insights_radar.api import workers

    _unsafe(ctx, "cur2")
    real = ctx.modules.list_all
    monkeypatch.setattr(
        ctx.modules, "list_all", lambda: [dc_replace(x, version=2) if x.slug == "cur2" else x for x in real()]
    )
    out = workers.seed({}, None, ctx)
    assert out["failed"] == [] and ctx.modules.get("cur2").enabled is False and "cur2" in out["migrated"]


def test_b3_021_a_module_that_keeps_changing_is_a_warning(ctx: TestContext, monkeypatch: Any) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.api import workers

    _unsafe(ctx, "cur3")
    real = ctx.modules._t.put_item

    def racing(**kwargs: Any) -> Any:
        if kwargs.get("Item", {}).get("pk") == "MODULE#cur3":
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "PutItem")
        return real(**kwargs)

    monkeypatch.setattr(ctx.modules._t, "put_item", racing)
    out = workers.seed({}, None, ctx)  # no ServiceUnavailableError
    assert out["failed"] == [] and out["warnings"] == ["cur3"]


def test_b3_021_a_module_fixed_meanwhile_is_left_alone(ctx: TestContext, monkeypatch: Any) -> None:
    from dataclasses import replace as dc_replace

    _unsafe(ctx, "cur4")
    real = ctx.modules.list_all
    listed = real()
    fixed = dc_replace(ctx.modules.get("cur4"), enabled=False)
    ctx.modules._t.put_item(Item=ctx.modules._def_item(fixed))
    monkeypatch.setattr(ctx.modules, "list_all", lambda: listed)
    report = ctx.modules.seed_builtins()
    assert "cur4" not in report.migrated and report.warnings == {}


def test_b3_022_an_outbound_module_with_private_inputs_cannot_run(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers
    from insights_radar.graph.registry import NodeType
    from insights_radar.modules.model import ToolGrant as G

    _unsafe(ctx, "web", grants=frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.WEB_FETCH}), input_types=(NodeType.GAP,))
    assert "gap" in (ctx.modules.get("web").run_refusal or "")
    workers.seed({}, None, ctx)
    assert ctx.modules.get("web").enabled is False


def test_b3_023_tombstones_are_one_item_per_name(api: Client, ctx: TestContext) -> None:
    _custom(api, "gone", ["kg_read", "kg_write_output", "product_knowledge"])
    assert api.call("DELETE", "/api/modules/gone")[0] == 200
    assert ctx.modules.tombstones() == (frozenset({"gone"}), frozenset({"gone_k"}), frozenset({"gone"}))
    assert ctx.ops_table.get_item(Key={"pk": "MODULES", "sk": "TOMBSTONE"}).get("Item") is None
    body = {"slug": "gone", "name": "x", "insight_kind": "new_k", "grants": ["kg_read", "kg_write_output"]}
    assert api.call("POST", "/api/modules", body)[0] == 409
    body = {"slug": "fresh", "name": "x", "insight_kind": "gone_k", "grants": ["kg_read", "kg_write_output"]}
    assert api.call("POST", "/api/modules", body)[0] == 409
    assert "gone" in ctx.modules.private_writer_slugs()


def test_b3_023_the_old_tombstone_item_is_split_once(ctx: TestContext) -> None:
    from insights_radar.api import workers

    ctx.ops_table.put_item(
        Item={
            "pk": "MODULES",
            "sk": "TOMBSTONE",
            "slugs": {"a", "b"},
            "insight_kinds": {"a_k"},
            "private_writers": {"b"},
        }
    )
    out = workers.seed({}, None, ctx)
    assert out["failed"] == []
    assert ctx.modules.tombstones() == (frozenset({"a", "b"}), frozenset({"a_k"}), frozenset({"b"}))
    # The legacy item stays: the previous release reads it after a rollback (SR3-01).
    assert ctx.ops_table.get_item(Key={"pk": "MODULES", "sk": "TOMBSTONE"}).get("Item")
    assert ctx.modules.split_tombstone() == 0  # nothing new to copy


def test_sr3_01_the_legacy_tombstone_counts_before_the_split(api: Client, ctx: TestContext) -> None:
    """While the seed has not split it yet (the new code is live first), names only the legacy item
    records are still reserved and still private writers."""
    ctx.ops_table.put_item(
        Item={"pk": "MODULES", "sk": "TOMBSTONE", "slugs": {"lw"}, "insight_kinds": {"lw_k"}, "private_writers": {"lw"}}
    )
    assert "lw" in ctx.modules.private_writer_slugs()
    assert ctx.modules.tombstones() == (frozenset({"lw"}), frozenset({"lw_k"}), frozenset({"lw"}))
    body = {"slug": "lw", "name": "x", "insight_kind": "new_k", "grants": ["kg_read", "kg_write_output"]}
    assert api.call("POST", "/api/modules", body)[0] == 409
    body = {"slug": "fresh", "name": "x", "insight_kind": "lw_k", "grants": ["kg_read", "kg_write_output"]}
    assert api.call("POST", "/api/modules", body)[0] == 409


def test_b3_025_a_budget_exhausted_run_with_no_usage_keeps_its_hold(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    for slug, status in (("signal", "budget_exhausted"), ("trend", "cancelled")):
        ctx.modules.update(slug, {"enabled": True}, ctx.modules.get(slug).version)
        run = ctx.module_service.start(slug, "manual", "user:u-admin")
        limit = ctx.modules.get(slug).daily_token_budget
        before = ctx.modules.budget(slug, limit).consumed
        event = {"module": slug, "run_id": run.run_id, "result": {"status": status, "usage": {}}}
        out = workers.finalize(event, None, ctx)
        assert out["weighted_tokens"] == run.budget_hold > 0
        assert ctx.modules.budget(slug, limit).consumed == before


def test_b3_025_a_proven_not_started_run_is_free(ctx: TestContext) -> None:
    from insights_radar.core.tokens import TokenUsage
    from insights_radar.modules.repository import RunStatus

    ctx.modules.update("signal", {"enabled": True}, ctx.modules.get("signal").version)
    run = ctx.module_service.start("signal", "manual", "user:u-admin")
    done = ctx.module_service.finalize(
        "signal", run.run_id, RunStatus.CANCELLED, TokenUsage(0, 0, 0, 0), not_started=True
    )
    assert done.weighted_tokens == 0 and ctx.modules.budget("signal", 2_000_000).consumed == 0


def test_b3_029_chat_usage_is_weighted_at_the_chat_models_tier(ctx: TestContext) -> None:
    from dataclasses import replace as dc_replace

    from insights_radar.api.context import AppContext, Settings
    from insights_radar.chat.budget import ChatBudget
    from insights_radar.core.tokens import TokenUsage, TokenWeights

    usage = TokenUsage(1000, 1000, 0, 0)
    chat = ChatBudget(ctx.modules, ctx.ops_table, ctx.clock, "max")
    chat.reserve("u-1", "turn-0001", 1000)
    assert chat.settle("u-1", "turn-0001", usage) == TokenWeights.for_tier("max").weigh(usage) > 6000
    assert chat.settle("u-1", "turn-0002", usage) == TokenWeights.for_tier("max").weigh(usage)  # no hold
    settings = dc_replace(ctx.settings, chat_model_key="opus-4-8")
    assert Settings.from_env({"AIR_CHAT_MODEL_KEY": "haiku-4-5"}).chat_model_key == "haiku-4-5"
    assert AppContext(settings, ctx.clock).chat_budget._weights == TokenWeights.for_tier(
        __import__("insights_radar.modules.catalog", fromlist=["ModelCatalog"]).ModelCatalog.get("opus-4-8").tier
    )


# ---- B3-030 / B3-031 / B3-032: products -----------------------------------------------------------


def test_b3_030_the_upload_sweep_resyncs_and_rows_outlive_it(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from insights_radar.product.service import PENDING_GRACE_SECONDS, UPLOAD_TTL_SECONDS

    _, p, _ = api.call("POST", "/api/products", {"name": "P"})
    _, up, _ = api.call("POST", f"/api/products/{p['id']}/documents", {"filename": "a.txt", "size": 5})
    row = ctx.ops_table.get_item(Key={"pk": f"PRODUCT#{p['id']}", "sk": f"DOC#{up['document']['id']}"})["Item"]
    cutoff = int(ctx.clock.now().timestamp()) + UPLOAD_TTL_SECONDS + PENDING_GRACE_SECONDS
    assert int(row["ttl"]) > cutoff
    calls: list[int] = []
    monkeypatch.setattr(ctx.knowledge, "start_ingestion", lambda: calls.append(1) or "J")
    ctx.clock.advance(timedelta(days=2))
    assert ctx.products.sweep_expired_uploads() == 1 and calls == [1]
    assert ctx.products.sweep_expired_uploads() == 0 and calls == [1]


def test_b3_031_a_resync_whose_crawl_invoke_fails_is_still_a_200(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.product.model import IngestionStatus

    _, p, _ = api.call("POST", "/api/products", {"name": "P", "doc_urls": ["https://docs.example.com/guide/"]})
    ctx.products._set_system_fields(p["id"], web_status=IngestionStatus.READY)

    def failing(pid: str) -> None:
        raise ClientError({"Error": {"Code": "ServiceException", "Message": "x"}}, "Invoke")

    monkeypatch.setattr(ctx.products, "_request_crawl", failing)
    status, _, _ = api.call("POST", f"/api/products/{p['id']}/sync")
    assert status == 200 and ctx.products.get(p["id"]).web_status.value == "pending"


def test_b3_032_a_stale_crawl_report_is_discarded(api: Client, ctx: TestContext) -> None:
    from insights_radar.product.crawler import CrawledPage, CrawlReport

    _, p, _ = api.call("POST", "/api/products", {"name": "P", "doc_urls": ["https://old.example.com/guide/"]})
    started_from = ctx.products.get(p["id"]).doc_urls
    stale = CrawlReport(pages=[CrawledPage("https://old.example.com/guide/x", "Old", "CONFIDENTIAL old page " * 20)])
    status, p2, _ = api.call(
        "PATCH", f"/api/products/{p['id']}", {"version": p["version"], "doc_urls": ["https://new.example.com/docs/"]}
    )
    assert status == 200, p2
    kept = ctx.products.store_crawl(p["id"], stale, started_from)
    keys = ctx.s3.list_objects_v2(Bucket="example-assets-111122223333-us-east-1", Prefix=f"docs/{p['id']}/web/").get(
        "Contents", []
    )
    assert kept.web_pages == 0 and keys == []


def test_b3_032_the_crawl_worker_passes_the_addresses_it_crawled(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    from insights_radar.api import workers
    from insights_radar.product.crawler import CrawlReport, DocsCrawler

    _, p, _ = api.call("POST", "/api/products", {"name": "P", "doc_urls": ["https://docs.example.com/guide/"]})
    monkeypatch.setattr(DocsCrawler, "crawl", lambda self, urls: CrawlReport())
    seen: list[Any] = []
    real = ctx.products.store_crawl
    monkeypatch.setattr(
        ctx.products,
        "store_crawl",
        lambda pid, report, crawled=None: seen.append(crawled) or real(pid, report, crawled),
    )
    workers.crawl({"product_id": p["id"]}, None, ctx)
    assert seen == [("https://docs.example.com/guide/",)]


# ---- B3-052: people's newest lists are ordered by creation -----------------------------------------


def test_b3_052_a_one_character_edit_does_not_refloat_an_old_output(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "signal")
    _, old = api.tool("kg_save_output", tok, {"title": "Old signal", "summary": "v1"})
    ctx.clock.advance(timedelta(minutes=5))
    api.tool("kg_save_output", tok, {"title": "New signal", "summary": "v1"})
    ctx.clock.advance(timedelta(minutes=5))
    api.tool("kg_update_output", tok, {"id": old["result"]["id"], "summary": "v1.", "expected_version": 1})
    _, page, _ = api.call("GET", "/api/insights/signals", role="viewer")
    assert [i["title"] for i in page["items"]] == ["New signal", "Old signal"]
    # kg_recent still resurfaces the edit for the modules downstream.
    _, recent = api.tool("kg_recent", _tok(ctx, "trend"), {"type": "signal", "after": "2026-10-02T08:06:00.000Z"})
    assert [i["title"] for i in recent["result"]["items"]] == ["Old signal"]
    t, u = old["result"]["id"].split("/")
    assert api.call("DELETE", f"/api/graph/nodes/{t}/{u}")[0] == 200
    _, page, _ = api.call("GET", "/api/insights/signals", role="viewer")
    assert [i["title"] for i in page["items"]] == ["New signal"]


def test_b3_052_outputs_written_before_the_listing_are_backfilled_by_the_seed(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    _, out = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "Legacy", "summary": "x"})
    node = ctx.graph.get(out["result"]["id"])
    ctx.graph._table.delete_item(Key=ctx.graph._listing_key(node))
    api.tool(
        "kg_add_evidence", _tok(ctx, "scout"), {"type": "fact", "title": "F", "evidence_url": "https://n.example.com/x"}
    )
    assert api.call("GET", "/api/insights/signals", role="viewer")[1]["items"] == []
    workers.seed({}, None, ctx)
    assert [i["title"] for i in api.call("GET", "/api/insights/signals", role="viewer")[1]["items"]] == ["Legacy"]


def test_b3_052_newest_pages_and_custom_kinds(api: Client, ctx: TestContext) -> None:
    from insights_radar.core.errors import ValidationError
    from insights_radar.graph.registry import NodeType

    _custom(api, "cx", ["kg_read", "kg_write_output"])
    tok = _tok(ctx, "cx")
    for i in range(3):
        api.tool("kg_save_output", tok, {"title": f"I{i}", "summary": "x"})
        ctx.clock.advance(timedelta(seconds=1))
    first = ctx.graph.newest(NodeType.INSIGHT, insight_kind="cx_k", limit=2)
    rest = ctx.graph.newest(NodeType.INSIGHT, insight_kind="cx_k", limit=2, cursor=first.cursor)
    assert [n.title for n in first.items + rest.items] == ["I2", "I1", "I0"]
    with pytest.raises(ValidationError):
        ctx.graph.newest(NodeType.INSIGHT)


# ---- B3-018: viewers do not read outputs derived from private documents -----------------------------


def test_b3_018_viewers_cannot_read_private_derived_outputs(api: Client, ctx: TestContext) -> None:
    attrs = {"request_title": "x", "problem": "y", "impact": "z"}
    _, gap = api.tool(
        "kg_save_output",
        _tok(ctx, "gap"),
        {"title": "SSO slips", "summary": "Roadmap: SSO moves to Q3", "attributes": attrs},
    )
    _custom(api, "pkx", PK)
    _, priv = api.tool("kg_save_output", _tok(ctx, "pkx"), {"title": "Private insight", "summary": "from notes"})
    _, pub = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "Public signal", "summary": "x", "supports": []})
    gid, pid, sid = gap["result"]["id"], priv["result"]["id"], pub["result"]["id"]
    for kind in ("gaps", "content", "drafts"):
        assert api.call("GET", f"/api/insights/{kind}", role="viewer")[0] == 403
    assert api.call("GET", "/api/insights/gaps", role="editor")[1]["items"][0]["id"] == gid
    _, own, _ = api.call("GET", "/api/insights/insight:pkx_k", role="viewer")
    assert own["items"] == []
    for node_id in (gid, pid):
        assert api.call("GET", f"/api/graph/nodes/{node_id}", role="viewer")[0] == 404
        assert api.call("GET", f"/api/insights/nodes/{node_id}", role="viewer")[0] == 404
        assert api.call("GET", f"/api/graph/nodes/{node_id}/neighbors", role="viewer")[0] == 404
        assert api.call("GET", f"/api/graph/nodes/{node_id}", role="editor")[0] == 200
    assert api.call("GET", f"/api/graph/nodes/{sid}", role="viewer")[0] == 200
    ctx.reindex()
    _, hits, _ = api.call("POST", "/api/graph/search", {"query": "SSO roadmap private insight"}, role="viewer")
    assert {h["node"]["id"] for h in hits["items"]} & {gid, pid} == set()
    _, all_hits, _ = api.call("POST", "/api/graph/search", {"query": "SSO roadmap private insight"}, role="editor")
    assert {h["node"]["id"] for h in all_hits["items"]} >= {gid, pid}
    _, ex, _ = api.call("GET", "/api/graph/explore", role="viewer")
    assert {n["id"] for n in ex["nodes"]} & {gid, pid} == set() and sid in {n["id"] for n in ex["nodes"]}


def test_b3_018_neighbours_and_detail_skip_private_nodes_for_viewers(api: Client, ctx: TestContext) -> None:
    attrs = {"request_title": "x", "problem": "y", "impact": "z"}
    _, sig = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "S", "summary": "x"})
    _, gap = api.tool(
        "kg_save_output",
        _tok(ctx, "gap"),
        {"title": "G", "summary": "p", "attributes": attrs, "addresses": [sig["result"]["id"]]},
    )
    sid, gid = sig["result"]["id"], gap["result"]["id"]
    _, hood, _ = api.call("GET", f"/api/graph/nodes/{sid}/neighbors", role="viewer")
    assert [n["id"] for n in hood["nodes"]] == [sid] and hood["edges"] == []
    _, detail, _ = api.call("GET", f"/api/insights/nodes/{sid}", role="viewer")
    assert detail["evidence"]["addressed_by"] == []
    _, detail, _ = api.call("GET", f"/api/insights/nodes/{sid}", role="editor")
    assert [e["node"]["id"] for e in detail["evidence"]["addressed_by"]] == [gid]


def test_b3_018_viewers_do_not_read_unstamped_nodes_of_private_writers(api: Client, ctx: TestContext) -> None:
    _custom(api, "pky", PK)
    _, out = api.tool("kg_save_output", _tok(ctx, "pky"), {"title": "Old", "summary": "from notes"})
    ctx.unstamp(out["result"]["id"])
    assert api.call("GET", f"/api/graph/nodes/{out['result']['id']}", role="viewer")[0] == 404


# ---- B3-061 / B3-068 / B3-069 / B3-071 / B3-072 / B3-075: graph and module moderation ----------------


def test_b3_061_an_admin_can_rewind_a_modules_progress(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "signal")
    api.tool("progress_update", tok, {"last_processed_at": "2026-10-02T07:00:00.000Z"})
    # While that run is live its next progress update would overwrite a reset: the reset waits for it.
    status, body, _ = api.call("PUT", "/api/modules/signal/progress", {"last_processed_at": "2026-09-01T00:00:00Z"})
    assert status == 409 and "run in progress" in body["message"]
    _end_run(ctx, "signal")
    status, body, _ = api.call("PUT", "/api/modules/signal/progress", {"last_processed_at": "2026-09-01T00:00:00Z"})
    assert status == 200 and body["last_processed_at"] == "2026-09-01T00:00:00.000Z"
    assert ctx.modules.progress("signal").last_processed_at == "2026-09-01T00:00:00.000Z"
    row = _rows(ctx, "module.progress_reset")[0]["details"]
    assert row == {"before": "2026-10-02T07:00:00.000Z", "after": "2026-09-01T00:00:00.000Z"}
    status, body, _ = api.call("PUT", "/api/modules/signal/progress", {"last_processed_at": None})
    assert status == 200 and body["last_processed_at"] is None
    assert api.call("PUT", "/api/modules/signal/progress", {"last_processed_at": "soon"})[0] == 400
    assert api.call("PUT", "/api/modules/signal/progress", {"last_processed_at": None}, role="editor")[0] == 403
    assert api.call("PUT", "/api/modules/nope/progress", {"last_processed_at": None})[0] == 404


def test_a_reading_position_cannot_be_set_in_the_future_or_out_of_range(api: Client, ctx: TestContext) -> None:
    # A future cursor would make the module read nothing until that date, and runs never move it back.
    status, body, _ = api.call("PUT", "/api/modules/signal/progress", {"last_processed_at": "2099-01-01T00:00:00Z"})
    assert status == 400 and "future" in body["message"]
    # Year 1 with an offset overflows on conversion to UTC: a 400, not a 500.
    status, _, _ = api.call("PUT", "/api/modules/signal/progress", {"last_processed_at": "0001-01-01T00:00:00+01:00"})
    assert status == 400
    assert ctx.modules.progress("signal").last_processed_at is None


def test_b3_068_a_person_can_remove_one_edge(api: Client, ctx: TestContext) -> None:
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    tok = _tok(ctx, "scout")
    _, own = api.tool("kg_upsert_entity", tok, {"type": "person", "name": "John Roe"})
    rival = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Rival Inc"), "user:u-admin")[0]
    api.tool("kg_link", tok, {"type": "affiliated_with", "from": own["result"]["id"], "to": rival.id})
    path = f"/api/graph/edges/{own['result']['id']}/affiliated_with/{rival.id}"
    assert api.call("DELETE", path, role="viewer")[0] == 403
    assert api.call("DELETE", path)[0] == 200
    assert ctx.graph.get(rival.id).in_degree == 0 and _rows(ctx, "graph.unlink")[0]["details"]["to"] == rival.id
    assert api.call("DELETE", path)[0] == 404
    assert api.call("DELETE", f"/api/graph/edges/{own['result']['id']}/nope/{rival.id}")[0] == 400


def test_b3_072_a_person_can_correct_a_node(api: Client, ctx: TestContext) -> None:
    _, out = api.tool("kg_save_output", _tok(ctx, "signal"), {"title": "Wrong", "summary": "injected"})
    nid = out["result"]["id"]
    status, body, _ = api.call("PATCH", f"/api/graph/nodes/{nid}", {"summary": "corrected", "expected_version": 1})
    assert status == 200 and body["summary"] == "corrected" and body["version"] == 2
    details = _rows(ctx, "graph.correct")[0]["details"]
    assert details["fields"] == ["summary"] and details["module"] == "signal" and "summary_sha256" in details
    assert api.call("PATCH", f"/api/graph/nodes/{nid}", {"summary": "again", "expected_version": 1})[0] == 409
    assert api.call("PATCH", f"/api/graph/nodes/{nid}", {"expected_version": 2})[0] == 400
    assert (
        api.call("PATCH", f"/api/graph/nodes/{nid}", {"summary": "x", "expected_version": 2}, role="viewer")[0] == 403
    )


def test_b3_069_a_node_with_too_many_links_is_deleted_by_the_sweep(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    from insights_radar.api import workers
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    monkeypatch.setattr(moderation, "MAX_DELETE_EDGES", 3)
    from insights_radar.api import routes

    monkeypatch.setattr(routes, "MAX_DELETE_EDGES", 3)
    hub = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Octank"), "user:u-admin")[0]
    for i in range(7):
        fact = ctx.graph.create(
            Node(id="", type=NodeType.FACT, title=f"F{i}", evidence_url=f"https://a.example/{i}"), "user:u-admin"
        )
        ctx.graph.link(fact.id, EdgeType.ABOUT, hub.id, "user:u-admin")
    status, body, _ = api.call("DELETE", f"/api/graph/nodes/{hub.id}")
    assert status == 202 and body == {"queued": True, "edges": 7}
    out = workers.sweep({}, None, ctx)
    assert out["nodes_deleted"] == 0 and ctx.graph.get(hub.id).degree == 4
    out = workers.sweep({}, None, ctx)
    assert out["nodes_deleted"] == 1
    from insights_radar.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        ctx.graph.get(hub.id)
    assert _rows(ctx, "graph.delete")[0]["details"]["queued"] is True
    assert workers.sweep({}, None, ctx)["nodes_deleted"] == 0


def test_b3_069_a_queued_node_deleted_meanwhile_leaves_the_queue(api: Client, ctx: TestContext) -> None:
    from insights_radar.graph.model import Node
    from insights_radar.graph.moderation import NodeRemoval
    from insights_radar.graph.registry import NodeType

    hub = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Octank"), "user:u-admin")[0]
    removal = NodeRemoval.of(ctx)
    removal.queue(hub, "user:u-admin")
    ctx.graph.delete(hub.id)
    assert removal.sweep() == 0
    assert ctx.ops_table.get_item(Key={"pk": "GRAPHDELETE", "sk": hub.id}).get("Item") is None


def test_b3_069_a_queued_idea_takes_its_drafts_with_it(ctx: TestContext, monkeypatch: Any) -> None:
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    monkeypatch.setattr(moderation, "MAX_DELETE_EDGES", 2)
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Idea",
            module="content",
            attributes={"content_type": "b", "audience": "a"},
        ),
        "module:content",
    )
    for i in range(5):
        d = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title=f"D{i}", module="draft"), "module:draft")
        ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, d.id, "module:draft")
    removal = moderation.NodeRemoval.of(ctx)
    removal.queue(ctx.graph.get(idea.id), "user:u-admin")
    for _ in range(3):
        removal.sweep()
    assert (
        list(ctx.graph.iterate(NodeType.CONTENT_DRAFT)) == [] and list(ctx.graph.iterate(NodeType.CONTENT_IDEA)) == []
    )


def test_a_queued_idea_with_many_other_links_is_pruned_after_its_drafts(ctx: TestContext, monkeypatch: Any) -> None:
    # Code review r53: an idea over the cap whose links were not drafts was skipped by every sweep.
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    monkeypatch.setattr(moderation, "MAX_DELETE_EDGES", 2)
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Idea",
            module="content",
            attributes={"content_type": "b", "audience": "a"},
        ),
        "module:content",
    )
    for i in range(5):
        topic = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title=f"T{i}"), "u")[0]
        ctx.graph.link(idea.id, EdgeType.ABOUT, topic.id, "module:content")
    removal = moderation.NodeRemoval.of(ctx)
    removal.queue(ctx.graph.get(idea.id), "user:u-admin")
    for _ in range(4):
        removal.sweep()
    assert list(ctx.graph.iterate(NodeType.CONTENT_IDEA)) == []


def test_b3_069_a_run_links_one_entity_a_bounded_number_of_times(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.api import agent_tools
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    monkeypatch.setattr(agent_tools, "MAX_LINKS_TO_ONE_ENTITY_PER_RUN", 2)
    ent = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Octank"), "user:u-admin")[0]
    tok = _tok(ctx, "scout")
    codes = [
        api.tool(
            "kg_add_evidence",
            tok,
            {"type": "fact", "title": f"F{i}", "evidence_url": f"https://a.example/{i}", "about": [ent.id]},
        )[0]
        for i in range(3)
    ]
    assert codes == [200, 200, 403] and ctx.graph.get(ent.id).in_degree == 2
    real = ctx.ops_table.update_item

    def throttled(**kwargs: Any) -> Any:
        if str(kwargs.get("Key", {}).get("pk", "")).startswith("LINKS#"):
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "UpdateItem")
        return real(**kwargs)

    monkeypatch.setattr(ctx.ops_table, "update_item", throttled)
    with pytest.raises(ClientError):
        api.app.tools.call(
            "kg_add_evidence",
            _tok(ctx, "scout", "run-2"),
            {"type": "fact", "title": "G", "evidence_url": "https://a.example/g", "about": [ent.id]},
            CALLER_ARN,
        )


def test_b3_071_admins_can_list_a_deleted_modules_insights(api: Client, ctx: TestContext) -> None:
    _custom(api, "old", ["kg_read", "kg_write_output"])
    _, out = api.tool("kg_save_output", _tok(ctx, "old"), {"title": "Orphan insight", "summary": "x"})
    api.call("DELETE", "/api/modules/old")
    assert api.call("GET", "/api/insights/insight:old_k", role="viewer")[0] == 404
    _, page, _ = api.call("GET", "/api/insights/insight:old_k")
    assert [i["id"] for i in page["items"]] == [out["result"]["id"]]
    _, ex, _ = api.call("GET", "/api/graph/explore", query={"types": "insight"})
    assert out["result"]["id"] in {n["id"] for n in ex["nodes"]}


def test_b3_075_deleting_an_idea_removes_every_draft(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    from insights_radar.graph import repository
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    monkeypatch.setattr(repository, "MAX_EDGES_PER_HOP", 3)  # the default edge page, made small
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Idea",
            module="content",
            attributes={"content_type": "b", "audience": "a"},
        ),
        "module:content",
    )
    for i in range(6):
        d = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title=f"D{i}", module="draft"), "module:draft")
        ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, d.id, "module:draft")
    status, _, _ = api.call("DELETE", f"/api/graph/nodes/{idea.id}")
    assert status == 200 and list(ctx.graph.iterate(NodeType.CONTENT_DRAFT)) == []


def test_b3_069_a_queued_node_already_small_enough_is_deleted_at_once(ctx: TestContext) -> None:
    from insights_radar.graph.model import Node
    from insights_radar.graph.moderation import NodeRemoval
    from insights_radar.graph.registry import NodeType

    hub = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Octank"), "user:u-admin")[0]
    removal = NodeRemoval.of(ctx)
    removal.queue(hub, "user:u-admin")
    assert removal.sweep() == 1


def test_b3_052_the_listing_backfill_pages(api: Client, ctx: TestContext) -> None:
    tok = _tok(ctx, "signal")
    for i in range(3):
        api.tool("kg_save_output", tok, {"title": f"S{i}", "summary": "x"})
    assert ctx.graph.index_created(page_size=1) == 3


def test_sr3_01_the_split_raises_a_write_error_that_is_not_a_name_already_copied(
    ctx: TestContext, monkeypatch: Any
) -> None:
    from botocore.exceptions import ClientError

    ctx.ops_table.put_item(Item={"pk": "MODULES", "sk": "TOMBSTONE", "slugs": {"x"}})
    table = ctx.modules._t

    def refuse(**_: Any) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

    monkeypatch.setattr(table, "put_item", refuse)
    with pytest.raises(ClientError):
        ctx.modules.split_tombstone()


# ---- port of CTR H-024 (00c5bddb): a discarded suggestion is not proposed again ------------------------


def test_h024_a_discarded_new_entry_is_refused_for_30_days_then_allowed(api: Client, ctx: TestContext) -> None:
    from datetime import timedelta

    from insights_radar.core.clock import FixedClock

    _assist(api, "h24")
    key = _kafka(api, ctx, "h24")
    assert api.call("DELETE", f"/api/radars/h24/suggestions/{key}")[0] == 200
    body = {
        "slug": "h24",
        "proposal": {"title": "Kafka", "sector": "General", "ring": "Now"},
        "reason": "ten chars plus",
    }
    status, out = api.tool("radar_suggest", _tok(ctx, "curator"), body)
    assert status == 409 and out["details"]["code"] == "recently_discarded", out
    # A different change for the same title is a new question for the reviewer.
    other = {**body, "proposal": {**body["proposal"], "ring": "Later"}}
    assert api.tool("radar_suggest", _tok(ctx, "curator"), other)[0] == 200
    assert isinstance(ctx.clock, FixedClock)
    ctx.clock.advance(timedelta(days=31))  # the memory lapses (DynamoDB's TTL delete is not immediate)
    (open_one,) = api.call("GET", "/api/radars/h24/suggestions")[1]["items"]
    assert api.call("DELETE", f"/api/radars/h24/suggestions/{open_one['entry_key']}")[0] == 200
    assert api.tool("radar_suggest", _tok(ctx, "curator"), body)[0] == 200


def test_h024_a_discarded_edit_is_remembered_by_its_entry(api: Client, ctx: TestContext) -> None:
    _assist(api, "h24e")
    meta = api.call("GET", "/api/radars/h24e")[1]["radar"]
    entries = [{"key": "k1", "title": "Kafka", "sector": "General", "ring": "Now"}]
    body = {"entries": entries, "expected_version": meta["current_version"]}
    saved = api.call("PUT", "/api/radars/h24e/entries", body)
    assert saved[0] == 200, saved[1]
    entry_key = api.call("GET", "/api/radars/h24e")[1]["entries"][0]["key"]
    edit = {"slug": "h24e", "entry_key": entry_key, "proposal": {"ring": "Later"}, "reason": "ten chars plus"}
    status, sug = api.tool("radar_suggest", _tok(ctx, "curator"), edit)
    assert status == 200, sug
    assert api.call("DELETE", f"/api/radars/h24e/suggestions/{entry_key}")[0] == 200
    status, out = api.tool("radar_suggest", _tok(ctx, "curator"), edit)
    assert status == 409 and out["details"]["code"] == "recently_discarded"


# ---- port of CTR e27239c5: a schema body with an unknown key is refused, not half-saved ----------------


def test_e27239c5_a_misspelled_schema_key_is_a_400_on_create_and_on_save(api: Client, ctx: TestContext) -> None:
    bad = {"sectors": ["A"], "rings": ["Now"], "marker": ["New"]}  # "marker" for "markers"
    status, body, _ = api.call("POST", "/api/radars", {"slug": "skey", "name": "S", "schema": bad})
    assert status == 400 and body["details"]["unknown"] == ["marker"], body
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "skey", "name": "S", "template": "blank"})
    assert status == 201
    good = {**radar["schema"], "rings": [{"name": "Now", "colour": "#fff"}]}
    status, body, _ = api.call(
        "PUT", "/api/radars/skey/schema", {"schema": good, "expected_version": radar["current_version"]}
    )
    assert status == 400 and body["details"]["unknown"] == ["rings[].colour"], body


def test_e27239c5_a_stored_schema_with_an_extra_key_still_loads() -> None:
    from insights_radar.radars.model import RadarSchema

    schema = RadarSchema.from_public({"sectors": ["A"], "rings": ["Now"], "legacy": True})
    assert [d.name for d in schema.sectors] == ["A"]


def test_hcb037_an_editor_address_with_no_account_needs_a_confirmation(api: Client, ctx: TestContext) -> None:
    from insights_radar.identity.principal import Role

    api.call("POST", "/api/radars", {"slug": "acl", "name": "Acl"}, role="editor")
    lock = api.call("GET", "/api/radars/acl")[1]["radar"]["version_lock"]
    ctx.users.invite("Real.Person@example.com", Role.EDITOR)
    body: dict[str, Any] = {"editors": ["real.person@example.com", "rael.person@example.com"], "version_lock": lock}
    # The owner is no administrator and cannot read the user list: the typo is named, nothing is saved.
    s, err, _ = api.call("PUT", "/api/radars/acl/access", body, role="editor")
    assert s == 400 and err["details"] == {"code": "unknown_editors", "unknown": ["rael.person@example.com"]}
    assert api.call("GET", "/api/radars/acl")[1]["radar"]["editors"] == []
    # Only a non-owner editor is refused before any lookup: a probe for accounts is a 403, not the list.
    assert api.call("PUT", "/api/radars/acl/access", body, role="viewer")[0] == 403
    s, r, _ = api.call("PUT", "/api/radars/acl/access", {**body, "confirm_unknown": True}, role="editor")
    assert s == 200 and sorted(r["editors"]) == sorted(body["editors"])
    # An editor already on the list is not looked up again.
    s, _, _ = api.call(
        "PUT", "/api/radars/acl/access", {"editors": body["editors"], "version_lock": r["version_lock"]}, role="editor"
    )
    assert s == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/radars", {"slug": "u1", "name": "U", "purpos": "typo"}),
        ("PUT", "/api/radars/u/entries", {"entries": [], "expected_version": 1, "allowEmpty": True}),
        ("POST", "/api/radars/u/import", {"format": "csv", "text": "title\nA\n", "dryRun": True}),
        ("POST", "/api/radars/u/rollback", {"version": 1, "expected_version": 1, "force": True}),
        ("PUT", "/api/radars/u/schema", {"schema": {}, "expected_version": 1, "rename": {}}),
        ("PUT", "/api/radars/u/access", {"editors": [], "version_lock": 1, "owners": []}),
        ("POST", "/api/radars/u/suggestions/k/accept", {"edit": {"ring": "Now"}}),
        ("POST", "/api/modules/m/run", {"target": "x"}),
        ("POST", "/api/graph/search", {"query": "kafka", "type": ["topic"]}),
    ],
)
def test_h016_a_misspelt_body_key_is_a_400_naming_it(api: Client, method: str, path: str, body: dict[str, Any]) -> None:
    api.call("POST", "/api/radars", {"slug": "u", "name": "U"})
    status, err, _ = api.call(method, path, body)
    assert status == 400 and err["message"].startswith("unknown fields:"), err


def _audit(ctx: TestContext) -> list[dict[str, Any]]:
    return ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 1000)[0]


def _three_open(api: Client, ctx: TestContext, slug: str) -> list[str]:
    api.call("POST", "/api/radars", {"slug": slug, "name": "B", "template": "competitor_landscape"}, role="editor")
    lock = ctx.radars.get(slug).version_lock
    api.call("PATCH", f"/api/radars/{slug}", {"version_lock": lock, "mode": "assist"}, role="editor")
    return [
        ctx.radars.propose(
            slug, "module:curator", "run", {"title": t, "sector": "Direct", "ring": "Leader"}, "Two analyst notes"
        )["entry_key"]
        for t in ("Alpha", "Beta", "Gamma")
    ]


def test_h019_accepting_several_suggestions_makes_one_version(api: Client, ctx: TestContext) -> None:
    keys = _three_open(api, ctx, "batch")
    before = ctx.radars.get("batch").current_version
    s, r, _ = api.call("POST", "/api/radars/batch/suggestions/accept", {"keys": keys}, role="editor")
    assert s == 200 and r["current_version"] == before + 1 and r["entry_count"] == 3
    assert api.call("GET", "/api/radars/batch/suggestions", role="viewer")[1]["items"] == []
    rows = [a for a in _audit(ctx) if a["action"] == "suggestion.accept"]
    assert len(rows) == 3 and {row["details"]["batch"] for row in rows} == {3}


def test_h019_one_bad_key_refuses_the_batch_and_keeps_every_proposal(api: Client, ctx: TestContext) -> None:
    keys = _three_open(api, ctx, "batch2")
    for body, status in (
        ({"keys": [*keys, "nope"]}, 404),
        ({"keys": [keys[0], keys[0]]}, 400),
        ({"keys": []}, 400),
        ({"keys": keys, "edits": {}}, 400),
    ):
        assert api.call("POST", "/api/radars/batch2/suggestions/accept", body, role="editor")[0] == status
    assert len(api.call("GET", "/api/radars/batch2/suggestions", role="viewer")[1]["items"]) == 3
    assert api.call("POST", "/api/radars/batch2/suggestions/accept", {"keys": keys}, role="viewer")[0] == 403


def test_ha033_the_dry_run_refuses_repeated_keys_as_the_import_does(api: Client) -> None:
    api.call("POST", "/api/radars", {"slug": "dk", "name": "Dk"}, role="editor")
    text = "key,title,sector,ring\nk1,Alpha,General,Now\nk1,Beta,General,Now\n"
    s, dry, _ = api.call(
        "POST", "/api/radars/dk/import", {"format": "csv", "text": text, "dry_run": True}, role="editor"
    )
    assert s == 200 and dry["accepted"] is False and dry["duplicate_keys"] == ["k1"]
    version = api.call("GET", "/api/radars/dk")[1]["radar"]["current_version"]
    body = {"format": "csv", "text": text, "expected_version": version}
    s, err, _ = api.call("POST", "/api/radars/dk/import", body, role="editor")
    assert s == 400 and err["details"] == {"code": "duplicate_key", "keys": ["k1"]}


def test_r54_kg_link_never_makes_a_has_draft_link(api: Client, ctx: TestContext) -> None:
    # Security review r54: an idea's has_draft links decide which drafts its delete takes with it, so a run that
    # hung one from its own idea to another idea's draft could have that draft deleted.
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    draft = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="Theirs", module="draft"), "module:draft")
    tok = _tok(ctx, "content")
    status, body = api.tool("kg_link", tok, {"type": "has_draft", "from": "content_idea/x", "to": draft.id})
    assert status == 403 and "draft_save" in str(body)
    assert ctx.graph.get(draft.id).in_degree == 0


def test_a_dangling_draft_link_does_not_stop_the_sweep(ctx: TestContext, monkeypatch: Any) -> None:
    # Code review r54: one HAS_DRAFT edge to a missing draft raised and aborted the whole sweep; and every draft
    # removed ahead of its idea is audited.
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    monkeypatch.setattr(moderation, "MAX_DELETE_EDGES", 1)
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Idea",
            module="content",
            attributes={"content_type": "b", "audience": "a"},
        ),
        "module:content",
    )
    drafts = []
    for i in range(3):
        d = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title=f"D{i}", module="draft"), "module:draft")
        ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, d.id, "module:draft")
        drafts.append(d)
    ctx.graph._table.delete_item(Key={"pk": f"N#{drafts[0].id}", "sk": "META"})  # the draft vanished, its link did not
    removal = moderation.NodeRemoval.of(ctx)
    removal.queue(ctx.graph.get(idea.id), "user:u-admin")
    for _ in range(5):
        removal.sweep(ctx.audit)
    assert (
        list(ctx.graph.iterate(NodeType.CONTENT_IDEA)) == [] and list(ctx.graph.iterate(NodeType.CONTENT_DRAFT)) == []
    )
    rows = ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 100)[0]
    deleted = [r for r in rows if r["action"] == "graph.delete"]
    # Drafts removed ahead of their idea have a row each; those still attached go with the idea's own row.
    by_id = {r["target"] for r in deleted} & {drafts[1].id, drafts[2].id}
    idea_row = next(r for r in deleted if r["target"] == idea.id)
    assert by_id and len(by_id) + idea_row["details"]["drafts_removed"] == 2


def test_removing_an_idea_skips_a_draft_that_is_already_gone(ctx: TestContext) -> None:
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Idea",
            module="content",
            attributes={"content_type": "b", "audience": "a"},
        ),
        "module:content",
    )
    ghost = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="Gone", module="draft"), "module:draft")
    real = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="Here", module="draft"), "module:draft")
    for d in (ghost, real):
        ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, d.id, "module:draft")
    ctx.graph._table.delete_item(Key={"pk": f"N#{ghost.id}", "sk": "META"})
    removal = moderation.NodeRemoval.of(ctx)
    purged: list[str] = []
    monkeypatch_purge = removal._insights.purge
    removal._insights.purge = lambda node_id: (purged.append(node_id), monkeypatch_purge(node_id))[1]  # type: ignore[method-assign]
    result = removal.remove(ctx.graph.get(idea.id))
    assert result.drafts_removed == 1 and list(ctx.graph.iterate(NodeType.CONTENT_DRAFT)) == []
    # The gone draft's votes and markdown are removed too (code review r55).
    assert ghost.id in purged


def test_r55_kg_schema_does_not_offer_has_draft(api: Client, ctx: TestContext) -> None:
    status, body = api.tool("kg_schema", _tok(ctx, "content"))
    assert status == 200 and "has_draft" not in body["result"]["edge_types"] and "about" in body["result"]["edge_types"]


def test_a_failed_cleanup_of_a_gone_draft_does_not_stop_the_sweep(
    ctx: TestContext, monkeypatch: Any, caplog: Any
) -> None:
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    monkeypatch.setattr(moderation, "MAX_DELETE_EDGES", 1)
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Idea",
            module="content",
            attributes={"content_type": "b", "audience": "a"},
        ),
        "module:content",
    )
    ghost = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="G", module="draft"), "module:draft")
    ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, ghost.id, "module:draft")
    other = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="T"), "u")[0]
    ctx.graph.link(idea.id, EdgeType.ABOUT, other.id, "module:content")  # two links: over the cap of 1
    ctx.graph._table.delete_item(Key={"pk": f"N#{ghost.id}", "sk": "META"})
    removal = moderation.NodeRemoval.of(ctx)
    monkeypatch.setattr(removal, "_forget_draft", lambda draft_id: (_ for _ in ()).throw(RuntimeError("s3 down")))
    removal.queue(ctx.graph.get(idea.id), "user:u-admin")
    removal.sweep()
    assert "could not forget gone draft" in caplog.text
    # The link stays, so the next sweep tries the cleanup again (code review r57).
    assert [e.target for e in ctx.graph.edges(idea.id, direction="out", edge=EdgeType.HAS_DRAFT)] == [ghost.id]
    # Once the cleanup works, the next sweep forgets the draft and drops the dangling link.
    forgotten: list[str] = []
    monkeypatch.setattr(removal, "_forget_draft", forgotten.append)
    removal.sweep()
    assert forgotten == [ghost.id]
    assert list(ctx.graph.edges(idea.id, direction="out", edge=EdgeType.HAS_DRAFT)) == []


def test_r57_drafts_go_before_their_idea(ctx: TestContext, monkeypatch: Any) -> None:
    # Code review r57: the idea went first, so a failure part way left its other drafts behind for good.
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Idea",
            module="content",
            attributes={"content_type": "b", "audience": "a"},
        ),
        "module:content",
    )
    d = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="D", module="draft"), "module:draft")
    ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, d.id, "module:draft")
    removal = moderation.NodeRemoval.of(ctx)
    monkeypatch.setattr(removal, "_forget_draft", lambda draft_id: (_ for _ in ()).throw(RuntimeError("s3 down")))
    with pytest.raises(RuntimeError):
        removal.remove(ctx.graph.get(idea.id))
    assert ctx.graph.get(idea.id).id == idea.id and ctx.graph.get(d.id).id == d.id  # nothing lost: ask again


def _big_idea(ctx: TestContext, monkeypatch: Any) -> Any:
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    monkeypatch.setattr(moderation, "MAX_DELETE_EDGES", 1)
    return ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Idea",
            module="content",
            attributes={"content_type": "b", "audience": "a"},
        ),
        "module:content",
    )


def test_r58_a_gone_draft_that_cannot_be_cleaned_does_not_keep_its_idea_large(
    ctx: TestContext, monkeypatch: Any
) -> None:
    # Code review r58: the kept link meant prune never ran, so the idea stayed over the cap and was never deleted.
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    idea = _big_idea(ctx, monkeypatch)
    ghost = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="G", module="draft"), "module:draft")
    ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, ghost.id, "module:draft")
    for t in ("T1", "T2"):
        other = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title=t), "u")[0]
        ctx.graph.link(idea.id, EdgeType.ABOUT, other.id, "module:content")
    ctx.graph._table.delete_item(Key={"pk": f"N#{ghost.id}", "sk": "META"})
    removal = moderation.NodeRemoval.of(ctx)
    monkeypatch.setattr(removal, "_forget_draft", lambda draft_id: (_ for _ in ()).throw(RuntimeError("s3 down")))
    monkeypatch.setattr(moderation, "MAX_DELETE_EDGES", 2)  # room for the draft link and a batch of others
    removal.queue(ctx.graph.get(idea.id), "user:u-admin")
    removal.sweep()  # both ABOUT links go, the draft link stays
    edges = list(ctx.graph.edges(idea.id))
    assert [(e.type, e.target) for e in edges] == [(EdgeType.HAS_DRAFT, ghost.id)]


def test_r60_a_full_page_of_stuck_drafts_prunes_nothing(ctx: TestContext, monkeypatch: Any, caplog: Any) -> None:
    # Code review r60: past a full page of draft links, the next ones may be live drafts, which a prune would orphan.
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    idea = _big_idea(ctx, monkeypatch)  # the cap is 1, so one stuck draft is a full page
    ghost = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="G", module="draft"), "module:draft")
    ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, ghost.id, "module:draft")
    other = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="T1"), "u")[0]
    ctx.graph.link(idea.id, EdgeType.ABOUT, other.id, "module:content")
    ctx.graph._table.delete_item(Key={"pk": f"N#{ghost.id}", "sk": "META"})
    removal = moderation.NodeRemoval.of(ctx)
    monkeypatch.setattr(removal, "_forget_draft", lambda draft_id: (_ for _ in ()).throw(RuntimeError("s3 down")))
    removal.queue(ctx.graph.get(idea.id), "user:u-admin")
    removal.sweep()
    assert ctx.graph.get(idea.id).degree == 2
    assert "gone drafts cannot be cleaned up" in caplog.text


def test_r58_one_node_that_cannot_be_swept_does_not_stop_the_rest(
    ctx: TestContext, monkeypatch: Any, caplog: Any
) -> None:
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    first = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="First"), "u")[0]
    second = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Second"), "u")[0]
    removal = moderation.NodeRemoval.of(ctx)
    for n in (first, second):
        removal.queue(n, "user:u-admin")
    real = removal.remove
    monkeypatch.setattr(
        removal, "remove", lambda node: (_ for _ in ()).throw(RuntimeError("x")) if node.id == first.id else real(node)
    )
    assert removal.sweep() == 1
    assert "could not sweep queued node" in caplog.text
    assert ctx.graph.get(first.id).id == first.id  # still there and still queued
    with pytest.raises(Exception):
        ctx.graph.get(second.id)


def test_r58_a_node_is_cleaned_up_before_it_is_deleted(ctx: TestContext, monkeypatch: Any) -> None:
    # Code review r58: purge after the delete meant a failure left votes and emails with nothing to retry.
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    draft = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="D", module="draft"), "module:draft")
    removal = moderation.NodeRemoval.of(ctx)
    monkeypatch.setattr(removal._insights, "purge", lambda node_id: (_ for _ in ()).throw(RuntimeError("throttled")))
    with pytest.raises(RuntimeError):
        removal.remove(ctx.graph.get(draft.id))
    assert ctx.graph.get(draft.id).id == draft.id  # still there, so the delete can be asked again


def test_r58_a_gone_draft_of_a_large_idea_is_forgotten_and_unlinked(ctx: TestContext, monkeypatch: Any) -> None:
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    idea = _big_idea(ctx, monkeypatch)
    ghost = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="G", module="draft"), "module:draft")
    ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, ghost.id, "module:draft")
    other = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="T"), "u")[0]
    ctx.graph.link(idea.id, EdgeType.ABOUT, other.id, "module:content")
    ctx.graph._table.delete_item(Key={"pk": f"N#{ghost.id}", "sk": "META"})
    removal = moderation.NodeRemoval.of(ctx)
    forgotten: list[str] = []
    monkeypatch.setattr(removal, "_forget_draft", forgotten.append)
    removal.queue(ctx.graph.get(idea.id), "user:u-admin")
    removal.sweep()
    assert forgotten[0] == ghost.id
    assert not list(ctx.graph.edges(idea.id, direction="out", edge=EdgeType.HAS_DRAFT))


def test_r59_a_node_is_purged_once_before_its_delete(ctx: TestContext, monkeypatch: Any) -> None:
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    node = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Purged"), "u")[0]
    removal = moderation.NodeRemoval.of(ctx)
    purged: list[str] = []
    monkeypatch.setattr(removal._insights, "purge", purged.append)
    removal.remove(ctx.graph.get(node.id))
    assert purged == [node.id]  # once, before the delete: later writes need the node to exist (code review r60)


def test_r59_a_failed_audit_leaves_the_delete_queued_and_audits_it_next_time(ctx: TestContext) -> None:
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    node = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Audited"), "u")[0]
    removal = moderation.NodeRemoval.of(ctx)
    removal.queue(node, "user:u-admin")
    records: list[tuple[Any, ...]] = []

    class Flaky:
        fail = True

        def record_actor(self, *args: Any) -> None:
            if self.fail:
                raise RuntimeError("throttled")
            records.append(args)

    audit = Flaky()
    assert removal.sweep(audit) == 0  # removed, but not audited: still queued
    audit.fail = False
    removal.sweep(audit)
    # Code review r61: the deletion is audited as such, with what it removed, not as a dequeue.
    assert [(r[2], r[3]) for r in records] == [("graph.delete", node.id)]
    assert records[0][4]["edges_removed"] == 0
    assert records[0][4]["title"] == "Audited"
    assert not list(
        removal._ops.query(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": moderation.QUEUE_PK})[
            "Items"
        ]
    )


def test_r60_a_queued_node_already_gone_is_cleaned_up(ctx: TestContext, monkeypatch: Any) -> None:
    # Code review r60: the gone path dequeued without purging, so what a failed earlier sweep left stayed for good.
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    topic = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Gone Topic"), "u")[0]
    draft = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="Gone", module="draft"), "module:draft")
    removal = moderation.NodeRemoval.of(ctx)
    for n in (topic, draft):
        removal.queue(n, "user:u-admin")
        ctx.graph.delete(n.id)
    purged: list[str] = []
    forgotten: list[str] = []
    monkeypatch.setattr(removal._insights, "purge", purged.append)
    monkeypatch.setattr(removal, "_forget_draft", forgotten.append)
    removal.sweep()
    assert purged == [topic.id] and forgotten == [draft.id]


def test_r60_a_vote_or_mark_on_a_node_deleted_meanwhile_is_refused(ctx: TestContext) -> None:
    # Code review r60: the node-exists read and the write were separate, so a vote or mark (with an email) written
    # just after the delete outlived the node. The write is now conditional on the node.
    from insights_radar.core.errors import NotFoundError
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType
    from insights_radar.identity.principal import Principal, PrincipalKind, Role

    user = Principal(PrincipalKind.USER, "u-late", Role.EDITOR, "late@example.com")
    signal = ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="S", module="signal"), "module:signal")
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            module="content",
            attributes={"content_type": "b", "audience": "a"},
        ),
        "module:content",
    )
    real_get = ctx.graph.get
    stale = {n.id: real_get(n.id) for n in (signal, idea)}
    for n in (signal, idea):
        ctx.graph.delete(n.id)
    ctx.graph.get = lambda node_id: stale[node_id]  # type: ignore[method-assign]
    try:
        with pytest.raises(NotFoundError):
            ctx.insights.give_feedback(signal.id, user, "up", None)
        with pytest.raises(NotFoundError):
            ctx.insights.set_working(idea.id, user, True)
    finally:
        ctx.graph.get = real_get  # type: ignore[method-assign]
    assert ctx.insights.purge(signal.id) == 0 and ctx.insights.purge(idea.id) == 0


def test_r61_a_queued_idea_takes_no_new_draft(ctx: TestContext, monkeypatch: Any) -> None:
    # Code review r61, r63: a draft linked while its idea was being taken apart lost its idea. The idea is flagged
    # when it is queued, so the link is refused instead, and the prune of an idea with no drafts still keeps any
    # HAS_DRAFT link.
    from insights_radar.core.errors import NotFoundError
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    idea = _big_idea(ctx, monkeypatch)
    for t in ("T1", "T2"):
        other = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title=t), "u")[0]
        ctx.graph.link(idea.id, EdgeType.ABOUT, other.id, "module:content")
    draft = ctx.graph.create(Node(id="", type=NodeType.CONTENT_DRAFT, title="New", module="draft"), "module:draft")
    removal = moderation.NodeRemoval.of(ctx)
    removal.queue(ctx.graph.get(idea.id), "user:u-admin")
    with pytest.raises(NotFoundError):
        ctx.graph.link(idea.id, EdgeType.HAS_DRAFT, draft.id, "module:draft")
    assert ctx.graph.prune(idea.id, 5, keep=EdgeType.HAS_DRAFT) == 2


def test_r62_a_retried_audit_has_number_counts(ctx: TestContext) -> None:
    from decimal import Decimal

    from insights_radar.graph import moderation

    removal = moderation.NodeRemoval.of(ctx)
    removal._ops.put_item(
        Item={
            "pk": moderation.QUEUE_PK,
            "sk": "topic/00000000-0000-0000-0000-00000000abcd",
            "kind": "graph_delete",
            "requested_by": "user:u-admin",
            "removed": {"type": "topic", "title": "T", "edges_removed": Decimal(3), "drafts_removed": Decimal(0)},
        }
    )
    records: list[tuple[Any, ...]] = []

    class Audit:
        def record_actor(self, *args: Any) -> None:
            records.append(args)

    removal.sweep(Audit())
    details = records[0][4]
    assert records[0][2] == "graph.delete" and details["edges_removed"] == 3 and type(details["edges_removed"]) is int


def test_r62_a_queue_item_cleared_meanwhile_is_not_recreated(ctx: TestContext, monkeypatch: Any) -> None:
    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    node = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Cleared"), "u")[0]
    removal = moderation.NodeRemoval.of(ctx)
    removal.queue(node, "user:u-admin")
    real_remove = removal.remove

    def remove_and_clear(n: Any) -> Any:
        out = real_remove(n)
        removal._ops.delete_item(Key={"pk": moderation.QUEUE_PK, "sk": node.id})  # an operator clears the queue
        return out

    monkeypatch.setattr(removal, "remove", remove_and_clear)
    records: list[tuple[Any, ...]] = []

    class Audit:
        def record_actor(self, *args: Any) -> None:
            records.append(args)

    assert removal.sweep(Audit()) == 1
    assert [r[2] for r in records] == ["graph.delete"]
    assert "Item" not in removal._ops.get_item(Key={"pk": moderation.QUEUE_PK, "sk": node.id})


def test_r62_a_queue_item_update_error_surfaces(ctx: TestContext, monkeypatch: Any, caplog: Any) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.graph import moderation
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    node = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Throttled Q"), "u")[0]
    removal = moderation.NodeRemoval.of(ctx)
    removal.queue(node, "user:u-admin")

    def boom(**_: Any) -> None:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "UpdateItem")

    monkeypatch.setattr(removal._ops, "update_item", boom)
    assert removal.sweep() == 0
    assert "could not sweep queued node" in caplog.text
