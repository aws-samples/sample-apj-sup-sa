# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Branch cases: the 'nothing to do' side of conditionals."""

from __future__ import annotations

from typing import Any

import boto3
import pytest

from insights_radar.api import workers
from insights_radar.api.app import Application
from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ConflictError
from insights_radar.core.html import HtmlSanitizer
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.graph.repository import GraphRepository
from insights_radar.identity.principal import Principal, PrincipalKind, Role
from insights_radar.identity.users import UserAdmin
from insights_radar.product.model import IngestionStatus, ProductProfile
from insights_radar.radars.service import MemoryBlobStore, RadarService
from tests.integration.harness import Client, TestContext, ensure_running
from tests.unit.test_failure_paths import THROTTLE, Chaos


class _PagedCognito:
    """moto paginates ``list_users_in_group``; force one user per page so the loop runs."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._real, name)
        if name == "list_users_in_group":
            return lambda **kw: attr(**{**kw, "Limit": 1})
        return attr


def test_group_listings_follow_pages(aws: Any) -> None:
    c = boto3.client("cognito-idp", region_name="us-east-1")
    pool = c.create_user_pool(PoolName="p")["UserPool"]["Id"]
    for g in ("admin", "editor"):
        c.create_group(UserPoolId=pool, GroupName=g)
    users = UserAdmin(_PagedCognito(c), pool)
    a = users.invite("a@example.com", Role.ADMIN)
    b = users.invite("b@example.com", Role.ADMIN)
    e = users.invite("e@example.com", Role.EDITOR)
    assert {u.role for u in users.list_page()[0]} == {Role.ADMIN, Role.EDITOR}
    assert users.set_role(e.username, Role.EDITOR).role is Role.EDITOR  # same role: nothing removed
    assert users.set_role(a.username, Role.VIEWER).role is Role.VIEWER
    with pytest.raises(ConflictError):
        users.set_enabled(b.username, False)  # b is now the only admin


def test_sanitizer_ignores_self_closing_non_br() -> None:
    assert HtmlSanitizer.sanitize("a<img src=x />b<br/>c") == "ab<br>c"


def test_graph_branch_cases(graph_table: Any, clock: FixedClock) -> None:
    repo = GraphRepository(graph_table, clock)
    a, _ = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Desks", attributes={"k": "v"}), "u")
    same, created = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="desks", attributes={"k": "w"}), "u")
    assert not created and same.attributes == {"k": "v"} and same.version == 1
    b, _ = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Chairs"), "u")
    repo.link(a.id, EdgeType.RELATED_TO, b.id, "u")
    assert repo.delete(a.id) == 1 and repo.get(b.id).in_degree == 0
    for i in range(3):
        repo.create(Node(id="", type=NodeType.SIGNAL, title=f"s{i}"), "u")
    assert len(list(repo.iterate(NodeType.SIGNAL, cap=2))) == 2


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> TestContext:
    return TestContext(graph_table, ops_table, clock)


def test_sweep_without_knowledge_base_or_finished_ingestion(ctx: TestContext) -> None:
    ctx.products.create(ProductProfile("", "DeskPro", "Desk"), "u")
    ctx.knowledge.source_status = lambda source_id=None: (IngestionStatus.INDEXING, None)  # type: ignore[method-assign]
    assert workers.sweep({}, None, ctx)["documents_marked"] == 0
    ctx.knowledge = None  # type: ignore[assignment]
    assert workers.sweep({}, None, ctx)["documents_marked"] == 0


def test_tool_branches_without_optional_inputs(ctx: TestContext) -> None:
    api = Client(Application(ctx))
    ensure_running(ctx, "trend", "r")  # progress writes need a live run
    tok = ctx.tokens.issue("trend", "r", ctx.modules.get("trend").tools, 600)
    s, body = api.tool("run_context", tok)
    assert s == 200 and "product" not in body["result"]
    s, body = api.tool("progress_update", tok, {"remaining": 3})
    assert s == 200 and body["result"]["remaining_history"] == [3]


def test_product_without_urls_and_resync_without_crawl(ctx: TestContext) -> None:
    p = ctx.products.create(ProductProfile("", "DeskPro", "Desk"), "u")
    assert p.web_status is IngestionStatus.NONE
    ctx.products.update(p.id, {"summary": "Standing desk"}, 1)
    ctx.products.resync(p.id)


def test_schema_update_with_renames_via_api(ctx: TestContext) -> None:
    api = Client(Application(ctx))
    api.call("POST", "/api/radars", {"slug": "r", "name": "R", "template": "competitor_landscape"})
    s, r, _ = api.call("GET", "/api/radars/r")
    schema = r["schema"]
    sectors = [d["name"] for d in schema["sectors"]]
    api.call(
        "PUT",
        "/api/radars/r/entries",
        {
            "entries": [{"key": "k", "title": "Acme", "sector": sectors[0], "ring": schema["rings"][0]["name"]}],
            "expected_version": 1,
        },
    )
    new = {**schema, "sectors": ["Core", *sectors[1:]]}
    s, out, _ = api.call(
        "PUT",
        "/api/radars/r/schema",
        {"schema": new, "expected_version": 2, "renames": {"sector": {sectors[0]: "Core"}}},
    )
    assert s == 200, out
    assert api.call("GET", "/api/radars/r")[1]["entries"][0]["sector"] == "Core"


def test_radar_meta_write_reraises_unexpected_errors(ops_table: Any, clock: FixedClock) -> None:
    owner = Principal(PrincipalKind.USER, "own", Role.EDITOR, "owner@example.com")
    store = MemoryBlobStore()
    RadarService(ops_table, store, clock).create(owner, "r", "R", template="blank")
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError):
        RadarService(Chaos(ops_table, {"put_item": THROTTLE}), store, clock).update_meta(owner, "r", {"name": "R2"}, 1)


def test_hc123_escaped_markup_is_decoded_once_when_there_is_no_real_tag() -> None:
    from insights_radar.core.html import HtmlSanitizer

    copied = "&lt;p&gt;Now &lt;strong&gt;first&lt;/strong&gt;&lt;/p&gt;"
    assert HtmlSanitizer.sanitize(copied) == "<p>Now <strong>first</strong></p>"
    # Escaped tags that are not allowed stay text, as do comparisons, and real markup is left alone.
    for kept in ("&lt;script&gt;x&lt;/script&gt;", "5 &lt; 6 &amp;&amp; 7 &gt; 3", "<p>&lt;p&gt; literal</p>"):
        assert HtmlSanitizer.sanitize(kept) == kept
    for s in (copied, "&lt;script&gt;x&lt;/script&gt;"):
        once = HtmlSanitizer.sanitize(s)
        assert HtmlSanitizer.sanitize(once) == once
