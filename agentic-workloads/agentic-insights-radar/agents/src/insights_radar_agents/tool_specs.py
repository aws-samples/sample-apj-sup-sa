# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Model-facing descriptions and JSON schemas of the backend agent tools (``/agent/tools/<name>``).

The backend is the authority: it re-validates every argument and refuses tools the run token
does not grant. These specs only tell the model what to send. ``tests/test_tools.py`` keeps them in
step with ``insights_radar.api.agent_tools.AgentTools``: the tool names, and every limit the backend
enforces (A3-024), so a spec never invites a call the backend refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from insights_radar.graph.registry import NodeType
from insights_radar.modules.model import PRIVATE_OUTPUT_TYPES

_ID = {"type": "string", "description": "Node id, '<type>/<uuid>'."}
MAX_LINKS_PER_CALL = 40  # insights_radar.api.agent_tools.MAX_LINKS_PER_CALL
MAX_NEIGHBOR_NODES = 150  # agent_tools.kg_neighbors max_nodes
DRAFT_MAX_CHARS = 200_000  # agent_tools.DRAFT_MAX_BYTES; the Drafts output cap keeps real drafts far shorter
_IDS = {"type": "array", "items": _ID, "maxItems": MAX_LINKS_PER_CALL}
_HTML = "Safe HTML: only <p>, <br>, <strong>, <em>, <code> and <a href>."


@dataclass(frozen=True, slots=True)
class ToolSpecDef:
    name: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...] = ()

    def to_strands(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": self.properties,
                    "required": list(self.required),
                    "additionalProperties": False,
                }
            },
        }


def _s(description: str, max_length: int) -> dict[str, Any]:
    return {"type": "string", "description": description, "maxLength": max_length}


def _i(description: str, low: int, high: int) -> dict[str, Any]:
    return {"type": "integer", "description": description, "minimum": low, "maximum": high}


_OUTPUT_FIELDS: dict[str, Any] = {
    "title": _s("Headline, at most 60 characters.", 200),
    "summary": _s("One or two plain-text sentences shown on cards.", 2000),
    "description": _s(f"The full write-up. {_HTML}", 40_000),
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "attributes": {
        "type": "object",
        "description": "Extra snake_case fields. Gaps need request_title, problem and impact; "
        "content ideas need content_type and audience.",
    },
    "supports": {**_IDS, "description": "Evidence or outputs that support this one."},
    "contradicts": {**_IDS, "description": "Evidence that argues against this one."},
    "about": {**_IDS, "description": "Entities (organization, person, product, topic, segment) it is about."},
    "addresses": {**_IDS, "description": "Signals or trends this output answers (gaps, content ideas)."},
}

SPECS: dict[str, ToolSpecDef] = {
    s.name: s
    for s in (
        ToolSpecDef("run_context", "Your module, run id, output kind, cursor and (if set) the product profile.", {}),
        ToolSpecDef(
            "budget_status",
            "Today's token budget for this module: state open|warn|half_open|closed and guidance. "
            "Check it before expensive work and stop when it says so.",
            {},
        ),
        ToolSpecDef(
            "progress_update",
            "Record progress so a stopped run resumes where it left off. Call after each item.",
            {
                "last_processed_at": _s("resume_after of the newest item you finished (or its updated_at).", 240),
                "remaining": _i("How many candidate items are left.", 0, 1_000_000),
            },
        ),
        ToolSpecDef("kg_schema", "Node and edge types of the knowledge graph and which edges are allowed.", {}),
        ToolSpecDef(
            "kg_search",
            "Hybrid semantic + keyword search over the knowledge graph. Search before you create anything.",
            {
                "query": _s("What to look for.", 500),
                "types": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "insight_kind": _s("Restrict to one custom insight kind.", 60),
                "limit": _i("Results to return.", 1, 50),
            },
            ("query",),
        ),
        ToolSpecDef(
            "kg_get",
            f"Read one node by id, or up to {MAX_LINKS_PER_CALL} at once with ids (one call for all the evidence "
            "an output cites).",
            {"id": _ID, "ids": {**_IDS, "description": "Several node ids, read in one call."}},
        ),
        ToolSpecDef(
            "kg_neighbors",
            "Read a node's neighbourhood (nodes and edges) up to depth 3.",
            {
                "id": _ID,
                "depth": _i("Hops to follow.", 1, 3),
                "edge_types": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "max_nodes": _i("Cap on nodes returned.", 1, MAX_NEIGHBOR_NODES),
            },
            ("id",),
        ),
        ToolSpecDef(
            "kg_recent",
            "List nodes of one type, newest first, or oldest first after a timestamp (your cursor). "
            "Order and the after filter both use updated_at, so a node that was updated since your cursor "
            "is listed again; set last_processed_at from the resume_after of the items you finished.",
            {
                # The types in the description: a module guessing a custom kind ("regulatory_change") as a
                # type spent a refused call every run before it read the list in the error.
                "type": _s(
                    f"Node type, one of: {', '.join(t.value for t in NodeType)}. A custom module's outputs are "
                    "type=insight with insight_kind. A module with outbound tools cannot read "
                    f"{', '.join(sorted(t.value for t in PRIVATE_OUTPUT_TYPES))} nodes.",
                    40,
                ),
                "after": _s(
                    "Only nodes after this cursor (an item's resume_after, or a timestamp), oldest first.", 240
                ),
                "insight_kind": _s("For type=insight: the insight kind.", 60),
                "limit": _i("Page size.", 1, 100),
                "cursor": _s("Page cursor from the previous call.", 2000),
            },
            ("type",),
        ),
        ToolSpecDef(
            "kg_upsert_entity",
            "Create an entity (organization, person, product, topic, segment) or merge into the existing one "
            "with the same normalised name. Returns created=false when it already existed.",
            {
                "type": _s("organization | person | product | topic | segment", 40),
                "name": _s("Canonical name.", 200),
                "summary": _s("One plain-text sentence.", 2000),
                "description": _s(_HTML, 40_000),
                "aliases": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "attributes": {"type": "object"},
                "evidence_url": _s("Public https page about the entity.", 2048),
            },
            ("type", "name"),
        ),
        ToolSpecDef(
            "kg_add_evidence",
            "Record one piece of external evidence (fact, opinion, event, adoption, sentiment) with the public "
            "https URL you read it at, and link it to the entities it is about.",
            {
                "type": _s("fact | opinion | event | adoption | sentiment", 40),
                "title": _s("Headline, at most 60 characters.", 200),
                "summary": _s("What the source says, in one or two sentences.", 2000),
                "description": _s(_HTML, 40_000),
                "evidence_url": _s("The public https URL of the source. Required.", 2048),
                "published_at": _s("When the source was published, RFC 3339.", 40),
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "attributes": {"type": "object"},
                "about": {**_IDS, "description": "Entity ids this evidence is about."},
                "held_by": _s("Entity id holding the opinion or sentiment.", 200),
                "reported_by": _s("Entity id that reported it.", 200),
                "adopted_by": _s("Entity id that adopted something.", 200),
            },
            ("type", "title", "evidence_url"),
        ),
        ToolSpecDef(
            "kg_link",
            "Add an edge between two existing nodes. Allowed edges are listed by kg_schema.",
            {
                "from": _ID,
                "type": _s("Edge type.", 40),
                "to": _ID,
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            ("from", "type", "to"),
        ),
        ToolSpecDef(
            "kg_save_output",
            "Publish one output of your module's kind and link its evidence. Search for an existing one first "
            "and use kg_update_output instead of duplicating it.",
            _OUTPUT_FIELDS,
            ("title",),
        ),
        ToolSpecDef(
            "kg_update_output",
            "Update an output your module created. Pass the version you read (optimistic locking).",
            {"id": _ID, "expected_version": _i("Version you read.", 1, 1_000_000), **_OUTPUT_FIELDS},
            ("id", "expected_version"),
        ),
        ToolSpecDef(
            "product_knowledge_search",
            "Search the product's documentation and uploaded internal documents.",
            {"query": _s("What to look for.", 1000), "limit": _i("Passages to return.", 1, 20)},
            ("query",),
        ),
        ToolSpecDef("product_profile", "The product profile: capabilities, audiences, known competitors.", {}),
        ToolSpecDef(
            "source_list",
            "The sources assigned to this module, each with its resume cursor. Each shows only its host "
            "(https://<host>): the path and query are never shown, because either can hold a credential. "
            "Fetch a source with http_fetch and its source_id, never by building an address from the host.",
            {},
        ),
        ToolSpecDef(
            "source_update_cursor",
            "Save where you stopped in a source so the next run resumes there. Call as you finish each source.",
            {
                "source_id": _s("Source id from source_list.", 64),
                "cursor": _s(
                    "Where the next run resumes: the RFC 3339 published time of the newest item you processed, "
                    "that item's address, or the source's own page token (no spaces). Anything else is refused.",
                    200,
                ),
            },
            ("source_id", "cursor"),
        ),
        ToolSpecDef("radar_list", "Radars that accept AI suggestions (assist, copilot or autopilot mode).", {}),
        ToolSpecDef(
            "radar_get",
            "One radar: schema, entries and open suggestions.",
            {"slug": _s("Radar slug.", 64)},
            ("slug",),
        ),
        ToolSpecDef(
            "radar_suggest",
            "Propose a new entry or a change to an existing one. A human (or autopilot after the configured "
            "delay) decides; you never edit a radar directly.",
            {
                "slug": _s("Radar slug.", 64),
                "entry_key": _s("Existing entry key, to change it. Omit to propose a new entry.", 64),
                "proposal": {
                    "type": "object",
                    "description": "Fields: title, sector, sub_sector, ring, marker, description, node_id, link.",
                },
                "reason": _s("Why, citing evidence, 10 to 2000 characters.", 2000),
                "evidence": {**_IDS, "description": "Graph nodes that justify the proposal."},
            },
            ("slug", "proposal", "reason"),
        ),
        ToolSpecDef(
            "draft_get_idea",
            "Read a content idea with its brief, evidence and existing drafts.",
            {"id": _ID},
            ("id",),
        ),
        ToolSpecDef(
            "draft_save",
            "Save the full Markdown draft for a content idea. This is the only way to deliver a draft.",
            {
                "idea_id": _ID,
                "title": _s("Draft title.", 200),
                "markdown": _s(
                    f"The whole article in Markdown. The backend refuses more than {DRAFT_MAX_CHARS:,} bytes.",
                    DRAFT_MAX_CHARS,
                ),
            },
            ("idea_id", "title", "markdown"),
        ),
        ToolSpecDef(
            "skill_load",
            "Read the full instructions of one of the skills listed in your prompt under Skills, before a task "
            "that matches its description. Follow them as guidance for that task.",
            {"name": _s("The skill's name, as listed under Skills.", 64)},
            ("name",),
        ),
    )
}
