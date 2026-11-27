# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Limits on the runtime-side tools of a module run: web search, the browser, the code interpreter and
attached MCP servers.

These tools run inside the runtime and never pass the backend's tool gate, so this hook is their gate:

* **Per-run caps.** Token budgets meter model calls only; web search is billed per query, browser
  and code sessions per minute, and ``http_fetch`` is this solution's outbound GET channel. Each grant
  has a cap on calls per run, and a call over it is refused.
* **Live grants.** The backend re-checks every gated tool call against the module's current grants;
  these tools are checked here instead, against ``run_definition`` at most once a minute, so an
  administrator who removes a grant mid-run stops it within a minute rather than at the end of the run.
  A re-check the backend refuses (403, 404, or the run has ended) revokes every runtime-side grant, and
  so do ``MAX_RECHECK_ERRORS`` consecutive failures: the re-check fails closed, not open (P2-021).
* **Fail closed on an unknown tool.** A tool that is neither a backend tool (``tool_specs.SPECS``) nor
  registered here is refused, so a tool added to the runtime without a gate cannot run ungated.
* **Browser navigation.** Every address the *model* sends the browser to passes ``UrlGuard`` (public
  https only) and length limits on its query, path and host, so injected text cannot point the browser
  at an internal address or pack the run's context into a URL. Actions that let a page receive typed
  data or run script (``type``, ``press_key``, ``evaluate``, CDP, cookies, network interception) are
  refused. Every navigation is logged.

  What this does **not** check is where the page itself sends the browser: a ``click`` on a link,
  ``back``/``forward``, or a redirect the page starts (HTTP 30x, meta refresh, script) reaches any
  address, including ``http://`` and a host ``UrlGuard`` would refuse, and is not logged as a
  navigation (P2-022). The browser runs in an AWS-managed sandbox outside this account's network,
  so it has no private network to reach, and what it reads comes back fenced as untrusted output;
  the remaining risk is a request to an attacker's public address, which the address budgets bound
  only for addresses the model types.

* **Sessions.** Browser and Code Interpreter sessions are billed per minute and count against the
  account's concurrent-session quota. A run may have ``MAX_OPEN_SESSIONS`` open at once and open
  ``MAX_SESSIONS_PER_RUN`` in all; a session counts once the call that opens it succeeds (a refused
  ``init_session`` uses nothing, P3-062), and stops counting as open when the run closes it or its
  lifetime (``SESSION_SECONDS``) ends, so a long Scout run can still browse after its first sessions
  expired (A3-027). The code interpreter opens a session with ``initSession`` and also for any action
  on a session name it has not seen (P3-015).
* **Writes.** Backend writes are metered by their output tokens only, so a run that is told to record
  thousands of items could fill the graph within its budget. Each write tool has a per-run cap here
  (``WRITE_CAPS``) and the counts are in run_metrics; a run that reaches one logs
  ``write_cap_reached`` (A3-008).
* **Removed MCP servers.** The live re-check also reads the module's MCP servers: a tool from a server
  an administrator removed is refused within a minute, even while the ``mcp`` grant stays (A3-007).
* **Screenshots** are refused: the image never reaches the model (the fence drops images) and the
  tool writes the file wherever the model names (A3-004).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from insights_radar.core.urls import UrlGuard
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from insights_radar_agents.tool_api import ToolCallError
from insights_radar_agents.tool_specs import SPECS

log = logging.getLogger(__name__)

# Calls per run, per runtime-side grant. Web search is USD 0.007 a query, so 60 cost at most 0.42 a run.
CAPS = {"web_search": 60, "browser": 120, "code_interpreter": 30, "mcp": 200, "web_fetch": 120}
RECHECK_SECONDS = 60.0
# Tools the backend gates itself; anything else without a runtime-side grant is refused here.
BACKEND_TOOLS = frozenset(SPECS)
# Backend refusals that mean this run may no longer use any runtime-side tool either.
REVOKE_CODES = frozenset({"run_finished", "genai_disabled", "forbidden", "not_found"})
MAX_RECHECK_ERRORS = 3
BROWSER_ALLOWED_ACTIONS = frozenset(
    {
        "init_session",
        "list_local_sessions",
        "navigate",
        "click",
        "get_text",
        "get_html",
        "refresh",
        "back",
        "forward",
        "new_tab",
        "switch_tab",
        "close_tab",
        "list_tabs",
        "close",
    }
)
# AgentCore Browser and Code Interpreter sessions are billed per minute and count against the
# account's concurrent-session quota, and each lives until its timeout whatever the run does (P2-023).
MAX_OPEN_SESSIONS = 2
MAX_SESSIONS_PER_RUN = 8
SESSION_SECONDS = 900.0  # connectors.BROWSER_SESSION_SECONDS and CODE_SESSION_SECONDS
# Backend writes per run. Generous for a Scout crawling every source, and far below what a budget buys.
WRITE_CAPS = {
    "kg_upsert_entity": 300,
    "kg_add_evidence": 400,
    "kg_link": 800,
    "kg_save_output": 60,
    "kg_update_output": 120,
    "radar_suggest": 60,
    "draft_save": 3,
}
MAX_URL_CHARS = 2048
# Data can ride in any part of an address, not just the query: the path (/aGVsbG8.../x) and DNS labels
# (<data>.attacker.example) get a budget too. This only slows a leak down; the real guarantee is that
# modules with these tools never see private data (the backend's private-writer rule).
MAX_QUERY_CHARS = 512
MAX_PATH_CHARS = 512
MAX_HOST_CHARS = 100


def address_budget_refusal(url: str) -> str | None:
    """Why an address an agent typed is too long to send, or None."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None  # UrlGuard reports the malformed address
    if (
        len(url) > MAX_URL_CHARS
        or len(parts.query) > MAX_QUERY_CHARS
        or len(parts.path) > MAX_PATH_CHARS
        or len(parts.netloc) > MAX_HOST_CHARS
    ):
        return "the address is too long (a long query, path or host name can carry data out)"
    return None


def _action(grant: str, tool_input: Any) -> dict[str, Any] | None:
    """The browser or code interpreter action of a call, or None."""
    if grant not in ("browser", "code_interpreter"):
        return None
    raw = tool_input if isinstance(tool_input, dict) else {}
    for value in raw.values():  # browser_input / code_interpreter_input, shape set by the tool
        action = value.get("action") if isinstance(value, dict) else None
        if isinstance(action, dict):
            return action
    return None


def _starts_a_session(grant: str, tool_input: Any) -> bool:
    """Whether this call explicitly asks the browser or the code interpreter for a new session."""
    action = _action(grant, tool_input)
    return action is not None and str(action.get("type", "")) in ("init_session", "initSession")


@dataclass
class LiveDefinition:
    """What the backend's copy of the module allows now: its grants and, when known, the addresses of its
    MCP servers."""

    grants: set[str]
    mcp_servers: set[str] | None = None


@dataclass
class _Sessions:
    open: dict[str, float] = field(default_factory=dict)  # session name -> when it opened
    opened: int = 0


def browser_refusal(tool_input: Any) -> str | None:
    """Why a browser call is refused, or None to allow it."""
    raw = tool_input.get("browser_input") if isinstance(tool_input, dict) else None
    action = raw.get("action") if isinstance(raw, dict) else None
    if not isinstance(action, dict):
        return None  # the tool itself rejects a malformed call
    kind = str(action.get("type", ""))
    if kind not in BROWSER_ALLOWED_ACTIONS:
        return f"the browser action {kind!r} is not allowed in module runs"
    url = action.get("url")
    if url is None:
        return None
    url = str(url)
    too_long = address_budget_refusal(url)
    if too_long:
        return too_long
    reason = UrlGuard.reason(url)
    if reason:
        return f"the browser may only open public https addresses: {reason}"
    return None


class RuntimeToolPolicy(HookProvider):
    def __init__(
        self,
        grant_of: dict[str, str],
        current: Callable[[], LiveDefinition | set[str]],
        clock: Callable[[], float] = time.monotonic,
        caps: dict[str, int] | None = None,
        server_of: dict[str, str] | None = None,
        refused_tools: dict[str, str] | None = None,
    ) -> None:
        """``grant_of`` maps each runtime-side tool name to the grant that provides it, ``server_of`` each MCP
        tool to the address of its server, and ``refused_tools`` names backend tools this run may not call
        whatever its grants say, with the reason."""
        self._grant_of = grant_of
        self._current = current
        self._clock = clock
        self._caps = caps or CAPS
        self._server_of = {name: server for name, server in (server_of or {}).items() if server}
        self._refused_tools = refused_tools or {}
        self._checked_at: float | None = None
        self._revoked: set[str] = set()
        self._gone_servers: set[str] = set()
        self._errors = 0
        self._sessions: dict[str, _Sessions] = {}
        self._pending: dict[str, tuple[str, str]] = {}  # toolUseId -> (grant, session) of an opening call
        self.calls: dict[str, int] = {}
        self.refused: dict[str, int] = {}
        self.writes: dict[str, int] = {}

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        name = str(event.tool_use.get("name", ""))
        grant = self._grant_of.get(name)
        if grant is None:
            if name in BACKEND_TOOLS:
                self._backend_tool(event, name)  # the backend checks the grant itself
                return
            self._refuse(event, name, "unknown", "it is not a tool this run may use")
            return
        reason = self._refusal(name, grant, event.tool_use.get("input"), str(event.tool_use.get("toolUseId", "")))
        if reason:
            self._refuse(event, name, grant, reason)
            return
        self.calls[grant] = self.calls.get(grant, 0) + 1

    def after_tool(self, event: AfterToolCallEvent) -> None:
        """A session counts once the call that opens it succeeded; a closed one stops counting as open."""
        use = event.tool_use
        pending = self._pending.pop(str(use.get("toolUseId", "")), None)
        result = event.result if isinstance(event.result, dict) else {}
        if result.get("status") != "success":
            return
        if pending is not None:
            grant, session = pending
            sessions = self._sessions.setdefault(grant, _Sessions())
            sessions.open[session] = self._clock()
            sessions.opened += 1
            return
        grant = self._grant_of.get(str(use.get("name", "")), "")
        action = _action(grant, use.get("input"))
        if grant == "browser" and action is not None and action.get("type") == "close":
            self._sessions.setdefault(grant, _Sessions()).open.pop(str(action.get("session_name") or ""), None)

    def _backend_tool(self, event: BeforeToolCallEvent, name: str) -> None:
        if name in self._refused_tools:
            self._refuse(event, name, "write", self._refused_tools[name])
            return
        cap = WRITE_CAPS.get(name)
        if cap is None:
            return
        if self.writes.get(name, 0) >= cap:
            if self.refused.get("write", 0) == 0:
                log.warning(json.dumps({"event": "write_cap_reached", "tool": name, "cap": cap}))
            self._refuse(event, name, "write", f"this run has made its {cap} {name} calls; finish with what you have")
            return
        self.writes[name] = self.writes.get(name, 0) + 1

    def _refuse(self, event: BeforeToolCallEvent, name: str, grant: str, reason: str) -> None:
        self.refused[grant] = self.refused.get(grant, 0) + 1
        log.warning(json.dumps({"event": "tool_refused", "tool": name, "grant": grant, "reason": reason[:200]}))
        event.cancel_tool = f"{name} refused: {reason}."

    def _refusal(self, name: str, grant: str, tool_input: Any, tool_use_id: str) -> str | None:
        if self.calls.get(grant, 0) >= self._caps.get(grant, 0):
            return f"this run has used its {self._caps.get(grant, 0)} {grant} calls"
        if self._is_revoked(grant):
            return f"the module is no longer granted {grant}"
        if self._server_of.get(name) in self._gone_servers:
            return "its MCP server is no longer attached to the module"
        if grant == "browser":
            reason = browser_refusal(tool_input)
            if reason:
                return reason
        reason = self._session_refusal(grant, tool_input, tool_use_id)
        if reason:
            return reason
        action = _action(grant, tool_input)
        if grant == "browser" and action is not None and action.get("url"):
            parts = urlsplit(str(action["url"]))
            digest = hashlib.sha256(str(action["url"]).encode("utf-8", "replace")).hexdigest()[:32]
            log.info(
                json.dumps(
                    {
                        "event": "browser_navigate",
                        "host": parts.hostname,
                        "path": parts.path[:200],
                        "url_sha256": digest,
                    }
                )
            )
        return None

    def _session_refusal(self, grant: str, tool_input: Any, tool_use_id: str) -> str | None:
        action = _action(grant, tool_input)
        if action is None:
            return None
        sessions = self._sessions.setdefault(grant, _Sessions())
        now = self._clock()
        sessions.open = {k: at for k, at in sessions.open.items() if now - at < SESSION_SECONDS}
        kind = str(action.get("type", ""))
        session = str(action.get("session_name") or "default")
        starts = kind in ("init_session", "initSession") or (
            grant == "code_interpreter" and kind != "listLocalSessions" and session not in sessions.open
        )
        if not starts:
            return None
        pending = sum(1 for g, _ in self._pending.values() if g == grant)
        if len(sessions.open) + pending >= MAX_OPEN_SESSIONS:
            return f"this run has {MAX_OPEN_SESSIONS} {grant} sessions open; use one of them"
        if sessions.opened + pending >= MAX_SESSIONS_PER_RUN:
            return f"this run has opened its {MAX_SESSIONS_PER_RUN} {grant} sessions"
        self._pending[tool_use_id] = (grant, session)
        return None

    def _is_revoked(self, grant: str) -> bool:
        now = self._clock()
        if self._checked_at is None or now - self._checked_at >= RECHECK_SECONDS:
            self._checked_at = now
            self._recheck()
        return grant in self._revoked

    def _recheck(self) -> None:
        try:
            current = self._current()
        except ToolCallError as err:
            # The module is gone, the token no longer grants the lookup, or the run has ended: there is
            # nothing left to permit, so every runtime-side grant goes rather than the last answer staying.
            if err.status in (403, 404) or err.code in REVOKE_CODES:
                self._revoke_all(f"{err.status} {err.code}")
            else:
                self._count_error(f"{err.status} {err.code}")
        except Exception as err:
            self._count_error(type(err).__name__)
        else:
            live = current if isinstance(current, LiveDefinition) else LiveDefinition(set(current))
            self._errors = 0
            self._revoked = {g for g in self._grant_of.values() if g not in live.grants}
            if live.mcp_servers is not None:
                self._gone_servers = {s for s in self._server_of.values() if s not in live.mcp_servers}

    def _count_error(self, why: str) -> None:
        self._errors += 1
        log.warning("could not re-check the module's grants (%d in a row): %s", self._errors, why)
        if self._errors >= MAX_RECHECK_ERRORS:
            self._revoke_all(f"{self._errors} failed grant re-checks")

    def _revoke_all(self, why: str) -> None:
        log.warning(json.dumps({"event": "grants_revoked", "reason": why[:200]}))
        self._errors = 0
        self._revoked = set(self._grant_of.values())
