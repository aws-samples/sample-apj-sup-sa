# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Watched records and the what's-new digest (CTR subscriptions, subscription-digest)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from insights_radar.api.app import Application
from insights_radar.core.clock import FixedClock
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.insights import digest as digest_mod
from tests.integration.harness import Client, TestContext


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> TestContext:
    return TestContext(graph_table, ops_table, clock)


@pytest.fixture
def api(ctx: TestContext) -> Client:
    return Client(Application(ctx))


def test_watching_a_record_is_per_person(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    beta, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="beta"), "u")
    assert api.call("GET", "/api/me/watches", role="viewer")[1] == {"items": []}
    s, b, _ = api.call("PUT", f"/api/me/watches/nodes/{acme.id}", {}, role="viewer")
    assert s == 200 and b == {"id": acme.id, "watching": True}
    api.call("PUT", f"/api/me/watches/nodes/{beta.id}", {}, role="viewer")
    api.call("PUT", f"/api/me/watches/nodes/{beta.id}", {}, role="viewer")  # watching twice is one watch
    titles = [i["title"] for i in api.call("GET", "/api/me/watches", role="viewer")[1]["items"]]
    assert titles == ["Acme", "beta"]
    assert api.call("GET", "/api/me/watches", role="editor")[1] == {"items": []}
    s, b, _ = api.call("DELETE", f"/api/me/watches/nodes/{acme.id}", role="viewer")
    assert s == 200 and b["watching"] is False
    assert [i["id"] for i in api.call("GET", "/api/me/watches", role="viewer")[1]["items"]] == [beta.id]
    assert api.call("PUT", "/api/me/watches/nodes/organization/nope", {}, role="viewer")[0] == 404
    assert api.call("PUT", "/api/me/watches/nodes/bogus/x", {}, role="viewer")[0] == 404
    # A gap is not for viewers (404), and nothing but an entity can be watched (400).
    gap = ctx.graph.create(Node(id="", type=NodeType.GAP, title="No plan", summary="s"), "u")
    assert api.call("PUT", f"/api/me/watches/nodes/{gap.id}", {}, role="viewer")[0] == 404
    assert api.call("PUT", f"/api/me/watches/nodes/{gap.id}", {})[0] == 400
    gamma, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Gamma"), "u")
    trend, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.PRODUCT, title="Delta"), "u")
    monkeypatch.setattr(digest_mod, "MAX_WATCHES", 2)
    s, b, _ = api.call("PUT", f"/api/me/watches/nodes/{acme.id}", {}, role="viewer")
    assert s == 200
    s, b, _ = api.call("PUT", f"/api/me/watches/nodes/{gamma.id}", {})
    assert s == 200  # the administrator watches one record
    assert api.call("PUT", f"/api/me/watches/nodes/{trend.id}", {}, role="viewer")[0] == 400  # a third: over the limit
    # A watched record that is removed frees its place.
    ctx.digests.set_watch("u-viewer", "organization/removed-since", False)
    ctx.digests.set_watch("u-viewer", acme.id, False)
    ctx.digests._t.put_item(Item={"pk": "USER#u-viewer", "sk": "WATCH#organization/removed-since", "kind": "watch"})
    assert api.call("PUT", f"/api/me/watches/nodes/{trend.id}", {}, role="viewer")[0] == 200
    assert "organization/removed-since" not in ctx.digests.watches("u-viewer")


def test_the_digest_lists_what_is_new_since_the_last_read(api: Client, ctx: TestContext) -> None:
    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    old = ctx.graph.create(Node(id="", type=NodeType.FACT, title="Old news", evidence_url="https://a.example/1"), "u")
    ctx.graph.link(old.id, EdgeType.ABOUT, acme.id, "u")
    api.call("PUT", f"/api/me/watches/nodes/{acme.id}", {}, role="viewer")
    s, b, _ = api.call("POST", "/api/me/digest/read", {}, role="viewer")
    assert s == 200 and b["read_at"]
    ctx.clock.advance(timedelta(hours=1))
    fresh = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Acme recalls", evidence_url="https://a.example/2"), "u"
    )
    ctx.graph.link(fresh.id, EdgeType.ABOUT, acme.id, "u")
    trend = ctx.graph.create(Node(id="", type=NodeType.TREND, title="Recalls rise", summary="s"), "u")
    gap = ctx.graph.create(Node(id="", type=NodeType.GAP, title="No recall plan", summary="s"), "u")
    ctx.graph.link(gap.id, EdgeType.ABOUT, acme.id, "u")
    s, b, _ = api.call("GET", "/api/me/digest", role="viewer")
    assert (
        s == 200 and [n["id"] for n in b["new"]["trend"]] == [trend.id] and "gap" not in b["new"]
    )  # never read for a viewer
    (w,) = b["watched"]
    assert w["node"]["id"] == acme.id and [n["id"] for n in w["new"]] == [fresh.id] and w["count"] == 1
    assert b["total"] == 2
    # An administrator sees the gap too; their first digest looks a week back.
    s, b, _ = api.call("GET", "/api/me/digest")
    assert [n["id"] for n in b["new"]["gap"]] == [gap.id] and b["watched"] == []
    # An explicit since, which cannot reach further back than 90 days.
    s, b, _ = api.call("GET", "/api/me/digest", role="viewer", query={"since": "2000-01-01T00:00:00Z"})
    assert s == 200 and b["since"] > "2000" and len(b["watched"][0]["new"]) == 2
    assert api.call("GET", "/api/me/digest", role="viewer", query={"since": "yesterday"})[0] == 400
    # After reading, nothing is new.
    api.call("POST", "/api/me/digest/read", {}, role="viewer")
    s, b, _ = api.call("GET", "/api/me/digest", role="viewer")
    assert b["total"] == 0


def test_a_watched_record_the_reader_may_not_see_is_left_out(api: Client, ctx: TestContext) -> None:
    gap = ctx.graph.create(Node(id="", type=NodeType.GAP, title="No plan", summary="s"), "u")
    ctx.digests.set_watch("u-viewer", gap.id, True)  # watched before the reader lost access
    assert api.call("GET", "/api/me/watches", role="viewer")[1] == {"items": []}
    assert api.call("GET", "/api/me/digest", role="viewer")[1]["watched"] == []
    ctx.digests.set_watch("u-admin", gap.id, True)
    b = api.call("GET", "/api/me/digest")[1]
    assert [w["node"]["id"] for w in b["watched"]] == [gap.id]


def test_a_viewer_is_not_told_how_many_hidden_neighbours_a_record_has(api: Client, ctx: TestContext) -> None:
    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    fact = ctx.graph.create(Node(id="", type=NodeType.FACT, title="Acme ships", evidence_url="https://a.ex/1"), "u")
    gap = ctx.graph.create(Node(id="", type=NodeType.GAP, title="No plan", summary="s"), "u")
    ctx.graph.link(fact.id, EdgeType.ABOUT, acme.id, "u")
    ctx.graph.link(gap.id, EdgeType.ABOUT, acme.id, "u")
    b = api.call("GET", f"/api/graph/nodes/{acme.id}/neighbors", role="viewer", query={"offset": "0"})[1]
    assert b["total"] == 1 and [n["id"] for n in b["nodes"]] == [acme.id, fact.id] and b["truncated"] is False
    b = api.call("GET", f"/api/graph/nodes/{acme.id}/neighbors", query={"offset": "0"})[1]
    assert b["total"] == 2


def test_marking_read_stops_at_the_digest_on_screen(api: Client, ctx: TestContext) -> None:
    shown = api.call("GET", "/api/me/digest", role="viewer")[1]
    ctx.clock.advance(timedelta(minutes=5))
    late = ctx.graph.create(Node(id="", type=NodeType.TREND, title="Arrived after", summary="s"), "u")
    s, b, _ = api.call("POST", "/api/me/digest/read", {"upto": shown["until"]}, role="viewer")
    assert s == 200 and b["read_at"] == shown["until"]
    assert [n["id"] for n in api.call("GET", "/api/me/digest", role="viewer")[1]["new"]["trend"]] == [late.id]
    # A time in the future is read as now; a malformed one is refused.
    s, b, _ = api.call("POST", "/api/me/digest/read", {"upto": "2999-01-01T00:00:00Z"}, role="viewer")
    assert b["read_at"] < "2999"
    assert api.call("POST", "/api/me/digest/read", {"upto": "soon"}, role="viewer")[0] == 400
    assert api.call("POST", "/api/me/digest/read", {"x": 1}, role="viewer")[0] == 400


def test_the_digest_lists_custom_module_outputs_under_their_label(api: Client, ctx: TestContext) -> None:
    body = {
        "slug": "tariffs",
        "name": "Tariffs",
        "insight_kind": "tariff_item",
        "insight_label": "Tariff changes",
        "grants": ["kg_read", "kg_write_output"],
    }
    s, err, _ = api.call("POST", "/api/modules", body)
    assert s == 201, err
    item = ctx.graph.create(
        Node(id="", type=NodeType.INSIGHT, insight_kind="tariff_item", title="Steel duty up", summary="s"), "u"
    )
    b = api.call("GET", "/api/me/digest", role="viewer")[1]
    assert [n["id"] for n in b["new"]["insight:tariff_item"]] == [item.id]
    assert b["labels"] == {"insight:tariff_item": api.call("GET", "/api/nav")[1]["custom"][0]["label"]}


def test_r8_newest_links_per_type_not_alphabetical(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    ctx.digests.set_watch("u-admin", acme.id, True)
    monkeypatch.setattr(digest_mod, "WATCH_PER_TYPE", 2)
    for i in range(4):  # older events, in a type that sorts first
        e = ctx.graph.create(
            Node(id="", type=NodeType.EVENT, title=f"Event {i}", evidence_url=f"https://e.example/{i}"), "u"
        )
        ctx.graph.link(e.id, EdgeType.ABOUT, acme.id, "u")
        ctx.clock.advance(timedelta(seconds=1))
    api.call("POST", "/api/me/digest/read", {})
    ctx.clock.advance(timedelta(minutes=1))
    trend = ctx.graph.create(Node(id="", type=NodeType.TREND, title="Newest trend", summary="s"), "u")
    ctx.graph.link(trend.id, EdgeType.ABOUT, acme.id, "u")
    (w,) = api.call("GET", "/api/me/digest")[1]["watched"]
    assert [n["id"] for n in w["new"]] == [trend.id]
    with pytest.raises(Exception, match="direction"):
        ctx.graph.newest_links(
            acme.id, direction="both", edge_types=[EdgeType.ABOUT], node_types=[NodeType.FACT], per_type=1
        )


def test_r8_a_cut_list_ends_the_digest_there(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    monkeypatch.setattr(digest_mod, "PER_TYPE", 2)
    api.call("POST", "/api/me/digest/read", {}, role="viewer")
    made = []
    for i in range(3):
        ctx.clock.advance(timedelta(seconds=1))
        made.append(ctx.graph.create(Node(id="", type=NodeType.TREND, title=f"T{i}", summary="s"), "u"))
    b = api.call("GET", "/api/me/digest", role="viewer")[1]
    assert b["more"]["trend"] is True and [n["id"] for n in b["new"]["trend"]] == [made[1].id, made[0].id]
    assert b["until"] == f"{made[1].updated_at}#{made[1].id}"
    api.call("POST", "/api/me/digest/read", {"upto": b["until"]}, role="viewer")
    b = api.call("GET", "/api/me/digest", role="viewer")[1]
    assert [n["id"] for n in b["new"]["trend"]] == [made[2].id] and b["more"]["trend"] is False
    # A digest loaded earlier cannot move the read point back.
    assert (
        api.call("POST", "/api/me/digest/read", {"upto": "2026-01-01T00:00:00Z"}, role="viewer")[1]["read_at"]
        > "2026-10"
    )


def test_r8_legal_form_only_as_an_organizations_last_word() -> None:
    from insights_radar.graph.repository import _name_variants

    assert "Acme" in _name_variants("Acme Corp", NodeType.ORGANIZATION)
    assert "Acme" not in _name_variants("Acme Corp", NodeType.TOPIC)
    assert "Op Bank" not in _name_variants("Co Op Bank", NodeType.ORGANIZATION)
    assert _name_variants("Inc", NodeType.ORGANIZATION) == []


def test_r9_records_in_the_same_millisecond_are_never_skipped(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    monkeypatch.setattr(digest_mod, "PER_TYPE", 2)
    api.call("POST", "/api/me/digest/read", {}, role="viewer")
    ctx.clock.advance(timedelta(seconds=1))
    same = [ctx.graph.create(Node(id="", type=NodeType.TREND, title=f"T{i}", summary="s"), "u") for i in range(3)]
    seen: list[str] = []
    for _ in range(3):
        b = api.call("GET", "/api/me/digest", role="viewer")[1]
        seen += [n["id"] for n in b["new"]["trend"]]
        api.call("POST", "/api/me/digest/read", {"upto": b["until"]}, role="viewer")
    assert sorted(seen) == sorted(n.id for n in same) and len(seen) == 3


def test_r9_a_page_thinned_by_hidden_records_still_ends_the_window(
    api: Client, ctx: TestContext, monkeypatch: Any
) -> None:
    monkeypatch.setattr(digest_mod, "PER_TYPE", 1)  # the page reads 3; two hidden gaps thin it
    api.call("POST", "/api/me/digest/read", {}, role="viewer")
    for i in range(4):
        ctx.clock.advance(timedelta(seconds=1))
        ctx.graph.create(Node(id="", type=NodeType.TREND, title=f"G{i}", summary="s", private=True), "u")
    b = api.call("GET", "/api/me/digest", role="viewer")[1]
    # Every page is read past the hidden records, so the list is not cut and the window runs to now.
    assert b["new"]["trend"] == [] and b["more"]["trend"] is False and "#" not in b["until"]
    s, r, _ = api.call("POST", "/api/me/digest/read", {"upto": b["until"].split("#")[0] + "#nope"}, role="viewer")
    assert s == 200 and "#" not in r["read_at"]  # an id that is not a record id is dropped
    assert api.call("POST", "/api/me/digest/read", {"upto": "bad#gap/x"}, role="viewer")[0] == 400


def test_r10_hidden_only_pages_never_stop_a_viewers_digest(api: Client, ctx: TestContext, monkeypatch: Any) -> None:
    monkeypatch.setattr(digest_mod, "PER_TYPE", 1)
    monkeypatch.setattr(digest_mod, "MAX_PAGES", 2)
    api.call("POST", "/api/me/digest/read", {}, role="viewer")
    hidden = []
    for i in range(9):  # three pages of three, every one hidden from a viewer
        ctx.clock.advance(timedelta(seconds=1))
        hidden.append(ctx.graph.create(Node(id="", type=NodeType.TREND, title=f"G{i}", summary="s", private=True), "u"))
    b = api.call("GET", "/api/me/digest", role="viewer")[1]
    assert b["total"] == 0 and b["more"]["trend"] is True  # read on past two pages, then cut where it stopped
    assert b["until"].endswith("#~") and not any(n.id in b["until"] for n in hidden)  # no private id handed out
    monkeypatch.setattr(digest_mod, "MAX_PAGES", 5)
    b = api.call("GET", "/api/me/digest", role="viewer")[1]
    assert b["more"]["trend"] is False  # with room to read every page, nothing is cut


def test_r10_mark_read_stores_the_index_form(api: Client, ctx: TestContext) -> None:
    s, r, _ = api.call("POST", "/api/me/digest/read", {"upto": "2026-10-02T15:00:00+08:00"}, role="viewer")
    assert s == 200 and r["read_at"] == "2026-10-02T07:00:00.000Z"
    s, r, _ = api.call("POST", "/api/me/digest/read", {"upto": "2999-01-01T00:00:00Z#trend/x"}, role="viewer")
    assert r["read_at"] == "2026-10-02T08:00:00.000Z"  # never later than now, and no id for a future time


def test_r11_exact_name_search_drops_a_legal_suffix_only_for_organizations(api: Client, ctx: TestContext) -> None:
    org, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    b = api.call("POST", "/api/graph/search", {"query": "Acme Corp", "types": ["organization"]})[1]
    assert b["items"] and b["items"][0]["node"]["id"] == org.id
