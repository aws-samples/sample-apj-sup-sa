# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Strands tools that forward to the backend tool API.

Only the tools named in the run token are built, so a module never sees a tool it cannot call.
A refused call comes back to the model as an error result with the backend's message, which
lets it correct the arguments; it is never raised into the agent loop.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from strands.tools.tools import PythonAgentTool
from strands.types.tools import ToolResult, ToolUse

from insights_radar_agents.tool_api import MAX_RESULT_CHARS, ToolApi, ToolCallError
from insights_radar_agents.tool_specs import SPECS

log = logging.getLogger(__name__)

WRITE_TOOLS = frozenset(
    {
        "kg_upsert_entity",
        "kg_add_evidence",
        "kg_link",
        "kg_save_output",
        "kg_update_output",
        "radar_suggest",
        "draft_save",
    }
)


# A node id as the backend writes it: ``<type>/<uuid>`` (graph/repository.py ``new_id``).
NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# The most created-entity ids one run reports; the backend caps the list at the same length.
MAX_CREATED_ENTITIES = 200


@dataclass
class WriteLedger:
    """Counts successful writes; the run summary reports them as ``nodes_written``. Also keeps the ids of the
    entities this run created (``kg_upsert_entity`` answered ``created: true``), so finalize can remove the ones
    the run left with no link: a run stopped between the upsert and the evidence or link that would connect the
    entity left it an orphan in the graph (Daily check 2026-11-10)."""

    counts: dict[str, int] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)
    # Created ids a later write named (evidence about it, a link, an insight's supports, a radar suggestion's
    # evidence): the entity is in use even where that use is not a graph edge, so finalize must keep it
    # (code review r50).
    referenced: set[str] = field(default_factory=set)

    def record(self, tool: str, result: Any = None, args: Any = None) -> None:
        self.counts[tool] = self.counts.get(tool, 0) + 1
        if self.created and args is not None:  # an upsert that names an earlier entity uses it too
            text = json.dumps(args, default=str)
            self.referenced.update(c for c in self.created if c in text)
        if tool == "kg_upsert_entity" and isinstance(result, dict) and result.get("created") is True:
            node_id = result.get("id")
            if (
                isinstance(node_id, str)
                and NODE_ID_RE.fullmatch(node_id)
                and node_id not in self.created
                and len(self.created) < MAX_CREATED_ENTITIES
            ):
                self.created.append(node_id)

    @property
    def unreferenced(self) -> list[str]:
        """The entities this run created and named in no later write: the ones finalize may remove."""
        return [c for c in self.created if c not in self.referenced]

    @property
    def nodes_written(self) -> int:
        return sum(n for t, n in self.counts.items() if t != "kg_link")


def _text(value: Any) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + " [truncated]"


# Backend error codes that end a run. Matched on the structured code of a backend refusal only, never on
# tool output text: a fetched page or an MCP server could otherwise stop a run by echoing the word.
STOP_CODES = ("genai_disabled", "budget_exhausted", "run_finished")
# A backend policy refusal (the grant is gone, the run may not touch the node): the call was answered and did
# nothing. A run whose every call came back like this did nothing at all (HC-139). A 404 or 409 is an answer
# about the data (no such node, a newer version), which the model can act on, so it is not counted.
REFUSED_STATUSES = frozenset({403})


class RemoteToolFactory:
    def __init__(self, api: ToolApi, ledger: WriteLedger | None = None) -> None:
        self._api = api
        self.ledger = ledger or WriteLedger()
        self.stop_code: str | None = None
        self.refused: set[str] = set()  # toolUseIds of the calls the backend refused (REFUSED_STATUSES)

    def build(self, granted: Iterable[str]) -> list[PythonAgentTool]:
        return [self._tool(name) for name in sorted(set(granted)) if name in SPECS]

    def _tool(self, name: str) -> PythonAgentTool:
        spec = SPECS[name]

        def run(tool_use: ToolUse, **_: Any) -> ToolResult:
            args = tool_use.get("input") or {}
            if not isinstance(args, dict):
                args = {}
            try:
                result = self._api.call(name, args)
            except ToolCallError as err:
                log.info("tool %s refused: %s %s", name, err.status, err.code)
                if err.code in STOP_CODES:
                    self.stop_code = err.code
                elif err.status in REFUSED_STATUSES:
                    self.refused.add(str(tool_use["toolUseId"]))
                return {"toolUseId": tool_use["toolUseId"], "status": "error", "content": [{"text": str(err)}]}
            except Exception as err:  # network or timeout: report and let the model decide
                log.warning("tool %s failed: %s", name, type(err).__name__)
                return {
                    "toolUseId": tool_use["toolUseId"],
                    "status": "error",
                    "content": [{"text": f"{name} is unavailable right now ({type(err).__name__}); try later."}],
                }
            if name in WRITE_TOOLS:
                self.ledger.record(name, result, args)
            return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": [{"text": _text(result)}]}

        return PythonAgentTool(name, spec.to_strands(), run)  # type: ignore[arg-type]
