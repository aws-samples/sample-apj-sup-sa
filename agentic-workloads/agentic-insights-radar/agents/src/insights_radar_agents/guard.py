# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Fence untrusted tool output before it re-enters the model's context.

Bedrock Guardrails do not evaluate tool results (``toolResult`` blocks are outside every
guardrail filter), so indirect prompt injection from a web page, a search snippet, browser
output, code output, an admin-attached MCP server or text stored in the graph reaches the model
unless something here stops it. This hook is that control: one ``AfterToolCallEvent`` callback
covers every tool regardless of where it came from.

Fencing is a mitigation, not a proof. The authoritative control is server-side: the backend
validates every write against the run token's grants, whatever the model was persuaded to try.

Only the bookkeeping tools are trusted (their output is produced by this solution, not copied
from outside). Everything else, including graph reads, is fenced: graph text was written by
agents from web content.
"""

from __future__ import annotations

import json
import re
from typing import Any

from insights_radar.core.defang import MARKER_REDACTED, Defang
from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry

# skill_load returns an administrator's skill, which the prompt tells the run to follow: like the instructions it
# is trusted text, defanged by the backend (api/agent_tools.py), so it is not fenced as fetched content.
TRUSTED_TOOLS = frozenset(
    {"run_context", "budget_status", "progress_update", "kg_schema", "source_update_cursor", "skill_load"}
)
MAX_CHARS = 100_000  # across every block of one result, not per block (P3-085)
TEXT_DOCUMENT_FORMATS = frozenset({"txt", "md", "csv", "html"})
# The fence tag's name, whatever surrounds it. Defang neutralises only the literal tag, but a model reads
# "&lt;/untrusted-tool-output&gt;" (browser get_html) or "\u003c/untrusted-tool-output\u003e" (a JSON API)
# as the closing tag too, so the name itself is replaced wherever it appears (P3-084).
_SEP = "[-_\u2010-\u2015\u2212\ufe58\ufe63\uff0d\\s]*"
_TAG_NAME = re.compile(rf"untrusted{_SEP}tool{_SEP}output", re.IGNORECASE)


def fence(tool: str, text: str, limit: int = MAX_CHARS) -> str:
    """``Defang.fence`` with the tag's name removed from the payload in every encoding, not only the
    literal tag."""
    return Defang.fence(tool, _TAG_NAME.sub(MARKER_REDACTED, Defang.fold(text)), limit)


def scrub_strings(value: Any, depth: int = 0) -> Any:
    """Neutralise markers in every string of a JSON value, keeping its structure."""
    if depth > 20:
        return None
    if isinstance(value, str):
        return Defang.redact_markers(value)
    if isinstance(value, dict):
        return {Defang.redact_markers(str(k)): scrub_strings(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_strings(v, depth + 1) for v in value]
    return value


def _document_text(document: Any) -> str | None:
    """The text of a plain-text document block, or None for binary formats and unreadable bytes."""
    if not isinstance(document, dict) or str(document.get("format", "")).lower() not in TEXT_DOCUMENT_FORMATS:
        return None
    raw = (document.get("source") or {}).get("bytes")
    if not isinstance(raw, bytes | bytearray):
        return None
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None


class UntrustedOutputGuard(HookProvider):
    def __init__(self, trusted: frozenset[str] = TRUSTED_TOOLS) -> None:
        self._trusted = trusted
        self.fenced = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.on_after_tool)

    def on_after_tool(self, event: AfterToolCallEvent) -> None:
        name = str(event.tool_use.get("name", ""))
        if name in self._trusted or not event.result:
            return
        content: list[Any] = []
        left = MAX_CHARS
        for block in event.result.get("content", []):
            if left <= 0:
                content.append({"text": fence(name, "[the rest of this result was cut: it is over its size cap]")})
                break
            text = self._text_of(block)
            content.append({"text": fence(name, text, left)})
            left -= len(text)
        event.result = {**event.result, "content": content}
        self.fenced += 1

    @staticmethod
    def _text_of(block: Any) -> str:
        if not isinstance(block, dict):
            return "[a non-text block from this tool was removed: only fenced text is passed on]"
        if "text" in block:
            return str(block["text"])
        if "json" in block:
            return json.dumps(scrub_strings(block["json"]), ensure_ascii=False, default=str)
        if "image" in block:
            # The model reads text in images (screenshots), and no fence or Defang step can neutralise text
            # drawn in pixels: images are dropped. The browser's get_text gives the page as text.
            return (
                "[an image from this tool was removed: images cannot be screened for injected "
                "instructions; read the page as text instead]"
            )
        # Documents are text the fence would never see (a PDF, a .txt). Plain-text ones become a fenced
        # text block; anything else (binary documents, video, unknown block types) is dropped.
        document = _document_text(block.get("document"))
        if document is None:
            return "[a non-text block from this tool was removed: only fenced text is passed on]"
        return document
