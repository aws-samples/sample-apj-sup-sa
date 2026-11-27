# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Graph value objects and their validation.

A ``Node`` carries a small fixed set of typed fields plus a bounded ``attributes`` map for
type-specific detail (``org_kind`` on organizations, a gap's improvement request, a
sentiment's ``polarity``). Descriptions are stored as sanitised HTML; ``summary`` is plain
text used for lists, embeddings and keyword search.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from insights_radar.core.errors import ValidationError
from insights_radar.core.html import HtmlSanitizer
from insights_radar.core.names import NameNormalizer
from insights_radar.core.urls import UrlGuard
from insights_radar.graph.registry import Category, EdgeType, NodeType, Registry

TITLE_MAX = 200
SUMMARY_MAX = 2000
DESCRIPTION_MAX = 40_000
ATTR_KEYS_MAX = 30
ATTR_VALUE_MAX = 4000
ALIASES_MAX = 20
INSIGHT_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_ATTR_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

AttrValue = str | int | float | bool | list[str]


def _clean_attrs(raw: dict[str, Any] | None) -> dict[str, AttrValue]:
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError("attributes must be an object")
    if len(raw) > ATTR_KEYS_MAX:
        raise ValidationError(f"at most {ATTR_KEYS_MAX} attributes are allowed")
    out: dict[str, AttrValue] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _ATTR_KEY_RE.fullmatch(key):
            raise ValidationError(f"attribute name {key!r} must be snake_case")
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, int):
            if abs(value) >= 10**38:  # DynamoDB numbers hold 38 digits
                raise ValidationError(f"attribute {key} is out of range")
            out[key] = value
        elif isinstance(value, float | Decimal):
            number = float(value)
            if not math.isfinite(number) or abs(number) >= 1e38:  # NaN and Infinity cannot be stored
                raise ValidationError(f"attribute {key} must be a finite number")
            out[key] = number
        elif isinstance(value, str):
            if len(value) > ATTR_VALUE_MAX:
                raise ValidationError(f"attribute {key} is too long")
            out[key] = value
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            if len(value) > 50 or any(len(v) > 500 for v in value):
                raise ValidationError(f"attribute {key} list is too large")
            out[key] = list(value)
        elif value is None:
            continue
        else:
            raise ValidationError(f"attribute {key} has an unsupported type")
    return out


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    type: NodeType
    title: str
    summary: str = ""
    description: str = ""
    attributes: dict[str, AttrValue] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    evidence_url: str | None = None
    published_at: str | None = None
    confidence: float | None = None
    insight_kind: str | None = None
    module: str | None = None
    # True when the module that wrote this node could read private product documents at the time: the
    # taint travels with the node, so deleting or re-granting the writing module cannot publish it.
    private: bool = False
    # Whether ``private`` was decided when the node was written. Nodes written before the stamp
    # existed carry none, and for them the writing module's sticky private-writer mark decides
    # (SR2-01): its current grants say nothing about what it wrote while it held product_knowledge.
    stamped: bool = False
    # Evidence the writing run really fetched, as the runtime (never the model) reported it. The
    # autopilot counts only such evidence, or evidence a person recorded, as corroboration (SR2-02).
    fetched: bool = False
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    version: int = 1
    in_degree: int = 0
    out_degree: int = 0

    @property
    def category(self) -> Category:
        return self.type.category

    @property
    def name_key(self) -> str | None:
        return NameNormalizer.key(self.title) if self.category is Category.ENTITY else None

    @property
    def degree(self) -> int:
        return self.in_degree + self.out_degree

    @property
    def search_text(self) -> str:
        parts = [self.title, self.summary, " ".join(self.aliases), HtmlSanitizer.to_text(self.description)[:4000]]
        return "\n".join(p for p in parts if p)

    def to_public(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": self.id,
            "type": self.type.value,
            "category": self.category.value,
            "title": self.title,
            "summary": self.summary,
            "description": self.description,
            "attributes": self.attributes,
            "aliases": list(self.aliases),
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "degree": self.degree,
        }
        if self.private:
            body["private"] = True
        for key in ("evidence_url", "published_at", "confidence", "insight_kind", "module"):
            value = getattr(self, key)
            if value is not None:
                body[key] = value
        return body


class NodeFactory:
    """Builds validated nodes from untrusted input (API bodies, agent tool calls)."""

    @staticmethod
    def validate(node: Node) -> Node:
        title = NameNormalizer.display(node.title)
        if not title:
            raise ValidationError("title is required")
        if len(title) > TITLE_MAX:
            raise ValidationError(f"title must be at most {TITLE_MAX} characters")
        summary = node.summary.strip()
        if len(summary) > SUMMARY_MAX:
            raise ValidationError(f"summary must be at most {SUMMARY_MAX} characters")
        description = HtmlSanitizer.sanitize(node.description)
        if len(description) > DESCRIPTION_MAX:
            raise ValidationError(f"description must be at most {DESCRIPTION_MAX} characters")
        # An alias must have a name key: one that normalises to nothing ("-") would break every later merge of
        # the entity that carries it (security review r58).
        aliases = tuple(
            dict.fromkeys(
                NameNormalizer.display(a) for a in node.aliases if a and a.strip() and NameNormalizer.has_key(a)
            )
        )
        if len(aliases) > ALIASES_MAX:
            raise ValidationError(f"at most {ALIASES_MAX} aliases are allowed")
        if node.category is Category.EVIDENCE:
            reason = UrlGuard.reason(node.evidence_url)
            if reason is not None:
                raise ValidationError(f"evidence needs a public https evidence_url ({reason})")
        elif node.evidence_url is not None and not UrlGuard.is_safe(node.evidence_url):
            raise ValidationError("evidence_url must be a public https URL")
        if node.confidence is not None and not 0.0 <= float(node.confidence) <= 1.0:
            raise ValidationError("confidence must be between 0 and 1")
        if node.type is NodeType.INSIGHT:
            if not node.insight_kind or not INSIGHT_KIND_RE.fullmatch(node.insight_kind):
                raise ValidationError("insight nodes need an insight_kind in snake_case")
        elif node.insight_kind is not None:
            raise ValidationError("insight_kind is only valid on insight nodes")
        if node.category is Category.ENTITY:
            NameNormalizer.key(title)  # raises on an empty key
        return replace(
            node,
            title=title,
            summary=summary,
            description=description,
            aliases=aliases,
            attributes=_clean_attrs(node.attributes),
            confidence=None if node.confidence is None else float(node.confidence),
        )


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    type: EdgeType
    target: str
    created_by: str = ""
    created_at: str = ""
    confidence: float | None = None
    run_id: str | None = None

    def to_public(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "from": self.source,
            "type": self.type.value,
            "to": self.target,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }
        if self.confidence is not None:
            body["confidence"] = self.confidence
        return body


def node_type_of(node_id: str) -> NodeType:
    """Node ids are ``<type>/<uuid>``; the type prefix is authoritative."""
    prefix, sep, rest = node_id.partition("/")
    if not sep or not rest:
        raise ValidationError(f"malformed node id {node_id!r}")
    return Registry.node_type(prefix)
