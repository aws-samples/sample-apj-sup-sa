# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from insights_radar.api import app as app_module
from insights_radar.api import workers
from insights_radar.api.body import Body
from insights_radar.api.context import AppContext, Settings
from insights_radar.api.http import MAX_BODY_BYTES, Request, Router, ok
from insights_radar.core.errors import BadRequestError, PayloadTooLargeError, ValidationError
from insights_radar.graph.repository import GraphRepository
from insights_radar.graph.search import HybridSearch
from insights_radar.identity.run_token import RunTokenService
from insights_radar.identity.users import UserAdmin
from insights_radar.insights.digest import DigestService
from insights_radar.insights.service import InsightService
from insights_radar.modules.service import ModuleService
from insights_radar.product.knowledge import KnowledgeBase
from insights_radar.product.service import ProductService
from insights_radar.radars.service import RadarService

# ---- Body ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "data", "message"),
    [
        (lambda b: b.text("k"), {}, "k is required"),
        (lambda b: b.text("k"), {"k": 1}, "k must be a string"),
        (lambda b: b.text("k", max_len=2), {"k": "abc"}, "at most 2"),
        (lambda b: b.integer("k"), {}, "k is required"),
        (lambda b: b.integer("k"), {"k": "1"}, "must be an integer"),
        (lambda b: b.integer("k"), {"k": True}, "must be an integer"),
        (lambda b: b.integer("k", low=1, high=3), {"k": 0}, "between 1 and 3"),
        (lambda b: b.integer("k", low=1, high=3), {"k": 4}, "between 1 and 3"),
        (lambda b: b.flag("k"), {}, "k is required"),
        (lambda b: b.flag("k"), {"k": "true"}, "true or false"),
        (lambda b: b.texts("k", required=True), {}, "k is required"),
        (lambda b: b.texts("k"), {"k": "a"}, "list of strings"),
        (lambda b: b.texts("k"), {"k": [1]}, "list of strings"),
        (lambda b: b.texts("k", max_items=1), {"k": ["a", "b"]}, "at most 1 items"),
        (lambda b: b.texts("k", max_len=1), {"k": ["ab"]}, "at most 1 characters"),
        (lambda b: b.mapping("k"), {}, "k is required"),
        (lambda b: b.mapping("k"), {"k": []}, "must be an object"),
        (lambda b: b.items("k"), {}, "k is required"),
        (lambda b: b.items("k"), {"k": {}}, "must be a list"),
        (lambda b: b.items("k", max_items=1), {"k": [1, 2]}, "at most 1 items"),
        (lambda b: b.only("a"), {"a": 1, "z": 2, "y": 3}, "unknown fields: y, z"),
    ],
)
def test_body_rejects(call: Any, data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        call(Body(data))


def test_body_defaults_and_values() -> None:
    b = Body({"t": "x", "i": 2, "f": False, "l": ["a"], "m": {"a": 1}, "it": [1], "n": None})
    assert b.raw["t"] == "x" and b.has("t") and not b.has("zz")
    assert b.text("t") == "x" and b.text("zz", required=False, default="d") == "d"
    assert b.opt_text("t") == "x" and b.opt_text("n") is None and b.opt_text("zz") is None
    assert b.integer("i", low=1, high=2) == 2 and b.integer("zz", required=False, default=7) == 7
    assert b.flag("f") is False and b.flag("zz", required=False, default=True) is True
    assert b.texts("l") == ["a"] and b.texts("zz") == []
    assert b.mapping("m") == {"a": 1} and b.mapping("zz", required=False) == {}
    assert b.items("it") == [1] and b.items("zz", required=False) == []
    b.only("t", "i", "f", "l", "m", "it", "n")


# ---- Request / Router ----------------------------------------------------------------------------


def test_request_from_event_variants() -> None:
    raw = base64.b64encode(b'{"a": 1}').decode()
    req = Request.from_event({"httpMethod": "post", "rawPath": "/x", "body": raw, "isBase64Encoded": True})
    assert req.method == "POST" and req.json() == {"a": 1} and req.claims == {} and req.source_arn is None
    req = Request.from_event({"requestContext": {"http": {"path": "/p"}}})
    assert req.method == "GET" and req.path == "/p" and req.json() == {}
    assert Request.from_event({}).path == "/"
    with pytest.raises(PayloadTooLargeError):
        Request.from_event({"body": "x" * (MAX_BODY_BYTES + 1)})
    bad = Request.from_event({"body": base64.b64encode(b"\xff\xfe").decode(), "isBase64Encoded": True})
    with pytest.raises(BadRequestError):
        bad.json()


@pytest.mark.parametrize(("raw", "expected"), [(None, 5), ("", 5), ("3", 3), ("-9", 1), ("999", 10)])
def test_int_query_clamps(raw: str | None, expected: int) -> None:
    req = Request("GET", "/", {}, {} if raw is None else {"n": raw}, b"", {}, {}, None)
    assert req.int_query("n", 5, 1, 10) == expected


@pytest.mark.parametrize("raw", ["1e3", "--5", "-", "5-", "1 2", "\u0661"])
def test_int_query_rejects_non_numbers(raw: str) -> None:
    req = Request("GET", "/", {}, {"n": raw}, b"", {}, {}, None)
    with pytest.raises(BadRequestError):
        req.int_query("n", 5, 1, 10)


def test_router_decorator_and_templates() -> None:
    r = Router()

    @r.route("get", "/a/{id}")
    def handler(req: Request) -> Any:
        return ok({"id": req.params["id"]})

    fn, params, template = r.resolve(Request("GET", "/a/x.1", {}, {}, b"", {}, {}, None))
    assert fn is handler and params == {"id": "x.1"} and template == "/a/{id}"
    assert r.templates == [("GET", "/a/{id}")]


# ---- AppContext wiring --------------------------------------------------------------------------


class _Session:
    def __init__(self) -> None:
        self.clients: list[str] = []

    def client(self, name: str, config: Any = None, **kwargs: Any) -> Any:
        self.clients.append(name)
        return object()

    def resource(self, name: str, config: Any = None) -> Any:
        class _Res:
            @staticmethod
            def Table(name: str) -> Any:
                return MagicMock(table_name=name)

        return _Res()


def test_app_context_builds_every_service() -> None:
    settings = Settings.from_env(
        {
            "AIR_GRAPH_TABLE": "g",
            "AIR_OPS_TABLE": "o",
            "AIR_ASSETS_BUCKET": "b",
            "AIR_KB_ID": "KB",
            "AIR_RUN_KEY_ID": "k",
        }
    )
    session = _Session()
    ctx = AppContext(settings, session=session)
    assert ctx.graph_table.table_name == "g" and ctx.ops_table.table_name == "o"
    assert isinstance(ctx.graph, GraphRepository) and isinstance(ctx.search, HybridSearch)
    assert isinstance(ctx.tokens, RunTokenService) and isinstance(ctx.module_service, ModuleService)
    assert isinstance(ctx.knowledge, KnowledgeBase) and isinstance(ctx.products, ProductService)
    assert isinstance(ctx.insights, InsightService) and isinstance(ctx.radars, RadarService)
    assert isinstance(ctx.digests, DigestService)
    assert isinstance(ctx.users, UserAdmin) and ctx.audit is ctx.audit and ctx.chat_budget is ctx.chat_budget
    assert ctx.live_roles is ctx.live_roles  # one cache per container
    assert ctx.profile is ctx.profile  # its read cache lives as long as the container
    assert {"s3vectors", "bedrock-runtime", "kms", "stepfunctions", "bedrock-agent", "cognito-idp"} <= set(
        session.clients
    )
    assert AppContext(Settings.from_env({}), session=_Session()).knowledge is None


def test_request_crawl_invokes_the_crawler_asynchronously() -> None:
    calls: list[dict[str, Any]] = []

    class _Lambda:
        def invoke(self, **kw: Any) -> None:
            calls.append(kw)

    class _CrawlSession(_Session):
        def client(self, name: str, config: Any = None, **kwargs: Any) -> Any:
            return _Lambda() if name == "lambda" else super().client(name, config, **kwargs)

    ctx = AppContext(Settings.from_env({"AIR_CRAWLER_FUNCTION": "crawler"}), session=_CrawlSession())
    ctx.request_crawl("p1")
    assert calls == [{"FunctionName": "crawler", "InvocationType": "Event", "Payload": b'{"product_id": "p1"}'}]
    assert ctx.products._request_crawl is not None  # type: ignore[attr-defined]


def test_request_watch_invokes_the_watcher_asynchronously() -> None:
    calls: list[dict[str, Any]] = []

    class _Lambda:
        def invoke(self, **kw: Any) -> None:
            calls.append(kw)

    class _WatchSession(_Session):
        def client(self, name: str, config: Any = None, **kwargs: Any) -> Any:
            return _Lambda() if name == "lambda" else super().client(name, config, **kwargs)

    assert not AppContext(Settings.from_env({}), session=_WatchSession()).request_watch()
    ctx = AppContext(Settings.from_env({"AIR_WATCH_FUNCTION": "watch"}), session=_WatchSession())
    assert ctx.request_watch()
    assert calls == [{"FunctionName": "watch", "InvocationType": "Event", "Payload": b'{"trigger": "manual"}'}]
    assert ctx.watch is ctx.watch
    assert ctx.skills is ctx.skills


def test_app_context_defaults_to_real_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    ctx = AppContext(Settings.from_env({"AWS_REGION": "eu-west-1"}))
    assert ctx.client("s3").meta.region_name == "eu-west-1"


# ---- Lambda entrypoints -------------------------------------------------------------------------


def test_handler_caches_application(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[int] = []

    class _App:
        def __init__(self, ctx: Any) -> None:
            built.append(1)

        def handle(self, event: dict[str, Any]) -> dict[str, Any]:
            return {"statusCode": 204}

    app_module._app.cache_clear()
    monkeypatch.setattr(app_module, "Application", _App)
    monkeypatch.setattr(app_module, "AppContext", lambda settings, request_scoped=False: None)
    assert app_module.handler({"rawPath": "/api/x"}, None)["statusCode"] == 204
    assert app_module.handler({}, None)["statusCode"] == 204
    assert built == [1]
    app_module._app.cache_clear()


def test_a_refused_agent_tool_call_logs_why(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """2026-10-31: six refused kg_add_evidence calls could not be explained, the log had only the status."""
    import json as _json
    import logging

    bodies = iter(
        [
            '{"code": "validation_failed", "message": "about: unknown node organization/x"}',
            '{"code": "validation_failed", "message": "node organization/IGNORE PRIOR RULES not found"}',
            '{"code": "validation_failed", "message": "linked nodes do not exist",'
            ' "details": {"missing": ["organization/acme-1", "organization/sk-live-AbC123"]}}',
            "not json",
            "[1]",
            None,
        ]
    )

    class _App:
        def __init__(self, ctx: Any) -> None:
            pass

        def handle(self, event: dict[str, Any]) -> dict[str, Any]:
            return {"statusCode": 400, "body": next(bodies)}

    app_module._app.cache_clear()
    monkeypatch.setattr(app_module, "Application", _App)
    monkeypatch.setattr(app_module, "AppContext", lambda settings, request_scoped=False: None)
    with caplog.at_level(logging.INFO, logger=app_module.log.name):
        for _ in range(6):
            app_module.handler({"rawPath": "/agent/tools/kg_add_evidence"}, None)
    lines = [_json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]
    assert [x["refusal"] for x in lines] == [
        "validation_failed",  # no message text: what the model sent stays out of the log (code review r18)
        "validation_failed",
        "validation_failed (2 missing)",
        "",
        "",
        "",
    ]
    app_module._app.cache_clear()


def test_unhandled_errors_are_generic_500(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Ctx:
        settings = Settings.from_env({"AIR_ALLOWED_ORIGIN": "https://a.example.com"})

    monkeypatch.setattr(app_module, "build_router", lambda ctx: Router())
    monkeypatch.setattr(app_module, "AgentTools", lambda ctx: None)
    application = app_module.Application(_Ctx())

    def boom(req: Request) -> Any:
        raise RuntimeError("secret detail")

    application.router.add("GET", "/api/boom", boom)
    res = application.handle(
        {
            "rawPath": "/api/boom",
            "requestContext": {"http": {"method": "GET"}, "authorizer": {"jwt": {"claims": {"sub": "u"}}}},
        }
    )
    assert res["statusCode"] == 500 and "secret" not in res["body"]
    assert json.loads(res["body"]) == {"code": "internal", "message": "something went wrong"}


def test_worker_context_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    workers._ctx.cache_clear()
    monkeypatch.setattr(workers, "AppContext", lambda settings: object())
    assert workers._ctx() is workers._ctx()
    workers._ctx.cache_clear()


def test_index_worker_wires_s3_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Indexer:
        def __init__(self, index: Any, embedder: Any, on_failure: Any = None) -> None:
            seen["index"] = index
            seen["on_failure"] = on_failure

        def handle(self, event: dict[str, Any]) -> Any:
            class _R:
                indexed, deleted, skipped, failed_ids = 1, 0, 0, ["n1"]

                @staticmethod
                def batch_response() -> dict[str, Any]:
                    return {"batchItemFailures": [{"itemIdentifier": "seq"}]}

            return _R()

    class _Ctx:
        settings = Settings.from_env({"AIR_VECTOR_BUCKET": "vb"})
        graph_table = ops_table = graph = None

        @staticmethod
        def client(name: str) -> Any:
            return name

    class _Reindexer:
        def __init__(self, *a: Any) -> None:
            pass

        def record_failure(self, node_id: str) -> None:
            seen.setdefault("recorded", []).append(node_id)

        def failed(self) -> dict[str, Any]:
            return {"indexed": 2, "failed": [], "more": False}

        def all(self, cursor: Any) -> dict[str, Any]:
            return {"indexed": 1, "failed": ["n9"], "cursor": cursor}

    monkeypatch.setattr(workers, "StreamIndexer", _Indexer)
    monkeypatch.setattr(workers, "Reindexer", _Reindexer)
    assert workers.index({"Records": []}, None, _Ctx()) == {"batchItemFailures": [{"itemIdentifier": "seq"}]}
    assert seen["index"].__class__.__name__ == "S3VectorsIndex"
    seen["on_failure"]("signal/1")  # a failed record is handed to the reindexer's list (CTR HC-074)
    assert seen["recorded"] == ["signal/1"]
    # Invoked by hand (CTR HC-106).
    assert workers.index({"reindex": "failed"}, None, _Ctx())["indexed"] == 2
    assert workers.index({"reindex": "all", "cursor": {"pk": "x"}}, None, _Ctx())["cursor"] == {"pk": "x"}
    with pytest.raises(ValueError, match="reindex must be"):
        workers.index({"reindex": "everything"}, None, _Ctx())


@pytest.mark.parametrize("region", ["us-east-1", "us-west-2", "eu-west-1"])
def test_presigned_uploads_use_the_regional_endpoint(region: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside us-east-1 a new bucket answers the global name with a 307 that a browser POST cannot follow."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    ctx = AppContext(Settings.from_env({"AWS_REGION": region}))
    url = ctx.s3.generate_presigned_post("assets-bucket", "docs/p/d/spec.txt")["url"]
    assert url == f"https://assets-bucket.s3.{region}.amazonaws.com/"
