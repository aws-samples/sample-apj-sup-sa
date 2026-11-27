# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import (
    BudgetExhaustedError,
    ConflictError,
    GenAiDisabledError,
    NotFoundError,
    ValidationError,
)
from insights_radar.core.tokens import TokenUsage
from insights_radar.graph.registry import NodeType
from insights_radar.identity.run_token import LocalHmac, RunTokenService
from insights_radar.modules.catalog import DEFAULT_MODEL_KEY, ModelCatalog
from insights_radar.modules.model import (
    ALWAYS_TOOLS,
    McpServer,
    ModuleDefinition,
    ModuleTemplate,
    ModuleValidator,
    Source,
    SourceType,
    ToolGrant,
)
from insights_radar.modules.repository import ModuleRepository, ModuleRun, RunStatus
from insights_radar.modules.service import ModuleService
from insights_radar.modules.templates import builtin_modules


class FakeLauncher:
    def __init__(self, fail: bool = False) -> None:
        self.stopped: list[tuple[str, str]] = []
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def launch(self, run: ModuleRun, payload: dict[str, Any]) -> str:
        if self.fail:
            raise RuntimeError("sfn down")
        self.calls.append(payload)
        return f"arn:aws:states:us-east-1:111122223333:execution:mr:{run.run_id}"

    def stop(self, execution_arn: str, cause: str) -> None:
        self.stopped.append((execution_arn, cause))


@pytest.fixture
def repo(ops_table: Any, clock: FixedClock) -> ModuleRepository:
    r = ModuleRepository(ops_table, clock)
    r.seed_builtins()
    return r


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def svc(repo: ModuleRepository, clock: FixedClock, launcher: FakeLauncher) -> ModuleService:
    return ModuleService(repo, RunTokenService(LocalHmac(b"k" * 32), clock), launcher, clock)


def custom(**kw: Any) -> ModuleDefinition:
    base: dict[str, Any] = dict(
        slug="competitor-watch",
        name="Competitor watch",
        template=ModuleTemplate.CUSTOM,
        output_kind=NodeType.INSIGHT,
        insight_kind="competitor_move",
        insight_label="Competitor moves",
        instructions="Watch competitor launches.",
        grants=frozenset({ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT, ToolGrant.WEB_SEARCH}),
        input_types=(NodeType.EVENT,),
    )
    base.update(kw)
    return ModuleDefinition(**base)


# ---- catalog -------------------------------------------------------------------------


def test_catalog() -> None:
    assert ModelCatalog.get(None).key == DEFAULT_MODEL_KEY and ModelCatalog.get("nope").key == DEFAULT_MODEL_KEY
    assert ModelCatalog.require("haiku-4-5").supports_temperature
    with pytest.raises(ValidationError):
        ModelCatalog.require("gpt-9")
    pub = ModelCatalog.public()
    assert sum(1 for m in pub if m["default"]) == 1
    assert ModelCatalog.usd_estimate(ModelCatalog.get("sonnet-5-5"), 1_000_000, 0) == 2.0


# ---- builtins + validation -----------------------------------------------------------


def test_builtins_seeded_disabled_and_valid(repo: ModuleRepository) -> None:
    mods = {m.slug: m for m in repo.list_all()}
    assert set(mods) == {"scout", "signal", "trend", "gap", "content", "draft", "curator"}
    for m in mods.values():
        assert not m.enabled and m.builtin
        ModuleValidator.validate(m)
        assert m.tools >= ALWAYS_TOOLS
    assert "source_update_cursor" in mods["scout"].tools and "radar_suggest" in mods["curator"].tools
    assert "radar_suggest" not in mods["signal"].tools and "kg_save_output" not in mods["scout"].tools
    assert mods["draft"].interval_minutes == 0


def test_seed_does_not_overwrite_edits(repo: ModuleRepository) -> None:
    m = repo.get("signal")
    repo.update("signal", {"enabled": True, "instructions": "Mine"}, m.version)
    assert repo.seed_builtins().created == []
    assert repo.get("signal").instructions == "Mine" and repo.get("signal").enabled


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"slug": "Bad Slug"}, "slug must"),
        ({"slug": "admin"}, "reserved"),
        ({"name": ""}, "name must"),
        ({"name": "x" * 81}, "name must"),
        ({"description": "d" * 1001}, "description"),
        ({"instructions": "i" * 20_001}, "instructions"),
        ({"insight_kind": None}, "insight_kind"),
        ({"insight_kind": "Bad-Kind"}, "insight_kind"),
        ({"insight_label": " "}, "insight_label"),
        ({"output_kind": NodeType.FACT}, "output_kind"),
        ({"model_key": "gpt-9"}, "unknown model"),
        ({"interval_minutes": 5}, "at least 15"),
        ({"interval_minutes": -1}, "interval_minutes"),
        ({"interval_minutes": 7 * 24 * 60 + 1}, "interval_minutes"),
        ({"daily_token_budget": -5}, "budget"),
        ({"daily_token_budget": 10**9}, "budget"),
        ({"max_concurrency": 0}, "max_concurrency"),
        ({"max_concurrency": 11}, "max_concurrency"),
        ({"grants": frozenset({ToolGrant.KG_READ})}, "kg_write_output"),
        ({"grants": frozenset({ToolGrant.KG_WRITE_OUTPUT})}, "kg_read"),
        (
            {"grants": frozenset({ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT, ToolGrant.RADAR_SUGGEST})},
            "radar_suggest is not available",
        ),
        (
            {"grants": frozenset({ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT, ToolGrant.CONTENT_DRAFT})},
            "content_draft is not available",
        ),
        ({"mcp_servers": (McpServer("x", "https://mcp.example.com/mcp"),)}, "needs the mcp grant"),
        ({"input_types": tuple([NodeType.FACT] * 17)}, "too many input types"),
    ],
)
def test_custom_validation(kw: dict[str, Any], msg: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ModuleValidator.validate(custom(**kw))
    assert any(msg in e for e in exc.value.details["errors"])


@pytest.mark.parametrize(
    ("server", "msg"),
    [
        (McpServer("ok", "http://mcp.example.com"), "https"),
        (McpServer("ok", "https://10.0.0.1/mcp"), "private"),
        (McpServer("bad name!", "https://mcp.example.com"), "must be 1-40"),
    ],
)
def test_mcp_validation(server: McpServer, msg: str) -> None:
    grants = frozenset({ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT, ToolGrant.MCP})
    with pytest.raises(ValidationError) as exc:
        ModuleValidator.validate(custom(grants=grants, mcp_servers=(server,)))
    assert any(msg in e for e in exc.value.details["errors"])


def test_mcp_duplicates_and_cap() -> None:
    grants = frozenset({ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT, ToolGrant.MCP})
    dup = (McpServer("a", "https://a.example.com"), McpServer("a", "https://b.example.com"))
    with pytest.raises(ValidationError):
        ModuleValidator.validate(custom(grants=grants, mcp_servers=dup))
    many = tuple(McpServer(f"s{i}", f"https://s{i}.example.com") for i in range(11))
    with pytest.raises(ValidationError):
        ModuleValidator.validate(custom(grants=grants, mcp_servers=many))
    ok = ModuleValidator.validate(custom(grants=grants, mcp_servers=dup[:1]))
    assert ok.mcp_servers == dup[:1]


def test_scout_and_curator_must_not_have_output() -> None:
    m = builtin_modules()[0]
    with pytest.raises(ValidationError):
        ModuleValidator.validate(replace(m, output_kind=NodeType.SIGNAL))


# ---- custom CRUD -------------------------------------------------------------------------


def test_custom_create_update_delete(repo: ModuleRepository, clock: FixedClock) -> None:
    m = repo.create(custom(), "user:admin")
    assert m.version == 1 and repo.get(m.slug).insight_label == "Competitor moves"
    with pytest.raises(ConflictError):
        repo.create(custom(), "user:admin")
    with pytest.raises(ConflictError, match="insight_kind"):
        repo.create(custom(slug="other"), "user:admin")
    clock.advance(timedelta(seconds=1))
    u = repo.update(m.slug, {"enabled": True, "interval_minutes": 60}, 1)
    assert u.version == 2 and u.enabled and u.updated_at > m.updated_at
    with pytest.raises(ConflictError):
        repo.update(m.slug, {"enabled": False}, 1)
    with pytest.raises(ValidationError, match="cannot be changed"):
        repo.update(m.slug, {"template": "scout"}, 2)
    repo.delete(m.slug)
    with pytest.raises(NotFoundError):
        repo.get(m.slug)


def test_create_rejects_builtin_shapes(repo: ModuleRepository) -> None:
    with pytest.raises(ValidationError):
        repo.create(builtin_modules()[1], "u")
    with pytest.raises(ValidationError):
        repo.create(custom(output_kind=NodeType.SIGNAL, insight_kind=None), "u")


def test_builtin_cannot_be_deleted(repo: ModuleRepository) -> None:
    with pytest.raises(ValidationError):
        repo.delete("scout")


# ---- sources -------------------------------------------------------------------------


def test_source_crud(repo: ModuleRepository) -> None:
    s = repo.add_source(
        "scout", "Desk forum", SourceType.WEB_PAGE, "https://forum.example.com/desks", "Read new threads"
    )
    assert repo.list_sources("scout") == [s]
    u = repo.update_source("scout", s.id, {"enabled": False, "type": "rss"})
    assert u.type is SourceType.RSS and not u.enabled
    # r2 B2-031: a paused source's cursor cannot be moved, so a run cannot skip what an admin
    # stopped for review.
    with pytest.raises(NotFoundError):
        repo.update_cursor("scout", s.id, "after post 4521")
    repo.update_source("scout", s.id, {"enabled": True})
    c = repo.update_cursor("scout", s.id, "after post 4521")
    assert c.cursor == "after post 4521"
    with pytest.raises(ValidationError):
        repo.update_cursor("scout", s.id, "x" * 2001)
    with pytest.raises(NotFoundError):
        repo.update_cursor("scout", "nosuchid00", "x")
    with pytest.raises(ValidationError, match="cannot be changed"):
        repo.update_source("scout", s.id, {"id": "x"})
    for bad, message in (
        ({"name": None}, "a string"),
        ({"enabled": "yes"}, "true or false"),
        ({"type": "carrier-pigeon"}, "unknown source type"),
    ):
        with pytest.raises(ValidationError, match=message):
            repo.update_source("scout", s.id, bad)
    repo.delete_source("scout", s.id)
    with pytest.raises(NotFoundError):
        repo.delete_source("scout", s.id)


@pytest.mark.parametrize(
    ("addr", "name"),
    [("http://x.com", "ok"), ("https://127.0.0.1", "ok"), ("https://ok.example.com", ""), ("", "ok")],
)
def test_source_validation(repo: ModuleRepository, addr: str, name: str) -> None:
    with pytest.raises(ValidationError):
        repo.add_source("scout", name, SourceType.WEB_PAGE, addr)


def test_sources_only_for_source_modules(repo: ModuleRepository) -> None:
    with pytest.raises(ValidationError):
        repo.add_source("signal", "x", SourceType.WEB_PAGE, "https://x.example.com")


def test_source_object_validation() -> None:
    with pytest.raises(ValidationError):
        Source.validate(Source("BAD", "scout", "n", SourceType.RSS, "https://x.example.com"))
    with pytest.raises(ValidationError):
        Source.validate(Source("aabbccdd00", "scout", "n", SourceType.RSS, "https://x.example.com", "i" * 4001))


# ---- progress --------------------------------------------------------------------------


def test_progress_history_window(repo: ModuleRepository) -> None:
    for i, remaining in enumerate([5, 10, 15, 32, 45, 60]):
        repo.begin_progress_slot("signal")
        repo.record_progress("signal", f"2026-10-0{i + 1}T00:00:00.000Z", remaining)
    p = repo.record_progress("signal", "2026-09-01T00:00:00.000Z", 44)  # same run: replaces its slot
    assert p.remaining_history == (44, 45, 32, 15, 10)
    assert repo.record_progress("signal", None, None).remaining_history == (44, 45, 32, 15, 10)
    assert p.last_processed_at == "2026-10-06T00:00:00.000Z"  # never moves backwards
    with pytest.raises(ValidationError):
        repo.record_progress("signal", None, -1)


def test_progress_never_overwrites_a_dispatch_stamp(repo: ModuleRepository) -> None:
    before = repo.progress("signal")  # a progress write that read before the dispatch...
    assert repo.claim_dispatch("signal", "2026-10-02T08:00:00.000Z", "2026-10-02T02:00:00.000Z")
    repo.record_progress("signal", "2026-10-02T07:00:00.000Z", 3)  # ...keeps the stamp
    assert before.last_dispatched_at is None
    assert repo.progress("signal").last_dispatched_at == "2026-10-02T08:00:00.000Z"


def test_claim_dispatch_is_exclusive(repo: ModuleRepository) -> None:
    assert repo.claim_dispatch("signal", "2026-10-02T08:00:00.000Z", "2026-10-02T02:00:00.000Z")
    assert not repo.claim_dispatch("signal", "2026-10-02T08:01:00.000Z", "2026-10-02T02:01:00.000Z")
    assert repo.claim_dispatch("signal", "2026-10-02T14:00:00.000Z", "2026-10-02T08:00:00.000Z")


# ---- budgets + andon -------------------------------------------------------------------


def test_budget_charge_and_reset_by_day(repo: ModuleRepository, clock: FixedClock) -> None:
    assert repo.charge("signal", TokenUsage()) == 0
    assert repo.charge("signal", TokenUsage(100, 10)) == 150
    assert repo.charge("signal", TokenUsage(0, 0, 1000)) == 250
    assert repo.budget("signal", 1000).consumed == 250
    clock.advance(timedelta(days=1))
    assert repo.budget("signal", 1000).consumed == 0


def test_andon_default_set_and_validation(repo: ModuleRepository) -> None:
    assert repo.andon().genai_enabled
    off = repo.set_andon(False, "incident", "user:a")
    assert not repo.andon().genai_enabled and repo.andon().reason == "incident" and off.updated_by == "user:a"
    with pytest.raises(ValidationError):
        repo.set_andon("no", None, "u")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        repo.set_andon(True, "r" * 501, "u")


def test_andon_fails_closed_on_read_error(repo: ModuleRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    def boom(**_: Any) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "GetItem")

    monkeypatch.setattr(repo._t, "get_item", boom)
    assert not repo.andon().genai_enabled


# ---- service ----------------------------------------------------------------------------


def test_on_demand_run_and_finalize(svc: ModuleService, repo: ModuleRepository, launcher: FakeLauncher) -> None:
    run = svc.start("draft", "manual", "user:e", target_id="content_idea/x")
    assert run.status is RunStatus.RUNNING and run.execution_arn
    payload = launcher.calls[0]
    assert payload["module"]["slug"] == "draft" and payload["target_id"] == "content_idea/x"
    claims = svc._tokens.verify(payload["run_token"])
    assert claims.module == "draft" and "draft_save" in claims.tools and "radar_suggest" not in claims.tools
    done = svc.finalize("draft", run.run_id, RunStatus.SUCCEEDED, TokenUsage(1000, 100), "ok", nodes_written=1)
    assert done.status is RunStatus.SUCCEEDED and done.weighted_tokens == 1500
    again = svc.finalize("draft", run.run_id, RunStatus.FAILED, TokenUsage(5, 5))
    assert again.status is RunStatus.SUCCEEDED  # idempotent
    assert repo.budget("draft", 10_000).consumed == 1500
    with pytest.raises(ValidationError):
        svc.finalize("draft", run.run_id, RunStatus.RUNNING, TokenUsage())
    runs, _ = repo.list_runs()
    assert runs[0].run_id == run.run_id and repo.list_runs("draft")[0][0].to_public()["status"] == "succeeded"


def test_scheduled_run_requires_enabled(svc: ModuleService) -> None:
    with pytest.raises(ValidationError, match="disabled"):
        svc.start("signal", "schedule", "system")


def test_gates(svc: ModuleService, repo: ModuleRepository) -> None:
    repo.set_andon(False, "x", "u")
    with pytest.raises(GenAiDisabledError):
        svc.start("signal", "manual", "u")
    repo.set_andon(True, None, "u")
    repo.update("signal", {"daily_token_budget": 100}, repo.get("signal").version)
    repo.charge("signal", TokenUsage(100))
    with pytest.raises(BudgetExhaustedError):
        svc.start("signal", "manual", "u")
    repo.update("trend", {"max_concurrency": 1}, repo.get("trend").version)
    svc.start("trend", "manual", "u")
    with pytest.raises(ConflictError):
        svc.start("trend", "manual", "u")


def test_launch_failure_marks_run_failed(repo: ModuleRepository, clock: FixedClock) -> None:
    svc = ModuleService(repo, RunTokenService(LocalHmac(b"k" * 32), clock), FakeLauncher(fail=True), clock)
    with pytest.raises(RuntimeError):
        svc.start("signal", "manual", "u")
    runs, _ = repo.list_runs("signal")
    assert runs[0].status is RunStatus.FAILED and "could not start" in (runs[0].error or "")


def test_sweep(svc: ModuleService, repo: ModuleRepository, clock: FixedClock) -> None:
    for slug in ("signal", "trend"):
        repo.update(slug, {"enabled": True}, repo.get(slug).version)
    repo.update("draft", {"enabled": True}, repo.get("draft").version)
    decisions = {d.module: d for d in svc.sweep()}
    # Upstream first: Trends reads what Signals writes, so it starts on the next tick (CTR HC-045/057).
    assert decisions["signal"].dispatched and decisions["trend"].reason == "waiting for an earlier stage"
    assert decisions["draft"].reason == "on-demand only" and decisions["scout"].reason == "disabled"
    clock.advance(timedelta(minutes=5))
    assert {d.module: d for d in svc.sweep()}["trend"].dispatched
    clock.advance(timedelta(minutes=5))
    for slug in ("signal", "trend"):
        run = repo.list_runs(slug)[0][0]
        svc.finalize(slug, run.run_id, RunStatus.SUCCEEDED, TokenUsage(10, 10, 0, 0))
    again = {d.module: d for d in svc.sweep()}
    assert again["signal"].reason == "interval not elapsed"
    clock.advance(timedelta(hours=6))
    assert {d.module: d for d in svc.sweep()}["signal"].dispatched
    repo.set_andon(False, None, "u")
    assert svc.sweep()[0].reason == "genai disabled"


def test_hc039_the_scheduler_keeps_to_a_deployment_wide_run_cap(repo: ModuleRepository, clock: FixedClock) -> None:
    svc = ModuleService(repo, RunTokenService(LocalHmac(b"k" * 32), clock), FakeLauncher(), clock, max_live_runs=1)
    for slug in ("signal", "trend"):
        repo.update(slug, {"enabled": True}, repo.get(slug).version)
    first = {d.module: d for d in svc.sweep()}
    assert first["signal"].dispatched
    clock.advance(timedelta(minutes=5))
    # Signals is still running: the cap holds Trends back, and the slot stays free for the next tick.
    assert {d.module: d for d in svc.sweep()}["trend"].reason == "deployment run cap reached"
    run = repo.list_runs("signal")[0][0]
    svc.finalize("signal", run.run_id, RunStatus.SUCCEEDED, TokenUsage(10, 10, 0, 0))
    assert repo.live_runs("2000-01-01T00:00:00Z") == 0
    assert {d.module: d for d in svc.sweep()}["trend"].dispatched


def test_sweep_reports_budget(svc: ModuleService, repo: ModuleRepository) -> None:
    repo.update("signal", {"enabled": True, "daily_token_budget": 10}, repo.get("signal").version)
    repo.charge("signal", TokenUsage(10))
    d = {x.module: x for x in svc.sweep()}
    assert d["signal"].reason == "budget_exhausted" and not d["signal"].dispatched


def test_get_run_missing(repo: ModuleRepository) -> None:
    with pytest.raises(NotFoundError):
        repo.get_run("signal", "nope")


def test_each_run_holds_its_share_of_the_budget_until_it_finishes(svc: ModuleService, repo: ModuleRepository) -> None:
    repo.update("trend", {"daily_token_budget": 10_000, "max_concurrency": 2}, repo.get("trend").version)
    first = svc.start("trend", "manual", "u")
    assert first.budget_hold == 5_000 and first.hold_day
    assert repo.budget("trend", 10_000).remaining == 5_000  # the hold counts while the run is going
    second = svc.start("trend", "manual", "u")
    assert second.budget_hold == 2_500  # half of what is left now, not the whole budget again
    assert repo.budget("trend", 10_000).remaining == 2_500
    svc.finalize("trend", first.run_id, RunStatus.SUCCEEDED, TokenUsage(1_000))
    assert repo.budget("trend", 10_000).remaining == 6_500  # hold swapped for the 1,000 actually used
    svc.finalize("trend", first.run_id, RunStatus.SUCCEEDED, TokenUsage(1_000))  # a retried finalize
    assert repo.budget("trend", 10_000).remaining == 6_500
    done = svc.finalize("trend", second.run_id, RunStatus.FAILED, TokenUsage())
    # It reported nothing (a crash or a timed-out task), so whatever it spent is unknown: the hold stays.
    assert repo.budget("trend", 10_000).remaining == 6_500 and done.weighted_tokens == 2_500
    third = svc.start("trend", "manual", "u")
    svc.finalize("trend", third.run_id, RunStatus.FAILED, TokenUsage(500))
    assert repo.budget("trend", 10_000).remaining == 6_000  # a failure that reported usage is charged that


def test_a_failed_launch_gives_its_hold_back(repo: ModuleRepository, clock: FixedClock) -> None:
    svc = ModuleService(repo, RunTokenService(LocalHmac(b"k" * 32), clock), FakeLauncher(fail=True), clock)
    repo.update("signal", {"daily_token_budget": 1_000}, repo.get("signal").version)
    with pytest.raises(RuntimeError):
        svc.start("signal", "manual", "u")
    assert repo.budget("signal", 1_000).remaining == 1_000


def test_a_refused_hold_is_reported_as_budget_exhausted(
    svc: ModuleService, repo: ModuleRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repo, "reserve", lambda slug, amount, limit: None)  # another run took it first
    with pytest.raises(BudgetExhaustedError):
        svc.start("signal", "manual", "u")


def test_runs_from_before_holds_finalize_as_before(svc: ModuleService, repo: ModuleRepository) -> None:
    run = svc.start("signal", "manual", "u")
    repo.put_run(replace(run, budget_hold=0, hold_day=None))
    svc.finalize("signal", run.run_id, RunStatus.SUCCEEDED, TokenUsage(10))
    assert repo.get_run("signal", run.run_id).weighted_tokens == 10


def test_a_failure_after_the_reservation_gives_the_hold_back(
    svc: ModuleService, repo: ModuleRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.update("signal", {"daily_token_budget": 1_000}, repo.get("signal").version)
    monkeypatch.setattr(repo, "put_run", lambda run: (_ for _ in ()).throw(RuntimeError("ddb down")))
    with pytest.raises(RuntimeError):
        svc.start("signal", "manual", "u")
    assert repo.budget("signal", 1_000).remaining == 1_000


def test_a_failure_after_the_run_is_stored_marks_it_failed(
    svc: ModuleService, repo: ModuleRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.update("signal", {"daily_token_budget": 1_000}, repo.get("signal").version)
    monkeypatch.setattr(repo, "begin_progress_slot", lambda slug: (_ for _ in ()).throw(RuntimeError("throttled")))
    with pytest.raises(RuntimeError):
        svc.start("signal", "manual", "u")
    runs, _ = repo.list_runs("signal")
    assert [r.status for r in runs] == [RunStatus.FAILED]  # not a QUEUED run blocking the module for hours
    assert runs[0].error == "could not start run: RuntimeError"
    assert repo.budget("signal", 1_000).remaining == 1_000


def test_a_run_finalized_before_launch_returns_keeps_its_terminal_record(
    svc: ModuleService, repo: ModuleRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = svc._launcher.launch

    def fails_at_once(run: Any, payload: Any) -> str:
        arn = real(run, payload)
        svc.finalize(run.module, run.run_id, RunStatus.FAILED, TokenUsage(0))  # the execution's catch ran first
        return arn

    monkeypatch.setattr(svc._launcher, "launch", fails_at_once)
    run = svc.start("signal", "manual", "u")
    assert run.status is RunStatus.FAILED
    assert repo.get_run("signal", run.run_id).status is RunStatus.FAILED  # not overwritten with RUNNING


def test_two_racing_finalizes_charge_the_run_once(
    svc: ModuleService, repo: ModuleRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.update("trend", {"daily_token_budget": 10_000}, repo.get("trend").version)
    run = svc.start("trend", "manual", "u")
    real_get = repo.get_run
    stale = real_get("trend", run.run_id)  # both invocations read it while it was still running
    first = svc.finalize("trend", run.run_id, RunStatus.SUCCEEDED, TokenUsage(1_000))
    calls: list[int] = []

    def second_read(slug: str, rid: str) -> Any:
        calls.append(1)
        return stale if len(calls) == 1 else real_get(slug, rid)

    monkeypatch.setattr(repo, "get_run", second_read)
    again = svc.finalize("trend", run.run_id, RunStatus.SUCCEEDED, TokenUsage(1_000))
    assert again.status is RunStatus.SUCCEEDED and first.weighted_tokens == again.weighted_tokens
    assert repo.budget("trend", 10_000).remaining == 9_000  # charged once, hold released once


def test_other_errors_finishing_a_run_are_raised(repo: ModuleRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    def boom(**kw: Any) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

    monkeypatch.setattr(repo._t, "put_item", boom)
    run = ModuleRun("trend", "r", RunStatus.SUCCEEDED, "manual", "2026-10-05T00:00:00Z", "u")
    with pytest.raises(ClientError):
        repo.finish_run(run, RunStatus.RUNNING)


def test_p2062_trends_has_no_outbound_grant() -> None:
    """Trends works from the graph only; its prompt never searches, so it holds no web tool (r2 P2-062)."""
    from insights_radar.modules.model import ToolGrant
    from insights_radar.modules.templates import builtin_modules

    trend = next(m for m in builtin_modules() if m.slug == "trend")
    outbound = {ToolGrant.WEB_SEARCH, ToolGrant.WEB_FETCH, ToolGrant.BROWSER, ToolGrant.CODE_INTERPRETER}
    assert not trend.grants & outbound


def test_hc043_a_run_is_refused_at_90_percent_of_the_budget(svc: ModuleService, repo: ModuleRepository) -> None:
    repo.update("signal", {"daily_token_budget": 100}, repo.get("signal").version)
    repo.charge("signal", TokenUsage(89))
    svc.start("signal", "manual", "u")  # at 89% a run still starts
    repo.update("trend", {"daily_token_budget": 100}, repo.get("trend").version)
    repo.charge("trend", TokenUsage(90))
    with pytest.raises(BudgetExhaustedError, match="90% or more") as err:
        svc.start("trend", "manual", "u")
    assert err.value.details["state"] == "half_open"


def test_hc044_a_sweep_a_few_seconds_early_still_dispatches(
    svc: ModuleService, repo: ModuleRepository, clock: FixedClock
) -> None:
    repo.update("signal", {"enabled": True}, repo.get("signal").version)
    interval = timedelta(minutes=repo.get("signal").interval_minutes)
    assert {d.module: d for d in svc.sweep()}["signal"].dispatched
    run = repo.list_runs("signal")[0][0]
    svc.finalize("signal", run.run_id, RunStatus.SUCCEEDED, TokenUsage(10, 10, 0, 0))
    clock.advance(interval - timedelta(seconds=5))  # EventBridge fired this sweep 5 seconds early
    assert {d.module: d for d in svc.sweep()}["signal"].dispatched
    run = repo.list_runs("signal")[0][0]
    svc.finalize("signal", run.run_id, RunStatus.SUCCEEDED, TokenUsage(10, 10, 0, 0))
    clock.advance(interval - timedelta(minutes=5))  # a whole sweep early is still too early
    assert {d.module: d for d in svc.sweep()}["signal"].reason == "interval not elapsed"


def test_a_stored_schema_that_no_longer_parses_is_dropped(repo: ModuleRepository, ops_table: Any, caplog: Any) -> None:
    ops_table.update_item(
        Key={"pk": "MODULE#signal", "sk": "DEF"},
        UpdateExpression="SET attribute_schema = :s",
        ExpressionAttributeValues={":s": [{"key": "x", "type": "colour"}]},
    )
    m = repo.get("signal")
    assert not m.attribute_schema and "attribute_schema_invalid_on_read" in caplog.text


def test_a_curator_may_not_read_product_knowledge() -> None:
    """Round 37 security review: radar suggestions and entries are shown to every viewer with no private
    stamp, so a curator given product_knowledge could quote the roadmap to viewers."""
    from insights_radar.modules.model import PRIVATE_SUGGEST_MESSAGE, ModuleValidator, ToolGrant
    from insights_radar.modules.templates import builtin_modules

    (curator,) = [m for m in builtin_modules() if ToolGrant.RADAR_SUGGEST in m.grants]
    assert curator.run_refusal is None and ModuleValidator.validate(curator)
    reading = replace(curator, grants=curator.grants | {ToolGrant.PRODUCT_KNOWLEDGE})
    assert reading.run_refusal == PRIVATE_SUGGEST_MESSAGE  # a definition stored before the rule cannot run
    with pytest.raises(ValidationError) as err:
        ModuleValidator.validate(reading)
    assert PRIVATE_SUGGEST_MESSAGE in str(err.value.details)


def test_a_scheduled_run_leaves_room_for_the_days_later_slots(
    svc: ModuleService, repo: ModuleRepository, clock: FixedClock
) -> None:
    # Daily check 2026-11-10: Scout every 4 hours on an 8M day, about 2M a run; the 00:03 run held the whole
    # day and the 16:03 run was cut off after 59 s. A scheduled run now holds its slot's share of what is left.
    clock.advance(timedelta(hours=-clock.now().hour, minutes=-clock.now().minute, seconds=-clock.now().second))
    clock.advance(timedelta(minutes=3))  # 00:03 UTC
    repo.update(
        "trend", {"daily_token_budget": 6_000, "interval_minutes": 240, "enabled": True}, repo.get("trend").version
    )
    assert svc.slots_left_today(240) == 6
    first = svc.start("trend", "schedule", "system")
    assert first.budget_hold == 1_000  # 6,000 over the 6 slots left today
    svc.finalize("trend", first.run_id, RunStatus.SUCCEEDED, TokenUsage(400))
    clock.advance(timedelta(hours=20))  # 20:03: one slot left
    assert svc.slots_left_today(240) == 1
    last = svc.start("trend", "schedule", "system")
    assert last.budget_hold == 5_600  # the rest of the day
    svc.finalize("trend", last.run_id, RunStatus.SUCCEEDED, TokenUsage(10))
    manual = svc.start("trend", "manual", "u")  # by hand: the whole remainder, as before
    assert manual.budget_hold == 5_590
    assert svc.slots_left_today(0) >= 1  # an on-demand module (interval 0) counts one slot
