# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The generic module runtime: one AgentCore runtime runs every module, built-in or custom.

Per run it:

1. reads ``run_context`` with the run token (cursor, product profile, granted tools);
2. builds only the granted tools, the system prompt for the module's template and the model;
3. runs the agent with two hooks: ``UntrustedOutputGuard`` (fences tool output) and ``RunGuard``
   (stops the run when the budget left at the start is used up, or when the kill switch trips);
4. reports the outcome, including token usage, so the backend can finalise the run and charge
   the budget. Usage is read from the agent's own metrics even when the run crashes, so a
   failing module is still billed for what it spent.

Step Functions starts a run with ``.waitForTaskToken``: ``ModuleApp`` returns at once, runs the
agent on a worker thread, heartbeats the task token and reports the outcome with
``SendTaskSuccess``. A failed run is still reported as task *success* carrying
``status=failed`` and its usage; ``SendTaskFailure`` (which carries no usage) is used only when
no outcome could be produced at all.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlparse

from insights_radar.core.defang import Defang
from insights_radar.core.fetch import SafeFetcher
from insights_radar.core.tokens import TokenUsage, TokenWeights
from strands import Agent
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    HookProvider,
    HookRegistry,
)

from insights_radar_agents.connectors import Connectors
from insights_radar_agents.executor import WritesLastExecutor
from insights_radar_agents.fetch import fetch_tool
from insights_radar_agents.guard import UntrustedOutputGuard
from insights_radar_agents.models import DRAFT_MAX_TOKENS, MODULE_MAX_TOKENS, ModelFactory
from insights_radar_agents.output_guard import OutputAddressGuard
from insights_radar_agents.prompts import PromptBuilder
from insights_radar_agents.remote_tools import MAX_CREATED_ENTITIES, WRITE_TOOLS, RemoteToolFactory, WriteLedger
from insights_radar_agents.retry import TransientRetry
from insights_radar_agents.sources import SourceHook, inbox_items, instructions_block, pass_note, scout_passes
from insights_radar_agents.tool_api import ToolApi, ToolCallError
from insights_radar_agents.tool_policy import LiveDefinition, RuntimeToolPolicy

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 60.0


def usage_of(metrics: Any) -> TokenUsage:
    raw = getattr(metrics, "accumulated_usage", None) or {}
    return TokenUsage(
        int(raw.get("inputTokens", 0) or 0),
        int(raw.get("outputTokens", 0) or 0),
        int(raw.get("cacheReadInputTokens", 0) or 0),
        int(raw.get("cacheWriteInputTokens", 0) or 0),
    )


# The input keys that say what a call was about, in order of preference. Free text (a search query) is
# left out: it is model-written and may be the very text the guardrail just blocked.
_TARGET_KEYS = ("source_id", "url", "address", "id")


def call_label(name: str, args: Any) -> str:
    """A short, safe name for a tool call: the tool and what it was about. An address is cut to its host:
    the label goes into the run summary, and a fetched page's full address may carry anything."""
    target = ""
    if isinstance(args, dict):
        key = next((k for k in _TARGET_KEYS if isinstance(args.get(k), str) and args[k].strip()), None)
        if key is not None:
            value = args[key].strip()
            target = (urlparse(value).hostname or "") if key in ("url", "address") else value
    label = Defang.neutralize(name, 64)
    return f"{label} ({Defang.neutralize(target, 80)})" if target else label


def message_text(result: Any) -> str:
    """Text blocks of the agent's final message (not the message dict's repr)."""
    message = getattr(result, "message", None) or {}
    content = message.get("content", []) if isinstance(message, dict) else []
    return "\n".join(str(b["text"]) for b in content if isinstance(b, dict) and "text" in b).strip()


CHARS_PER_TOKEN = 4


def address_note(
    summary: str, defanged: int, links: int = 0, notes: list[str] | None = None, after: list[str] | None = None
) -> str:
    """The run summary, after the runtime's own lines: how many addresses the output guard defanged (an
    injection attempt belongs in the summary the operator reads, never in a published insight, L2-001),
    and anything else the operator must see. The runtime lines come first, because the summary is cut at
    ``MAX_SUMMARY`` characters and a long model summary would otherwise cut them off (P3-065). ``after`` is
    bookkeeping (cursors moved): it follows the run's own text, so the summary's first line says what the
    run did (E2E F17)."""
    lines = []
    if defanged:
        lines.append(
            f"[runtime: {defanged} address(es) this run never read were defanged in its writes, {links} of them "
            "links. Addresses taken from fetched content belong in this summary, not in a published output.]"
        )
    lines.extend(f"[runtime: {note}]" for note in notes or [])
    tail = [f"[runtime: {note}]" for note in after or []]
    return "\n".join([*lines, *([summary] if summary else []), *tail]).strip()


MAX_SUMMARY = 4000
# What the backend charges a metered call (insights_radar.api.agent_tools.METERED_TOOLS), in weighted
# tokens. The model's usage does not include it, so the run guard adds it to what the run spent (P3-087).
METERED_TOOLS = {"kg_search": 2_000, "kg_neighbors": 1_000}
# Writes a Drafts run may not make whatever its grants say: a draft changes only through draft_save, which
# is bound to the idea the run was started for (A3-023).
DRAFT_REFUSED = {
    "kg_save_output": "a Drafts run saves its draft with draft_save",
    "kg_update_output": "a Drafts run changes drafts only with draft_save",
}


STATE_CHECK_SECONDS = 60.0
# The Bedrock guardrail's canned reply when it blocks a model turn.
GUARDRAIL_BLOCKED_TEXT = "blocked by a safety guardrail"


def guardrail_blocked(result: Any, text: str) -> bool:
    """The run ended on a guardrail intervention: Strands' stop reason, or the guardrail's canned reply as the
    run's last text."""
    return getattr(result, "stop_reason", None) == "guardrail_intervened" or GUARDRAIL_BLOCKED_TEXT in text.lower()


# Calls a run makes to keep its own books, whatever its work: they say nothing about whether it did any.
BOOKKEEPING_TOOLS = frozenset(
    {"run_context", "run_definition", "budget_status", "progress_update", "source_update_cursor", "kg_schema"}
)


def ack_inbox(api: Any, batch: list[dict[str, Any]]) -> None:
    """Clear the inbox items of a finished pass, source by source; a failure leaves them for the next run."""
    for source in batch:
        fps = [str(i["fp"]) for i in inbox_items(source)]
        if not fps:
            continue
        try:
            api.call("source_inbox_ack", {"source_id": str(source.get("id", "")), "fps": fps})
        except Exception as err:  # bookkeeping: the items are only read again
            log.warning("source_inbox_ack failed: %s", type(err).__name__)


class ToolTally(HookProvider):
    """Counts a run's work tool calls by outcome: refused (by the runtime's own gate, or by the backend as
    ``refused_by_backend`` reports), succeeded, or failed some other way (a timeout, a bad argument).

    A refusal reaches the model as a tool error, so a run whose every call was refused (the grant removed
    mid-run, a module that may not run) ended normally and was recorded "succeeded" having done nothing; the
    refusals were only in the logs (HC-139). The runner fails such a run and names partial refusals."""

    def __init__(self, refused_by_backend: Callable[[str], bool]) -> None:
        self._refused_by_backend = refused_by_backend
        self.succeeded = 0
        self.refused = 0
        self.failed = 0
        self.refused_tools: list[str] = []
        self.last_call = ""  # the latest call of any kind, named for a pass that a guardrail block ends

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event: AfterToolCallEvent) -> None:
        use = getattr(event, "tool_use", None) or {}
        name = str(use.get("name", ""))
        # Every call counts for the label, bookkeeping too: a run_definition result carries the module's
        # own prompt, and the guardrail may block on it (E2E F16b: a pass-1 block named no call).
        self.last_call = call_label(name, use.get("input"))
        if name in BOOKKEEPING_TOOLS:
            return
        result = event.result if isinstance(event.result, dict) else {}
        if getattr(event, "cancel_message", None) or self._refused_by_backend(str(use.get("toolUseId", ""))):
            self.refused += 1
            if name not in self.refused_tools:
                self.refused_tools.append(name)
        elif result.get("status") == "success":
            self.succeeded += 1
        else:
            self.failed += 1

    def _named(self) -> str:
        return ", ".join(self.refused_tools[:10])

    def denied(self) -> str | None:
        """The run's error when calls were refused and none succeeded, or None."""
        if not self.refused or self.succeeded:
            return None
        return (
            f"ToolPolicyDenied: {self.refused} of {self.refused + self.failed} tool calls were refused and "
            f"none succeeded ({self._named()})"
        )

    def note(self) -> list[str]:
        """The summary line for a run some of whose calls were refused while others ran."""
        if not self.refused or not self.succeeded:
            return []
        total = self.refused + self.succeeded + self.failed
        return [f"{self.refused} of {total} tool calls were refused ({self._named()}); the rest ran"]


# Error codes with which Bedrock refuses a request before any inference runs: no tokens are billed. A throttled
# request is refused too: ThrottlingException (capitalised) answers the call itself, while a stream that fails
# part-way reports a lower-case event code such as "throttlingException" (round 46 code review).
REFUSED_CODES = frozenset(
    {
        "ValidationException",
        "AccessDeniedException",
        "ResourceNotFoundException",
        "UnrecognizedClientException",
        "ThrottlingException",
    }
)


def causes(error: BaseException) -> list[BaseException]:
    """``error`` and its explicit causes, at most four. Strands re-raises some errors as its own exceptions
    ``from`` the botocore one ("Input is too long" becomes a ContextWindowOverflowException, a throttle a
    ModelThrottledException; round 44). Never ``__context__``: Strands retries inside its ``except``, so a
    later error carries an earlier one as its context (round 45)."""
    chain: list[BaseException] = []
    seen: BaseException | None = error
    while seen is not None and len(chain) < 4:
        chain.append(seen)
        seen = seen.__cause__
    return chain


def error_codes(error: BaseException) -> list[str]:
    """The botocore error codes on ``error`` and its explicit causes."""
    codes: list[str] = []
    for seen in causes(error):
        response = getattr(seen, "response", None)
        code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
        if isinstance(code, str):
            codes.append(code)
    return codes


# A call that never reached Bedrock: the connection was never made (round 48 code review).
_UNCONNECTED_NAMES = ("ConnectTimeout", "EndpointConnection", "NewConnectionError")


def refused_before_inference(error: BaseException) -> bool:
    """Whether a failed model call never ran: Bedrock refused it as a request (one of ``REFUSED_CODES``), or the
    connection to Bedrock was never made. Either way nothing is billed."""
    if any(code in REFUSED_CODES for code in error_codes(error)):
        return True
    return any(part in type(seen).__name__ for seen in causes(error) for part in _UNCONNECTED_NAMES)


# Failures after the response began: the stream broke, or the connection was read from and then dropped or timed
# out (botocore ReadTimeoutError, ResponseStreamingError, ConnectionClosedError; urllib3 ProtocolError and
# IncompleteRead), none of which has "Stream" in its name (round 47 code review).
_MID_STREAM_NAMES = ("Stream", "ReadTimeout", "ConnectionClosed", "ProtocolError", "IncompleteRead")


def failed_mid_stream(error: BaseException) -> bool:
    """A call that failed after its response began, so it ran and may have been billed: by its own class or a
    cause's, or by a lower-case event-stream code. Only its estimated input is counted: output generated before
    the break is not known (an accepted limit, round 48)."""
    if any(part in type(seen).__name__ for seen in causes(error) for part in _MID_STREAM_NAMES):
        return True
    return any(code[:1].islower() for code in error_codes(error))


@dataclass
class RunGuard(HookProvider):
    """Hard stop for an agent: the budget left at the start, a cap on model calls, the stop codes the
    backend returns (kill switch, budget) as reported by ``stop_signal``, and the live run state read
    from ``live_stop`` before a model call, at most once every ``STATE_CHECK_SECONDS``. A model call is
    made only while its estimated input plus ``call_reserve`` (what its output may cost) still fits, so
    the last call cannot overshoot. A call that starts but never reports usage (the client left
    mid-stream) is charged at its estimated input by ``billed``.

    ``live_stop`` is what stops a run that only uses runtime-side tools (web search, the browser, the
    code interpreter, MCP, ``http_fetch``) or is writing a long final answer: none of those reaches the
    backend's tool gate, so ``stop_signal`` stays empty however long the run goes on (P2-002)."""

    budget_remaining: int
    call_reserve: int = 0
    # The run's model tier, as finalize charges it: ``budget_remaining`` comes from the tiered ledger,
    # so spending it at the balanced rate let a max-tier run use about four times its budget and stopped
    # a fast-tier one at about a third of it (CR2-05).
    weights: TokenWeights = field(default_factory=TokenWeights)
    stop_reason: str | None = None
    turns: int = 0
    max_turns: int = 200
    stop_signal: Callable[[], str | None] = lambda: None
    live_stop: Callable[[], str | None] = lambda: None
    clock: Callable[[], float] = time.monotonic
    checked_at: float | None = None
    cancel_message: str = "Run stopped: {reason}."
    in_flight_input: int = 0  # estimated input of a call that has started and not yet reported usage
    failed_input: int = 0  # estimated input of calls that failed mid-stream (Strands reports no usage)
    metered: int = 0  # weighted tokens the backend charged for metered tool calls (P3-087)
    prior: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0, 0, 0))  # earlier passes' usage (HC-111)
    # Calls this guard let through to the model. 0 is the proof that a run spent nothing; a call the guard
    # cancelled itself never reached the model and is not counted (code review r30).
    admitted: int = 0
    cancelled_call: bool = False

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def poll_state(self) -> None:
        """Re-read the kill switch and the budget from the backend, at most once a minute."""
        if self.stop_reason is not None:
            return
        now = self.clock()
        if self.checked_at is not None and now - self.checked_at < STATE_CHECK_SECONDS:
            return
        self.checked_at = now
        self.stop_reason = self.live_stop()

    def before_model(self, event: BeforeModelCallEvent) -> None:
        self.poll_state()
        weights = self.weights
        used = usage_of(getattr(event.agent, "event_loop_metrics", None)) + self.prior
        spent = weights.weigh(self.billed(used)) + self.metered
        estimate = self.estimate_input(event.agent)
        upcoming = weights.weigh(TokenUsage(estimate, 0, 0, 0)) + self.call_reserve
        if self.stop_reason is None and spent + upcoming >= self.budget_remaining:
            self.stop_reason = "budget_exhausted"
        if self.stop_reason is None and self.turns >= self.max_turns:  # turns counts completed calls
            self.stop_reason = "turn_limit"
        if self.stop_reason:
            event.cancel = self.cancel_message.format(reason=self.stop_reason)
            self.cancelled_call = True
        else:
            self.in_flight_input = estimate
            self.admitted += 1

    def after_model(self, event: AfterModelCallEvent) -> None:
        if self.cancelled_call:
            # Strands reports the call this guard cancelled as finished without an error: it never ran.
            self.cancelled_call = False
            return
        error = getattr(event, "exception", None)
        if error is not None and refused_before_inference(error):
            # Bedrock rejected the request itself (a deleted guardrail, a bad model id, no access, a throttle)
            # or it was never reached: nothing ran and nothing is billed, so it is no call. Counting it booked
            # each failed run's whole hold, and after a deployment's guardrail was deleted two failed runs used up
            # Signal's day (a deployment, 2026-11-05).
            self.admitted -= 1
        elif error is None:
            self.turns += 1  # its usage is in the metrics now
        elif failed_mid_stream(error):
            # It ran and may have been billed, and its retry adds nothing to the metrics, so its estimated
            # input is counted here (output generated before the failure is not known). Any other failure
            # stays counted as a call: it is no proof that nothing was spent.
            self.failed_input += self.in_flight_input
        self.in_flight_input = 0

    def billed(self, usage: TokenUsage) -> TokenUsage:
        """``usage`` plus the estimated input of calls that started and never reported usage: one still in
        flight, and any that failed mid-stream."""
        return replace(usage, input_tokens=usage.input_tokens + self.in_flight_input + self.failed_input)

    @staticmethod
    def estimate_input(agent: Any) -> int:
        """The next call's input in tokens, at about four characters a token over the system prompt and
        the conversation (tool results included) and the tool definitions sent with every call. Binary
        content counts by its text form, which only overestimates."""
        text = str(getattr(agent, "system_prompt", "") or "")
        text += json.dumps(getattr(agent, "messages", None) or [], default=str)
        registry = getattr(agent, "tool_registry", None)
        if registry is not None:
            text += json.dumps(registry.get_all_tool_specs(), default=str)
        return len(text) // CHARS_PER_TOKEN

    def after_tool(self, event: AfterToolCallEvent) -> None:
        """A stop code a backend tool returned, the charge of a metered call, and the live run state: a
        tool call can take minutes (an MCP server, a slow page), and the kill switch must not wait for the
        next model call to be read (A3-003)."""
        use = getattr(event, "tool_use", None) or {}
        self.metered += METERED_TOOLS.get(str(use.get("name", "")), 0)
        code = self.stop_signal()
        if code and self.stop_reason is None:
            self.stop_reason = code
        self.poll_state()


@dataclass
class RunOutcome:
    status: str
    summary: str = ""
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0, 0, 0))
    nodes_written: int = 0
    error: str | None = None
    # How many model calls the guard let through (attempts, not completions), when the runtime knows; 0 is the
    # proof that a run with no usage spent nothing (the fast path with no new items), so the backend gives back
    # its hold. A request Bedrock refused before it ran (REFUSED_CODES, a throttle included) is not counted.
    model_calls: int | None = None
    # The entities this run created (kg_upsert_entity answered created: true) and named in no later write, sent with
    # every outcome: for a run that ended early, finalize removes the ones still unlinked. A run killed before it
    # could report (a task timeout) sends none; the admin Graph hygiene view lists what that leaves.
    created_entities: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            **({} if self.model_calls is None else {"model_calls": self.model_calls}),
            "created_entities": self.created_entities[:MAX_CREATED_ENTITIES],
            "status": self.status,
            "summary": self.summary[:MAX_SUMMARY],
            "nodes_written": self.nodes_written,
            "error": self.error,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "cache_read_tokens": self.usage.cache_read_tokens,
                "cache_write_tokens": self.usage.cache_write_tokens,
            },
        }


AgentBuilder = Callable[..., Any]


class ModuleRunner:
    def __init__(
        self,
        api_factory: Callable[[str], ToolApi],
        models: ModelFactory,
        connectors: Connectors,
        prompts: PromptBuilder | None = None,
        fetcher_factory: Callable[[], SafeFetcher] = SafeFetcher,
        agent_builder: AgentBuilder = Agent,
    ) -> None:
        self._api_factory = api_factory
        self._models = models
        self._connectors = connectors
        self._prompts = prompts or PromptBuilder()
        self._fetcher_factory = fetcher_factory
        self._agent_builder = agent_builder

    def run(self, payload: dict[str, Any]) -> RunOutcome:
        """Run once, then log one line of run metrics (tool calls by name, refusals, fenced results,
        defanged addresses, model calls) as JSON. These are not CloudWatch metrics: the stack has one
        metric filter, on the workflow log, for run timeouts. Read them with Logs Insights, for
        example ``filter @message like /run_metrics/`` on the module runtime's log group (P2-027)."""
        metrics: dict[str, Any] = {}
        ledger = WriteLedger()
        outcome = self._run(payload, metrics, ledger)
        # Set here, not at each return of _run: every outcome, an early stop or a crash included, reports them.
        outcome.created_entities = ledger.unreferenced  # created and named by no later write
        metrics["created_entities"] = len(ledger.created)
        module = payload.get("module") or {}
        log.info(
            json.dumps(
                {
                    "event": "run_metrics",
                    "module": str(module.get("slug", ""))[:64],
                    "run_id": str(payload.get("run_id", ""))[:64],
                    "status": outcome.status,
                    "nodes_written": outcome.nodes_written,
                    **metrics,
                },
                default=str,
            )
        )
        return outcome

    def _run(self, payload: dict[str, Any], metrics: dict[str, Any], ledger: WriteLedger | None = None) -> RunOutcome:
        module = payload.get("module") or {}
        api = self._api_factory(str(payload.get("run_token", "")))
        agent: Any = None
        guard: RunGuard | None = None
        factory = RemoteToolFactory(api, ledger)
        fence = UntrustedOutputGuard()
        addresses = OutputAddressGuard()
        policy: RuntimeToolPolicy | None = None
        sources = SourceHook(lambda: addresses.read_hosts)
        tally = ToolTally(lambda tool_use_id: tool_use_id in factory.refused)
        unavailable: list[str] = []
        try:
            context = api.call("run_context", {})
            # Grants, MCP servers, instructions and model come from the backend's copy of the module,
            # not the invocation payload, which is not signed. run_definition is never given to the
            # model: the definition holds MCP server addresses, which can embed a credential.
            module = api.call("run_definition", {}).get("definition")
            if not isinstance(module, dict) or not module:  # fail closed: never trust the unsigned payload
                return RunOutcome("failed", error="the backend sent no module definition", model_calls=0)
            budget = api.call("budget_status", {})
            if not budget.get("genai_enabled", True):
                return RunOutcome("cancelled", "GenAI is paused by an administrator.", model_calls=0)
            if budget.get("state") == "closed":
                return RunOutcome("budget_exhausted", "Daily token budget is used up.", model_calls=0)
            granted = set(context.get("tools") or [])
            grants = set(module.get("grants") or [])
            weights = TokenWeights.for_tier(str(budget.get("tier") or "balanced"))
            guard = RunGuard(
                int(budget.get("remaining", 0)),
                # one full answer at the output weight, at the run's tier
                call_reserve=weights.weigh(TokenUsage(0, max_tokens_for(module), 0, 0)),
                weights=weights,
                stop_signal=lambda: factory.stop_code,
                live_stop=lambda: live_stop_code(api),
            )
            # The administrator's per-source instructions go in the run message, not inside a fence (A3-006).
            listing = api.call("source_list", {}) if "source_list" in granted else {}
            passes = scout_passes(listing) if module.get("template") == "scout" else [None]
            # Whether the run read one of its sources: a page fetched by source id, or one of a watched source's
            # inbox items by its address. Any other page (a link it followed) does not count (code review r51).
            # A read of a source is a fetch by its id, or of its listed address or one of its inbox items, compared
            # without the host's case, a trailing slash or a fragment (code review r53). A link it followed on the same
            # site is not (r54); a source read only through some other page of its site is not counted either.
            source_pages = {
                page
                for batch in passes
                for source in batch or []
                # The listed address is the source's own page only when it was not cut to its origin.
                for page in (
                    _page(None if source.get("address_redacted") else source.get("address")),
                    *(_page(item.get("url")) for item in inbox_items(source)),
                )
                if page
            }
            read_a_source = False

            def on_read(address: str, source_id: str | None) -> None:
                nonlocal read_a_source
                read_a_source = read_a_source or source_id is not None or _page(address) in source_pages

            with ExitStack() as stack:
                tools: list[Any] = factory.build(granted)
                # Runtime-side tools never reach the backend's tool gate: the policy hook is theirs.
                runtime_side: dict[str, str] = {}

                def add(grant: str, found: list[Any]) -> None:
                    if not found:
                        unavailable.append(grant)  # granted, but it could not be loaded (A3-009)
                    for tool in found:
                        runtime_side[str(getattr(tool, "tool_name", ""))] = grant
                    tools.extend(found)

                if "http_fetch" in granted:
                    fetcher = self._fetcher_factory()
                    stack.callback(fetcher.close)

                    # A source is fetched by id: the runtime, not the model, holds its real address. It counts
                    # as read once the fetch succeeds, not when it is resolved (P3-020).
                    def resolve_source(source_id: str) -> str:
                        return str(api.call("source_address", {"source_id": source_id}).get("address", ""))

                    resolve = resolve_source if "source_address" in granted else None

                    def mark_read(source_id: str, ok: bool) -> object:
                        return api.call("source_mark_read", {"source_id": source_id, "ok": ok})

                    marker = mark_read if "source_mark_read" in granted else None
                    # http_fetch runs here, not at the backend, so it needs the runtime-side gate too:
                    # a per-run cap, the live grant re-check and the kill switch (P2-003).
                    add("web_fetch", [fetch_tool(fetcher, resolve, marker, on_read=on_read)])
                if "web_search" in grants:
                    add("web_search", self._connectors.web_search(stack))
                if "browser" in grants:
                    add("browser", self._connectors.browser(stack))
                if "code_interpreter" in grants:
                    add("code_interpreter", self._connectors.code_interpreter(stack))
                if "mcp" in grants:
                    add("mcp", self._connectors.mcp_servers(stack, list(module.get("mcp_servers") or [])))
                policy = RuntimeToolPolicy(
                    runtime_side,
                    lambda: _live_of(api.call("run_definition", {})),
                    server_of={
                        # the web-search gateway's tools carry no server (""): they are not a module's MCP
                        # server, so the liveness re-check must not treat them as removed (CR3-02)
                        str(t.tool_name): str(t.server)
                        for t in tools
                        if getattr(t, "server", None)
                    },
                    refused_tools=DRAFT_REFUSED if module.get("template") == "draft" else None,
                )
                message = self._prompts.run_message(payload)
                texts: list[str] = []
                result: Any = None  # no pass when no source has anything new
                deferred = 0  # passes left for the next run because they would not fit in this one
                for number, batch in enumerate(passes, start=1):
                    if guard.stop_reason:  # stopped in an earlier pass: no new conversation
                        break
                    if number > 1 and not pass_fits(guard, number - 1):
                        # Starting a pass the run cannot finish spends its budget and stops it mid-pass, with
                        # entities created and nothing linked to them (Daily check 2026-11-10). The passes
                        # go least recently read first, so the ones left here open the next run.
                        deferred = len(passes) - number + 1
                        break
                    # A fresh conversation per pass, sharing the guard, the ledger and every hook, so the
                    # budget, the turn limit and the write caps span the whole run (HC-111).
                    agent = self._agent_builder(
                        system_prompt=self._prompts.system_prompt(module, context.get("product")),
                        tools=tools,
                        model=self._models.build(module.get("model_key"), max_tokens_for(module)),
                        # addresses and sources come last on purpose: AfterToolCallEvent runs its callbacks
                        # in reverse, so they see a tool's result before the fence turns it into fenced text.
                        hooks=[fence, guard, policy, sources, addresses, tally],
                        callback_handler=None,
                        retry_strategy=TransientRetry(),
                        tool_executor=WritesLastExecutor(WRITE_TOOLS),
                    )
                    tally.last_call = ""  # a block names a call of its own pass, not an earlier one's
                    text = message if batch is None else f"{message}\n\n{pass_note(batch, number, len(passes))}"
                    block = instructions_block(listing if batch is None else {"sources": batch})
                    result = agent(f"{text}\n\n{block}" if block else text)
                    guard.prior = guard.prior + usage_of(getattr(agent, "event_loop_metrics", None))
                    agent = None  # its usage is in guard.prior now
                    said = message_text(result)
                    if guardrail_blocked(result, said):
                        # Without a trace the block names no filter: the call before it is where to look,
                        # and with none, the pass's own input was blocked (E2E F16b).
                        said = (
                            f"{said} Last tool call before the block: {tally.last_call}."
                            if tally.last_call
                            else f"{said} The block came before any tool call in this pass, on its input."
                        )
                    texts.append(said if batch is None else f"Pass {number} of {len(passes)}: {said}")
                    if batch is not None and guard.stop_reason is None and "source_inbox_ack" in granted:
                        ack_inbox(api, batch)  # a pass cut short leaves its items for the next run
                if not passes:
                    texts.append("No source has new items since the last check, so there was nothing to read.")
                if deferred:
                    texts.append(
                        f"Stopped before pass {len(passes) - deferred + 1} of {len(passes)}: the next pass would not "
                        "fit in this run's budget, so its sources are read first in the next run."
                    )
            usage = guard.billed(guard.prior)
            notes = [f"granted {grant}, but it could not be loaded; this run went without it" for grant in unavailable]
            summary = address_note(
                addresses.scrub_summary("\n".join(texts)),
                addresses.defanged,
                addresses.defanged_links,
                [*tally.note(), *notes],
                sources.summary_lines(),
            )
            if guard.stop_reason == "turn_limit":  # a runaway run is a failure, whatever it wrote first
                return RunOutcome(
                    "failed",
                    summary,
                    usage,
                    factory.ledger.nodes_written,
                    f"stopped after {guard.max_turns} model calls",
                    guard.admitted,
                )
            status = {
                "budget_exhausted": "budget_exhausted",
                "genai_disabled": "cancelled",
                "run_ended": "cancelled",
                "run_finished": "cancelled",  # the backend refused a call: the run was finalized (P3-086)
            }.get(guard.stop_reason or "", "succeeded")
            denied = tally.denied()
            if status == "succeeded" and denied:  # it did nothing: every call it made was refused (HC-139)
                return RunOutcome("failed", summary, usage, factory.ledger.nodes_written, denied, guard.admitted)
            # A Scout run that had sources to read and left with no cursor moved and nothing written read none of
            # them: every fetch failed, or it gave up. Its own summary says it failed, so its status does too
            # (Daily check 2026-11-08: "no source readable" under a green "succeeded").
            if (
                status == "succeeded"
                and any(batch is not None for batch in passes)
                and "source_update_cursor" in granted
                and "http_fetch" in granted  # a Scout that reads with the browser or MCP is not judged here
                and not sources.cursors
                and not read_a_source  # a source read is a read, even when nothing in it was new
                and not factory.ledger.nodes_written
            ):
                return RunOutcome(
                    "failed",
                    summary,
                    usage,
                    0,
                    "NoSourceRead: the run had sources to read but fetched none, moved no cursor and wrote nothing",
                    guard.admitted,
                )
            # A guardrail block with nothing written is a failed run, not a green "succeeded" whose summary is
            # the canned reply (HC-109: scouting was down for days on a green dashboard). A block after the run
            # wrote is left as a finish: failing it would make a retry redo committed work.
            if status == "succeeded" and not factory.ledger.nodes_written and guardrail_blocked(result, summary):
                return RunOutcome(
                    "failed",
                    summary,
                    usage,
                    0,
                    f"GuardrailBlocked: the guardrail stopped the run before it wrote anything: {summary[:300]}",
                    guard.admitted,
                )
            return RunOutcome(status, summary, usage, factory.ledger.nodes_written, model_calls=guard.admitted)
        except Exception as err:
            log.exception("module run failed")
            spent = usage_of(getattr(agent, "event_loop_metrics", None)) + (
                guard.prior if guard else TokenUsage(0, 0, 0, 0)
            )
            return RunOutcome(
                "failed",
                "",
                guard.billed(spent) if guard else spent,  # calls that failed mid-stream were billed too
                factory.ledger.nodes_written,
                f"{type(err).__name__}: {str(err)[:500]}",
                # The calls the guard let through: 0 proves nothing was spent. With any, the backend charges what
                # was reported, or keeps the hold when nothing was (code review r30).
                guard.admitted if guard else 0,
            )
        finally:
            metrics.update(
                tool_calls=dict(getattr(api, "calls", {}) or {}),
                runtime_tool_calls=dict(policy.calls) if policy else {},
                refused=dict(policy.refused) if policy else {},
                writes=dict(policy.writes) if policy else {},
                unavailable_grants=unavailable,
                cursors=len(sources.cursors),
                fenced=fence.fenced,
                addresses_defanged=addresses.defanged,
                links_defanged=addresses.defanged_links,
                model_calls=guard.admitted if guard else 0,  # the count RunOutcome reports (code review r32)
                tool_outcomes={"succeeded": tally.succeeded, "refused": tally.refused, "failed": tally.failed},
            )
            api.close()


def _grants_of(definition: Any) -> set[str]:
    body = definition.get("definition") if isinstance(definition, dict) else None
    return set((body or {}).get("grants") or []) if isinstance(body, dict) else set()


def _live_of(definition: Any) -> LiveDefinition:
    """The module's grants and MCP server addresses, as ``run_definition`` returns them now."""
    body = definition.get("definition") if isinstance(definition, dict) else None
    servers = (body or {}).get("mcp_servers") if isinstance(body, dict) else None
    return LiveDefinition(
        _grants_of(definition),
        {str(s.get("url", "")) for s in servers if isinstance(s, dict)} if isinstance(servers, list) else set(),
    )


def _page(address: Any) -> str:
    """An http(s) address as one page: lower-case scheme and host, no fragment, no trailing slash; "" otherwise."""
    if not isinstance(address, str):
        return ""
    try:
        parts = urlparse(address)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""
    netloc = parts.netloc.lower()
    query = f"?{parts.query}" if parts.query else ""
    params = f";{parts.params}" if parts.params else ""
    return f"{parts.scheme.lower()}://{netloc}{parts.path.rstrip('/')}{params}{query}"


def pass_fits(guard: RunGuard, done: int) -> bool:
    """Whether another pass fits in what the run has left, judged by the average of the ``done`` passes so far,
    plus the room the guard keeps for one call's answer (a call without it is refused, ending the run)."""
    spent = guard.weights.weigh(guard.billed(guard.prior)) + guard.metered
    return guard.budget_remaining - spent >= spent / max(1, done) + guard.call_reserve


def live_stop_code(api: ToolApi) -> str | None:
    """The kill switch and the daily budget as the backend sees them now, as a stop code. ``budget_status``
    is bookkeeping, so the backend answers it even with GenAI paused, which is the point: nothing else tells
    a run that only uses runtime-side tools to stop. A backend that cannot be reached does not stop the run
    (the budget left at its start is still a hard cap); the next check tries again. A refusal does: the
    module was deleted (404), the run token no longer holds (403) or the run was finalized (P3-061)."""
    try:
        state = api.call("budget_status", {})
    except ToolCallError as err:
        if err.status in (403, 404) or err.code in ("run_finished", "not_found", "forbidden"):
            log.warning("the backend refused the run state: %s %s", err.status, err.code)
            return "run_ended"
        log.warning("could not re-read the run state: %s %s", err.status, err.code)
        return None
    except Exception as err:
        log.warning("could not re-read the run state: %s", type(err).__name__)
        return None
    if not isinstance(state, dict):
        return None
    if not state.get("genai_enabled", True):
        return "genai_disabled"
    if state.get("run_ended") is True:
        # Finalized already (its task timed out, or an operator ended it): its hold was released, so
        # any further model call would be spend on no ledger (SR2-09).
        return "run_ended"
    return "budget_exhausted" if state.get("state") == "closed" else None


def max_tokens_for(module: dict[str, Any]) -> int:
    """Drafts write a whole article in one draft_save call, so they get room for it."""
    if module.get("template") != "draft" or str(module.get("model_key") or "").startswith("nova"):
        return MODULE_MAX_TOKENS  # the Amazon Nova model keeps the default output cap
    return DRAFT_MAX_TOKENS


TOKEN_CLOSED = frozenset({"TaskTimedOut", "InvalidToken", "TaskDoesNotExist"})
MAX_HEARTBEAT_FAILURES = 5
SEND_ATTEMPTS = 6
SEND_BACKOFF_SECONDS = 1.0


def _error_code(err: Exception) -> str:
    return str(getattr(err, "response", {}).get("Error", {}).get("Code") or type(err).__name__)


class TaskToken:
    """The Step Functions task token a run reports to. A retried invoke of the same run hands the running
    thread its new token (P3-089): the old one is closed by then, so the outcome goes to the new one."""

    def __init__(self, value: str) -> None:
        self._lock = threading.Lock()
        self._value = value

    @property
    def value(self) -> str:
        with self._lock:
            return self._value

    def replace(self, value: str) -> None:
        with self._lock:
            self._value = value


class TaskTokenReporter:
    """Heartbeats a Step Functions task token while ``work`` runs, then reports its outcome."""

    def __init__(
        self,
        sfn: Any,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sfn = sfn
        self._every = heartbeat_seconds
        self._sleep = sleep

    def run(self, task_token: str | TaskToken, work: Callable[[], RunOutcome]) -> None:
        token = task_token if isinstance(task_token, TaskToken) else TaskToken(task_token)
        stop = threading.Event()

        def beat() -> None:
            failures = 0
            while not stop.wait(self._every):
                used = token.value
                try:
                    self._sfn.send_task_heartbeat(taskToken=used)
                    failures = 0
                except Exception as err:
                    code = _error_code(err)
                    failures += 1
                    log.warning("heartbeat failed: %s", code)
                    if code in TOKEN_CLOSED and token.value != used:
                        failures = 0  # a retried invoke gave the run a new token: beat that one
                        continue
                    # A closed token has nothing left to report to; anything else (throttling, a network
                    # blip) is retried, since one missed beat would otherwise time the whole run out.
                    if code in TOKEN_CLOSED or failures >= MAX_HEARTBEAT_FAILURES:
                        return

        threading.Thread(target=beat, daemon=True).start()
        try:
            outcome = work()
        except Exception as err:
            stop.set()
            log.exception("run produced no outcome")
            try:
                self._sfn.send_task_failure(taskToken=token.value, error="RunError", cause=str(err)[:32000])
            except Exception:
                log.exception("send_task_failure failed")
            return
        try:
            self._send(token, json.dumps(outcome.to_payload()))
        finally:
            stop.set()  # beating until the outcome is in: a retry must not find the task timed out

    def _send(self, token: TaskToken, output: str) -> None:
        """``SendTaskSuccess``, retried on throttling and transient errors with backoff: it is idempotent
        for a token, and a lost one loses the run's usage from the ledger (A3-010)."""
        for attempt in range(SEND_ATTEMPTS):
            used = token.value
            try:
                self._sfn.send_task_success(taskToken=used, output=output)
                return
            except Exception as err:
                code = _error_code(err)
                if code in TOKEN_CLOSED and token.value == used:
                    log.warning("send_task_success failed: Step Functions already closed this task (%s)", code)
                    return
                log.warning("send_task_success failed (attempt %d): %s", attempt + 1, code)
                if attempt + 1 < SEND_ATTEMPTS:
                    self._sleep(SEND_BACKOFF_SECONDS * 2**attempt * (0.5 + random.random() / 2))  # noqa: S311  # nosec B311 - backoff jitter, not a secret
        log.error("send_task_success failed %d times; the run's outcome is lost", SEND_ATTEMPTS)
