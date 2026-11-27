# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Closed registries of node and edge types (the generic successor of the internal radar's sec. 21/22).

Adding a type is a code change reviewed like any other; agents cannot invent types at run
time. Every edge declares which node types may sit at each end, and the repository rejects
anything else (the internal radar's sec. 25.13 perimeter rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from insights_radar.core.errors import ValidationError


class Category(StrEnum):
    ENTITY = "entity"
    EVIDENCE = "evidence"
    OUTPUT = "output"


class NodeType(StrEnum):
    # entities: the things the world talks about
    ORGANIZATION = "organization"  # company, vendor, competitor, partner, community, regulator, investor, media
    PERSON = "person"
    PRODUCT = "product"  # an external product, technology, component, material or offering
    TOPIC = "topic"  # a theme, concept, practice, feature idea or standard
    SEGMENT = "segment"  # a customer segment, market or audience
    # evidence: what was observed, with a source
    FACT = "fact"
    OPINION = "opinion"
    EVENT = "event"  # news, launch, announcement, release, funding, regulation change
    ADOPTION = "adoption"  # an actor adopting / using something
    SENTIMENT = "sentiment"  # aggregate mood of a group about something
    # outputs: what modules conclude
    SIGNAL = "signal"
    TREND = "trend"
    GAP = "gap"
    CONTENT_IDEA = "content_idea"
    CONTENT_DRAFT = "content_draft"
    INSIGHT = "insight"  # custom-module output, labelled by ``insight_kind``

    @property
    def category(self) -> Category:
        return _CATEGORY[self]


_CATEGORY: dict[NodeType, Category] = {
    **dict.fromkeys(
        (NodeType.ORGANIZATION, NodeType.PERSON, NodeType.PRODUCT, NodeType.TOPIC, NodeType.SEGMENT), Category.ENTITY
    ),
    **dict.fromkeys(
        (NodeType.FACT, NodeType.OPINION, NodeType.EVENT, NodeType.ADOPTION, NodeType.SENTIMENT), Category.EVIDENCE
    ),
    **dict.fromkeys(
        (
            NodeType.SIGNAL,
            NodeType.TREND,
            NodeType.GAP,
            NodeType.CONTENT_IDEA,
            NodeType.CONTENT_DRAFT,
            NodeType.INSIGHT,
        ),
        Category.OUTPUT,
    ),
}

ENTITIES = frozenset(t for t in NodeType if t.category is Category.ENTITY)
EVIDENCE = frozenset(t for t in NodeType if t.category is Category.EVIDENCE)
OUTPUTS = frozenset(t for t in NodeType if t.category is Category.OUTPUT)
ACTORS = frozenset({NodeType.ORGANIZATION, NodeType.PERSON, NodeType.SEGMENT})
CONCLUSIONS = frozenset({NodeType.SIGNAL, NodeType.TREND, NodeType.GAP, NodeType.INSIGHT})


class EdgeType(StrEnum):
    ABOUT = "about"
    HELD_BY = "held_by"
    REPORTED_BY = "reported_by"
    ADOPTED_BY = "adopted_by"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    ADDRESSES = "addresses"
    OFFERS = "offers"
    COMPETES_WITH = "competes_with"
    PARTNERS_WITH = "partners_with"
    USES = "uses"
    TARGETS = "targets"
    PART_OF = "part_of"
    RELATED_TO = "related_to"
    AFFILIATED_WITH = "affiliated_with"
    HAS_DRAFT = "has_draft"


@dataclass(frozen=True, slots=True)
class EdgeRule:
    sources: frozenset[NodeType]
    targets: frozenset[NodeType]
    description: str


EDGE_RULES: dict[EdgeType, EdgeRule] = {
    EdgeType.ABOUT: EdgeRule(EVIDENCE | OUTPUTS, ENTITIES, "what the evidence or output is about"),
    EdgeType.HELD_BY: EdgeRule(frozenset({NodeType.OPINION, NodeType.SENTIMENT}), ACTORS, "who holds the view"),
    EdgeType.REPORTED_BY: EdgeRule(
        frozenset({NodeType.FACT, NodeType.EVENT}),
        frozenset({NodeType.ORGANIZATION, NodeType.PERSON}),
        "who reported it",
    ),
    EdgeType.ADOPTED_BY: EdgeRule(frozenset({NodeType.ADOPTION}), ACTORS, "who adopted it"),
    EdgeType.SUPPORTS: EdgeRule(
        OUTPUTS - {NodeType.CONTENT_DRAFT}, EVIDENCE | CONCLUSIONS, "evidence or conclusion supporting this output"
    ),
    EdgeType.CONTRADICTS: EdgeRule(
        OUTPUTS - {NodeType.CONTENT_DRAFT}, EVIDENCE | CONCLUSIONS, "evidence or conclusion arguing against this output"
    ),
    EdgeType.ADDRESSES: EdgeRule(
        frozenset({NodeType.GAP, NodeType.CONTENT_IDEA, NodeType.INSIGHT}),
        CONCLUSIONS,
        "the signal/trend/gap/insight this responds to",
    ),
    EdgeType.OFFERS: EdgeRule(frozenset({NodeType.ORGANIZATION}), frozenset({NodeType.PRODUCT}), "sells or provides"),
    EdgeType.COMPETES_WITH: EdgeRule(
        frozenset({NodeType.ORGANIZATION, NodeType.PRODUCT}),
        frozenset({NodeType.ORGANIZATION, NodeType.PRODUCT}),
        "competitor relation",
    ),
    EdgeType.PARTNERS_WITH: EdgeRule(
        frozenset({NodeType.ORGANIZATION}), frozenset({NodeType.ORGANIZATION}), "partnership"
    ),
    EdgeType.USES: EdgeRule(
        ACTORS | {NodeType.PRODUCT}, frozenset({NodeType.PRODUCT, NodeType.TOPIC}), "uses or depends on"
    ),
    EdgeType.TARGETS: EdgeRule(
        frozenset({NodeType.PRODUCT, NodeType.ORGANIZATION}), frozenset({NodeType.SEGMENT}), "aims at a segment"
    ),
    EdgeType.PART_OF: EdgeRule(
        frozenset({NodeType.PRODUCT, NodeType.TOPIC, NodeType.SEGMENT}),
        frozenset({NodeType.PRODUCT, NodeType.TOPIC, NodeType.SEGMENT}),
        "component/sub-theme",
    ),
    EdgeType.RELATED_TO: EdgeRule(ENTITIES, ENTITIES, "generic association between entities"),
    EdgeType.AFFILIATED_WITH: EdgeRule(
        frozenset({NodeType.PERSON}), frozenset({NodeType.ORGANIZATION}), "works for / member of"
    ),
    EdgeType.HAS_DRAFT: EdgeRule(
        frozenset({NodeType.CONTENT_IDEA}), frozenset({NodeType.CONTENT_DRAFT}), "a generated draft of the idea"
    ),
}


class Registry:
    @staticmethod
    def node_type(value: str) -> NodeType:
        try:
            return NodeType(value)
        except ValueError as err:
            raise ValidationError(
                f"unknown node type {value!r}; allowed: {', '.join(t.value for t in NodeType)}"
            ) from err

    @staticmethod
    def edge_type(value: str) -> EdgeType:
        try:
            return EdgeType(value)
        except ValueError as err:
            raise ValidationError(
                f"unknown edge type {value!r}; allowed: {', '.join(e.value for e in EdgeType)}"
            ) from err

    @staticmethod
    def check_edge(edge: EdgeType, source: NodeType, target: NodeType) -> None:
        rule = EDGE_RULES[edge]
        if source not in rule.sources or target not in rule.targets:
            raise ValidationError(
                f"edge {edge.value} cannot connect {source.value} -> {target.value}; "
                f"allowed {sorted(s.value for s in rule.sources)} -> {sorted(t.value for t in rule.targets)}"
            )

    @staticmethod
    def describe() -> dict[str, object]:
        """Machine-readable description handed to agents (``kg_schema`` tool)."""
        return {
            "node_types": {t.value: t.category.value for t in NodeType},
            "edge_types": {
                e.value: {
                    "from": sorted(s.value for s in r.sources),
                    "to": sorted(t.value for t in r.targets),
                    "meaning": r.description,
                }
                for e, r in EDGE_RULES.items()
            },
        }
