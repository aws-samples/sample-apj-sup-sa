# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Remaining validator and branch cases, grouped by module."""

from __future__ import annotations

import base64
import json
from typing import Any

import boto3
import pytest

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import UnauthorizedError, ValidationError
from insights_radar.core.urls import UrlGuard
from insights_radar.graph.model import ALIASES_MAX, DESCRIPTION_MAX, Node, NodeFactory
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.graph.repository import GraphRepository
from insights_radar.graph.search import HashEmbedder, HybridSearch, InMemoryVectorIndex
from insights_radar.identity.principal import Role
from insights_radar.identity.run_token import LocalHmac, RunTokenService
from insights_radar.identity.users import UserAdmin
from insights_radar.insights.service import InsightService
from insights_radar.modules.model import Source, SourceType
from insights_radar.modules.repository import ModuleRepository, ModuleRun
from insights_radar.modules.service import ModuleService, StepFunctionsLauncher

URL = "https://news.example.com/x"

# ---- urls / graph model -----------------------------------------------------------------------


@pytest.mark.parametrize("url", ["https://1.2.65536/", "https://1.16777216/", "https://[::1/x"])
def test_url_guard_rejects_overflowing_and_unparseable(url: str) -> None:
    assert not UrlGuard.is_safe(url)


@pytest.mark.parametrize(
    ("node", "message"),
    [
        (Node(id="signal/1", type=NodeType.SIGNAL, title="t", attributes="x"), "attributes must be an object"),
        (
            Node(id="signal/1", type=NodeType.SIGNAL, title="t", description="<p>" + "d" * DESCRIPTION_MAX + "</p>"),
            "description must be at most",
        ),
        (
            Node(id="topic/1", type=NodeType.TOPIC, title="t", aliases=tuple(f"a{i}" for i in range(ALIASES_MAX + 1))),
            "aliases",
        ),
        (Node(id="signal/1", type=NodeType.SIGNAL, title="t", evidence_url="http://10.0.0.1/"), "public https"),
    ],
)
def test_node_factory_rejections(node: Node, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        NodeFactory.validate(node)


# ---- run tokens ---------------------------------------------------------------------------------


def test_run_token_rejects_well_signed_but_malformed_claims(clock: FixedClock) -> None:
    mac = LocalHmac(b"k" * 32)
    svc = RunTokenService(mac, clock)
    payload = json.dumps({"module": "m", "run_id": "r", "exp": "soon", "tools": []}).encode()

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    with pytest.raises(UnauthorizedError, match="claims are invalid"):
        svc.verify(f"{b64(payload)}.{b64(mac.generate(payload))}")


# ---- users ----------------------------------------------------------------------------------------


@pytest.fixture
def users(aws: Any) -> tuple[UserAdmin, Any, str]:
    c = boto3.client("cognito-idp", region_name="us-east-1")
    pool = c.create_user_pool(PoolName="p")["UserPool"]["Id"]
    for g in ("admin", "editor", "viewer"):
        c.create_group(UserPoolId=pool, GroupName=g)
    return UserAdmin(c, pool), c, pool


def test_list_users_pages_and_resolves_roles_in_bulk(users: tuple[UserAdmin, Any, str]) -> None:
    admin, _, _ = users
    for i in range(65):
        admin.invite(f"u{i}@example.com", Role.EDITOR if i % 2 else Role.VIEWER)
    admin.invite("boss@example.com", Role.ADMIN)
    listed = admin.list_page(500)[0]
    assert len(listed) == 66
    roles = {u.email: u.role for u in listed}
    assert roles["boss@example.com"] is Role.ADMIN and roles["u1@example.com"] is Role.EDITOR
    assert roles["u0@example.com"] is Role.VIEWER
    assert len(admin.list_page(10)[0]) == 10


def test_role_changes_and_reenable(users: tuple[UserAdmin, Any, str]) -> None:
    admin, _, _ = users
    a = admin.invite("a@example.com", Role.ADMIN)
    admin.invite("b@example.com", Role.ADMIN)
    assert admin.set_role(a.username, Role.EDITOR).role is Role.EDITOR
    assert admin.set_role(a.username, Role.VIEWER).role is Role.VIEWER
    assert admin.set_role(a.username, Role.VIEWER).role is Role.VIEWER
    v = admin.set_enabled(a.username, False)
    assert not v.enabled
    assert admin.set_enabled(a.username, True).enabled


# ---- insights -------------------------------------------------------------------------------------


@pytest.fixture
def insights(graph_table: Any, ops_table: Any, clock: FixedClock) -> tuple[InsightService, GraphRepository]:
    repo = GraphRepository(graph_table, clock)
    return InsightService(repo, HybridSearch(repo, InMemoryVectorIndex(), HashEmbedder()), ops_table, clock), repo


def test_detail_groups_other_edges_skips_missing_and_briefs_insights(
    insights: tuple[InsightService, GraphRepository], graph_table: Any
) -> None:
    svc, repo = insights
    a, _ = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    b, _ = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Beta"), "u")
    gone, _ = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Gone"), "u")
    fact = repo.create(Node(id="", type=NodeType.FACT, title="Acme ships", evidence_url=URL), "u")
    move = repo.create(Node(id="", type=NodeType.INSIGHT, title="Acme moves", insight_kind="competitor_move"), "u")
    repo.link(a.id, EdgeType.COMPETES_WITH, b.id, "u")
    repo.link(a.id, EdgeType.COMPETES_WITH, gone.id, "u")
    repo.link(fact.id, EdgeType.ABOUT, a.id, "u")
    repo.link(move.id, EdgeType.SUPPORTS, fact.id, "u")
    graph_table.delete_item(Key={"pk": f"N#{gone.id}", "sk": "META"})
    d = svc.detail(a.id)
    assert [x["node"]["id"] for x in d["evidence"]["other"]] == [b.id, fact.id] or {
        x["node"]["id"] for x in d["evidence"]["other"]
    } == {b.id, fact.id}
    f = svc.detail(fact.id)
    assert f["evidence"]["supported_by"][0]["node"]["insight_kind"] == "competitor_move"


@pytest.mark.parametrize("raw", ["x:1", "o:-1", "o:"])
def test_offset_cursor_rejects_bad_text(insights: tuple[InsightService, GraphRepository], raw: str) -> None:
    from insights_radar.insights.service import InsightKind, Sort

    svc, _ = insights
    cursor = base64.urlsafe_b64encode(raw.encode()).decode()
    with pytest.raises(ValidationError, match="cursor"):
        svc.browse(InsightKind.parse("signals"), sort=Sort.CONNECTED, cursor=cursor)


# ---- modules --------------------------------------------------------------------------------------


def test_source_cursor_length() -> None:
    s = Source(
        id="s", module="scout", name="n", type=SourceType.RSS, address="https://a.example.com", cursor="c" * 2001
    )
    with pytest.raises(ValidationError) as err:
        Source.validate(s)
    assert any("cursor" in e for e in err.value.details["errors"])


def test_step_functions_launcher_names_execution() -> None:
    calls: list[dict[str, Any]] = []

    class _Sfn:
        def start_execution(self, **kw: Any) -> dict[str, Any]:
            calls.append(kw)
            return {"executionArn": "arn:x"}

    run = ModuleRun(
        "signal",
        "0190-abc",
        __import__("insights_radar.modules.repository", fromlist=["RunStatus"]).RunStatus.QUEUED,
        "manual",
        "t",
        "u",
    )
    assert StepFunctionsLauncher(_Sfn(), "arn:sm").launch(run, {"a": 1}) == "arn:x"
    assert calls[0]["name"] == "signal-0190-abc" and json.loads(calls[0]["input"]) == {"a": 1}


def test_launcher_stop_ignores_an_execution_that_is_gone() -> None:
    from botocore.exceptions import ClientError

    stopped: list[dict[str, Any]] = []

    class _Sfn:
        def __init__(self, code: str | None) -> None:
            self.code = code

        def stop_execution(self, **kw: Any) -> None:
            if self.code:
                raise ClientError({"Error": {"Code": self.code, "Message": "x"}}, "StopExecution")
            stopped.append(kw)

    StepFunctionsLauncher(_Sfn(None), "arn:sm").stop("arn:x", "c" * 400)
    assert stopped[0]["executionArn"] == "arn:x" and len(stopped[0]["cause"]) == 300
    StepFunctionsLauncher(_Sfn("ExecutionDoesNotExist"), "arn:sm").stop("arn:x", "c")
    with pytest.raises(ClientError):
        StepFunctionsLauncher(_Sfn("AccessDeniedException"), "arn:sm").stop("arn:x", "c")


def test_schedule_trigger_refuses_on_demand_module(ops_table: Any, clock: FixedClock) -> None:
    repo = ModuleRepository(ops_table, clock)
    repo.seed_builtins()
    draft = repo.get("draft")
    repo.update("draft", {"enabled": True}, draft.version)
    svc = ModuleService(repo, RunTokenService(LocalHmac(b"k" * 32), clock), None, clock)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="on demand"):
        svc.start("draft", "schedule", "system:scheduler")


def test_sweep_reports_lost_claim(ops_table: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = ModuleRepository(ops_table, clock)
    repo.seed_builtins()
    repo.update("signal", {"enabled": True, "interval_minutes": 60}, repo.get("signal").version)
    monkeypatch.setattr(repo, "claim_dispatch", lambda *a: False)
    svc = ModuleService(repo, RunTokenService(LocalHmac(b"k" * 32), clock), None, clock)  # type: ignore[arg-type]
    decision = next(d for d in svc.sweep() if d.module == "signal")
    assert decision.reason == "claimed by another sweep" and not decision.dispatched
