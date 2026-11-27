# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""A module run's configured sources: their administrator instructions and the cursors the model writes.

* **Instructions** (A3-006). An administrator's per-source instruction ("only the /press section; use
  page=N") is configuration, but ``source_list`` returns it inside the untrusted-output fence, beside the
  cursor the model wrote, where the shared rules say nothing is to be followed. The runtime therefore
  reads the instructions itself at the start of the run and puts them in the run message (defanged, as
  module instructions are), and removes them from what ``source_list`` shows the model.
* **Cursors** (A3-005). The model writes each source's resume point from what it read, and the next
  run is told to resume there, so a hostile feed item could make it save a cursor that skips the source
  for good ("after the post titled <one that never comes>", a year-2099 timestamp) or carries text for
  the next run. A cursor must be an RFC 3339 time (a future one is clamped to now), the address of an
  item on a host the run read, or a page token (no spaces, at most ``MAX_TOKEN`` characters). Every
  cursor a run saves is reported in its summary.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from insights_radar.core.clock import iso, parse_iso
from insights_radar.core.defang import Defang
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

MAX_TOKEN = 200
MAX_INSTRUCTION = 4000
# Sources one run lists: with the source watcher, a run reads only the sources with new items, so a deployment can
# watch hundreds (one deployment: over a hundred official sources across Asia-Pacific).
MAX_SOURCES = 500
_TOKEN = re.compile(rf"[A-Za-z0-9._~:+/=,-]{{1,{MAX_TOKEN}}}")


SCOUT_PASS_SIZE = 6


def scout_passes(listing: Any) -> list[list[dict[str, Any]] | None]:
    """The sources a Scout run works through, a few at a time.

    One conversation over every source re-sends the whole growing context (fetched pages included) on each
    model call, so a 50-source run cost tens of millions of cached tokens (CTR HC-111). A run with more than
    SCOUT_PASS_SIZE sources works in passes of that many, each a fresh conversation; ``[None]`` means one
    pass over everything, as before.

    The passes go least recently read first. A run that stops early (the module's daily budget, a block)
    starts over at pass 1 next time, so with the listing's own order (by id) the last passes were never
    reached once a deployment had more sources than one day's budget covers: on one deployment, 24 sources and a
    2,000,000-token day left Australia, the Philippines, Taiwan and CBP unread. The order is the source's
    ``tried_at``, which the runtime stamps on every fetch of it, read or failed (not a cursor save the model
    makes, nor an admin's edit, which moved ``updated_at``); a source never tried, new ones included, sorts
    first."""
    sources = listing.get("sources") if isinstance(listing, dict) else None
    found = [s for s in (sources or [])[:MAX_SOURCES] if isinstance(s, dict)]
    # The source watcher (backend modules/watch.py) checks sources without a model: a watched source with an empty
    # inbox has nothing new, so it is left out; a source not watched yet is read as before.
    work = [s for s in found if not s.get("watched") or inbox_items(s)]
    if not work:
        return [] if found else [None]
    if len(work) <= SCOUT_PASS_SIZE:
        # One unnamed pass only when nothing is watched (as before the watcher): a watched source's pass is named,
        # so its inbox is acknowledged when the pass finishes.
        return [None] if not any(s.get("watched") for s in found) else [work]
    found = work
    # Last attempt, read or not (falling back to the last read): a source that keeps failing goes to the back
    # like one that was read, instead of taking the first pass every run (code review r15).
    found.sort(key=lambda s: str(s.get("tried_at") or s.get("read_at") or ""))  # stable: ties keep the listing order
    return [found[i : i + SCOUT_PASS_SIZE] for i in range(0, len(found), SCOUT_PASS_SIZE)]


def inbox_items(source: dict[str, Any]) -> list[dict[str, Any]]:
    """A source's new items from the watcher, as source_list gave them (each with its fingerprint ``fp``)."""
    inbox = source.get("inbox")
    return [i for i in inbox if isinstance(i, dict) and isinstance(i.get("fp"), str)] if isinstance(inbox, list) else []


def pass_note(batch: list[dict[str, Any]], number: int, total: int) -> str:
    """The run-message line that limits one pass to its own sources. Worded as a statement: with the
    instructions header, imperative lines here tripped the guardrail's prompt-attack filter (E2E F16c). Only ids
    and counts: the items' titles come from the pages, so they stay in source_list's fenced result."""
    parts = []
    for s in batch:
        sid = Defang.neutralize(str(s.get("id", "")), 40)
        n = len(inbox_items(s))
        parts.append(f"{sid} ({n} new item{'s' if n != 1 else ''} in its inbox)" if s.get("watched") else sid)
    return (
        f"THIS PASS ({number} of {total}) covers these sources: {', '.join(parts)}. The other sources belong to "
        "other passes."
    )


def instructions_block(listing: Any) -> str:
    """The run-message block that carries each source's administrator instruction, or ``""``."""
    sources = listing.get("sources") if isinstance(listing, dict) else None
    lines = []
    for source in (sources or [])[:MAX_SOURCES]:
        if not isinstance(source, dict) or not str(source.get("instruction") or "").strip():
            continue
        text = Defang.neutralize(source["instruction"], MAX_INSTRUCTION)
        lines.append(f"- source {Defang.neutralize(source.get('id', ''), 40)}: {text}")
    if not lines:
        return ""
    return "SOURCE NOTES (from the administrator, one per source):\n" + "\n".join(lines)


class SourceHook(HookProvider):
    def __init__(self, read_hosts: Callable[[], set[str]], now: Callable[[], datetime] | None = None) -> None:
        self._read_hosts = read_hosts
        self._now = now or (lambda: datetime.now(UTC))
        self.cursors: dict[str, str] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        if str(event.tool_use.get("name", "")) != "source_update_cursor":
            return
        args = event.tool_use.get("input")
        args = args if isinstance(args, dict) else {}
        cursor, reason = self.checked(str(args.get("cursor") or ""))
        if reason:
            event.cancel_tool = f"source_update_cursor refused: {reason}"
            return
        if cursor != args.get("cursor"):
            event.tool_use = {**event.tool_use, "input": {**args, "cursor": cursor}}
        self.cursors[str(args.get("source_id", ""))[:40]] = cursor

    def checked(self, cursor: str) -> tuple[str, str | None]:
        """The cursor to save, or why it is refused."""
        text = cursor.strip()
        try:
            at = parse_iso(text)
        except ValueError:
            pass
        else:
            return iso(min(at, self._now())), None  # a cursor in the future would skip every later item
        try:
            parts = urlsplit(text)
        except ValueError:
            parts = None
        if parts is not None and parts.scheme in ("http", "https") and parts.hostname:
            if parts.hostname.lower() in self._read_hosts():
                return text, None
            return "", "a cursor address must be an item this run read on that source"
        if _TOKEN.fullmatch(text):
            return text, None
        return "", (
            "a cursor is the RFC 3339 time of the newest item you processed, that item's address, or the "
            f"source's own page token (no spaces, at most {MAX_TOKEN} characters)"
        )

    def after_tool(self, event: AfterToolCallEvent) -> None:
        """``source_list`` without the administrator instructions, which the run message already carries.
        Registered after the fence, so it runs before it (``AfterToolCallEvent`` runs in reverse)."""
        if str(event.tool_use.get("name", "")) != "source_list" or not isinstance(event.result, dict):
            return
        content = []
        for block in event.result.get("content") or []:
            content.append(self._without_instructions(block))
        event.result = {**event.result, "content": content}

    @staticmethod
    def _without_instructions(block: Any) -> Any:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            return block
        try:
            listing = json.loads(block["text"])
        except ValueError:
            return block
        for source in (listing.get("sources") or []) if isinstance(listing, dict) else []:
            if isinstance(source, dict) and source.pop("instruction", None):
                source["instruction"] = "(in the run message)"
        return {**block, "text": json.dumps(listing, ensure_ascii=False)}

    def summary_lines(self) -> list[str]:
        """One line for every cursor the run moved: bookkeeping, so it reads after the run's own account
        (E2E F17: ten cursor lines led the summary and its one-line preview showed only the first)."""
        if not self.cursors:
            return []
        moved = "; ".join(f"{sid} to {Defang.neutralize(c, 120)}" for sid, c in self.cursors.items())
        return [f"cursors set for {len(self.cursors)} source(s): {moved}"]
