# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Module (agent) definitions and information sources.

A module is data. The seven built-ins (scout, signal, trend, gap, content, draft, curator) and
every admin-created custom module share this one shape and run on the same runtime; the
template only selects the base prompt and sensible defaults.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from insights_radar.core.errors import ValidationError
from insights_radar.core.ids import Slug
from insights_radar.core.tokens import DEFAULT_DAILY_TOKENS, BudgetSnapshot
from insights_radar.core.urls import UrlGuard
from insights_radar.graph.model import INSIGHT_KIND_RE, Node
from insights_radar.graph.registry import OUTPUTS, NodeType
from insights_radar.modules.attributes import AttributeSchema
from insights_radar.modules.catalog import DEFAULT_MODEL_KEY, ModelCatalog


class ModuleTemplate(StrEnum):
    SCOUT = "scout"
    SIGNAL = "signal"
    TREND = "trend"
    GAP = "gap"
    CONTENT = "content"
    DRAFT = "draft"
    CURATOR = "curator"
    CUSTOM = "custom"


class ToolGrant(StrEnum):
    KG_READ = "kg_read"
    KG_WRITE_EVIDENCE = "kg_write_evidence"
    KG_WRITE_OUTPUT = "kg_write_output"
    PRODUCT_KNOWLEDGE = "product_knowledge"
    SOURCES = "sources"
    RADAR_SUGGEST = "radar_suggest"
    CONTENT_DRAFT = "content_draft"
    WEB_FETCH = "web_fetch"
    WEB_SEARCH = "web_search"
    BROWSER = "browser"
    CODE_INTERPRETER = "code_interpreter"
    MCP = "mcp"


# Backend-enforced agent tools per grant. Tools served by AgentCore (web search, browser,
# code interpreter) or remote MCP servers are runtime-side and have no backend route.
GRANT_TOOLS: dict[ToolGrant, frozenset[str]] = {
    ToolGrant.KG_READ: frozenset({"kg_schema", "kg_search", "kg_get", "kg_neighbors", "kg_recent"}),
    ToolGrant.KG_WRITE_EVIDENCE: frozenset({"kg_upsert_entity", "kg_add_evidence", "kg_link"}),
    ToolGrant.KG_WRITE_OUTPUT: frozenset({"kg_save_output", "kg_update_output", "kg_link"}),
    ToolGrant.PRODUCT_KNOWLEDGE: frozenset({"product_knowledge_search", "product_profile"}),
    ToolGrant.SOURCES: frozenset(
        {"source_list", "source_address", "source_update_cursor", "source_mark_read", "source_inbox_ack"}
    ),
    ToolGrant.RADAR_SUGGEST: frozenset({"radar_list", "radar_get", "radar_suggest"}),
    ToolGrant.CONTENT_DRAFT: frozenset({"draft_get_idea", "draft_save"}),
    ToolGrant.WEB_FETCH: frozenset({"http_fetch"}),
    ToolGrant.WEB_SEARCH: frozenset(),
    ToolGrant.BROWSER: frozenset(),
    ToolGrant.CODE_INTERPRETER: frozenset(),
    ToolGrant.MCP: frozenset(),
}
# Grants that can send data out of the deployment, or read content an attacker controls and act on it.
OUTBOUND_GRANTS = frozenset(
    {ToolGrant.WEB_FETCH, ToolGrant.WEB_SEARCH, ToolGrant.BROWSER, ToolGrant.CODE_INTERPRETER, ToolGrant.MCP}
)
# Grants that read material derived from private product documents through a path that does not go
# through the graph's hider. None of them may be held together with an outbound tool.
GATED_GRANTS = frozenset({ToolGrant.PRODUCT_KNOWLEDGE, ToolGrant.CONTENT_DRAFT, ToolGrant.RADAR_SUGGEST})
# Outputs derived from product knowledge: a module with an outbound grant must not read them either.
PRIVATE_OUTPUT_TYPES = frozenset({NodeType.GAP, NodeType.CONTENT_IDEA, NodeType.CONTENT_DRAFT})
PRIVATE_WRITER = "private_writer"


def private_hider(writers: frozenset[str] | set[str]) -> Callable[[Node], bool]:
    """The one rule for what is private (viewers through the API, modules through the agent tools): a node
    stamped private, an output derived from product knowledge, or an unstamped node from a private writer."""
    return lambda node: bool(
        node.private
        or node.type in PRIVATE_OUTPUT_TYPES
        or (not node.stamped and node.module is not None and node.module in writers)
    )


# Radar suggestions and entries are read by every viewer, with no private stamp: a curator that read product
# documents could quote the roadmap in them (round 37 security review).
PRIVATE_SUGGEST_MESSAGE = (
    "product_knowledge cannot be combined with radar_suggest: radar suggestions and entries are shown to every "
    "viewer, so they must not be written from private documents"
)
PRIVATE_AND_OUTBOUND_MESSAGE = (
    "product_knowledge cannot be combined with web_fetch, web_search, browser, code_interpreter or mcp: a run "
    "that reads private documents must not be able to send them out"
)


def module_actor(slug: str) -> str:
    """Who a module's graph writes are recorded as (``created_by``), and what finalize matches to find them."""
    return f"module:{slug}"


ALWAYS_TOOLS = frozenset({"progress_update", "budget_status", "run_context", "run_definition"})

# Only these templates may hold the special grants.
SPECIAL_GRANTS: dict[ToolGrant, frozenset[ModuleTemplate]] = {
    ToolGrant.SOURCES: frozenset({ModuleTemplate.SCOUT, ModuleTemplate.CUSTOM}),
    ToolGrant.RADAR_SUGGEST: frozenset({ModuleTemplate.CURATOR}),
    ToolGrant.CONTENT_DRAFT: frozenset({ModuleTemplate.DRAFT}),
}


class SourceType(StrEnum):
    WEB_PAGE = "web_page"
    DYNAMIC_WEB_PAGE = "dynamic_web_page"
    RSS = "rss"
    PUBLIC_API = "public_api"


NAME_MAX = 80
PROMPT_MODES = ("extend", "replace")
REPLACE_MIN_INSTRUCTIONS = 200
DESCRIPTION_MAX = 1000
INSTRUCTIONS_MAX = 20_000
# An administrator's own version of the module's base template (the job description before the shared rules).
SYSTEM_PROMPT_MAX = 64_000
MAX_MODULE_SKILLS = 10  # skills attached to one module (modules/skills.py keeps the library)
SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
MAX_MCP_SERVERS = 10
MAX_INTERVAL_MINUTES = 7 * 24 * 60
SOURCE_NAME_MAX = 120
SOURCE_INSTRUCTION_MAX = 4000
SOURCE_CURSOR_MAX = 2000
_SOURCE_ID_RE = re.compile(r"^[a-z0-9]{6,16}$")


@dataclass(frozen=True, slots=True)
class McpServer:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class ModuleDefinition:
    slug: str
    name: str
    template: ModuleTemplate
    output_kind: NodeType | None
    description: str = ""
    instructions: str = ""
    insight_kind: str | None = None
    insight_label: str | None = None
    input_types: tuple[NodeType, ...] = ()
    grants: frozenset[ToolGrant] = frozenset()
    product_id: str | None = None
    model_key: str = DEFAULT_MODEL_KEY
    interval_minutes: int = 360
    daily_token_budget: int = DEFAULT_DAILY_TOKENS
    max_concurrency: int = 1
    mcp_servers: tuple[McpServer, ...] = ()
    # Typed fields every output must (or may) carry; empty = free-form attributes, as before.
    attribute_schema: AttributeSchema = field(default_factory=AttributeSchema)
    # "extend": the instructions fill the template's focus slot. "replace": they replace the template body;
    # the shared rules (quality, writing, untrusted content, run protocol) are appended either way.
    prompt_mode: str = "extend"
    # An administrator's version of the template body ("" = the packaged template). The shared rules are
    # appended either way (modules/prompts.py).
    system_prompt: str = ""
    # Names of library skills this module's runs may load (modules/skills.py).
    skills: tuple[str, ...] = ()
    enabled: bool = False
    builtin: bool = False
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    version: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def tools(self) -> frozenset[str]:
        names: set[str] = set(ALWAYS_TOOLS)
        for grant in self.grants:
            names |= GRANT_TOOLS[grant]
        if self.skills:
            names.add("skill_load")
        return frozenset(names)

    @property
    def outbound(self) -> bool:
        """Whether a run can send a request outside the deployment (and so must not see private data)."""
        return bool(self.grants & OUTBOUND_GRANTS)

    @property
    def private_and_outbound(self) -> bool:
        """Reads private product documents and can also send a request out: one prompt injection from
        exfiltrating them. Refused when saved, and again when a run starts or calls a tool, so a
        definition stored before the rule existed cannot run."""
        return ToolGrant.PRODUCT_KNOWLEDGE in self.grants and self.outbound

    @property
    def run_refusal(self) -> str | None:
        """Why this definition may not run, or None. Covers every grant that reads material derived from
        private documents outside the graph's hider (``GATED_GRANTS``), not only product_knowledge: a
        custom curator stored before the radar_suggest rule with web_fetch kept running, because only
        the validator knew the newer rule (SR2-04)."""
        if self.reads_private and ToolGrant.RADAR_SUGGEST in self.grants:
            return PRIVATE_SUGGEST_MESSAGE
        if not self.outbound:
            return None
        if self.private_and_outbound:
            return PRIVATE_AND_OUTBOUND_MESSAGE
        gated = sorted(g.value for g in self.grants & GATED_GRANTS)
        if gated:
            return f"grant {gated[0]} cannot be combined with an outbound tool"
        # As in the validator: a definition stored before the rule kept running, told to process gaps
        # and refused every read of them (B3-022).
        private_inputs = sorted(t.value for t in self.input_types if t in PRIVATE_OUTPUT_TYPES)
        return (
            f"modules with outbound tools cannot take {', '.join(private_inputs)} as input types"
            if private_inputs
            else None
        )

    @property
    def reads_private(self) -> bool:
        """Whether this module is given material derived from private product documents: the
        ``product_knowledge`` grant is what the full product profile (its ``notes``) and the private
        output types (gaps, content ideas, drafts) are gated on, and whatever it writes is stamped
        ``private`` so the taint travels with the node rather than with the module."""
        return ToolGrant.PRODUCT_KNOWLEDGE in self.grants

    @property
    def private_writer(self) -> bool:
        """Holds, or has ever held, product_knowledge: anything it wrote (insights, evidence, entities) may
        carry text derived from private documents, so modules with an outbound grant never read it. The
        mark is kept after the grant is removed (``extra.private_writer``), since the old outputs remain."""
        return ToolGrant.PRODUCT_KNOWLEDGE in self.grants or self.extra.get(PRIVATE_WRITER) is True

    def with_private_mark(self, previous: ModuleDefinition | None = None) -> ModuleDefinition:
        """This definition with the sticky private-writer mark set if it, or ``previous``, reads product
        knowledge."""
        marked = self.private_writer or (previous is not None and previous.private_writer)
        if self.extra.get(PRIVATE_WRITER) is True or not marked:
            return self
        return replace(self, extra={**self.extra, PRIVATE_WRITER: True})

    @property
    def nav_label(self) -> str:
        return self.insight_label or self.name

    def to_public(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "template": self.template.value,
            "output_kind": self.output_kind.value if self.output_kind else None,
            "description": self.description,
            "instructions": self.instructions,
            "insight_kind": self.insight_kind,
            "insight_label": self.insight_label,
            "input_types": [t.value for t in self.input_types],
            "grants": sorted(g.value for g in self.grants),
            "product_id": self.product_id,
            "model_key": self.model_key,
            "interval_minutes": self.interval_minutes,
            "daily_token_budget": self.daily_token_budget,
            "max_concurrency": self.max_concurrency,
            "mcp_servers": [{"name": m.name, "url": m.url} for m in self.mcp_servers],
            "attribute_schema": self.attribute_schema.to_public(),
            "prompt_mode": self.prompt_mode,
            "system_prompt": self.system_prompt,
            "skills": list(self.skills),
            "enabled": self.enabled,
            "builtin": self.builtin,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "extra": self.extra,
        }


class ModuleValidator:
    @staticmethod
    def validate(m: ModuleDefinition) -> ModuleDefinition:
        # Callers (route bodies, tests, a migration) can hand in a list where the field is a set or a
        # tuple; normalise first so every check below, and what is stored, sees the declared type.
        m = replace(
            m,
            grants=frozenset(m.grants),
            input_types=tuple(m.input_types),
            mcp_servers=tuple(m.mcp_servers),
            skills=tuple(dict.fromkeys(m.skills)),
        )
        errors: list[str] = []
        if not Slug.is_valid(m.slug):
            errors.append("slug must be 1-64 lower-case letters, digits or hyphens")
        elif Slug.is_reserved(m.slug):
            errors.append(f"slug {m.slug!r} is reserved")
        name = m.name.strip()
        if not name or len(name) > NAME_MAX:
            errors.append(f"name must be 1-{NAME_MAX} characters")
        if len(m.description) > DESCRIPTION_MAX:
            errors.append(f"description must be at most {DESCRIPTION_MAX} characters")
        if len(m.instructions) > INSTRUCTIONS_MAX:
            errors.append(f"instructions must be at most {INSTRUCTIONS_MAX} characters")
        if len(m.skills) > MAX_MODULE_SKILLS:
            errors.append(f"at most {MAX_MODULE_SKILLS} skills on a module")
        if any(not SKILL_NAME.fullmatch(s) for s in m.skills):
            errors.append("skill names are 1-64 lower-case letters, digits or hyphens")
        if len(m.system_prompt) > SYSTEM_PROMPT_MAX:
            errors.append(f"system_prompt must be at most {SYSTEM_PROMPT_MAX} characters")
        if m.template is ModuleTemplate.SCOUT or m.template is ModuleTemplate.CURATOR:
            if m.output_kind is not None:
                errors.append(f"{m.template.value} modules do not produce output nodes")
        elif m.output_kind is None or m.output_kind not in OUTPUTS:
            errors.append("output_kind must be an output node type")
        if m.output_kind is NodeType.INSIGHT:
            if not m.insight_kind or not INSIGHT_KIND_RE.fullmatch(m.insight_kind):
                errors.append("custom insight modules need an insight_kind in snake_case (2-41 chars)")
            if m.insight_label is not None and not 1 <= len(m.insight_label.strip()) <= 40:
                errors.append("insight_label must be 1-40 characters")
        elif m.insight_kind is not None:
            errors.append("insight_kind is only valid when output_kind is insight")
        if len(m.input_types) > 16:
            errors.append("too many input types")
        for grant, templates in SPECIAL_GRANTS.items():
            if grant in m.grants and m.template not in templates:
                errors.append(f"grant {grant.value} is not available to {m.template.value} modules")
        if m.private_and_outbound:
            errors.append(PRIVATE_AND_OUTBOUND_MESSAGE)
        if m.reads_private and ToolGrant.RADAR_SUGGEST in m.grants:
            errors.append(PRIVATE_SUGGEST_MESSAGE)
        private_inputs = sorted(t.value for t in m.input_types if t in PRIVATE_OUTPUT_TYPES)
        if m.outbound and private_inputs:
            # The run would be told to process them and then be refused every read: a wasted run, and
            # a prompt that names the private types to a model that reads untrusted pages.
            errors.append(f"modules with outbound tools cannot take {', '.join(private_inputs)} as input types")
        for grant in (ToolGrant.CONTENT_DRAFT, ToolGrant.RADAR_SUGGEST):
            if grant in m.grants and m.outbound:
                # Both read text derived from private documents (a content idea, a radar a non-outbound
                # curator wrote) through tools that do not go through the graph's hider.
                errors.append(f"grant {grant.value} cannot be combined with an outbound tool")
        if m.output_kind is not None and ToolGrant.KG_WRITE_OUTPUT not in m.grants:
            errors.append("modules that produce outputs need the kg_write_output grant")
        if ToolGrant.KG_READ not in m.grants:
            errors.append("every module needs the kg_read grant")
        if m.model_key not in ModelCatalog.MODELS:
            errors.append(f"unknown model {m.model_key!r}")
        if not 0 <= m.interval_minutes <= MAX_INTERVAL_MINUTES:
            errors.append(f"interval_minutes must be between 0 (on demand) and {MAX_INTERVAL_MINUTES}")
        elif 0 < m.interval_minutes < 15:
            errors.append("interval_minutes must be 0 (on demand) or at least 15")
        try:
            BudgetSnapshot.validate_limit(m.daily_token_budget)
        except ValueError as err:
            errors.append(str(err))
        if not 1 <= m.max_concurrency <= 10:
            errors.append("max_concurrency must be between 1 and 10")
        if len(m.mcp_servers) > MAX_MCP_SERVERS:
            errors.append(f"at most {MAX_MCP_SERVERS} MCP servers")
        names = set()
        for server in m.mcp_servers:
            reason = UrlGuard.reason(server.url)
            if reason:
                errors.append(f"MCP server {server.name!r}: {reason}")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", server.name):
                errors.append(f"MCP server name {server.name!r} must be 1-40 letters, digits, - or _")
            if server.name in names:
                errors.append(f"duplicate MCP server name {server.name!r}")
            names.add(server.name)
        if m.prompt_mode not in PROMPT_MODES:
            errors.append("prompt_mode must be extend or replace")
        elif m.prompt_mode == "replace" and len(m.instructions.strip()) < REPLACE_MIN_INSTRUCTIONS:
            errors.append(f"prompt_mode replace needs instructions of at least {REPLACE_MIN_INSTRUCTIONS} characters")
        if m.attribute_schema and m.output_kind is None:
            errors.append("only modules that produce outputs can declare an attribute_schema")
        if m.mcp_servers and ToolGrant.MCP not in m.grants:
            errors.append("attaching MCP servers needs the mcp grant")
        if errors:
            raise ValidationError("module definition is invalid", {"errors": errors})
        return replace(
            m,
            name=name,
            description=m.description.strip(),
            insight_label=m.insight_label.strip() if m.insight_label else None,
        )


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    module: str
    name: str
    type: SourceType
    address: str
    instruction: str = ""
    cursor: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""
    # When a run last fetched it successfully ("" = never), stamped by the runtime, not the model or an admin
    # edit: the Scout reads the least recently read sources first (code review r14).
    read_at: str = ""
    # When a run last tried to fetch it, read or not: the pass order, so a source that keeps failing does not
    # take the first pass every run (code review r15).
    tried_at: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "name": self.name,
            "type": self.type.value,
            "address": self.address,
            "instruction": self.instruction,
            "cursor": self.cursor,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "read_at": self.read_at,
            "tried_at": self.tried_at,
        }

    @staticmethod
    def validate(s: Source) -> Source:
        errors: list[str] = []
        if not _SOURCE_ID_RE.fullmatch(s.id):
            errors.append("source id is malformed")
        name = s.name.strip()
        if not 1 <= len(name) <= SOURCE_NAME_MAX:
            errors.append(f"name must be 1-{SOURCE_NAME_MAX} characters")
        reason = UrlGuard.reason(s.address)
        if reason:
            errors.append(f"address: {reason}")
        if len(s.instruction) > SOURCE_INSTRUCTION_MAX:
            errors.append(f"instruction must be at most {SOURCE_INSTRUCTION_MAX} characters")
        if len(s.cursor) > SOURCE_CURSOR_MAX:
            errors.append(f"cursor must be at most {SOURCE_CURSOR_MAX} characters")
        if errors:
            raise ValidationError("source is invalid", {"errors": errors})
        return replace(s, name=name, instruction=s.instruction.strip(), cursor=s.cursor.strip())
