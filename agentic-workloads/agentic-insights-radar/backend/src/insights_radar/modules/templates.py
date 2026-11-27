# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Built-in module templates: the internal radar's fleet, expressed as editable data.

They ship disabled (the internal radar's cost-safety rule: an unrelated deploy must never arm
the fleet) with a 6-hour interval and a 2M weighted-token daily budget.

No module that reads product knowledge (private documents) holds an outbound tool (fetch, web
search, browser, code interpreter, MCP): an agent that has private data, reads untrusted content
and can send a request is one prompt injection from exfiltrating that data. Gaps, Content ideas
and Drafts therefore work offline from the graph and the product documents; the web work happens
in Scout and Signals, which cannot read product knowledge or the outputs derived from it.
"""

from __future__ import annotations

from dataclasses import dataclass

from insights_radar.graph.registry import EVIDENCE, NodeType
from insights_radar.modules.model import ModuleDefinition, ModuleTemplate, ToolGrant

G = ToolGrant
_EVIDENCE = tuple(sorted(EVIDENCE, key=lambda t: t.value))
_ENTITIES = (NodeType.ORGANIZATION, NodeType.PERSON, NodeType.PRODUCT, NodeType.TOPIC, NodeType.SEGMENT)


def builtin_modules() -> list[ModuleDefinition]:
    return [
        ModuleDefinition(
            slug="scout",
            name="Scout",
            template=ModuleTemplate.SCOUT,
            output_kind=None,
            builtin=True,
            description="Crawls your configured sources and records facts, opinions, events, adoptions and "
            "sentiment as linked evidence in the knowledge graph.",
            grants=frozenset({G.KG_READ, G.KG_WRITE_EVIDENCE, G.SOURCES, G.WEB_FETCH, G.WEB_SEARCH, G.BROWSER}),
        ),
        ModuleDefinition(
            slug="signal",
            name="Signals",
            template=ModuleTemplate.SIGNAL,
            output_kind=NodeType.SIGNAL,
            builtin=True,
            description="Turns well-corroborated, source-weighted evidence into signals: early movements your "
            "team should know about.",
            input_types=_EVIDENCE,
            grants=frozenset({G.KG_READ, G.KG_WRITE_EVIDENCE, G.KG_WRITE_OUTPUT, G.WEB_FETCH, G.WEB_SEARCH}),
        ),
        ModuleDefinition(
            slug="trend",
            name="Trends",
            template=ModuleTemplate.TREND,
            output_kind=NodeType.TREND,
            builtin=True,
            description="Finds sustained patterns across signals over weeks, not days.",
            input_types=(NodeType.SIGNAL,),
            grants=frozenset({G.KG_READ, G.KG_WRITE_OUTPUT}),
        ),
        ModuleDefinition(
            slug="gap",
            name="Gaps",
            template=ModuleTemplate.GAP,
            output_kind=NodeType.GAP,
            builtin=True,
            description="Compares signals and trends with your product profile and documents and records what "
            "the product does not do yet, with a ready-to-file improvement request.",
            input_types=(NodeType.SIGNAL, NodeType.TREND),
            grants=frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.PRODUCT_KNOWLEDGE}),
        ),
        ModuleDefinition(
            slug="content",
            name="Content ideas",
            template=ModuleTemplate.CONTENT,
            output_kind=NodeType.CONTENT_IDEA,
            builtin=True,
            description="Suggests content that rides a signal or trend your product already answers, when "
            "nothing equivalent exists publicly.",
            input_types=(NodeType.SIGNAL, NodeType.TREND, NodeType.GAP),
            grants=frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.PRODUCT_KNOWLEDGE}),
        ),
        ModuleDefinition(
            slug="draft",
            name="Content drafts",
            template=ModuleTemplate.DRAFT,
            output_kind=NodeType.CONTENT_DRAFT,
            builtin=True,
            interval_minutes=0,
            # On-demand and per-person: with one slot, a single hung run (a runtime that crashed
            # without finalizing) made every editor's draft request a 409 for the whole eight hours
            # of its run token.
            max_concurrency=5,
            description="On demand: writes a full Markdown draft from a content idea, citing sources and marking "
            "anything it cannot verify.",
            grants=frozenset({G.KG_READ, G.KG_WRITE_OUTPUT, G.CONTENT_DRAFT, G.PRODUCT_KNOWLEDGE}),
        ),
        ModuleDefinition(
            slug="curator",
            name="Radar curator",
            template=ModuleTemplate.CURATOR,
            output_kind=None,
            builtin=True,
            description="Proposes radar entry changes from graph evidence, following each radar's mode "
            "(manual, assist, copilot, autopilot).",
            input_types=(*_ENTITIES, NodeType.SIGNAL, NodeType.TREND, NodeType.INSIGHT),
            grants=frozenset({G.KG_READ, G.RADAR_SUGGEST}),
        ),
    ]


# What a new custom module arrives with: reading the graph and writing its own insights, and nothing
# that can send a request out of the deployment. An outbound tool makes a module one prompt injection
# from exfiltrating what it reads, so web_fetch and web_search are an administrator's explicit choice,
# not a default (platform P2-063).
CUSTOM_DEFAULT_GRANTS = frozenset({G.KG_READ, G.KG_WRITE_OUTPUT})


@dataclass(frozen=True, slots=True)
class TemplateChange:
    """A one-time change to a stored built-in that a new template version makes and the seed delivers
    to an existing deployment (CR2-03). The seed otherwise only removes a grant the safety rule forbids,
    so a template fix such as Trends losing web_search reached fresh installs only.

    Each change is applied once per module and recorded in ``extra.template_changes``: an admin who
    puts the grant back afterwards keeps it. ``max_concurrency`` is ``(old default, new)`` and applies
    only while the stored value is still the old default, so an admin's own choice is kept.

    The record alone is not trusted (r3 P3-003). The seed runs in the middle of a stack update, and
    when a later resource fails CloudFormation rolls the code back and runs the previous release's
    seed, which reset Trends' grants to its old template (web_search included) and kept the record.
    So a recorded change that is no longer in effect is applied again, unless an administrator undid
    it: ``ModuleRepository.update`` lists such edits in ``extra.template_overrides``, which no older
    release writes.
    """

    id: str
    slug: str
    reason: str
    remove_grants: frozenset[ToolGrant] = frozenset()
    max_concurrency: tuple[int, int] | None = None

    def undone_in(self, m: ModuleDefinition) -> bool:
        """Whether ``m`` is back in the state this change moves a module out of."""
        if self.remove_grants & m.grants:
            return True
        return bool(self.max_concurrency and m.max_concurrency == self.max_concurrency[0])

    def undone_by(self, before: ModuleDefinition, after: ModuleDefinition) -> bool:
        """Whether an edit from ``before`` to ``after`` puts back what this change took away."""
        if self.remove_grants & (after.grants - before.grants):
            return True
        old = self.max_concurrency[0] if self.max_concurrency else None
        return old is not None and after.max_concurrency == old != before.max_concurrency


# Where a stored built-in records the template changes it has had, and the ones an administrator
# undid afterwards (which the seed leaves undone).
TEMPLATE_CHANGES_KEY = "template_changes"
TEMPLATE_OVERRIDES_KEY = "template_overrides"
TEMPLATE_CHANGES: tuple[TemplateChange, ...] = (
    TemplateChange(
        "2026-10-trend-no-web-search",
        "trend",
        "the Trends prompt never searches the web; the grant only cost money (r2 P2-062)",
        remove_grants=frozenset({G.WEB_SEARCH}),
    ),
    TemplateChange(
        "2026-10-draft-concurrency-5",
        "draft",
        "one slot let a single hung draft run block every editor for eight hours (B2-024)",
        max_concurrency=(1, 5),
    ),
)
