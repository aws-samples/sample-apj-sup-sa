# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Regression tests for the red-team r1 review of the blue-team fixes (code-review CR-xx and
security-review SR-xx). Each test names the finding it closes; the scenario is the one the review
described.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from insights_radar.api import workers
from insights_radar.api.app import Application
from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ConflictError, NotFoundError, ServiceUnavailableError, ValidationError
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.modules.model import McpServer, ToolGrant
from insights_radar.modules.repository import ModuleRepository
from insights_radar.modules.templates import builtin_modules
from tests.integration.harness import Client, TestContext, ensure_running

G = ToolGrant


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> TestContext:
    return TestContext(graph_table, ops_table, clock)


@pytest.fixture
def api(ctx: TestContext) -> Client:
    return Client(Application(ctx))


def _tok(ctx: TestContext, slug: str, run_id: str = "run-1") -> str:
    ensure_running(ctx, slug, run_id)
    return ctx.tokens.issue(slug, run_id, ctx.modules.get(slug).tools, 600, None)


def _audit(ctx: TestContext) -> list[dict[str, Any]]:
    return ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 1000)[0]


PRE_R1 = {
    "gap": frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.PRODUCT_KNOWLEDGE, G.WEB_FETCH, G.WEB_SEARCH}),
    "content": frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.PRODUCT_KNOWLEDGE, G.WEB_SEARCH}),
    "draft": frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.CONTENT_DRAFT, G.PRODUCT_KNOWLEDGE})
    | {G.WEB_FETCH, G.WEB_SEARCH, G.CODE_INTERPRETER},
}


def _store_pre_r1(ctx: TestContext) -> None:
    """Write the definitions a deployment installed before r1 still holds, bypassing the validator (it
    did not have the rule when they were saved), with an admin's edits on top."""
    for slug, grants in PRE_R1.items():
        current = ctx.modules.get(slug)
        old = replace(current, grants=grants, instructions=f"My {slug} notes", interval_minutes=720, version=4)
        if slug == "gap":
            old = replace(old, daily_token_budget=500_000, enabled=True)
        ctx.ops_table.put_item(Item=ModuleRepository._def_item(old))


# ---- SR-01 / CR-02: upgraded deployments keep the lethal trifecta ------------------------------


def test_sr01_a_stored_pre_r1_module_cannot_start_or_call_tools(api: Client, ctx: TestContext) -> None:
    _store_pre_r1(ctx)
    with pytest.raises(ValidationError, match="cannot run: product_knowledge cannot be combined"):
        ctx.module_service.start("gap", "manual", "u-admin")
    assert api.call("POST", "/api/modules/draft/run", {})[0] == 400
    tok = _tok(ctx, "gap")
    s, err = api.tool("product_knowledge_search", tok, {"query": "roadmap"})
    assert s == 403 and "cannot run" in err["message"]
    # It may still read its own bookkeeping, so the runtime can report and stop.
    assert api.tool("budget_status", tok)[0] == 200


def test_sr01_cr02_seed_migrates_grants_and_keeps_edits(api: Client, ctx: TestContext) -> None:
    _store_pre_r1(ctx)
    out = workers.seed({}, None, ctx)
    assert out == {"created": [], "migrated": ["content", "draft", "gap"], "failed": [], "warnings": []}
    templates = {m.slug: m for m in builtin_modules()}
    for slug in PRE_R1:
        m = ctx.modules.get(slug)
        assert m.grants == templates[slug].grants and not m.private_and_outbound
        assert m.instructions == f"My {slug} notes" and m.interval_minutes == 720 and m.version == 5
    gap = ctx.modules.get("gap")
    assert gap.enabled and gap.daily_token_budget == 500_000
    rows = {r["target"]: r for r in _audit(ctx) if r["action"] == "module.migrate"}
    assert set(rows) == {"gap", "content", "draft"}
    assert rows["gap"]["actor"] == "system:deploy"
    # r2 B2-009: the migration only removes; it no longer reports (or makes) additions.
    assert rows["gap"]["details"] == {"grants_removed": ["web_fetch", "web_search"]}
    assert rows["draft"]["details"]["grants_removed"] == ["code_interpreter", "web_fetch", "web_search"]
    # Idempotent: the next deploy changes nothing and writes no row.
    assert workers.seed({}, None, ctx) == {"created": [], "migrated": [], "failed": [], "warnings": []}
    assert len([r for r in _audit(ctx) if r["action"] == "module.migrate"]) == 3


def test_cr02_after_the_migration_the_gap_module_works_again(api: Client, ctx: TestContext) -> None:
    _store_pre_r1(ctx)
    # Before: changing only the interval is refused with the trifecta message.
    s, err, _ = api.call("PATCH", "/api/modules/gap", {"interval_minutes": 60, "version": 4})
    assert s == 400 and "product_knowledge cannot be combined" in str(err)
    workers.seed({}, None, ctx)
    s, body, _ = api.call("PATCH", "/api/modules/gap", {"interval_minutes": 60, "version": 5})
    assert s == 200 and body["interval_minutes"] == 60
    # The gap module is no longer outbound, so it can see the gaps it wrote.
    ctx.graph.create(Node(id="", type=NodeType.GAP, title="Missing SSO", module="gap"), "module:gap")
    s, page = api.tool("kg_recent", _tok(ctx, "gap", "run-2"), {"type": "gap"})
    assert s == 200 and [i["title"] for i in page["result"]["items"]] == ["Missing SSO"]


def test_cr02_seed_keeps_input_types_and_mcp_servers_an_admin_set(ctx: TestContext) -> None:
    """r2 B2-009 reversed this: writing the template's grants, input types and MCP servers back on
    every deploy re-armed tools an admin had removed and deleted servers an admin had attached."""
    trend = ctx.modules.get("trend")
    edited = replace(
        trend,
        grants=trend.grants | {G.MCP},
        mcp_servers=(McpServer("crm", "https://mcp.example.com/sse"),),
        input_types=(NodeType.FACT,),
    )
    ctx.ops_table.put_item(Item=ModuleRepository._def_item(edited))
    assert ctx.modules.seed_builtins().migrated == {}
    after = ctx.modules.get("trend")
    assert after.mcp_servers == edited.mcp_servers and after.input_types == (NodeType.FACT,)
    assert G.MCP in after.grants and after.version == trend.version


def test_cr02_seed_migration_retries_a_concurrent_edit(ctx: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_failure_paths import Chaos

    _store_pre_r1(ctx)
    # r2 B2-040: a module that cannot be repaired is reported, not raised, so the ones after it are
    # still created and migrated; the deploy itself then fails on the report (workers.seed).
    racing = ModuleRepository(Chaos(ctx.ops_table, {"put_item": "ConditionalCheckFailedException"}), ctx.clock)
    report = racing.seed_builtins()
    assert sorted(report.failed) == ["content", "draft", "gap"]
    assert all("being edited" in why for why in report.failed.values())
    # One lost race, then the write lands; any other error is reported against that module alone.
    from botocore.exceptions import ClientError

    real = ctx.ops_table.put_item
    failures = ["ConditionalCheckFailedException"]

    def flaky(**kw: Any) -> Any:
        if kw.get("ConditionExpression") == "version = :v" and failures:
            raise ClientError({"Error": {"Code": failures.pop(), "Message": "x"}}, "PutItem")
        return real(**kw)

    monkeypatch.setattr(ctx.ops_table, "put_item", flaky)
    repo = ModuleRepository(ctx.ops_table, ctx.clock)
    assert set(repo.seed_builtins().migrated) == {"gap", "content", "draft"}
    _store_pre_r1(ctx)
    failures.append("ThrottlingException")
    throttled = repo.seed_builtins()
    assert "ThrottlingException" in throttled.failed["gap"]
    assert set(throttled.migrated) == {"content", "draft"}  # the others were still repaired
    # The deploy itself is told at the end, once every built-in has been attempted.
    _store_pre_r1(ctx)
    failures.append("ThrottlingException")
    with pytest.raises(ServiceUnavailableError, match="could not be seeded"):
        workers.seed({}, None, ctx)


# ---- CR-01: keyed sources stay fetchable; ordinary parameters are not redacted ----------------


def test_cr01_redaction_matches_whole_credential_names_only() -> None:
    from insights_radar.core.urls import UrlGuard

    feed = "https://feed.example.com/s?keywords=agentic&author=jdoe&monkey=1&passage=2&signal=3&design=4&session_id=5"
    assert UrlGuard.redact_secrets(feed) == (feed, False)
    out, changed = UrlGuard.redact_secrets("https://api.example.com/v2?q=ai&apiKey=K1&X-Api-Key=K2&sig=S&token=T")
    assert (
        changed
        and out == "https://api.example.com/v2?q=ai&apiKey=REDACTED&X-Api-Key=REDACTED&sig=REDACTED&token=REDACTED"
    )
    forms = UrlGuard.secret_values("https://a.example.com/?key=abcdefgh&api_key=abcdefghij&q=x")
    assert {"abcdefghij", "abcdefgh"} <= set(forms) and "x" not in forms and "q=x" not in forms
    assert forms == sorted(forms, key=len, reverse=True)
    assert UrlGuard.secret_values("https://[::1/a?key=1") == []


def test_cr01_source_address_gives_the_runtime_the_real_address(api: Client, ctx: TestContext) -> None:
    from insights_radar.modules.model import SourceType

    keyed = ctx.modules.add_source(
        "scout", "News", SourceType.PUBLIC_API, "https://newsapi.example.com/v2/everything?q=ai&apiKey=SECRET1"
    )
    off = ctx.modules.add_source("scout", "Off", SourceType.RSS, "https://off.example.com/rss", enabled=False)
    tok = _tok(ctx, "scout")
    s, listed = api.tool("source_list", tok)
    assert s == 200 and "SECRET1" not in str(listed) and "source_id" in listed["result"]["note"]
    s, body = api.tool("source_address", tok, {"source_id": keyed.id})
    assert s == 200 and body["result"] == {"id": keyed.id, "type": "public_api", "address": keyed.address}
    assert api.tool("source_address", tok, {"source_id": off.id})[0] == 404
    # Another module (no sources grant) cannot read it.
    assert api.tool("source_address", _tok(ctx, "signal"), {"source_id": keyed.id})[0] == 403


# ---- CR-04: deleting a user removes edit rights first, and survives legacy editors and races ----


def _admin() -> Any:
    from insights_radar.identity.principal import Principal, PrincipalKind, Role

    return Principal(PrincipalKind.USER, "u-admin", Role.ADMIN, "admin@example.com")


def _legacy_editors(ctx: TestContext, slug: str, editors: tuple[str, ...]) -> None:
    """Editors stored under the old ``"@" in e`` rule, which the strict email check now rejects."""
    radar = ctx.radars.get(slug)
    ctx.radars._put_radar(replace(radar, editors=editors), "attribute_exists(pk)")


def test_cr04_a_legacy_editor_on_the_radar_does_not_block_the_delete(api: Client, ctx: TestContext) -> None:
    from insights_radar.identity.principal import Role

    x = ctx.users.invite("x@example.com", Role.EDITOR)
    ctx.radars.create(_admin(), "r", "R", template="blank")
    _legacy_editors(ctx, "r", ("ops@intranet", "x@example.com"))
    s, body, _ = api.call("DELETE", f"/api/users/{x.username}")
    assert s == 200 and body["editor_access_removed"] == ["r"]
    assert ctx.radars.get("r").editors == ("ops@intranet",)
    assert next(r for r in _audit(ctx) if r["action"] == "user.delete")["details"] == {
        "editor_of": ["r"],
        "owner_of": [],
        "working_marks_removed": 0,
    }
    with pytest.raises(NotFoundError):
        ctx.users.get(x.username)


def test_cr04_a_failed_removal_keeps_the_account_and_audits_what_changed(
    api: Client, ctx: TestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insights_radar.identity.principal import Role

    x = ctx.users.invite("x@example.com", Role.EDITOR)
    for slug in ("a", "b"):
        ctx.radars.create(_admin(), slug, slug.upper(), template="blank")
        ctx.radars.set_access(_admin(), slug, ["x@example.com"], 1)
    real = ctx.radars._replace_radar

    def locked(updated: Any, read: Any) -> None:
        if updated.slug == "b":
            raise ConflictError("the radar was changed by someone else; reload and retry")
        real(updated, read)

    monkeypatch.setattr(ctx.radars, "_replace_radar", locked)
    s, _, _ = api.call("DELETE", f"/api/users/{x.username}")
    assert s == 409
    assert ctx.users.get(x.username).email == "x@example.com"  # the Cognito account is still there
    assert ctx.radars.get("a").editors == () and ctx.radars.get("b").editors == ("x@example.com",)
    row = next(r for r in _audit(ctx) if r["action"] == "user.delete")
    assert row["details"] == {"editor_of": ["a"], "owner_of": [], "account_deleted": False}


def test_cr04_a_lock_conflict_is_retried(ctx: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx.radars.create(_admin(), "r", "R", template="blank")
    ctx.radars.set_access(_admin(), "r", ["x@example.com"], 1)
    real = ctx.radars._replace_radar
    lost = [True]

    def once(updated: Any, read: Any) -> None:
        if lost:
            lost.pop()
            raise ConflictError("raced")
        real(updated, read)

    monkeypatch.setattr(ctx.radars, "_replace_radar", once)
    assert ctx.radars.remove_editor(_admin(), "r", "X@example.com") is True
    assert ctx.radars.remove_editor(_admin(), "r", "x@example.com") is False  # already gone


def test_cr04_a_cognito_failure_with_nothing_removed_writes_no_row(
    api: Client, ctx: TestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insights_radar.identity.principal import Role

    x = ctx.users.invite("x@example.com", Role.EDITOR)

    def boom(name: str) -> None:
        raise RuntimeError("cognito down")

    monkeypatch.setattr(ctx.users, "delete", boom)
    assert api.call("DELETE", f"/api/users/{x.username}")[0] == 500
    assert not [r for r in _audit(ctx) if r["action"] == "user.delete"]


# ---- SR-02: what a product-knowledge module writes is hidden from outbound modules ------------


def _pricing_module(ctx: TestContext) -> None:
    from insights_radar.modules.model import ModuleDefinition, ModuleTemplate

    ctx.modules.create(
        ModuleDefinition(
            slug="pricing",
            name="Pricing exposure",
            template=ModuleTemplate.CUSTOM,
            output_kind=NodeType.INSIGHT,
            insight_kind="pricing_exposure",
            grants=frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.KG_WRITE_EVIDENCE, G.PRODUCT_KNOWLEDGE}),
        ),
        "u-admin",
    )


def test_sr02_outbound_modules_cannot_read_a_private_writers_nodes(api: Client, ctx: TestContext) -> None:
    _pricing_module(ctx)
    assert ctx.modules.get("pricing").extra == {"private_writer": True}
    secret = ctx.graph.create(
        Node(
            id="",
            type=NodeType.INSIGHT,
            title="Pricing exposure: v3 drops on-prem",
            summary="internal pricing paragraph",
            insight_kind="pricing_exposure",
            module="pricing",
        ),
        "module:pricing",
    )
    org, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    fact = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Pricing fact", evidence_url="https://a.example.com", module="pricing"),
        "module:pricing",
    )
    ctx.graph.link(secret.id, EdgeType.ABOUT, org.id, "module:pricing")
    ctx.graph.link(fact.id, EdgeType.ABOUT, org.id, "module:pricing")
    ctx.unstamp(secret.id, fact.id)  # written before nodes were stamped: the module's mark decides
    ctx.reindex()
    scout = _tok(ctx, "scout")
    s, found = api.tool("kg_search", scout, {"query": "pricing exposure"})
    assert s == 200 and secret.id not in str(found) and fact.id not in str(found)
    assert api.tool("kg_get", scout, {"id": secret.id})[0] == 404
    s, many = api.tool("kg_get", scout, {"ids": [secret.id, org.id]})
    assert [n["id"] for n in many["result"]["nodes"]] == [org.id] and many["result"]["missing"] == [secret.id]
    s, hood = api.tool("kg_neighbors", scout, {"id": org.id})
    assert {n["id"] for n in hood["result"]["nodes"]} == {org.id}
    s, recent = api.tool("kg_recent", scout, {"type": "insight", "insight_kind": "pricing_exposure"})
    assert s == 200 and recent["result"]["items"] == []
    # r2 B2-006 narrowed this: the curator has no outbound grant but does not read product knowledge
    # either, so it no longer reads a private writer's nodes (it would launder them into a radar
    # suggestion, or into its own output, in one hop). Only a module that reads the documents itself may.
    curator = _tok(ctx, "curator")
    assert api.tool("kg_get", curator, {"id": secret.id})[0] == 404
    s, recent = api.tool("kg_recent", curator, {"type": "insight", "insight_kind": "pricing_exposure"})
    assert s == 200 and recent["result"]["items"] == []
    pricing = _tok(ctx, "pricing")
    assert api.tool("kg_get", pricing, {"id": secret.id})[0] == 200


def test_sr02_the_private_mark_outlives_the_grant(api: Client, ctx: TestContext) -> None:
    _pricing_module(ctx)
    m = ctx.modules.get("pricing")
    s, body, _ = api.call(
        "PATCH", "/api/modules/pricing", {"grants": ["kg_read", "kg_write_output"], "version": m.version}
    )
    assert s == 200 and body["grants"] == ["kg_read", "kg_write_output"]
    after = ctx.modules.get("pricing")
    assert after.private_writer and after.extra == {"private_writer": True}
    # A module that never read product knowledge is not marked.
    plain = ctx.modules.get("trend")
    assert not plain.private_writer and plain.with_private_mark() is plain
    assert plain.with_private_mark(after).extra == {**plain.extra, "private_writer": True}


# ---- SR-03: outbound modules get only the public part of the product profile ------------------


def test_sr03_outbound_modules_get_a_public_product_projection(api: Client, ctx: TestContext) -> None:
    from insights_radar.product.model import ProductProfile

    prod = ctx.products.create(
        ProductProfile(
            id="",
            name="Desk",
            summary="Standing desks",
            competitors=("Rival",),
            doc_urls=("https://docs.example.com",),
            notes="unreleased: v3 drops on-prem support in Q2",
        ),
        "u-admin",
    )
    for slug in ("scout", "gap"):
        # r3 B3-015: the run's product is the module's product.
        ctx.modules.update(slug, {"product_id": prod.id}, ctx.modules.get(slug).version)
        ensure_running(ctx, slug, "run-p")
    scout = ctx.tokens.issue("scout", "run-p", ctx.modules.get("scout").tools, 600, prod.id)
    s, body = api.tool("run_context", scout)
    assert s == 200 and body["result"]["product"] == "Product: Desk\nPublic documentation: https://docs.example.com"
    gap = ctx.tokens.issue("gap", "run-p", ctx.modules.get("gap").tools, 600, prod.id)
    s, body = api.tool("run_context", gap)
    assert "unreleased: v3" in body["result"]["product"] and "Rival" in body["result"]["product"]
    bare = ctx.products.create(ProductProfile(id="", name="Bare"), "u-admin")
    assert bare.public_prompt_block() == "Product: Bare"


# ---- CR-09: a failed audit write does not turn a committed agent write into an error -------------


def test_cr09_agent_writes_survive_a_failed_audit_write(
    api: Client, ctx: TestContext, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def throttled(*a: Any, **kw: Any) -> None:
        raise RuntimeError("ProvisionedThroughputExceededException")

    monkeypatch.setattr(ctx.audit, "record_actor", throttled)
    s, body = api.tool("kg_upsert_entity", _tok(ctx, "scout"), {"type": "organization", "name": "Acme"})
    assert s == 200 and body["result"]["created"] is True
    assert "audit.write_failed" in caplog.text


# ---- CR-10: the daily draft limit counts per user per day and says when it resets ---------------


def test_cr10_draft_quota_is_per_user_and_resets_at_midnight(
    api: Client, ctx: TestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insights_radar.api import routes
    from insights_radar.core.errors import GenAiDisabledError

    monkeypatch.setattr(routes, "DRAFTS_PER_USER_PER_DAY", 1)
    api = Client(Application(ctx))
    version = ctx.modules.get("draft").version
    api.call("PATCH", "/api/modules/draft", {"enabled": True, "version": version})
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            module="content",
            attributes={"content_type": "blog", "audience": "x"},
        ),
        "m",
    ).id
    # A request that does not start a run (GenAI paused) does not use the allowance.
    real_start = ctx.module_service.start

    def paused(*a: Any, **kw: Any) -> Any:
        raise GenAiDisabledError("paused")

    monkeypatch.setattr(ctx.module_service, "start", paused)
    assert api.call("POST", f"/api/nodes/{idea}/draft", {}, role="editor")[0] == 503
    monkeypatch.setattr(ctx.module_service, "start", real_start)
    assert api.call("POST", f"/api/nodes/{idea}/draft", {}, role="editor")[0] == 202
    from tests.integration.test_redteam_r1 import _finish_last_draft

    _finish_last_draft(ctx)
    # Many other people's runs do not push this user's run out of the count (the old 100-run window).
    from insights_radar.modules.repository import ModuleRun, RunStatus

    for i in range(120):
        ctx.modules.put_run(
            ModuleRun("draft", f"other-{i:03d}", RunStatus.SUCCEEDED, "manual", "2026-10-02T23:00:00.000Z", "user:x")
        )
    s, err, headers = api.call("POST", f"/api/nodes/{idea}/draft", {}, role="editor")
    midnight = 86400 - int(ctx.clock.now().timestamp()) % 86400
    assert s == 429 and headers["Retry-After"] == str(midnight) and int(headers["Retry-After"]) > 60
    # Another user has their own allowance.
    from tests.integration import harness

    monkeypatch.setitem(
        harness.ROLES, "editor2", {"sub": "u-editor2", "email": "e2@example.com", "cognito:groups": "[editor]"}
    )
    s, err, _ = api.call("POST", f"/api/nodes/{idea}/draft", {}, role="editor2")
    assert s == 202, err


def test_cr10_quota_reraises_unexpected_errors(ctx: TestContext) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.core.ratelimit import DailyQuota
    from tests.unit.test_failure_paths import Chaos

    quota = DailyQuota(Chaos(ctx.ops_table, {"update_item": "ThrottlingException"}), ctx.clock)
    with pytest.raises(ClientError):
        quota.take("u", "draft", 1, "x")


# ---- CR-12: the pre-cursor wrappers are gone --------------------------------------------------


def test_cr12_dead_list_wrappers_are_removed() -> None:
    from insights_radar.audit.log import AuditLog
    from insights_radar.identity.users import UserAdmin
    from insights_radar.insights.service import InsightService
    from insights_radar.radars.service import RadarService

    for cls, name in (
        (UserAdmin, "list_users"),
        (InsightService, "feedback_by_type"),
        (RadarService, "list_versions"),
        (AuditLog, "day"),
    ):
        assert not hasattr(cls, name), name


# ---- CR-13 / SR-06: disabled accounts get their own code; unconfirmed roles are viewers ---------


def test_cr13_sr06_live_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.core.errors import AccountDisabledError, UnauthorizedError
    from insights_radar.identity.principal import Principal, PrincipalKind, Role
    from insights_radar.identity.users import LiveRoles, UserView

    state: dict[str, Any] = {"enabled": True, "error": None}

    class Users:
        def get(self, subject: str) -> UserView:
            if state["error"]:
                raise state["error"]
            return UserView(subject, f"{subject}@example.com", Role.ADMIN, state["enabled"], "CONFIRMED", "")

    now = [0.0]
    live = LiveRoles(Users(), ttl=30, now=lambda: now[0])  # type: ignore[arg-type]
    admin = Principal(PrincipalKind.USER, "a", Role.ADMIN, "a@example.com")
    assert live.confirm(admin).role is Role.ADMIN
    # SR-06: Cognito throttles while the cache is stale: the admin is treated as a viewer, not an admin.
    state["error"] = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "AdminGetUser")
    now[0] = 60.0
    assert live.confirm(admin).role is Role.VIEWER
    # CR-13: a disabled or deleted account is not "session expired".
    state["error"], state["enabled"] = None, False
    now[0] = 120.0
    with pytest.raises(AccountDisabledError) as err:
        live.confirm(admin)
    assert isinstance(err.value, UnauthorizedError) and err.value.to_body()["code"] == "account_disabled"
    state["error"] = NotFoundError("gone")
    now[0] = 200.0
    with pytest.raises(AccountDisabledError, match="no longer exists"):
        live.confirm(admin)


# ---- SR-05: the new owner is looked up only after authorisation --------------------------------


def test_sr05_access_change_authorises_before_the_owner_lookup(
    api: Client, ctx: TestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insights_radar.identity.principal import Principal, PrincipalKind, Role

    ctx.radars.create(Principal(PrincipalKind.USER, "u-other", Role.EDITOR, "o@example.com"), "theirs", "T")
    looked_up: list[str] = []
    real_get = ctx.users.get

    def spy(name: str) -> Any:
        looked_up.append(name)
        return real_get(name)

    monkeypatch.setattr(ctx.users, "get", spy)
    body = {"editors": [], "version_lock": 1, "owner": {"subject": "u-probe"}}
    s, _, _ = api.call("PUT", "/api/radars/theirs/access", body, role="editor")
    assert s == 403 and looked_up == []  # no existence oracle, no Cognito call
    assert api.call("PUT", "/api/radars/none/access", body, role="editor")[0] == 404


# ---- SR-07: mutation responses hide editor emails from callers who cannot administer -----------


def test_sr07_a_co_editor_does_not_get_the_editor_list_from_a_save(api: Client, ctx: TestContext) -> None:
    from insights_radar.identity.principal import Principal, PrincipalKind, Role

    owner = Principal(PrincipalKind.USER, "u-other", Role.EDITOR, "o@example.com")
    ctx.radars.create(owner, "shared", "Shared", template="blank")
    ctx.radars.set_access(owner, "shared", ["editor@example.com", "secret.person@example.com"], 1)
    radar = ctx.radars.get("shared")
    entry = {"key": "k1", "title": "Acme", "sector": radar.schema.sectors[0].name, "ring": radar.schema.rings[0].name}
    s, body, _ = api.call(
        "PUT", "/api/radars/shared/entries", {"entries": [entry], "expected_version": 1}, role="editor"
    )
    assert s == 200 and body["editors"] == [] and "secret.person" not in str(body)
    s, body, _ = api.call("PUT", "/api/radars/shared/entries", {"entries": [entry], "expected_version": 2})
    assert s == 200 and "secret.person@example.com" in body["editors"]  # an administrator still sees them


# ---- CR-07: the dry run says when the import would empty the radar ----------------------------


def test_cr07_dry_run_reports_an_empty_replace(api: Client, ctx: TestContext) -> None:
    ctx.radars.create(_admin(), "r", "R", template="blank")
    radar = ctx.radars.get("r")
    row = f"title,sector,ring\nAcme,{radar.schema.sectors[0].name},{radar.schema.rings[0].name}\n"
    api.call("POST", "/api/radars/r/import", {"format": "csv", "text": row, "expected_version": 1, "mode": "replace"})
    empty = {"format": "csv", "text": "title,sector,ring\n", "dry_run": True}
    s, body, _ = api.call("POST", "/api/radars/r/import", {**empty, "mode": "replace"})
    assert s == 200 and body["empty"] is True and body["rows"] == 0
    s, body, _ = api.call("POST", "/api/radars/r/import", {**empty, "mode": "merge"})
    assert s == 200 and body["empty"] is False and body["rows"] == 1
