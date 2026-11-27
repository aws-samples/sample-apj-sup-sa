# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Round-2 campaign: every round-2 feature crossed with every caller and every input scenario.

Each case is one (feature, role, scenario) and checks the status a person would get, plus the
property that matters for that scenario (a hidden record never shows, a bad input is refused with
400 or 404, never 500). The expectations come from rules, not from recorded output.
"""

from __future__ import annotations

import itertools
from datetime import timedelta
from typing import Any

import pytest

from insights_radar.api.app import Application
from insights_radar.core.clock import FixedClock
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from tests.integration import harness
from tests.integration.harness import Client, TestContext

ROLES = ("anon", "viewer", "editor", "admin")


@pytest.fixture
def world(graph_table: Any, ops_table: Any, clock: FixedClock) -> dict[str, Any]:
    ctx = TestContext(graph_table, ops_table, clock)
    api = Client(Application(ctx))
    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme Motors"), "u")
    fact = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Acme cuts motor prices", evidence_url="https://a.example.com/1"), "u"
    )
    clock.advance(timedelta(seconds=1))
    trend = ctx.graph.create(Node(id="", type=NodeType.TREND, title="Motor price war", summary="Prices fall"), "u")
    gap = ctx.graph.create(Node(id="", type=NodeType.GAP, title="No warranty plan", summary="s"), "u")
    ctx.graph.link(fact.id, EdgeType.ABOUT, acme.id, "u")
    ctx.graph.link(trend.id, EdgeType.SUPPORTS, fact.id, "u")
    ctx.graph.link(gap.id, EdgeType.ABOUT, acme.id, "u")
    return {"api": api, "ctx": ctx, "entity": acme, "fact": fact, "trend": trend, "gap": gap}


# ---- record-scoped features ---------------------------------------------------------------------

TARGETS = ("entity", "fact", "trend", "gap", "missing", "bad_type", "dot_uid")
RECORD_FEATURES = {
    "get_node": ("GET", "/api/graph/nodes/{id}", None, None),
    "neighbours": ("GET", "/api/graph/nodes/{id}/neighbors", None, {"offset": "0"}),
    "neighbours_d2": ("GET", "/api/graph/nodes/{id}/neighbors", None, {"depth": "2"}),
    "brief": ("GET", "/api/graph/nodes/{id}/brief", None, None),
    "watch": ("PUT", "/api/me/watches/nodes/{id}", {}, None),
    "unwatch": ("DELETE", "/api/me/watches/nodes/{id}", None, None),
}


def _target_id(world: dict[str, Any], target: str) -> str:
    return {
        "missing": "organization/does-not-exist",
        "bad_type": "planet/x",
        "dot_uid": "organization/..",
    }.get(target) or world[target].id


def _expected(feature: str, role: str, target: str) -> int:
    if role == "anon":
        return 401
    if target in ("bad_type", "dot_uid"):
        return 404
    if feature == "unwatch":
        return 200  # stopping a watch on anything of a known type is a no-op success
    if target == "missing":
        return 404
    if target == "gap" and role == "viewer":
        return 404  # gaps are written from private product documents
    if feature == "watch" and target != "entity":
        return 400  # only entities have records about them to follow
    return 200


@pytest.mark.parametrize(("feature", "role", "target"), list(itertools.product(RECORD_FEATURES, ROLES, TARGETS)))
def test_record_features(world: dict[str, Any], feature: str, role: str, target: str) -> None:
    method, path, body, query = RECORD_FEATURES[feature]
    node_id = _target_id(world, target)
    s, b, _ = world["api"].call(method, path.format(id=node_id), body, role=role, query=query)
    assert s == _expected(feature, role, target), (s, b)
    if s == 200 and role == "viewer":
        text = repr(b)
        assert world["gap"].id not in text or (target == "gap" and feature == "unwatch")


# ---- request-body features ------------------------------------------------------------------------

LONG = "tariff " * 80  # 560 characters, past every 500-character limit
BODY_CASES: dict[str, tuple[str, str, Any, dict[str, str] | None, int]] = {
    # name: (method, path, body, query, status for a signed-in caller)
    "ask_ok": ("POST", "/api/graph/ask", {"question": "motor price war"}, None, 200),
    "ask_typed": ("POST", "/api/graph/ask", {"question": "motor prices", "types": ["trend", "fact"]}, None, 200),
    "ask_nothing": ("POST", "/api/graph/ask", {"question": "zeppelin airships"}, None, 200),
    "ask_empty": ("POST", "/api/graph/ask", {"question": ""}, None, 400),
    "ask_short": ("POST", "/api/graph/ask", {"question": "x"}, None, 400),
    "ask_long": ("POST", "/api/graph/ask", {"question": LONG}, None, 400),
    "ask_number": ("POST", "/api/graph/ask", {"question": 42}, None, 400),
    "ask_extra": ("POST", "/api/graph/ask", {"question": "motor prices", "why": 1}, None, 400),
    "ask_bad_type": ("POST", "/api/graph/ask", {"question": "motor prices", "types": ["planet"]}, None, 400),
    "ask_many_types": ("POST", "/api/graph/ask", {"question": "motor prices", "types": ["fact"] * 21}, None, 400),
    "ask_no_body": ("POST", "/api/graph/ask", None, None, 400),
    "digest_default": ("GET", "/api/me/digest", None, None, 200),
    "digest_since": ("GET", "/api/me/digest", None, {"since": "2026-10-01T00:00:00Z"}, 200),
    "digest_future": ("GET", "/api/me/digest", None, {"since": "2999-01-01T00:00:00Z"}, 200),
    "digest_ancient": ("GET", "/api/me/digest", None, {"since": "1999-01-01T00:00:00Z"}, 200),
    "digest_bad": ("GET", "/api/me/digest", None, {"since": "last week"}, 400),
    "digest_blank": ("GET", "/api/me/digest", None, {"since": "   "}, 200),
    "read_default": ("POST", "/api/me/digest/read", {}, None, 200),
    "read_upto": ("POST", "/api/me/digest/read", {"upto": "2026-10-02T08:00:00Z"}, None, 200),
    "read_future": ("POST", "/api/me/digest/read", {"upto": "2999-01-01T00:00:00Z"}, None, 200),
    "read_bad": ("POST", "/api/me/digest/read", {"upto": "soon"}, None, 400),
    "read_extra": ("POST", "/api/me/digest/read", {"x": 1}, None, 400),
    "read_number": ("POST", "/api/me/digest/read", {"upto": 5}, None, 400),
    "watches": ("GET", "/api/me/watches", None, None, 200),
    "search_ok": ("POST", "/api/graph/search", {"query": "motor"}, None, 200),
    "search_typed": ("POST", "/api/graph/search", {"query": "motor", "types": ["trend"]}, None, 200),
    "search_long": ("POST", "/api/graph/search", {"query": "x" * 501}, None, 400),
    "search_bad_type": ("POST", "/api/graph/search", {"query": "motor", "types": ["planet"]}, None, 400),
    "favourites": ("GET", "/api/me/favourites", None, None, 200),
    "favourite_missing": ("PUT", "/api/me/favourites/radars/no-such-radar", {}, None, 404),
    "unfavourite_missing": ("DELETE", "/api/me/favourites/radars/no-such-radar", None, None, 200),
    "entries_by_name": ("GET", "/api/radars/entries", None, {"q": "motor"}, 200),
    "entries_short": ("GET", "/api/radars/entries", None, {"q": "m"}, 400),
    "entries_none": ("GET", "/api/radars/entries", None, None, 400),
    "entries_by_node": ("GET", "/api/radars/entries", None, {"node_id": "organization/x"}, 200),
}


@pytest.mark.parametrize(("case", "role"), list(itertools.product(BODY_CASES, ROLES)))
def test_body_features(world: dict[str, Any], case: str, role: str) -> None:
    method, path, body, query, ok_status = BODY_CASES[case]
    s, b, _ = world["api"].call(method, path, body, role=role, query=query)
    assert s == (401 if role == "anon" else ok_status), (s, b)
    if s == 200 and role == "viewer":
        assert world["gap"].id not in repr(b)  # a viewer never sees the gap, whatever the feature


# ---- radar features -----------------------------------------------------------------------------

harness.ROLES.setdefault(
    "outsider", {"sub": "u-outsider", "email": "outsider@example.com", "cognito:groups": "[editor]"}
)  # an editor who is not on this radar's editors
RADAR_ROLES = ("anon", "viewer", "outsider", "editor", "admin")
CAN_EDIT = {"editor", "admin"}


@pytest.fixture
def radar(world: dict[str, Any]) -> dict[str, Any]:
    api = world["api"]
    s, b, _ = api.call(
        "POST", "/api/radars", {"slug": "rivals", "name": "Rivals", "template": "competitor_landscape"}, role="editor"
    )
    assert s == 201, b
    entry = {"key": "k1", "title": "Linak", "sector": "Direct", "ring": "Leader"}
    assert (
        api.call("PUT", "/api/radars/rivals/entries", {"entries": [entry], "expected_version": 1}, role="editor")[0]
        == 200
    )
    assert api.call("PUT", "/api/radars/rivals/versions/2/tag", {"tag": "Q3 2026"}, role="editor")[0] == 200
    return world


READS: dict[str, tuple[str, dict[str, str] | None, int]] = {
    "radar": ("/api/radars/rivals", None, 200),
    "radar_by_tag": ("/api/radars/rivals", {"tag": "q3.2026"}, 200),
    "radar_unknown_tag": ("/api/radars/rivals", {"tag": "nope"}, 404),
    "radar_v1": ("/api/radars/rivals", {"version": "1"}, 200),
    "radar_missing": ("/api/radars/none", None, 404),
    "tags": ("/api/radars/rivals/tags", None, 200),
    "tags_missing_radar": ("/api/radars/none/tags", None, 404),
    "entry_history": ("/api/radars/rivals/entries/k1/history", None, 200),
    "entry_history_missing": ("/api/radars/rivals/entries/nope/history", None, 404),
    "export_tag": ("/api/radars/rivals/export", {"tag": "Q3 2026", "format": "json"}, 200),
    "finder": ("/api/radars/entries", {"q": "Linak"}, 200),
    "radars": ("/api/radars", None, 200),
}

WRITES: dict[str, tuple[str, str, Any, int]] = {
    # name: (method, path, body, status for someone who may edit the radar)
    "tag_v1": ("PUT", "/api/radars/rivals/versions/1/tag", {"tag": "Launch"}, 200),
    "tag_taken": ("PUT", "/api/radars/rivals/versions/1/tag", {"tag": "q3-2026"}, 409),
    "tag_missing_version": ("PUT", "/api/radars/rivals/versions/9/tag", {"tag": "later"}, 404),
    "untag": ("DELETE", "/api/radars/rivals/tags/q3-2026", None, 200),
    "untag_missing": ("DELETE", "/api/radars/rivals/tags/never", None, 404),
}


@pytest.mark.parametrize(("case", "role"), list(itertools.product(READS, RADAR_ROLES)))
def test_radar_reads(radar: dict[str, Any], case: str, role: str) -> None:
    path, query, ok_status = READS[case]
    s, b, _ = radar["api"].call("GET", path, role=role, query=query)
    assert s == (401 if role == "anon" else ok_status), (s, b)
    if case == "radar_by_tag" and s == 200:
        assert b["version"] == 2 and b["radar"]["current_version"] == 2


@pytest.mark.parametrize(("case", "role"), list(itertools.product(WRITES, RADAR_ROLES)))
def test_radar_writes(radar: dict[str, Any], case: str, role: str) -> None:
    method, path, body, ok_status = WRITES[case]
    s, b, _ = radar["api"].call(method, path, body, role=role)
    expected = 401 if role == "anon" else ok_status if role in CAN_EDIT else 403
    assert s == expected, (s, b)
    after = radar["api"].call("GET", "/api/radars/rivals/tags", role="admin")[1]["items"]
    changed = s == 200
    assert ({t["tag"] for t in after} != {"Q3 2026"}) == changed  # nothing changes unless the write succeeded


@pytest.mark.parametrize("role", RADAR_ROLES)
@pytest.mark.parametrize("on", [True, False])
def test_favourite_a_real_radar(radar: dict[str, Any], role: str, on: bool) -> None:
    s, b, _ = radar["api"].call(
        "PUT" if on else "DELETE", "/api/me/favourites/radars/rivals", {} if on else None, role=role
    )
    assert s == (401 if role == "anon" else 200), (s, b)
    if s == 200:
        assert radar["api"].call("GET", "/api/me/favourites", role=role)[1]["radars"] == (["rivals"] if on else [])


# ---- administrator features ---------------------------------------------------------------------------

MERGES: dict[str, tuple[str, Any, int | None]] = {
    # name: (duplicate, body, admin status; None: any refusal (4xx) is right, a 5xx never is)
    "dry_run": ("dup", {"into": "keep"}, 200),
    "apply": ("dup", {"into": "keep", "apply": True}, 200),
    "apply_false": ("dup", {"into": "keep", "apply": False}, 200),
    "into_self": ("dup", {"into": "dup"}, None),
    "into_other_type": ("dup", {"into": "topic"}, None),
    "into_missing": ("dup", {"into": "organization/zzz"}, None),
    "into_malformed": ("dup", {"into": "nope"}, 400),
    "into_number": ("dup", {"into": 5}, 400),
    "no_into": ("dup", {}, 400),
    "extra_field": ("dup", {"into": "keep", "force": True}, 400),
    "duplicate_is_evidence": ("fact", {"into": "keep"}, None),
    "duplicate_missing": ("missing", {"into": "keep"}, 404),
}


@pytest.fixture
def entities(world: dict[str, Any]) -> dict[str, Any]:
    g = world["ctx"].graph
    keep, _ = g.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme Corporation"), "u")
    dup, _ = g.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="ACME Corp"), "u")
    topic, _ = g.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Motor pricing"), "u")
    return {**world, "keep": keep.id, "dup": dup.id, "topic": topic.id, "missing": "organization/gone"}


@pytest.mark.parametrize(("case", "role"), list(itertools.product(MERGES, RADAR_ROLES)))
def test_entity_merges(entities: dict[str, Any], case: str, role: str) -> None:
    dup_key, body, admin_status = MERGES[case]
    dup = entities[dup_key].id if dup_key == "fact" else entities[dup_key]
    if isinstance(body.get("into"), str) and body["into"] in entities:
        body = {**body, "into": entities[body["into"]]}
    api = entities["api"]
    s, b, _ = api.call("POST", f"/api/graph/nodes/{dup}/merge", body, role=role)
    if role == "anon":
        assert s == 401
    elif role != "admin":
        assert s in (403, 404), (s, b)  # 404 when the duplicate does not exist at all
    elif admin_status is None:
        assert 400 <= s < 500, (s, b)
    else:
        assert s == admin_status, (s, b)
    still_there = api.call("GET", f"/api/graph/nodes/{entities['dup']}")[0] == 200
    assert still_there != (s == 200 and body.get("apply") is True)  # only an applied merge removes it


@pytest.mark.parametrize("role", RADAR_ROLES)
def test_hygiene_report(entities: dict[str, Any], role: str) -> None:
    s, b, _ = entities["api"].call("GET", "/api/graph/hygiene", role=role)
    assert s == {"anon": 401, "admin": 200}.get(role, 403), (s, b)
    if s == 200:
        groups = [
            {m["id"] for m in g["members"]} if isinstance(g, dict) and "members" in g else g for g in b["duplicates"]
        ]
        assert any(entities["dup"] in str(g) and entities["keep"] in str(g) for g in groups), b


# ---- run cancel ----------------------------------------------------------------------------------

RUN_STATES = ("queued", "running", "succeeded", "failed", "budget_exhausted", "cancelled", "missing_run", "no_module")


@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize(("state", "role"), list(itertools.product(RUN_STATES, RADAR_ROLES)))
def test_run_cancel(world: dict[str, Any], state: str, role: str, extra: bool) -> None:
    from insights_radar.modules.repository import ModuleRun, RunStatus

    ctx, api = world["ctx"], world["api"]
    module = "nope" if state == "no_module" else "signal"
    if state not in ("missing_run", "no_module"):
        ctx.modules.put_run(
            ModuleRun(
                "signal",
                "run-x",
                RunStatus(state),
                "manual",
                "2026-10-02T07:00:00.000Z",
                "u-admin",
                execution_arn="arn:aws:states:us-east-1:111122223333:execution:mr:run-x",
            )
        )
    s, b, _ = api.call("POST", f"/api/modules/{module}/runs/run-x/cancel", {"why": 1} if extra else {}, role=role)
    if role == "anon":
        expected = 401
    elif role != "admin":
        expected = 403
    elif extra:
        expected = 400
    elif state in ("missing_run", "no_module"):
        expected = 404
    elif RunStatus(state).terminal:
        expected = 409
    else:
        expected = 200
    assert s == expected, (s, b)
    if state not in ("missing_run", "no_module"):
        after = ctx.modules.get_run("signal", "run-x").status.value
        assert after == ("cancelled" if s == 200 else state)  # nothing changes unless the cancel succeeded


# ---- digest, ask and brief: data shapes x readers -------------------------------------------------

SINCE = {
    "default": None,
    "a_week": "2026-09-25T08:00:00Z",
    "an_hour": "2026-10-02T07:00:00Z",
    "after_all": "2026-10-02T09:00:00Z",
    "future": "2999-01-01T00:00:00Z",
    "ancient": "1999-01-01T00:00:00Z",
    "offset_zone": "2026-10-02T15:00:00+08:00",
}
READERS = ("viewer", "editor", "admin")


@pytest.mark.parametrize("gap_linked", [False, True])
@pytest.mark.parametrize("watched", [0, 1, 3])
@pytest.mark.parametrize(("since", "reader"), list(itertools.product(SINCE, READERS)))
def test_digest_shapes(world: dict[str, Any], since: str, reader: str, watched: int, gap_linked: bool) -> None:
    ctx, api = world["ctx"], world["api"]
    sub = harness.ROLES[reader]["sub"]  # type: ignore[index]
    others = [
        ctx.graph.upsert_entity(Node(id="", type=NodeType.PRODUCT, title=f"Widget {i}"), "u")[0] for i in range(2)
    ]
    for n in [world["entity"], *others][:watched]:
        ctx.digests.set_watch(sub, n.id, True)
    if not gap_linked:
        ctx.graph.unlink(world["gap"].id, EdgeType.ABOUT, world["entity"].id)
    query = {"since": SINCE[since]} if SINCE[since] else None
    s, b, _ = api.call("GET", "/api/me/digest", role=reader, query=query)
    assert s == 200, b
    assert len(b["watched"]) == watched and b["since"] <= b["until"]
    shown = repr(b)
    sees_gap = reader != "viewer"
    if not sees_gap:
        assert world["gap"].id not in shown
    # The world was written at 08:00 UTC; 15:00+08:00 is 07:00 UTC, so it is before it.
    window_open = since in ("default", "a_week", "an_hour", "ancient", "offset_zone")
    assert (world["trend"].id in shown) == window_open, (since, b["since"])
    if watched and window_open:
        about = (
            {n["id"] for n in b["watched"][0]["new"]} if b["watched"][0]["node"]["id"] == world["entity"].id else set()
        )
        if b["watched"][0]["node"]["id"] == world["entity"].id:
            assert world["fact"].id in about
            assert (world["gap"].id in about) == (gap_linked and sees_gap)
    assert b["total"] == sum(len(v) for v in b["new"].values()) + sum(w["count"] for w in b["watched"])


GRAPHS = ("empty", "entity_only", "unsourced", "one_site", "two_sites")
FILTERS = {"none": None, "match": ["trend", "fact", "organization"], "other": ["segment"]}


@pytest.mark.parametrize("reader", ["viewer", "admin"])
@pytest.mark.parametrize(("graph", "flt"), list(itertools.product(GRAPHS, FILTERS)))
def test_ask_coverage(graph_table: Any, ops_table: Any, clock: FixedClock, graph: str, flt: str, reader: str) -> None:
    ctx = TestContext(graph_table, ops_table, clock)
    api = Client(Application(ctx))
    if graph != "empty":
        acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme tariff motors"), "u")
    if graph in ("unsourced", "one_site", "two_sites"):
        trend = ctx.graph.create(Node(id="", type=NodeType.TREND, title="Tariff motors trend", summary="s"), "u")
        ctx.graph.link(trend.id, EdgeType.ABOUT, acme.id, "u")
    for i, host in enumerate(
        {"one_site": ["a.example.com"], "two_sites": ["a.example.com", "b.example.org"]}.get(graph, [])
    ):
        f = ctx.graph.create(
            Node(id="", type=NodeType.FACT, title=f"Tariff motors fact {i}", evidence_url=f"https://{host}/{i}"), "u"
        )
        ctx.graph.link(f.id, EdgeType.ABOUT, acme.id, "u")
        ctx.graph.link(trend.id, EdgeType.SUPPORTS, f.id, "u")
    ctx.reindex()  # in a deployment the stream indexer embeds every record as it is written
    body: dict[str, Any] = {"question": "tariff motors"}
    if FILTERS[flt]:
        body["types"] = FILTERS[flt]
    s, b, _ = api.call("POST", "/api/graph/ask", body, role=reader)
    assert s == 200, b
    expected = {
        "empty": "none",
        "entity_only": "no_sources",
        "unsourced": "no_sources",
        "one_site": "single_source",
        "two_sites": "sourced",
    }[graph]
    if flt == "other" or (graph == "empty"):
        expected = "none"
    assert b["coverage"] == expected, (graph, flt, b["coverage"], b["hits"])
    assert b["note"] and all(x["url"].startswith("https://") for x in b["sources"])


BRIEF_TYPES = ("entity", "fact", "trend", "gap")


@pytest.mark.parametrize("placed", [False, True])
@pytest.mark.parametrize(("target", "reader"), list(itertools.product(BRIEF_TYPES, READERS)))
def test_brief_shapes(world: dict[str, Any], target: str, reader: str, placed: bool) -> None:
    api = world["api"]
    if placed:
        api.call(
            "POST",
            "/api/radars",
            {"slug": "rivals", "name": "Rivals", "template": "competitor_landscape"},
            role="editor",
        )
        entry = {"key": "k1", "title": "Acme Motors", "sector": "Direct", "ring": "Leader", "node_id": world[target].id}
        api.call("PUT", "/api/radars/rivals/entries", {"entries": [entry], "expected_version": 1}, role="editor")
    s, b, _ = api.call("GET", f"/api/graph/nodes/{world[target].id}/brief", role=reader)
    if target == "gap" and reader == "viewer":
        assert s == 404
        return
    assert s == 200, b
    assert b["node"]["id"] == world[target].id
    assert bool(b["radars"]) == placed, b["radars"]
    if reader == "viewer":
        assert world["gap"].id not in repr(b)
    assert b["coverage"] in ("none", "no_sources", "single_source", "sourced")


EXPORT_PINS = {"current": None, "tag": {"tag": "Q3 2026"}, "version": {"version": "1"}}


@pytest.mark.parametrize("role", ["viewer", "outsider", "editor", "admin"])
@pytest.mark.parametrize(("fmt", "pin"), list(itertools.product(["csv", "json"], EXPORT_PINS)))
def test_export_pins(radar: dict[str, Any], fmt: str, pin: str, role: str) -> None:
    s, out, _ = radar["api"].call(
        "GET", "/api/radars/rivals/export", role=role, query={"format": fmt, **(EXPORT_PINS[pin] or {})}
    )
    assert s == 200, out
    # Version 1 is the empty radar; the current version and the tagged one (both 2) hold Linak.
    assert ("Linak" in str(out)) == (pin != "version")
