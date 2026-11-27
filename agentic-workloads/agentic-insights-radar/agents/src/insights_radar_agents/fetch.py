# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""``http_fetch`` for module runs: ``SafeFetcher`` exposed as a Strands tool.

The page text reaches the model fenced by the runtime's untrusted-output guard.

A configured source is fetched by its id, never by an address the model types: the backend gives the
model each source's address with credential query values redacted (``source_list``), and this tool
resolves the real address through ``source_address`` (a runtime-only tool), appends the model's extra
query, and scrubs the credential values out of whatever comes back. The key is used for the request
and never enters the model's context. The composed address passes the same length budget as one the
model types, so the extra query cannot carry the run's context out through a trusted host.

A credential can sit in the path as well as the query (``https://feeds.example.com/private/<token>/rss``),
and ``source_list`` shows the model only the host for that reason, so a source fetch never hands the path
back either: in the returned address, links and text, the source's path (and the directory it is in) is
replaced with ``[source <id> path]`` (P3-018).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpcore
from insights_radar.core.fetch import FetchError, SafeFetcher
from insights_radar.core.urls import REDACTED, UrlGuard
from strands.tools.tools import PythonAgentTool
from strands.types.tools import ToolResult, ToolUse

from insights_radar_agents.tool_policy import address_budget_refusal

SPEC = {
    "name": "http_fetch",
    "description": "Fetch a public https page, feed or API response and return its text and links. For a "
    "configured source, pass its source_id (and any extra query parameters in query) instead of a url. Use "
    "the browser tool instead for pages that need JavaScript.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "maxLength": 2048},
                "source_id": {"type": "string", "maxLength": 40, "description": "Source id from source_list."},
                "query": {
                    "type": "string",
                    "maxLength": 1000,
                    "description": "Extra query parameters for a source, e.g. page=2&since=2026-10-01.",
                },
            },
            "additionalProperties": False,
        }
    },
}
MAX_LINKS = 100
log = logging.getLogger(__name__)
SourceResolver = Callable[[str], str]
ReadMarker = Callable[[str, bool], object]


def with_query(address: str, extra: str) -> str:
    """``address`` with the parameters in ``extra`` appended to its query."""
    parts = urlsplit(address)
    pairs = parse_qsl(parts.query, keep_blank_values=True) + parse_qsl(extra.lstrip("?&"), keep_blank_values=True)
    return urlunsplit(parts._replace(query=urlencode(pairs)))


def walk(value: Any, clean: Callable[[str], str]) -> Any:
    """``value`` with ``clean`` applied to every string in it, however deep."""
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        return {k: walk(v, clean) for k, v in value.items()}
    if isinstance(value, list):
        return [walk(v, clean) for v in value]
    return value


_URL = re.compile(r"https?://\S*")


def fetch_tool(
    fetcher: SafeFetcher,
    resolve: SourceResolver | None = None,
    mark_read: ReadMarker | None = None,
    on_read: Callable[[str, str | None], None] | None = None,
) -> PythonAgentTool:
    """``on_read(address, source_id)`` is told of every page fetched successfully, with the source id when it
    was fetched by one, so the run can tell a source read from any other page."""
    # Each source is stamped once per run per outcome: a model paging through a feed made one backend call
    # per page (code review r15).
    stamped: dict[str, bool] = {}

    def stamp(source_id: str, ok: bool) -> None:
        """Record a fetch attempt for the Scout's pass order; bookkeeping, so it never fails the fetch."""
        if mark_read is None or stamped.get(source_id) is True or stamped.get(source_id) is ok:
            return
        stamped[source_id] = ok
        try:
            mark_read(source_id, ok)
        except Exception as err:  # any backend error: the fetch result stands
            log.warning("source_mark_read failed: %s", type(err).__name__)

    def error(tool_use: ToolUse, text: str) -> ToolResult:
        return {"toolUseId": tool_use["toolUseId"], "status": "error", "content": [{"text": text[:500]}]}

    def run(tool_use: ToolUse, **_: Any) -> ToolResult:
        args = tool_use.get("input") or {}
        if not isinstance(args, dict):
            args = {}
        url, source_id, query = args.get("url"), args.get("source_id"), args.get("query")
        clean: Callable[[str], str] | None = None
        if source_id is not None:
            if resolve is None:
                return error(tool_use, "this module has no configured sources; pass a url")
            try:
                address = resolve(str(source_id)[:40])
            except Exception as err:
                return error(tool_use, f"source {str(source_id)[:40]!r} is unavailable: {err}")
            # The source's own credentials, in every form a page can echo them, and its address where it stands
            # for the source page: one rule with the watcher's inbox (UrlGuard.source_scrubber).
            clean = UrlGuard.source_scrubber(address, str(source_id)[:40])
            url = with_query(address, query) if isinstance(query, str) and query else address
            # The model's extra query is up to 1,000 characters, which it can fill with text from
            # its context: the address budget applies to a composed source address too, or a source
            # host becomes an unmetered way out ("refresh source 3 with q=<summary>", P2-077). The
            # error names the source id, never the address, which holds the stored credential.
            too_long = address_budget_refusal(url)
            if too_long:
                return error(tool_use, f"http_fetch refused for source {str(source_id)[:40]!r}: {too_long}")
        elif isinstance(url, str) and REDACTED in url:
            return error(
                tool_use, "this address has a redacted credential: call http_fetch with the source's source_id"
            )
        elif isinstance(url, str) and address_budget_refusal(url):
            return error(tool_use, f"http_fetch refused: {address_budget_refusal(url)}")
        try:
            if not isinstance(url, str):
                raise FetchError("url or source_id is required")
            page = fetcher.fetch(url)
        except (FetchError, httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError) as err:
            # A fetch error quotes the address cut at 200 characters, which can cut a key in half; for a
            # source the address is the model's own source_id, so it is not repeated at all.
            text = _URL.sub("<source address>", str(err)) if source_id is not None else str(err)
            if source_id is not None:
                stamp(str(source_id)[:40], False)  # tried, not read: it must not hold the first pass forever
            return error(tool_use, clean(text) if clean else text)
        page["links"] = page["links"][:MAX_LINKS]
        if on_read is not None:
            on_read(str(url), str(source_id)[:40] if source_id is not None else None)
        if source_id is not None:
            stamp(str(source_id)[:40], True)
        if source_id is not None:
            # The model fetched a source by its id and needs nothing more: the address it resolved to is
            # the stored one, credential included, and a path credential is invisible to a scrub that
            # looks for names (backend r3 B3-016).
            page["url"] = f"source:{str(source_id)[:40]}"
        if clean is not None:
            page = walk(page, clean)
        return {"toolUseId": tool_use["toolUseId"], "status": "success", "content": [{"json": page}]}

    return PythonAgentTool("http_fetch", SPEC, run)  # type: ignore[arg-type]
