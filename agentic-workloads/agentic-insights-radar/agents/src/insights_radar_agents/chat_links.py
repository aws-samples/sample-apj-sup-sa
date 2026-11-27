# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Links in chat answers (A3-013).

Chat holds private text for an editor (product passages, gaps), reads graph text that agents wrote from
web pages, and writes links the web app renders as clickable. A poisoned evidence node can ask for a
"full report" link whose query carries what the model just read. So an absolute address in an answer
stays as written only when it is exactly a citation a tool of this turn returned (a node's
``evidence_url``, an entry's ``link``); any other is defanged (``hxxps://``, ``[.]``) before it streams
to the user, so it is no longer a link. Relative links into the app (``/insights/nodes/...``) are not
addresses and are kept.

The answer streams in chunks and an address can span two, so text is held back from the last
whitespace on and checked once the address is complete (or the turn ends).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry

from insights_radar_agents.output_guard import (
    _MAILTO,
    _SCHEME_URL,
    DEFANGED_SCHEME,
    defang_host,
    has_userinfo,
    normal_url,
)

log = logging.getLogger(__name__)

CITATION_KEYS = frozenset({"evidence_url", "link"})
MAX_HELD = 4000  # an answer that writes this much without a space is let through, checked as it is
MAX_WALK = 20
_SPACE = re.compile(r"\s")


class ChatLinkGuard(HookProvider):
    def __init__(self) -> None:
        self.cited: set[str] = set()
        self.defanged = 0
        self._held = ""

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event: AfterToolCallEvent) -> None:
        """Record the citations a tool returned. Registered after the fence, so it runs before it."""
        result = event.result if isinstance(event.result, dict) else {}
        for block in result.get("content") or []:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                try:
                    self._walk(json.loads(block["text"]), 0)
                except ValueError:
                    continue

    def _walk(self, value: Any, depth: int) -> None:
        if depth > MAX_WALK:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key in CITATION_KEYS and isinstance(item, str):
                    url = normal_url(item)
                    if url:
                        self.cited.add(url)
                else:
                    self._walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                self._walk(item, depth + 1)

    def clean(self, text: str) -> str:
        def one(match: re.Match[str]) -> str:
            whole = match.group(0)
            for candidate in (whole, whole.rstrip(".,;:!?)]*_")):
                if normal_url(candidate) in self.cited and not has_userinfo(match):
                    return whole
            self.defanged += 1
            special = match.group("special")
            scheme = (
                f"{DEFANGED_SCHEME[special.lower()]}:{match.group('sep')}"
                if special
                else f"{match.group('scheme')}[:]//"
            )
            return f"{scheme}{defang_host(match.group('host'))}{match.group('port') or ''}{match.group('rest')}"

        def mailto(match: re.Match[str]) -> str:
            # A citation is a web address, so no mailto: link in an answer is one: the address itself
            # carries data to whoever receives the mail (SR3-05).
            self.defanged += 1
            return f"{match.group('scheme')}[:]{match.group('user')}@{defang_host(match.group('host'))}"

        return _MAILTO.sub(mailto, _SCHEME_URL.sub(one, text))

    def feed(self, chunk: str) -> str:
        """The part of the answer so far that can be checked and sent: up to its last whitespace."""
        self._held += chunk
        cut = max((m.end() for m in _SPACE.finditer(self._held)), default=0)
        if cut == 0 and len(self._held) < MAX_HELD:
            return ""
        if cut == 0:
            cut = len(self._held)
        ready, self._held = self._held[:cut], self._held[cut:]
        return self.clean(ready)

    def flush(self) -> str:
        ready, self._held = self._held, ""
        out = self.clean(ready) if ready else ""
        if self.defanged:
            log.warning('{"event": "chat_links_defanged", "links": %d}', self.defanged)
        return out
