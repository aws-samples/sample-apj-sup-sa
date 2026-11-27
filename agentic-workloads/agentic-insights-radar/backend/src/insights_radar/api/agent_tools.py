# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Agent tool dispatcher: the single authority for what a module run may do.

A call is honoured only if (1) the function URL (AWS_IAM) verified a SigV4 signature from the
module-runtime role, (2) the ``X-MR-Run-Token`` header verifies against the KMS key and is unexpired, and
(3) the token grants the tool. Authority never comes from model-typed arguments: the module
slug, run id and product id are read from the token, and every node a module writes is stamped
with that module. A module may only create or update outputs of its own ``output_kind`` (and
``insight_kind``), and may only edit outputs it created itself. Radar entries can only be
*proposed* (``radar_suggest``), never written.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any, cast

from botocore.exceptions import ClientError

from insights_radar.api.body import Body
from insights_radar.core.clock import iso, parse_iso
from insights_radar.core.defang import Defang
from insights_radar.core.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    RunFinishedError,
    ValidationError,
)
from insights_radar.core.html import HtmlSanitizer
from insights_radar.core.names import NameNormalizer
from insights_radar.core.tokens import BudgetSnapshot
from insights_radar.core.urls import UrlGuard
from insights_radar.graph.model import Node, NodeFactory, node_type_of
from insights_radar.graph.registry import (
    ENTITIES,
    EVIDENCE,
    Category,
    EdgeType,
    NodeType,
    Registry,
)
from insights_radar.graph.search import nothing_hidden
from insights_radar.identity.principal import RoleCaller
from insights_radar.identity.run_token import RunClaims
from insights_radar.insights.service import lifecycle_ended
from insights_radar.modules.catalog import ModelCatalog
from insights_radar.modules.model import PRIVATE_OUTPUT_TYPES, ModuleDefinition, module_actor, private_hider
from insights_radar.modules.prompts import clean_admin_text
from insights_radar.modules.skills import MAX_SKILL_BYTES
from insights_radar.profile.model import list_key

log = logging.getLogger(__name__)
ToolFn = Callable[[RunClaims, ModuleDefinition, Body], Any]
MAX_LINKS_PER_CALL = 40
MAX_LINKS_TO_ONE_ENTITY_PER_RUN = 50
DRAFT_MAX_BYTES = 200_000
# How many of a type's newest outputs a save checks for one of the same title by the same module.
TWIN_SCAN = 100
# A prompt-injected run can be refused in a loop: the first few refusals per run are audited, the
# rest only logged, so one run cannot bury the day's real changes.
# How many entity names a run that can send requests out may match among the ones it cannot read
# before every kg_upsert_entity in that run is refused alike (SR2-05).
MAX_HIDDEN_MATCHES_PER_RUN = 3
HIDDEN_ENTITY_MESSAGE = "this entity cannot be created or read by this module"
MAX_DENIALS_AUDITED_PER_RUN = 5
DENIAL_RUNS_TRACKED = 200
# Tools that spend money outside the model: kg_search embeds the query (one Titan call), kg_neighbors
# can fan out to hundreds of DynamoDB queries. Each call is charged a flat weighted cost against the
# module's daily budget and capped per run, so they are no longer free and unlimited.
METERED_TOOLS = {"kg_search": 2_000, "kg_neighbors": 1_000}
MAX_METERED_CALLS_PER_RUN = 300

REQUIRED_OUTPUT_ATTRIBUTES: dict[NodeType, tuple[str, ...]] = {
    NodeType.GAP: ("request_title", "problem", "impact"),
    NodeType.CONTENT_IDEA: ("content_type", "audience"),
}


def _ids(b: Body, key: str) -> list[str]:
    values = b.texts(key, max_items=MAX_LINKS_PER_CALL, max_len=200)
    for v in values:
        try:
            node_type_of(v)
        except ValueError as err:
            raise ValidationError(f"{key}: {err}") from err
    return values


# Always answered, even with GenAI paused, so a run can learn it must stop and save progress.
BOOKKEEPING_TOOLS = frozenset(
    {
        "run_context",
        "run_definition",
        "budget_status",
        "progress_update",
        "source_update_cursor",
        "source_mark_read",
        "source_inbox_ack",
    }
)
# New items shown per source in one source_list: a pass of a few sources stays a readable size; the rest wait.
INBOX_SHOWN = 40
# Of those, only budget_status survives the end of the run: run_definition returns MCP server
# addresses (which can embed a credential) and run_context the product profile, and a finished run
# has no reason to read either for the rest of its token's eight-hour life.
BOOKKEEPING_AFTER_RUN = frozenset({"budget_status"})
# Tools that change stored state; each successful call leaves an audit row naming the module and run.
AUDITED_TOOLS = frozenset(
    {
        "kg_upsert_entity",
        "kg_add_evidence",
        "kg_link",
        "kg_save_output",
        "kg_update_output",
        "radar_suggest",
        "draft_save",
        "source_update_cursor",
        # Not a write, but it hands a stored credential to the runtime: a read worth a row (and an
        # alarm on calls from a run that never fetches anything).
        "source_address",
    }
)


def _confidence(raw: Any) -> float | None:
    """A confidence from a model-typed argument. Range-checked before converting: ``float(10**400)``
    raises OverflowError, which reached the caller as a 500 the model then retried."""
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        return None
    if isinstance(raw, int) and not -1 <= raw <= 1:
        raise ValidationError("confidence must be between 0 and 1")
    return float(raw)


def run_module(ctx: Any, module: ModuleDefinition) -> dict[str, Any]:
    """The module as a run gets it, with what this deployment calls itself and the module's list (the
    deployment profile), so prompts read "Regulatory changes module of Trade Radar" rather than the built-in
    wording."""
    profile = ctx.profile.get(cached=True)
    key = list_key(module.output_kind, module.insight_kind)
    lp = profile.list_profile(key) if key else None
    deployment = {
        "app_name": profile.app_name,
        "list_label": lp.label if lp else module.nav_label,
        "item_label": lp.item if lp else module.nav_label,
    }
    # Each attached skill's name and description, for the prompt; the body is read with skill_load.
    return {**module.to_public(), "deployment": deployment, "skill_index": ctx.skills.index(module.skills)}


def _search_note(degraded: bool, unworded: bool) -> str:
    if degraded:
        note = (
            "semantic search is unavailable: these are keyword matches only, so search again with the exact "
            "words a record would use"
        )
        return note + (", and none of these shares most of the query's words" if unworded else "")
    if unworded:
        return (
            "none of these shares most of the query's words; they are only near it in meaning, so do not treat "
            "one as the record you searched for"
        )
    return "scores are reciprocal-rank-fusion values; ~0.016 is the best possible single-list rank"


class AgentTools:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._denials: dict[str, int] = {}
        self._tools: dict[str, ToolFn] = {
            "run_context": self.run_context,
            "run_definition": self.run_definition,
            "budget_status": self.budget_status,
            "progress_update": self.progress_update,
            "kg_schema": self.kg_schema,
            "kg_search": self.kg_search,
            "kg_get": self.kg_get,
            "kg_neighbors": self.kg_neighbors,
            "kg_recent": self.kg_recent,
            "kg_upsert_entity": self.kg_upsert_entity,
            "kg_add_evidence": self.kg_add_evidence,
            "kg_link": self.kg_link,
            "kg_save_output": self.kg_save_output,
            "kg_update_output": self.kg_update_output,
            "product_knowledge_search": self.product_knowledge_search,
            "product_profile": self.product_profile,
            "source_list": self.source_list,
            "source_address": self.source_address,
            "source_update_cursor": self.source_update_cursor,
            "source_mark_read": self.source_mark_read,
            "source_inbox_ack": self.source_inbox_ack,
            "radar_list": self.radar_list,
            "radar_get": self.radar_get,
            "radar_suggest": self.radar_suggest,
            "draft_get_idea": self.draft_get_idea,
            "draft_save": self.draft_save,
            "skill_load": self.skill_load,
        }

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def call(self, tool: str, token: str | None, args: dict[str, Any], caller_arn: str | None) -> Any:
        expected_role = self._ctx.settings.module_runtime_role_arn
        if not expected_role:
            # Fail closed, like the chat-usage route: without the role there is no caller to trust.
            raise ForbiddenError("agent tools are not configured on this deployment")
        if not RoleCaller.matches(expected_role, caller_arn):
            raise ForbiddenError("agent tools may only be called by the module runtime")
        if tool not in self._tools:
            raise NotFoundError(f"unknown tool {tool}")
        # The token is verified first, then the grant checked, so a call outside the token's tools is
        # attributable: it used to be refused inside authorize_tool, before anything was logged.
        claims = self._ctx.tokens.verify(token)
        try:
            return self._run(tool, claims, args)
        except DomainError as err:
            if err.status in (403, 404, 409):
                # Refused calls are the main signal that a run is prompt-injected: a tool outside the
                # grants, a hidden node, a cursor move after the run ended. Recorded with the module,
                # run and reason so an investigation can see them and an alarm can fire on them.
                self._denied(tool, claims, err)
            raise

    def _run(self, tool: str, claims: RunClaims, args: dict[str, Any]) -> Any:
        if tool not in claims.tools:
            raise ForbiddenError(f"module {claims.module} is not granted tool {tool}", {"tool": tool})
        if tool not in BOOKKEEPING_TOOLS:
            # The kill switch stops runs already in flight: their next tool call is refused.
            self._ctx.module_service.ensure_genai()
        # So does the end of the run: a run finalized as failed (its task timed out) no longer holds
        # budget or a concurrency slot, so an agent that kept going must not keep writing.
        if tool not in BOOKKEEPING_AFTER_RUN and self._run_ended(claims):
            raise RunFinishedError(f"run {claims.run_id} has ended", {"tool": tool})
        module = self._ctx.modules.get(claims.module)
        refusal = module.run_refusal
        if refusal and tool not in BOOKKEEPING_TOOLS:
            # A run started before the definition gained this combination: it may report and stop, not act.
            raise ForbiddenError(f"module {module.slug} cannot run: {refusal}", {"tool": tool})
        if tool not in module.tools:
            # An admin removed the grant after this run started; the token's copy is not the authority.
            raise ForbiddenError(f"module {module.slug} is no longer granted tool {tool}", {"tool": tool})
        log.info(
            json.dumps(
                {
                    "event": "agent_tool",
                    "tool": tool,
                    "module": claims.module,
                    "run": claims.run_id,
                    "arg_keys": sorted(args),
                }
            )
        )
        if tool in METERED_TOOLS:
            self._meter(tool, claims, module)
        try:
            result = self._tools[tool](claims, module, Body(args))
        except DomainError:
            if tool in METERED_TOOLS:
                # Refused before it did anything (a bad argument, a hidden node): it neither costs the
                # module's budget nor uses one of the run's metered calls (B3-026).
                self._meter_refund(claims)
            raise
        if tool in METERED_TOOLS:
            self._ctx.modules.charge_weighted(module.slug, METERED_TOOLS[tool])
        if tool in AUDITED_TOOLS and self._worth_auditing(tool, claims, args):
            self._audit(tool, claims, args, result)
        return result

    def _meter_key(self, claims: RunClaims) -> dict[str, str]:
        return {"pk": f"METER#{claims.run_id}", "sk": "CALLS"}

    def _meter(self, tool: str, claims: RunClaims, module: ModuleDefinition) -> None:
        """Take one of the run's metered calls, counted in the ops table so every container sees the same
        count: a per-container dict gave a run a fresh allowance in each Lambda container (B3-027). A
        module whose budget is closed gets none: only the model was stopped there, while kg_search kept
        embedding and charging (B3-028)."""
        if self._snapshot(claims, module).state.value == "closed":
            raise ForbiddenError("this module's budget is used up; stop now", {"tool": tool})
        try:
            self._ctx.ops_table.update_item(
                Key=self._meter_key(claims),
                UpdateExpression="ADD n :one SET #ttl = if_not_exists(#ttl, :ttl)",
                ConditionExpression="attribute_not_exists(n) OR n < :cap",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":one": 1,
                    ":cap": MAX_METERED_CALLS_PER_RUN,
                    ":ttl": int(self._ctx.clock.now().timestamp()) + 86_400,
                },
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            raise ForbiddenError(
                f"this run has made {MAX_METERED_CALLS_PER_RUN} {tool}-class calls; work with what you have",
                {"tool": tool},
            ) from err

    def _meter_refund(self, claims: RunClaims) -> None:
        try:
            self._ctx.ops_table.update_item(
                Key=self._meter_key(claims), UpdateExpression="ADD n :minus", ExpressionAttributeValues={":minus": -1}
            )
        except Exception:
            log.exception(json.dumps({"event": "meter.refund_failed", "run": claims.run_id}))

    def _worth_auditing(self, tool: str, claims: RunClaims, args: dict[str, Any]) -> bool:
        """``source_address`` is resolved by the runtime on every fetch of a source: one row per run and
        source (the first resolution), not one per fetch (B3-065). Every other audited tool is a write."""
        if tool != "source_address":
            return True
        try:
            self._ctx.ops_table.put_item(
                Item={
                    "pk": f"AUDITED#{claims.run_id}",
                    "sk": f"SOURCE#{str(args.get('source_id', ''))[:40]}",
                    "ttl": int(self._ctx.clock.now().timestamp()) + 86_400,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return False
        return True

    def _run_ended(self, claims: RunClaims) -> bool:
        return bool(self._ctx.modules.get_run(claims.module, claims.run_id).status.terminal)

    def _denied(self, tool: str, claims: RunClaims, err: DomainError) -> None:
        """Log every refusal, and audit the first few per run: an injected run can produce them in a
        loop, and a row per attempt would bury the day's real changes. Best effort per container."""
        log.warning(
            json.dumps(
                {
                    "event": "agent_tool_denied",
                    "tool": tool,
                    "module": claims.module,
                    "run": claims.run_id,
                    "code": err.code,
                    "status": err.status,
                }
            )
        )
        seen = self._denials.get(claims.run_id, 0)
        if seen >= MAX_DENIALS_AUDITED_PER_RUN:
            return
        if len(self._denials) >= DENIAL_RUNS_TRACKED:
            self._denials.clear()
        self._denials[claims.run_id] = seen + 1
        try:
            self._ctx.audit.record_actor(
                f"module:{claims.module}",
                "",
                "agent.denied",
                tool,
                {"run_id": claims.run_id, "code": err.code, "reason": err.message[:200]},
            )
        except Exception:
            log.exception(json.dumps({"event": "audit.write_failed", "action": "agent.denied", "target": tool}))

    def _audit(self, tool: str, claims: RunClaims, args: dict[str, Any], result: Any) -> None:
        """Who (module and run) changed what: the record an investigation of a poisoned graph or radar
        starts from. Details are ids only, never the written text."""
        body = result if isinstance(result, dict) else {}
        target = str(
            body.get("id") or body.get("entry_key") or args.get("id") or args.get("source_id") or args.get("from") or ""
        )
        details: dict[str, Any] = {"tool": tool, "run_id": claims.run_id}
        for key in ("slug", "to", "type", "idea_id"):
            if isinstance(args.get(key), str):
                details[key] = args[key][:200]
        links = body.get("links")
        if isinstance(links, list):
            details["links"] = len(links)
        try:
            self._ctx.audit.record_actor(f"module:{claims.module}", "", f"agent.{tool}", target or "-", details)
        except Exception:
            # The write already happened: an error result would make the agent repeat it (a second draft,
            # duplicate evidence, a conflicting suggestion). Logged loudly instead, like routes.audit.
            log.exception(
                json.dumps({"event": "audit.write_failed", "actor": f"module:{claims.module}", "action": tool})
            )

    # ---- context / bookkeeping ------------------------------------------------------

    def skill_load(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        """The full instructions of one of the module's skills (progressive disclosure: the prompt holds
        only names and descriptions). A skill not attached to the module is refused, so a run cannot read
        the library beyond what an administrator gave it. The body is administrator text the run is told
        to follow, so it is defanged here and passed unfenced (see the comment below)."""
        b.only("name")
        name = b.text("name", max_len=64)
        if name not in module.skills:
            raise NotFoundError(f"skill {name!r} is not attached to this module")
        skill = self._ctx.skills.get(name)
        # Administrator text the prompt tells the run to follow, so the runtime passes it unfenced (agents
        # guard.TRUSTED_TOOLS): defanged here like the instructions, so a skill cannot open or close a fence.
        return {
            "name": skill.name,
            "description": Defang.neutralize(skill.description, 1024),
            "instructions": clean_admin_text(skill.content, MAX_SKILL_BYTES),
        }

    @staticmethod
    def product_block(ctx: Any, module: ModuleDefinition, product_id: str | None) -> str | None:
        """The product profile a run of ``module`` is given: only a module that reads the product's private
        documents gets the full profile. The notes carry unreleased plans, and whatever a module writes from
        them would otherwise be readable by outbound modules, because it is not stamped private."""
        if not product_id:
            return None
        try:
            profile = ctx.products.get(product_id)
        except NotFoundError:
            return None
        block: str = profile.prompt_block() if module.reads_private else profile.public_prompt_block()
        return block

    def run_context(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        progress = self._ctx.modules.progress(module.slug)
        body: dict[str, Any] = {
            "module": module.slug,
            "run_id": claims.run_id,
            "output_kind": module.output_kind.value if module.output_kind else None,
            "insight_kind": module.insight_kind,
            "input_types": [t.value for t in module.input_types],
            "last_processed_at": progress.last_processed_at,
            "tools": sorted(claims.tools),
        }
        if claims.product_id and module.product_id != claims.product_id:
            # An admin re-pointed the module mid-run: the token's product is not the authority, and the
            # run must not keep reading the old product's notes (B3-015).
            body["product"] = None
            body["note"] = "this module's product changed; finish, save progress and stop"
        elif claims.product_id:
            body["product"] = self.product_block(self._ctx, module, claims.product_id)
        return body

    def run_definition(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        """The module as stored, for the runtime itself: someone who can invoke the runtime could pair a
        valid token with a forged module (grants, MCP servers, instructions), so it is read here rather
        than trusted from the invocation payload. Separate from run_context because the model can call
        run_context, and MCP server addresses can embed a credential; the runtime never registers this
        tool with the model (agents tool_specs has no spec for it)."""
        # What this deployment calls itself and the module's list (the deployment profile), so prompts
        # read "Regulatory changes module of Trade Radar" rather than the built-in wording.
        return {"definition": run_module(self._ctx, module)}

    def _allowance(self, claims: RunClaims, module: ModuleDefinition) -> tuple[BudgetSnapshot, int, bool, str]:
        """The module's ledger as this run sees it (its own hold not counted against it), the hold, whether
        the run has ended, and the tier it is charged at."""
        try:
            run = self._ctx.modules.get_run(module.slug, claims.run_id)
            hold = 0 if run.status.terminal else run.budget_hold
            hold_day = run.hold_day
            ended = run.status.terminal
            tier = self._ctx.module_service.run_tier(run)
        except NotFoundError:
            hold, hold_day, ended = 0, None, True
            tier = ModelCatalog.get(module.model_key).tier
        # The hold sits on the day the run started. After UTC midnight the new day's ledger does not
        # hold it, so subtracting it there told a run it still had its whole hold left while another
        # run could reserve the whole new day: read the hold's own day instead.
        ledger = self._ctx.modules.budget(module.slug, module.daily_token_budget, day=hold_day)
        return BudgetSnapshot(max(0, ledger.consumed - hold), ledger.limit), hold, ended, tier

    def _snapshot(self, claims: RunClaims, module: ModuleDefinition) -> BudgetSnapshot:
        return self._allowance(claims, module)[0]

    def budget_status(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        """The run's own allowance: the hold it reserved at start. Its own hold does not count against
        it, so a run that holds the whole remainder still sees an open budget and may spend that hold."""
        snap, hold, ended, tier = self._allowance(claims, module)
        return {
            "genai_enabled": self._ctx.modules.andon().genai_enabled,
            "state": snap.state.value,
            "consumed": snap.consumed,
            "limit": snap.limit,
            "remaining": min(hold, snap.remaining) if hold else snap.remaining,
            # The tier the run is charged at, so the runtime's guard spends ``remaining`` at the rate
            # finalize charges (CR2-05), and whether the run was already finalized: a run whose task
            # timed out was charged and released, and must stop rather than spend on no ledger (SR2-09).
            "tier": tier,
            "run_ended": ended,
            "guidance": {
                "open": "work normally",
                "warn": "prefer the most valuable items",
                "half_open": "finish the current item, save progress and stop",
                "closed": "stop now",
            }[snap.state.value],
        }

    def _cursor(self, raw: str | None, field: str) -> str | None:
        """A kg_recent cursor in one form: an RFC 3339 timestamp (``after`` it means strictly after that
        millisecond), or an item's ``resume_after`` (``<updated_at>#<id>``: strictly after that item, so
        siblings with the same updated_at are not skipped, B3-059). The timestamp is normalised to the
        stored UTC form, so ``...08:00:00Z`` no longer sorts after every ``...08:00:00.123Z`` and skips the
        whole second (B3-058); never later than now, so text an agent read cannot plant a future cursor
        that makes the module skip everything from then on."""
        if raw is None:
            return None
        stamp, sep, node_id = raw.partition("#")
        try:
            parsed = parse_iso(stamp)
            if sep:
                node_type_of(node_id)
        except ValueError as err:
            raise ValidationError(f"{field} must be an RFC 3339 timestamp or an item's resume_after") from err
        now = self._ctx.clock.now()
        if parsed > now:
            return iso(now)
        return f"{iso(parsed)}#{node_id}" if sep else iso(parsed)

    def progress_update(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        last = self._cursor(b.opt_text("last_processed_at", 240), "last_processed_at")
        remaining = b.integer("remaining", required=False, default=-1, low=-1, high=10**9)
        p = self._ctx.modules.record_progress(module.slug, last, None if remaining < 0 else remaining)
        return {"last_processed_at": p.last_processed_at, "remaining_history": list(p.remaining_history)}

    # ---- graph reads ----------------------------------------------------------------

    def _hider(self, module: ModuleDefinition) -> Callable[[Node], bool]:
        """Which nodes this module must not read.

        Gaps, content ideas and drafts are derived from private product documents, and so is anything
        written by a module that could read those documents. Only a module that reads product knowledge
        itself may read them: a module that can send requests out (fetch, search, browser, code, MCP)
        must not, so an injected page cannot make it carry their text to an attacker, and a module that
        reads neither must not either, or it would launder the text in one hop by saving its own output.

        The decision is the node's own ``private`` stamp (set at write time, so it survives the writing
        module being deleted or re-granted). A node written before the stamp existed has none, and for
        it the writing module's sticky private-writer mark decides: whether it holds product_knowledge
        now, ever held it, or did when it was deleted. Its current grants alone are not enough: an admin
        who swaps product_knowledge for web_fetch would otherwise publish months of outputs derived from
        roadmap notes to every outbound module (SR2-01).
        """
        if module.reads_private:
            return nothing_hidden
        return private_hider(self._ctx.modules.private_writer_slugs())

    def kg_schema(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        schema = Registry.describe()
        edges = cast("dict[str, object]", schema["edge_types"])  # describe() builds it as a dict
        # The edges a module can make with kg_link: has_draft is draft_save's alone (code review r55).
        return {**schema, "edge_types": {k: v for k, v in edges.items() if k != EdgeType.HAS_DRAFT.value}}

    def kg_search(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        types = [Registry.node_type(t) for t in b.texts("types", max_items=20, max_len=40)]
        hidden = self._hider(module)
        query = b.text("query", max_len=500)
        # Hidden records are dropped before ranking and never counted: ranks, scores, the top-`limit` cut and a
        # hidden count each told an injected module whether a private record matched a name it guessed,
        # uncapped by MAX_HIDDEN_MATCHES_PER_RUN (round 34 security review).
        result = self._ctx.search.search_result(
            query,
            types=types or None,
            insight_kind=b.opt_text("insight_kind", 60),
            limit=b.integer("limit", required=False, default=10, low=1, high=50),
            hidden=hidden,
        )
        visible = result.hits
        return {
            "results": [
                {
                    "id": h.node.id,
                    "type": h.node.type.value,
                    "title": h.node.title,
                    "summary": h.node.summary[:400],
                    "updated_at": h.node.updated_at,
                    "evidence_url": h.node.evidence_url,
                    "score": round(h.score, 5),
                }
                for h in visible
            ],
            # Fixed text, not a count (B3-060 had one): an empty list means nothing this module may read matches.
            "scope": "records this module may not read are left out of the search",
            "note": _search_note(result.degraded, bool(visible) and not result.worded),
        }

    def _readable(self, module: ModuleDefinition, node: Node) -> Node:
        if self._hider(module)(node):
            raise NotFoundError(f"node {node.id} not found", {"id": node.id})
        return node

    def kg_get(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        if b.has("ids"):
            ids = _ids(b, "ids")
            if not ids:
                raise ValidationError("ids must name at least one node")
            # Always the batch shape for ``ids``, however many: one id used to answer with the node itself
            # (or a 404), two or more with {nodes, missing} (B3-066).
            found = self._ctx.graph.get_many(ids)
            hidden = self._hider(module)
            return {
                "nodes": [found[i].to_public() for i in ids if i in found and not hidden(found[i])],
                "missing": [i for i in ids if i not in found or hidden(found[i])],
            }
        return self._readable(module, self._ctx.graph.get(b.text("id", max_len=200))).to_public()

    def kg_neighbors(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        edge_types = [Registry.edge_type(e) for e in b.texts("edge_types", max_items=20, max_len=40)]
        hidden = self._hider(module)
        # A hidden root is a 404, and hidden nodes are not traversed through: listing the visible
        # neighbours of a private gap mapped which signals the gap addresses.
        root = self._readable(module, self._ctx.graph.get(b.text("id", max_len=200)))
        hood = self._ctx.graph.neighborhood(
            root.id,
            depth=b.integer("depth", required=False, default=1, low=1, high=3),
            edge_types=edge_types or None,
            max_nodes=b.integer("max_nodes", required=False, default=60, low=1, high=150),
            blocked=hidden,
        )
        nodes = [n for n in hood.nodes if not hidden(n)]
        visible = {n.id for n in nodes}
        return {
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type.value,
                    "title": n.title,
                    "summary": n.summary[:400],
                    "updated_at": n.updated_at,
                    "evidence_url": n.evidence_url,
                    "module": n.module,
                }
                for n in nodes
            ],
            "edges": [e.to_public() for e in hood.edges if e.source in visible and e.target in visible],
            "truncated": hood.truncated,
        }

    def kg_recent(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        node_type = Registry.node_type(b.text("type", max_len=40))
        if module.outbound and node_type in PRIVATE_OUTPUT_TYPES:
            raise ForbiddenError(f"modules with outbound tools cannot read {node_type.value} nodes")
        hidden = self._hider(module)
        page = self._ctx.graph.recent(
            node_type,
            insight_kind=b.opt_text("insight_kind", 60),
            after=self._cursor(b.opt_text("after", 240), "after"),
            limit=b.integer("limit", required=False, default=25, low=1, high=100),
            cursor=b.opt_text("cursor", 2000),
            oldest_first=True,
        )
        visible = [n for n in page.items if not hidden(n)]
        last = page.items[-1] if page.items else None
        return {
            "items": [
                {
                    "id": n.id,
                    "type": n.type.value,
                    "title": n.title,
                    "summary": n.summary[:600],
                    "created_at": n.created_at,
                    # The clock ``after`` filters on and the list is ordered by: a module's
                    # last_processed_at is the newest updated_at it finished (CR2-02).
                    "updated_at": n.updated_at,
                    # The exact place after this item, for ``after`` and last_processed_at: siblings
                    # sharing its updated_at are not skipped (B3-059).
                    "resume_after": f"{n.updated_at}#{n.id}",
                    "evidence_url": n.evidence_url,
                    "module": n.module,
                }
                for n in visible
            ],
            "cursor": page.cursor,
            "cursor_field": "updated_at",
            # Items on this page the module may not read: a page of only private nodes is not "nothing
            # new", and ``resume_after`` moves past them (B3-060).
            "hidden": len(page.items) - len(visible),
            "resume_after": f"{last.updated_at}#{last.id}" if last else None,
        }

    # ---- graph writes ---------------------------------------------------------------

    def _actor(self, module: ModuleDefinition) -> str:
        return module_actor(module.slug)

    @staticmethod
    def _writes_private(claims: RunClaims, module: ModuleDefinition) -> bool:
        """Whether what this run writes may carry text derived from private documents: the module reads
        them now, or the run was started with the grant (its token lists product_knowledge_search) and
        so may already hold notes and passages in its context after an admin removed it (B3-002)."""
        return module.reads_private or "product_knowledge_search" in claims.tools

    def _hidden_matches(self, claims: RunClaims, add: int = 0) -> int:
        """How many entity names this run has matched that it may not read, kept in the ops table so
        every container sees the same count. Read with GetItem and written only on a match: every
        upsert used to write the counter, even a plain create (B3-070)."""
        key = {"pk": f"PROBE#{claims.run_id}", "sk": "ENTITY"}
        if not add:
            item = self._ctx.ops_table.get_item(Key=key, ConsistentRead=True).get("Item") or {}
            return int(item.get("n", 0))
        res = self._ctx.ops_table.update_item(
            Key=key,
            UpdateExpression="ADD n :a SET #ttl = if_not_exists(#ttl, :ttl)",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={":a": add, ":ttl": int(self._ctx.clock.now().timestamp()) + 86_400},
            ReturnValues="ALL_NEW",
        )
        return int(res["Attributes"]["n"])

    def kg_upsert_entity(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        node_type = Registry.node_type(b.text("type", max_len=40))
        if node_type not in ENTITIES:
            raise ValidationError(f"type must be an entity: {sorted(t.value for t in ENTITIES)}")
        hider = self._hider(module)
        if module.outbound and self._hidden_matches(claims) >= MAX_HIDDEN_MATCHES_PER_RUN:
            # A refusal for a private match is one bit an injected page can collect name by name
            # ("does Project Falcon exist?"). After a few, every create in the run gets the same
            # answer, so a candidate list yields nothing more (SR2-05).
            raise ForbiddenError(HIDDEN_ENTITY_MESSAGE)
        node, created = self._ctx.graph.upsert_entity(
            Node(
                id="",
                type=node_type,
                title=b.text("name", max_len=200),
                summary=b.text("summary", required=False, max_len=2000),
                description=b.text("description", required=False, max_len=40_000),
                aliases=tuple(b.texts("aliases", max_items=20, max_len=200)),
                attributes=b.mapping("attributes", required=False),
                evidence_url=b.opt_text("evidence_url", 2048),
                module=module.slug,
                private=self._writes_private(claims, module),
            ),
            self._actor(module),
        )
        if not created and hider(node):
            # The name matched an entity this module may not read ("Project Falcon"): returning its id
            # and title told the caller it exists and let it hang evidence off it. Each match is
            # logged and audited, and counted towards the run's limit (SR2-05).
            seen = self._hidden_matches(claims, 1)
            log.warning(json.dumps({"event": "agent_private_name_match", "module": module.slug, "run": claims.run_id}))
            try:
                self._ctx.audit.record_actor(
                    f"module:{module.slug}",
                    "",
                    "agent.private_name_match",
                    node_type.value,
                    {"run_id": claims.run_id, "matches": seen},
                )
            except Exception:
                log.exception("audit write failed for a private name match")
            raise ForbiddenError(HIDDEN_ENTITY_MESSAGE)
        return {"id": node.id, "created": created, "title": node.title}

    def kg_add_evidence(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        node_type = Registry.node_type(b.text("type", max_len=40))
        if node_type not in EVIDENCE:
            raise ValidationError(f"type must be evidence: {sorted(t.value for t in EVIDENCE)}")
        published = b.opt_text("published_at", 40)
        if published:
            try:
                when = parse_iso(published)
            except ValueError as err:
                raise ValidationError("published_at must be an RFC 3339 timestamp") from err
            # Text an agent read must not plant evidence "published" in the future (it would sort first
            # and outlive everything as the newest item).
            if when > self._ctx.clock.now() + timedelta(days=1):
                raise ValidationError("published_at cannot be in the future")
            published = iso(when)  # one UTC form, like the progress cursor, so string order is time order
        about = _ids(b, "about")
        for target in about:
            if node_type_of(target) not in ENTITIES:
                raise ValidationError("about must reference entity nodes")
        plan = [(EdgeType.ABOUT, t) for t in about]
        for key, edge in (
            ("held_by", EdgeType.HELD_BY),
            ("reported_by", EdgeType.REPORTED_BY),
            ("adopted_by", EdgeType.ADOPTED_BY),
        ):
            role_target = b.opt_text(key, 200)
            if role_target:
                plan.append((edge, role_target))
        self._check_plan(node_type, plan, module)
        title, url = b.text("title", max_len=200), b.text("evidence_url", max_len=2048)
        node, created = self._ctx.graph.create_once(
            Node(
                id="",
                type=node_type,
                title=title,
                summary=b.text("summary", required=False, max_len=2000),
                description=b.text("description", required=False, max_len=40_000),
                evidence_url=url,
                published_at=published,
                confidence=_confidence(b.raw.get("confidence")),
                attributes=b.mapping("attributes", required=False),
                module=module.slug,
                private=self._writes_private(claims, module),
                # Set by the module runtime from what the run fetched; whatever the model passed is
                # replaced there, so this is the runtime's word (SR2-02).
                fetched=b.raw.get("fetched") is True,
            ),
            self._actor(module),
            # The same module recording the same claim about the same page is the retry of a call
            # whose answer was lost, not a second piece of evidence.
            hashlib.sha256(f"{module.slug}|{node_type.value}|{url}|{title}".encode()).hexdigest()[:32],
        )
        if not created:
            # The retry of a call whose answer was lost, or the same claim about another entity: either
            # way the links this call asked for are made (idempotent) and reported, so the agent is not
            # told nothing while believing it linked the evidence (B3-055).
            return {
                "id": node.id,
                "links": [self._link(node.id, e, t, module, claims) for e, t in plan],
                "created": False,
            }
        return {"id": node.id, "links": self._link_all(node, plan, module, claims), "created": True}

    def _count_entity_link(self, claims: RunClaims, target: str) -> None:
        """At most ``MAX_LINKS_TO_ONE_ENTITY_PER_RUN`` links from one run to one entity: a run recording
        hundreds of pieces of evidence about one entity is spam, and made it too big to delete (B3-069)."""
        if node_type_of(target) not in ENTITIES:
            return
        try:
            self._ctx.ops_table.update_item(
                Key={"pk": f"LINKS#{claims.run_id}", "sk": target},
                UpdateExpression="ADD n :one SET #ttl = if_not_exists(#ttl, :ttl)",
                ConditionExpression="attribute_not_exists(n) OR n < :cap",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":one": 1,
                    ":cap": MAX_LINKS_TO_ONE_ENTITY_PER_RUN,
                    ":ttl": int(self._ctx.clock.now().timestamp()) + 86_400,
                },
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            raise ForbiddenError(
                f"this run has linked {MAX_LINKS_TO_ONE_ENTITY_PER_RUN} nodes to {target}; that is enough"
            ) from err

    def _link(
        self,
        source: str,
        edge: EdgeType,
        target: str,
        module: ModuleDefinition,
        claims: RunClaims,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        self._count_entity_link(claims, target)
        e, created = self._ctx.graph.link(
            source, edge, target, self._actor(module), confidence=confidence, run_id=claims.run_id
        )
        return {**e.to_public(), "created": created}

    def _owns_output(self, node: Node, module: ModuleDefinition) -> bool:
        return (
            node.module == module.slug
            and node.type is module.output_kind
            and (module.insight_kind is None or node.insight_kind == module.insight_kind)
        )

    def kg_link(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        edge = Registry.edge_type(b.text("type", max_len=40))
        if edge is EdgeType.HAS_DRAFT:
            # Only draft_save makes an idea's draft link: deleting an idea deletes its drafts, so a link an injected
            # run hung from its own idea to another idea's draft would delete that draft too (security review r54).
            raise ForbiddenError("has_draft links are made by draft_save only")
        source_id, target_id = b.text("from", max_len=200), b.text("to", max_len=200)
        # The source is read like any node: a private entity was a valid source for an outbound module,
        # which could hang edges off it and learn from the 200 that it exists (B3-014).
        source = self._readable(module, self._ctx.graph.get(source_id))
        if source.category is Category.OUTPUT:
            # module.tools, not the token's copy: a grant an admin removed mid-run stops working at once.
            if "kg_save_output" not in module.tools or not self._owns_output(source, module):
                raise ForbiddenError("a module can only link from outputs it created")
        elif "kg_add_evidence" not in module.tools:
            raise ForbiddenError("this module cannot link evidence or entities")
        elif source.category is Category.EVIDENCE and source.module != module.slug:
            # Evidence belongs to whoever recorded it: another module (or a person) vouched for its links.
            raise ForbiddenError("a module can only link from evidence it recorded")
        elif source.category is Category.ENTITY and source.created_by != self._actor(module):
            # A relation between entities is a claim about them: an injected scout made a named person
            # "affiliated with" a rival, between two entities people had created (B3-067). A module
            # relates only entities it created itself; a person reviews the rest.
            raise ForbiddenError("a module can only relate entities it created")
        # A hidden target is a 404 like any other read: attacker-page text was being attached ABOUT
        # private-writer entities that people see, with existence as the only check.
        self._readable(module, self._ctx.graph.get(target_id))
        conf = b.raw.get("confidence")
        return self._link(
            source_id,
            edge,
            target_id,
            module,
            claims,
            _confidence(conf),
        )

    def _check_plan(self, source_type: NodeType, plan: list[tuple[EdgeType, str]], module: ModuleDefinition) -> None:
        """Reject a create whose links cannot succeed, before anything is written. A target this module
        may not read counts as missing, so it cannot attach text to a node it cannot see."""
        for edge, target in plan:
            Registry.check_edge(edge, source_type, node_type_of(target))
        hidden = self._hider(module)
        found = self._ctx.graph.get_many([t for _, t in plan])
        missing = sorted({t for _, t in plan} - {i for i, n in found.items() if not hidden(n)})
        if missing:
            raise ValidationError("linked nodes do not exist", {"missing": missing[:20]})

    def _link_all(
        self, node: Node, plan: list[tuple[EdgeType, str]], module: ModuleDefinition, claims: RunClaims
    ) -> list[dict[str, Any]]:
        """Link a node created in this call; if any link fails, delete the node so a retry starts clean."""
        try:
            return [self._link(node.id, edge, target, module, claims) for edge, target in plan]
        except Exception:
            self._ctx.graph.delete(node.id)
            raise

    def _output_node(self, claims: RunClaims, module: ModuleDefinition, b: Body, base: Node | None = None) -> Node:
        if module.output_kind is None:
            raise ForbiddenError("this module does not produce outputs")
        attrs = b.mapping("attributes", required=False) if b.has("attributes") or base is None else base.attributes
        for key in REQUIRED_OUTPUT_ATTRIBUTES.get(module.output_kind, ()):
            if not isinstance(attrs.get(key), str) or not str(attrs.get(key)).strip():
                raise ValidationError(f"{module.output_kind.value} outputs need attributes.{key}")
        if module.attribute_schema and (b.has("attributes") or base is None):
            # The module's own fields: every problem in one error, so the agent fixes them in one retry.
            attrs = module.attribute_schema.check(attrs)
        conf = b.raw.get("confidence", base.confidence if base else None)
        return Node(
            id=base.id if base else "",
            type=module.output_kind,
            title=b.text("title", max_len=200, required=base is None, default=base.title if base else ""),
            summary=b.text("summary", required=False, max_len=2000, default=base.summary if base else ""),
            description=b.text("description", required=False, max_len=40_000, default=base.description if base else ""),
            confidence=_confidence(conf),
            insight_kind=module.insight_kind,
            attributes=attrs,
            module=module.slug,
            # An update by a run that read private material stamps the node private; nothing lowers it
            # (B3-001). The repository keeps it sticky either way.
            private=(base is not None and base.private) or self._writes_private(claims, module),
        )

    def kg_save_output(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        if module.output_kind is NodeType.CONTENT_DRAFT:
            # Drafts only through draft_save: this path leaves a draft with no markdown, no HAS_DRAFT
            # link and no binding to the run's idea, which lists but 404s on open.
            raise ForbiddenError("content drafts are saved with draft_save, not kg_save_output")
        draft = self._output_node(claims, module, b)
        plan = [
            (edge, target)
            for key, edge in (
                ("supports", EdgeType.SUPPORTS),
                ("contradicts", EdgeType.CONTRADICTS),
                ("about", EdgeType.ABOUT),
                ("addresses", EdgeType.ADDRESSES),
            )
            for target in _ids(b, key)
        ]
        self._check_plan(draft.type, plan, module)
        twin = self._same_title_output(draft, module)
        if twin is not None:
            return self._fold_into(twin, plan, module, claims)
        node = self._ctx.graph.create(draft, self._actor(module))
        links = self._link_all(node, plan, module, claims)
        if module.output_kind in (NodeType.SIGNAL, NodeType.TREND) and not b.texts("supports"):
            log.warning("output %s saved without supporting evidence", node.id)
        return {"id": node.id, "links": links, "app_path": f"/insights/nodes/{node.id}"}

    def _same_title_output(self, draft: Node, module: ModuleDefinition) -> Node | None:
        """This module's output of the same type and title among the newest it wrote: a second save made a
        twin a reader saw twice, and a closed gap or retired idea the need came back to (CTR HC-126/127)."""
        key = NameNormalizer.title_key(draft.title)
        actor = self._actor(module)
        recent = self._ctx.graph.recent(draft.type, insight_kind=draft.insight_kind, limit=TWIN_SCAN)
        return next(
            (n for n in recent.items if n.created_by == actor and NameNormalizer.title_key(n.title) == key), None
        )

    def _fold_into(
        self, twin: Node, plan: list[tuple[EdgeType, str]], module: ModuleDefinition, claims: RunClaims
    ) -> dict[str, Any]:
        """Link the new evidence to the existing output instead of saving a twin, and reopen it when its
        life had ended: the need is evidenced again. A reopen that loses a race leaves the node as the
        other writer set it; the links are the result either way."""
        reopened = False
        if lifecycle_ended(twin):
            attrs = {k: v for k, v in twin.attributes.items() if k != "retired_reason"}
            try:
                self._ctx.graph.update(
                    twin.id, {"attributes": {**attrs, "status": "open"}}, twin.version, self._actor(module)
                )
                reopened = True
            except ConflictError:
                log.info("reopen of %s lost a race; keeping the other writer's status", twin.id)
        links = [self._link(twin.id, edge, target, module, claims) for edge, target in plan]
        return {
            "id": twin.id,
            "links": links,
            "app_path": f"/insights/nodes/{twin.id}",
            "existing": True,
            "reopened": reopened,
            "note": "an output with this title exists, so the links were added to it; change its text with "
            "kg_update_output",
        }

    def kg_update_output(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        current = self._ctx.graph.get(b.text("id", max_len=200))
        if not self._owns_output(current, module):
            raise ForbiddenError("a module can only update outputs it created")
        draft = self._output_node(claims, module, b, current)
        changes = {
            "title": draft.title,
            "summary": draft.summary,
            "description": draft.description,
            "attributes": draft.attributes,
            "confidence": draft.confidence,
            "private": draft.private,
        }
        plan = [
            (edge, target)
            for key, edge in (
                ("supports", EdgeType.SUPPORTS),
                ("contradicts", EdgeType.CONTRADICTS),
                ("about", EdgeType.ABOUT),
                ("addresses", EdgeType.ADDRESSES),
            )
            for target in _ids(b, key)
        ]
        # Before the update, not after: a missing target used to 404 with the new text already stored and
        # the version bumped, so the agent's retry with the same expected_version got a 409.
        self._check_plan(draft.type, plan, module)
        updated = self._ctx.graph.update(current.id, changes, b.integer("expected_version"), self._actor(module))
        links = [self._link(updated.id, edge, target, module, claims) for edge, target in plan]
        return {"id": updated.id, "version": updated.version, "links": links}

    # ---- product knowledge ---------------------------------------------------------

    def product_knowledge_search(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        if self._ctx.knowledge is None:
            return {"results": [], "note": "product knowledge is not configured"}
        if module.product_id != claims.product_id:
            # An admin changed or cleared the module's product mid-run: the token's copy is not the
            # authority, and the run must not keep searching the old product's private documents.
            raise ForbiddenError("this module's product changed; the run must stop and be restarted")
        profile = self._ctx.products.get(module.product_id) if module.product_id else None
        passages = self._ctx.knowledge.retrieve(
            b.text("query", max_len=1000), profile, b.integer("limit", required=False, default=6, low=1, high=20)
        )
        return {"results": [p.to_public() for p in passages]}

    def product_profile(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        if module.product_id != claims.product_id:
            # As product_knowledge_search: the module's product changed mid-run (B3-015).
            raise ForbiddenError("this module's product changed; the run must stop and be restarted")
        if claims.product_id:
            return self._ctx.products.get(claims.product_id).to_public()
        return {"profiles": [p.to_public() for p in self._ctx.products.list_all()]}

    # ---- sources -------------------------------------------------------------------

    def source_list(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        """The module's sources as ``https://<host>`` only: the scout reads untrusted pages while holding
        a fetch tool, so a key in its context is one injected GET from leaking. An exact-name allowlist of
        credential query parameters was not enough -- it missed ``appid``, ``code``, ``private_token`` and
        friends, and never covered a credential in the path -- so the stored address is not shown at all.
        The model fetches a source by its id (``http_fetch`` with ``source_id``); the runtime resolves the
        real address through ``source_address``, which the model is never given."""
        out = []
        enabled = [s for s in self._ctx.modules.list_sources(module.slug) if s.enabled]
        # What the source watcher found new on each source (modules/watch.py): a watched source with an empty inbox
        # has nothing to read this run.
        watch = self._ctx.watch.many(module.slug, [s.id for s in enabled])
        for s in enabled:
            body = s.to_public()
            origin = UrlGuard.origin(s.address)
            body["address"] = origin
            if s.address.rstrip("/") != origin:
                body["address_redacted"] = True
            state = watch.get(s.id)
            # Watched only when the last check could list it: a page the watcher cannot read or list (an error, a
            # JavaScript-only page) is read by the Scout as before, with its own tools.
            body["watched"] = bool(state and state.checked_at and state.status == "ok")
            # Every value below came from the source's pages or a fetch of its stored address: a link resolved
            # against a path that holds a token, or an error quoting the address, would carry the credential the
            # address redaction above keeps out of the model's context. Scrubbed the way http_fetch scrubs.
            clean = UrlGuard.source_scrubber(s.address, s.id)
            watched = state.to_public() if state else None
            if watched:
                watched["error"] = clean(str(watched["error"]))
            body["watch"] = watched
            body["inbox"] = [
                # The fingerprint is the backend's own hash, sent back to acknowledge the item: never rewritten.
                {"fp": str(p.get("fp", "")), **{k: clean(str(p.get(k, ""))) for k in ("title", "url", "date")}}
                for p in (state.pending if state else [])
            ][:INBOX_SHOWN]
            out.append(body)
        return {"sources": out, "note": "fetch a source with http_fetch and its source_id, not by typing its address"}

    def source_inbox_ack(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        """Runtime-only (no tool spec gives it to the model): the runtime clears the inbox items of a pass it
        finished, so a run cut short by the budget leaves them for the next one."""
        source_id = b.text("source_id", max_len=40)
        fps = b.raw.get("fps")
        if not isinstance(fps, list) or not all(isinstance(f, str) and len(f) <= 40 for f in fps) or len(fps) > 500:
            raise ValidationError("fps must be a list of at most 500 item fingerprints")
        self._ctx.modules.get_source(module.slug, source_id)  # a typo is a 404, not a silent no-op
        return {"id": source_id, "acked": self._ctx.watch.ack(module.slug, source_id, fps)}

    def source_address(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        """The stored address of one of this module's enabled sources, credential included, for the
        runtime's ``http_fetch``. Runtime-only: no tool spec exposes it to the model."""
        s = self._ctx.modules.get_source(module.slug, b.text("source_id", max_len=40))
        if not s.enabled:
            raise NotFoundError(f"source {s.id} not found")
        return {"id": s.id, "type": s.type.value, "address": s.address}

    def source_mark_read(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        """Runtime-only (no tool spec gives it to the model): the runtime calls it after each fetch of a
        source, ``ok`` false when the fetch failed, so the Scout's pass order follows real attempts and the
        "Last read" column real reads, not cursor saves or admin edits."""
        source_id = b.text("source_id", max_len=40)
        ok = b.flag("ok", required=False, default=True)
        return {"id": source_id, "at": self._ctx.modules.mark_source_read(module.slug, source_id, ok), "ok": ok}

    def source_update_cursor(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        s = self._ctx.modules.update_cursor(
            module.slug, b.text("source_id", max_len=40), b.text("cursor", max_len=2000)
        )
        return {"id": s.id, "cursor": s.cursor}

    # ---- radars --------------------------------------------------------------------

    def radar_list(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        return {
            "radars": [
                {
                    "slug": r.slug,
                    "name": r.name,
                    "purpose": r.purpose,
                    "mode": r.mode.value,
                    "schema": r.schema.to_public(),
                    "entry_count": r.entry_count,
                }
                for r in self._ctx.radars.list_all()
                if r.mode.value != "manual"
            ]
        }

    def radar_get(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        slug = b.text("slug", max_len=64)
        radar = self._ctx.radars.get(slug)
        if radar.mode.value == "manual":  # like radar_list: a manual radar is not the curator's to read
            raise NotFoundError(f"radar {slug} not found")
        entries = self._ctx.radars.entries(slug) if radar.current_version else []
        return {
            "slug": slug,
            "mode": radar.mode.value,
            "schema": radar.schema.to_public(),
            "entries": [e.to_public() for e in entries],
            # What each open suggestion proposes, so the curator neither repeats a new-entry proposal
            # (its key is fresh, so it matches no entry) nor revises an entry under review.
            "open_suggestions": [
                {
                    "entry_key": s["entry_key"],
                    "is_new": bool(s.get("is_new")),
                    "proposal": {k: (s.get("proposal") or {}).get(k) for k in ("title", "sector", "ring", "marker")},
                }
                for s in self._ctx.radars.suggestions(slug)
            ],
        }

    def radar_suggest(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        proposal = b.mapping("proposal")
        evidence = _ids(b, "evidence")
        hidden = self._hider(module)
        found = self._ctx.graph.get_many(evidence) if evidence else {}
        # A node this module may not read counts as missing, as in kg_get: the curator could cite a gap
        # it is refused, and autopilot counted it (B3-013).
        missing = sorted(set(evidence) - {i for i, n in found.items() if not hidden(n)})
        if missing:  # a proposal must not look evidenced by nodes that do not exist
            raise ValidationError("evidence nodes do not exist", {"missing": missing[:20]})
        return self._ctx.radars.propose(
            b.text("slug", max_len=64),
            self._actor(module),
            claims.run_id,
            proposal,
            b.text("reason", max_len=2000),
            b.opt_text("entry_key", 64),
            evidence,
        )

    # ---- drafts --------------------------------------------------------------------

    def draft_get_idea(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        idea_id = b.text("id", max_len=200)
        if node_type_of(idea_id) is not NodeType.CONTENT_IDEA:
            raise ValidationError("id must be a content idea")
        # The same hider as kg_get: this tool used to return an idea (and the private gaps it addresses)
        # that kg_get refused, which mattered as soon as the draft module lost product_knowledge.
        self._readable(module, self._ctx.graph.get(idea_id))
        body = self._ctx.insights.detail(idea_id, None)
        groups: dict[str, list[dict[str, Any]]] = body.get("evidence") or {}
        hidden = self._hider(module)
        ids = [str((e.get("node") or {}).get("id") or "") for entries in groups.values() for e in entries]
        found = self._ctx.graph.get_many([i for i in ids if i])
        blocked = {i for i, n in found.items() if hidden(n)}
        body["evidence"] = {
            group: [e for e in entries if (e.get("node") or {}).get("id") not in blocked]
            for group, entries in groups.items()
        }
        # Who is working on the idea is for people, not the model: give the agent a count, not emails.
        body["working_on"] = len(body.get("working_on", []))
        return body

    def draft_save(self, claims: RunClaims, module: ModuleDefinition, b: Body) -> Any:
        idea_id = b.text("idea_id", max_len=200)
        if node_type_of(idea_id) is not NodeType.CONTENT_IDEA:
            raise ValidationError("idea_id must be a content idea")
        run = self._ctx.modules.get_run(claims.module, claims.run_id)
        if not run.target_id:
            # Without a target any draft run (an admin's manual run, or the module given an interval)
            # could write a draft for every content idea there is.
            raise ForbiddenError("a draft run must be started for one content idea")
        if run.target_id != idea_id:
            raise ForbiddenError("a draft run can only save a draft for the idea it was started for")
        self._readable(module, self._ctx.graph.get(idea_id))
        markdown = b.text("markdown", max_len=DRAFT_MAX_BYTES)
        if len(markdown.strip()) < 200:
            raise ValidationError("the draft is too short to save")
        draft = NodeFactory.validate(
            Node(
                id=self._ctx.graph.new_id(NodeType.CONTENT_DRAFT),
                type=NodeType.CONTENT_DRAFT,
                title=b.text("title", max_len=200),
                summary=HtmlSanitizer.to_text(markdown)[:400],
                module=module.slug,
                private=self._writes_private(claims, module),
                attributes={"run_id": claims.run_id, "idea_id": idea_id},
            )
        )
        key = f"drafts/{draft.id.split('/', 1)[1]}.md"
        # The text first, then the node that points at it: a failed put leaves nothing behind, never a
        # draft node with no markdown.
        self._ctx.s3.put_object(
            Bucket=self._ctx.settings.assets_bucket,
            Key=key,
            Body=markdown.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
        node = self._ctx.graph.create(
            replace(draft, attributes={**draft.attributes, "markdown_key": key}), self._actor(module)
        )
        try:
            self._link(idea_id, EdgeType.HAS_DRAFT, node.id, module, claims)
        except Exception:
            # Without the link the draft is an orphan: listed, unopenable and pointing at nothing.
            self._ctx.graph.delete(node.id)
            self._ctx.s3.delete_object(Bucket=self._ctx.settings.assets_bucket, Key=key)
            raise
        return {"id": node.id, "app_path": f"/insights/nodes/{node.id}"}
