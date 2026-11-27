# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Regression tests for the red-team review r2 (backend findings B2-xxx).

Each test names the finding it closes; the scenario is the one the review reproduced.
"""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from boto3.dynamodb.conditions import Key

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ConflictError, RateLimitedError, ValidationError
from insights_radar.core.urls import UrlGuard
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.modules.model import ToolGrant
from insights_radar.product.model import ProductProfile
from insights_radar.radars.model import RadarEntry
from tests.integration.harness import Client, TestContext, ensure_running

URL = "https://news.example.com/a"
URL2 = "https://news.example.com/b"


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> TestContext:
    return TestContext(graph_table, ops_table, clock)


@pytest.fixture
def api(ctx: TestContext) -> Client:
    from insights_radar.api.app import Application

    return Client(Application(ctx))


def _tok(
    ctx: TestContext,
    slug: str,
    run_id: str = "run-1",
    product_id: str | None = None,
    target_id: str | None = None,
) -> str:
    ensure_running(ctx, slug, run_id, target_id)
    return ctx.tokens.issue(slug, run_id, ctx.modules.get(slug).tools, 600, product_id)


def _custom(api: Client, slug: str, grants: list[str], **extra: Any) -> dict[str, Any]:
    body = {"slug": slug, "name": slug, "insight_kind": slug.replace("-", "_") + "_k", "grants": grants, **extra}
    status, created, _ = api.call("POST", "/api/modules", body)
    assert status == 201, created
    return dict(created)


# ---- B2-001: credential forms the scrub has to match ------------------------------------------


def test_b2_001_secret_values_cover_the_encoded_form_of_a_base64_key() -> None:
    stored = "https://api.example.com/v1/items?q=ai&api_key=Zm9v%2BYmFy%2F%3D%3D"
    forms = UrlGuard.secret_values(stored)
    assert "Zm9v+YmFy/==" in forms  # the decoded value, as before
    assert "Zm9v%2BYmFy%2F%3D%3D" in forms  # and the form the URL and any echoed link carry
    # What the runtime scrubs page results with now also catches the fetched URL itself.
    parts = urlsplit(stored)
    pairs = [*parse_qsl(parts.query, keep_blank_values=True), ("page", "2")]
    fetched = urlunsplit(parts._replace(query=urlencode(pairs)))
    scrubbed = fetched
    for value in forms:
        scrubbed = scrubbed.replace(value, "REDACTED")
    assert "Zm9v" not in scrubbed


def test_b2_001_secret_values_are_longest_first_and_skip_empty_values() -> None:
    assert UrlGuard.secret_values("https://x.example.com/a?token=&b=1") == []
    forms = UrlGuard.secret_values("https://x.example.com/a?token=abcd+efgh&key=abcdefgh")
    assert forms == sorted(forms, key=len, reverse=True)
    assert "abcd efgh" in forms and "abcd%20efgh" in forms and "abcd+efgh" in forms
    assert UrlGuard.secret_values("https://[::1/a?key=1") == []  # unparseable: nothing to scrub


# ---- B2-002 / B2-004: what a module is told about a source address -----------------------------


SHAPES = [
    "https://api.openweathermap.org/data/2.5/weather?q=x&appid=SECRET1KEY",
    "https://fn.azurewebsites.net/api/feed?code=SECRET2KEY",
    "https://gitlab.example.com/api/v4/projects?private_token=SECRET3KEY",
    "https://api.example.com/v1/items?api_token=SECRET4KEY",
    "https://storage.googleapis.com/b/o?X-Goog-Signature=SECRET5KEY&X-Goog-Credential=c",
    "https://x.azure-api.net/feed?subscription_key=ok&Ocp-Apim-Subscription-Key=SECRET6KEY",
    "https://b.s3.amazonaws.com/feed.xml?AWSAccessKeyId=SECRET7KEY&Expires=1&Signature=x",
    "https://api.example.com/v1?consumer_secret=SECRET8KEY",
    "https://api.example.com/v1?apiToken=SECRET9KEY",
]
PATH_SHAPES = [
    "https://hooks.example.com/services/T000/B000/SECRET7KEY",
    "https://api.telegram.org/botSECRET8KEY/getUpdates",
    "https://feeds.example.com/feeds/SECRET9KEY/rss",
]


@pytest.mark.parametrize("address", SHAPES)
def test_b2_002_common_credential_parameter_names_are_redacted(address: str) -> None:
    redacted, changed = UrlGuard.redact_secrets(address)
    assert changed and "SECRET" not in redacted
    assert UrlGuard.secret_values(address)  # and the runtime can scrub it out of fetched text


@pytest.mark.parametrize("address", SHAPES + PATH_SHAPES)
def test_b2_002_source_list_shows_the_host_only(api: Client, ctx: TestContext, address: str) -> None:
    source = ctx.modules.add_source("scout", "Feed", _public_api(), address)
    status, body = api.tool("source_list", _tok(ctx, "scout"))
    assert status == 200
    shown = body["result"]["sources"][0]
    assert "SECRET" not in str(body)
    assert shown["address"] == f"https://{urlsplit(address).hostname}"
    assert shown["address_redacted"] is True
    # The runtime still resolves the real address for http_fetch, and only the runtime.
    status, resolved = api.tool("source_address", _tok(ctx, "scout"), {"source_id": source.id})
    assert status == 200 and resolved["result"]["address"] == address


def test_b2_002_origin_of_an_unparseable_address_is_redacted() -> None:
    assert UrlGuard.origin("https://[::1/a") == "REDACTED"
    assert UrlGuard.origin("not a url") == "REDACTED"
    assert UrlGuard.origin("https://feed.example.com:8443/a") == "https://feed.example.com"


def _public_api() -> Any:
    from insights_radar.modules.model import SourceType

    return SourceType.PUBLIC_API


# ---- B2-028: ";" as a query separator ----------------------------------------------------------


def test_b2_028_semicolon_separated_query_is_redacted() -> None:
    redacted, changed = UrlGuard.redact_secrets("https://api.example.com/v1/feed?q=ai;api_key=SECRET")
    assert changed and redacted == "https://api.example.com/v1/feed?q=ai;api_key=REDACTED"
    forms = UrlGuard.secret_values("https://api.example.com/v1/feed?q=ai;api_key=SECRETKEY")
    assert "SECRETKEY" in forms and "api_key=SECRETKEY" in forms and "q=ai" not in forms


def test_b2_028_redaction_keeps_every_other_value_byte_for_byte() -> None:
    # The old rebuild went through urlencode, which re-encoded untouched values.
    address = "https://api.example.com/v1?q=a%20b&token=T&tags=x,y&empty="
    assert UrlGuard.redact_secrets(address)[0] == "https://api.example.com/v1?q=a%20b&token=REDACTED&tags=x,y&empty="


# ---- B2-005: documentation links in a prompt block ---------------------------------------------


def test_b2_005_doc_url_tokens_do_not_reach_a_module_prompt(api: Client, ctx: TestContext) -> None:
    status, product, _ = api.call(
        "POST", "/api/products", {"name": "W", "doc_urls": ["https://docs.example.com/guide/?token=DOCSECRET"]}
    )
    assert status == 201, product
    scout = ctx.modules.get("scout")
    status, body, _ = api.call("PATCH", "/api/modules/scout", {"version": scout.version, "product_id": product["id"]})
    assert status == 200, body
    status, run_context = api.tool("run_context", _tok(ctx, "scout", product_id=product["id"]))
    assert status == 200
    assert "DOCSECRET" not in run_context["result"]["product"]
    # r2 review SR2-06: an outbound module sees the documentation host only.
    assert run_context["result"]["product"].endswith("Public documentation: https://docs.example.com")


def test_b2_005_the_private_prompt_block_redacts_doc_urls_too() -> None:
    profile = ProductProfile(
        id="p", name="W", notes="UNRELEASED", doc_urls=("https://docs.example.com/g?token=DOCSECRET",)
    )
    assert "DOCSECRET" not in profile.prompt_block()
    assert "UNRELEASED" in profile.prompt_block()


# ---- B2-003 / B2-018: the taint travels with the node, not with the module ---------------------


def test_b2_003_deleting_a_private_writer_keeps_its_nodes_hidden(api: Client, ctx: TestContext) -> None:
    _custom(api, "pw", ["kg_read", "kg_write_output", "product_knowledge"])
    status, out = api.tool(
        "kg_save_output", _tok(ctx, "pw"), {"title": "Pricing exposure", "summary": "INTERNAL list price 41k"}
    )
    assert status == 200, out
    node_id = out["result"]["id"]
    stored = ctx.graph.get(node_id)
    assert stored.private is True and stored.to_public()["private"] is True  # stamped at write time
    scout = _tok(ctx, "scout", "run-s")
    assert api.tool("kg_get", scout, {"id": node_id})[0] == 404
    assert api.call("DELETE", "/api/modules/pw")[0] == 200
    assert api.tool("kg_get", scout, {"id": node_id})[0] == 404
    # And a node with no stamp (written before the stamp existed) is still hidden by the tombstone.
    legacy = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="legacy", evidence_url="https://a.example.com/x", module="pw"),
        "module:pw",
    )
    ctx.unstamp(legacy.id)
    assert api.tool("kg_get", scout, {"id": legacy.id})[0] == 404


def test_b2_018_a_removed_product_knowledge_grant_only_hides_what_it_wrote_then(api: Client, ctx: TestContext) -> None:
    before = api.tool("kg_add_evidence", _tok(ctx, "scout"), {"type": "fact", "title": "public", "evidence_url": URL})
    assert before[0] == 200
    scout = ctx.modules.get("scout")
    status, body, _ = api.call(
        "PATCH",
        "/api/modules/scout",
        {"version": scout.version, "grants": ["kg_read", "kg_write_evidence", "sources", "product_knowledge"]},
    )
    assert status == 200, body
    during = api.tool(
        "kg_add_evidence", _tok(ctx, "scout", "run-pk"), {"type": "fact", "title": "p", "evidence_url": URL2}
    )
    assert during[0] == 200
    scout = ctx.modules.get("scout")
    status, body, _ = api.call(
        "PATCH",
        "/api/modules/scout",
        {"version": scout.version, "grants": ["kg_read", "kg_write_evidence", "sources", "web_fetch"]},
    )
    assert status == 200, body
    ctx.modules.seed_builtins()
    assert ctx.modules.get("scout").private_writer  # the informational sticky mark survives
    signal = _tok(ctx, "signal", "run-g")
    assert api.tool("kg_get", signal, {"id": before[1]["result"]["id"]})[0] == 200  # written before: visible
    assert api.tool("kg_get", signal, {"id": during[1]["result"]["id"]})[0] == 404  # written while private: hidden


# ---- B2-006 / B2-007: who may read private material ------------------------------------------


def test_b2_006_a_module_that_does_not_read_documents_cannot_read_gaps(api: Client, ctx: TestContext) -> None:
    gap = ctx.graph.create(
        Node(
            id="",
            type=NodeType.GAP,
            title="No SSO",
            summary="PRIVATE: SSO slips to Q3",
            attributes={"request_title": "x", "problem": "y", "impact": "z"},
            module="gap",
        ),
        "module:gap",
    )
    _custom(api, "summ", ["kg_read", "kg_write_output"])
    token = _tok(ctx, "summ")
    assert api.tool("kg_get", token, {"id": gap.id})[0] == 404
    assert api.tool("kg_recent", token, {"type": "gap"})[1]["result"]["items"] == []


def test_b2_007_product_notes_only_reach_a_module_that_reads_the_documents(api: Client, ctx: TestContext) -> None:
    status, product, _ = api.call("POST", "/api/products", {"name": "Widget", "notes": "UNRELEASED: v3 drops on-prem"})
    assert status == 201, product
    _custom(api, "fit", ["kg_read", "kg_write_output"], product_id=product["id"])
    status, run_context = api.tool("run_context", _tok(ctx, "fit", product_id=product["id"]))
    assert status == 200
    assert "UNRELEASED" not in run_context["result"]["product"]
    assert run_context["result"]["product"] == "Product: Widget"
    # A module that does read them gets the notes, and whatever it writes is stamped private.
    _custom(api, "deep", ["kg_read", "kg_write_output", "product_knowledge"], product_id=product["id"])
    status, run_context = api.tool("run_context", _tok(ctx, "deep", "run-d", product_id=product["id"]))
    assert status == 200 and "UNRELEASED" in run_context["result"]["product"]


# ---- B2-008 / B2-035 / B2-039 / B2-079: grant combinations the validator now refuses ----------


def test_b2_008_a_draft_module_cannot_hold_an_outbound_grant(api: Client, ctx: TestContext) -> None:
    draft = ctx.modules.get("draft")
    status, body, _ = api.call(
        "PATCH",
        "/api/modules/draft",
        {"version": draft.version, "grants": ["kg_read", "kg_write_output", "content_draft", "web_fetch"]},
    )
    assert status == 400 and "content_draft cannot be combined with an outbound tool" in str(body)


def test_b2_008_draft_get_idea_applies_the_hider(api: Client, ctx: TestContext) -> None:
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="Launch post",
            summary="PRIVATE launch date 3 Nov",
            attributes={"content_type": "blog", "audience": "devs"},
            module="content",
        ),
        "module:content",
    )
    private_gap = ctx.graph.create(
        Node(
            id="",
            type=NodeType.GAP,
            title="No SSO",
            attributes={"request_title": "x", "problem": "y", "impact": "z"},
            module="gap",
        ),
        "module:gap",
    )
    ctx.graph.link(idea.id, EdgeType.ADDRESSES, private_gap.id, "module:content")
    # The built-in draft module reads product knowledge, so it still sees both.
    status, body = api.tool("draft_get_idea", _tok(ctx, "draft", target_id=idea.id), {"id": idea.id})
    assert status == 200 and body["result"]["evidence"]["addresses"][0]["node"]["id"] == private_gap.id
    # A module without that grant cannot reach the idea through this tool either.
    plain = _custom(api, "nodraft", ["kg_read", "kg_write_output"])
    assert plain["slug"] == "nodraft"
    ctx.modules.update("draft", {"grants": [ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT, ToolGrant.CONTENT_DRAFT]}, 1)
    assert api.tool("draft_get_idea", _tok(ctx, "draft", "run-2", target_id=idea.id), {"id": idea.id})[0] == 404


def test_b2_035_a_curator_cannot_hold_an_outbound_grant(api: Client, ctx: TestContext) -> None:
    curator = ctx.modules.get("curator")
    status, body, _ = api.call(
        "PATCH", "/api/modules/curator", {"version": curator.version, "grants": ["kg_read", "radar_suggest", "mcp"]}
    )
    assert status == 400 and "radar_suggest cannot be combined with an outbound tool" in str(body)


def test_b2_039_an_outbound_module_cannot_declare_private_input_types(api: Client, ctx: TestContext) -> None:
    scout = ctx.modules.get("scout")
    status, body, _ = api.call(
        "PATCH", "/api/modules/scout", {"version": scout.version, "input_types": ["gap", "content_draft"]}
    )
    assert status == 400 and "cannot take content_draft, gap as input types" in str(body)


def test_b2_079_drafts_cannot_be_created_with_kg_save_output(api: Client, ctx: TestContext) -> None:
    status, body = api.tool("kg_save_output", _tok(ctx, "draft"), {"title": "Orphan draft"})
    assert status == 403 and "draft_save" in str(body)


# ---- B2-032 / B2-033 / B2-034: the hidden graph as an oracle ----------------------------------


def test_b2_032_upsert_does_not_report_a_hidden_entity(api: Client, ctx: TestContext) -> None:
    _custom(api, "pwe", ["kg_read", "kg_write_output", "kg_write_evidence", "product_knowledge"])
    status, entity = api.tool(
        "kg_upsert_entity", _tok(ctx, "pwe"), {"type": "product", "name": "Project Falcon", "summary": "unreleased"}
    )
    assert status == 200
    scout = _tok(ctx, "scout", "run-s")
    assert api.tool("kg_get", scout, {"id": entity["result"]["id"]})[0] == 404
    status, body = api.tool("kg_upsert_entity", scout, {"type": "product", "name": "project falcon"})
    assert status == 403 and entity["result"]["id"] not in str(body)


def test_b2_033_a_hidden_root_is_not_found_and_is_not_traversed(api: Client, ctx: TestContext) -> None:
    public = Node(id="", type=NodeType.SIGNAL, title="Public signal", module="signal")
    signal = ctx.graph.create(public, "module:signal")
    far = ctx.graph.create(Node(id="", type=NodeType.TREND, title="Public trend", module="trend"), "module:trend")
    gap = ctx.graph.create(
        Node(
            id="",
            type=NodeType.GAP,
            title="No SSO",
            attributes={"request_title": "x", "problem": "y", "impact": "z"},
            module="gap",
        ),
        "module:gap",
    )
    ctx.graph.link(gap.id, EdgeType.ADDRESSES, signal.id, "module:gap")
    ctx.graph.link(far.id, EdgeType.SUPPORTS, gap.id, "module:trend")
    scout = _tok(ctx, "scout")
    assert api.tool("kg_neighbors", scout, {"id": gap.id})[0] == 404
    # And depth 2 from a visible node does not reach what only the hidden gap connects to.
    status, hood = api.tool("kg_neighbors", scout, {"id": signal.id, "depth": 2})
    assert status == 200 and [n["id"] for n in hood["result"]["nodes"]] == [signal.id]
    assert hood["result"]["edges"] == []


def test_b2_034_an_outbound_module_cannot_link_to_a_hidden_node(api: Client, ctx: TestContext) -> None:
    _custom(api, "pwx", ["kg_read", "kg_write_output", "kg_write_evidence", "product_knowledge"])
    status, entity = api.tool("kg_upsert_entity", _tok(ctx, "pwx"), {"type": "product", "name": "Project Falcon"})
    assert status == 200
    scout = _tok(ctx, "scout")
    status, body = api.tool(
        "kg_add_evidence",
        scout,
        {
            "type": "fact",
            "title": "Falcon leaked",
            "evidence_url": "https://attacker.example/x",
            "about": [entity["result"]["id"]],
        },
    )
    assert status == 400 and "do not exist" in str(body)
    # kg_link refuses the same target.
    status, evidence = api.tool(
        "kg_add_evidence",
        scout,
        {"type": "fact", "title": "F2", "evidence_url": "https://attacker.example/y"},
    )
    assert status == 200
    status, body = api.tool(
        "kg_link", scout, {"from": evidence["result"]["id"], "type": "about", "to": entity["result"]["id"]}
    )
    assert status == 404


# ---- B2-036 / B2-037: a deleted module's names stay reserved ----------------------------------


def test_b2_036_a_deleted_insight_kind_cannot_be_reused(api: Client, ctx: TestContext) -> None:
    status, _, _ = api.call(
        "POST",
        "/api/modules",
        {"slug": "old", "name": "old", "insight_kind": "pricing", "grants": ["kg_read", "kg_write_output"]},
    )
    assert status == 201
    assert api.call("DELETE", "/api/modules/old")[0] == 200
    status, body, _ = api.call(
        "POST",
        "/api/modules",
        {"slug": "fresh", "name": "fresh", "insight_kind": "pricing", "grants": ["kg_read", "kg_write_output"]},
    )
    assert status == 409 and "was used by a deleted module" in str(body)


def test_b2_037_a_deleted_slug_stays_reserved_after_its_runs_expire(api: Client, ctx: TestContext) -> None:
    status, _, _ = api.call(
        "POST",
        "/api/modules",
        {"slug": "gone", "name": "gone", "insight_kind": "gone_k", "grants": ["kg_read", "kg_write_output"]},
    )
    assert status == 201
    assert api.call("DELETE", "/api/modules/gone")[0] == 200
    slugs, kinds, writers = ctx.modules.tombstones()
    assert slugs == frozenset({"gone"}) and kinds == frozenset({"gone_k"}) and writers == frozenset()
    status, body, _ = api.call(
        "POST",
        "/api/modules",
        {"slug": "gone", "name": "gone again", "insight_kind": "other_k", "grants": ["kg_read", "kg_write_output"]},
    )
    assert status == 409 and "was used by a deleted module" in str(body)


# ---- B2-078: kg_update_output checks its links first ------------------------------------------


def test_b2_078_a_bad_link_does_not_land_the_update(api: Client, ctx: TestContext) -> None:
    token = _tok(ctx, "signal")
    status, out = api.tool("kg_save_output", token, {"title": "Sig", "summary": "v1"})
    assert status == 200
    node_id = out["result"]["id"]
    status, body = api.tool(
        "kg_update_output",
        token,
        {
            "id": node_id,
            "summary": "v2",
            "expected_version": 1,
            "supports": ["fact/01900000-0000-7000-8000-000000000000"],
        },
    )
    assert status == 400 and "do not exist" in str(body)
    assert ctx.graph.get(node_id).summary == "v1" and ctx.graph.get(node_id).version == 1


# ---- B2-080 / B2-101: a draft is bound to one idea and never left orphaned ---------------------


def test_b2_101_a_draft_run_without_a_target_cannot_save(api: Client, ctx: TestContext) -> None:
    # Since CTR H-026 such a run is refused at start (test_h026_*); one stored before the rule, with no
    # target recorded, still cannot save.
    run = ctx.module_service.start("draft", "manual", "u-admin", "content_idea/elsewhere")
    ctx.modules.put_run(replace(run, target_id=None))
    token = ctx.tokens.issue("draft", run.run_id, ctx.modules.get("draft").tools, 600)
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            attributes={"content_type": "b", "audience": "a"},
            module="content",
        ),
        "module:content",
    )
    status, body = api.tool("draft_save", token, {"idea_id": idea.id, "title": "T", "markdown": "z" * 300})
    assert status == 403 and "started for one content idea" in str(body)


def test_b2_080_a_draft_whose_link_fails_leaves_nothing_behind(api: Client, ctx: TestContext) -> None:
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            attributes={"content_type": "b", "audience": "a"},
            module="content",
        ),
        "module:content",
    )
    token = _tok(ctx, "draft", target_id=idea.id)
    real_link = ctx.graph.link

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("link failed")

    ctx.graph.link = boom  # type: ignore[method-assign]
    try:
        status, _ = api.tool("draft_save", token, {"idea_id": idea.id, "title": "T", "markdown": "z" * 300})
    finally:
        ctx.graph.link = real_link  # type: ignore[method-assign]
    assert status == 500
    assert list(ctx.graph.iterate(NodeType.CONTENT_DRAFT)) == []
    assert "Contents" not in ctx.s3.list_objects_v2(Bucket="example-assets-111122223333-us-east-1", Prefix="drafts/")


# ---- B2-009 / B2-010 / B2-040 / B2-104: what a deploy's seed may change -----------------------


def test_b2_009_seed_does_not_re_add_grants_an_admin_removed(api: Client, ctx: TestContext) -> None:
    scout = ctx.modules.get("scout")
    status, body, _ = api.call(
        "PATCH",
        "/api/modules/scout",
        {"version": scout.version, "grants": ["kg_read", "kg_write_evidence", "sources", "web_fetch"]},
    )
    assert status == 200, body
    report = ctx.modules.seed_builtins("system:deploy")
    assert report.migrated == {} and report.failed == {}
    grants = ctx.modules.get("scout").to_public()["grants"]
    assert "browser" not in grants and "web_search" not in grants


def test_b2_009_seed_keeps_mcp_servers_and_input_types_a_manager_set(api: Client, ctx: TestContext) -> None:
    signal = ctx.modules.get("signal")
    status, body, _ = api.call(
        "PATCH",
        "/api/modules/signal",
        {
            "version": signal.version,
            "grants": [*sorted(g.value for g in signal.grants), "mcp"],
            "mcp_servers": [{"name": "news", "url": "https://mcp.example.com/sse"}],
            "input_types": ["fact"],
        },
    )
    assert status == 200, body
    assert ctx.modules.seed_builtins("system:deploy").migrated == {}
    after = ctx.modules.get("signal")
    assert [s.name for s in after.mcp_servers] == ["news"] and after.input_types == (NodeType.FACT,)


def test_b2_009_seed_still_removes_the_unsafe_half_of_a_pair(ctx: TestContext) -> None:
    from dataclasses import replace

    from insights_radar.modules.repository import ModuleRepository

    # A gap module (its job is the documents) that also gained a web tool loses the web tool...
    gap = ctx.modules.get("gap")
    ctx.ops_table.put_item(Item=ModuleRepository._def_item(replace(gap, grants=gap.grants | {ToolGrant.WEB_FETCH})))
    # ... and a scout (its job is the web) that gained product_knowledge loses that instead.
    scout = ctx.modules.get("scout")
    ctx.ops_table.put_item(
        Item=ModuleRepository._def_item(replace(scout, grants=scout.grants | {ToolGrant.PRODUCT_KNOWLEDGE}))
    )
    report = ctx.modules.seed_builtins("system:deploy")
    assert report.migrated == {
        "gap": {"grants_removed": ["web_fetch"]},
        "scout": {"grants_removed": ["product_knowledge"]},
    }
    assert ToolGrant.WEB_FETCH not in ctx.modules.get("gap").grants
    assert ToolGrant.PRODUCT_KNOWLEDGE not in ctx.modules.get("scout").grants


def test_b2_040_an_invalid_stored_builtin_no_longer_aborts_the_seed(ctx: TestContext) -> None:
    from dataclasses import replace

    from insights_radar.modules.repository import ModuleRepository

    scout = ctx.modules.get("scout")
    ctx.ops_table.put_item(Item=ModuleRepository._def_item(replace(scout, model_key="retired-model")))
    gap = ctx.modules.get("gap")
    ctx.ops_table.put_item(
        Item=ModuleRepository._def_item(replace(gap, grants=gap.grants | {ToolGrant.WEB_FETCH}, version=9))
    )
    report = ctx.modules.seed_builtins("system:deploy")
    assert report.failed == {} and report.migrated == {"gap": {"grants_removed": ["web_fetch"]}}
    assert ToolGrant.WEB_FETCH not in ctx.modules.get("gap").grants  # migrated, despite scout's bad model


def test_b2_010_a_migration_is_audited_as_soon_as_it_lands(ctx: TestContext) -> None:
    from dataclasses import replace

    from insights_radar.api import workers
    from insights_radar.modules.repository import ModuleRepository

    gap = ctx.modules.get("gap")
    ctx.ops_table.put_item(
        Item=ModuleRepository._def_item(replace(gap, grants=gap.grants | {ToolGrant.WEB_FETCH}, version=3))
    )
    content = ctx.modules.get("content")
    ctx.ops_table.put_item(
        Item=ModuleRepository._def_item(replace(content, grants=content.grants | {ToolGrant.WEB_SEARCH}, version=3))
    )
    rows: list[tuple[str, str, dict[str, Any]]] = []
    real = ctx.audit.record_actor

    def recording(actor: str, email: str, action: str, target: str, details: Any = None) -> None:
        rows.append((action, target, dict(details or {})))
        if target == "gap":  # a failing audit write must not undo, hide or stop the change
            raise RuntimeError("audit unavailable")
        real(actor, email, action, target, details)

    ctx.audit.record_actor = recording  # type: ignore[method-assign]
    try:
        out = workers.seed({}, None, ctx)
    finally:
        ctx.audit.record_actor = real  # type: ignore[method-assign]
    assert out == {"created": [], "migrated": ["content", "gap"], "failed": [], "warnings": []}
    assert rows == [
        ("module.migrate", "gap", {"grants_removed": ["web_fetch"]}),
        ("module.migrate", "content", {"grants_removed": ["web_search"]}),
    ]
    assert ToolGrant.WEB_FETCH not in ctx.modules.get("gap").grants
    stored = [r for r in ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 100)[0] if r["action"].startswith("mod")]
    assert [r["target"] for r in stored] == ["content"]


# ---- radars ----------------------------------------------------------------------------------


def _unmatched_radar(api: Client, mode: str = "copilot") -> dict[str, Any]:
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "rr", "name": "RR", "template": "blank"})
    assert status == 201, radar
    entries = [
        {"key": "a", "title": "A", "sector": "General", "ring": "Now"},
        {"key": "b", "title": "B", "sector": "General", "ring": "Someday"},
    ]
    status, radar, _ = api.call(
        "PUT", "/api/radars/rr/entries", {"entries": entries, "expected_version": 1, "allow_unmatched": True}
    )
    assert status == 200, radar
    status, radar, _ = api.call(
        "PATCH", "/api/radars/rr", {"mode": mode, "auto_apply_agent": True, "version_lock": radar["version_lock"]}
    )
    assert status == 200, radar
    return dict(radar)


def test_b2_011_one_unmatched_entry_no_longer_blocks_every_accept(api: Client, ctx: TestContext) -> None:
    _unmatched_radar(api)
    sug = ctx.radars.propose(
        "rr", "module:curator", "run-c", {"title": "C", "sector": "General", "ring": "Next"}, "evidence says so"
    )
    status, body, _ = api.call("POST", f"/api/radars/rr/suggestions/{sug['entry_key']}/accept", {})
    assert status == 200, body
    assert sorted(e.title for e in ctx.radars.entries("rr")) == ["A", "B", "C"]
    assert ctx.radars.entries("rr")[1].ring == "Someday"  # the untouched row is left as it was


def test_b2_011_an_accept_whose_own_entry_is_unmatched_is_still_refused(api: Client, ctx: TestContext) -> None:
    _unmatched_radar(api)
    sug = ctx.radars.propose(
        "rr", "module:curator", "run-c", {"title": "C", "sector": "General", "ring": "Next"}, "evidence says so"
    )
    schema = {"sectors": ["General"], "rings": ["Now", "Later"]}
    status, body, _ = api.call(
        "PUT", "/api/radars/rr/schema", {"schema": schema, "expected_version": 2, "drop_unmatched": True}
    )
    assert status == 200, body
    ctx.radars._t.update_item(  # put the old ring name back on the proposal only
        Key={"pk": "RADAR#rr", "sk": f"SUG#{sug['entry_key']}"},
        UpdateExpression="SET proposal.#r = :v",
        ExpressionAttributeNames={"#r": "ring"},
        ExpressionAttributeValues={":v": "Nonsense"},
    )
    status, body, _ = api.call("POST", f"/api/radars/rr/suggestions/{sug['entry_key']}/accept", {})
    assert status == 400 and body["details"]["code"] == "unmatched_values"


def test_b2_012_autopilot_holds_a_proposal_it_cannot_apply(api: Client, ctx: TestContext) -> None:
    _unmatched_radar(api, mode="autopilot")
    evidence = [
        ctx.graph.create(
            Node(id="", type=NodeType.FACT, title=f"F{i}", evidence_url=u, module="scout", fetched=True),
            "module:scout",
        )
        for i, u in enumerate((URL, "https://other.example.org/x"))
    ]
    ctx.radars.propose(
        "rr",
        "module:curator",
        "run-c",
        {"title": "C", "sector": "General", "ring": "Next"},
        "evidence says so",
        None,
        [e.id for e in evidence],
    )
    ctx.clock.advance(timedelta(days=8))
    from insights_radar.api.workers import _usable_evidence

    result = ctx.radars.autopilot_tick(usable_evidence=_usable_evidence(ctx), audit=ctx.audit)
    assert result["superseded"] == 0 and result["applied"] == 1  # B2-011: the unmatched row no longer blocks it
    assert ctx.radars.suggestions("rr") == []


def test_b2_049_a_concurrent_save_holds_the_suggestion(ctx: TestContext) -> None:
    from insights_radar.core.errors import ConflictError

    status = ctx.radars
    status.create(_admin(), "ap", "AP", template="blank")
    status.update_meta(_admin(), "ap", {"mode": "autopilot", "auto_apply_days": 1, "auto_apply_agent": True}, 1)
    fact = ctx.graph.create(Node(id="", type=NodeType.FACT, title="F", evidence_url=URL, module="scout"), "m")
    status.propose(
        "ap", "module:curator", "r", {"title": "New", "sector": "General", "ring": "Now"}, "because of reasons", None
    )
    ctx.clock.advance(timedelta(days=2))
    real = status._save

    def racing(*args: Any, **kwargs: Any) -> Any:
        raise ConflictError("someone else saved this radar; reload and retry")

    status._save = racing  # type: ignore[method-assign]
    try:
        result = status.autopilot_tick(usable_evidence=lambda ids: {fact.id: frozenset({"a.example.com"})})
    finally:
        status._save = real  # type: ignore[method-assign]
    assert result["superseded"] == 0 and len(status.suggestions("ap")) == 1


def test_b2_048_one_broken_radar_does_not_stop_the_sweep(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    for slug in ("ok1", "bad"):
        status, radar, _ = api.call("POST", "/api/radars", {"slug": slug, "name": slug, "template": "blank"})
        assert status == 201, radar
        status, radar, _ = api.call(
            "PATCH", f"/api/radars/{slug}", {"mode": "autopilot", "version_lock": radar["version_lock"]}
        )
        assert status == 200, radar
        ctx.radars.propose(
            slug, "module:curator", "run-c", {"title": "C", "sector": "General", "ring": "Next"}, "because of reasons"
        )
    del ctx.radars._blobs.objects["radars/bad/v000001.json"]
    ctx.clock.advance(timedelta(days=8))
    out = workers.sweep({}, None, ctx)  # no longer raises
    assert out["autopilot"]["errors"] == 1 and out["autopilot"]["held"] == 1


def test_b2_048_a_failing_sweep_step_does_not_stop_the_others(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bedrock unavailable")

    ctx.products.list_all = boom  # type: ignore[method-assign]
    ctx.products.due_for_crawl = boom  # type: ignore[method-assign]
    out = workers.sweep({}, None, ctx)
    assert out["documents_marked"] == 0 and out["crawls"] == 0
    assert out["modules"] and out["autopilot"] == {"applied": 0, "superseded": 0, "held": 0, "errors": 0}


def test_b2_050_autopilot_applies_at_most_a_bounded_number_per_radar(ctx: TestContext) -> None:
    from insights_radar.radars import service as radar_service

    ctx.radars.create(_admin(), "ap", "AP", template="blank")
    ctx.radars.update_meta(_admin(), "ap", {"mode": "autopilot", "auto_apply_days": 1, "auto_apply_agent": True}, 1)
    facts = [
        ctx.graph.create(
            Node(id="", type=NodeType.FACT, title=f"F{i}", evidence_url=f"https://s.example{i}.com/x"), "m"
        )
        for i in range(2)
    ]
    ids = [f.id for f in facts]
    for i in range(radar_service.MAX_AUTOPILOT_APPLIES + 3):
        ctx.radars.propose(
            "ap",
            "module:curator",
            "r",
            {"title": f"T{i}", "sector": "General", "ring": "Now"},
            "because of reasons",
            None,
            ids,
        )
    ctx.clock.advance(timedelta(days=2))
    from insights_radar.api.workers import _usable_evidence

    result = ctx.radars.autopilot_tick(usable_evidence=_usable_evidence(ctx))
    assert result["applied"] == radar_service.MAX_AUTOPILOT_APPLIES and result["held"] == 3


def test_b2_051_autopilot_needs_real_evidence_from_two_sites(ctx: TestContext) -> None:
    from insights_radar.api.workers import _usable_evidence

    ctx.radars.create(_admin(), "ap", "AP", template="blank")
    ctx.radars.update_meta(_admin(), "ap", {"mode": "autopilot", "auto_apply_days": 1, "auto_apply_agent": True}, 1)
    topic, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Anything"), "user:u-admin")
    one = ctx.graph.create(Node(id="", type=NodeType.FACT, title="F1", evidence_url=URL, module="scout"), "m")
    two = ctx.graph.create(Node(id="", type=NodeType.FACT, title="F2", evidence_url=URL2, module="scout"), "m")
    three = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="F3", evidence_url="https://other.example.org/x", module="scout"), "m"
    )
    check = _usable_evidence(ctx)
    assert check([topic.id]) == {}  # an entity is not evidence at all
    for n, cited in enumerate(([topic.id], [one.id], [one.id, two.id])):  # not evidence, one source, one site twice
        ctx.radars.propose(
            "ap",
            "module:curator",
            "r",
            {"title": f"T{n}", "sector": "General", "ring": "Now"},
            "reasons" * 2,
            None,
            cited,
        )
    ctx.clock.advance(timedelta(days=2))
    assert ctx.radars.autopilot_tick(usable_evidence=check) == {
        "applied": 0,
        "superseded": 0,
        "held": 3,
        "errors": 0,
    }
    # Two distinct sites is enough.
    ctx.radars.propose(
        "ap",
        "module:curator",
        "r",
        {"title": "Good", "sector": "General", "ring": "Now"},
        "reasons" * 2,
        None,
        [one.id, three.id],
    )
    ctx.clock.advance(timedelta(days=2))
    assert ctx.radars.autopilot_tick(usable_evidence=check)["applied"] == 1


def test_b2_052_the_unattended_opt_in_does_not_survive_a_transfer(api: Client, ctx: TestContext) -> None:
    status, bob, _ = api.call("POST", "/api/users", {"email": "bob@example.com", "role": "editor"})
    assert status == 201
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "ap", "name": "AP"})
    status, radar, _ = api.call(
        "PATCH",
        "/api/radars/ap",
        {"mode": "autopilot", "auto_apply_agent": True, "version_lock": radar["version_lock"]},
    )
    assert status == 200 and radar["auto_apply_agent"] is True
    status, radar, _ = api.call(
        "PUT",
        "/api/radars/ap/access",
        {"version_lock": radar["version_lock"], "owner": {"subject": bob["username"]}},
    )
    assert status == 200 and radar["auto_apply_agent"] is False


def test_b2_052_only_an_admin_shortens_the_review_delay_while_it_is_on(api: Client, ctx: TestContext) -> None:
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "ap", "name": "AP"}, role="editor")
    assert status == 201, radar
    status, radar, _ = api.call(
        "PATCH",
        "/api/radars/ap",
        {"mode": "autopilot", "auto_apply_agent": True, "version_lock": radar["version_lock"]},
    )
    assert status == 200, radar
    status, body, _ = api.call(
        "PATCH", "/api/radars/ap", {"auto_apply_days": 1, "version_lock": radar["version_lock"]}, role="editor"
    )
    assert status == 403 and "review delay" in str(body)
    assert api.call("PATCH", "/api/radars/ap", {"auto_apply_days": 1, "version_lock": radar["version_lock"]})[0] == 200


def test_b2_053_a_schema_rename_is_applied_to_open_suggestions(api: Client, ctx: TestContext) -> None:
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "t5", "name": "T", "template": "blank"})
    assert status == 201
    status, radar, _ = api.call(
        "PUT",
        "/api/radars/t5/entries",
        {"entries": [{"key": "a", "title": "A", "sector": "General", "ring": "Now"}], "expected_version": 1},
    )
    assert status == 200, radar
    status, radar, _ = api.call("PATCH", "/api/radars/t5", {"mode": "copilot", "version_lock": radar["version_lock"]})
    assert status == 200, radar
    ctx.radars.propose("t5", "module:curator", "run-c", {"ring": "Next"}, "move it because of reasons", "a")
    schema = {"sectors": ["General"], "rings": ["Now", "Soon", "Later"]}
    status, body, _ = api.call(
        "PUT",
        "/api/radars/t5/schema",
        {"schema": schema, "expected_version": 2, "renames": {"ring": {"Next": "Soon"}}},
    )
    assert status == 200, body
    assert ctx.radars.suggestions("t5")[0]["proposal"]["ring"] == "Soon"
    status, body, _ = api.call("POST", "/api/radars/t5/suggestions/a/accept", {})
    assert status == 200, body
    assert ctx.radars.entries("t5")[0].ring == "Soon"


def test_b2_054_a_full_radar_refuses_a_new_entry_proposal(ctx: TestContext) -> None:
    from insights_radar.core.errors import ConflictError
    from insights_radar.radars.model import MAX_ENTRIES

    ctx.radars.create(_admin(), "full", "Full", template="blank")
    entries = [RadarEntry(key=f"k{i}", title=f"T{i}", sector="General", ring="Now") for i in range(MAX_ENTRIES)]
    ctx.radars.save(_admin(), "full", entries, 1)
    ctx.radars.update_meta(_admin(), "full", {"mode": "copilot"}, 1)
    with pytest.raises(ConflictError, match="cannot be proposed"):
        ctx.radars.propose(
            "full", "module:curator", "r", {"title": "One more", "sector": "General", "ring": "Now"}, "reasons" * 3
        )
    # An edit to an existing entry is still allowed.
    assert (
        ctx.radars.propose("full", "module:curator", "r", {"ring": "Later"}, "reasons" * 3, "k0")["entry_key"] == "k0"
    )


def test_b2_059_a_replace_import_that_halves_the_radar_needs_confirmation(api: Client, ctx: TestContext) -> None:
    assert api.call("POST", "/api/radars", {"slug": "w1", "name": "W", "template": "blank"})[0] == 201
    entries = [{"key": f"k{i}", "title": f"T{i}", "sector": "General", "ring": "Now"} for i in range(50)]
    status, radar, _ = api.call("PUT", "/api/radars/w1/entries", {"entries": entries, "expected_version": 1})
    assert status == 200, radar
    body = {"format": "csv", "text": "title,sector,ring\nOnly,General,Now\n", "expected_version": 2}
    status, err, _ = api.call("POST", "/api/radars/w1/import", body)
    assert status == 400 and err["details"]["code"] == "shrinking_import"
    assert err["details"] == {"code": "shrinking_import", "before": 50, "after": 1, "removed": 50}
    dry = api.call("POST", "/api/radars/w1/import", {**body, "dry_run": True})[1]
    assert dry["before"] == 50 and dry["removed"] == 50  # r3 B3-047: by key, not by count
    status, radar, _ = api.call("POST", "/api/radars/w1/import", {**body, "allow_empty": True})
    assert status == 200 and radar["entry_count"] == 1


def test_b2_060_saving_unchanged_entries_writes_no_new_version(api: Client, ctx: TestContext) -> None:
    assert api.call("POST", "/api/radars", {"slug": "v1", "name": "V", "template": "blank"})[0] == 201
    entries = [{"key": "a", "title": "A", "sector": "General", "ring": "Now"}]
    status, first, _ = api.call("PUT", "/api/radars/v1/entries", {"entries": entries, "expected_version": 1})
    assert status == 200 and first["current_version"] == 2
    status, again, _ = api.call("PUT", "/api/radars/v1/entries", {"entries": entries, "expected_version": 2})
    assert status == 200 and again["current_version"] == 2
    # A real change still writes one.
    changed = [{**entries[0], "ring": "Later"}]
    status, third, _ = api.call("PUT", "/api/radars/v1/entries", {"entries": changed, "expected_version": 2})
    assert status == 200 and third["current_version"] == 3


def test_b2_100_a_lock_only_patch_is_refused(api: Client, ctx: TestContext) -> None:
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "lk", "name": "L"})
    assert status == 201
    status, body, _ = api.call("PATCH", "/api/radars/lk", {"version_lock": radar["version_lock"]})
    assert status == 400 and "nothing to change" in str(body)
    assert ctx.radars.get("lk").version_lock == radar["version_lock"]
    assert [r for r in _audit(ctx) if r["action"] == "radar.update"] == []


# ---- audit coverage of radar changes ---------------------------------------------------------


def test_b2_016_a_radar_patch_records_what_the_values_became(api: Client, ctx: TestContext) -> None:
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "aa", "name": "A"})
    assert status == 201
    status, radar, _ = api.call(
        "PATCH",
        "/api/radars/aa",
        {"auto_apply_agent": True, "mode": "autopilot", "version_lock": radar["version_lock"]},
    )
    assert status == 200, radar
    row = next(r for r in _audit(ctx) if r["action"] == "radar.update")
    assert row["details"]["auto_apply_agent"] == {"before": False, "after": True}
    assert row["details"]["mode"] == {"before": "manual", "after": "autopilot"}


def test_b2_055_b2_056_access_changes_name_the_editors_and_the_owner(api: Client, ctx: TestContext) -> None:
    status, bob, _ = api.call("POST", "/api/users", {"email": "bob@example.com", "role": "editor"})
    assert status == 201
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "t1", "name": "T"})
    status, radar, _ = api.call(
        "PUT",
        "/api/radars/t1/access",
        {"editors": ["a@example.com", "b@example.com"], "version_lock": radar["version_lock"], "confirm_unknown": True},
    )
    assert status == 200, radar
    # An owner transfer that does not mention editors leaves them alone.
    status, radar, _ = api.call(
        "PUT", "/api/radars/t1/access", {"owner": {"subject": bob["username"]}, "version_lock": radar["version_lock"]}
    )
    assert status == 200 and radar["editors"] == ["a@example.com", "b@example.com"]
    row = next(r for r in _audit(ctx) if r["action"] == "radar.access" and "owner" in r["details"])
    assert row["details"]["owner"] == {"before": "u-admin", "after": bob["username"]}
    # An explicit empty list still clears them, and the removals are named.
    status, radar, _ = api.call("PUT", "/api/radars/t1/access", {"editors": [], "version_lock": radar["version_lock"]})
    assert status == 200 and radar["editors"] == []
    row = next(r for r in _audit(ctx) if r["action"] == "radar.access" and "editors_removed" in r["details"])
    assert row["details"]["editors_removed"] == ["a@example.com", "b@example.com"]


def test_b2_057_discard_import_and_schema_rows_say_what_happened(api: Client, ctx: TestContext) -> None:
    status, radar, _ = api.call("POST", "/api/radars", {"slug": "t6", "name": "T", "template": "blank"})
    assert status == 201
    status, radar, _ = api.call(
        "PUT",
        "/api/radars/t6/entries",
        {"entries": [{"key": "a", "title": "A", "sector": "General", "ring": "Now"}], "expected_version": 1},
    )
    assert status == 200, radar
    status, radar, _ = api.call("PATCH", "/api/radars/t6", {"mode": "copilot", "version_lock": radar["version_lock"]})
    assert status == 200, radar
    sug = ctx.radars.propose(
        "t6", "module:curator", "run-c", {"title": "Z", "sector": "General", "ring": "Now"}, "an important proposal"
    )
    assert api.call("DELETE", f"/api/radars/t6/suggestions/{sug['entry_key']}")[0] == 200
    discard = next(r for r in _audit(ctx) if r["action"] == "suggestion.discard")
    assert discard["details"] == {"title": "Z", "proposed_by": "module:curator", "run_id": "run-c", "is_new": True}
    status, body, _ = api.call(
        "POST",
        "/api/radars/t6/import",
        {"format": "csv", "text": "title,sector,ring\n", "expected_version": 2, "allow_empty": True},
    )
    assert status == 200, body
    imported = next(r for r in _audit(ctx) if r["action"] == "radar.import")
    assert imported["details"]["entries_before"] == 1 and imported["details"]["entries_after"] == 0


def _admin() -> Any:
    from insights_radar.identity.principal import Principal, PrincipalKind, Role

    return Principal(PrincipalKind.USER, "u-admin", Role.ADMIN, "admin@example.com")


def _audit(ctx: TestContext) -> list[dict[str, Any]]:
    return ctx.audit.page(ctx.clock.now().strftime("%Y-%m-%d"), 1000)[0]


# ---- B2-013: a long details blob must not make a day's audit log a 500 ------------------------


def test_b2_013_long_audit_details_stay_valid_json(api: Client, ctx: TestContext) -> None:
    status, user, _ = api.call("POST", "/api/users", {"email": "bob@example.com", "role": "editor"})
    assert status == 201, user
    for i in range(36):
        slug = f"r{i:02d}-" + "x" * 56
        status, radar, _ = api.call("POST", "/api/radars", {"slug": slug, "name": f"R{i}"})
        assert status == 201, radar
        status, radar, _ = api.call(
            "PUT",
            f"/api/radars/{slug}/access",
            {"editors": ["bob@example.com"], "version_lock": radar["version_lock"]},
        )
        assert status == 200, radar
    assert api.call("DELETE", f"/api/users/{user['username']}")[0] == 200
    status, page, _ = api.call("GET", "/api/audit", query={"limit": "1000"})
    assert status == 200
    row = next(r for r in page["items"] if r["action"] == "user.delete")
    # r3 B3-044: the list continues on further rows instead of "... N more"; every row is JSON.
    assert len(row["details"]["editor_of"]) == 10 and row["details"]["continued_rows"] == 3
    assert sum(len(r["details"]["editor_of"]) for r in page["items"] if r["action"] == "user.delete.continued") == 26


def test_b2_013_a_row_that_is_not_json_does_not_fail_the_page(ctx: TestContext) -> None:
    from insights_radar.audit.log import _details_json, _shrink

    ctx.audit.record(_admin(), "test.row", "t", {"a": 1})
    day = ctx.clock.now().strftime("%Y-%m-%d")
    rows = ctx.audit.page(day, 100)[0]
    ctx.ops_table.put_item(Item={"pk": f"AUDIT#{day}", "sk": "9999#x", "action": "legacy", "details": '{"a'})
    page = ctx.audit.page(day, 100)[0]
    assert page[0]["details"] == {"unparsed": '{"a'} and page[0]["actor"] == ""
    assert rows[0]["details"] == {"a": 1}
    # Structural capping: deep maps, long strings and long lists all survive as valid JSON.
    assert _shrink({"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}})["a"]["b"]["c"]["d"] == "... 1 keys"
    assert _shrink("y" * 500) == "y" * 200
    assert _shrink(list(range(30)))[-1] == "... 20 more"
    assert json.loads(_details_json({f"k{i}": "v" * 300 for i in range(60)})) == {
        "truncated": True,
        "keys": sorted(f"k{i}" for i in range(60))[:20],
    }


# ---- B2-015: a refused agent tool call leaves a trail ----------------------------------------


def test_b2_015_denied_agent_calls_are_logged_and_audited(api: Client, ctx: TestContext) -> None:
    gap = ctx.graph.create(
        Node(
            id="",
            type=NodeType.GAP,
            title="G",
            attributes={"request_title": "x", "problem": "y", "impact": "z"},
            module="gap",
        ),
        "module:gap",
    )
    token = _tok(ctx, "scout")
    assert api.tool("kg_recent", token, {"type": "gap"})[0] == 403
    assert api.tool("kg_get", token, {"id": gap.id})[0] == 404
    assert api.tool("product_knowledge_search", token, {"query": "roadmap"})[0] == 403
    rows = [r for r in _audit(ctx) if r["action"] == "agent.denied"]
    assert sorted(r["target"] for r in rows) == ["kg_get", "kg_recent", "product_knowledge_search"]
    assert all(r["actor"] == "module:scout" and r["details"]["run_id"] == "run-1" for r in rows)


def test_b2_015_denials_are_capped_per_run(api: Client, ctx: TestContext) -> None:
    from insights_radar.api.agent_tools import MAX_DENIALS_AUDITED_PER_RUN

    token = _tok(ctx, "scout")
    for _ in range(MAX_DENIALS_AUDITED_PER_RUN + 4):
        assert api.tool("product_knowledge_search", token, {"query": "x"})[0] == 403
    rows = [r for r in _audit(ctx) if r["action"] == "agent.denied"]
    assert len(rows) == MAX_DENIALS_AUDITED_PER_RUN


def test_b2_015_the_denial_counter_is_bounded(api: Client, ctx: TestContext) -> None:
    from insights_radar.api.agent_tools import DENIAL_RUNS_TRACKED

    tools = api.app.tools
    tools._denials = {f"run-{i}": 1 for i in range(DENIAL_RUNS_TRACKED)}
    assert api.tool("product_knowledge_search", _tok(ctx, "scout"), {"query": "x"})[0] == 403
    assert tools._denials == {"run-1": 2}  # cleared, then this run counted (it was already in the map)


def test_b2_015_a_failing_denial_audit_still_refuses_the_call(api: Client, ctx: TestContext) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit unavailable")

    ctx.audit.record_actor = boom  # type: ignore[method-assign]
    assert api.tool("product_knowledge_search", _tok(ctx, "scout"), {"query": "x"})[0] == 403


# ---- B2-016: module, source and product changes record their values --------------------------


def test_b2_016_a_module_patch_records_the_grants_it_gained(api: Client, ctx: TestContext) -> None:
    status, module, _ = api.call(
        "POST",
        "/api/modules",
        {"slug": "cm", "name": "cm", "insight_kind": "cm_k", "grants": ["kg_read", "kg_write_output"]},
    )
    assert status == 201, module
    status, module, _ = api.call(
        "PATCH",
        "/api/modules/cm",
        {
            "version": module["version"],
            "grants": ["kg_read", "kg_write_output", "web_fetch", "browser", "mcp"],
            "mcp_servers": [{"name": "crm", "url": "https://mcp.example.com/sse"}],
            "enabled": True,
        },
    )
    assert status == 200, module
    row = next(r for r in _audit(ctx) if r["action"] == "module.update")
    assert row["details"]["grants_added"] == ["browser", "mcp", "web_fetch"]
    assert row["details"]["mcp_servers"] == {"before": [], "after": ["crm"]}
    assert row["details"]["enabled"] == {"before": False, "after": True}


def test_b2_016_a_source_patch_records_the_redacted_address(api: Client, ctx: TestContext) -> None:
    status, source, _ = api.call(
        "POST", "/api/modules/scout/sources", {"name": "n", "type": "rss", "address": "https://a.example.com/feed"}
    )
    assert status == 201, source
    status, updated, _ = api.call(
        "PATCH",
        f"/api/modules/scout/sources/{source['id']}",
        {"address": "https://b.example.com/feed?api_key=SECRET", "enabled": False},
    )
    assert status == 200, updated
    row = next(r for r in _audit(ctx) if r["action"] == "source.update")
    # r2 review SR2-08: the origin and a hash, so a path credential is not kept either.
    assert row["details"]["address"]["after"].startswith("https://b.example.com (sha256:")
    assert "SECRET" not in str(row["details"])
    assert row["details"]["enabled"] == {"before": True, "after": False}
    assert "SECRET" not in json.dumps(row)
    status, renamed, _ = api.call(
        "PATCH",
        f"/api/modules/scout/sources/{source['id']}",
        {"name": "renamed", "cursor": "2026-01-01", "type": "public_api"},
    )
    assert status == 200, renamed
    row = next(r for r in _audit(ctx) if r["action"] == "source.update" and "name" in r["details"])
    assert row["details"]["name"] == {"before": "n", "after": "renamed"}
    assert row["details"]["cursor"] == {"before": "", "after": "2026-01-01"}
    assert row["details"]["type"] == {"before": "rss", "after": "public_api"}


def test_b2_016_a_product_patch_records_the_redacted_doc_urls(api: Client, ctx: TestContext) -> None:
    status, product, _ = api.call("POST", "/api/products", {"name": "P"})
    assert status == 201, product
    status, updated, _ = api.call(
        "PATCH",
        f"/api/products/{product['id']}",
        {"version": product["version"], "name": "P2", "doc_urls": ["https://docs.example.com/?token=T"]},
    )
    assert status == 200, updated
    row = next(r for r in _audit(ctx) if r["action"] == "product.update")
    assert [u.split(" ")[0] for u in row["details"]["doc_urls"]] == ["https://docs.example.com"]
    assert "sha256:" in row["details"]["doc_urls"][0]
    assert row["details"]["name"] == {"before": "P", "after": "P2"}


# ---- B2-017 / B2-045 / B2-046: a run that never started gives its hold back -------------------


def test_b2_017_the_kill_switch_between_start_and_invoke_refunds_the_hold(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers
    from insights_radar.modules.repository import RunStatus

    run = ctx.module_service.start("scout", "manual", "u-admin")
    hold = ctx.modules.get_run("scout", run.run_id).budget_hold
    assert hold == 2_000_000
    assert api.call("PUT", "/api/andon", {"genai_enabled": False, "reason": "incident"})[0] == 200
    # The task is failed with GenAiDisabled rather than raised (CTR HC-011 follow-up).
    out = workers.invoke({"payload": ctx.launcher.payloads[-1], "task_token": "t", "runtime_arn": "arn:x"}, None, ctx)
    assert out == {"accepted": False, "reason": "genai_disabled"}
    # A finalize that still arrives finds the run already terminal: a no-op.
    workers.finalize(
        {"module": "scout", "run_id": run.run_id, "error": {"Error": "GenAiDisabledError", "Cause": "paused"}},
        None,
        ctx,
    )
    snap = ctx.modules.budget("scout", ctx.modules.get("scout").daily_token_budget)
    assert snap.consumed == 0 and snap.state.value == "open"
    done = ctx.modules.get_run("scout", run.run_id)
    assert done.status is RunStatus.CANCELLED and done.weighted_tokens == 0


def test_b2_046_a_refused_agentcore_invoke_refunds_the_hold(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    started = ctx.module_service.start("signal", "manual", "u-admin")
    assert ctx.modules.get_run("signal", started.run_id).budget_hold == 2_000_000

    from botocore.exceptions import ClientError

    class Refusing:
        def invoke_agent_runtime(self, **kwargs: Any) -> Any:
            raise ClientError(
                {"Error": {"Code": "ThrottlingException"}, "ResponseMetadata": {"HTTPStatusCode": 429}},
                "InvokeAgentRuntime",
            )

    real = ctx.client
    ctx.client = lambda name: Refusing() if name == "bedrock-agentcore" else real(name)  # type: ignore[method-assign]
    try:
        with pytest.raises(ClientError):
            workers.invoke({"payload": ctx.launcher.payloads[-1], "task_token": "t", "runtime_arn": "arn:x"}, None, ctx)
    finally:
        ctx.client = real  # type: ignore[method-assign]
    assert ctx.modules.budget("signal", 2_000_000).consumed == 0


def test_b2_045_a_failed_dispatch_releases_the_claim(ctx: TestContext) -> None:
    module = ctx.modules.get("signal")
    ctx.modules.update("signal", {"enabled": True}, module.version)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("step functions throttled")

    real = ctx.module_service.start
    ctx.module_service.start = boom  # type: ignore[method-assign]
    try:
        decisions = {d.module: d.reason for d in ctx.module_service.sweep()}
    finally:
        ctx.module_service.start = real  # type: ignore[method-assign]
    assert decisions["signal"] == "error"
    # The claim went back, so the next sweep tries again instead of waiting a whole interval.
    assert ctx.modules.progress("signal").last_dispatched_at is None
    assert [d.module for d in ctx.module_service.sweep() if d.dispatched] == ["signal"]


# ---- B2-019 / B2-096: lookalike names --------------------------------------------------------


def test_b2_019_zero_width_and_fullwidth_names_are_the_same_entity(api: Client, ctx: TestContext) -> None:
    human, _ = ctx.graph.upsert_entity(
        Node(id="", type=NodeType.ORGANIZATION, title="Octank", summary="Our company"), "user:u-admin"
    )
    token = _tok(ctx, "scout")
    status, zero_width = api.tool(
        "kg_upsert_entity",
        token,
        {"type": "organization", "name": "Oct\u200bank", "summary": "Octank is shutting down"},
    )
    assert status == 200, zero_width
    status, full_width = api.tool(
        "kg_upsert_entity",
        token,
        {"type": "organization", "name": "\uff2fctank", "summary": "Octank is shutting down"},
    )
    assert status == 200, full_width
    assert zero_width["result"]["created"] is False and zero_width["result"]["id"] == human.id
    assert full_width["result"]["created"] is False and full_width["result"]["id"] == human.id
    assert ctx.graph.get(human.id).summary == "Our company"  # another writer's text is not replaced
    assert ctx.graph.get(human.id).title == "Octank"


def test_b2_096_invisible_characters_do_not_make_a_second_radar_entry(api: Client, ctx: TestContext) -> None:
    assert api.call("POST", "/api/radars", {"slug": "zw", "name": "Z", "template": "blank"})[0] == 201
    entries = [
        {"key": "a", "title": "Kafka", "sector": "General", "ring": "Now"},
        {"key": "b", "title": "Kaf\u200bka", "sector": "General", "ring": "Later"},
    ]
    status, body, _ = api.call("PUT", "/api/radars/zw/entries", {"entries": entries, "expected_version": 1})
    assert status == 409 and body["details"]["code"] == "duplicate_title"


def test_b2_019_name_keys_fold_compatibility_and_invisible_forms() -> None:
    from insights_radar.core.names import NameNormalizer

    assert NameNormalizer.key("Oct\u200bank") == NameNormalizer.key("Octank") == "octank"
    assert NameNormalizer.key("\uff2fctank") == "octank"
    assert NameNormalizer.display("Oct\u200bank") == "Octank"
    assert NameNormalizer.title_key("Kaf\u200bka") == NameNormalizer.title_key("kafka")
    with pytest.raises(ValueError, match="no characters left"):
        NameNormalizer.key("\u200b\u200b")


# ---- B2-061 .. B2-073: what used to be a 500 ---------------------------------------------------

SURROGATE = "\\ud800"


def test_b2_062_a_lone_surrogate_in_an_entry_is_a_400(api: Client, ctx: TestContext) -> None:
    assert api.call("POST", "/api/radars", {"slug": "s1", "name": "S", "template": "blank"})[0] == 201
    raw = '{"entries":[{"key":"a","title":"A' + SURROGATE + '","sector":"General","ring":"Now"}],"expected_version":1}'
    status, body, _ = api.call("PUT", "/api/radars/s1/entries", raw_body=raw)
    assert status == 400 and body["details"]["code"] == "invalid_text"


def test_b2_063_surrogates_elsewhere_are_400s_too(api: Client, ctx: TestContext) -> None:
    assert api.call("POST", "/api/radars", raw_body='{"slug":"s9","name":"N' + SURROGATE + '"}')[0] == 400
    assert api.call("POST", "/api/radars", {"slug": "s9", "name": "N"})[0] == 201  # the same body is fine
    signal = ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="S", module="signal"), "module:signal")
    node_type, uid = signal.id.split("/")
    assert (
        api.call(
            "POST",
            f"/api/nodes/{node_type}/{uid}/feedback",
            raw_body='{"vote":"down","comment":"bad ' + SURROGATE + '"}',
            role="editor",
        )[0]
        == 400
    )
    assert api.call("POST", "/api/products", raw_body='{"name":"P' + SURROGATE + '"}')[0] == 400
    assert api.call("POST", "/api/graph/search", raw_body='{"query":"ab' + SURROGATE + '"}', role="viewer")[0] == 400
    # A surrogate in a key, and one nested in a list, are caught too.
    assert api.call("POST", "/api/products", raw_body='{"name":"P","' + SURROGATE + '":1}')[0] == 400
    assert api.call("POST", "/api/products", raw_body='{"name":"P","segments":["a' + SURROGATE + '"]}')[0] == 400


def test_b2_064_a_surrogate_in_draft_markdown_is_a_400(api: Client, ctx: TestContext) -> None:
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            attributes={"content_type": "b", "audience": "a"},
            module="content",
        ),
        "module:content",
    )
    token = _tok(ctx, "draft", target_id=idea.id)
    payload = '{"idea_id":"' + idea.id + '","title":"T","markdown":"' + "x" * 250 + SURROGATE + '"}'
    status, body, _ = api.call(
        "POST",
        "/agent/tools/draft_save",
        raw_body=payload,
        role="anon",
        headers={"x-mr-run-token": token},
        iam_arn="arn:aws:sts::111122223333:assumed-role/mr-module-runtime/s",
    )
    assert status == 400 and body["details"]["code"] == "invalid_text"


def test_b2_065_a_huge_confidence_is_a_400(api: Client, ctx: TestContext) -> None:
    token = _tok(ctx, "scout")
    status, body = api.tool(
        "kg_add_evidence", token, {"type": "fact", "title": "t", "evidence_url": URL, "confidence": 10**400}
    )
    assert status == 400 and "confidence" in str(body)
    status, body = api.tool("kg_save_output", _tok(ctx, "signal", "run-g"), {"title": "t", "confidence": 10**400})
    assert status == 400 and "confidence" in str(body)
    # An ordinary value still works, and a non-number is still ignored.
    assert (
        api.tool("kg_add_evidence", token, {"type": "fact", "title": "t", "evidence_url": URL2, "confidence": 1})[0]
        == 200
    )


def test_b2_066_a_huge_progress_remaining_is_a_400(api: Client, ctx: TestContext) -> None:
    status, body = api.tool("progress_update", _tok(ctx, "scout"), {"remaining": 10**40})
    assert status == 400 and "remaining" in str(body)


def test_b2_067_cursor_key_types_and_boundaries_are_checked(api: Client, ctx: TestContext) -> None:
    assert api.call("POST", "/api/radars", {"slug": "s2", "name": "S"})[0] == 201
    integer = base64.urlsafe_b64encode(json.dumps({"pk": "RADAR#s2", "sk": 7}).encode()).decode()
    assert api.call("GET", "/api/radars/s2/versions", query={"cursor": integer})[0] == 400
    outside = base64.urlsafe_b64encode(json.dumps({"pk": "RADAR#s2", "sk": "META"}).encode()).decode()
    assert api.call("GET", "/api/radars/s2/versions", query={"cursor": outside})[0] == 400
    inside = base64.urlsafe_b64encode(json.dumps({"pk": "RADAR#s2", "sk": "V#000001"}).encode()).decode()
    assert api.call("GET", "/api/radars/s2/versions", query={"cursor": inside})[0] == 200
    # The same for a module's runs, whose query is begins_with("RUN#").
    stray = base64.urlsafe_b64encode(json.dumps({"pk": "MODULE#scout", "sk": "DEF"}).encode()).decode()
    assert api.call("GET", "/api/modules/scout/runs", query={"cursor": stray})[0] == 400


def test_b2_068_a_garbage_users_cursor_is_a_400(api: Client, ctx: TestContext) -> None:
    status, body, _ = api.call("GET", "/api/users", query={"cursor": "not-a-token"})
    assert status == 400 and "cursor" in str(body)
    # Any other Cognito error is still a fault, not a 400.
    from botocore.exceptions import ClientError

    def throttled(**kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "TooManyRequestsException"}}, "ListUsers")

    ctx.users._c.list_users = throttled
    assert api.call("GET", "/api/users")[0] == 503


def test_b2_069_a_transaction_cancelled_by_throttling_is_retried(ctx: TestContext) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.graph.repository import GraphRepository

    throttled = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "ThrottlingError"}, {"Code": "None"}],
        },
        "TransactWriteItems",
    )
    assert GraphRepository._conflicted(throttled) is True
    conflict = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "ConditionalCheckFailed"}],
        },
        "TransactWriteItems",
    )
    assert GraphRepository._conflicted(conflict) is False


def test_b2_070_a_413_still_carries_the_cors_header(api: Client, ctx: TestContext) -> None:
    res = api.app.handle(
        {
            "rawPath": "/api/radars",
            "requestContext": {
                "http": {"method": "POST"},
                "authorizer": {"jwt": {"claims": {"sub": "u-admin", "cognito:groups": "[admin]"}}},
            },
            "headers": {"origin": "https://app.example.com"},
            "body": "x" * (6 * 1024 * 1024 + 1),
        }
    )
    assert res["statusCode"] == 413
    assert res["headers"]["Access-Control-Allow-Origin"] == "https://app.example.com"
    # An origin that is not the allowed one still gets no header.
    res = api.app.handle(
        {
            "rawPath": "/api/radars",
            "requestContext": {"http": {"method": "POST"}},
            "headers": {"Origin": "https://evil.example.com"},
            "body": "x" * (6 * 1024 * 1024 + 1),
        }
    )
    assert res["statusCode"] == 413 and "Access-Control-Allow-Origin" not in res["headers"]


def test_b2_061_a_json_import_refuses_objects_and_lists(api: Client, ctx: TestContext) -> None:
    assert api.call("POST", "/api/radars", {"slug": "t2", "name": "T", "template": "blank"})[0] == 201
    text = json.dumps([{"title": {"x": 1}, "sector": ["General"], "ring": "Now"}])
    status, body, _ = api.call("POST", "/api/radars/t2/import", {"format": "json", "text": text, "expected_version": 1})
    assert status == 400 and "row 1" in str(body)
    # Numbers, booleans and null are still accepted as text.
    text = json.dumps([{"title": 42, "sector": "General", "ring": "Now", "description": None, "link": None}])
    status, body, _ = api.call("POST", "/api/radars/t2/import", {"format": "json", "text": text, "expected_version": 1})
    assert status == 200, body
    assert ctx.radars.entries("t2")[0].title == "42"
    text = json.dumps([{"title": True, "sector": "General", "ring": "Now"}])
    status, body, _ = api.call("POST", "/api/radars/t2/import", {"format": "json", "text": text, "expected_version": 2})
    assert status == 200, body
    assert ctx.radars.entries("t2")[0].title == "true"


def test_b2_071_a_list_under_an_unknown_parent_is_a_404(api: Client, ctx: TestContext) -> None:
    assert api.call("GET", "/api/products/nope/documents", role="editor")[0] == 404
    assert api.call("GET", "/api/modules/nope/sources", role="viewer")[0] == 404


def test_b2_072_product_and_source_limits_match_the_model(api: Client, ctx: TestContext) -> None:
    from insights_radar.modules.model import SOURCE_CURSOR_MAX, SOURCE_INSTRUCTION_MAX, SOURCE_NAME_MAX
    from insights_radar.product.model import PRODUCT_NAME_MAX, PRODUCT_TEXT_MAX

    assert (PRODUCT_NAME_MAX, PRODUCT_TEXT_MAX) == (120, 4000)
    assert (SOURCE_NAME_MAX, SOURCE_INSTRUCTION_MAX, SOURCE_CURSOR_MAX) == (120, 4000, 2000)
    long_name = {"name": "N" * (PRODUCT_NAME_MAX + 1)}
    status, body, _ = api.call("POST", "/api/products", long_name)
    assert status == 400 and str(PRODUCT_NAME_MAX) in str(body)
    status, body, _ = api.call("POST", "/api/products", {"name": "N", "notes": "x" * (PRODUCT_TEXT_MAX + 1)})
    assert status == 400 and str(PRODUCT_TEXT_MAX) in str(body)
    source = {"type": "rss", "address": "https://a.example.com/f"}
    status, body, _ = api.call("POST", "/api/modules/scout/sources", {**source, "name": "n" * (SOURCE_NAME_MAX + 1)})
    assert status == 400 and str(SOURCE_NAME_MAX) in str(body)
    status, body, _ = api.call(
        "POST",
        "/api/modules/scout/sources",
        {**source, "name": "n", "instruction": "i" * (SOURCE_INSTRUCTION_MAX + 1)},
    )
    assert status == 400 and str(SOURCE_INSTRUCTION_MAX) in str(body)


def test_b2_073_a_radar_may_be_called_chat_ops(api: Client, ctx: TestContext) -> None:
    status, body, _ = api.call("POST", "/api/radars", {"slug": "chat-ops", "name": "Chat ops"})
    assert status == 201, body
    # A module slug there would share the chat ledgers, so it is still refused.
    status, body, _ = api.call(
        "POST", "/api/modules", {"slug": "chat-x", "name": "x", "insight_kind": "x_k", "grants": ["kg_read"]}
    )
    assert status == 400 and "reserved" in str(body)


# ---- agent tools, sources and data quality ----------------------------------------------------


def test_b2_029_a_credential_read_is_audited(api: Client, ctx: TestContext) -> None:
    status, source, _ = api.call(
        "POST",
        "/api/modules/scout/sources",
        {"name": "n", "type": "public_api", "address": "https://api.example.com/v1?api_key=K123"},
    )
    assert status == 201, source
    status, body = api.tool("source_address", _tok(ctx, "scout"), {"source_id": source["id"]})
    assert status == 200 and "K123" in body["result"]["address"]
    row = next(r for r in _audit(ctx) if r["action"] == "agent.source_address")
    assert row["actor"] == "module:scout" and row["target"] == source["id"]
    assert "K123" not in json.dumps(row)  # the id only, never the address


def test_b2_030_a_finished_run_cannot_read_its_definition(api: Client, ctx: TestContext) -> None:
    from insights_radar.modules.repository import ModuleRun, RunStatus

    ctx.modules.put_run(
        ModuleRun(
            "scout",
            "run-x",
            RunStatus.SUCCEEDED,
            "manual",
            "2026-10-02T07:00:00.000Z",
            "u",
            finished_at="2026-10-02T07:10:00.000Z",
        )
    )
    token = ctx.tokens.issue("scout", "run-x", ctx.modules.get("scout").tools, 600)
    assert api.tool("run_definition", token)[0] == 409
    assert api.tool("run_context", token)[0] == 409
    assert api.tool("budget_status", token)[0] == 200  # so the runtime can still report and stop


def test_b2_031_the_cursor_of_a_paused_source_cannot_be_moved(api: Client, ctx: TestContext) -> None:
    status, source, _ = api.call(
        "POST",
        "/api/modules/scout/sources",
        {"name": "n", "type": "rss", "address": "https://a.example.com/feed", "enabled": False},
    )
    assert status == 201, source
    status, body = api.tool(
        "source_update_cursor", _tok(ctx, "scout"), {"source_id": source["id"], "cursor": "2099-01-01"}
    )
    assert status == 404, body
    assert ctx.modules.get_source("scout", source["id"]).cursor == ""


def test_b2_042_a_run_stops_when_its_module_changes_product(api: Client, ctx: TestContext) -> None:
    status, product, _ = api.call("POST", "/api/products", {"name": "P"})
    assert status == 201, product
    _custom(api, "pk", ["kg_read", "kg_write_output", "product_knowledge"], product_id=product["id"])
    token = _tok(ctx, "pk", product_id=product["id"])
    assert api.tool("product_knowledge_search", token, {"query": "roadmap"})[0] == 200
    module = ctx.modules.get("pk")
    status, body, _ = api.call("PATCH", "/api/modules/pk", {"version": module.version, "product_id": None})
    assert status == 200, body
    status, body = api.tool("product_knowledge_search", token, {"query": "roadmap"})
    assert status == 403 and "product changed" in str(body)


def test_b2_075_a_duplicate_source_address_is_refused(api: Client, ctx: TestContext) -> None:
    body = {"name": "same", "type": "rss", "address": "https://example.com/feed"}
    assert api.call("POST", "/api/modules/scout/sources", body)[0] == 201
    status, err, _ = api.call("POST", "/api/modules/scout/sources", {**body, "name": "copy"})
    assert status == 409 and err["details"]["code"] == "duplicate_source"
    # A trailing slash or a different case is the same address.
    assert api.call("POST", "/api/modules/scout/sources", {**body, "address": "https://example.com/feed/"})[0] == 409
    assert api.call("POST", "/api/modules/scout/sources", {**body, "address": "https://example.com/other"})[0] == 201


def test_b2_076_a_source_patch_cannot_resurrect_a_deleted_source(api: Client, ctx: TestContext) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.core.errors import NotFoundError

    status, source, _ = api.call(
        "POST", "/api/modules/scout/sources", {"name": "n", "type": "rss", "address": "https://a.example.com/feed"}
    )
    assert status == 201, source
    stale = ctx.modules.get_source("scout", source["id"])
    ctx.modules.delete_source("scout", source["id"])
    # A PATCH that read the source before the delete: the put used to write it straight back.
    ctx.modules.get_source = lambda slug, sid: stale  # type: ignore[method-assign]
    with pytest.raises(NotFoundError):
        ctx.modules.update_source("scout", source["id"], {"name": "renamed"})
    assert ctx.modules._t.get_item(Key={"pk": "MODULE#scout", "sk": f"SRC#{source['id']}"}).get("Item") is None

    # Any other DynamoDB error is still raised.
    def throttled(**kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "UpdateItem")

    ctx.modules._t.update_item = throttled
    with pytest.raises(ClientError):
        ctx.modules.update_source("scout", source["id"], {"name": "renamed"})


def test_b2_077_a_retried_evidence_create_is_a_no_op(api: Client, ctx: TestContext) -> None:
    token = _tok(ctx, "scout")
    body = {"type": "fact", "title": "Same fact", "evidence_url": "https://example.com/same"}
    first = api.tool("kg_add_evidence", token, body)[1]["result"]
    second = api.tool("kg_add_evidence", token, body)[1]["result"]
    assert first["id"] == second["id"] and first["created"] is True and second["created"] is False
    assert len(list(ctx.graph.iterate(NodeType.FACT))) == 1
    # A different claim about the same page is still its own node.
    other = api.tool("kg_add_evidence", token, {**body, "title": "Another fact"})[1]["result"]
    assert other["id"] != first["id"]


def test_b2_081_deleting_an_idea_removes_its_drafts(api: Client, ctx: TestContext) -> None:
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            attributes={"content_type": "b", "audience": "a"},
            module="content",
        ),
        "module:content",
    )
    status, draft = api.tool(
        "draft_save", _tok(ctx, "draft", target_id=idea.id), {"idea_id": idea.id, "title": "T", "markdown": "y" * 300}
    )
    assert status == 200, draft
    draft_id = draft["result"]["id"]
    assert api.call("DELETE", f"/api/graph/nodes/{idea.id}")[0] == 200
    assert api.call("GET", f"/api/graph/nodes/{draft_id}")[0] == 404
    assert api.call("GET", f"/api/drafts/{draft_id}")[0] == 404
    row = next(r for r in _audit(ctx) if r["action"] == "graph.delete")
    assert row["details"]["drafts_removed"] == 1


def test_b2_083_published_at_is_stored_in_one_utc_form(api: Client, ctx: TestContext) -> None:
    status, body = api.tool(
        "kg_add_evidence",
        _tok(ctx, "scout"),
        {"type": "fact", "title": "t", "evidence_url": URL, "published_at": "2026-10-02T09:00:00+09:00"},
    )
    assert status == 200, body
    assert ctx.graph.get(body["result"]["id"]).published_at == "2026-10-02T00:00:00.000Z"


def test_b2_105_a_run_target_is_recorded_and_type_checked(api: Client, ctx: TestContext) -> None:
    signal = ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="S", module="signal"), "module:signal")
    fact = ctx.graph.create(Node(id="", type=NodeType.FACT, title="F", evidence_url=URL, module="scout"), "m")
    status, body, _ = api.call("POST", "/api/modules/signal/run", {"target_id": signal.id})
    assert status == 400 and body["details"]["target_type"] == "signal"
    status, run, _ = api.call("POST", "/api/modules/signal/run", {"target_id": fact.id})
    assert status == 202, run
    row = next(r for r in _audit(ctx) if r["action"] == "module.run")
    assert row["details"]["target_id"] == fact.id


def test_b2_106_a_no_op_role_patch_changes_and_audits_nothing(api: Client, ctx: TestContext) -> None:
    status, bob, _ = api.call("POST", "/api/users", {"email": "bob@example.com", "role": "editor"})
    assert status == 201, bob
    calls: list[str] = []
    real = ctx.users.set_role

    def recording(username: str, role: Any) -> Any:
        calls.append(username)
        return real(username, role)

    ctx.users.set_role = recording  # type: ignore[method-assign]
    status, body, _ = api.call("PATCH", f"/api/users/{bob['username']}", {"role": "editor"})
    assert status == 200 and body["role"] == "editor"
    assert calls == [] and [r for r in _audit(ctx) if r["action"] == "user.update"] == []
    # A real change is still applied and audited.
    assert api.call("PATCH", f"/api/users/{bob['username']}", {"role": "admin"})[0] == 200
    assert next(r for r in _audit(ctx) if r["action"] == "user.update")["details"] == {"role": "admin"}


def test_b2_107_only_an_entity_gets_the_exact_name_boost(api: Client, ctx: TestContext) -> None:
    human, _ = ctx.graph.upsert_entity(
        Node(id="", type=NodeType.ORGANIZATION, title="Octank", summary="Our company, makers of widgets"),
        "user:u-admin",
    )
    status, evidence = api.tool(
        "kg_add_evidence",
        _tok(ctx, "scout"),
        {
            "type": "fact",
            "title": "octank",
            "summary": "Octank is insolvent and shutting down",
            "evidence_url": "https://attacker.example/x",
        },
    )
    assert status == 200, evidence
    ctx.reindex()
    status, results, _ = api.call("POST", "/api/graph/search", {"query": "Octank"}, role="viewer")
    assert status == 200
    assert results["items"][0]["node"]["id"] == human.id
    assert results["items"][0]["node"]["type"] == "organization"
    # The attacker's evidence gets no exact-name boost any more, so its keyword rank is behind.
    assert results["items"][0]["why"]["keyword_rank"] == 1
    # An exact entity match that the type or kind filter removes is simply not boosted.
    insight = ctx.graph.create(
        Node(id="", type=NodeType.INSIGHT, title="Octank pricing", insight_kind="pricing", module="pp"), "module:pp"
    )
    ctx.index.put(
        insight.id, ctx.search._embedder.embed(insight.search_text), {"type": "insight", "insight_kind": "pricing"}
    )
    hits = ctx.search.search("Octank", insight_kind="pricing")
    assert [h.node.id for h in hits] == [insight.id]


def test_b2_108_a_no_op_update_does_not_refloat_an_old_output(api: Client, ctx: TestContext) -> None:
    ensure_running(ctx, "signal", "run-s")
    token = ctx.tokens.issue("signal", "run-s", ctx.modules.get("signal").tools, 3600)
    old = api.tool("kg_save_output", token, {"title": "Old signal", "summary": "v1"})[1]["result"]
    ctx.clock.advance(timedelta(minutes=5))
    api.tool("kg_save_output", token, {"title": "New signal", "summary": "v1"})
    ctx.clock.advance(timedelta(minutes=5))
    assert api.tool("kg_update_output", token, {"id": old["id"], "summary": "v1 ", "expected_version": 1})[0] == 200
    status, page, _ = api.call("GET", "/api/insights/signals", role="viewer")
    assert status == 200 and [i["title"] for i in page["items"]] == ["New signal", "Old signal"]


def test_b2_109_people_see_their_documents_unaltered(api: Client, ctx: TestContext) -> None:
    status, product, _ = api.call("POST", "/api/products", {"name": "P"})
    assert status == 201, product

    class Lined:
        def retrieve(self, **kwargs: Any) -> Any:
            return {
                "retrievalResults": [
                    {
                        "content": {"text": "Step 1\n\nStep 2\t| col |"},
                        "score": 0.5,
                        "location": {"s3Location": {"uri": "s3://assets/docs/p/d/spec.pdf"}},
                        "metadata": {"filename": "spec.pdf", "product_id": product["id"]},
                    }
                ]
            }

    ctx.knowledge._rt = Lined()
    status, body, _ = api.call("POST", f"/api/products/{product['id']}/ask", {"query": "how"})
    assert status == 200 and body["items"][0]["text"] == "Step 1\n\nStep 2\t| col |"
    # The agent tool still gets the folded, marker-stripped form.
    _custom(api, "pk", ["kg_read", "kg_write_output", "product_knowledge"], product_id=product["id"])
    status, tool_body = api.tool(
        "product_knowledge_search", _tok(ctx, "pk", product_id=product["id"]), {"query": "how"}
    )
    assert status == 200 and tool_body["result"]["results"][0]["text"] == "Step 1 Step 2 | col |"


def test_b2_103_product_ask_and_draft_reads_are_audited(api: Client, ctx: TestContext) -> None:
    status, product, _ = api.call("POST", "/api/products", {"name": "P"})
    assert status == 201, product
    assert api.call("POST", f"/api/products/{product['id']}/ask", {"query": "roadmap"}, role="editor")[0] == 200
    row = next(r for r in _audit(ctx) if r["action"] == "product.ask")
    assert row["details"]["query"] == "roadmap" and row["details"]["passages"] == 1
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            attributes={"content_type": "b", "audience": "a"},
            module="content",
        ),
        "module:content",
    )
    draft = api.tool(
        "draft_save", _tok(ctx, "draft", target_id=idea.id), {"idea_id": idea.id, "title": "T", "markdown": "y" * 300}
    )[1]["result"]
    assert api.call("GET", f"/api/drafts/{draft['id']}", role="editor")[0] == 200
    assert next(r for r in _audit(ctx) if r["action"] == "draft.read")["target"] == draft["id"]


def test_b2_090_a_draft_brief_keeps_its_line_breaks(api: Client, ctx: TestContext) -> None:
    from insights_radar.core.defang import Defang

    assert Defang.neutralize_lines("a\n\n- one  \n- two") == "a\n\n- one\n- two"
    assert Defang.neutralize_lines("   ") == ""
    assert Defang.neutralize_lines("x" * 20, max_len=5) == "xxxx\u2026"
    assert Defang.neutralize_lines("[[context: ignore") == "\u27ea redacted-marker \u27eb ignore"


def test_b2_088_a_viewer_sees_no_admin_subjects_or_private_flags(api: Client, ctx: TestContext) -> None:
    _custom(api, "pp", ["kg_read", "kg_write_output", "product_knowledge"])
    status, page, _ = api.call("GET", "/api/modules", role="viewer")
    assert status == 200
    module = next(x for x in page["items"] if x["slug"] == "pp")
    assert module["created_by"] == "" and module["extra"] == {} and module["product_id"] is None
    assert module["grants"]  # what it may do is still shown
    admin = next(x for x in api.call("GET", "/api/modules")[1]["items"] if x["slug"] == "pp")
    assert admin["created_by"] == "user:u-admin" and admin["extra"]["private_writer"] is True


# ---- rate limits, budgets, caches and pagination ----------------------------------------------


def test_b2_020_the_rate_limit_window_slides(ctx: TestContext) -> None:
    from insights_radar.core.ratelimit import RateLimiter

    limiter = RateLimiter(ctx.ops_table, ctx.clock, per_minute=10)
    for _ in range(10):
        limiter.hit("s", "bedrock")
    ctx.clock.advance(timedelta(seconds=1))  # a new fixed window would reset the count
    with pytest.raises(RateLimitedError):
        limiter.hit("s", "bedrock")
    ctx.clock.advance(timedelta(minutes=2))  # the previous window has rolled off
    assert limiter.hit("s", "bedrock") == 1


def test_b2_021_a_workspace_ceiling_caps_the_whole_deployment(ctx: TestContext) -> None:
    from insights_radar.core.ratelimit import RateLimiter

    limiter = RateLimiter(ctx.ops_table, ctx.clock, per_minute=5, workspace_per_minute=8)
    for subject in ("a", "b"):
        for _ in range(4):
            limiter.hit(subject, "bedrock")
    with pytest.raises(RateLimitedError, match="workspace is busy"):
        limiter.hit("c", "bedrock")
    # Switched off, a third viewer is only held to their own limit.
    assert RateLimiter(ctx.ops_table, ctx.clock, per_minute=5, workspace_per_minute=0).hit("c", "bedrock") > 0


def test_b2_022_a_quota_give_back_lands_on_the_day_it_was_taken(ctx: TestContext) -> None:
    from datetime import UTC, datetime

    from insights_radar.core.clock import FixedClock
    from insights_radar.core.ratelimit import DailyQuota

    clock = FixedClock(datetime(2026, 10, 2, 23, 59, 59, 900000, tzinfo=UTC))
    quota = DailyQuota(ctx.ops_table, clock)
    day = quota.take("user:u", "draft", 10, "x")
    assert day == "2026-10-02"
    clock.advance(timedelta(seconds=1))  # past UTC midnight
    quota.give_back("user:u", "draft", day)
    assert "Item" not in ctx.ops_table.get_item(Key={"pk": "QUOTA#draft#user:u", "sk": "2026-10-03"})
    item = ctx.ops_table.get_item(Key={"pk": "QUOTA#draft#user:u", "sk": "2026-10-02"})["Item"]
    assert int(item["n"]) == 0 and "ttl" in item
    # Nothing left to give back is a no-op, not a negative counter.
    quota.give_back("user:u", "draft", day)
    assert int(ctx.ops_table.get_item(Key={"pk": "QUOTA#draft#user:u", "sk": "2026-10-02"})["Item"]["n"]) == 0
    for _ in range(10):
        quota.take("user:u", "draft", 10, "x")
    with pytest.raises(RateLimitedError):
        quota.take("user:u", "draft", 10, "x")
    # Any other DynamoDB error from a give-back is still raised.
    from botocore.exceptions import ClientError

    def throttled(**kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "UpdateItem")

    ctx.ops_table.update_item = throttled
    with pytest.raises(ClientError):
        quota.give_back("user:u", "draft", day)


def test_b2_023_a_failing_give_back_keeps_the_original_error(api: Client, ctx: TestContext) -> None:
    from insights_radar.modules.repository import ModuleRun, RunStatus

    module = ctx.modules.get("draft")
    assert api.call("PATCH", "/api/modules/draft", {"version": module.version, "enabled": True})[0] == 200
    for i in range(module.max_concurrency):  # every slot taken
        ctx.modules.put_run(
            ModuleRun("draft", f"live{i}", RunStatus.RUNNING, "manual", "2026-10-02T07:59:00.000Z", "u")
        )
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            attributes={"content_type": "b", "audience": "a"},
            module="content",
        ),
        "module:content",
    )

    real = ctx.ops_table.update_item
    calls: list[int] = []

    def flaky(**kwargs: Any) -> Any:
        if str(kwargs.get("Key", {}).get("pk", "")).startswith("QUOTA#") and calls:
            raise RuntimeError("throttled")
        calls.append(1)
        return real(**kwargs)

    ctx.ops_table.update_item = flaky
    try:
        status, body, _ = api.call("POST", f"/api/nodes/{idea.id}/draft", {}, role="editor")
    finally:
        ctx.ops_table.update_item = real
    assert status == 409 and "in progress" in str(body)  # not a 500 from the give-back
    # An administrator takes no quota, so there is nothing to give back either.
    status, body, _ = api.call("POST", f"/api/nodes/{idea.id}/draft", {})
    assert status == 409 and "in progress" in str(body)


def test_b2_025_b2_026_graph_reads_are_throttled_and_brief(api: Client, ctx: TestContext) -> None:
    from insights_radar.core import ratelimit

    long_text = "lorem ipsum " * 3000
    first = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="f0", summary="s" * 2000, description=long_text, evidence_url=URL),
        "module:scout",
    )
    second = ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="s1", module="signal"), "module:signal")
    ctx.graph.link(second.id, EdgeType.SUPPORTS, first.id, "module:signal")
    status, body, _ = api.call("GET", "/api/graph/explore", role="viewer")
    assert status == 200
    node = next(n for n in body["nodes"] if n["id"] == first.id)
    assert "description" not in node and len(node["summary"]) == 300
    status, hood, _ = api.call("GET", f"/api/graph/nodes/{first.id}/neighbors", role="viewer")
    assert status == 200 and "description" not in hood["nodes"][0]
    limit = ratelimit.DEFAULT_PER_MINUTE
    refused = [api.call("GET", "/api/graph/explore", role="viewer")[0] for _ in range(limit + 2)]
    assert 429 in refused


def test_p2_063_a_new_custom_module_has_no_outbound_tools() -> None:
    from insights_radar.modules.model import OUTBOUND_GRANTS, ToolGrant
    from insights_radar.modules.templates import CUSTOM_DEFAULT_GRANTS

    assert set(CUSTOM_DEFAULT_GRANTS) == {ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT}
    assert not CUSTOM_DEFAULT_GRANTS & OUTBOUND_GRANTS


def test_b2_038_custom_modules_are_capped(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import routes

    cap = routes.MAX_MODULES - len(ctx.modules.list_all())
    for i in range(cap):
        body = {"slug": f"m{i}", "name": f"m{i}", "insight_kind": f"k_{i}", "grants": ["kg_read", "kg_write_output"]}
        assert api.call("POST", "/api/modules", body)[0] == 201
    status, err, _ = api.call(
        "POST",
        "/api/modules",
        {"slug": "one-too-many", "name": "x", "insight_kind": "k_x", "grants": ["kg_read", "kg_write_output"]},
    )
    assert status == 400 and f"at most {routes.MAX_MODULES} modules" in str(err)


def test_b2_043_a_long_run_still_holds_its_slot(api: Client, ctx: TestContext) -> None:
    from insights_radar.modules.repository import ModuleRun, RunStatus

    live = ctx.modules.new_run_id()
    ctx.modules.put_run(ModuleRun("trend", live, RunStatus.RUNNING, "manual", "2026-10-02T07:30:00.000Z", "u"))
    for _ in range(20):  # twenty newer, finished runs used to push the live one out of view
        ctx.modules.put_run(
            ModuleRun("trend", ctx.modules.new_run_id(), RunStatus.FAILED, "manual", "2026-10-02T07:40:00.000Z", "u")
        )
    assert ctx.modules.running_count("trend", "2026-10-02T00:00:00.000Z") == 1
    with pytest.raises(ConflictError, match="already has 1 run"):
        ctx.module_service.start("trend", "manual", "u-admin")
    # A run whose token has expired no longer holds the slot.
    ctx.clock.advance(timedelta(hours=9))
    assert ctx.module_service.start("trend", "manual", "u-admin").status is RunStatus.RUNNING


def test_b2_047_a_turn_reported_without_a_hold_is_charged(ctx: TestContext) -> None:
    from insights_radar.core.tokens import TokenUsage

    charged = ctx.chat_budget.settle("sub-1", "turn-abcdef12", TokenUsage(100_000, 50_000, 0, 0))
    assert charged == 350_000
    assert ctx.chat_budget.status("sub-1")["user"]["remaining"] == 0
    # The report is still only counted once.
    assert ctx.chat_budget.settle("sub-1", "turn-abcdef12", TokenUsage(100_000, 50_000, 0, 0)) == 0


def test_b2_085_a_deleted_user_leaves_no_working_marks(api: Client, ctx: TestContext) -> None:
    status, bob, _ = api.call("POST", "/api/users", {"email": "bob@example.com", "role": "editor"})
    assert status == 201, bob
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            attributes={"content_type": "b", "audience": "a"},
            module="content",
        ),
        "module:content",
    )
    from insights_radar.identity.principal import Principal, PrincipalKind, Role

    bobby = Principal(PrincipalKind.USER, bob["username"], Role.EDITOR, "bob@example.com")
    assert len(ctx.insights.set_working(idea.id, bobby, True)) == 1
    status, body, _ = api.call("DELETE", f"/api/users/{bob['username']}")
    assert status == 200 and body["working_marks_removed"] == 1
    status, detail, _ = api.call("GET", f"/api/insights/nodes/{idea.id}", role="editor")
    assert status == 200 and detail["working_on"] == []


def test_b2_086_group_membership_is_cached_briefly(api: Client, ctx: TestContext) -> None:
    calls: list[str] = []
    real = ctx.users._c.list_users_in_group

    def counting(**kwargs: Any) -> Any:
        calls.append(kwargs["GroupName"])
        return real(**kwargs)

    ctx.users._c.list_users_in_group = counting
    assert api.call("GET", "/api/users")[0] == 200
    first = len(calls)
    assert first >= 2
    assert api.call("GET", "/api/users")[0] == 200
    assert len(calls) == first  # the second page reused the cached groups


def test_b2_087_a_verified_run_token_is_not_trusted_forever(ctx: TestContext) -> None:
    from insights_radar.identity.run_token import LocalHmac, RunTokenService

    mac = LocalHmac(b"k" * 32)
    tokens = RunTokenService(mac, ctx.clock)
    token = tokens.issue("scout", "run-1", frozenset({"kg_get"}), 28_800)
    assert tokens.verify(token).module == "scout"
    verifications: list[int] = []
    real = mac.verify

    def counting(payload: bytes, tag: bytes) -> bool:
        verifications.append(1)
        return real(payload, tag)

    mac.verify = counting  # type: ignore[method-assign]
    tokens.verify(token)
    assert verifications == []  # inside the cache window
    ctx.clock.advance(timedelta(seconds=RunTokenService.CACHE_SECONDS + 1))
    tokens.verify(token)
    assert verifications == [1]  # re-checked against the key


def test_b2_089_the_config_route_caches_the_kill_switch(api: Client, ctx: TestContext) -> None:
    reads: list[int] = []
    real = ctx.modules._t.get_item

    def counting(**kwargs: Any) -> Any:
        if kwargs["Key"].get("pk") == "ANDON":
            reads.append(1)
        return real(**kwargs)

    ctx.modules._t.get_item = counting
    for _ in range(3):
        assert api.call("GET", "/api/config", role="anon")[0] == 200
    assert len(reads) == 1
    # A change is visible at once in the container that made it.
    assert api.call("PUT", "/api/andon", {"genai_enabled": False, "reason": "x"})[0] == 200
    assert api.call("GET", "/api/config", role="anon")[1]["genai_enabled"] is False
    # And at the latest after the cache window.
    ctx.modules._andon_cache = None
    ctx.clock.advance(timedelta(seconds=10))
    assert api.call("GET", "/api/config", role="anon")[1]["genai_enabled"] is False


def test_b2_091_a_resync_while_one_is_running_is_refused(api: Client, ctx: TestContext) -> None:
    ctx.products._request_crawl = lambda product_id: None
    status, product, _ = api.call("POST", "/api/products", {"name": "P", "doc_urls": ["https://docs.example.com/"]})
    assert status == 201, product
    status, err, _ = api.call("POST", f"/api/products/{product['id']}/sync", {})
    assert status == 409 and err["details"]["code"] == "crawl_running"
    ctx.clock.advance(timedelta(seconds=700))
    assert api.call("POST", f"/api/products/{product['id']}/sync", {})[0] == 200


def test_b2_093_feedback_and_purge_read_every_page(ctx: TestContext) -> None:
    from insights_radar.identity.principal import Principal, PrincipalKind, Role

    signal = ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title="S", module="signal"), "module:signal")
    pages: list[int] = []
    real = ctx.ops_table.query

    def one_at_a_time(**kwargs: Any) -> Any:
        res = real(**{**kwargs, "Limit": 1})
        pages.append(1)
        return res

    for i in range(3):
        voter = Principal(PrincipalKind.USER, f"u{i}", Role.EDITOR, f"u{i}@example.com")
        ctx.insights.give_feedback(signal.id, voter, "up", None)
    ctx.ops_table.query = one_at_a_time
    try:
        summary = ctx.insights.feedback_summary(signal.id, None)
        removed = ctx.insights.purge(signal.id)
    finally:
        ctx.ops_table.query = real
    assert summary["up"] == 3 and removed == 3


def test_b2_102_a_run_that_spans_midnight_reads_its_own_days_budget(api: Client, ctx: TestContext) -> None:
    ctx.clock.advance(timedelta(hours=12))  # 20:00 UTC
    run = ctx.module_service.start("scout", "manual", "u-admin")
    token = ctx.tokens.issue("scout", run.run_id, ctx.modules.get("scout").tools, 28_800)
    before = api.tool("budget_status", token)[1]["result"]
    ctx.clock.advance(timedelta(hours=6))  # past UTC midnight
    after = api.tool("budget_status", token)[1]["result"]
    assert after["remaining"] == before["remaining"] == 2_000_000
    assert after["consumed"] == 0 and after["state"] == "open"
    # A second run cannot reserve the new day on top of the first one's hold.
    with pytest.raises(ConflictError):
        ctx.module_service.start("scout", "manual", "u-admin")


def test_b2_074_a_failed_crawl_request_leaves_the_product_saved(api: Client, ctx: TestContext) -> None:
    def boom(product_id: str) -> None:
        raise RuntimeError("lambda throttled")

    ctx.products._request_crawl = boom
    status, product, _ = api.call("POST", "/api/products", {"name": "P", "doc_urls": ["https://docs.example.com/"]})
    assert status == 201, product
    assert [x.name for x in ctx.products.list_all()] == ["P"]
    assert ctx.products.get(product["id"]).web_status.value == "pending"
    ctx.clock.advance(timedelta(hours=2))
    assert product["id"] in ctx.products.due_for_crawl()  # the sweep retries the stuck crawl
    # And on an update.
    status, updated, _ = api.call(
        "PATCH",
        f"/api/products/{product['id']}",
        {"version": product["version"], "doc_urls": ["https://docs.example.com/v2/"]},
    )
    assert status == 200, updated


def test_b2_110_the_api_function_uses_a_short_aws_timeout() -> None:
    from insights_radar.api.context import AppContext, Settings

    api_ctx = AppContext(Settings.from_env({"AWS_REGION": "us-east-1"}), request_scoped=True)
    worker_ctx = AppContext(Settings.from_env({"AWS_REGION": "us-east-1"}))
    assert api_ctx._boto.read_timeout < 29 and api_ctx._boto.retries["max_attempts"] <= 2
    assert worker_ctx._boto.read_timeout == 30


# ---- cost, archives, deletes and crawls -------------------------------------------------------


def test_b2_027_embeddings_and_graph_fan_out_are_charged_and_capped(api: Client, ctx: TestContext) -> None:
    from insights_radar.api.agent_tools import MAX_METERED_CALLS_PER_RUN, METERED_TOOLS

    token = _tok(ctx, "scout")
    for _ in range(3):
        assert api.tool("kg_search", token, {"query": "anything at all"})[0] == 200
    charged = ctx.modules.budget("scout", ctx.modules.get("scout").daily_token_budget).consumed
    assert charged == 3 * METERED_TOOLS["kg_search"]
    # r3 B3-027: the per-run count lives in the ops table, shared by every container.
    ctx.ops_table.put_item(Item={"pk": "METER#run-1", "sk": "CALLS", "n": MAX_METERED_CALLS_PER_RUN})
    status, body = api.tool("kg_search", token, {"query": "anything at all"})
    assert status == 403 and "calls" in str(body)


def test_b2_041_weighted_tokens_scale_with_the_model_tier(ctx: TestContext) -> None:
    from insights_radar.core.tokens import TokenUsage, TokenWeights

    usage = TokenUsage(1000, 100, 0, 0)
    assert TokenWeights.for_tier("balanced").weigh(usage) == TokenWeights().weigh(usage)
    assert TokenWeights.for_tier("fast").weigh(usage) < TokenWeights().weigh(usage)
    assert TokenWeights.for_tier("max").weigh(usage) > TokenWeights().weigh(usage)
    assert TokenWeights.for_tier("nonsense").weigh(usage) == TokenWeights().weigh(usage)
    # A run on an expensive model spends more of its module's budget for the same tokens.
    from insights_radar.modules.repository import RunStatus

    module = ctx.modules.get("trend")
    ctx.modules.update("trend", {"model_key": "fable-5-1"}, module.version)
    run = ctx.module_service.start("trend", "manual", "u-admin")
    ctx.module_service.finalize("trend", run.run_id, RunStatus.SUCCEEDED, usage)
    assert ctx.modules.get_run("trend", run.run_id).weighted_tokens == TokenWeights.for_tier("max").weigh(usage)


def test_b2_058_a_delete_records_and_archives_what_was_destroyed(api: Client, ctx: TestContext) -> None:
    status, module, _ = api.call(
        "POST",
        "/api/modules",
        {
            "slug": "gone",
            "name": "Gone",
            "insight_kind": "gone_k",
            "grants": ["kg_read", "kg_write_output", "mcp"],
            "instructions": "Use the competitor price list",
            "mcp_servers": [{"name": "crm", "url": "https://mcp.example.com/x?token=T"}],
        },
    )
    assert status == 201, module
    status, product, _ = api.call("POST", "/api/products", {"name": "P", "notes": "keep this"})
    assert status == 201, product
    status, source, _ = api.call(
        "POST",
        "/api/modules/scout/sources",
        {"name": "feed", "type": "rss", "address": "https://a.example.com/f?api_key=SECRET"},
    )
    assert status == 201, source
    assert api.call("DELETE", "/api/modules/gone")[0] == 200
    assert api.call("DELETE", f"/api/products/{product['id']}")[0] == 200
    assert api.call("DELETE", f"/api/modules/scout/sources/{source['id']}")[0] == 200
    rows = {r["action"]: r for r in _audit(ctx)}
    assert rows["module.delete"]["details"]["grants"] == ["kg_read", "kg_write_output", "mcp"]
    archive = rows["module.delete"]["details"]["archive"]
    body = json.loads(ctx.s3.get_object(Bucket="example-assets-111122223333-us-east-1", Key=archive)["Body"].read())
    assert body["instructions"] == "Use the competitor price list" and body["mcp_servers"][0]["name"] == "crm"
    assert rows["product.delete"]["details"]["name"] == "P"
    assert rows["source.delete"]["details"]["address"].startswith("https://a.example.com (sha256:")


def test_b2_058_a_failed_archive_does_not_fail_the_delete(api: Client, ctx: TestContext) -> None:
    status, product, _ = api.call("POST", "/api/products", {"name": "P"})
    assert status == 201, product

    def boom(**kwargs: Any) -> Any:
        raise RuntimeError("bucket unavailable")

    ctx.s3.put_object = boom
    assert api.call("DELETE", f"/api/products/{product['id']}")[0] == 200
    assert next(r for r in _audit(ctx) if r["action"] == "product.delete")["details"]["archive"] == ""


def test_b2_082_deleting_a_hub_node_is_refused_rather_than_timing_out(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import routes

    hub, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Hub"), "user:u-admin")
    ctx.graph._table.update_item(
        Key={"pk": f"N#{hub.id}", "sk": "META"},
        UpdateExpression="SET in_degree = :n",
        ExpressionAttributeValues={":n": routes.MAX_DELETE_EDGES + 1},
    )
    status, body, _ = api.call("DELETE", f"/api/graph/nodes/{hub.id}")
    # r3 B3-069: queued for the sweep to take apart, rather than refused.
    assert status == 202 and body == {"queued": True, "edges": routes.MAX_DELETE_EDGES + 1}


def test_b2_084_a_disabled_account_is_not_softened_by_throttling(ctx: TestContext) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.core.errors import AccountDisabledError
    from insights_radar.identity.principal import Principal, Role
    from insights_radar.identity.users import LiveRoles, UserView

    class Users:
        def __init__(self) -> None:
            self.mode = "ok"
            self.enabled = True

        def get(self, sub: str) -> UserView:
            if self.mode == "throttle":
                raise ClientError({"Error": {"Code": "TooManyRequestsException"}}, "AdminGetUser")
            return UserView(sub, "a@example.com", Role.EDITOR, self.enabled, "CONFIRMED", "")

    clock = [0.0]
    users = Users()
    roles = LiveRoles(users, ttl=30, now=lambda: clock[0])
    principal = Principal.from_claims({"sub": "s1", "cognito:groups": "[editor]"})
    roles.confirm(principal)
    users.mode = "throttle"
    clock[0] = 40.0
    assert roles.confirm(principal).role is Role.VIEWER  # a short grace, as a viewer only
    # Once the account is known to be disabled, throttling never lets it back in.
    users.mode, users.enabled, clock[0] = "ok", False, 80.0
    with pytest.raises(AccountDisabledError):
        roles.confirm(principal)
    users.mode, clock[0] = "throttle", 120.0
    with pytest.raises(AccountDisabledError):
        roles.confirm(principal)


def test_b2_094_an_abandoned_upload_is_removed_from_the_bucket(api: Client, ctx: TestContext) -> None:
    from insights_radar.api import workers

    status, product, _ = api.call("POST", "/api/products", {"name": "P"})
    assert status == 201, product
    status, presigned, _ = api.call(
        "POST", f"/api/products/{product['id']}/documents", {"filename": "spec.pdf", "size": 1000}
    )
    assert status == 201, presigned
    key = ctx.products.list_documents(product["id"])[0].s3_key
    ctx.s3.put_object(Bucket="example-assets-111122223333-us-east-1", Key=key, Body=b"never completed")
    assert ctx.products.sweep_expired_uploads() == 0  # not yet expired
    ctx.clock.advance(timedelta(days=2))
    assert workers.sweep({}, None, ctx)["abandoned_uploads"] == 1
    with pytest.raises(Exception):
        ctx.s3.get_object(Bucket="example-assets-111122223333-us-east-1", Key=key)
    assert ctx.products.list_documents(product["id"]) == []


def test_b2_095_a_fetch_has_a_wall_clock_deadline(ctx: TestContext) -> None:
    from insights_radar.core.fetch import FetchError, SafeFetcher
    from tests.unit.test_core_fetch import Scripted, raw

    ticks = iter([0.0, 100.0, 100.0])  # the clock jumps past the deadline before the request goes out
    fetcher = SafeFetcher(now=lambda: next(ticks))
    try:
        with pytest.raises(FetchError, match="took longer than"):
            fetcher.fetch("https://example.com/slow", deadline_seconds=10)
    finally:
        fetcher.close()
    # And a host that drips bytes is cut off mid-body rather than held open to the Lambda timeout.
    calls = [0]

    def dripping() -> float:
        calls[0] += 1  # the deadline base, then the pre-request check, then mid-body
        return {1: 0.0, 2: 1.0}.get(calls[0], 999.0)

    body = raw(200, {"content-type": "text/plain"}, b"x" * 100)
    slow = SafeFetcher(backend=Scripted(body), now=dripping)
    try:
        with pytest.raises(FetchError, match="took longer than"):
            slow.fetch("https://example.com/drip", deadline_seconds=10)
    finally:
        slow.close()


def test_b2_098_a_throttled_commit_check_is_retried(ctx: TestContext) -> None:
    from insights_radar.radars.service import MemoryBlobStore, RadarService

    service = RadarService(ctx.ops_table, MemoryBlobStore(), ctx.clock, sleep=lambda _s: None)
    service.create(_admin(), "r1", "R1", template="blank")
    reads = [0]
    real = service.get

    def flaky(slug: str) -> Any:
        reads[0] += 1
        if reads[0] <= 2:
            raise RuntimeError("throttled")
        return real(slug)

    service.get = flaky  # type: ignore[method-assign]
    try:
        assert service._committed("r1", 1) is True  # two throttles, then the answer
        reads[0] = -10  # every attempt fails
        assert service._committed("r1", 1) is None
    finally:
        service.get = real  # type: ignore[method-assign]


def test_b2_099_a_radar_delete_takes_the_record_first(api: Client, ctx: TestContext) -> None:
    from botocore.exceptions import ClientError

    status, radar, _ = api.call("POST", "/api/radars", {"slug": "d1", "name": "D", "template": "blank"})
    assert status == 201, radar
    status, saved, _ = api.call(
        "PUT",
        "/api/radars/d1/entries",
        {"entries": [{"key": "a", "title": "A", "sector": "General", "ring": "Now"}], "expected_version": 1},
    )
    assert status == 200, saved
    real = ctx.ops_table.delete_item

    def raced(**kwargs: Any) -> Any:
        if kwargs.get("Key", {}).get("sk") == "META" and "ConditionExpression" in kwargs:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "DeleteItem")
        return real(**kwargs)

    ctx.ops_table.delete_item = raced
    try:
        # A save landed between the read and the delete: refused, with nothing removed.
        assert api.call("DELETE", "/api/radars/d1")[0] == 409

        # Any other DynamoDB error is raised rather than reported as a conflict.
        def throttled(**kwargs: Any) -> Any:
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "DeleteItem")

        ctx.ops_table.delete_item = throttled
        assert api.call("DELETE", "/api/radars/d1")[0] == 503
    finally:
        ctx.ops_table.delete_item = real
    assert ctx.radars.get("d1").current_version == 2
    assert len(ctx.radars.versions_page("d1", 10)[0]) == 2
    # Uncontended, the record goes first and then everything under it.
    status, _, _ = api.call("DELETE", "/api/radars/d1")
    assert status == 200
    assert "Item" not in ctx.ops_table.get_item(Key={"pk": "RADAR#d1", "sk": "META"})
    assert ctx.ops_table.query(KeyConditionExpression=Key("pk").eq("RADAR#d1")).get("Items") == []


def test_b2_024_one_hung_draft_run_does_not_block_every_editor(api: Client, ctx: TestContext) -> None:
    from insights_radar.modules.repository import ModuleRun, RunStatus

    module = ctx.modules.get("draft")
    assert module.max_concurrency > 1
    assert api.call("PATCH", "/api/modules/draft", {"version": module.version, "enabled": True})[0] == 200
    ctx.modules.put_run(ModuleRun("draft", "hung", RunStatus.RUNNING, "manual", "2026-10-02T07:59:00.000Z", "u"))
    idea = ctx.graph.create(
        Node(
            id="",
            type=NodeType.CONTENT_IDEA,
            title="I",
            attributes={"content_type": "b", "audience": "a"},
            module="content",
        ),
        "module:content",
    )
    status, run, _ = api.call("POST", f"/api/nodes/{idea.id}/draft", {}, role="editor")
    assert status == 202, run


# ---- port of CTR H-026 (68cfa5e3, e3484870): a Drafts run needs a content idea ---------------------------


def test_h026_a_drafts_run_without_a_content_idea_is_refused_before_it_holds_budget(ctx: TestContext) -> None:
    with pytest.raises(ValidationError, match="needs a content idea"):
        ctx.module_service.start("draft", "manual", "u-admin")
    assert ctx.modules.list_runs("draft")[0] == []  # no run was created, so no budget was held
