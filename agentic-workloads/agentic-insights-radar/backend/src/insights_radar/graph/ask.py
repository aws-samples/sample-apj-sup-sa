# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Questions to the graph, answered with the records that bear on them (CTR kg_ask, HC-098/100/101).

Retrieval only: the caller's own model (chat, an MCP client) writes the answer from what comes back,
so a question costs no model call here and nothing is generated that a source does not back. The
search hits are walked one hop to their evidence (an output's supporting and contradicting records,
an entity's evidence and outputs), and the answer says how well it is backed: no matching records,
records but no sourced evidence, or evidence from a single source.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from insights_radar.graph.model import Node
from insights_radar.graph.registry import CONCLUSIONS, ENTITIES, EVIDENCE, OUTPUTS, EdgeType, NodeType
from insights_radar.graph.repository import GraphRepository
from insights_radar.graph.search import HybridSearch

HITS = 8
EVIDENCE_PER_HIT = 6
MAX_SOURCES = 30
# What may be linked to a record: evidence and outputs, without drafts, which are working copies (HC-032).
LINKED = sorted((EVIDENCE | OUTPUTS) - {NodeType.CONTENT_DRAFT}, key=lambda t: t.value)
CITING = sorted(OUTPUTS - {NodeType.CONTENT_DRAFT}, key=lambda t: t.value)
# What an output supports or contradicts: evidence, and other conclusions (registry SUPPORTS rules).
CLAIMED = sorted(EVIDENCE | CONCLUSIONS, key=lambda t: t.value)
BRIEF_LINKED = 60
BRIEF_EACH = 10

NOTES = {
    "none": "Nothing in the graph matches: say so rather than answer from general knowledge.",
    "loose": "No record shares most of the question's words. Say the graph may not cover this, and use only a "
    "record that clearly answers it.",
    "no_sources": "Records match, but none links a source: say the answer is not backed by evidence.",
    "single_source": "The evidence comes from one site (it may hold several documents): say so, and treat it as "
    "one publisher's view.",
    "sourced": "Answer from these records and cite their sources.",
}


@dataclass(frozen=True, slots=True)
class GraphAnswer:
    question: str
    hits: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    coverage: str  # "none", "loose", "no_sources", "single_source" or "sourced"
    degraded: bool

    def to_public(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "coverage": self.coverage,
            "note": NOTES[self.coverage]
            + (" Search by meaning was unavailable: these are keyword matches." if self.degraded else ""),
            "degraded": self.degraded,
            "hits": self.hits,
            "sources": self.sources,
        }


class GraphAsk:
    def __init__(self, repo: GraphRepository, search: HybridSearch) -> None:
        self._repo = repo
        self._search = search

    def ask(
        self, question: str, *, types: list[NodeType] | None = None, hidden: Callable[[Node], bool] | None = None
    ) -> GraphAnswer:
        # Hidden records are dropped before ranking, so the scores below are over what the caller may read.
        result = self._search.search_result(question, types=types, limit=HITS, hidden=hidden)

        def keep(n: Node) -> bool:  # for linked records: the hits themselves come back without hidden ones
            return hidden is None or not hidden(n)

        hits = result.hits
        evidence: dict[str, Node] = {}
        rows: list[dict[str, Any]] = []
        for h in hits:
            linked = self._linked(h.node)
            shown = [n for n in linked if keep(n)][:EVIDENCE_PER_HIT]
            for n in shown:
                evidence.setdefault(n.id, n)
            rows.append({**_brief(h.node), "score": round(h.score, 5), "evidence": [n.id for n in shown]})
        pool = {**{h.node.id: h.node for h in hits}, **evidence}
        sources = _sources(pool.values())
        # Semantic search always returns its nearest records, however far: with none sharing a word of the
        # question (a question the graph does not cover, an instruction aimed at the model), "sourced" told the
        # model to answer from unrelated records (MCP test, a deployment, 2026-11-03).
        # Only over "sourced"/"single_source": "no_sources" keeps its "not backed by evidence" warning
        # (round 33 security review).
        coverage = _coverage(bool(hits), sources)
        if coverage in ("sourced", "single_source") and not result.worded:
            coverage = "loose"
        return GraphAnswer(question, rows, sources, coverage, result.degraded)

    def brief(
        self, node: Node, *, placements: list[dict[str, Any]], hidden: Callable[[Node], bool] | None = None
    ) -> dict[str, Any]:
        """Everything the graph holds on one record, for researching it (CTR research_blip): its newest
        evidence, the conclusions (trends, gaps, signals) linked to it, the radars placing it, and its
        sources with a coverage flag."""
        linked = [n for n in self._linked(node, limit=BRIEF_LINKED) if hidden is None or not hidden(n)]
        evidence = [n for n in linked if n.type in EVIDENCE][:BRIEF_EACH]
        conclusions = [n for n in linked if n.type in OUTPUTS][:BRIEF_EACH]  # drafts are never linked (LINKED)
        sources = _sources([node, *evidence, *conclusions])
        coverage = _coverage(True, sources)
        return {
            "node": {**_brief(node), "summary": node.summary},
            "evidence": [_brief(n) for n in evidence],
            "conclusions": [_brief(n) for n in conclusions],
            "radars": placements,
            "sources": sources,
            "coverage": coverage,
            "note": NOTES[coverage],
        }

    def _linked(self, node: Node, limit: int = EVIDENCE_PER_HIT * 3) -> list[Node]:
        """One hop out: an output's evidence and the outputs that cite it; an entity's evidence and outputs."""
        claims = (EdgeType.SUPPORTS, EdgeType.CONTRADICTS)
        if node.type in ENTITIES:
            edges = self._repo.newest_links(
                node.id, direction="in", edge_types=[EdgeType.ABOUT], node_types=LINKED, per_type=limit
            )
            ids = [e.source for e in edges]
        elif node.type in EVIDENCE:
            # An output names its evidence (output -supports-> evidence): the outputs that cite this one.
            edges = self._repo.newest_links(
                node.id, direction="in", edge_types=claims, node_types=CITING, per_type=limit
            )
            ids = [e.source for e in edges]
        else:
            edges = self._repo.newest_links(
                node.id, direction="out", edge_types=claims, node_types=CLAIMED, per_type=limit
            )
            ids = [e.target for e in edges]
        found = self._repo.get_many(ids)
        # The newest first: the latest evidence is what a question about "now" needs.
        return sorted((found[i] for i in dict.fromkeys(ids) if i in found), key=lambda n: n.updated_at, reverse=True)[
            :limit
        ]


def _sources(nodes: Any) -> list[dict[str, Any]]:
    """The sourced records, newest first."""
    sourced = {n.id: n for n in nodes if n.evidence_url}.values()
    return [
        {"id": n.id, "type": n.type.value, "title": n.title, "url": n.evidence_url, "published_at": n.published_at}
        for n in sorted(sourced, key=lambda n: n.published_at or n.updated_at, reverse=True)[:MAX_SOURCES]
    ]


def _coverage(matched: bool, sources: list[dict[str, Any]]) -> str:
    hosts = {urlsplit(s["url"]).hostname for s in sources}
    return "none" if not matched else "no_sources" if not sources else "single_source" if len(hosts) == 1 else "sourced"


def _brief(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type.value if node.type is not NodeType.INSIGHT else f"insight:{node.insight_kind}",
        "title": node.title,
        "summary": node.summary[:400],
        "evidence_url": node.evidence_url,
        "updated_at": node.updated_at,
    }
