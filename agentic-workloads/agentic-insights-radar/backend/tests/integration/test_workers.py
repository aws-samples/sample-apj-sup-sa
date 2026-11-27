# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from insights_radar.api import workers
from insights_radar.core.clock import FixedClock
from insights_radar.modules.repository import RunStatus
from insights_radar.product.model import ProductProfile
from tests.integration.harness import TestContext


class FakeAgentCore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke_agent_runtime(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {"statusCode": 200, "response": io.BytesIO(b'{"accepted": true}')}


class FakeStepFunctions:
    def __init__(self) -> None:
        self.failures: list[dict[str, Any]] = []

    def send_task_failure(self, **kw: Any) -> None:
        self.failures.append(kw)


class WorkerContext(TestContext):
    __test__ = False

    def __init__(self, *a: Any) -> None:
        super().__init__(*a)
        self.agentcore = FakeAgentCore()
        self.sfn = FakeStepFunctions()

    def client(self, name: str) -> Any:
        if name == "stepfunctions":
            return self.sfn
        assert name == "bedrock-agentcore"
        return self.agentcore


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> WorkerContext:
    return WorkerContext(graph_table, ops_table, clock)


def _enable(ctx: WorkerContext, slug: str, interval: int = 60) -> None:
    m = ctx.modules.get(slug)
    ctx.modules.update(slug, {"enabled": True, "interval_minutes": interval}, m.version)


def test_seed_is_idempotent(ctx: WorkerContext) -> None:
    assert workers.seed({}, None, ctx) == {"created": [], "migrated": [], "failed": [], "warnings": []}
    assert len(ctx.modules.list_all()) == 7
    ctx.ops_table.delete_item(Key={"pk": "MODULE#trend", "sk": "DEF"})
    assert workers.seed({}, None, ctx) == {"created": ["trend"], "migrated": [], "failed": [], "warnings": []}
    # r2 B2-104: a release that ships a built-in audits creating it, with the grants it arrives with.
    rows = [r for r in ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 100)[0] if r["action"] == "module.create"]
    assert [r["target"] for r in rows] == ["trend"]
    assert rows[0]["details"] == {"grants": ["kg_read", "kg_write_output"]}  # r2 P2-062: no web_search


def test_sweep_dispatches_due_modules_once(ctx: WorkerContext, clock: FixedClock) -> None:
    _enable(ctx, "signal")
    out = workers.sweep({}, None, ctx)
    dispatched = [d for d in out["modules"] if d["dispatched"]]
    assert [d["module"] for d in dispatched] == ["signal"]
    assert {d["reason"] for d in out["modules"] if not d["dispatched"]} <= {"disabled", "on-demand only"}
    again = workers.sweep({}, None, ctx)
    assert not any(d["dispatched"] for d in again["modules"])
    assert {d["reason"] for d in again["modules"] if d["module"] == "signal"} == {"interval not elapsed"}
    assert out["documents_marked"] == 0 and out["autopilot"]["applied"] == 0


def test_sweep_skips_everything_when_genai_disabled(ctx: WorkerContext) -> None:
    _enable(ctx, "signal")
    ctx.modules.set_andon(False, "incident", "u")
    out = workers.sweep({}, None, ctx)
    assert out["modules"] == [{"module": "*", "dispatched": False, "reason": "genai disabled"}]


def test_sweep_marks_documents_indexed(ctx: WorkerContext) -> None:
    p = ctx.products.create(ProductProfile("", "DeskPro", "Standing desk"), "u")
    up = ctx.products.presign_upload(p.id, "notes.md", 5, "u")
    key = up["upload"]["fields"]["key"]
    ctx.s3.put_object(
        Bucket="example-assets-111122223333-us-east-1", Key=key, Body=b"hello", ContentType="text/markdown"
    )
    ctx.products.complete_upload(p.id, up["document"]["id"])
    out = workers.sweep({}, None, ctx)
    assert out["documents_marked"] == 1
    assert ctx.products.list_documents(p.id)[0].status == "ready"


def test_invoke_passes_task_token_and_pads_session(ctx: WorkerContext) -> None:
    run = ctx.module_service.start("draft", "manual", "user:u", "content_idea/i1")
    payload = ctx.launcher.payloads[-1]
    out = workers.invoke({"payload": payload, "task_token": "TT", "runtime_arn": "arn:rt"}, None, ctx)
    assert out == {"accepted": True, "response": '{"accepted": true}'}
    call = ctx.agentcore.calls[0]
    sent = json.loads(call["payload"])
    assert sent["task_token"] == "TT" and sent["run_id"] == run.run_id
    assert len(call["runtimeSessionId"]) >= 40 and call["agentRuntimeArn"] == "arn:rt"


def test_invoke_refuses_when_genai_disabled(ctx: WorkerContext) -> None:
    run = ctx.module_service.start("draft", "manual", "user:u", "content_idea/i1")
    payload = ctx.launcher.payloads[-1]
    ctx.modules.set_andon(False, "incident", "u")
    # CTR HC-011 follow-up: no exception (InvokeErrors), the task fails with its own error instead.
    out = workers.invoke({"payload": payload, "task_token": "TT", "runtime_arn": "arn:rt"}, None, ctx)
    assert out == {"accepted": False, "reason": "genai_disabled"} and ctx.agentcore.calls == []
    assert [(c["taskToken"], c["error"]) for c in ctx.sfn.failures] == [("TT", "GenAiDisabled")]
    assert ctx.modules.get_run("draft", run.run_id).status.value == "cancelled"


def test_finalize_success_charges_budget(ctx: WorkerContext) -> None:
    run = ctx.module_service.start("draft", "manual", "user:u", "content_idea/i1")
    out = workers.finalize(
        {
            "module": "draft",
            "run_id": run.run_id,
            "result": {
                "status": "succeeded",
                "summary": "1 draft",
                "nodes_written": 1,
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "cache_read_tokens": 1000,
                    "cache_write_tokens": 0,
                },
            },
        },
        None,
        ctx,
    )
    assert out["status"] == "succeeded" and out["weighted_tokens"] == 1000 + 500 + 100
    assert ctx.modules.budget("draft", 10**6).consumed == 1600
    again = workers.finalize(
        {"module": "draft", "run_id": run.run_id, "result": {"usage": {"input_tokens": 5}}}, None, ctx
    )
    assert again["weighted_tokens"] == 1600 and ctx.modules.budget("draft", 10**6).consumed == 1600


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ({"error": {"Error": "States.Timeout", "Cause": "heartbeat"}}, "heartbeat"),
        ({"error": {"Error": "States.TaskFailed"}}, "States.TaskFailed"),
        ({"error": "boom"}, "boom"),
        ({"result": {"status": "weird"}}, None),
        ({"result": {"status": "running"}}, None),
        ({"result": {"status": "failed", "error": "tool quota"}}, "tool quota"),
    ],
)
def test_finalize_failures(ctx: WorkerContext, event: dict[str, Any], message: str | None) -> None:
    run = ctx.module_service.start("draft", "manual", "user:u", "content_idea/i1")
    out = workers.finalize({"module": "draft", "run_id": run.run_id, **event}, None, ctx)
    assert out["status"] == RunStatus.FAILED.value and out.get("error") == message


def test_crawl_worker_stores_pages(ctx: WorkerContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from insights_radar.product.crawler import CrawledPage, CrawlReport

    closed: list[bool] = []

    class Fetcher:
        def close(self) -> None:
            closed.append(True)

    class Crawler:
        def __init__(self, fetcher: Any) -> None:
            pass

        def crawl(self, seeds: list[str]) -> CrawlReport:
            return CrawlReport(pages=[CrawledPage(seeds[0], "Guide", "text " * 60)])

    monkeypatch.setattr(workers, "SafeFetcher", Fetcher)
    monkeypatch.setattr(workers, "DocsCrawler", Crawler)
    p = ctx.products.create(ProductProfile("", "DeskPro", "Desk", doc_urls=("https://docs.example.com/",)), "u")
    out = workers.crawl({"product_id": p.id}, None, ctx)
    assert out == {"product_id": p.id, "pages": 1, "status": "indexing"} and closed == [True]


def test_sweep_requests_due_crawls(ctx: WorkerContext, clock: FixedClock) -> None:
    from datetime import timedelta

    from insights_radar.product.crawler import CrawledPage, CrawlReport

    requested: list[str] = []
    ctx.products._request_crawl = requested.append  # type: ignore[attr-defined]
    p = ctx.products.create(ProductProfile("", "DeskPro", "Desk", doc_urls=("https://docs.example.com/",)), "u")
    ctx.products.store_crawl(p.id, CrawlReport(pages=[CrawledPage("https://docs.example.com/", "", "t " * 200)]))
    clock.advance(timedelta(days=8))
    assert workers.sweep({}, None, ctx)["crawls"] == 1 and requested == [p.id, p.id]


def test_seed_applies_the_deployment_bundle(ctx: WorkerContext) -> None:
    import json
    from dataclasses import replace

    from insights_radar.core.errors import ValidationError

    bundle = {
        "version": 1,
        "profile": {"app_name": "Trade Radar"},
        "modules": [{"slug": "gap", "enabled": False}, {"slug": "ghost", "enabled": False}],
    }
    ctx.s3.put_object(
        Bucket="example-assets-111122223333-us-east-1", Key="seed/bundle.json", Body=json.dumps(bundle).encode()
    )
    ctx.settings = replace(ctx.settings, seed_bundle_key="seed/bundle.json")
    workers.seed({}, None, ctx)
    assert ctx.profile.get().app_name == "Trade Radar" and ctx.modules.get("gap").enabled is False
    rows = ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 100)[0]
    applied = [r for r in rows if r["action"] == "seed.applied"]
    assert applied and applied[0]["details"]["errors"] == 1  # the unknown module, reported not raised
    # Not JSON, or not a valid bundle: the deploy fails with the reason.
    ctx.s3.put_object(Bucket="example-assets-111122223333-us-east-1", Key="seed/bundle.json", Body=b"{nope")
    with pytest.raises(ValidationError, match="not JSON"):
        workers.seed({}, None, ctx)
    ctx.s3.put_object(Bucket="example-assets-111122223333-us-east-1", Key="seed/bundle.json", Body=b'{"version": 9}')
    with pytest.raises(ValidationError, match="seed bundle is not valid"):
        workers.seed({}, None, ctx)


def test_seed_bundle_audit_failure_is_logged(ctx: WorkerContext, caplog: pytest.LogCaptureFixture) -> None:
    import json
    from dataclasses import replace
    from unittest.mock import patch

    ctx.s3.put_object(
        Bucket="example-assets-111122223333-us-east-1", Key="b.json", Body=json.dumps({"version": 1}).encode()
    )
    ctx.settings = replace(ctx.settings, seed_bundle_key="b.json")
    with patch.object(ctx.audit, "record_actor", side_effect=RuntimeError("down")):
        workers.seed({}, None, ctx)
    assert "seed.applied" in caplog.text


@pytest.mark.parametrize(
    ("result", "error", "charged"),
    [
        # A deployment, 2026-11-02: a Scout run with no new items made no call, booked its whole hold and stopped Scout
        # for the rest of the day. The runtime's count of 0 calls now proves it spent nothing.
        ({"status": "succeeded", "model_calls": 0, "usage": {}}, None, 0),
        # Without that proof (an older runtime, a count it did not send, calls it did make) the hold stays.
        ({"status": "succeeded", "usage": {}}, None, None),
        ({"status": "succeeded", "model_calls": 2, "usage": {}}, None, None),
        ({"status": "succeeded", "model_calls": True, "usage": {}}, None, None),
        ({"status": "succeeded", "model_calls": 0, "usage": {"input_tokens": "x"}}, None, None),
        ({"status": "succeeded", "model_calls": 0}, {"Error": "States.Timeout"}, None),
    ],
)
def test_a_run_that_made_no_model_call_costs_nothing(
    ctx: WorkerContext, result: dict[str, Any], error: Any, charged: int | None
) -> None:
    run = ctx.module_service.start("draft", "manual", "user:u", "content_idea/i1")
    event: dict[str, Any] = {"module": "draft", "run_id": run.run_id, "result": result}
    if error:
        event["error"] = error
    out = workers.finalize(event, None, ctx)
    budget = ctx.modules.budget("draft", 10**6)
    if charged == 0:
        assert out["weighted_tokens"] == 0 and budget.consumed == 0 and budget.remaining == 10**6
    else:
        assert out["weighted_tokens"] == run.budget_hold > 0  # unknown spend: the hold stays charged


@pytest.mark.parametrize("status", ["budget_exhausted", "cancelled", "failed"])
def test_any_status_that_proves_no_model_call_gives_the_hold_back(ctx: WorkerContext, status: str) -> None:
    """Code review r29: GenAI paused, the budget closed, or a failure before the first call spent nothing."""
    run = ctx.module_service.start("draft", "manual", "user:u", "content_idea/i1")
    result = {"status": status, "model_calls": 0, "usage": {}, "error": "before the first call"}
    out = workers.finalize({"module": "draft", "run_id": run.run_id, "result": result}, None, ctx)
    assert out["status"] == status and out["weighted_tokens"] == 0
    assert ctx.modules.budget("draft", 10**6).remaining == 10**6


# ---- finalize removes the entities a run created and left unlinked (Daily check 2026-11-10) ----------


def _entity(ctx: WorkerContext, title: str, actor: str = "module:scout", kind: Any = None) -> Any:
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    node, created = ctx.graph.upsert_entity(Node(id="", type=kind or NodeType.ORGANIZATION, title=title), actor)
    assert created
    return node


def _finish(ctx: WorkerContext, run_id: str, created: Any, status: str = "budget_exhausted") -> dict[str, Any]:
    result = {"status": status, "summary": "stopped", "usage": {"input_tokens": 10}, "created_entities": created}
    return workers.finalize({"module": "scout", "run_id": run_id, "result": result}, None, ctx)


@pytest.mark.parametrize("status", ["budget_exhausted", "cancelled", "failed"])
def test_finalize_removes_the_runs_own_unlinked_entities(ctx: WorkerContext, clock: FixedClock, status: str) -> None:
    from datetime import timedelta

    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    before = _entity(ctx, "Old orphan")  # created by the module, but before this run: not this run's
    clock.advance(timedelta(seconds=1))
    run = ctx.module_service.start("scout", "manual", "user:u")
    russia = _entity(ctx, "UK Russia sanctions regime")
    iran = _entity(ctx, "UK Iran sanctions regime")
    linked = _entity(ctx, "Foreign, Commonwealth and Development Office (UK)")
    fact = ctx.graph.create(Node(id="", type=NodeType.FACT, title="f", evidence_url="https://a.example.com/1"), "u")
    ctx.graph.link(fact.id, EdgeType.ABOUT, linked.id, "u")
    other = _entity(ctx, "Someone else's", actor="module:signal")
    person = _entity(ctx, "A person's", actor="user:u")
    ids = [russia.id, iran.id, linked.id, other.id, person.id, before.id]
    out = _finish(ctx, run.run_id, [*ids, russia.id], status)
    assert out["status"] == status
    remaining = ctx.graph.get_many([*ids, fact.id])
    assert set(remaining) == {linked.id, other.id, person.id, before.id, fact.id}
    # The name key went too, so the name can be created again as a new entity.
    assert ctx.graph.find_entity(NodeType.ORGANIZATION, "UK Russia sanctions regime") is None
    again, created = ctx.graph.upsert_entity(
        Node(id="", type=NodeType.ORGANIZATION, title="UK Russia sanctions regime"), "module:scout"
    )
    assert created and again.id != russia.id
    rows = [
        r for r in ctx.audit.page(clock.now().strftime("%Y-%m-%d"), 100)[0] if r["action"] == "graph.delete_run_orphans"
    ]
    assert [r["target"] for r in rows] == [f"scout/{run.run_id}"]
    assert {d["id"] for d in rows[0]["details"]["removed"]} == {russia.id, iran.id} and rows[0]["details"]["kept"] == 4
    # Idempotent: a retried finalize finds the run finished and touches nothing.
    assert _finish(ctx, run.run_id, ids)["status"] == status


@pytest.mark.parametrize(
    "created",
    [
        None,
        "organization/0190a6c4-0b5e-7c3a-9f00-000000000001",
        {"id": "x"},
        [
            7,
            None,
            "",
            "../../etc",
            "ORGANIZATION/0190a6c4-0b5e-7c3a-9f00-000000000001",
            "nosuch/0190a6c4-0b5e-7c3a-9f00-000000000001",
        ],
        ["fact/0190a6c4-0b5e-7c3a-9f00-000000000001"],  # not an entity type
        ["organization/0190a6c4-0b5e-7c3a-9f00-000000000001"],  # well-formed, but no such node
    ],
)
def test_finalize_tolerates_malformed_or_missing_created_entities(ctx: WorkerContext, created: Any) -> None:
    run = ctx.module_service.start("scout", "manual", "user:u")
    kept = _entity(ctx, "Kept")
    assert _finish(ctx, run.run_id, created)["status"] == "budget_exhausted"
    assert ctx.graph.get(kept.id).id == kept.id


def test_created_entities_are_validated_and_capped() -> None:
    from insights_radar.graph.moderation import MAX_RUN_ORPHANS

    good = [f"topic/0190a6c4-0b5e-7c3a-9f00-{n:012x}" for n in range(MAX_RUN_ORPHANS + 10)]
    got = workers._created_entities([good[0], good[0], "fact/0190a6c4-0b5e-7c3a-9f00-000000000001", *good])
    assert got == good[:MAX_RUN_ORPHANS]
    assert workers._created_entities("topic/x") == [] and workers._created_entities(None) == []


def test_a_failing_cleanup_never_fails_the_finalize(ctx: WorkerContext, caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        def remove(self, *a: Any) -> list[str]:
            raise RuntimeError("graph down")

    ctx.module_service._orphans = Broken()
    run = ctx.module_service.start("scout", "manual", "user:u")
    orphan = _entity(ctx, "Orphan")
    assert _finish(ctx, run.run_id, [orphan.id])["status"] == "budget_exhausted"
    assert ctx.modules.get_run("scout", run.run_id).status is RunStatus.BUDGET_EXHAUSTED
    assert "unlinked entities failed" in caplog.text


def test_a_service_without_a_cleaner_keeps_the_entities(ctx: WorkerContext) -> None:
    ctx.module_service._orphans = None
    run = ctx.module_service.start("scout", "manual", "user:u")
    orphan = _entity(ctx, "Orphan")
    assert _finish(ctx, run.run_id, [orphan.id])["status"] == "budget_exhausted"
    assert ctx.graph.get(orphan.id).id == orphan.id


def test_one_failed_removal_is_logged_and_the_rest_go(
    ctx: WorkerContext, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = ctx.module_service.start("scout", "manual", "user:u")
    first, second = _entity(ctx, "First"), _entity(ctx, "Second")
    real = ctx.graph.delete_if_unlinked

    def flaky(node: Any) -> bool:
        if node.id == first.id:
            raise RuntimeError("throttled")
        return real(node)

    monkeypatch.setattr(ctx.graph, "delete_if_unlinked", flaky)
    monkeypatch.setattr(ctx.audit, "record_actor", lambda *a: (_ for _ in ()).throw(RuntimeError("audit down")))
    assert _finish(ctx, run.run_id, [first.id, second.id])["status"] == "budget_exhausted"
    assert set(ctx.graph.get_many([first.id, second.id])) == {first.id}
    assert "could not remove run" in caplog.text and "audit write failed" in caplog.text


def test_an_entity_without_a_creation_time_is_kept() -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    from insights_radar.graph.model import Node
    from insights_radar.graph.moderation import RunOrphans
    from insights_radar.graph.registry import NodeType

    since = datetime(2026, 11, 10, tzinfo=UTC)
    node = Node(id="organization/x", type=NodeType.ORGANIZATION, title="t", created_by="module:scout")
    assert not RunOrphans.removable(node, "module:scout", since)
    assert RunOrphans.removable(replace(node, created_at="2026-11-10T00:00:00.000Z"), "module:scout", since)


def test_a_run_that_finished_keeps_the_entities_it_created(ctx: WorkerContext) -> None:
    # Code review r50: a run that finished may have created an entity for a later run, a radar entry or a watch,
    # none of which is a graph edge; only a run cut off part way is cleaned.
    run = ctx.module_service.start("scout", "manual", "user:u")
    alone = _entity(ctx, "Created on purpose")
    out = _finish(ctx, run.run_id, [alone.id], "succeeded")
    assert out["status"] == "succeeded" and ctx.graph.get(alone.id).id == alone.id


def test_an_entity_linked_between_the_check_and_the_delete_is_kept(ctx: WorkerContext) -> None:
    from insights_radar.graph.model import Node
    from insights_radar.graph.moderation import RunOrphans
    from insights_radar.graph.registry import EdgeType, NodeType

    run = ctx.module_service.start("scout", "manual", "user:u")
    entity = _entity(ctx, "Linked just in time")
    graph = ctx.graph
    real_get = graph.get
    calls = {"n": 0}

    def get(node_id: str) -> Node:
        calls["n"] += 1
        if calls["n"] == 1:  # read as unlinked, then another writer links it before the delete
            node = real_get(node_id)
            fact = graph.create(Node(id="", type=NodeType.FACT, title="f", evidence_url="https://a.example.com/2"), "u")
            graph.link(fact.id, EdgeType.ABOUT, entity.id, "u")
            return node
        return real_get(node_id)

    graph.get = get  # type: ignore[method-assign]
    try:
        removed = RunOrphans.of(ctx).remove("scout", "module:scout", run.run_id, run.started_at, [entity.id])
    finally:
        graph.get = real_get  # type: ignore[method-assign]
    assert removed == [] and real_get(entity.id).degree == 1
    assert ctx.graph.find_entity(entity.type, entity.title) is not None  # its name key stays too


def test_delete_if_unlinked_is_one_conditional_transaction(ctx: WorkerContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.core.errors import ValidationError
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import EdgeType, NodeType

    graph = ctx.graph
    alone = _entity(ctx, "Alone")
    assert graph.delete_if_unlinked(alone) is True
    assert graph.get_many([alone.id]) == {} and graph.find_entity(NodeType.ORGANIZATION, "Alone") is None
    linked = _entity(ctx, "Linked")
    fact = graph.create(Node(id="", type=NodeType.FACT, title="f", evidence_url="https://a.example.com/3"), "u")
    graph.link(fact.id, EdgeType.ABOUT, linked.id, "u")
    assert graph.delete_if_unlinked(linked) is False and graph.get(linked.id).degree == 1
    with pytest.raises(ValidationError):
        graph.delete_if_unlinked(fact)
    other = _entity(ctx, "Other error")

    def boom(**_: Any) -> None:
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "TransactWriteItems")

    monkeypatch.setattr(graph._client, "transact_write_items", boom)
    with pytest.raises(ClientError):
        graph.delete_if_unlinked(other)


def test_a_retried_finalize_still_removes_the_runs_orphans(ctx: WorkerContext) -> None:
    # Code review r53: a finalize that recorded the run and died before the cleanup left them for good.
    run = ctx.module_service.start("scout", "manual", "user:u")
    orphan = _entity(ctx, "Left by a dead finalize")
    cleaner, ctx.module_service._orphans = ctx.module_service._orphans, None
    assert _finish(ctx, run.run_id, [orphan.id])["status"] == "budget_exhausted"
    assert ctx.graph.get(orphan.id).id == orphan.id
    ctx.module_service._orphans = cleaner
    _finish(ctx, run.run_id, [orphan.id])  # the retry
    assert ctx.graph.get_many([orphan.id]) == {}


def test_an_entity_whose_name_now_resolves_elsewhere_is_deleted_and_the_key_kept(ctx: WorkerContext) -> None:
    from insights_radar.graph.registry import NodeType

    first = _entity(ctx, "Renamed away")
    other = _entity(ctx, "Kept entity")
    key = {"pk": f"K#{NodeType.ORGANIZATION.value}#{first.name_key}", "sk": "KEY"}
    ctx.graph._table.put_item(Item={**key, "node_id": other.id, "kind": "key"})  # as after a merge
    assert ctx.graph.delete_if_unlinked(first) is True
    assert ctx.graph.get_many([first.id]) == {}
    assert ctx.graph._table.get_item(Key=key)["Item"]["node_id"] == other.id


def test_a_plain_delete_keeps_a_name_key_that_resolves_elsewhere(ctx: WorkerContext) -> None:
    # Code review r54: GraphRepository.delete removed the key whatever entity it pointed to.
    from insights_radar.graph.registry import NodeType

    first = _entity(ctx, "Merged away")
    kept = _entity(ctx, "The kept one")
    key = {"pk": f"K#{NodeType.ORGANIZATION.value}#{first.name_key}", "sk": "KEY"}
    ctx.graph._table.put_item(Item={**key, "node_id": kept.id, "kind": "key"})
    ctx.graph.delete(first.id)
    assert ctx.graph._table.get_item(Key=key)["Item"]["node_id"] == kept.id
    own = _entity(ctx, "Own key")
    ctx.graph.delete(own.id)
    assert ctx.graph.find_entity(NodeType.ORGANIZATION, "Own key") is None


def test_a_retry_that_reports_success_keeps_the_entities(ctx: WorkerContext) -> None:
    # Code review r54: a run cancelled after it had in fact finished keeps what it made.
    run = ctx.module_service.start("scout", "manual", "user:u")
    made = _entity(ctx, "Made on purpose")
    cleaner, ctx.module_service._orphans = ctx.module_service._orphans, None
    _finish(ctx, run.run_id, [made.id])  # recorded as budget_exhausted, cleanup lost
    ctx.module_service._orphans = cleaner
    _finish(ctx, run.run_id, [made.id], "succeeded")  # a report that says it succeeded
    assert ctx.graph.get(made.id).id == made.id


def test_a_plain_delete_surfaces_other_key_errors(ctx: WorkerContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    entity = _entity(ctx, "Key error")
    real = ctx.graph._table.delete_item

    def boom(**kw: Any) -> Any:
        if kw.get("ConditionExpression"):
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "DeleteItem"
            )
        return real(**kw)

    monkeypatch.setattr(ctx.graph._table, "delete_item", boom)
    with pytest.raises(ClientError):
        ctx.graph.delete(entity.id)


def test_a_name_key_left_by_a_failed_delete_is_taken_back(ctx: WorkerContext, monkeypatch: pytest.MonkeyPatch) -> None:
    # Code review r56: a key whose node is gone made the name impossible to create again.
    from botocore.exceptions import ClientError

    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    entity = _entity(ctx, "Half deleted")
    real = ctx.graph._table.delete_item

    def boom(**kw: Any) -> Any:
        if kw.get("ConditionExpression"):
            raise ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}}, "DeleteItem")
        return real(**kw)

    monkeypatch.setattr(ctx.graph._table, "delete_item", boom)
    with pytest.raises(ClientError):
        ctx.graph.delete(entity.id)
    monkeypatch.setattr(ctx.graph._table, "delete_item", real)
    assert ctx.graph.get_many([entity.id]) == {}  # the node went, its key stayed
    again, created = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Half deleted"), "u")
    assert created and again.id != entity.id
    assert ctx.graph.find_entity(NodeType.ORGANIZATION, "Half deleted").id == again.id  # type: ignore[union-attr]


def test_drop_stale_key_cases(ctx: WorkerContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    graph = ctx.graph
    live = _entity(ctx, "Live")
    key = f"K#organization#{live.name_key}"
    assert graph._drop_stale_key(key) is False  # its node exists: someone is creating it
    assert graph._drop_stale_key("K#organization#nothing-here") is True  # no key at all
    graph._table.put_item(
        Item={"pk": "K#organization#ghost", "sk": "KEY", "node_id": "organization/gone", "kind": "key"}
    )

    def repointed(**_: Any) -> Any:
        raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "DeleteItem")

    monkeypatch.setattr(graph._table, "delete_item", repointed)
    assert graph._drop_stale_key("K#organization#ghost") is False

    def broken(**_: Any) -> Any:
        raise ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}}, "DeleteItem")

    monkeypatch.setattr(graph._table, "delete_item", broken)
    with pytest.raises(ClientError):
        graph._drop_stale_key("K#organization#ghost")


def test_upsert_with_a_stale_key_and_a_live_racer_is_a_conflict(
    ctx: WorkerContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insights_radar.core.errors import ConflictError
    from insights_radar.graph.model import Node
    from insights_radar.graph.registry import NodeType

    _entity(ctx, "Raced")
    monkeypatch.setattr(ctx.graph, "find_entity", lambda *a: None)
    monkeypatch.setattr(ctx.graph, "_drop_stale_key", lambda key: False)
    with pytest.raises(ConflictError):
        ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Raced"), "u")
