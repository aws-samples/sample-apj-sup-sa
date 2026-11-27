# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Regression tests for the round-2 code and security reviews (CR2-xx, SR2-xx).

Each test names the finding it closes; the scenario is the one the review described.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from insights_radar.core.clock import FixedClock
from insights_radar.graph.model import Node
from insights_radar.graph.registry import NodeType
from tests.integration.harness import Client, TestContext, ensure_running


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


def _custom(api: Client, slug: str, grants: list[str], **extra: Any) -> dict[str, Any]:
    body = {"slug": slug, "name": slug, "insight_kind": slug.replace("-", "_") + "_k", "grants": grants, **extra}
    status, created, _ = api.call("POST", "/api/modules", body)
    assert status == 201, created
    return dict(created)


# ---- CR2-02: kg_recent filters and orders by the clock it shows ---------------------------------


def test_cr2_02_an_updated_trend_resurfaces_and_nothing_created_between_is_skipped(
    api: Client, ctx: TestContext
) -> None:
    trend = _tok(ctx, "trend")
    first = api.tool("kg_save_output", trend, {"title": "Trend A", "summary": "v1"})[1]["result"]
    ctx.clock.advance(timedelta(minutes=5))
    second = api.tool("kg_save_output", trend, {"title": "Trend B", "summary": "v1"})[1]["result"]
    ctx.clock.advance(timedelta(minutes=5))

    gap = _tok(ctx, "gap")
    status, page = api.tool("kg_recent", gap, {"type": "trend", "after": "2000-01-01T00:00:00Z"})
    assert status == 200 and page["result"]["cursor_field"] == "updated_at"
    items = page["result"]["items"]
    assert [i["title"] for i in items] == ["Trend A", "Trend B"]
    assert all(i["created_at"] for i in items)
    cursor = items[-1]["updated_at"]  # what the prompt tells the module to store

    # Trend A is refreshed after the downstream module finished it.
    status, _ = api.tool(
        "kg_update_output", trend, {"id": first["id"], "summary": "v2, with a new source", "expected_version": 1}
    )
    assert status == 200
    ctx.clock.advance(timedelta(minutes=5))
    third = api.tool("kg_save_output", trend, {"title": "Trend C", "summary": "v1"})[1]["result"]

    status, page = api.tool("kg_recent", gap, {"type": "trend", "after": cursor})
    titles = [i["title"] for i in page["result"]["items"]]
    assert titles == ["Trend A", "Trend C"]  # the update resurfaces, in updated_at order
    # Storing the newest updated_at shown never passes an item: B was finished before A's update.
    newest = page["result"]["items"][-1]["updated_at"]
    status, page = api.tool("kg_recent", gap, {"type": "trend", "after": newest})
    assert page["result"]["items"] == []
    assert second["id"] != third["id"]


def test_cr2_02_a_whitespace_only_update_keeps_its_place(api: Client, ctx: TestContext) -> None:
    trend = _tok(ctx, "trend")
    old = api.tool("kg_save_output", trend, {"title": "Old", "summary": "v1"})[1]["result"]
    ctx.clock.advance(timedelta(minutes=5))
    api.tool("kg_save_output", trend, {"title": "New", "summary": "v1"})
    ctx.clock.advance(timedelta(minutes=5))
    status, _body = api.tool("kg_update_output", trend, {"id": old["id"], "summary": " v1 ", "expected_version": 1})
    assert status == 200
    node = ctx.graph.get(old["id"])
    assert node.version == 2 and node.updated_at == node.created_at
    assert [n.title for n in ctx.graph.recent(node.type).items] == ["New", "Old"]


# ---- SR2-01: an unstamped node keeps the sticky private-writer mark -----------------------------


def test_sr2_01_an_admin_dropping_product_knowledge_does_not_publish_old_unstamped_outputs(
    api: Client, ctx: TestContext
) -> None:
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    made = _custom(api, "roadmap", ["kg_read", "kg_write_output", "product_knowledge"])
    status, out = api.tool(
        "kg_save_output", _tok(ctx, "roadmap"), {"title": "Falcon", "summary": "Project Falcon ships Q3"}
    )
    assert status == 200, out
    # Written months ago, before nodes were stamped: no private attribute at all.
    old = out["result"]["id"]
    ctx.graph._table.update_item(
        Key={"pk": f"N#{old}", "sk": "META"}, UpdateExpression="REMOVE #p", ExpressionAttributeNames={"#p": "private"}
    )
    legacy_entity = ctx.graph.upsert_entity(
        Node(id="", type=NodeType.ORGANIZATION, title="Falcon Partner", module="roadmap"), "module:roadmap"
    )[0]
    ctx.graph._table.update_item(
        Key={"pk": f"N#{legacy_entity.id}", "sk": "META"},
        UpdateExpression="REMOVE #p",
        ExpressionAttributeNames={"#p": "private"},
    )
    assert not ctx.graph.get(old).stamped

    # The validator refuses product_knowledge with a web tool, so the admin swaps one for the other.
    status, body, _ = api.call(
        "PATCH",
        "/api/modules/roadmap",
        {"version": made["version"], "grants": ["kg_read", "kg_write_output", "web_fetch"]},
    )
    assert status == 200, body
    assert ctx.modules.get("roadmap").private_writer and not ctx.modules.get("roadmap").reads_private

    scout = _tok(ctx, "scout", "run-s")
    assert api.tool("kg_get", scout, {"id": old})[0] == 404
    assert api.tool("kg_get", scout, {"id": legacy_entity.id})[0] == 404
    status, hits = api.tool("kg_recent", scout, {"type": "insight", "insight_kind": "roadmap_k"})
    assert status == 200 and [i["id"] for i in hits["result"]["items"]] == []

    # An update by the now-outbound module does not stamp the old node public.
    roadmap = _tok(ctx, "roadmap", "run-2")
    assert api.tool("kg_update_output", roadmap, {"id": old, "summary": "edited", "expected_version": 1})[0] == 200
    assert not ctx.graph.get(old).stamped
    assert api.tool("kg_get", scout, {"id": old})[0] == 404

    # What it writes now, stamped public at write time, is readable as before (B2-018).
    status, out = api.tool("kg_save_output", roadmap, {"title": "Public", "summary": "From the web"})
    assert status == 200 and ctx.graph.get(out["result"]["id"]).stamped
    assert api.tool("kg_get", scout, {"id": out["result"]["id"]})[0] == 200


# ---- CR2-03: the seed delivers safe template changes to an existing deployment ------------------


def test_cr2_03_an_upgrade_gets_the_template_changes_once(api: Client, ctx: TestContext) -> None:
    from dataclasses import replace

    from insights_radar.api import workers
    from insights_radar.modules.model import ToolGrant as G
    from insights_radar.modules.repository import ModuleRepository

    # What an r1 deployment stored: Trends with web_search, drafts with one slot, no change record.
    for slug, changes in (
        ("trend", {"grants": frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.WEB_SEARCH})}),
        ("draft", {"max_concurrency": 1}),
        ("signal", {}),
    ):
        current = ctx.modules.get(slug)
        old = replace(current, **changes, extra={}, instructions="mine", version=3)
        ctx.ops_table.put_item(Item=ModuleRepository._def_item(old))

    out = workers.seed({}, None, ctx)
    assert out["migrated"] == ["draft", "trend"] and out["failed"] == []
    trend, draft, signal = (ctx.modules.get(s) for s in ("trend", "draft", "signal"))
    assert G.WEB_SEARCH not in trend.grants and trend.instructions == "mine" and trend.version == 4
    assert draft.max_concurrency == 5 and draft.version == 4
    assert signal.version == 3  # nothing to do for it
    rows = {
        r["target"]: r["details"]
        for r in ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 1000)[0]
        if r["action"] == "module.migrate"
    }
    assert rows == {
        "trend": {"grants_removed": ["web_search"], "template_changes": ["2026-10-trend-no-web-search"]},
        "draft": {"max_concurrency": [1, 5], "template_changes": ["2026-10-draft-concurrency-5"]},
    }

    # Once only: an admin who puts web_search back and lowers drafts to one slot keeps both.
    status, body, _ = api.call(
        "PATCH", "/api/modules/trend", {"version": 4, "grants": ["kg_read", "kg_write_output", "web_search"]}
    )
    assert status == 200, body
    assert api.call("PATCH", "/api/modules/draft", {"version": 4, "max_concurrency": 1})[0] == 200
    assert workers.seed({}, None, ctx)["migrated"] == []
    assert G.WEB_SEARCH in ctx.modules.get("trend").grants and ctx.modules.get("draft").max_concurrency == 1


def _r1_seed_rollback(ctx: TestContext, slug: str, **changes: Any) -> None:
    """What the previous release's seed does when an upgrade rolls back: it resets the built-in to
    its old template (Trends got web_search back, drafts one slot) and keeps ``extra`` as it is,
    the new release's change record included (r3 P3-003, live Seed log 8 Oct 01:49:53)."""
    from dataclasses import replace

    from insights_radar.modules.repository import ModuleRepository

    current = ctx.modules.get(slug)
    ctx.ops_table.put_item(Item=ModuleRepository._def_item(replace(current, **changes, version=current.version + 1)))


def test_p3003_a_rollback_does_not_undo_a_template_change_for_good(api: Client, ctx: TestContext) -> None:
    from dataclasses import replace

    from insights_radar.api import workers
    from insights_radar.modules.model import ToolGrant as G
    from insights_radar.modules.repository import ModuleRepository

    with_search = frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.WEB_SEARCH})
    for slug, changes in (("trend", {"grants": with_search}), ("draft", {"max_concurrency": 1})):
        old = replace(ctx.modules.get(slug), **changes, extra={}, version=3)
        ctx.ops_table.put_item(Item=ModuleRepository._def_item(old))

    # The upgrade's seed applies both changes and records them, in the same write as the change.
    assert workers.seed({}, None, ctx)["migrated"] == ["draft", "trend"]
    for slug in ("trend", "draft"):
        assert ctx.modules.get(slug).extra["template_changes"]
    # A later resource fails; the rollback's (older) seed restores both and keeps the records.
    _r1_seed_rollback(ctx, "trend", grants=with_search)
    _r1_seed_rollback(ctx, "draft", max_concurrency=1)
    assert G.WEB_SEARCH in ctx.modules.get("trend").grants

    # The next successful upgrade looks at the module, not only at the record.
    out = workers.seed({}, None, ctx)
    assert out["migrated"] == ["draft", "trend"] and out["failed"] == []
    assert G.WEB_SEARCH not in ctx.modules.get("trend").grants
    assert ctx.modules.get("draft").max_concurrency == 5
    rows = [
        r["details"]
        for r in ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 1000)[0]
        if r["action"] == "module.migrate" and r["target"] == "trend"
    ]
    assert {
        "grants_removed": ["web_search"],
        "template_changes": ["2026-10-trend-no-web-search"],
        "template_changes_reapplied": ["2026-10-trend-no-web-search"],
    } in rows
    assert workers.seed({}, None, ctx)["migrated"] == []  # and then it is done

    # An administrator who puts the grant back keeps it, through a rollback and the next seed.
    version = ctx.modules.get("trend").version
    grants = ["kg_read", "kg_write_output", "web_search"]
    assert api.call("PATCH", "/api/modules/trend", {"version": version, "grants": grants})[0] == 200
    assert ctx.modules.get("trend").extra["template_overrides"] == ["2026-10-trend-no-web-search"]
    # An unrelated edit while the grant is held keeps the override.
    version = ctx.modules.get("trend").version
    assert api.call("PATCH", "/api/modules/trend", {"version": version, "instructions": "mine"})[0] == 200
    assert ctx.modules.get("trend").extra["template_overrides"] == ["2026-10-trend-no-web-search"]
    _r1_seed_rollback(ctx, "trend", grants=with_search)
    assert workers.seed({}, None, ctx)["migrated"] == []
    assert G.WEB_SEARCH in ctx.modules.get("trend").grants

    # Taking it away again ends the override: a later rollback is repaired again.
    version = ctx.modules.get("trend").version
    assert (
        api.call("PATCH", "/api/modules/trend", {"version": version, "grants": ["kg_read", "kg_write_output"]})[0]
        == 200
    )
    assert "template_overrides" not in ctx.modules.get("trend").extra
    _r1_seed_rollback(ctx, "trend", grants=with_search)
    assert workers.seed({}, None, ctx)["migrated"] == ["trend"]
    assert G.WEB_SEARCH not in ctx.modules.get("trend").grants

    # The same for the drafts' concurrency: an administrator's one slot is kept.
    version = ctx.modules.get("draft").version
    assert api.call("PATCH", "/api/modules/draft", {"version": version, "max_concurrency": 1})[0] == 200
    _r1_seed_rollback(ctx, "draft", max_concurrency=1)
    assert workers.seed({}, None, ctx)["migrated"] == []
    assert ctx.modules.get("draft").max_concurrency == 1


def test_p3003_an_edit_unrelated_to_a_template_change_records_no_override(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    workers.seed({}, None, ctx)
    trend = ctx.modules.get("trend")
    assert api.call("PATCH", "/api/modules/trend", {"version": trend.version, "instructions": "mine"})[0] == 200
    assert "template_overrides" not in ctx.modules.get("trend").extra
    # A custom module or a built-in without a recorded change is never marked.
    draft = ctx.modules.get("draft")
    assert api.call("PATCH", "/api/modules/draft", {"version": draft.version, "max_concurrency": 2})[0] == 200
    assert "template_overrides" not in ctx.modules.get("draft").extra


def test_cr2_03_an_admin_choice_of_concurrency_is_kept(ctx: TestContext) -> None:
    from dataclasses import replace

    from insights_radar.modules.repository import ModuleRepository

    current = ctx.modules.get("draft")
    ctx.ops_table.put_item(Item=ModuleRepository._def_item(replace(current, max_concurrency=3, extra={})))
    assert ctx.modules.seed_builtins().migrated == {}
    stored = ctx.modules.get("draft")
    assert stored.max_concurrency == 3 and stored.version == current.version
    assert stored.extra["template_changes"] == ["2026-10-draft-concurrency-5"]


# ---- SR2-04: a stored custom module with a gated grant and an outbound tool cannot run -----------


def test_sr2_04_a_stored_curator_with_radar_suggest_and_web_fetch_is_refused_and_disabled(
    api: Client, ctx: TestContext
) -> None:
    from dataclasses import replace

    from insights_radar.api import workers
    from insights_radar.core.errors import ValidationError
    from insights_radar.modules.model import ToolGrant as G
    from insights_radar.modules.repository import ModuleRepository

    made = _custom(api, "mycur", ["kg_read", "kg_write_output"])
    stored = replace(
        ctx.modules.get(made["slug"]),
        grants=frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.RADAR_SUGGEST, G.WEB_FETCH}),
        enabled=True,
    )
    ctx.ops_table.put_item(Item=ModuleRepository._def_item(stored))  # before the rule existed
    with pytest.raises(ValidationError, match="radar_suggest cannot be combined with an outbound tool"):
        ctx.module_service.start("mycur", "manual", "u-admin")
    ensure_running(ctx, "mycur", "run-old")
    tok = ctx.tokens.issue("mycur", "run-old", stored.tools, 600)
    status, err = api.tool("kg_search", tok, {"query": "x"})
    assert status == 403 and "radar_suggest cannot be combined" in err["message"]
    assert api.tool("budget_status", tok)[0] == 200  # it may still report and stop

    out = workers.seed({}, None, ctx)
    assert "mycur" in out["migrated"]
    after = ctx.modules.get("mycur")
    assert not after.enabled and after.grants == stored.grants and after.version == stored.version + 1
    rows = [
        r
        for r in ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 1000)[0]
        if r["action"] == "module.migrate" and r["target"] == "mycur"
    ]
    assert rows[0]["details"] == {
        "disabled": True,
        "reason": "grant radar_suggest cannot be combined with an outbound tool",
    }
    assert "mycur" not in workers.seed({}, None, ctx)["migrated"]  # disabled: nothing more to do


# ---- CR2-05 / CR2-08 / SR2-09: a run is charged at the tier it started on ----------------------


def test_cr2_08_finalize_works_after_the_module_was_deleted(api: Client, ctx: TestContext) -> None:
    from dataclasses import replace

    from insights_radar.core.tokens import TokenUsage, TokenWeights
    from insights_radar.modules.repository import RunStatus

    made = _custom(api, "doomed", ["kg_read", "kg_write_output"], model_key="opus-5-5")
    run = ctx.module_service.start("doomed", "manual", "u-admin")
    assert ctx.modules.get_run("doomed", run.run_id).tier == "max"
    assert api.call("DELETE", "/api/modules/doomed")[0] == 200
    usage = TokenUsage(1000, 100, 0, 0)
    done = ctx.module_service.finalize("doomed", run.run_id, RunStatus.SUCCEEDED, usage)
    assert done.status is RunStatus.SUCCEEDED
    assert ctx.modules.get_run("doomed", run.run_id).weighted_tokens == TokenWeights.for_tier("max").weigh(usage)

    # A run recorded before the tier was stored: the module's current model, or balanced once it is gone.
    del made
    legacy = replace(ctx.module_service.start("trend", "manual", "u-admin"), tier=None)
    assert ctx.module_service.run_tier(legacy) == "balanced"  # trend runs on the balanced default
    gone = replace(legacy, module="nobody")
    assert ctx.module_service.run_tier(gone) == "balanced"


def test_cr2_08_a_model_change_mid_run_does_not_change_what_the_run_costs(ctx: TestContext) -> None:
    from insights_radar.core.tokens import TokenUsage, TokenWeights
    from insights_radar.modules.repository import RunStatus

    module = ctx.modules.get("trend")
    module = ctx.modules.update("trend", {"model_key": "opus-5-5"}, module.version)
    run = ctx.module_service.start("trend", "manual", "u-admin")
    ctx.modules.update("trend", {"model_key": "haiku-4-5"}, module.version)
    usage = TokenUsage(1000, 100, 0, 0)
    ctx.module_service.finalize("trend", run.run_id, RunStatus.SUCCEEDED, usage)
    assert ctx.modules.get_run("trend", run.run_id).weighted_tokens == TokenWeights.for_tier("max").weigh(usage)


def test_cr2_05_sr2_09_budget_status_reports_the_tier_and_whether_the_run_ended(api: Client, ctx: TestContext) -> None:
    from insights_radar.core.tokens import TokenUsage
    from insights_radar.modules.repository import RunStatus

    module = ctx.modules.get("trend")
    ctx.modules.update("trend", {"model_key": "haiku-4-5"}, module.version)
    run = ctx.module_service.start("trend", "manual", "u-admin")
    token = ctx.tokens.issue("trend", run.run_id, ctx.modules.get("trend").tools, 600)
    status, body = api.tool("budget_status", token)
    assert status == 200 and body["result"]["tier"] == "fast" and body["result"]["run_ended"] is False
    ctx.module_service.finalize("trend", run.run_id, RunStatus.FAILED, TokenUsage(0, 0, 0, 0))
    status, body = api.tool("budget_status", token)
    assert status == 200 and body["result"]["run_ended"] is True
    # A token whose run record is gone (expired) counts as ended too.
    stray = ctx.tokens.issue("trend", "no-such-run", ctx.modules.get("trend").tools, 600)
    status, body = api.tool("budget_status", stray)
    assert status == 200 and body["result"]["run_ended"] is True and body["result"]["tier"] == "fast"


# ---- CR2-07 / SR2-07: what the runtime scrubs out of fetched pages -----------------------------


@pytest.mark.parametrize(
    "address",
    [
        "https://api.example.com/v1/rates?code=US",
        "https://api.example.com/v1/rates?appid=en",
        "https://api.example.com/v1/rates?code=1&sas=ab",
        "https://api.example.com/v1/rates?consumer_key=short",
    ],
)
def test_cr2_07_a_short_value_is_not_scrubbed_out_of_every_page(address: str) -> None:
    from insights_radar.core.urls import UrlGuard

    # r3 B3-016: a short value is scrubbed only as ``name=value`` (and in the whole query), never
    # bare, so "US" stays in "USD" and "STATUS".
    forms = UrlGuard.secret_values(address)
    assert forms and all("=" in f for f in forms)
    assert _scrubbed("PRICES IN USD; STATUS en", forms) == "PRICES IN USD; STATUS en"
    redacted, changed = UrlGuard.redact_secrets(address)
    assert changed and "REDACTED" in redacted  # the address itself is still redacted


def _scrubbed(text: str, forms: list[str]) -> str:
    for value in forms:
        text = text.replace(value, "REDACTED")
    return text


def test_cr2_07_a_credential_length_value_is_still_scrubbed() -> None:
    from insights_radar.core.urls import UrlGuard

    assert "Xk2pQ9vLm7" in UrlGuard.secret_values("https://fn.azurewebsites.net/api/feed?code=Xk2pQ9vLm7")


@pytest.mark.parametrize("name", ["api%5Fkey", "%61pi_key", "API%2DKEY", "api%5fkey"])
def test_sr2_07_an_encoded_parameter_name_is_recognised(name: str) -> None:
    from insights_radar.core.urls import UrlGuard

    address = f"https://api.example.com/feed?{name}=SECRETVALUE1"
    assert "SECRETVALUE1" in UrlGuard.secret_values(address)
    redacted, changed = UrlGuard.redact_secrets(address)
    assert changed and "SECRETVALUE1" not in redacted


# ---- SR2-06 / SR2-08: path credentials in prompts and audit rows -------------------------------


def test_sr2_06_outbound_modules_see_documentation_hosts_only() -> None:
    from insights_radar.product.model import ProductProfile

    profile = ProductProfile(
        id="p",
        name="W",
        doc_urls=(
            "https://docs.example.com/share/9f3cdeadbeefe1/guide",
            "https://docs.example.com/other",
            "https://help.example.org/a?token=T",
        ),
    )
    block = profile.public_prompt_block()
    assert block == "Product: W\nPublic documentation: https://docs.example.com https://help.example.org"
    assert "9f3c" not in block


def test_sr2_08_audit_rows_keep_no_path_credential(api: Client, ctx: TestContext) -> None:
    hook = "https://hooks.example.com/services/T000/B000/XOXSECRETPATH"
    status, src, _ = api.call(
        "POST", "/api/modules/scout/sources", {"name": "Hook", "type": "public_api", "address": hook}
    )
    assert status == 201, src
    moved = "https://hooks.example.com/services/T000/B000/OTHERSECRETPATH"
    assert api.call("PATCH", f"/api/modules/scout/sources/{src['id']}", {"address": moved})[0] == 200
    assert api.call("DELETE", f"/api/modules/scout/sources/{src['id']}")[0] == 200
    status, product, _ = api.call("POST", "/api/products", {"name": "P"})
    assert status == 201
    status, body, _ = api.call(
        "PATCH",
        f"/api/products/{product['id']}",
        {"version": product["version"], "doc_urls": ["https://docs.example.com/share/DOCSECRETPATH/guide"]},
    )
    assert status == 200, body
    rows = ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 1000)[0]
    text = str(rows)
    for secret in ("XOXSECRETPATH", "OTHERSECRETPATH", "DOCSECRETPATH"):
        assert secret not in text
    assert "https://hooks.example.com (sha256:" in text and "https://docs.example.com (sha256:" in text


# ---- CR2-09: a source PATCH does not roll back the scout's cursor -------------------------------


def test_cr2_09_a_rename_racing_a_cursor_move_keeps_the_new_cursor(ctx: TestContext) -> None:
    from insights_radar.modules.model import SourceType

    src = ctx.modules.add_source("scout", "Feed", SourceType.RSS, "https://feeds.example.com/rss", cursor="c1")
    real_get = ctx.modules.get_source

    def stale_then_real(slug: str, sid: str) -> Any:
        read = real_get(slug, sid)  # the PATCH reads cursor c1 ...
        ctx.ops_table.update_item(  # ... and the scout moves it before the PATCH writes
            Key={"pk": f"MODULE#{slug}", "sk": f"SRC#{sid}"},
            UpdateExpression="SET #c = :c",
            ExpressionAttributeNames={"#c": "cursor"},
            ExpressionAttributeValues={":c": "c2"},
        )
        ctx.modules.get_source = real_get  # type: ignore[method-assign]
        return read

    ctx.modules.get_source = stale_then_real  # type: ignore[method-assign]
    renamed = ctx.modules.update_source("scout", src.id, {"name": "Renamed"})
    assert renamed.name == "Renamed" and renamed.cursor == "c2"
    assert ctx.modules.get_source("scout", src.id).cursor == "c2"


def test_sr2_04_a_custom_module_edited_during_the_seed_is_still_disabled(ctx: TestContext, api: Client) -> None:
    from dataclasses import replace

    from insights_radar.modules.model import ToolGrant as G
    from insights_radar.modules.repository import ModuleRepository

    made = _custom(api, "racer", ["kg_read", "kg_write_output"])
    stored = replace(
        ctx.modules.get(made["slug"]),
        grants=frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.RADAR_SUGGEST, G.MCP}),
        enabled=True,
    )
    ctx.ops_table.put_item(Item=ModuleRepository._def_item(stored))
    real = ctx.modules.list_all

    def stale() -> Any:
        found = real()
        current = ctx.modules.get("racer")  # an admin saves it while the seed is reading
        ctx.ops_table.put_item(Item=ModuleRepository._def_item(replace(current, version=current.version + 1)))
        return found

    ctx.modules.list_all = stale  # type: ignore[method-assign]
    report = ctx.modules.seed_builtins()
    # r3 B3-021: the seed re-reads the module and disables the admin's newer version; no deploy failure.
    assert report.failed == {} and report.migrated["racer"]["disabled"] is True
    assert ctx.modules.get("racer").enabled is False


# ---- CR2-11 / CR2-12: data written under the old rules ------------------------------------------


def test_cr2_11_entity_keys_written_before_nfkc_folding_are_moved(ctx: TestContext) -> None:
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme\u2122"), "u")
    soft, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Oct\u00adank"), "u")
    other, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Zeta"), "u")
    table = ctx.graph._table
    # As an earlier build keyed them: NFC, with the trade mark sign and the soft hyphen kept.
    for node, old in ((acme, "acme\u2122"), (soft, "oct\u00adank")):
        table.delete_item(Key={"pk": f"K#organization#{node.name_key}", "sk": "KEY"})
        table.put_item(Item={"pk": f"K#organization#{old}", "sk": "KEY", "node_id": node.id, "kind": "key"})
    # One whose new key another entity already holds, and one whose node is gone.
    table.put_item(Item={"pk": "K#organization#zeta\u00ad", "sk": "KEY", "node_id": other.id, "kind": "key"})
    table.put_item(Item={"pk": "K#organization#ghost", "sk": "KEY", "node_id": "organization/gone", "kind": "key"})
    assert ctx.graph.find_entity(NodeType.ORGANIZATION, "Acme\u2122") is None  # the duplicate the review saw

    assert ctx.graph.rekey_names(page_size=2) == (2, 1)
    assert ctx.graph.find_entity(NodeType.ORGANIZATION, "AcmeTM") == ctx.graph.get(acme.id)
    again, created = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Octank"), "u")
    assert not created and again.id == soft.id
    assert ctx.graph.rekey_names() == (0, 1)  # idempotent; the collision waits for a person


def test_cr2_11_a_rekey_write_error_is_raised(ctx: TestContext) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType
    from insights_radar.graph.repository import GraphRepository

    node, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Beta"), "u")
    table = ctx.graph._table
    table.put_item(Item={"pk": "K#organization#old-beta", "sk": "KEY", "node_id": node.id, "kind": "key"})

    class Failing:
        name = table.name
        meta = table.meta

        def scan(self, **kwargs: Any) -> Any:
            return table.scan(**kwargs)

        def get_item(self, **kwargs: Any) -> Any:
            return table.get_item(**kwargs)

        def put_item(self, **kwargs: Any) -> Any:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "PutItem")

    with pytest.raises(ClientError):
        GraphRepository(Failing()).rekey_names()  # type: ignore[arg-type]


def test_cr2_11_a_radar_saved_with_compatibility_form_twins_can_still_be_saved(ctx: TestContext) -> None:
    from insights_radar.core.errors import ConflictError
    from insights_radar.identity.principal import Principal, PrincipalKind, Role
    from insights_radar.radars.model import RadarEntry

    admin = Principal(PrincipalKind.USER, "u-admin", Role.ADMIN, "admin@example.com")
    ctx.radars.create(admin, "twins", "Twins", template="blank")
    pair = [
        RadarEntry(key="a", title="Octank", sector="General", ring="Now"),
        RadarEntry(key="b", title="\uff2fctank", sector="General", ring="Now"),  # full-width O
    ]
    radar = ctx.radars.get("twins")
    ctx.radars._save(radar, pair, radar.current_version, "u", "", False, allow_duplicates=True)  # an r1 save
    edited = [*pair, RadarEntry(key="c", title="Other", sector="General", ring="Now")]
    saved = ctx.radars.save(admin, "twins", edited, ctx.radars.get("twins").current_version)
    assert [e.title for e in ctx.radars.entries("twins")] == ["Octank", "\uff2fctank", "Other"]
    # Nothing new may add such a pair, and an exact duplicate is still refused.
    for extra in ("\uff4fctank", "Other"):
        with pytest.raises(ConflictError, match="entry titles must be unique"):
            ctx.radars.save(
                admin,
                "twins",
                [*edited, RadarEntry(key="d", title=extra, sector="General", ring="Now")],
                saved.current_version,
            )


def test_cr2_12_working_marks_from_before_the_index_are_removed_with_their_user(ctx: TestContext) -> None:
    from insights_radar.api import workers

    for i in range(3):
        ctx.ops_table.put_item(
            Item={"pk": f"WORKING#content_idea/{i}", "sk": "USER#u-gone", "email": "g@example.com", "since": "x"}
        )
    assert ctx.insights.working_marks_of("u-gone") == 0  # the gap the review found
    assert ctx.insights.index_working_marks(page_size=1) == 3
    for i in range(3):  # as before again, to run it through the seed
        ctx.ops_table.update_item(
            Key={"pk": f"WORKING#content_idea/{i}", "sk": "USER#u-gone"}, UpdateExpression="REMOVE gsi1pk, gsi1sk"
        )
    workers.seed({}, None, ctx)
    assert ctx.insights.index_working_marks() == 0  # done once
    assert ctx.insights.working_marks_of("u-gone") == 3


# ---- CR2-15: an invoke that may have started is not finalized as cancelled ----------------------


@pytest.mark.parametrize("kind", ["timeout", "server", "param"])
def test_cr2_15_only_a_proof_that_nothing_started_releases_the_hold(ctx: TestContext, kind: str) -> None:
    from botocore.exceptions import ClientError, ParamValidationError, ReadTimeoutError

    from insights_radar.api import workers
    from insights_radar.modules.repository import RunStatus

    run = ctx.module_service.start("signal", "manual", "u-admin")
    errors: dict[str, Exception] = {
        "timeout": ReadTimeoutError(endpoint_url="https://agentcore"),
        "server": ClientError(
            {"Error": {"Code": "InternalServerException"}, "ResponseMetadata": {"HTTPStatusCode": 500}},
            "InvokeAgentRuntime",
        ),
        "param": ParamValidationError(report="bad payload"),
    }

    class Failing:
        def invoke_agent_runtime(self, **kwargs: Any) -> Any:
            raise errors[kind]

    real = ctx.client
    ctx.client = lambda name: Failing() if name == "bedrock-agentcore" else real(name)  # type: ignore[method-assign]
    try:
        with pytest.raises(type(errors[kind])):
            workers.invoke({"payload": ctx.launcher.payloads[-1], "task_token": "t", "runtime_arn": "arn:x"}, None, ctx)
    finally:
        ctx.client = real  # type: ignore[method-assign]
    stored = ctx.modules.get_run("signal", run.run_id)
    if kind == "param":  # refused before it was sent: nothing ran, the hold goes back
        assert stored.status is RunStatus.CANCELLED
    else:  # the runtime may have accepted it: still live, the hold still charged
        assert stored.status is not RunStatus.CANCELLED and not stored.status.terminal
        assert ctx.modules.budget("signal", 2_000_000).consumed == stored.budget_hold


# ---- SR2-12: raw private passages are not for viewers ----------------------------------------------


def test_sr2_12_viewers_cannot_ask_a_products_documents_or_list_them(api: Client, ctx: TestContext) -> None:
    status, product, _ = api.call("POST", "/api/products", {"name": "P"})
    assert status == 201
    for method, path, body in (
        ("POST", f"/api/products/{product['id']}/ask", {"query": "roadmap"}),
        ("GET", f"/api/products/{product['id']}/documents", None),
    ):
        assert api.call(method, path, body, role="viewer")[0] == 403, path
        assert api.call(method, path, body, role="editor")[0] == 200, path


# ---- SR2-11: the workspace ceiling is configured, spares administrators and is alarmed -----------


def test_sr2_11_viewers_at_the_ceiling_do_not_lock_out_an_administrator(
    graph_table: Any, ops_table: Any, clock: FixedClock, caplog: pytest.LogCaptureFixture
) -> None:
    from dataclasses import replace

    from insights_radar.api.app import Application
    from insights_radar.api.context import Settings

    ctx = TestContext(graph_table, ops_table, clock)
    ctx.settings = replace(ctx.settings, workspace_rate_per_minute=3)
    api = Client(Application(ctx))
    body = {"query": "motors"}
    for _ in range(3):
        assert api.call("POST", "/api/graph/search", body, role="viewer")[0] == 200
    status, err, _ = api.call("POST", "/api/graph/search", {**body, "query": "x" * 3}, role="viewer")
    assert status == 429 and "workspace is busy" in str(err)
    # r3 B3-054: the ceiling is per role, so the viewers do not lock editors out.
    assert api.call("POST", "/api/graph/search", body, role="editor")[0] == 200
    assert '"event": "rate.workspace_limit"' in caplog.text
    for _ in range(5):  # an administrator is held to their own limit only
        assert api.call("POST", "/api/graph/search", body)[0] == 200
    assert Settings.from_env({"AIR_WORKSPACE_RATE_PER_MINUTE": "1200"}).workspace_rate_per_minute == 1200
    assert Settings.from_env({}).workspace_rate_per_minute == 600


# ---- SR2-02: autopilot corroboration counts fetched evidence from distinct registrable domains ----


def test_sr2_02_one_injected_run_cannot_forge_two_sites(api: Client, ctx: TestContext) -> None:
    from insights_radar.api.workers import _usable_evidence

    scout = _tok(ctx, "scout")
    forged = [
        api.tool("kg_add_evidence", scout, {"type": "fact", "title": f"F{i}", "evidence_url": u})[1]["result"]["id"]
        for i, u in enumerate(("https://www.reuters.com/x", "https://techcrunch.com/y"))
    ]
    same_owner = [
        api.tool("kg_add_evidence", scout, {"type": "fact", "title": f"G{i}", "evidence_url": u, "fetched": True})[1][
            "result"
        ]["id"]
        for i, u in enumerate(("https://a.attacker.example/1", "https://b.attacker.example/2"))
    ]
    check = _usable_evidence(ctx)
    assert check(forged) == {}  # the runtime never reported fetching them
    assert set(check(same_owner).values()) == {frozenset({"attacker.example"})}  # one registrable domain
    person = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Recorded by a person", evidence_url="https://news.example.net/z"),
        "user:u-admin",
    )
    assert check([person.id]) == {person.id: frozenset({"example.net"})}


@pytest.mark.parametrize(
    ("address", "site"),
    [
        ("https://www.reuters.com/x", "reuters.com"),
        ("https://a.attacker.co.uk/x", "attacker.co.uk"),
        ("https://user.github.io/p", "user.github.io"),
        ("https://x.y.s3.amazonaws.com/k", "y.s3.amazonaws.com"),
        ("https://203.0.113.7/a", "ip:203.0.113.0/24"),  # r3 B3-008: one network is one site
        ("https://localhost/a", "localhost"),
        ("not a url", "REDACTED"),
    ],
)
def test_sr2_02_site_is_the_registrable_domain(address: str, site: str) -> None:
    from insights_radar.core.urls import UrlGuard

    assert UrlGuard.site(address) == site


# ---- SR2-05: probing private entity names yields at most a few bits per run ---------------------


def test_sr2_05_a_run_probing_private_names_is_cut_off_and_audited(api: Client, ctx: TestContext) -> None:
    _custom(api, "pw", ["kg_read", "kg_write_output", "kg_write_evidence", "product_knowledge"])
    pw = _tok(ctx, "pw")
    for name in ("Project Falcon", "Project Heron", "Project Owl", "Project Wren"):
        assert api.tool("kg_upsert_entity", pw, {"type": "product", "name": name})[0] == 200
    scout = _tok(ctx, "scout", "run-s")
    answers = [
        api.tool("kg_upsert_entity", scout, {"type": "product", "name": name})
        for name in ("Project Falcon", "Project Heron", "Project Owl", "Project Wren", "Unrelated Name")
    ]
    assert [a[0] for a in answers] == [403, 403, 403, 403, 403]
    assert len({a[1]["message"] for a in answers}) == 1  # the same answer, private match or not
    assert api.tool("kg_upsert_entity", _tok(ctx, "scout", "run-t"), {"type": "product", "name": "New"})[0] == 200
    rows = [
        r
        for r in ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 1000)[0]
        if r["action"] == "agent.private_name_match"
    ]
    assert sorted(r["details"]["matches"] for r in rows) == [1, 2, 3]


def test_sr2_05_an_audit_failure_does_not_reveal_the_match(api: Client, ctx: TestContext) -> None:
    _custom(api, "pw", ["kg_read", "kg_write_output", "kg_write_evidence", "product_knowledge"])
    assert api.tool("kg_upsert_entity", _tok(ctx, "pw"), {"type": "product", "name": "Project Falcon"})[0] == 200

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit table down")

    ctx.audit.record_actor = boom  # type: ignore[method-assign]
    status, err = api.tool(
        "kg_upsert_entity", _tok(ctx, "scout", "run-s"), {"type": "product", "name": "Project Falcon"}
    )
    assert status == 403 and err["message"] == "this entity cannot be created or read by this module"
