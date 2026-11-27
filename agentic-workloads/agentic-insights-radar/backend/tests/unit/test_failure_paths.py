# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Storage failure paths: throttling, unexpected AWS errors and multi-page reads.

``Chaos`` wraps a moto table and makes chosen operations raise a ``ClientError`` with a given
code, or forces every query to return one item per page so pagination loops run.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ConflictError
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.graph.repository import GraphRepository
from insights_radar.modules.repository import ModuleRepository, ModuleRun, RunStatus
from insights_radar.modules.templates import builtin_modules

URL = "https://news.example.com/a"


def _error(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class _Client:
    def __init__(self, real: Any, fail: dict[str, str]) -> None:
        self._real, self._fail = real, fail

    def __getattr__(self, name: str) -> Any:
        if name in self._fail:
            code = self._fail[name]

            def raiser(**kw: Any) -> Any:
                raise _error(code, name)

            return raiser
        return getattr(self._real, name)


class _Meta:
    def __init__(self, real: Any, fail: dict[str, str]) -> None:
        self.client = _Client(real.client, fail)


class Chaos:
    def __init__(self, table: Any, fail: dict[str, str] | None = None, page: bool = False) -> None:
        self._t, self._fail, self._page = table, fail or {}, page
        self.meta = _Meta(table.meta, self._fail)

    def __getattr__(self, name: str) -> Any:
        if name in self._fail:
            code = self._fail[name]

            def raiser(**kw: Any) -> Any:
                raise _error(code, name)

            return raiser
        attr = getattr(self._t, name)
        if name == "query" and self._page:
            return lambda **kw: attr(**{**kw, "Limit": 1})
        return attr


THROTTLE = "ProvisionedThroughputExceededException"


def _custom(*extra: Any) -> Any:
    from insights_radar.modules.model import ModuleDefinition, ModuleTemplate, ToolGrant

    return ModuleDefinition(
        slug="watch",
        name="Watch",
        description="Watches things",
        template=ModuleTemplate.CUSTOM,
        output_kind=NodeType.INSIGHT,
        insight_kind="watch_item",
        insight_label="Watch items",
        instructions="Find watch items in the graph.",
        input_types=(NodeType.FACT,),
        grants=frozenset({ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT, *extra}),
    )


# ---- graph ------------------------------------------------------------------------------------


def test_graph_create_reraises_unexpected_errors(graph_table: Any, clock: FixedClock) -> None:
    # r3 B3-052: an output and its listing are one transaction.
    repo = GraphRepository(Chaos(graph_table, {"transact_write_items": THROTTLE}), clock)
    with pytest.raises(ClientError):
        repo.create(Node(id="", type=NodeType.FACT, title="t", evidence_url=URL), "u")


def test_graph_upsert_reraises_unexpected_errors(graph_table: Any, clock: FixedClock) -> None:
    repo = GraphRepository(Chaos(graph_table, {"transact_write_items": THROTTLE}), clock)
    with pytest.raises(ClientError):
        repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")


def test_graph_update_reraises_unexpected_errors(graph_table: Any, clock: FixedClock) -> None:
    node = GraphRepository(graph_table, clock).create(Node(id="", type=NodeType.SIGNAL, title="s"), "u")
    repo = GraphRepository(Chaos(graph_table, {"update_item": THROTTLE}), clock)
    with pytest.raises(ClientError):
        repo.update(node.id, {"summary": "x"}, 1, "u")


def test_graph_link_reraises_unexpected_errors(graph_table: Any, clock: FixedClock) -> None:
    good = GraphRepository(graph_table, clock)
    a = good.create(Node(id="", type=NodeType.SIGNAL, title="s"), "u")
    b = good.create(Node(id="", type=NodeType.FACT, title="f", evidence_url=URL), "u")
    repo = GraphRepository(Chaos(graph_table, {"transact_write_items": THROTTLE}), clock)
    with pytest.raises(ClientError):
        repo.link(a.id, EdgeType.SUPPORTS, b.id, "u")


def test_graph_degree_bump_tolerates_missing_node_but_not_throttling(graph_table: Any, clock: FixedClock) -> None:
    good = GraphRepository(graph_table, clock)
    a = good.create(Node(id="", type=NodeType.SIGNAL, title="s"), "u")
    b = good.create(Node(id="", type=NodeType.FACT, title="f", evidence_url=URL), "u")
    good.link(a.id, EdgeType.SUPPORTS, b.id, "u")
    graph_table.delete_item(Key={"pk": f"N#{b.id}", "sk": "META"})
    assert good.unlink(a.id, EdgeType.SUPPORTS, b.id)
    assert good.get(a.id).out_degree == 0
    good.link(
        a.id, EdgeType.SUPPORTS, good.create(Node(id="", type=NodeType.FACT, title="g", evidence_url=URL), "u").id, "u"
    )
    target = good.edges(a.id)[0].target
    repo = GraphRepository(Chaos(graph_table, {"update_item": THROTTLE}), clock)
    with pytest.raises(ClientError):
        repo.unlink(a.id, EdgeType.SUPPORTS, target)


def test_graph_upsert_fills_empty_description(graph_table: Any, clock: FixedClock) -> None:
    repo = GraphRepository(graph_table, clock)
    a, _ = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    b, created = repo.upsert_entity(
        Node(id="", type=NodeType.ORGANIZATION, title="ACME", description="Motor maker"), "u"
    )
    assert not created and b.id == a.id and b.description == "Motor maker"


def test_graph_iterate_follows_pages(graph_table: Any, clock: FixedClock) -> None:
    good = GraphRepository(graph_table, clock)
    for i in range(5):
        good.create(Node(id="", type=NodeType.SIGNAL, title=f"s{i}"), "u")
        clock.advance(timedelta(seconds=1))
    paged = GraphRepository(Chaos(graph_table, page=True), clock)
    assert len(list(paged.iterate(NodeType.SIGNAL))) == 5


def test_condition_failed_is_false_for_other_codes() -> None:
    assert not GraphRepository._condition_failed(_error(THROTTLE, "x"))


# ---- modules ----------------------------------------------------------------------------------


def test_modules_reraise_unexpected_errors(ops_table: Any, clock: FixedClock) -> None:
    bad = ModuleRepository(Chaos(ops_table, {"put_item": THROTTLE, "update_item": THROTTLE}), clock)
    with pytest.raises(ClientError):
        bad.seed_builtins()
    good = ModuleRepository(ops_table, clock)
    good.seed_builtins()
    custom = _custom()
    with pytest.raises(ClientError):
        bad.create(custom, "u")
    with pytest.raises(ClientError):
        bad.update("signal", {"enabled": True}, 1)
    with pytest.raises(ClientError):
        bad.claim_dispatch("signal", "2026-10-02T08:00:00.000Z", "2026-10-02T07:00:00.000Z")


def test_modules_conflicts(ops_table: Any, clock: FixedClock) -> None:
    repo = ModuleRepository(ops_table, clock)
    repo.seed_builtins()
    with pytest.raises(ConflictError):
        repo.update("signal", {"enabled": True}, 99)


def test_list_all_and_runs_follow_pages(ops_table: Any, clock: FixedClock) -> None:
    good = ModuleRepository(ops_table, clock)
    good.seed_builtins()
    paged = ModuleRepository(Chaos(ops_table, page=True), clock)
    assert len(paged.list_all()) == len(builtin_modules())
    for _ in range(3):
        good.put_run(
            ModuleRun("signal", good.new_run_id(), RunStatus.SUCCEEDED, "manual", "2026-10-02T08:00:00.000Z", "u")
        )
        clock.advance(timedelta(milliseconds=5))
    first, cursor = paged.list_runs("signal", 1)
    second, _ = paged.list_runs("signal", 1, cursor)
    assert len(first) == 1 and len(second) == 1 and first[0].run_id > second[0].run_id


def test_source_update_reraises_unexpected_errors(ops_table: Any, clock: FixedClock) -> None:
    from insights_radar.modules.model import SourceType

    good = ModuleRepository(ops_table, clock)
    good.seed_builtins()
    src = good.add_source("scout", "Forum", SourceType.RSS, "https://forum.example.com/rss")
    bad = ModuleRepository(Chaos(ops_table, {"update_item": THROTTLE}), clock)
    with pytest.raises(ClientError):
        bad.update_cursor("scout", src.id, "c")
    with pytest.raises(ClientError):
        bad.mark_source_read("scout", src.id)


def test_delete_custom_module_removes_its_sources(ops_table: Any, clock: FixedClock) -> None:
    from insights_radar.modules.model import SourceType, ToolGrant

    repo = ModuleRepository(ops_table, clock)
    repo.create(_custom(ToolGrant.SOURCES), "u")
    repo.add_source("watch", "Feed", SourceType.RSS, "https://feed.example.com/rss")
    repo.delete("watch")
    assert (
        ops_table.query(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": "MODULE#watch"})["Items"]
        == []
    )


def test_source_cap(ops_table: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> None:
    from insights_radar.core.errors import ValidationError
    from insights_radar.modules import repository as mod_repo
    from insights_radar.modules.model import SourceType

    monkeypatch.setattr(mod_repo, "MAX_SOURCES", 1)
    repo = ModuleRepository(ops_table, clock)
    repo.seed_builtins()
    repo.add_source("scout", "A", SourceType.RSS, "https://a.example.com/rss")
    with pytest.raises(ValidationError, match="at most 1"):
        repo.add_source("scout", "B", SourceType.RSS, "https://b.example.com/rss")


def test_stale_runs_do_not_hold_a_slot(ops_table: Any, clock: FixedClock) -> None:
    from insights_radar.core.clock import iso

    repo = ModuleRepository(ops_table, clock)
    repo.seed_builtins()
    repo.put_run(ModuleRun("signal", repo.new_run_id(), RunStatus.RUNNING, "manual", iso(clock.now()), "u"))
    assert repo.running_count("signal") == 1
    clock.advance(timedelta(hours=9))
    assert repo.running_count("signal", iso(clock.now() - timedelta(hours=8))) == 0


# ---- lost optimistic-lock races: the version matched on read, then changed before the write ----

RACE = "ConditionalCheckFailedException"


def test_lost_races_become_conflicts(graph_table: Any, ops_table: Any, clock: FixedClock) -> None:
    import boto3

    from insights_radar.identity.principal import Principal, PrincipalKind, Role
    from insights_radar.product.model import ProductProfile
    from insights_radar.product.service import ProductService
    from insights_radar.radars.service import MemoryBlobStore, RadarService

    node = GraphRepository(graph_table, clock).create(Node(id="", type=NodeType.SIGNAL, title="s"), "u")
    with pytest.raises(ConflictError):
        GraphRepository(Chaos(graph_table, {"update_item": RACE}), clock).update(node.id, {"summary": "x"}, 1, "u")

    ModuleRepository(ops_table, clock).seed_builtins()
    racing = ModuleRepository(Chaos(ops_table, {"put_item": RACE}), clock)
    with pytest.raises(ConflictError):
        racing.update("signal", {"enabled": True}, 1)
    with pytest.raises(ConflictError):
        racing.create(_custom(), "u")

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="race")
    p = ProductService(ops_table, s3, "race", None, clock).create(ProductProfile("", "DeskPro", "Desk"), "u")
    with pytest.raises(ConflictError):
        ProductService(Chaos(ops_table, {"put_item": RACE}), s3, "race", None, clock).update(p.id, {"summary": "x"}, 1)

    owner = Principal(PrincipalKind.USER, "own", Role.EDITOR, "owner@example.com")
    store = MemoryBlobStore()
    RadarService(ops_table, store, clock).create(owner, "r", "R", template="blank")
    with pytest.raises(ConflictError):
        RadarService(Chaos(ops_table, {"put_item": RACE}), store, clock).update_meta(owner, "r", {"name": "R2"}, 1)


class Conflicting:
    """Fails the first ``n`` transactions with TransactionConflict, then delegates."""

    def __init__(self, client: Any, n: int, codes: list[str] | None = None) -> None:
        self._client, self.n, self.codes = client, n, codes or ["None", "None", "TransactionConflict"]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def transact_write_items(self, **kw: Any) -> Any:
        if self.n > 0:
            self.n -= 1
            raise ClientError(
                {
                    "Error": {"Code": "TransactionCanceledException", "Message": "x"},
                    "CancellationReasons": [{"Code": c} for c in self.codes],
                },
                "TransactWriteItems",
            )
        return self._client.transact_write_items(**kw)


def _with_client(table: Any, client: Any) -> Any:
    class T:
        def __getattr__(self, name: str) -> Any:
            return getattr(table, name)

    t = T()
    t.meta = type("M", (), {"client": client})()  # type: ignore[attr-defined]
    return t


def test_transaction_conflicts_are_retried_then_become_409(graph_table: Any, clock: FixedClock) -> None:
    good = GraphRepository(graph_table, clock)
    a = good.create(Node(id="", type=NodeType.FACT, title="f", evidence_url=URL), "u")
    org, _ = good.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    sleeps: list[float] = []
    flaky = GraphRepository(
        _with_client(graph_table, Conflicting(graph_table.meta.client, 2)), clock, sleep=sleeps.append
    )
    _, created = flaky.link(a.id, EdgeType.ABOUT, org.id, "u")
    assert created and len(sleeps) == 2 and all(0 < s <= 1.0 for s in sleeps)
    stuck = GraphRepository(
        _with_client(graph_table, Conflicting(graph_table.meta.client, 99)), clock, sleep=lambda s: None
    )
    with pytest.raises(ConflictError, match="busy"):
        stuck.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Beta"), "u")
    mixed = GraphRepository(
        _with_client(
            graph_table, Conflicting(graph_table.meta.client, 1, ["ConditionalCheckFailed", "TransactionConflict"])
        ),
        clock,
        sleep=lambda s: None,
    )
    with pytest.raises(ClientError):
        mixed.link(a.id, EdgeType.ABOUT, org.id, "u")


def test_runs_budgets_and_audit_expire(ops_table: Any, clock: FixedClock) -> None:
    from insights_radar.audit.log import AuditLog
    from insights_radar.core.clock import iso
    from insights_radar.core.tokens import TokenUsage
    from insights_radar.identity.principal import Principal, PrincipalKind, Role

    repo = ModuleRepository(ops_table, clock)
    run = ModuleRun("signal", repo.new_run_id(), RunStatus.SUCCEEDED, "manual", iso(clock.now()), "u")
    repo.put_run(run)
    repo.charge("signal", TokenUsage(10, 1, 0, 0))
    AuditLog(ops_table, clock).record(Principal(PrincipalKind.USER, "s", Role.ADMIN, "a@example.com"), "x", "y")
    now = int(clock.now().timestamp())
    items = ops_table.scan()["Items"]
    ttls = {i["pk"].split("#")[0]: int(i["ttl"]) - now for i in items if "ttl" in i}
    assert ttls == {"MODULE": 180 * 86400, "BUDGET": 90 * 86400, "AUDIT": 400 * 86400}
    assert not any("expires_at" in i for i in items)
