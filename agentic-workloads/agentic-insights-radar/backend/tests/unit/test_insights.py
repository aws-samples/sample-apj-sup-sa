# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import NotFoundError, ValidationError
from insights_radar.graph.indexer import StreamIndexer
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.graph.repository import GraphRepository
from insights_radar.graph.search import HashEmbedder, HybridSearch, InMemoryVectorIndex
from insights_radar.identity.principal import Principal, PrincipalKind, Role
from insights_radar.insights.service import InsightKind, InsightService, Sort

URL = "https://news.example.com/x"
ED = Principal(PrincipalKind.USER, "sub-ed", Role.EDITOR, "ed@example.com")
ED2 = Principal(PrincipalKind.USER, "sub-2", Role.EDITOR, "two@example.com")


@pytest.fixture
def env(
    graph_table: Any, ops_table: Any, clock: FixedClock
) -> tuple[InsightService, GraphRepository, InMemoryVectorIndex]:
    repo = GraphRepository(graph_table, clock)
    idx = InMemoryVectorIndex()
    return InsightService(repo, HybridSearch(repo, idx, HashEmbedder()), ops_table, clock), repo, idx


def index(idx: InMemoryVectorIndex, *nodes: Node) -> None:
    for n in nodes:
        idx.put(n.id, HashEmbedder().embed(n.search_text), StreamIndexer.metadata(n))


@pytest.mark.parametrize(
    ("raw", "nt", "kind"),
    [
        ("signals", NodeType.SIGNAL, None),
        ("trends", NodeType.TREND, None),
        ("gaps", NodeType.GAP, None),
        ("content", NodeType.CONTENT_IDEA, None),
        ("drafts", NodeType.CONTENT_DRAFT, None),
        ("insight:competitor_move", NodeType.INSIGHT, "competitor_move"),
    ],
)
def test_kind_parse(raw: str, nt: NodeType, kind: str | None) -> None:
    k = InsightKind.parse(raw)
    assert k.node_type is nt and k.insight_kind == kind


@pytest.mark.parametrize("raw", ["facts", "insight:", "insight:Bad", "insight", "signal", ""])
def test_kind_parse_rejects(raw: str) -> None:
    with pytest.raises(ValidationError):
        InsightKind.parse(raw)


def test_browse_newest_connected_and_search(env: Any, clock: FixedClock) -> None:
    svc, repo, idx = env
    made = []
    for i in range(30):
        made.append(repo.create(Node(id="", type=NodeType.SIGNAL, title=f"signal {i} about desks"), "u"))
        clock.advance(timedelta(seconds=1))
    fact = repo.create(Node(id="", type=NodeType.FACT, title="f", evidence_url=URL), "u")
    for target in made[3:6]:
        repo.link(target.id, EdgeType.SUPPORTS, fact.id, "u")
    repo.create(Node(id="", type=NodeType.FACT, title="g", evidence_url=URL), "u")
    p1 = svc.browse(InsightKind.parse("signals"))
    assert len(p1["items"]) == 25 and p1["items"][0]["title"] == "signal 29 about desks" and p1["cursor"]
    p2 = svc.browse(InsightKind.parse("signals"), cursor=p1["cursor"])
    assert len(p2["items"]) == 5
    c1 = svc.browse(InsightKind.parse("signals"), sort=Sort.CONNECTED)
    assert {i["title"] for i in c1["items"][:3]} == {
        "signal 3 about desks",
        "signal 4 about desks",
        "signal 5 about desks",
    }
    c2 = svc.browse(InsightKind.parse("signals"), sort=Sort.CONNECTED, cursor=c1["cursor"])
    assert len(c2["items"]) == 5 and c2["cursor"] is None
    index(idx, *made)
    s = svc.browse(InsightKind.parse("signals"), query="signal 7 desks")
    assert s["sort"] == "relevance" and s["items"][0]["title"] == "signal 7 about desks"
    sn = svc.browse(InsightKind.parse("signals"), query="desks", sort=Sort.NEWEST)
    assert sn["items"][0]["updated_at"] >= sn["items"][-1]["updated_at"]
    sc = svc.browse(InsightKind.parse("signals"), query="desks", sort=Sort.CONNECTED)
    assert sc["items"][0]["degree"] == 1
    from insights_radar.insights.service import AttrQuery

    none = svc.browse(InsightKind.parse("signals"), query="desks", attrs=AttrQuery({"urgency": frozenset({"x"})}))
    assert none["items"] == []
    with pytest.raises(ValidationError):
        svc.browse(InsightKind.parse("signals"), sort=Sort.RELEVANCE)
    with pytest.raises(ValidationError):
        svc.browse(InsightKind.parse("signals"), sort=Sort.CONNECTED, cursor="bad!")


def test_detail_groups_evidence(env: Any) -> None:
    svc, repo, _ = env
    org, _ = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    pro = repo.create(Node(id="", type=NodeType.FACT, title="pro", evidence_url=URL), "u")
    con = repo.create(Node(id="", type=NodeType.OPINION, title="con", evidence_url=URL), "u")
    sig = repo.create(Node(id="", type=NodeType.SIGNAL, title="s"), "u")
    trend = repo.create(Node(id="", type=NodeType.TREND, title="t"), "u")
    gap = repo.create(Node(id="", type=NodeType.GAP, title="g"), "u")
    repo.link(sig.id, EdgeType.SUPPORTS, pro.id, "u")
    repo.link(sig.id, EdgeType.CONTRADICTS, con.id, "u")
    repo.link(sig.id, EdgeType.ABOUT, org.id, "u")
    repo.link(trend.id, EdgeType.SUPPORTS, sig.id, "u")
    repo.link(gap.id, EdgeType.ADDRESSES, sig.id, "u")
    d = svc.detail(sig.id, ED)
    ev = d["evidence"]
    assert [x["node"]["title"] for x in ev["supports"]] == ["pro"]
    assert [x["node"]["title"] for x in ev["contradicts"]] == ["con"]
    assert ev["supports"][0]["node"]["evidence_url"] == URL
    assert [x["node"]["title"] for x in ev["about"]] == ["Acme"]
    assert [x["node"]["title"] for x in ev["supported_by"]] == ["t"]
    assert [x["node"]["title"] for x in ev["addressed_by"]] == ["g"]
    assert d["feedback"] == {"up": 0, "down": 0, "mine": None}
    g = svc.detail(gap.id)
    assert [x["node"]["title"] for x in g["evidence"]["addresses"]] == ["s"]
    assert "feedback" not in svc.detail(pro.id)
    with pytest.raises(NotFoundError):
        svc.detail("signal/00000000-0000-7000-8000-000000000000")


def test_feedback(env: Any) -> None:
    svc, repo, _ = env
    sig = repo.create(Node(id="", type=NodeType.SIGNAL, title="s"), "u")
    assert svc.give_feedback(sig.id, ED, "up", None) == {"up": 1, "down": 0, "mine": {"vote": "up", "comment": ""}}
    with pytest.raises(ValidationError, match="what is wrong"):
        svc.give_feedback(sig.id, ED2, "down", "  ")
    s = svc.give_feedback(sig.id, ED2, "down", "Only one source")
    assert s["up"] == 1 and s["down"] == 1
    s2 = svc.give_feedback(sig.id, ED, "down", "changed my mind")
    assert s2 == {"up": 0, "down": 2, "mine": {"vote": "down", "comment": "changed my mind"}}
    with pytest.raises(ValidationError):
        svc.give_feedback(sig.id, ED, "meh", None)
    with pytest.raises(ValidationError):
        svc.give_feedback(sig.id, ED, "up", "x" * 2001)
    fact = repo.create(Node(id="", type=NodeType.FACT, title="f", evidence_url=URL), "u")
    with pytest.raises(ValidationError, match="module outputs"):
        svc.give_feedback(fact.id, ED, "up", None)
    rows = svc.feedback_page(NodeType.SIGNAL)[0]
    assert {r["by"] for r in rows} == {"ed@example.com", "two@example.com"}


ANY_SINCE = "2026-10-02T08:00:00.000Z"


def test_working_on(env: Any) -> None:
    svc, repo, _ = env
    idea = repo.create(Node(id="", type=NodeType.CONTENT_IDEA, title="BPA-free buying guide"), "u")
    assert svc.set_working(idea.id, ED, True)[0]["email"] == "ed@example.com"
    assert len(svc.set_working(idea.id, ED2, True)) == 2
    # B-018: a colleague's identity is shown only to themself and to administrators
    assert svc.set_working(idea.id, ED, False) == [{"user": "", "email": "a colleague", "since": ANY_SINCE}]
    page = svc.browse(InsightKind.parse("content"), principal=ED2)
    assert page["items"][0]["working_on"][0]["user"] == "sub-2"
    assert svc.browse(InsightKind.parse("content"))["items"][0]["working_on"][0]["email"] == "a colleague"
    assert svc.detail(idea.id, ED2)["working_on"][0]["email"] == "two@example.com"
    admin = Principal(PrincipalKind.USER, "adm", Role.ADMIN, "a@example.com")
    assert svc.detail(idea.id, admin)["working_on"][0]["email"] == "two@example.com"
    sig = repo.create(Node(id="", type=NodeType.SIGNAL, title="s"), "u")
    with pytest.raises(ValidationError):
        svc.set_working(sig.id, ED, True)


def test_search_unconfigured(graph_table: Any, ops_table: Any, clock: FixedClock) -> None:
    svc = InsightService(GraphRepository(graph_table, clock), None, ops_table, clock)
    with pytest.raises(ValidationError, match="not configured"):
        svc.browse(InsightKind.parse("signals"), query="x desks")


def test_hu077_browse_by_title_folds_case(env: Any) -> None:
    svc, repo, idx = env
    made = [
        repo.create(Node(id="", type=NodeType.SIGNAL, title=t), "u") for t in ("beta desks", "Alpha desks", "gamma")
    ]
    page = svc.browse(InsightKind.parse("signals"), sort=Sort.TITLE)
    assert [i["title"] for i in page["items"]] == ["Alpha desks", "beta desks", "gamma"] and page["sort"] == "title"
    index(idx, *made)
    hits = svc.browse(InsightKind.parse("signals"), query="desks", sort=Sort.TITLE)
    titles = [i["title"] for i in hits["items"]]
    assert titles[:2] == ["Alpha desks", "beta desks"] and titles == sorted(titles, key=str.lower)


def test_hc126_a_closed_gap_leaves_the_list_unless_asked_for(env: Any) -> None:
    svc, repo, _ = env
    repo.create(Node(id="", type=NodeType.GAP, title="No SSO", attributes={"status": "closed"}), "u")
    repo.create(Node(id="", type=NodeType.GAP, title="No audit log", attributes={"status": "narrowed"}), "u")
    gaps = InsightKind.parse("gaps")
    assert [i["title"] for i in svc.browse(gaps)["items"]] == ["No audit log"]
    assert {i["title"] for i in svc.browse(gaps, include_closed=True)["items"]} == {"No SSO", "No audit log"}
    assert len(svc.browse(gaps, sort=Sort.TITLE)["items"]) == 1


def test_hc127_a_retired_content_idea_leaves_the_list_unless_asked_for(env: Any) -> None:
    svc, repo, _ = env
    repo.create(Node(id="", type=NodeType.CONTENT_IDEA, title="Launch recap", attributes={"status": "retired"}), "u")
    repo.create(Node(id="", type=NodeType.CONTENT_IDEA, title="How to file", attributes={"status": "open"}), "u")
    ideas = InsightKind.parse("content")
    assert [i["title"] for i in svc.browse(ideas)["items"]] == ["How to file"]
    assert len(svc.browse(ideas, include_closed=True)["items"]) == 2


def test_attr_query_filters_search_results_too() -> None:
    from insights_radar.insights.service import AttrQuery

    q = AttrQuery({"urgency": frozenset({"plan"})})
    keep = Node(id="signal/a", type=NodeType.SIGNAL, title="a", attributes={"urgency": "plan"})
    drop = Node(id="signal/b", type=NodeType.SIGNAL, title="b", attributes={"urgency": "watch"})
    assert q.matches(keep) and not q.matches(drop)
    assert q.order([drop, keep]) == sorted([drop, keep], key=lambda n: (n.created_at, n.id), reverse=True)
