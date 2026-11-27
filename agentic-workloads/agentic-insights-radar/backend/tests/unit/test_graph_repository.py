# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ConflictError, NotFoundError, ValidationError
from insights_radar.graph.model import Node, node_type_of
from insights_radar.graph.registry import EDGE_RULES, EdgeType, NodeType, Registry
from insights_radar.graph.repository import MERGE_LOCK_SECONDS, GraphRepository, decode_cursor, encode_cursor

URL = "https://news.example.com/motors"


@pytest.fixture
def repo(graph_table: Any, clock: FixedClock) -> GraphRepository:
    return GraphRepository(graph_table, clock)


def entity(t: NodeType, title: str, **kw: Any) -> Node:
    return Node(id="", type=t, title=title, **kw)


def evidence(t: NodeType = NodeType.FACT, title: str = "Vendor X ships long-life motor", **kw: Any) -> Node:
    kw.setdefault("evidence_url", URL)
    kw.setdefault("id", "")
    return Node(type=t, title=title, **kw)


# ---- entities ----------------------------------------------------------------------


def test_upsert_entity_creates_then_dedupes(repo: GraphRepository) -> None:
    a, created = repo.upsert_entity(entity(NodeType.ORGANIZATION, "Acme Motors"), "module:scout")
    assert created and a.id.startswith("organization/") and a.created_by == "module:scout"
    b, created2 = repo.upsert_entity(
        entity(NodeType.ORGANIZATION, "acme-motors", summary="Motor vendor", aliases=("ACME",)), "module:scout"
    )
    assert not created2 and b.id == a.id
    assert b.summary == "Motor vendor" and b.aliases == ("ACME",) and b.version == 2
    c, created3 = repo.upsert_entity(entity(NodeType.ORGANIZATION, "ACME_MOTORS", summary="ignored"), "x")
    assert not created3 and c.summary == "Motor vendor" and c.version == 2
    assert repo.find_entity(NodeType.ORGANIZATION, "Acme   Motors") == c


def test_same_name_different_type_is_distinct(repo: GraphRepository) -> None:
    org, _ = repo.upsert_entity(entity(NodeType.ORGANIZATION, "Nova"), "u")
    prod, _ = repo.upsert_entity(entity(NodeType.PRODUCT, "Nova"), "u")
    assert org.id != prod.id


def test_upsert_merges_new_attributes_only(repo: GraphRepository) -> None:
    repo.upsert_entity(entity(NodeType.ORGANIZATION, "Acme", attributes={"org_kind": "vendor"}), "u")
    node, _ = repo.upsert_entity(
        entity(NodeType.ORGANIZATION, "Acme", attributes={"org_kind": "competitor", "country": "DE"}), "u"
    )
    assert node.attributes == {"org_kind": "vendor", "country": "DE"}


def test_upsert_rejects_non_entity(repo: GraphRepository) -> None:
    with pytest.raises(ValidationError):
        repo.upsert_entity(evidence(), "u")


def test_create_rejects_entity(repo: GraphRepository) -> None:
    with pytest.raises(ValidationError):
        repo.create(entity(NodeType.TOPIC, "USB-C power"), "u")


# ---- evidence/outputs --------------------------------------------------------------


def test_create_evidence_requires_public_url(repo: GraphRepository) -> None:
    with pytest.raises(ValueError, match="evidence_url"):
        repo.create(evidence(evidence_url=None), "u")
    with pytest.raises(ValueError, match="evidence_url"):
        repo.create(evidence(evidence_url="http://10.0.0.1/x"), "u")
    node = repo.create(evidence(description="<p>Rated <script>x</script>50k cycles</p>"), "u")
    assert node.description == "<p>Rated 50k cycles</p>" and repo.get(node.id) == node


def test_create_duplicate_id_conflicts(repo: GraphRepository) -> None:
    n = repo.create(evidence(), "u")
    with pytest.raises(ConflictError):
        repo.create(evidence(id=n.id), "u")


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"title": ""}, "title is required"),
        ({"title": "x" * 201}, "title must be"),
        ({"summary": "s" * 2001}, "summary"),
        ({"confidence": 1.5}, "confidence"),
        ({"insight_kind": "foo"}, "insight_kind is only valid"),
    ],
)
def test_output_validation(repo: GraphRepository, kw: dict[str, Any], msg: str) -> None:
    base: dict[str, Any] = {"id": "", "type": NodeType.SIGNAL, "title": "Bottle holders gain traction"}
    base.update(kw)
    with pytest.raises(ValueError, match=msg):
        repo.create(Node(**base), "u")


@pytest.mark.parametrize("kind", [None, "", "Bad Kind", "1abc", "a"])
def test_insight_requires_snake_kind(repo: GraphRepository, kind: str | None) -> None:
    with pytest.raises(ValueError, match="insight_kind"):
        repo.create(Node(id="", type=NodeType.INSIGHT, title="x", insight_kind=kind), "u")


def test_attribute_validation(repo: GraphRepository) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        repo.create(Node(id="", type=NodeType.GAP, title="g", attributes={"Bad Key": 1}), "u")
    with pytest.raises(ValueError, match="at most"):
        repo.create(Node(id="", type=NodeType.GAP, title="g", attributes={f"k{i}": i for i in range(31)}), "u")
    with pytest.raises(ValueError, match="unsupported"):
        repo.create(Node(id="", type=NodeType.GAP, title="g", attributes={"k": {"x": 1}}), "u")
    with pytest.raises(ValueError, match="too long"):
        repo.create(Node(id="", type=NodeType.GAP, title="g", attributes={"k": "x" * 5000}), "u")
    with pytest.raises(ValueError, match="list is too large"):
        repo.create(Node(id="", type=NodeType.GAP, title="g", attributes={"k": ["x"] * 51}), "u")
    ok = repo.create(
        Node(
            id="",
            type=NodeType.GAP,
            title="g",
            attributes={"a": 1, "b": 2.5, "c": True, "d": ["x"], "e": "s", "f": None},
        ),
        "u",
    )
    assert repo.get(ok.id).attributes == {"a": 1, "b": 2.5, "c": True, "d": ["x"], "e": "s"}


def test_update_with_version_and_conflict(repo: GraphRepository, clock: FixedClock) -> None:
    n = repo.create(Node(id="", type=NodeType.SIGNAL, title="s", confidence=0.4), "u")
    clock.advance(timedelta(seconds=5))
    u = repo.update(n.id, {"summary": "now", "confidence": 0.8}, 1, "u")
    assert u.version == 2 and u.summary == "now" and u.updated_at > n.updated_at
    with pytest.raises(ConflictError):
        repo.update(n.id, {"summary": "stale"}, 1, "u")
    with pytest.raises(ValidationError, match="cannot be updated"):
        repo.update(n.id, {"type": "trend"}, 2, "u")


def test_entity_rename_guard(repo: GraphRepository) -> None:
    n, _ = repo.upsert_entity(entity(NodeType.PRODUCT, "Desk Pro"), "u")
    assert repo.update(n.id, {"title": "desk-pro"}, 1, "u").title == "desk-pro"
    with pytest.raises(ValidationError, match="renamed"):
        repo.update(n.id, {"title": "Desk Max"}, 2, "u")


def test_get_missing_and_malformed(repo: GraphRepository) -> None:
    with pytest.raises(NotFoundError):
        repo.get("fact/00000000-0000-7000-8000-000000000000")
    with pytest.raises(ValueError):
        repo.get("nonsense")
    with pytest.raises(ValueError):
        repo.get("planet/x")


def test_get_many(repo: GraphRepository) -> None:
    ids = [repo.create(evidence(title=f"f{i}"), "u").id for i in range(130)]
    got = repo.get_many([*ids, ids[0]])
    assert set(got) == set(ids)
    assert repo.get_many([]) == {}


# ---- listing -----------------------------------------------------------------------


def test_recent_newest_first_with_cursor(repo: GraphRepository, clock: FixedClock) -> None:
    made = []
    for i in range(7):
        made.append(repo.create(Node(id="", type=NodeType.SIGNAL, title=f"s{i}"), "u"))
        clock.advance(timedelta(seconds=1))
    p1 = repo.recent(NodeType.SIGNAL, limit=3)
    assert [n.title for n in p1.items] == ["s6", "s5", "s4"] and p1.cursor
    p2 = repo.recent(NodeType.SIGNAL, limit=3, cursor=p1.cursor)
    assert [n.title for n in p2.items] == ["s3", "s2", "s1"]
    after = repo.recent(NodeType.SIGNAL, after=made[4].updated_at, oldest_first=True)
    assert [n.title for n in after.items] == ["s5", "s6"]
    assert len(list(repo.iterate(NodeType.SIGNAL))) == 7
    assert len(list(repo.iterate(NodeType.SIGNAL, cap=2))) == 2


def test_insight_listing_by_kind(repo: GraphRepository) -> None:
    repo.create(Node(id="", type=NodeType.INSIGHT, title="a", insight_kind="competitor_move"), "u")
    repo.create(Node(id="", type=NodeType.INSIGHT, title="b", insight_kind="co_marketing"), "u")
    assert [n.title for n in repo.recent(NodeType.INSIGHT, insight_kind="competitor_move").items] == ["a"]
    with pytest.raises(ValidationError):
        repo.recent(NodeType.INSIGHT)


@pytest.mark.parametrize("cursor", ["!!!", "bm90LWpzb24=", "WzEsMl0="])
def test_bad_cursor(repo: GraphRepository, cursor: str) -> None:
    with pytest.raises(ValidationError):
        repo.recent(NodeType.FACT, cursor=cursor)


def test_cursor_roundtrip() -> None:
    assert encode_cursor(None) is None and decode_cursor(None) is None
    key = {"pk": "N#x", "sk": "META"}
    assert decode_cursor(encode_cursor(key)) == key


# ---- edges -------------------------------------------------------------------------


def _seed(repo: GraphRepository) -> tuple[Node, Node, Node]:
    org, _ = repo.upsert_entity(entity(NodeType.ORGANIZATION, "Acme Motors"), "u")
    fact = repo.create(evidence(), "u")
    sig = repo.create(Node(id="", type=NodeType.SIGNAL, title="Long-life motors"), "u")
    return org, fact, sig


def test_link_validates_and_counts_degree(repo: GraphRepository) -> None:
    org, fact, sig = _seed(repo)
    e, created = repo.link(fact.id, EdgeType.REPORTED_BY, org.id, "u", confidence=0.9, run_id="r1")
    assert created and e.confidence == 0.9
    _, again = repo.link(fact.id, EdgeType.REPORTED_BY, org.id, "u")
    assert not again
    repo.link(sig.id, EdgeType.SUPPORTS, fact.id, "u")
    assert repo.get(fact.id).degree == 2 and repo.get(org.id).in_degree == 1 and repo.get(sig.id).out_degree == 1
    with pytest.raises(ValidationError, match="cannot connect"):
        repo.link(org.id, EdgeType.SUPPORTS, fact.id, "u")
    with pytest.raises(ValidationError, match="itself"):
        repo.link(sig.id, EdgeType.SUPPORTS, sig.id, "u")
    with pytest.raises(ValidationError, match="confidence"):
        repo.link(sig.id, EdgeType.ABOUT, org.id, "u", confidence=2.0)


def test_link_missing_node(repo: GraphRepository) -> None:
    org, _, _ = _seed(repo)
    with pytest.raises(NotFoundError):
        repo.link("fact/00000000-0000-7000-8000-000000000000", EdgeType.REPORTED_BY, org.id, "u")


def test_edges_directions_and_unlink(repo: GraphRepository) -> None:
    org, fact, sig = _seed(repo)
    repo.link(fact.id, EdgeType.REPORTED_BY, org.id, "u")
    repo.link(sig.id, EdgeType.SUPPORTS, fact.id, "u")
    assert {e.type for e in repo.edges(fact.id)} == {EdgeType.REPORTED_BY, EdgeType.SUPPORTS}
    assert [e.type for e in repo.edges(fact.id, direction="out")] == [EdgeType.REPORTED_BY]
    assert [e.type for e in repo.edges(fact.id, direction="in", edge=EdgeType.SUPPORTS)] == [EdgeType.SUPPORTS]
    with pytest.raises(ValidationError):
        repo.edges(fact.id, direction="sideways")
    assert repo.unlink(sig.id, EdgeType.SUPPORTS, fact.id)
    assert not repo.unlink(sig.id, EdgeType.SUPPORTS, fact.id)
    assert repo.get(fact.id).in_degree == 0


def test_neighborhood_depth_and_filters(repo: GraphRepository) -> None:
    org, fact, sig = _seed(repo)
    trend = repo.create(Node(id="", type=NodeType.TREND, title="Durability"), "u")
    repo.link(fact.id, EdgeType.REPORTED_BY, org.id, "u")
    repo.link(sig.id, EdgeType.SUPPORTS, fact.id, "u")
    repo.link(trend.id, EdgeType.SUPPORTS, sig.id, "u")
    one = repo.neighborhood(sig.id)
    assert {n.id for n in one.nodes} == {sig.id, fact.id, trend.id} and len(one.edges) == 2
    two = repo.neighborhood(sig.id, depth=2)
    assert org.id in {n.id for n in two.nodes}
    only = repo.neighborhood(sig.id, depth=2, node_types=[NodeType.FACT])
    assert {n.id for n in only.nodes} == {sig.id, fact.id}
    by_edge = repo.neighborhood(sig.id, edge_types=[EdgeType.SUPPORTS], depth=3)
    assert org.id not in {n.id for n in by_edge.nodes}
    capped = repo.neighborhood(sig.id, depth=2, max_nodes=2)
    assert capped.truncated and len(capped.nodes) <= 2
    for bad in (0, 4):
        with pytest.raises(ValidationError):
            repo.neighborhood(sig.id, depth=bad)


def test_neighbour_pages_walk_every_direct_neighbour_once(repo: GraphRepository) -> None:
    hub = repo.create(Node(id="", type=NodeType.SIGNAL, title="Hub"), "u")
    leaves = [repo.create(Node(id="", type=NodeType.TREND, title=f"Trend {i}"), "u") for i in range(7)]
    for leaf in leaves:
        repo.link(leaf.id, EdgeType.SUPPORTS, hub.id, "u")
    seen: list[str] = []
    offset = 0
    while True:
        page = repo.neighborhood(hub.id, max_nodes=4, offset=offset)
        assert page.total == 7 and page.offset == offset and page.nodes[0].id == hub.id
        ids = [n.id for n in page.nodes[1:]]
        assert len(ids) <= 3 and len(page.edges) == len(ids)
        seen.extend(ids)
        if not page.truncated:
            break
        offset += len(ids)
    assert sorted(seen) == sorted(leaf.id for leaf in leaves)
    hidden = repo.neighborhood(hub.id, offset=0, blocked=lambda n: n.id == leaves[1].id)
    # A hidden neighbour is neither shown nor counted (security review r7).
    assert leaves[1].id not in {n.id for n in hidden.nodes} and hidden.total == 6
    beyond = repo.neighborhood(hub.id, offset=99)
    assert [n.id for n in beyond.nodes] == [hub.id] and not beyond.truncated
    assert repo.neighborhood(hub.id, offset=0, node_types=[NodeType.FACT]).total == 0
    assert repo.neighborhood(hub.id, offset=0, edge_types=[EdgeType.ABOUT]).total == 0
    for bad in ({"depth": 2, "offset": 0}, {"offset": -1}):
        with pytest.raises(ValidationError):
            repo.neighborhood(hub.id, **bad)  # type: ignore[arg-type]


def test_delete_removes_edges_and_key(repo: GraphRepository) -> None:
    org, fact, sig = _seed(repo)
    repo.link(fact.id, EdgeType.REPORTED_BY, org.id, "u")
    repo.link(sig.id, EdgeType.ABOUT, org.id, "u")
    assert repo.delete(org.id) == 2
    with pytest.raises(NotFoundError):
        repo.get(org.id)
    assert repo.find_entity(NodeType.ORGANIZATION, "Acme Motors") is None
    assert repo.get(fact.id).out_degree == 0 and repo.edges(sig.id) == []
    again, created = repo.upsert_entity(entity(NodeType.ORGANIZATION, "Acme Motors"), "u")
    assert created and again.id != org.id


# ---- registry ----------------------------------------------------------------------


@pytest.mark.parametrize("edge", list(EdgeType))
def test_every_edge_has_a_rule_and_allows_some_pair(edge: EdgeType) -> None:
    rule = EDGE_RULES[edge]
    Registry.check_edge(edge, next(iter(rule.sources)), next(iter(rule.targets)))


@pytest.mark.parametrize("edge", list(EdgeType))
@pytest.mark.parametrize("src", list(NodeType))
@pytest.mark.parametrize("dst", list(NodeType))
def test_edge_matrix(edge: EdgeType, src: NodeType, dst: NodeType) -> None:
    allowed = src in EDGE_RULES[edge].sources and dst in EDGE_RULES[edge].targets
    if allowed:
        Registry.check_edge(edge, src, dst)
    else:
        with pytest.raises(ValueError):
            Registry.check_edge(edge, src, dst)


def test_registry_lookups_and_describe() -> None:
    assert Registry.node_type("fact") is NodeType.FACT
    assert Registry.edge_type("about") is EdgeType.ABOUT
    with pytest.raises(ValueError, match="unknown node type"):
        Registry.node_type("planet")
    with pytest.raises(ValueError, match="unknown edge type"):
        Registry.edge_type("loves")
    desc = Registry.describe()
    assert set(desc["node_types"]) == {t.value for t in NodeType}  # type: ignore[arg-type]
    assert node_type_of("signal/abc") is NodeType.SIGNAL
    with pytest.raises(ValueError):
        node_type_of("signal/")


def test_public_shapes(repo: GraphRepository) -> None:
    org, fact, sig = _seed(repo)
    e, _ = repo.link(fact.id, EdgeType.REPORTED_BY, org.id, "u", confidence=0.5)
    body = repo.get(fact.id).to_public()
    assert body["evidence_url"] == URL and body["category"] == "evidence" and body["degree"] == 1
    assert e.to_public() == {
        "from": fact.id,
        "type": "reported_by",
        "to": org.id,
        "created_by": "u",
        "created_at": e.created_at,
        "confidence": 0.5,
    }
    assert "evidence_url" not in repo.get(sig.id).to_public()
    assert "Acme Motors" in org.search_text


def test_update_keeps_concurrent_edge_counts_and_clears_removed_fields(graph_table: Any, clock: FixedClock) -> None:
    repo = GraphRepository(graph_table, clock)
    a = repo.create(Node(id="", type=NodeType.SIGNAL, title="s", summary="has a summary", confidence=0.8), "u")
    b = repo.create(Node(id="", type=NodeType.FACT, title="f", evidence_url=URL), "u")
    stale = repo.get(a.id)
    repo.link(a.id, EdgeType.SUPPORTS, b.id, "u")  # counts change; the version does not
    repo.update(a.id, {"summary": "", "confidence": None}, stale.version, "u")
    fresh = repo.get(a.id)
    assert fresh.out_degree == 1 and fresh.summary == "" and fresh.confidence is None


def test_delete_removes_every_edge_beyond_one_page(graph_table: Any, clock: FixedClock) -> None:
    repo = GraphRepository(graph_table, clock)
    hub, _ = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Hub"), "u")
    facts = [repo.create(Node(id="", type=NodeType.FACT, title=f"f{i}", evidence_url=URL), "u") for i in range(5)]
    for f in facts:
        repo.link(f.id, EdgeType.ABOUT, hub.id, "u")
    real = repo._table.query
    pages: list[int] = []

    def small_pages(**kwargs: Any) -> Any:  # force pagination with 2 items per page
        res = real(**{**kwargs, "Limit": min(2, kwargs.get("Limit", 2))})
        pages.append(len(res.get("Items", [])))
        return res

    repo._table.query = small_pages  # type: ignore[method-assign]
    assert repo.delete(hub.id) == 5 and len(pages) >= 3
    repo._table.query = real  # type: ignore[method-assign]
    assert all(repo.get(f.id).out_degree == 0 for f in facts)


def test_create_once_reports_a_node_id_collision(graph_table: Any, clock: FixedClock) -> None:
    """r2 B2-077: the dedupe key is free but the node id is taken, so neither write lands; the caller
    gets a retryable 409 rather than a second node."""
    repo = GraphRepository(graph_table, clock)
    draft = Node(id="", type=NodeType.FACT, title="F", evidence_url="https://a.example.com/x", module="scout")
    first, created = repo.create_once(draft, "module:scout", "key-one")
    assert created
    with pytest.raises(ConflictError, match="being created concurrently"):
        repo.create_once(replace(draft, id=first.id), "module:scout", "key-two")
    # Any other DynamoDB error is raised as it is.
    from botocore.exceptions import ClientError

    def throttled(**kwargs: Any) -> Any:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "TransactWriteItems")

    repo._client.transact_write_items = throttled
    with pytest.raises(ClientError):
        repo.create_once(draft, "module:scout", "key-three")


# ---- CR2-02: the list index is keyed on updated_at ---------------------------------------------


def test_cr2_02_the_list_index_follows_material_updates_only(repo: GraphRepository, clock: FixedClock) -> None:
    a = repo.create(Node(id="", type=NodeType.TREND, title="a", summary="one two"), "u")
    clock.advance(timedelta(seconds=5))
    b = repo.create(Node(id="", type=NodeType.TREND, title="b"), "u")
    clock.advance(timedelta(seconds=5))
    same = repo.update(a.id, {"summary": " one  two "}, 1, "u")
    assert same.updated_at == a.updated_at and same.version == 2
    assert [n.id for n in repo.recent(NodeType.TREND, after=b.updated_at).items] == []
    moved = repo.update(a.id, {"summary": "one two three", "confidence": 0.5}, 2, "u")
    assert moved.updated_at > b.updated_at
    assert [n.id for n in repo.recent(NodeType.TREND, after=b.updated_at).items] == [a.id]


def test_cr2_02_rekey_moves_nodes_written_under_the_created_at_key(
    repo: GraphRepository, graph_table: Any, clock: FixedClock
) -> None:
    nodes = [repo.create(Node(id="", type=NodeType.TREND, title=f"t{i}"), "u") for i in range(3)]
    clock.advance(timedelta(seconds=5))
    for n in nodes:  # as an r2 build wrote them: updated later, keyed on created_at
        graph_table.update_item(
            Key={"pk": f"N#{n.id}", "sk": "META"},
            UpdateExpression="SET updated_at = :u, gsi1sk = :k",
            ExpressionAttributeValues={":u": "2099-01-01T00:00:00Z", ":k": f"{n.created_at}#{n.id}"},
        )
    assert repo.recent(NodeType.TREND, after="2098-01-01T00:00:00Z").items == []
    assert repo.rekey_list_index(page_size=2) == 3
    after = repo.recent(NodeType.TREND, after="2098-01-01T00:00:00Z").items
    assert {n.id for n in after} == {n.id for n in nodes}
    assert repo.rekey_list_index() == 0  # idempotent


def test_p3003_rekey_repairs_what_older_code_wrote_after_a_rollback(repo: GraphRepository, graph_table: Any) -> None:
    """The re-key keeps no "done" marker: every seed looks at the stored keys. So a node that the
    previous release wrote under the old key while an upgrade was rolled back (r3 P3-003) is
    re-keyed by the next successful upgrade, not skipped because an earlier attempt ran."""
    node = repo.create(Node(id="", type=NodeType.TREND, title="t"), "u")
    assert repo.rekey_list_index() == 0
    graph_table.update_item(  # the rolled-back code writes the created_at key again
        Key={"pk": f"N#{node.id}", "sk": "META"},
        UpdateExpression="SET updated_at = :u, gsi1sk = :k",
        ExpressionAttributeValues={":u": "2099-01-01T00:00:00Z", ":k": f"{node.created_at}#{node.id}"},
    )
    assert repo.rekey_list_index() == 1
    assert [n.id for n in repo.recent(NodeType.TREND, after="2098-01-01T00:00:00Z").items] == [node.id]


def test_cr2_02_rekey_skips_a_node_written_meanwhile_and_raises_other_errors(
    repo: GraphRepository, graph_table: Any
) -> None:
    from botocore.exceptions import ClientError

    node = repo.create(Node(id="", type=NodeType.TREND, title="t"), "u")
    graph_table.update_item(
        Key={"pk": f"N#{node.id}", "sk": "META"},
        UpdateExpression="SET gsi1sk = :k",
        ExpressionAttributeValues={":k": "old"},
    )

    class Racing:
        def __init__(self, code: str) -> None:
            self.code = code
            self.name = graph_table.name
            self.meta = graph_table.meta

        def scan(self, **kwargs: Any) -> Any:
            return graph_table.scan(**kwargs)

        def update_item(self, **kwargs: Any) -> Any:
            raise ClientError({"Error": {"Code": self.code, "Message": "x"}}, "UpdateItem")

    assert GraphRepository(Racing("ConditionalCheckFailedException")).rekey_list_index() == 0  # type: ignore[arg-type]
    with pytest.raises(ClientError):
        GraphRepository(Racing("ProvisionedThroughputExceededException")).rekey_list_index()  # type: ignore[arg-type]


def test_hc118_another_spelling_reuses_the_entity(repo: GraphRepository) -> None:
    mcp, _ = repo.upsert_entity(entity(NodeType.TOPIC, "Model Context Protocol"), "module:scout")
    again, created = repo.upsert_entity(entity(NodeType.TOPIC, "Model Context Protocol (MCP)"), "module:scout")
    assert again.id == mcp.id and not created and "Model Context Protocol (MCP)" in again.aliases
    strands, _ = repo.upsert_entity(entity(NodeType.PRODUCT, "Strands Agents"), "module:scout")
    sdk, created = repo.upsert_entity(entity(NodeType.PRODUCT, "Strands Agents SDK"), "module:scout")
    assert sdk.id == strands.id and not created
    # A kind word is kept when only one word would be left, and different names stay apart.
    lam, _ = repo.upsert_entity(entity(NodeType.PRODUCT, "Lambda"), "module:scout")
    lam_sdk, created = repo.upsert_entity(entity(NodeType.PRODUCT, "Lambda SDK"), "module:scout")
    assert created and lam_sdk.id != lam.id
    aws, created = repo.upsert_entity(entity(NodeType.PRODUCT, "AWS Lambda"), "module:scout")
    assert created and aws.id != lam.id


def test_hc118_spellings_match_both_ways(repo: GraphRepository) -> None:
    sdk, _ = repo.upsert_entity(entity(NodeType.PRODUCT, "Strands Agents SDK"), "module:scout")
    bare, created = repo.upsert_entity(entity(NodeType.PRODUCT, "Strands Agents"), "module:scout")
    assert bare.id == sdk.id and not created and "Strands Agents" in bare.aliases
    vendor, created = repo.upsert_entity(entity(NodeType.PRODUCT, "AWS Strands Agents"), "module:signal")
    assert vendor.id == sdk.id and not created
    # A vendor word is dropped too, and the other way round.
    agentcore, _ = repo.upsert_entity(entity(NodeType.PRODUCT, "Amazon Bedrock AgentCore"), "module:scout")
    short, created = repo.upsert_entity(entity(NodeType.PRODUCT, "Bedrock AgentCore"), "module:scout")
    assert short.id == agentcore.id and not created
    # Distinct names that share words stay apart.
    bedrock, created = repo.upsert_entity(entity(NodeType.PRODUCT, "Bedrock"), "module:scout")
    assert created and bedrock.id != agentcore.id
    awsome, created = repo.upsert_entity(entity(NodeType.ORGANIZATION, "AWSome Awards"), "module:scout")
    some, created_some = repo.upsert_entity(entity(NodeType.ORGANIZATION, "Awards"), "module:scout")
    assert created and created_some and some.id != awsome.id


def test_hc118_a_name_key_left_without_its_node_is_skipped(repo: GraphRepository, graph_table: Any) -> None:
    graph_table.put_item(Item={"pk": "K#product#strandsagents", "sk": "KEY", "node_id": "product/gone", "kind": "key"})
    node, created = repo.upsert_entity(entity(NodeType.PRODUCT, "Strands Agents SDK"), "module:scout")
    assert created and node.title == "Strands Agents SDK"


def test_hc118_name_variants() -> None:
    from insights_radar.graph.repository import _name_variants

    assert "Strands Agents" in _name_variants("Strands Agents SDK")
    assert "AWS Strands Agents" in _name_variants("Strands Agents SDK")
    assert "Strands Agents SDK" in _name_variants("AWS Strands Agents")
    assert "Model Context Protocol" in _name_variants("Model Context Protocol (MCP)")
    assert _name_variants("Lambda") == []
    assert "Lambda" not in _name_variants("AWS Lambda") and "Lambda SDK" not in _name_variants("Lambda")
    assert all(v != "Bedrock" for v in _name_variants("Amazon Bedrock AgentCore"))
    assert _name_variants("(MCP)") == []


def test_hcb043_a_topic_named_like_a_product_is_the_product(repo: GraphRepository) -> None:
    product, _ = repo.upsert_entity(entity(NodeType.PRODUCT, "Platform Engineering"), "module:scout")
    topic, created = repo.upsert_entity(entity(NodeType.TOPIC, "Platform Engineering"), "module:scout")
    assert topic.id == product.id and topic.type is NodeType.PRODUCT and not created
    # A company and its same-named product stay two nodes.
    org, created = repo.upsert_entity(entity(NodeType.ORGANIZATION, "Platform Engineering"), "module:scout")
    assert created and org.id != product.id


def test_hc105_merge_entity_dry_run_then_apply(repo: GraphRepository) -> None:
    keep = repo.upsert_entity(entity(NodeType.PRODUCT, "Strands Agents"), "u")[0]
    dup = repo.upsert_entity(entity(NodeType.PRODUCT, "Strands SDK", aliases=("strands-sdk",)), "u")[0]
    dup = repo.update(dup.id, {"summary": "An agent SDK"}, dup.version, "u")
    org = repo.upsert_entity(entity(NodeType.ORGANIZATION, "Acme"), "u")[0]
    fact = repo.create(evidence(), "module:scout")
    trend = repo.create(
        Node(
            id="",
            type=NodeType.TREND,
            title="Agents",
            description=f'<p>See <a href="/insights/nodes/{dup.id}">it</a></p>',
        ),
        "module:trend",
    )
    repo.link(fact.id, EdgeType.ABOUT, dup.id, "module:scout", confidence=0.7, run_id="r1")
    repo.link(trend.id, EdgeType.ABOUT, dup.id, "module:trend")
    repo.link(org.id, EdgeType.OFFERS, dup.id, "u")
    repo.link(dup.id, EdgeType.COMPETES_WITH, keep.id, "u")  # would become a self-loop
    dry = repo.merge_entity(dup.id, keep.id, "admin@example.com")
    assert dry["applied"] is False and dry["edges"] == 4 and dry["moved"] == 3 and len(dry["dropped"]) == 1
    assert dry["aliases_added"] == ["Strands SDK", "strands-sdk"] and dry["descriptions_rewritten"] == [trend.id]
    assert repo.get(dup.id).id == dup.id  # nothing changed
    done = repo.merge_entity(dup.id, keep.id, "admin@example.com", apply=True)
    assert done["applied"] is True
    kept = repo.get(keep.id)
    assert kept.summary == "An agent SDK" and {"Strands SDK", "strands-sdk"} <= set(kept.aliases)
    moved = {(e.source, e.type) for e in repo.edges(keep.id, limit=None)}
    assert moved == {(fact.id, EdgeType.ABOUT), (trend.id, EdgeType.ABOUT), (org.id, EdgeType.OFFERS)}
    about = next(e for e in repo.edges(keep.id, limit=None) if e.source == fact.id)
    assert about.confidence == 0.7 and about.run_id == "r1" and about.created_by == "module:scout"
    assert keep.id in repo.get(trend.id).description and dup.id not in repo.get(trend.id).description
    with pytest.raises(NotFoundError):
        repo.get(dup.id)
    # The duplicate's name now lands on the kept entity.
    again, created = repo.upsert_entity(entity(NodeType.PRODUCT, "Strands SDK"), "module:scout")
    assert again.id == keep.id and not created


def test_hc105_merge_entity_refusals(repo: GraphRepository) -> None:
    prod = repo.upsert_entity(entity(NodeType.PRODUCT, "Widget"), "u")[0]
    topic = repo.upsert_entity(entity(NodeType.TOPIC, "Widgets"), "u")[0]
    org = repo.upsert_entity(entity(NodeType.ORGANIZATION, "Widget Inc"), "u")[0]
    fact = repo.create(evidence(), "u")
    with pytest.raises(ValidationError, match="itself"):
        repo.merge_entity(prod.id, prod.id, "a")
    with pytest.raises(ValidationError, match="only entities"):
        repo.merge_entity(fact.id, prod.id, "a")
    with pytest.raises(ValidationError, match="cannot be merged into"):
        repo.merge_entity(org.id, prod.id, "a")
    # A topic and a product can be one thing; an edge the kept type cannot carry is dropped, not moved.
    repo.link(topic.id, EdgeType.PART_OF, prod.id, "u")
    org2 = repo.upsert_entity(entity(NodeType.ORGANIZATION, "Other"), "u")[0]
    repo.link(org2.id, EdgeType.OFFERS, prod.id, "u")
    out = repo.merge_entity(prod.id, topic.id, "a", apply=True)
    assert out["moved"] == 0 and {d["type"] for d in out["dropped"]} == {"part_of", "offers"}


def test_hc120_entity_hygiene_report(repo: GraphRepository) -> None:
    a, _ = repo.upsert_entity(entity(NodeType.PRODUCT, "Strands Agents"), "u")
    b, _ = repo.upsert_entity(entity(NodeType.TOPIC, "Agents", aliases=("Strands Agents SDK",)), "u")
    lonely, _ = repo.upsert_entity(entity(NodeType.PERSON, "Ada"), "u")
    fact = repo.create(evidence(), "u")
    repo.link(fact.id, EdgeType.ABOUT, a.id, "u")
    out = repo.entity_hygiene()
    assert out["scanned"] == 3 and out["duplicate_count"] == 1
    assert [n["id"] for n in out["duplicates"][0]] == [a.id, b.id]  # the linked one first, to keep
    assert {n["id"] for n in out["orphans"]} == {b.id, lonely.id} and out["orphan_count"] == 2


def test_r12_a_name_key_that_outlived_its_node_is_no_match(repo: Any) -> None:
    from insights_radar.graph.model import Node

    node, _ = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Ghost Co"), "u")
    repo._table.delete_item(Key={"pk": f"N#{node.id}", "sk": "META"})
    assert repo.find_entity(NodeType.ORGANIZATION, "Ghost Co") is None


def test_r57_a_chained_merge_keeps_every_name_on_the_kept_entity(repo: GraphRepository) -> None:
    # Code review r57: A merged into B, then B into C, left A's key on the deleted B, which a later upsert took back
    # as stale and so created A again as a new duplicate.
    a = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Alpha Ltd"), "u")[0]
    b = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Beta Corp"), "u")[0]
    c = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Gamma Inc"), "u")[0]
    repo.merge_entity(a.id, b.id, "u", apply=True)
    repo.merge_entity(b.id, c.id, "u", apply=True)
    for name in ("Alpha Ltd", "Beta Corp", "Gamma Inc"):
        assert repo.find_entity(NodeType.ORGANIZATION, name).id == c.id  # type: ignore[union-attr]
    again, created = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Alpha Ltd"), "u")
    assert not created and again.id == c.id


def test_r57_a_merge_never_moves_a_third_entitys_key(repo: GraphRepository) -> None:
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Dup Co", aliases=("Third Co",)), "u")[0]
    third = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Third Co"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Kept Co"), "u")[0]
    repo.merge_entity(dup.id, kept.id, "u", apply=True)
    assert repo.find_entity(NodeType.ORGANIZATION, "Third Co").id == third.id  # type: ignore[union-attr]
    assert repo.find_entity(NodeType.ORGANIZATION, "Dup Co").id == kept.id  # type: ignore[union-attr]


def test_r57_a_merge_surfaces_other_key_errors(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    other = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Throttled Co"), "u")[0]
    target = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Target Co"), "u")[0]
    real_put = repo._table.put_item

    def put(**kw: Any) -> Any:
        if kw.get("ConditionExpression"):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "PutItem")
        return real_put(**kw)

    monkeypatch.setattr(repo._table, "put_item", put)
    with pytest.raises(ClientError):
        repo.merge_entity(other.id, target.id, "u", apply=True)


def test_r58_an_alias_with_no_name_key_is_not_stored(repo: GraphRepository) -> None:
    # Security review r58: an alias such as "-" passed validation, then broke every merge of the entity for good.
    node = repo.upsert_entity(
        Node(id="", type=NodeType.ORGANIZATION, title="Dash Co", aliases=("-", "_ _", "Dash")), "u"
    )[0]
    assert repo.get(node.id).aliases == ("Dash",)
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Kept Dash"), "u")[0]
    repo.merge_entity(node.id, kept.id, "u", apply=True)
    assert repo.find_entity(NodeType.ORGANIZATION, "Dash Co").id == kept.id  # type: ignore[union-attr]


def test_r58_a_stored_alias_with_no_key_does_not_break_a_merge(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Old Co"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="New Co"), "u")[0]
    real_get = repo.get
    # An alias stored before aliases needed a key.
    monkeypatch.setattr(repo, "get", lambda i: replace(real_get(i), aliases=("-",)) if i == dup.id else real_get(i))
    repo.merge_entity(dup.id, kept.id, "u", apply=True)
    monkeypatch.undo()
    assert repo.find_entity(NodeType.ORGANIZATION, "Old Co").id == kept.id  # type: ignore[union-attr]


def test_r58_a_chained_merge_across_product_and_topic_keeps_the_name(repo: GraphRepository) -> None:
    # Security review r58: topic C into product D wrote K#topic#c -> D; D into E then looked only under K#product#c,
    # so the topic key was left on the deleted D and the next write of topic "C" created it again.
    c = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Cee Topic"), "u")[0]
    d = repo.upsert_entity(Node(id="", type=NodeType.PRODUCT, title="Dee Product"), "u")[0]
    e = repo.upsert_entity(Node(id="", type=NodeType.PRODUCT, title="Eee Product"), "u")[0]
    repo.merge_entity(c.id, d.id, "u", apply=True)
    repo.merge_entity(d.id, e.id, "u", apply=True)
    assert repo.find_entity(NodeType.TOPIC, "Cee Topic").id == e.id  # type: ignore[union-attr]
    again, created = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Cee Topic"), "u")
    assert not created and again.id == e.id


def test_r58_rekey_leaves_a_merge_redirect_alone(repo: GraphRepository) -> None:
    # Code review r58: rekey_names took a merged name's key for a stale key of the kept entity.
    a = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Alpha Ltd"), "u")[0]
    c = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Gamma Inc"), "u")[0]
    repo.merge_entity(a.id, c.id, "u", apply=True)
    assert repo.rekey_names() == (0, 0)
    assert repo.find_entity(NodeType.ORGANIZATION, "Alpha Ltd").id == c.id  # type: ignore[union-attr]


def test_r58_a_key_repointed_during_a_merge_is_left_alone(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Between the batch read and the write another merge can repoint a key: the conditional write fails and the
    # merge goes on.
    from botocore.exceptions import ClientError

    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Racy Co"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Calm Co"), "u")[0]
    real_put = repo._table.put_item

    def put(**kw: Any) -> Any:
        if kw.get("ConditionExpression"):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "PutItem")
        return real_put(**kw)

    monkeypatch.setattr(repo._table, "put_item", put)
    repo.merge_entity(dup.id, kept.id, "u", apply=True)
    monkeypatch.undo()
    with pytest.raises(NotFoundError):
        repo.get(dup.id)


def test_r59_a_same_title_cross_type_merge_keeps_the_name_through_a_chain(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r59: a topic merged into a product of the same name adds no alias, so the topic key it left on
    # the product was invisible to the next merge and to rekey_names. The two exist side by side from before the
    # product/topic variant lookup (HCB-043).
    topic = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Platform Engineering"), "u")[0]
    monkeypatch.setattr(repo, "_variant_entity", lambda node: None)
    product = repo.upsert_entity(Node(id="", type=NodeType.PRODUCT, title="Platform Engineering"), "u")[0]
    monkeypatch.undo()
    assert product.id != topic.id
    last = repo.upsert_entity(Node(id="", type=NodeType.PRODUCT, title="Zed Platform"), "u")[0]
    repo.merge_entity(topic.id, product.id, "u", apply=True)
    assert repo.rekey_names() == (0, 0)
    repo.merge_entity(product.id, last.id, "u", apply=True)
    assert repo.find_entity(NodeType.TOPIC, "Platform Engineering").id == last.id  # type: ignore[union-attr]


def test_r59_prune_can_keep_one_edge_type(repo: GraphRepository) -> None:
    a = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Hub Org"), "u")[0]
    for t in ("One Org", "Two Org"):
        b = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title=t), "u")[0]
        repo.link(a.id, EdgeType.PARTNERS_WITH, b.id, "u")
    c = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Some Topic"), "u")[0]
    repo.link(a.id, EdgeType.RELATED_TO, c.id, "u")
    assert repo.prune(a.id, 5, keep=EdgeType.PARTNERS_WITH) == 1
    assert {e.type for e in repo.edges(a.id)} == {EdgeType.PARTNERS_WITH}


def test_r60_put_while_exists_writes_only_while_the_node_is_there(repo: GraphRepository) -> None:
    node = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Here Topic"), "u")[0]
    item = {"pk": "MARK#x", "sk": "Y", "v": 1}
    repo.put_while_exists(node.id, repo._table.name, item)
    assert repo._table.get_item(Key={"pk": "MARK#x", "sk": "Y"})["Item"]["v"] == 1
    repo.delete(node.id)
    with pytest.raises(NotFoundError):
        repo.put_while_exists(node.id, repo._table.name, {**item, "v": 2})
    assert repo._table.get_item(Key={"pk": "MARK#x", "sk": "Y"})["Item"]["v"] == 1


def test_r60_put_while_exists_surfaces_other_errors(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    def boom(**_: Any) -> None:
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}}, "TransactWriteItems")

    monkeypatch.setattr(repo, "_transact", boom)
    with pytest.raises(ClientError):
        repo.put_while_exists("topic/x", repo._table.name, {"pk": "a", "sk": "b"})


def test_r61_a_node_being_deleted_takes_no_new_votes(repo: GraphRepository) -> None:
    # Code review r61: a vote written between the purge and the delete outlived the node with its email.
    node = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Going Topic"), "u")[0]
    repo.mark_deleting(node.id)
    with pytest.raises(NotFoundError):
        repo.put_while_exists(node.id, repo._table.name, {"pk": "MARK#y", "sk": "Z"})
    assert "Item" not in repo._table.get_item(Key={"pk": "MARK#y", "sk": "Z"})
    repo.delete(node.id)
    with pytest.raises(NotFoundError):
        repo.mark_deleting(node.id)


def test_r61_mark_deleting_surfaces_other_errors(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    def boom(**_: Any) -> None:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "UpdateItem")

    monkeypatch.setattr(repo._table, "update_item", boom)
    with pytest.raises(ClientError):
        repo.mark_deleting("topic/x")


def test_r62_no_link_onto_a_node_being_deleted(repo: GraphRepository) -> None:
    # Code review r62: a draft linked while its idea was being deleted lost its idea with the rest of the edges.
    a = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Linked A"), "u")[0]
    b = repo.upsert_entity(Node(id="", type=NodeType.TOPIC, title="Linked B"), "u")[0]
    repo.mark_deleting(b.id)
    with pytest.raises(NotFoundError):
        repo.link(a.id, EdgeType.RELATED_TO, b.id, "u")
    with pytest.raises(NotFoundError):
        repo.link(b.id, EdgeType.RELATED_TO, a.id, "u")
    assert repo.edges(a.id) == []


def test_r63_a_merge_drops_an_edge_to_a_node_being_deleted(repo: GraphRepository) -> None:
    # Code review r63: link() refuses a node being deleted, so a merge aborted part way on such a neighbour.
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Dup Org"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Kept Org"), "u")[0]
    going = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Going Org"), "u")[0]
    staying = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Staying Org"), "u")[0]
    for other in (going, staying):
        repo.link(dup.id, EdgeType.PARTNERS_WITH, other.id, "u")
    repo.mark_deleting(going.id)
    report = repo.merge_entity(dup.id, kept.id, "u", apply=True)
    assert report["moved"] == 1 and [d["to"] for d in report["dropped"]] == [going.id]
    assert [e.target for e in repo.edges(kept.id)] == [staying.id]
    with pytest.raises(NotFoundError):
        repo.get(dup.id)


def test_r64_no_merge_with_an_entity_being_deleted(repo: GraphRepository) -> None:
    # Security and code review r64: a merge into a queued entity dropped every edge and then deleted the duplicate.
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Dup Two"), "u")[0]
    into = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Into Two"), "u")[0]
    other = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Other Two"), "u")[0]
    repo.link(dup.id, EdgeType.PARTNERS_WITH, other.id, "u")
    repo.mark_deleting(into.id)
    with pytest.raises(ConflictError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    with pytest.raises(ConflictError):
        repo.merge_entity(into.id, dup.id, "u")
    assert [e.target for e in repo.edges(dup.id)] == [other.id]
    assert not repo.is_deleting(dup.id) and not repo.is_deleting("topic/gone")


def test_r64_a_kept_entity_flagged_mid_merge_stops_it(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Dup Three"), "u")[0]
    into = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Into Three"), "u")[0]
    other = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Other Three"), "u")[0]
    repo.link(dup.id, EdgeType.PARTNERS_WITH, other.id, "u")
    real_link = repo.link

    def link(*args: Any, **kw: Any) -> Any:
        repo.mark_deleting(into.id)  # flagged between the check and the first move
        return real_link(*args, **kw)

    monkeypatch.setattr(repo, "link", link)
    with pytest.raises(ConflictError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    monkeypatch.undo()
    assert repo.get(dup.id).id == dup.id  # not deleted
    assert [e.target for e in repo.edges(dup.id)] == [other.id]
    assert not repo.is_deleting(dup.id)  # released, so it can be merged elsewhere (code review r65)


def _three(repo: GraphRepository, tag: str) -> tuple[Node, Node, Node]:
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title=f"Dup {tag}"), "u")[0]
    into = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title=f"Into {tag}"), "u")[0]
    other = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title=f"Other {tag}"), "u")[0]
    repo.link(dup.id, EdgeType.PARTNERS_WITH, other.id, "u")
    return dup, into, other


def test_r65_two_merges_of_one_duplicate_cannot_both_start(repo: GraphRepository) -> None:
    # Code review r65: the check and the flag were separate steps, so two merges of one duplicate both applied.
    dup, into, other = _three(repo, "Race")
    repo._claim_merge(dup.id, other.id)  # a first merge holds the duplicate
    with pytest.raises(ConflictError):
        repo._claim_merge(dup.id, into.id)
    assert [e.target for e in repo.edges(dup.id)] == [other.id]


def test_r65_opposite_merges_leave_the_kept_entity_free(repo: GraphRepository) -> None:
    dup, into, _ = _three(repo, "Swap")
    repo._claim_merge(dup.id, into.id)  # A into B holds A
    with pytest.raises(ConflictError):
        repo._claim_merge(into.id, dup.id)  # B into A: A is flagged, so B is never flagged
    assert not repo.is_deleting(into.id)
    with pytest.raises(ConflictError):
        repo._claim_merge(dup.id, "organization/gone")


def test_r65_a_stopped_merge_releases_the_duplicate(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    # Code review r65: a merge that stopped after the flag could never be run again.
    dup, into, other = _three(repo, "Retry")

    def boom(*_: Any, **__: Any) -> Any:
        raise ConflictError("edited meanwhile")

    monkeypatch.setattr(repo, "_merge_into", boom)
    with pytest.raises(ConflictError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    monkeypatch.undo()
    assert not repo.is_deleting(dup.id)
    report = repo.merge_entity(dup.id, into.id, "u", apply=True)
    assert report["applied"] and report["moved"] == 1
    assert [e.target for e in repo.edges(into.id)] == [other.id]


def test_r66_a_delete_mid_merge_is_refused(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    # Code review r66: a delete took over a running merge, so both deleted the duplicate (and the kept entity could
    # go too). Now both ends are locked: a delete of either is a 409 to retry, and the merge carries on.
    dup, into, other = _three(repo, "Takeover")
    real = repo._merge_into
    refused: list[str] = []

    def during(*args: Any, **kw: Any) -> Any:
        for node_id in (dup.id, into.id):
            with pytest.raises(ConflictError):
                repo.mark_deleting(node_id)
            refused.append(node_id)
        assert not repo.delete_if_unlinked(repo.get(into.id))  # the kept entity has no link of its own
        return real(*args, **kw)

    monkeypatch.setattr(repo, "_merge_into", during)
    report = repo.merge_entity(dup.id, into.id, "u", apply=True)
    assert report["applied"] and refused == [dup.id, into.id]
    assert [e.target for e in repo.edges(into.id)] == [other.id]
    with pytest.raises(NotFoundError):
        repo.get(dup.id)
    repo.mark_deleting(into.id)  # released once the merge is done
    with pytest.raises(NotFoundError):
        repo.mark_deleting("organization/gone")


def test_r66_chained_merges_cannot_overlap(repo: GraphRepository) -> None:
    # Code review r66: X into A while A into B lost X's names.
    x, a, _ = _three(repo, "Chain")
    b = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Chain B"), "u")[0]
    repo._claim_merge(x.id, a.id)  # X into A holds A as its target
    with pytest.raises(ConflictError):
        repo._claim_merge(a.id, b.id)  # A cannot be claimed as a duplicate meanwhile
    with pytest.raises(ConflictError):
        repo._claim_merge(b.id, a.id)  # nor be another merge's target
    assert not repo.is_deleting(a.id) and not repo.is_deleting(b.id)


def test_r66_a_link_to_an_entity_mid_merge_stops_instead_of_dropping(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r66: an edge to an entity held by another merge was dropped as "being deleted".
    dup, into, other = _three(repo, "Held")
    elsewhere = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Held Elsewhere"), "u")[0]
    real_claim = repo._claim_merge

    def claim(duplicate_id: str, into_id: str) -> str:
        token = real_claim(duplicate_id, into_id)
        real_claim(other.id, elsewhere.id)  # the other end of the edge is claimed by a second merge
        return token

    monkeypatch.setattr(repo, "_claim_merge", claim)
    with pytest.raises(ConflictError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    monkeypatch.undo()
    assert [e.target for e in repo.edges(dup.id)] == [other.id]  # kept, not dropped
    assert not repo.is_deleting(dup.id) and repo.mark_deleting(into.id) is None  # both released


def test_r66_a_dead_merge_lock_lapses(repo: GraphRepository, clock: FixedClock) -> None:
    dup, into, other = _three(repo, "Lapse")
    repo._claim_merge(dup.id, into.id)  # a merge that died: nothing releases it
    with pytest.raises(ConflictError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    clock.advance(timedelta(seconds=MERGE_LOCK_SECONDS + 1))
    report = repo.merge_entity(dup.id, into.id, "u", apply=True)  # run again: it finishes
    assert report["applied"] and [e.target for e in repo.edges(into.id)] == [other.id]


def test_r66_a_merge_stopped_after_its_names_moved_keeps_the_duplicate_locked(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r66: released after its name pointed at the kept entity, the duplicate took writes again.
    dup, into, _ = _three(repo, "Late")

    def throttled(node_id: str) -> int:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchWriteItem")

    monkeypatch.setattr(repo, "delete", throttled)
    with pytest.raises(ClientError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    monkeypatch.undo()
    assert repo.is_deleting(dup.id)  # still locked, for the rerun that finishes it
    with pytest.raises(ConflictError) as raised:  # the kept entity waits for it too (code review r69)
        repo.mark_deleting(into.id)
    assert raised.value.details == {"from": [dup.id]}


def test_r66_a_hidden_duplicate_makes_the_kept_entity_private(repo: GraphRepository) -> None:
    # Security review r66: merging a hidden (legacy, unstamped) entity showed its names on a public one.
    dup, into, _ = _three(repo, "Hidden")
    repo.merge_entity(dup.id, into.id, "u", apply=True, private=True)
    assert repo.get(into.id).private


def test_r66_only_a_merges_own_locks_are_released(repo: GraphRepository) -> None:
    dup, into, _ = _three(repo, "Release")
    token = repo._claim_merge(dup.id, into.id)
    repo._release_merge(dup.id, into.id, "not-the-token")  # locks that are not this merge's stay
    assert repo.is_deleting(dup.id)
    with pytest.raises(ConflictError):
        repo.mark_deleting(into.id)
    repo._release_merge(dup.id, into.id, token)
    assert not repo.is_deleting(dup.id)
    repo.mark_deleting(into.id)


def test_r65_a_throttled_claim_is_not_a_conflict(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    dup, into, _ = _three(repo, "Throttled")

    from botocore.exceptions import ClientError

    def throttled(**_: Any) -> None:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "TransactWriteItems")

    monkeypatch.setattr(repo, "_transact", throttled)
    with pytest.raises(ClientError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    monkeypatch.undo()
    assert not repo.is_deleting(dup.id)


def test_r67_a_dead_merge_does_not_block_merges_of_its_neighbours(repo: GraphRepository, clock: FixedClock) -> None:
    # Code review r67: a lapsed merge claim on a linked entity made every later merge a 409, for good.
    x, y, _ = _three(repo, "Neighbour")
    dead = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Neighbour Dead"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Neighbour Kept"), "u")[0]
    repo.link(x.id, EdgeType.PARTNERS_WITH, dead.id, "u")
    repo._claim_merge(dead.id, kept.id)  # a merge of the neighbour that died
    with pytest.raises(ConflictError):
        repo.merge_entity(x.id, y.id, "u", apply=True)  # still live: wait for it
    clock.advance(timedelta(seconds=MERGE_LOCK_SECONDS + 1))
    report = repo.merge_entity(x.id, y.id, "u", apply=True)  # lapsed: the edge goes with the dying neighbour
    assert report["applied"] and [d["to"] for d in report["dropped"]] == [dead.id]


def test_r67_a_rerun_never_releases_a_duplicate_whose_names_moved(
    repo: GraphRepository, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r67: a rerun that stopped early cleared the lock of a duplicate an earlier run had redirected.
    dup, into, _ = _three(repo, "Rerun")

    def throttled(node_id: str) -> int:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchWriteItem")

    monkeypatch.setattr(repo, "delete", throttled)
    with pytest.raises(ClientError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    monkeypatch.undo()
    clock.advance(timedelta(seconds=MERGE_LOCK_SECONDS + 1))

    def conflict(*_: Any, **__: Any) -> Any:
        raise ConflictError("edited meanwhile")

    monkeypatch.setattr(repo, "_merge_into", conflict)
    with pytest.raises(ConflictError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    monkeypatch.undo()
    assert repo.is_deleting(dup.id)  # still locked
    clock.advance(timedelta(seconds=MERGE_LOCK_SECONDS + 1))
    assert repo.merge_entity(dup.id, into.id, "u", apply=True)["applied"]  # a later rerun finishes it
    with pytest.raises(NotFoundError):
        repo.get(dup.id)


def test_r67_a_lapsed_lock_does_not_stop_orphan_cleanup(repo: GraphRepository, clock: FixedClock) -> None:
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Orphan Dup"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Orphan Kept"), "u")[0]
    repo._claim_merge(dup.id, kept.id)  # a merge into an unlinked entity that died
    assert not repo.delete_if_unlinked(repo.get(kept.id))
    clock.advance(timedelta(seconds=MERGE_LOCK_SECONDS + 1))
    assert repo.delete_if_unlinked(repo.get(kept.id))


def _stuck(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch, tag: str) -> tuple[Node, Node, Node]:
    """A merge whose names moved and then died at the final delete."""
    dup, into, other = _three(repo, tag)

    def throttled(node_id: str) -> int:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchWriteItem")

    monkeypatch.setattr(repo, "delete", throttled)
    with pytest.raises(ClientError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    monkeypatch.undo()
    return dup, into, other


def test_r68_the_dry_run_plans_the_rerun_of_a_dead_merge(
    repo: GraphRepository, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r68: the UI's dry run refused a duplicate left by a dead merge, so it could never be finished.
    dup, into, _ = _stuck(repo, monkeypatch, "DryRun")
    # The failed run let go of its time (code review r69): the dry run, and the rerun, work at once.
    assert not repo.merge_entity(dup.id, into.id, "u")["applied"]
    assert repo.merge_entity(dup.id, into.id, "u", apply=True)["applied"]
    repo.mark_deleting(into.id)  # finished: the kept entity no longer waits for anything


def test_r68_a_half_merged_duplicate_finishes_only_into_its_own_target(
    repo: GraphRepository, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r68: a rerun could merge a duplicate whose names had moved into a different entity.
    dup, into, _ = _stuck(repo, monkeypatch, "Target")
    other_kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Target Elsewhere"), "u")[0]
    clock.advance(timedelta(seconds=MERGE_LOCK_SECONDS + 1))
    with pytest.raises(ConflictError):
        repo.merge_entity(dup.id, other_kept.id, "u")
    with pytest.raises(ConflictError):
        repo._claim_merge(dup.id, other_kept.id)
    assert repo.merge_entity(dup.id, into.id, "u", apply=True)["applied"]


def test_r68_a_half_merged_duplicate_cannot_be_deleted(
    repo: GraphRepository, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r68: a delete after the lapse left its alias keys pointing at a deleted entity.
    dup, into, _ = _stuck(repo, monkeypatch, "NoDelete")
    clock.advance(timedelta(seconds=MERGE_LOCK_SECONDS + 1))
    with pytest.raises(ConflictError) as raised:
        repo.mark_deleting(dup.id)
    assert raised.value.details == {"into": into.id}
    assert not repo.delete_if_unlinked(repo.get(dup.id))


def test_r68_a_lost_claim_before_the_names_move_is_a_conflict(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    dup, into, _ = _three(repo, "Lost")
    repo._claim_merge(dup.id, into.id)
    with pytest.raises(ConflictError):
        repo._mark_names_moved(dup.id, into.id, "not-the-token")

    def throttled(**_: Any) -> Any:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "TransactWriteItems")

    monkeypatch.setattr(repo, "_transact", throttled)
    with pytest.raises(ClientError):  # anything else is raised as it is
        repo._mark_names_moved(dup.id, into.id, "x")


def test_r69_a_kept_entity_waiting_on_a_half_merged_duplicate_stays(
    repo: GraphRepository, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r69: deleting or merging away the kept entity stranded the duplicate for good.
    dup, into, _ = _stuck(repo, monkeypatch, "Waits")
    elsewhere = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Waits Elsewhere"), "u")[0]
    clock.advance(timedelta(seconds=MERGE_LOCK_SECONDS + 1))
    with pytest.raises(ConflictError):
        repo.mark_deleting(into.id)
    assert not repo.delete_if_unlinked(repo.get(into.id))
    with pytest.raises(ConflictError) as planned:
        repo.merge_entity(into.id, elsewhere.id, "u")  # nor be planned
    with pytest.raises(ConflictError) as claimed:
        repo._claim_merge(into.id, elsewhere.id)  # nor merged away
    # Both name the merge to rerun (code review r72).
    assert planned.value.details == claimed.value.details == {"from": [dup.id]}
    assert repo.merge_entity(dup.id, into.id, "u", apply=True)["applied"]
    repo.mark_deleting(into.id)


def test_r69_a_failed_rerun_does_not_relock_the_duplicate(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r69: each failed rerun locked the duplicate for another 15 minutes.
    dup, into, _ = _stuck(repo, monkeypatch, "Relock")

    def conflict(*_: Any, **__: Any) -> Any:
        raise ConflictError("edited meanwhile")

    monkeypatch.setattr(repo, "_merge_into", conflict)
    with pytest.raises(ConflictError):
        repo.merge_entity(dup.id, into.id, "u", apply=True)
    monkeypatch.undo()
    assert repo.is_deleting(dup.id)
    assert repo.merge_entity(dup.id, into.id, "u", apply=True)["applied"]  # straight away


def test_r70_a_waiting_duplicate_that_is_gone_does_not_lock_the_kept_entity(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r70: a delete that failed after removing the duplicate left it listed on the kept entity for good.
    dup, into, _ = _stuck(repo, monkeypatch, "Gone")
    repo.delete(dup.id)  # the duplicate's own delete got further than it reported
    elsewhere = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Gone Elsewhere"), "u")[0]
    assert not repo.merge_entity(into.id, elsewhere.id, "u")["applied"]  # the dry run prunes it
    repo._table.update_item(  # listed again, for each of the other steps
        Key={"pk": f"N#{into.id}", "sk": "META"},
        UpdateExpression="ADD merge_pending :d",
        ExpressionAttributeValues={":d": {dup.id}},
    )
    repo._claim_merge(into.id, elsewhere.id)  # pruned, then claimed
    for node in (into, elsewhere):
        repo._table.update_item(
            Key={"pk": f"N#{node.id}", "sk": "META"},
            UpdateExpression="REMOVE deleting, merge_claim, merge_target, merge_at ADD merge_pending :d",
            ExpressionAttributeValues={":d": {dup.id}},
        )
    assert repo.delete_if_unlinked(repo.get(elsewhere.id))  # pruned, then deleted
    repo.mark_deleting(into.id)  # pruned, then flagged


def test_r70_a_finished_merge_forgets_the_duplicate_even_without_its_lock(repo: GraphRepository) -> None:
    dup, into, _ = _three(repo, "Forget")
    repo._table.update_item(
        Key={"pk": f"N#{into.id}", "sk": "META"},
        UpdateExpression="ADD merge_pending :d",
        ExpressionAttributeValues={":d": {dup.id}},
    )
    repo._finish_merge(dup.id, into.id, "a-lost-token")
    assert "merge_pending" not in (repo._meta_flags(into.id) or {})


def test_r70_an_edge_to_a_half_merged_duplicate_stops_the_merge(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r70: once its lock time was gone, another merge dropped edges to it as "being deleted".
    waiting, waiting_into, _ = _three(repo, "Waiting")
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Waiting Dup"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Waiting Kept"), "u")[0]
    repo.link(dup.id, EdgeType.PARTNERS_WITH, waiting.id, "u")

    def throttled(node_id: str) -> int:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchWriteItem")

    monkeypatch.setattr(repo, "delete", throttled)
    with pytest.raises(ClientError):
        repo.merge_entity(waiting.id, waiting_into.id, "u", apply=True)  # names moved, then stopped
    monkeypatch.undo()
    # Code review r71: the edge goes where the half-merged entity's own merge would take it, rather than a 409.
    # Its own merge already copied the edge to its kept entity, so the redirect and that copy make one edge between
    # them, counted once (code review r73, r74).
    assert repo.merge_entity(dup.id, kept.id, "u")["redirected"] == []  # the dry run has the key too
    report = repo.merge_entity(dup.id, kept.id, "u", apply=True)
    assert report["applied"] and not report["dropped"]  # the redirect or the plain move makes the edge, once
    assert report["edges"] == report["moved"] + len(report["dropped"]) + len(report["redirected"])  # r74
    assert waiting_into.id in {e.target for e in repo.edges(kept.id)}


def test_r73_a_redirect_that_makes_an_edge_is_listed(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting, waiting_into, _ = _three(repo, "Listed")
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Listed Dup"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Listed Kept"), "u")[0]
    repo.link(dup.id, EdgeType.PARTNERS_WITH, waiting.id, "u")

    def throttled(node_id: str) -> int:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchWriteItem")

    monkeypatch.setattr(repo, "delete", throttled)
    with pytest.raises(ClientError):
        repo.merge_entity(waiting.id, waiting_into.id, "u", apply=True)
    monkeypatch.undo()
    repo.unlink(dup.id, EdgeType.PARTNERS_WITH, waiting_into.id)  # as if the copy never landed
    report = repo.merge_entity(dup.id, kept.id, "u", apply=True)
    assert report["redirected"] == [
        {"from": dup.id, "type": EdgeType.PARTNERS_WITH.value, "to": waiting.id, "via": waiting_into.id}
    ]
    assert report["moved"] == 0  # counted once, as a redirect
    assert report["edges"] == report["moved"] + len(report["dropped"]) + len(report["redirected"])


def test_r71_a_link_that_cannot_follow_a_half_merged_entity_names_it(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r71: the 409 said "try again in a moment" and named nothing to rerun.
    waiting, waiting_into, _ = _three(repo, "Named")
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Named Dup"), "u")[0]
    repo.link(dup.id, EdgeType.PARTNERS_WITH, waiting.id, "u")

    def throttled(node_id: str) -> int:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchWriteItem")

    monkeypatch.setattr(repo, "delete", throttled)
    with pytest.raises(ClientError):
        repo.merge_entity(waiting.id, waiting_into.id, "u", apply=True)
    monkeypatch.undo()
    # Into the same kept entity the edge would link it to itself: dropped, as the half-merged entity's own merge
    # drops it, not a 409 (code review r72).
    report = repo.merge_entity(dup.id, waiting_into.id, "u", apply=True)
    assert report["applied"] and report["moved"] == 0
    assert {d["why"] for d in report["dropped"]} == {"it would link the kept entity to itself"}
    assert repo.merge_entity(waiting.id, waiting_into.id, "u", apply=True)["applied"]  # and the other still finishes


def test_r71_the_dry_run_writes_nothing(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    dup, into, _ = _three(repo, "ReadOnly")
    repo._table.update_item(
        Key={"pk": f"N#{dup.id}", "sk": "META"},
        UpdateExpression="ADD merge_pending :d",
        ExpressionAttributeValues={":d": {"organization/gone"}},
    )
    assert not repo.merge_entity(dup.id, into.id, "u")["applied"]  # a gone entry does not count
    assert (repo._meta_flags(dup.id) or {}).get("merge_pending") == {"organization/gone"}  # and stays

    def throttled(**_: Any) -> Any:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "UpdateItem")

    monkeypatch.setattr(repo._table, "update_item", throttled)
    assert not repo._prune_pending(dup.id)  # a failed prune drops nothing, so no retry is spent


def test_r71_a_refused_redirect_stops_the_merge(repo: GraphRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    waiting, waiting_into, _ = _three(repo, "Refused")
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Refused Dup"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Refused Kept"), "u")[0]
    repo.link(waiting.id, EdgeType.PARTNERS_WITH, dup.id, "u")  # an edge into the duplicate this time

    def throttled(node_id: str) -> int:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchWriteItem")

    monkeypatch.setattr(repo, "delete", throttled)
    with pytest.raises(ClientError):
        repo.merge_entity(waiting.id, waiting_into.id, "u", apply=True)
    monkeypatch.undo()
    repo._table.update_item(  # its kept entity refuses links too
        Key={"pk": f"N#{waiting_into.id}", "sk": "META"},
        UpdateExpression="SET deleting = :t",
        ExpressionAttributeValues={":t": True},
    )
    with pytest.raises(ConflictError) as raised:
        repo.merge_entity(dup.id, kept.id, "u", apply=True)
    assert raised.value.details == {"entity": waiting.id, "into": waiting_into.id}


def test_r72_two_half_merged_duplicates_linked_to_each_other_both_finish(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code review r72: each rerun refused the edge to the other, so neither could ever finish.
    one, kept, _ = _three(repo, "Mutual")
    two = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Mutual Two"), "u")[0]
    repo.link(one.id, EdgeType.PARTNERS_WITH, two.id, "u")

    def throttled(node_id: str) -> int:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchWriteItem")

    monkeypatch.setattr(repo, "delete", throttled)
    for node in (one, two):
        with pytest.raises(ClientError):
            repo.merge_entity(node.id, kept.id, "u", apply=True)
    monkeypatch.undo()
    for node in (one, two):
        assert repo.merge_entity(node.id, kept.id, "u", apply=True)["applied"]
    repo.mark_deleting(kept.id)  # nothing waits on it any more
