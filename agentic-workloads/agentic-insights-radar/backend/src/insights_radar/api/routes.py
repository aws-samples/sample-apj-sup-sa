# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""User-facing API routes (``/api/*``, Cognito JWT).

Each handler: resolve the principal from verified claims, ask ``Policy`` (and the radar ACL where
relevant), validate the body, call one service, write an audit row for mutations. Node ids
(``<type>/<uuid>``) contain a slash, so routes take them as two path segments.
"""

from __future__ import annotations

import hashlib
import html
import itertools
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast
from urllib.parse import parse_qs, urlencode

from botocore.exceptions import ClientError

from insights_radar.api.agent_tools import AgentTools, run_module
from insights_radar.api.body import Body
from insights_radar.api.http import Request, Response, Router, ok
from insights_radar.core.clock import parse_iso
from insights_radar.core.defang import Defang
from insights_radar.core.errors import (
    AccountDisabledError,
    ConflictError,
    ForbiddenError,
    NoAccountError,
    NotFoundError,
    UnauthorizedError,
    UnsubscribeRetryError,
    UnsubscribeUnavailableError,
    ValidationError,
)
from insights_radar.core.ratelimit import DailyQuota, RateLimiter
from insights_radar.core.urls import UrlGuard
from insights_radar.graph.ask import GraphAsk
from insights_radar.graph.model import node_type_of
from insights_radar.graph.moderation import MAX_DELETE_EDGES, NodeRemoval
from insights_radar.graph.registry import ENTITIES, NodeType, Registry
from insights_radar.graph.sample import RADAR_SLUG, SampleData
from insights_radar.identity.policy import Action, Policy
from insights_radar.identity.principal import Principal, PrincipalKind, Role, RoleCaller
from insights_radar.identity.users import THROTTLE_GRACE_SECONDS
from insights_radar.insights import export
from insights_radar.insights.delivery import (
    MAX_CHANNELS,
    MAX_LIST_VERSION,
    SUPPRESSED_MESSAGE,
    TIMEZONE_MAX,
    WEBHOOK_CHANNEL,
    DeliveryKind,
    DeliveryService,
    DigestChannel,
    DmResolver,
    SlackMode,
    Target,
)
from insights_radar.insights.delivery_runner import UNSUBSCRIBE_PATH, digest_total, nothing_sent
from insights_radar.insights.delivery_transport import ALIAS_RE, TransientDeliveryError, UnsubscribeTokens
from insights_radar.insights.digest import custom_lists, reader_view
from insights_radar.insights.service import UPCOMING_DAYS, AttrQuery, InsightKind, Sort
from insights_radar.modules.attributes import AttributeSchema, parse_schema
from insights_radar.modules.catalog import ModelCatalog
from insights_radar.modules.model import (
    DESCRIPTION_MAX,
    INSTRUCTIONS_MAX,
    MAX_MODULE_SKILLS,
    NAME_MAX,
    PRIVATE_OUTPUT_TYPES,
    SOURCE_CURSOR_MAX,
    SOURCE_INSTRUCTION_MAX,
    SOURCE_NAME_MAX,
    SYSTEM_PROMPT_MAX,
    McpServer,
    ModuleDefinition,
    ModuleTemplate,
    SourceType,
    ToolGrant,
    private_hider,
)
from insights_radar.modules.prompts import SystemPrompt, read_template, same_text
from insights_radar.modules.skills import MAX_SKILL_BYTES
from insights_radar.product.model import (
    MAX_CAPABILITIES,
    MAX_DOC_URLS,
    MAX_LIST_ITEM,
    PRODUCT_NAME_MAX,
    PRODUCT_TEXT_MAX,
    ProductProfile,
)
from insights_radar.profile.model import list_key
from insights_radar.profile.repository import VERSION_MAX
from insights_radar.radars.model import (
    DIM_NAME_MAX,
    ENTRY_DESCRIPTION_MAX,
    ENTRY_KEY_RE,
    ENTRY_TITLE_MAX,
    TEMPLATES,
    RadarEntry,
    RadarSchema,
)

log = logging.getLogger(__name__)


RELOAD_MESSAGE = "this page is out of date: reload it and try again"


def channel_list(channels: list[DigestChannel], version: int) -> dict[str, Any]:
    """The admin view of the channel list: names as stored, the version a save must quote, and each group of
    IDs whose names read the same (subscribers see all but the first as '<name> (<id>)' until renamed) (c31-c35)."""
    return {
        "channels": [c.to_public() for c in channels],
        "version": version,
        "clashes": DeliveryService.clashes(channels),
    }


def channel_changes(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    """What a save of the channel list changed, as lists of IDs the audit log can split over continued rows; the
    names of each renamed channel go on a row of their own (see ``digest_channels_put``) (scan cycles 28 to 30)."""
    return {
        "added": [cid for cid in after if cid not in before],
        "removed": [cid for cid in before if cid not in after],
        "renamed": [cid for cid, name in after.items() if cid in before and before[cid] != name],
    }


# The Overview's counts per list, shared by callers who see the same records (see insights_facets).
FACETS_CACHE_SECONDS = 60
FACETS_CACHE_MAX = 64
# A SigV4 caller's account is looked up again after this many seconds (as LiveRoles re-reads a role),
# and at most this many are kept per Lambda container.
LIVE_ROLE_TTL = 30.0
IAM_SEEN_MAX = 500
# The unsubscribe link in a digest email works without signing in: its token names one delivery.
PUBLIC_ROUTES = frozenset({("GET", "/api/config"), ("GET", UNSUBSCRIBE_PATH), ("POST", UNSUBSCRIBE_PATH)})
DRAFT_BRIEF_MAX = 2000
MAX_MODULES = 50
EXPLORE_SCAN_CAP = 150
# One editor must not be able to drain the drafts module's shared daily budget for everyone.
DRAFTS_PER_USER_PER_DAY = 10
# Templates that act on one node of a fixed type when given a target.
TEMPLATE_TARGETS: dict[ModuleTemplate, tuple[NodeType, ...]] = {ModuleTemplate.DRAFT: (NodeType.CONTENT_IDEA,)}
# What a non-manager sees of a module source.
SOURCE_SUMMARY = ("id", "module", "name", "type", "enabled")


UNSUBSCRIBE_NOT_USED = "Unsubscribe links are not used on this deployment, so there is nothing to turn off."
UNSUBSCRIBE_BAD_LINK = "This unsubscribe link is incomplete or no longer valid."


def _answer_code(answer: dict[str, Any]) -> str:
    """The error code in a JSON answer's body, or "" (only compared with fixed codes, never shown)."""
    try:
        body = json.loads(answer.get("body") or "{}")
    except (TypeError, ValueError):
        return ""
    return str(body.get("code", "")) if isinstance(body, dict) else ""


def unsubscribe_page_answer(event: dict[str, Any], answer: dict[str, Any], app_url: str) -> dict[str, Any]:
    """The confirm page's form post (it accepts HTML) gets the answer as a page: a fixed text for each status, the
    same status and headers (Retry-After) as the JSON answer, and nothing taken from the error itself. Applied
    once, after every handler and error handler has answered, so success and failure decide 'page or JSON' in one
    place (scan cycles 38 to 40)."""
    ctx_http = (event.get("requestContext") or {}).get("http") or {}
    path = event.get("rawPath") or ctx_http.get("path")  # the same fallbacks as Request.from_event (c41)
    if path != UNSUBSCRIBE_PATH:  # checked first: every other request returns here at once
        return answer
    method = str(ctx_http.get("method") or event.get("httpMethod") or "").upper()
    # Request.from_event's own rule: lower-cased keys, so the last of any case variants wins (scan cycles 42, 43).
    accept_value = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}.get("accept", "")
    if method != "POST" or "text/html" not in accept_value:
        return answer
    status = int(answer.get("statusCode", 500))
    if app_url:
        link = html.escape(f"{app_url}/whats-new", quote=True)
        elsewhere = f'sign in at <a href="{link}">{link}</a> and turn the delivery off there'
    else:
        elsewhere = "sign in to the app and turn the delivery off under What's new"
    if 200 <= status < 300:
        title, text = "Unsubscribed", "You are unsubscribed. This digest email will not be sent again."
    elif status == 404:
        title, text = "Could not unsubscribe", UNSUBSCRIBE_NOT_USED
    elif status == 410:
        title, text = "Could not unsubscribe", f"This unsubscribe link has expired. To stop the email, {elsewhere}."
    elif 400 <= status < 500:  # a bad, cut-short or no longer valid link, or a request the API could not take (c41)
        title, text = (
            "Could not unsubscribe",
            f"{UNSUBSCRIBE_BAD_LINK} Open the link in the email again, or {elsewhere}.",
        )
    elif _answer_code(answer) == UnsubscribeUnavailableError.code:
        # Only an operator can fix it, so retrying the link will not help. Decided by the answer's code, the same
        # rule as the app's page, so the two never disagree (scan cycles 49 to 51).
        title, text = (
            "Could not unsubscribe",
            "Something went wrong on our side, and trying the link again will not help."
            f" To stop the email, {elsewhere}.",
        )
    else:
        title, text = (
            "Could not unsubscribe",
            f"Something went wrong on our side. Try the link again later, or {elsewhere}.",
        )
    page = _html_page(title, f"<p>{text}</p>", status).to_event()
    kept = {
        k: v
        for k, v in (answer.get("headers") or {}).items()
        if k.lower() in {"retry-after", "access-control-allow-origin", "vary"}
    }
    return {**page, "headers": {**page["headers"], **kept}}


def _html_page(title: str, body: str, status: int = 200) -> Response:
    """A minimal page with no script, no style and no outside resource; it may only post a form to this origin.
    ``body`` is built by the caller from fixed text and escaped values."""
    page = (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        f"</head><body><h1>{html.escape(title)}</h1>{body}</body></html>"
    )
    return Response(
        status,
        raw=page,
        content_type="text/html; charset=utf-8",
        headers={"Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'"},
    )


def _node_id(params: dict[str, str]) -> str:
    node_id = f"{params['type']}/{params['uid']}"
    try:
        node_type_of(node_id)
    except ValueError as err:
        raise NotFoundError(f"node {node_id} not found") from err
    if params["uid"] in (".", ".."):  # no record has one; a route that writes must not accept it (campaign r2)
        raise NotFoundError(f"node {node_id} not found")
    return node_id


def _entries(raw: list[Any]) -> list[RadarEntry]:
    out = []
    for i, e in enumerate(raw, 1):
        if not isinstance(e, dict):
            raise ValidationError(f"entry {i} must be an object")
        b = Body(e)
        key = b.text("key", max_len=64)
        if not ENTRY_KEY_RE.fullmatch(key):  # a rewritten key would no longer match the client's row
            raise ValidationError(f"entry {i}: key may only use letters, digits and . _ ~ : @ -")
        out.append(
            RadarEntry(
                key=key,
                title=b.text("title", max_len=ENTRY_TITLE_MAX),
                sector=b.text("sector", max_len=DIM_NAME_MAX),
                ring=b.text("ring", max_len=DIM_NAME_MAX),
                marker=b.opt_text("marker", DIM_NAME_MAX),
                description=b.text("description", required=False, max_len=ENTRY_DESCRIPTION_MAX),
                node_id=b.opt_text("node_id", 200),
                link=b.opt_text("link", 2048),
                sub_sector=b.opt_text("sub_sector", 120),
            )
        )
    return out


def _module_delta(before: ModuleDefinition, after: ModuleDefinition) -> dict[str, Any]:
    """What a module PATCH actually changed, not just which fields it named: which grants it gained,
    which MCP servers it now has, whether it is armed, and which product it reads."""
    out: dict[str, Any] = {}
    gained = sorted(g.value for g in after.grants - before.grants)
    lost = sorted(g.value for g in before.grants - after.grants)
    if gained:
        out["grants_added"] = gained
    if lost:
        out["grants_removed"] = lost
    if before.mcp_servers != after.mcp_servers:
        out["mcp_servers"] = {
            "before": [s.name for s in before.mcp_servers],
            "after": [s.name for s in after.mcp_servers],
        }
    for field in ("product_id", "enabled", "model_key", "interval_minutes", "daily_token_budget", "max_concurrency"):
        old, new = getattr(before, field), getattr(after, field)
        if old != new:
            out[field] = {"before": old, "after": new}
    if before.input_types != after.input_types:
        out["input_types"] = [t.value for t in after.input_types]
    if before.skills != after.skills:
        out["skills"] = {"before": list(before.skills), "after": list(after.skills)}
    if before.system_prompt != after.system_prompt:
        out["system_prompt_sha256"] = {"before": _digest(before.system_prompt), "after": _digest(after.system_prompt)}
    if before.instructions != after.instructions:
        # The prompt itself is too long for a row, but its hashes say what it was and became, and match
        # the archived definition when the module is deleted (B3-042).
        out["instructions_sha256"] = {"before": _digest(before.instructions), "after": _digest(after.instructions)}
    return out


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _source_delta(before: Any, after: Any) -> dict[str, Any]:
    """A source's address can carry a credential in its query or its path, so only its origin and a hash
    are recorded (SR2-08)."""
    out: dict[str, Any] = {}
    if before.address != after.address:
        out["address"] = {
            "before": UrlGuard.audit_form(before.address),
            "after": UrlGuard.audit_form(after.address),
        }
    if before.enabled is not after.enabled:
        out["enabled"] = {"before": before.enabled, "after": after.enabled}
    for field in ("name", "cursor"):
        old, new = getattr(before, field), getattr(after, field)
        if old != new:
            out[field] = {"before": str(old)[:200], "after": str(new)[:200]}
    if before.type is not after.type:
        out["type"] = {"before": before.type.value, "after": after.type.value}
    return out


def _graph_brief(node: Any) -> dict[str, Any]:
    """What the map and the neighbour panel need. Full ``to_public()`` nodes carry 40,000-character
    descriptions, so 150 of them could push the response past the 6 MB Lambda limit and fail the
    page for every viewer."""
    body: dict[str, Any] = {
        "id": node.id,
        "type": node.type.value,
        "category": node.category.value,
        "title": node.title,
        "summary": node.summary[:300],
        "degree": node.degree,
        "updated_at": node.updated_at,
    }
    for key in ("evidence_url", "insight_kind", "module"):
        value = getattr(node, key)
        if value is not None:
            body[key] = value
    return body


def _radar_delta(before: Any, after: Any) -> dict[str, Any]:
    """The security-relevant values a radar PATCH changed, not just the field names: whether
    unattended applies were switched on, and how short the review delay now is."""
    out: dict[str, Any] = {}
    for field in ("mode", "auto_apply_agent", "auto_apply_days"):
        old, new = getattr(before, field), getattr(after, field)
        if old != new:
            out[field] = {"before": getattr(old, "value", old), "after": getattr(new, "value", new)}
    return out


def _mcp_server(raw: Any) -> McpServer:
    if not isinstance(raw, dict):
        raise ValidationError("each mcp_servers item must be an object with name and url")
    sb = Body(raw)
    return McpServer(sb.text("name", max_len=40), sb.text("url", max_len=2048))


def _module_from_body(b: Body) -> ModuleDefinition:
    grants = frozenset(
        ToolGrant(g)
        for g in b.texts("grants", required=True, max_items=20, max_len=40)
        if g in ToolGrant._value2member_map_
    )
    if len(grants) != len(set(b.texts("grants", required=True))):
        raise ValidationError("grants contains an unknown value")
    servers = [_mcp_server(s) for s in b.items("mcp_servers", required=False, max_items=20)]
    input_types = tuple(Registry.node_type(t) for t in b.texts("input_types", max_items=16, max_len=40))
    return ModuleDefinition(
        slug=b.text("slug", max_len=64),
        name=b.text("name", max_len=NAME_MAX),
        template=ModuleTemplate.CUSTOM,
        output_kind=NodeType.INSIGHT,
        description=b.text("description", required=False, max_len=DESCRIPTION_MAX),
        instructions=b.text("instructions", required=False, max_len=INSTRUCTIONS_MAX),
        insight_kind=b.text("insight_kind", max_len=60),
        insight_label=b.opt_text("insight_label", 40),
        input_types=input_types,
        grants=grants,
        product_id=b.opt_text("product_id", 40),
        model_key=b.text("model_key", required=False, default="sonnet-5-5"),
        interval_minutes=b.integer("interval_minutes", required=False, default=360),
        daily_token_budget=b.integer("daily_token_budget", required=False, default=2_000_000),
        max_concurrency=b.integer("max_concurrency", required=False, default=1),
        mcp_servers=tuple(servers),
        attribute_schema=parse_schema(b.raw.get("attribute_schema")),
        prompt_mode=b.text("prompt_mode", required=False, default="extend", max_len=20),
        enabled=b.flag("enabled", required=False, default=False),
    )


_MODULE_EDITABLE = (
    "name",
    "description",
    "instructions",
    "insight_label",
    "input_types",
    "grants",
    "product_id",
    "model_key",
    "interval_minutes",
    "daily_token_budget",
    "max_concurrency",
    "mcp_servers",
    "attribute_schema",
    "prompt_mode",
    "system_prompt",
    "skills",
    "enabled",
)


_TEXT_LIMITS = {
    "name": NAME_MAX,
    "description": DESCRIPTION_MAX,
    "instructions": INSTRUCTIONS_MAX,
    "model_key": 40,
    "prompt_mode": 20,
    "system_prompt": SYSTEM_PROMPT_MAX,
}


def _module_changes(b: Body) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key in _MODULE_EDITABLE:
        if not b.has(key):
            continue
        if key in _TEXT_LIMITS:
            changes[key] = b.text(key, max_len=_TEXT_LIMITS[key])
        elif key in ("product_id", "insight_label"):  # nullable: null clears them
            changes[key] = b.opt_text(key, 40)
        elif key in ("interval_minutes", "daily_token_budget", "max_concurrency"):
            changes[key] = b.integer(key)
        elif key == "enabled":
            changes[key] = b.flag(key)
        elif key == "grants":
            values = b.texts(key, required=True, max_items=20)
            if any(v not in ToolGrant._value2member_map_ for v in values):
                raise ValidationError("grants contains an unknown value")
            changes[key] = frozenset(ToolGrant(v) for v in values)
        elif key == "input_types":
            changes[key] = tuple(Registry.node_type(t) for t in b.texts(key, max_items=16))
        elif key == "attribute_schema":
            changes[key] = parse_schema(b.raw.get(key))
        elif key == "skills":
            changes[key] = tuple(b.texts(key, max_items=MAX_MODULE_SKILLS, max_len=64))
        else:  # mcp_servers
            changes[key] = tuple(_mcp_server(s) for s in b.items(key, max_items=20))
    return changes


def build_router(ctx: Any) -> Router:
    r = Router()
    limiter = RateLimiter(ctx.ops_table, ctx.clock, workspace_per_minute=ctx.settings.workspace_rate_per_minute)
    # One per application (a Lambda container), so tests and deployments never share answers.
    facets_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
    drafts_quota = DailyQuota(ctx.ops_table, ctx.clock)

    # The accounts behind SigV4 callers, kept as briefly as LiveRoles keeps a role (an MCP tool makes several
    # calls; a Cognito lookup for each would throttle). Only an enabled account is kept.
    iam_seen: dict[str, tuple[float, Principal]] = {}

    def iam_principal(req: Request) -> Principal:
        """The account behind a SigV4 caller (deploy options mcpIssuer and mcpAudience). The MCP role's trust
        policy makes the session name the web identity token's subject, which is the identity provider's
        user id, so the account is the one Cognito made at that person's first sign-in to the web app:
        ``<provider>_<id>``. No such account, or a disabled one, is refused: this path never creates users,
        and the role is the account's own, read from the pool (at most ``LIVE_ROLE_TTL`` old). The email
        follows the browser path's rule: a federated account's address counts only when the pool marks it
        verified, or the deployment trusts the provider's addresses (oidcTrustEmail)."""
        session = RoleCaller.session_name(ctx.settings.mcp_role_arn, req.source_arn)
        provider = ctx.settings.identity_provider
        if session is None or not provider:
            raise UnauthorizedError("sign in required")
        username = f"{provider.lower()}_{session}"
        now = time.monotonic()
        hit = iam_seen.get(username)
        if hit is not None and now - hit[0] <= LIVE_ROLE_TTL:
            return hit[1]
        try:
            view = ctx.users.get(username)
        except NotFoundError as err:
            iam_seen.pop(username, None)  # a deleted account must not be served from the throttle grace
            raise NoAccountError("sign in to the web app once before using this client") from err
        except ClientError as err:
            # As LiveRoles does: Cognito throttling keeps the last confirmed answer for a bounded grace
            # period, as a viewer only (a role that cannot be confirmed is not trusted). A disabled
            # account is never cached, so it is never softened.
            code = err.response.get("Error", {}).get("Code")
            if code not in ("TooManyRequestsException", "ThrottlingException") or hit is None:
                raise
            if now - hit[0] > LIVE_ROLE_TTL + THROTTLE_GRACE_SECONDS:
                raise
            return replace(hit[1], role=Role.VIEWER)
        if not view.enabled:
            iam_seen.pop(username, None)
            raise AccountDisabledError("this account is disabled")
        verified = ctx.settings.trust_provider_email or view.email_verified.lower() == "true"
        principal = Principal(
            kind=PrincipalKind.USER,
            subject=view.subject or view.username,
            role=view.role,
            email=(view.email or None) if verified else None,
            username=view.username,
        )
        # Least recently refreshed out first: re-inserting moves a user to the end, and a user already
        # cached replaces their own entry rather than evicting someone else's.
        iam_seen.pop(username, None)
        if len(iam_seen) >= IAM_SEEN_MAX:
            iam_seen.pop(next(iter(iam_seen)))
        iam_seen[username] = (now, principal)
        return principal

    def principal_of(req: Request) -> Principal:
        """The caller, with an elevated role confirmed live (a demoted admin's token still says admin)."""
        if req.via_iam:
            return iam_principal(req)
        return cast(
            Principal,
            ctx.live_roles.confirm(
                Principal.from_claims(
                    req.claims,
                    trust_email=ctx.settings.trust_provider_email,
                    federated=bool(ctx.settings.identity_provider),
                )
            ),
        )

    def _archive(context: Any, prefix: str, body: dict[str, Any]) -> str:
        """Keep a deleted definition where an operator can find it, and return its key for the audit
        row. Best effort: the delete has already happened, so a failed archive is logged, not raised."""
        key = f"{prefix}/{context.clock.now().strftime('%Y%m%dT%H%M%S')}.json"
        try:
            context.s3.put_object(
                Bucket=context.settings.assets_bucket,
                Key=key,
                Body=json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"),
                ContentType="application/json",
            )
        except Exception:
            log.exception(json.dumps({"event": "archive.failed", "key": key}))
            return ""
        return key

    def _listable_kinds(p: Principal, mods: list[Any] | None = None) -> set[str]:
        """Custom insight kinds the caller can list: the live modules', and for an administrator the
        deleted modules' too, whose insights stayed readable by id and in search but could not be found
        to review or remove (B3-071)."""
        kinds = {m.insight_kind for m in (ctx.modules.list_all() if mods is None else mods) if m.insight_kind}
        if p.is_admin:
            kinds |= ctx.modules.tombstones()[1]
        return kinds

    def _list_schema(kind: InsightKind, mods: list[Any] | None = None) -> AttributeSchema:
        """The fields of the module that writes this list: the custom module with this insight kind, or the
        built-in module of this output type. Empty when it declares none."""
        for m in ctx.modules.list_all() if mods is None else mods:
            if m.output_kind is kind.node_type and m.insight_kind == kind.insight_kind and m.attribute_schema:
                return cast(AttributeSchema, m.attribute_schema)
        return AttributeSchema()

    def hidden_for(p: Principal) -> Callable[[Any], bool]:
        """What a caller without ``product.read`` (a viewer) may not read: the outputs derived from private
        product documents (gaps, content ideas, drafts) and anything stamped private, by the same rule the
        agent tools' hider applies. ``product.read`` kept raw passages from viewers while every gap and
        draft written from them stayed readable (B3-018). The digest deliveries use the same rule."""
        return reader_view(p, ctx.modules.private_writer_slugs).hidden

    def _first_read_today(p: Principal, node_id: str) -> bool:
        day = ctx.clock.now().strftime("%Y-%m-%d")
        try:
            ctx.ops_table.put_item(
                Item={
                    "pk": f"READ#{day}#{p.subject}",
                    "sk": node_id,
                    "ttl": int(ctx.clock.now().timestamp()) + 2 * 86_400,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return False
        return True

    def audit(p: Principal, action: str, target: str, details: dict[str, Any] | None = None) -> None:
        """Record a committed change. A failed audit write is logged loudly (with the row) rather than
        turned into a 500: the change already happened, and a retry would repeat or conflict with it."""
        try:
            ctx.audit.record(p, action, target, details)
        except Exception:
            log.exception(
                json.dumps({"event": "audit.write_failed", "actor": p.actor, "action": action, "target": target[:300]})
            )

    # ---- config / me ----------------------------------------------------------------
    @r.route("GET", "/api/config")
    def config(req: Request) -> Response:
        s = ctx.settings
        return ok(
            {
                "region": s.region,
                "user_pool_id": s.user_pool_id,
                "user_pool_client_id": s.user_pool_client_id,
                "cognito_domain": s.cognito_domain,
                # The SPA sends people straight to this provider instead of the user pool's own page.
                "identity_provider": s.identity_provider or None,
                "chat_runtime_arn": s.chat_runtime_arn,
                # The deploy options mcpIssuer and mcpAudience: what an MCP client needs to sign in without a browser
                # (an identity token from this issuer for this audience, exchanged for this role). The API Gateway
                # URL is not here: this answer is public, and that origin bypasses CloudFront's firewall, so the
                # operator hands it to clients (stack output McpApiUrl, AIR_MCP_API_URL).
                "mcp_web_identity": (
                    {
                        "role_arn": s.mcp_role_arn,
                        "issuer": s.mcp_issuer,
                        "audience": s.mcp_audience,
                    }
                    if s.mcp_role_arn
                    else None
                ),
                "version": s.solution_version,
                # Cached briefly: this route is unauthenticated.
                "genai_enabled": ctx.modules.andon(cached=True).genai_enabled,
                # Display text only (names, labels, which lists show): public by design.
                "profile": ctx.profile.get(cached=True).to_public(),
            }
        )

    @r.route("GET", "/api/admin/profile")
    def profile_get(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PROFILE_MANAGE)
        prof = ctx.profile.get()
        return ok(
            {
                # Only what this code can edit; the stored fields it does not know are named apart, so the
                # GET, edit, PUT round trip is never refused (code review r19).
                "profile": prof.to_known_item(),
                "unknown": prof.unknown_keys(),
                "effective": prof.to_public(),
                "version": prof.version,
                "updated_by": prof.updated_by,
                "updated_at": prof.updated_at,
            }
        )

    @r.route("PUT", "/api/admin/profile")
    def profile_put(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PROFILE_MANAGE)
        b = Body(req.json())
        b.only("profile", "expected_version", "drop_unknown")
        expected = b.integer("expected_version", low=0, high=VERSION_MAX)
        # Names are checked against the stored ones, so the length cap only bounds the request (code review r20).
        drop = frozenset(b.texts("drop_unknown", max_items=50, max_len=1000))
        saved = ctx.profile.put(b.mapping("profile"), p.actor, expected, drop)
        # The version put() replaced is one below what it wrote: no second read that could disagree.
        details: dict[str, Any] = {"before_version": saved.version - 1, "version": saved.version}
        if drop:
            # The names only (put() refused any that were not stored). Values are not kept: they were written by a
            # release this code does not know, and copying arbitrary stored data into the audit is what kept
            # breaking (code reviews r21, r22).
            details["dropped"] = [_audit_name(name) for name in sorted(drop)]
        audit(p, "profile.set", "global", details)
        return ok(
            {
                "profile": saved.to_known_item(),
                "unknown": saved.unknown_keys(),
                "effective": saved.to_public(),
                "version": saved.version,
                "updated_by": saved.updated_by,
                "updated_at": saved.updated_at,
            }
        )

    @r.route("GET", "/api/chat/status")
    def chat_status(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.CHAT)
        return ok(
            {
                "genai_enabled": ctx.modules.andon().genai_enabled,
                **ctx.chat_budget.status(p.subject),
                # The deployment's own names for its lists, so the assistant speaks them (public text).
                "profile": ctx.profile.get(cached=True).to_public(),
            }
        )

    @r.route("GET", "/api/chat/settings")
    def chat_settings_get(req: Request) -> Response:
        Policy.require(principal_of(req), Action.BUDGET_MANAGE)
        return ok(ctx.chat_budget.limits().to_public())

    @r.route("PUT", "/api/chat/settings")
    def chat_settings_put(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.BUDGET_MANAGE)
        b = Body(req.json())
        b.only("daily_tokens", "per_user_daily_tokens")
        before = ctx.chat_budget.limits().to_public()
        limits = ctx.chat_budget.set_limits(b.integer("daily_tokens"), b.integer("per_user_daily_tokens"))
        audit(p, "chat.settings", "chat", {"before": before, "after": limits.to_public()})
        return ok(limits.to_public())

    @r.route("GET", "/api/me")
    def me(req: Request) -> Response:
        p = principal_of(req)
        return ok(
            {
                "subject": p.subject,
                "email": p.email,
                "role": p.role.value,
                "can": sorted(a.value for a in Action if Policy.allows(p, a)),
            }
        )

    @r.route("GET", "/api/nav")
    def nav(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        custom = [
            {"kind": f"insight:{m.insight_kind}", "label": m.nav_label, "module": m.slug}
            for m in ctx.modules.list_all()
            if m.insight_kind
        ]
        return ok({"builtin": ["signals", "trends", "gaps", "content"], "custom": custom})

    # ---- users ----------------------------------------------------------------------
    @r.route("GET", "/api/users")
    def users_list(req: Request) -> Response:
        Policy.require(principal_of(req), Action.USER_MANAGE)
        users, cursor = ctx.users.list_page(req.int_query("limit", 60, 1, 500), req.query.get("cursor"))
        return ok({"items": [u.to_public() for u in users], "cursor": cursor})

    @r.route("POST", "/api/users")
    def users_invite(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.USER_MANAGE)
        if ctx.settings.identity_provider:
            # No password accounts here: people appear after their first sign-in, then get a role.
            raise ValidationError(
                "this deployment signs people in through its identity provider: they appear in the list "
                "after their first sign-in, and you can then change their role"
            )
        b = Body(req.json())
        b.only("email", "role")
        try:
            role = Role(b.text("role", required=False, default="viewer"))
        except ValueError as err:
            raise ValidationError("role must be admin, editor or viewer") from err
        user = ctx.users.invite(b.text("email", max_len=254), role)
        audit(p, "user.invite", user.username, {"role": role.value})
        return ok(user.to_public(), 201)

    @r.route("PATCH", "/api/users/{username}")
    def users_update(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.USER_MANAGE)
        b = Body(req.json())
        b.only("role", "enabled")
        # The whole body is validated before any change: a bad "enabled" used to fail after the role
        # change had been committed, leaving a role change with no audit row.
        role: Role | None = None
        if b.has("role"):
            try:
                role = Role(b.text("role"))
            except ValueError as err:
                raise ValidationError("role must be admin, editor or viewer") from err
        enabled = b.flag("enabled") if b.has("enabled") else None
        if role is None and enabled is None:
            raise ValidationError("nothing to change")
        target = ctx.users.get(req.path_params["username"])
        demotes_self = role is not None and role is not Role.ADMIN
        if target.username in (p.username, p.subject) and (demotes_self or enabled is False):
            raise ValidationError("you cannot demote or disable your own account; ask another administrator")
        applied: dict[str, Any] = {}
        user = target
        if role is target.role:
            role = None  # nothing to do: the Cognito group add and the audit row would both be noise
        if enabled is target.enabled:
            enabled = None  # likewise for enabling an enabled account (B3-038)
        if role is None and enabled is None:
            return ok(target.to_public())
        try:
            if role is not None:
                user = ctx.users.set_role(target.username, role)
                applied["role"] = role.value
            if enabled is not None:
                user = ctx.users.set_enabled(target.username, enabled)
                applied["enabled"] = enabled
        except Exception:
            if applied:  # a partial failure still leaves a record of what did change
                audit(p, "user.update", target.username, applied)
            raise
        audit(p, "user.update", target.username, applied)
        return ok(user.to_public())

    @r.route("DELETE", "/api/users/{username}")
    def users_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.USER_MANAGE)
        target = ctx.users.get(req.path_params["username"])
        # Radars and working marks name a user by subject (the token's sub); for a password user that is
        # the username, for a federated one it is not.
        owners = list(dict.fromkeys([target.subject or target.username, target.username]))
        if target.username in (p.username, p.subject):
            raise ValidationError("you cannot delete your own account; ask another administrator")
        # Edit rights are keyed by email: a later account with the same address must not inherit them.
        # They are removed first, so a failure leaves the account in place to retry the delete, never a
        # deleted account that still holds edit rights. The audit row records whatever did happen.
        email = target.email.lower()
        removed_from: list[str] = []
        owned: list[str] = []
        try:
            for radar in ctx.radars.list_all():
                if email and email in radar.editors and ctx.radars.remove_editor(p, radar.slug, email):
                    removed_from.append(radar.slug)
                if radar.owner in owners:
                    owned.append(radar.slug)  # orphaned: an administrator reassigns them
            ctx.users.delete(target.username)
        except Exception:
            if removed_from:
                audit(
                    p,
                    "user.delete",
                    target.username,
                    {"editor_of": removed_from, "owner_of": owned, "account_deleted": False},
                )
            raise
        # The account is gone: its row is written now, and the marks it left are removed best effort. A
        # failure there used to propagate, and the deleted account had no audit row at all (B3-039).
        marks: int | None
        try:
            marks = sum(ctx.insights.working_marks_of(who) for who in owners)
        except Exception:
            log.exception(json.dumps({"event": "working_marks.remove_failed", "user": target.username}))
            marks = None
        audit(
            p,
            "user.delete",
            target.username,
            {"editor_of": removed_from, "owner_of": owned, "working_marks_removed": marks},
        )
        return ok({"editor_access_removed": removed_from, "owned_radars": owned, "working_marks_removed": marks})

    # ---- products -------------------------------------------------------------------
    def _profile(b: Body) -> ProductProfile:
        return ProductProfile(
            id="",
            name=b.text("name", max_len=PRODUCT_NAME_MAX),
            summary=b.text("summary", required=False, max_len=PRODUCT_TEXT_MAX),
            # The model's own limits, so the route and the model cannot disagree (B3-033).
            capabilities=tuple(b.texts("capabilities", max_items=MAX_CAPABILITIES, max_len=MAX_LIST_ITEM)),
            segments=tuple(b.texts("segments", max_items=MAX_CAPABILITIES, max_len=MAX_LIST_ITEM)),
            competitors=tuple(b.texts("competitors", max_items=MAX_CAPABILITIES, max_len=MAX_LIST_ITEM)),
            doc_urls=tuple(b.texts("doc_urls", max_items=MAX_DOC_URLS, max_len=2048)),
            notes=b.text("notes", required=False, max_len=PRODUCT_TEXT_MAX),
        )

    def product_view(p: Principal, prof: ProductProfile) -> dict[str, Any]:
        """Full profiles (documentation addresses, notes, crawl report) are for the people who manage
        products; everyone else gets ``to_viewer`` (B3-017)."""
        return prof.to_public() if Policy.allows(p, Action.PRODUCT_MANAGE) else prof.to_viewer()

    @r.route("GET", "/api/products")
    def products_list(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        return ok({"items": [product_view(p, x) for x in ctx.products.list_all()]})

    @r.route("POST", "/api/products")
    def products_create(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PRODUCT_MANAGE)
        prof = ctx.products.create(_profile(Body(req.json())), p.actor)
        audit(p, "product.create", prof.id)
        return ok(prof.to_public(), 201)

    @r.route("GET", "/api/products/{pid}")
    def products_get(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        # A read: the 5-minute sweep refreshes ingestion status, so a viewer polling the page makes no
        # control-plane calls and no writes.
        return ok(product_view(p, ctx.products.get(req.path_params["pid"])))

    @r.route("PATCH", "/api/products/{pid}")
    def products_update(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PRODUCT_MANAGE)
        b = Body(req.json())
        version = b.integer("version")
        changes = {k: v for k, v in b.raw.items() if k != "version"}
        before = ctx.products.get(req.path_params["pid"])
        prof = ctx.products.update(req.path_params["pid"], changes, version)
        details: dict[str, Any] = {"fields": sorted(changes)}
        if before.doc_urls != prof.doc_urls:
            # They are crawled and can carry an access token in the query or the path, so only each
            # one's origin and a hash are recorded (SR2-08).
            details["doc_urls"] = [UrlGuard.audit_form(u) for u in prof.doc_urls]
        if before.name != prof.name:
            details["name"] = {"before": before.name[:120], "after": prof.name[:120]}
        audit(p, "product.update", prof.id, details)
        return ok(prof.to_public())

    @r.route("DELETE", "/api/products/{pid}")
    def products_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PRODUCT_MANAGE)
        users = sorted(m.slug for m in ctx.modules.list_all() if m.product_id == req.path_params["pid"])
        if users:  # their runs would fail mid-way on every product tool call
            raise ConflictError("modules still use this product; reassign them first", {"modules": users})
        gone = ctx.products.get(req.path_params["pid"])
        ctx.products.delete(req.path_params["pid"])
        # Documentation addresses can carry a token: archived in their audit form, like the trail (B3-045).
        archived = {**gone.to_public(), "doc_urls": [UrlGuard.audit_form(u) for u in gone.doc_urls]}
        key = _archive(ctx, f"deleted/products/{gone.id}", archived)
        audit(p, "product.delete", req.path_params["pid"], {"name": gone.name[:200], "archive": key})
        return ok()

    @r.route("POST", "/api/products/{pid}/sync")
    def products_sync(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PRODUCT_MANAGE)
        ctx.products.resync(req.path_params["pid"])
        audit(p, "product.sync", req.path_params["pid"])
        return ok()

    @r.route("POST", "/api/products/{pid}/ask")
    def products_ask(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PRODUCT_READ)
        ctx.module_service.ensure_genai()
        limiter.hit(p.subject, "bedrock", admin=p.is_admin, role=p.role.value)
        if ctx.knowledge is None:
            raise ValidationError("product knowledge is not configured")
        prof = ctx.products.get(req.path_params["pid"])
        b = Body(req.json())
        b.only("query", "limit")  # a misspelt key used to be ignored (CTR H-016)
        passages = ctx.knowledge.retrieve(
            b.text("query", max_len=1000),
            prof,
            b.integer("limit", required=False, default=6, low=1, high=20),
            defang=False,  # people read their own documents; the client escapes them
        )
        # Private material leaving the deployment as an answer: who asked what is worth a row.
        audit(p, "product.ask", prof.id, {"passages": len(passages), "query": b.raw.get("query", "")[:200]})
        return ok({"items": [x.to_public() for x in passages]})

    @r.route("GET", "/api/products/{pid}/documents")
    def docs_list(req: Request) -> Response:
        Policy.require(principal_of(req), Action.PRODUCT_READ)
        ctx.products.get(req.path_params["pid"])  # a typo is a 404, not an empty list
        return ok({"items": [d.to_public() for d in ctx.products.list_documents(req.path_params["pid"])]})

    @r.route("POST", "/api/products/{pid}/documents")
    def docs_presign(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PRODUCT_MANAGE)
        b = Body(req.json())
        b.only("filename", "size")  # a misspelt key used to be ignored (CTR H-016)
        res = ctx.products.presign_upload(
            req.path_params["pid"], b.text("filename", max_len=255), b.integer("size"), p.actor
        )
        audit(p, "document.upload_requested", res["document"]["id"], {"filename": res["document"]["filename"]})
        return ok(res, 201)

    @r.route("POST", "/api/products/{pid}/documents/{did}/complete")
    def docs_complete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PRODUCT_MANAGE)
        doc = ctx.products.complete_upload(req.path_params["pid"], req.path_params["did"])
        audit(p, "document.uploaded", doc.id)
        return ok(doc.to_public())

    @r.route("DELETE", "/api/products/{pid}/documents/{did}")
    def docs_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PRODUCT_MANAGE)
        wanted = req.path_params["did"]
        gone = next((d for d in ctx.products.list_documents(req.path_params["pid"]) if d.id == wanted), None)
        ctx.products.delete_document(req.path_params["pid"], req.path_params["did"])
        audit(
            p,
            "document.delete",
            req.path_params["did"],
            {"filename": gone.filename[:200] if gone else "", "product": req.path_params["pid"]},
        )
        return ok()

    # ---- modules --------------------------------------------------------------------
    @r.route("GET", "/api/models")
    def models(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        return ok({"items": ModelCatalog.public()})

    def module_view(m: ModuleDefinition, p: Principal) -> dict[str, Any]:
        """A module as the caller may see it: MCP server addresses (which can embed a credential) are
        for the people who manage modules."""
        body = m.to_public()
        if not Policy.allows(p, Action.MODULE_MANAGE):
            body["mcp_servers"] = [{"name": s["name"]} for s in body["mcp_servers"]]
            body["instructions"] = ""  # prompts can name customers or strategy
            body["system_prompt"] = ""
            # Who created it (an admin's Cognito subject), which product it reads and whether it reads
            # private documents are configuration, not something a viewer needs.
            body["created_by"] = ""
            body["product_id"] = None
            body["extra"] = {}
        return body

    @r.route("GET", "/api/modules")
    def modules_list(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        out = []
        for m in ctx.modules.list_all():
            body = module_view(m, p)
            snap = ctx.modules.budget(m.slug, m.daily_token_budget)
            body["budget"] = {"consumed": snap.consumed, "limit": snap.limit, "state": snap.state.value}
            body["last_dispatched_at"] = ctx.modules.progress(m.slug).last_dispatched_at
            out.append(body)
        return ok({"items": out})

    @r.route("POST", "/api/modules")
    def modules_create(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        definition = _module_from_body(Body(req.json()))
        if len(ctx.modules.list_all()) >= MAX_MODULES:
            # Every /api/modules, /api/nav and /api/health call, and every agent graph read, scales
            # with the module count.
            raise ValidationError(f"at most {MAX_MODULES} modules; delete one first")
        if definition.product_id:
            ctx.products.get(definition.product_id)  # 404 for a product that does not exist
        if ctx.modules.list_runs(definition.slug, 1)[0]:
            # A deleted module's runs, budget and outputs are keyed by its slug: a new module under the same
            # slug would inherit them (and pass the ownership checks on its outputs).
            raise ConflictError(f"slug {definition.slug!r} was used by a deleted module; choose another")
        m = ctx.modules.create(definition, p.actor)
        # What it may do, not only its slug: a custom module created with web_fetch, an MCP server and
        # enabled left no trace of either (B3-041). The seed audits a built-in the same way.
        audit(
            p,
            "module.create",
            m.slug,
            {
                "grants": sorted(g.value for g in m.grants),
                "mcp_servers": [x.name for x in m.mcp_servers],
                "product_id": m.product_id,
                "enabled": m.enabled,
                "interval_minutes": m.interval_minutes,
                "daily_token_budget": m.daily_token_budget,
                "instructions_sha256": _digest(m.instructions),
            },
        )
        return ok(m.to_public(), 201)

    @r.route("GET", "/api/modules/{slug}")
    def modules_get(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        m = ctx.modules.get(req.path_params["slug"])
        progress = ctx.modules.progress(m.slug)
        snap = ctx.modules.budget(m.slug, m.daily_token_budget)
        return ok(
            {
                **module_view(m, p),
                "budget": {"consumed": snap.consumed, "limit": snap.limit, "state": snap.state.value},
                "progress": {
                    "last_processed_at": progress.last_processed_at,
                    "remaining_history": list(progress.remaining_history),
                    "last_dispatched_at": progress.last_dispatched_at,
                },
            }
        )

    @r.route("PATCH", "/api/modules/{slug}")
    def modules_update(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        b = Body(req.json())
        version = b.integer("version")
        unknown = set(b.raw) - set(_MODULE_EDITABLE) - {"version"}
        if unknown:
            raise ValidationError(f"fields cannot be changed: {', '.join(sorted(unknown))}")
        changes = _module_changes(b)
        if changes.get("product_id"):
            ctx.products.get(changes["product_id"])
        before = ctx.modules.get(req.path_params["slug"])
        missing = [s for s in changes.get("skills", ()) if s not in {k.name for k in ctx.skills.list_all()}]
        if missing:
            raise ValidationError(f"no skill named {missing[0]!r} in the library")
        if "system_prompt" in changes and same_text(changes["system_prompt"], read_template(before.template.value)):
            # The packaged template, re-saved: stored as "no override", so a later template fix reaches it
            # (CTR refuses to pin a copy of the default for the same reason).
            changes["system_prompt"] = ""
        m = ctx.modules.update(req.path_params["slug"], changes, version)
        if any(getattr(before, f) != getattr(m, f) for f in ctx.modules.PROMPT_FIELDS):
            if not ctx.modules.prompt_history(m.slug, limit=1):
                ctx.modules.record_prompt(before, before.created_by or "system")  # what it was before the first edit
            ctx.modules.record_prompt(m, p.email or p.actor)
        audit(p, "module.update", m.slug, {"fields": sorted(set(b.raw) - {"version"}), **_module_delta(before, m)})
        return ok(m.to_public())

    @r.route("DELETE", "/api/modules/{slug}")
    def modules_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        gone = ctx.modules.get(req.path_params["slug"])
        ctx.modules.delete(req.path_params["slug"])
        # The definition is archived where a radar delete archives its own, so the trail can say what
        # was destroyed (grants, instructions, MCP server names) rather than only which slug.
        # MCP server addresses can embed a credential: archived in their audit form (B3-045).
        archived = {
            **gone.to_public(),
            "mcp_servers": [{"name": m.name, "url": UrlGuard.audit_form(m.url)} for m in gone.mcp_servers],
        }
        key = _archive(ctx, f"deleted/modules/{gone.slug}", archived)
        audit(
            p,
            "module.delete",
            req.path_params["slug"],
            {"grants": sorted(g.value for g in gone.grants), "insight_kind": gone.insight_kind, "archive": key},
        )
        return ok()

    @r.route("POST", "/api/modules/{slug}/run")
    def modules_run(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MODULE_RUN)
        b = Body(req.json())
        b.only("target_id")  # a misspelt key used to be ignored (CTR H-016)
        target_id = b.opt_text("target_id", 200)
        if target_id is not None:  # a node id the runtime can act on, never free text for the model
            try:
                node_type_of(target_id)
            except ValidationError as err:
                raise ValidationError("target_id must be a node id") from err
            target = ctx.graph.get(target_id)
            module = ctx.modules.get(req.path_params["slug"])
            # The template's own target first: the draft built-in declares no input_types, so a draft run
            # aimed at a signal still started and held 400,000 tokens (B3-046).
            wanted = TEMPLATE_TARGETS.get(module.template, module.input_types)
            if wanted and target.type not in wanted:
                # A draft run aimed at a signal used to start, hold budget and fail at draft_save.
                raise ValidationError(
                    f"this module processes {', '.join(sorted(t.value for t in wanted))} nodes",
                    {"target_type": target.type.value},
                )
        run = ctx.module_service.start(req.path_params["slug"], "manual", p.actor, target_id)
        audit(p, "module.run", req.path_params["slug"], {"run_id": run.run_id, "target_id": target_id})
        return ok(run.to_public(), 202)

    @r.route("POST", "/api/modules/{slug}/runs/{run_id}/cancel")
    def modules_run_cancel(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        Body(req.json()).only()
        slug, run_id = req.path_params["slug"], req.path_params["run_id"]
        run = ctx.module_service.cancel(slug, run_id, p.email or p.actor)
        audit(p, "module.run_cancel", slug, {"run_id": run_id, "status": run.status.value})
        return ok(run.to_public())

    # ---- skills (CTR's skill library) -------------------------------------------------------
    @r.route("GET", "/api/skills")
    def skills_list(req: Request) -> Response:
        Policy.require(principal_of(req), Action.MODULE_MANAGE)
        used: dict[str, list[str]] = {}
        for m in ctx.modules.list_all():
            for name in m.skills:
                used.setdefault(name, []).append(m.slug)
        return ok({"items": [{**s.to_summary(), "modules": used.get(s.name, [])} for s in ctx.skills.list_all()]})

    @r.route("GET", "/api/skills/{name}")
    def skills_get(req: Request) -> Response:
        Policy.require(principal_of(req), Action.MODULE_MANAGE)
        return ok(ctx.skills.get(req.path_params["name"]).to_public())

    @r.route("PUT", "/api/skills/{name}")
    def skills_put(req: Request) -> Response:
        """Create a skill (``version`` 0 or absent) or replace one (the version read)."""
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        b = Body(req.json())
        b.only("content", "version")
        name = req.path_params["name"]
        content = b.text("content", max_len=MAX_SKILL_BYTES)
        expected = b.integer("version", required=False, default=0)
        skill = ctx.skills.put(name, content, p.actor, expected)
        audit(p, "skill.save", name, {"version": skill.version, "sha256": skill.sha256, "size": skill.size})
        return ok(skill.to_public(), 201 if skill.version == 1 else 200)

    @r.route("DELETE", "/api/skills/{name}")
    def skills_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        name = req.path_params["name"]
        users = [m.slug for m in ctx.modules.list_all() if name in m.skills]
        if users:
            raise ConflictError(
                f"the skill is attached to {', '.join(users)}; detach it there first", {"modules": users}
            )
        gone = ctx.skills.delete(name)
        audit(p, "skill.delete", name, {"sha256": gone.sha256})
        return ok()

    @r.route("GET", "/api/modules/{slug}/prompt")
    def module_prompt(req: Request) -> Response:
        """The module's prompt as a run would get it now: the template it starts from, its override, the
        shared rules no edit removes, and the whole system prompt (CTR's system-prompt panel)."""
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        m = ctx.modules.get(req.path_params["slug"])
        product = AgentTools.product_block(ctx, m, m.product_id)
        return ok({"slug": m.slug, **SystemPrompt.preview(run_module(ctx, m), product)})

    @r.route("GET", "/api/modules/{slug}/prompt/history")
    def module_prompt_history(req: Request) -> Response:
        """The module's earlier prompts (instructions, template, mode, skills), newest first. Restoring one
        is a PATCH with its fields, so it is checked and audited like any edit."""
        Policy.require(principal_of(req), Action.MODULE_MANAGE)
        ctx.modules.get(req.path_params["slug"])
        items = ctx.modules.prompt_history(req.path_params["slug"], req.int_query("limit", 50, 1, 50))
        return ok({"items": [{k: v for k, v in i.items() if k != "sk"} for i in items]})

    @r.route("PUT", "/api/modules/{slug}/progress")
    def module_progress(req: Request) -> Response:
        """Set (or clear) a module's cursor: progress only moves forward for runs, so a module that skipped
        items could not be rewound without editing DynamoDB (B3-061)."""
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        b = Body(req.json())
        b.only("last_processed_at")
        ctx.modules.get(req.path_params["slug"])
        raw = b.opt_text("last_processed_at", 240)
        value = None
        if raw is not None:
            try:
                value = parse_iso(raw)
            except (ValueError, OverflowError) as err:  # OverflowError: year 1 with an offset
                raise ValidationError("last_processed_at must be an RFC 3339 timestamp or null") from err
        before = ctx.modules.progress(req.path_params["slug"]).last_processed_at
        progress = ctx.module_service.reset_progress(req.path_params["slug"], value)
        audit(
            p, "module.progress_reset", req.path_params["slug"], {"before": before, "after": progress.last_processed_at}
        )
        return ok({"last_processed_at": progress.last_processed_at})

    @r.route("GET", "/api/modules/{slug}/runs")
    def module_runs(req: Request) -> Response:
        Policy.require(principal_of(req), Action.HEALTH_READ)
        ctx.modules.get(req.path_params["slug"])  # a typo is a 404, not an empty list
        runs, cursor = ctx.modules.list_runs(
            req.path_params["slug"], req.int_query("limit", 25, 1, 100), req.query.get("cursor")
        )
        return ok({"items": [x.to_public() for x in runs], "cursor": cursor})

    @r.route("GET", "/api/runs")
    def runs_all(req: Request) -> Response:
        Policy.require(principal_of(req), Action.HEALTH_READ)
        runs, cursor = ctx.modules.list_runs(None, req.int_query("limit", 25, 1, 100), req.query.get("cursor"))
        return ok({"items": [x.to_public() for x in runs], "cursor": cursor})

    @r.route("GET", "/api/modules/{slug}/sources")
    def sources_list(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        manager = Policy.allows(p, Action.MODULE_MANAGE)
        ctx.modules.get(req.path_params["slug"])  # a typo is a 404, not an empty list
        items = [s.to_public() for s in ctx.modules.list_sources(req.path_params["slug"])]
        if not manager:  # an address can carry an API key; instructions and cursors are configuration
            items = [{k: s[k] for k in SOURCE_SUMMARY} for s in items]
        else:  # what the source watcher last saw: when, the outcome, and how many new items wait
            watch = ctx.watch.many(req.path_params["slug"], [s["id"] for s in items])
            items = [{**s, "watch": watch[s["id"]].to_public() if s["id"] in watch else None} for s in items]
        return ok({"items": items})

    @r.route("POST", "/api/modules/{slug}/sources/check")
    def sources_check(req: Request) -> Response:
        """Check every source of the module for new items now, instead of at the next scheduled check."""
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        ctx.modules.get(req.path_params["slug"])
        if not ctx.request_watch():
            raise ValidationError("this deployment has no source watcher")
        audit(p, "sources.check", req.path_params["slug"])
        return ok({"requested": True}, 202)

    @r.route("POST", "/api/modules/{slug}/sources")
    def sources_add(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        b = Body(req.json())
        b.only(
            "type", "name", "address", "instruction", "enabled", "cursor"
        )  # a misspelt key used to be ignored (CTR H-016)
        try:
            stype = SourceType(b.text("type"))
        except ValueError as err:
            raise ValidationError("type must be web_page, dynamic_web_page, rss or public_api") from err
        s = ctx.modules.add_source(
            req.path_params["slug"],
            b.text("name", max_len=SOURCE_NAME_MAX),
            stype,
            b.text("address", max_len=2048),
            b.text("instruction", required=False, max_len=SOURCE_INSTRUCTION_MAX),
            b.text("cursor", required=False, max_len=SOURCE_CURSOR_MAX),
            b.flag("enabled", required=False, default=True),
        )
        audit(p, "source.add", f"{s.module}/{s.id}")
        return ok(s.to_public(), 201)

    @r.route("PATCH", "/api/modules/{slug}/sources/{sid}")
    def sources_update(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        b = Body(req.json())
        if "type" in b.raw and (
            not isinstance(b.raw["type"], str) or b.raw["type"] not in SourceType._value2member_map_
        ):
            # An unhashable value (a list, an object) made the membership test itself a 500 (B3-036).
            raise ValidationError("type must be web_page, dynamic_web_page, rss or public_api")
        before = ctx.modules.get_source(req.path_params["slug"], req.path_params["sid"])
        s = ctx.modules.update_source(req.path_params["slug"], req.path_params["sid"], dict(b.raw))
        audit(p, "source.update", f"{s.module}/{s.id}", {"fields": sorted(b.raw), **_source_delta(before, s)})
        return ok(s.to_public())

    @r.route("DELETE", "/api/modules/{slug}/sources/{sid}")
    def sources_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MODULE_MANAGE)
        gone = ctx.modules.get_source(req.path_params["slug"], req.path_params["sid"])
        ctx.modules.delete_source(req.path_params["slug"], req.path_params["sid"])
        audit(
            p,
            "source.delete",
            f"{req.path_params['slug']}/{req.path_params['sid']}",
            {"name": gone.name[:200], "type": gone.type.value, "address": UrlGuard.audit_form(gone.address)},
        )
        return ok()

    @r.route("GET", "/api/andon")
    def andon_get(req: Request) -> Response:
        p = principal_of(req)
        state = ctx.modules.andon()
        body: dict[str, Any] = {"genai_enabled": state.genai_enabled}
        if Policy.allows(p, Action.ANDON_MANAGE):
            body.update({"reason": state.reason, "updated_by": state.updated_by, "updated_at": state.updated_at})
        return ok(body)

    @r.route("PUT", "/api/andon")
    def andon_put(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.ANDON_MANAGE)
        b = Body(req.json())
        b.only("genai_enabled", "reason")
        before = ctx.modules.andon().genai_enabled
        reason = b.opt_text("reason", 500)
        state = ctx.modules.set_andon(b.flag("genai_enabled"), reason, p.actor)
        audit(
            p,
            "andon.set",
            "global",
            {"before": before, "genai_enabled": state.genai_enabled, "reason": reason or ""},
        )
        return ok(
            {
                "genai_enabled": state.genai_enabled,
                "reason": state.reason,
                "updated_by": state.updated_by,
                "updated_at": state.updated_at,
            }
        )

    # ---- graph ----------------------------------------------------------------------
    @r.route("GET", "/api/graph/schema")
    def graph_schema(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        return ok(Registry.describe())

    @r.route("POST", "/api/graph/search")
    def graph_search(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        limiter.hit(p.subject, "bedrock", admin=p.is_admin, role=p.role.value)  # every search embeds the query
        b = Body(req.json())
        b.only("query", "types", "insight_kind", "limit")  # a misspelt key used to be ignored (CTR H-016)
        types = [Registry.node_type(t) for t in b.texts("types", max_items=20, max_len=40)]
        result = ctx.search.search_result(
            b.text("query", max_len=500),
            types=types or None,
            insight_kind=b.opt_text("insight_kind", 60),
            limit=b.integer("limit", required=False, default=20, low=1, high=50),
            hidden=hidden_for(p),  # dropped before ranking: ranks and the note below see only what p may read
        )
        body: dict[str, Any] = {"items": [h.to_public() for h in result.hits]}
        if result.degraded:
            body["degraded"] = True  # keyword matches only: the page says so
        if body["items"] and not result.worded:
            # Nearest by meaning only: a nonsense or off-topic query read these as matches (MCP test). Keyword
            # matches alone (degraded) can share one word of many: there was no meaning search (round 34/35).
            body["note"] = (
                "No result contains most of the query's words."
                if result.degraded
                else "No result contains most of the query's words; these are only near it in meaning."
            )
        return ok(body)

    @r.route("POST", "/api/graph/ask")
    def graph_ask(req: Request) -> Response:
        """A question, with the records and sources that bear on it and how well they back an answer
        (CTR kg_ask, HC-098/100/101). Retrieval only: the caller's model writes the answer."""
        p = principal_of(req)
        Policy.require(p, Action.READ)
        limiter.hit(p.subject, "bedrock", admin=p.is_admin, role=p.role.value)  # it embeds the question
        b = Body(req.json())
        b.only("question", "types")
        types = [Registry.node_type(t) for t in b.texts("types", max_items=20, max_len=40)]
        answer = GraphAsk(ctx.graph, ctx.search).ask(
            b.text("question", max_len=500), types=types or None, hidden=hidden_for(p)
        )
        return ok(answer.to_public())

    @r.route("GET", "/api/graph/nodes/{type}/{uid}")
    def graph_node(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        node = ctx.graph.get(_node_id(req.path_params))
        if hidden_for(p)(node):
            raise NotFoundError(f"node {node.id} not found")
        return ok(node.to_public())

    @r.route("GET", "/api/graph/nodes/{type}/{uid}/brief")
    def graph_node_brief(req: Request) -> Response:
        """One record researched: its newest evidence, linked trends and gaps, the radars placing it and
        its sources (CTR research_blip). Retrieval only, like ask."""
        p = principal_of(req)
        Policy.require(p, Action.READ)
        # It reads up to 500 links and the current version of every radar: limited like explore.
        limiter.hit(p.subject, "graph", admin=p.is_admin, role=p.role.value)
        hidden = hidden_for(p)
        node = ctx.graph.get(_node_id(req.path_params))
        if hidden(node):
            raise NotFoundError(f"node {node.id} not found")
        placements = ctx.radars.find_entries(node_id=node.id, limit=20)
        return ok(GraphAsk(ctx.graph, ctx.search).brief(node, placements=placements, hidden=hidden))

    @r.route("GET", "/api/graph/hygiene")
    def graph_hygiene(req: Request) -> Response:
        """Entities with no links, and groups that look like one entity (CTR HC-120/124/140)."""
        Policy.require(principal_of(req), Action.GRAPH_EDIT)
        return ok(ctx.graph.entity_hygiene())

    @r.route("POST", "/api/graph/nodes/{type}/{uid}/merge")
    def graph_merge(req: Request) -> Response:
        """Merge this entity into another (CTR HC-105): a dry run unless ``apply`` is true."""
        p = principal_of(req)
        Policy.require(p, Action.GRAPH_EDIT)
        b = Body(req.json())
        b.only("into", "apply")
        node_id = _node_id(req.path_params)
        into = b.text("into", max_len=200)
        node_type_of(into)
        apply = b.flag("apply", required=False, default=False)
        node = ctx.graph.get(node_id)
        if node.degree > MAX_DELETE_EDGES:
            # Moving every edge must finish within one request, like a delete.
            raise ConflictError(
                f"this entity has more than {MAX_DELETE_EDGES} links; unlink some or merge the other way round",
                {"limit": MAX_DELETE_EDGES},
            )
        # A duplicate hidden from viewers (a legacy unstamped node from a private writer too) makes the kept entity
        # private (security review r66).
        hidden = private_hider(ctx.modules.private_writer_slugs())(node)
        report = ctx.graph.merge_entity(node_id, into, p.email or p.actor, apply=apply, private=hidden)
        if apply:
            audit(
                p,
                "graph.merge",
                node_id,
                {
                    "into": into,
                    "moved": report["moved"],
                    "dropped": len(report["dropped"]),
                    # Edges taken on to a half-merged neighbour's kept entity (code review r72).
                    "redirected": len(report["redirected"]),
                },
            )
        return ok(report)

    @r.route("DELETE", "/api/graph/nodes/{type}/{uid}")
    def graph_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.GRAPH_EDIT)
        node_id = _node_id(req.path_params)
        node = ctx.graph.get(node_id)
        removal = NodeRemoval.of(ctx)
        if node.degree > MAX_DELETE_EDGES:
            # Too many links to take apart within one request: queued, and the sweep prunes it a batch at a
            # time. A 409 asking to "unlink some first" left an entity any evidence-writing module had
            # linked 501 times undeletable (B3-069).
            removal.queue(node, p.actor)
            audit(p, "graph.delete_queued", node_id, {"type": node.type.value, "edges": node.degree})
            return ok({"queued": True, "edges": node.degree}, 202)
        done = removal.remove(node)
        details = {
            "edges_removed": done.edges_removed,
            "type": node.type.value,
            "title": node.title[:200],
            "created_by": node.created_by,
            "module": node.module,
            "drafts_removed": done.drafts_removed,
        }
        audit(p, "graph.delete", node_id, details)
        return ok({"edges_removed": done.edges_removed})

    @r.route("PATCH", "/api/graph/nodes/{type}/{uid}")
    def graph_correct(req: Request) -> Response:
        """A person corrects a node's text: agent-written text found wrong or injected could only be
        deleted, losing its links and history (B3-072)."""
        p = principal_of(req)
        Policy.require(p, Action.GRAPH_EDIT)
        b = Body(req.json())
        b.only("title", "summary", "description", "expected_version")
        node_id = _node_id(req.path_params)
        changes: dict[str, Any] = {}
        for field, limit in (("title", 200), ("summary", 2000), ("description", 40_000)):
            if b.has(field):
                changes[field] = b.text(field, required=field == "title", max_len=limit)
        if not changes:
            raise ValidationError("nothing to change")
        before = ctx.graph.get(node_id)
        node = ctx.graph.update(node_id, changes, b.integer("expected_version"), p.actor)
        audit(
            p,
            "graph.correct",
            node_id,
            {
                "fields": sorted(changes),
                "created_by": before.created_by,
                "module": before.module,
                **{
                    f"{k}_sha256": {"before": _digest(str(getattr(before, k))), "after": _digest(str(changes[k]))}
                    for k in changes
                },
            },
        )
        return ok(node.to_public())

    @r.route("DELETE", "/api/graph/edges/{ftype}/{fuid}/{edge}/{ttype}/{tuid}")
    def graph_unlink(req: Request) -> Response:
        """Remove one edge: an injected relation or a wrong ABOUT link could only be undone by deleting one
        of its nodes (B3-068)."""
        p = principal_of(req)
        Policy.require(p, Action.GRAPH_EDIT)
        params = req.path_params
        source = _node_id({"type": params["ftype"], "uid": params["fuid"]})
        target = _node_id({"type": params["ttype"], "uid": params["tuid"]})
        edge = Registry.edge_type(params["edge"])
        if not ctx.graph.unlink(source, edge, target):
            raise NotFoundError("edge not found")
        audit(p, "graph.unlink", source, {"edge": edge.value, "to": target})
        return ok()

    @r.route("GET", "/api/graph/nodes/{type}/{uid}/neighbors")
    def graph_neighbors(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        limiter.hit(
            p.subject, "graph", admin=p.is_admin, role=p.role.value
        )  # each hop costs two edge queries per frontier node
        hidden = hidden_for(p)
        root = ctx.graph.get(_node_id(req.path_params))
        if hidden(root):
            raise NotFoundError(f"node {root.id} not found")
        hood = ctx.graph.neighborhood(
            root.id,
            # Each hop costs two edge queries per frontier node: bounded so a viewer cannot fan one request out
            # into hundreds of queries.
            depth=req.int_query("depth", 1, 1, 2),
            max_nodes=req.int_query("max_nodes", 150, 1, 150),
            blocked=hidden,
            # Paging through a node's direct neighbours (the viewer's "More neighbours").
            offset=req.int_query("offset", 0, 0, 100_000) if "offset" in req.query else None,
        )
        nodes = [n for n in hood.nodes if not hidden(n)]
        visible = {n.id for n in nodes}
        body: dict[str, Any] = {
            "nodes": [_graph_brief(n) for n in nodes],
            "edges": [e.to_public() for e in hood.edges if e.source in visible and e.target in visible],
            "truncated": hood.truncated,
        }
        if hood.total is not None:
            body.update(total=hood.total, offset=hood.offset)
        return ok(body)

    @r.route("POST", "/api/admin/sample-data")
    def sample_data(req: Request) -> Response:
        """Load the sample graph and radar (idempotent), so a new deployment has something to show."""
        p = principal_of(req)
        Policy.require(p, Action.SAMPLE_LOAD)
        # Read now and strictly: a throttled or unreadable profile must refuse, not fall back to "on".
        if not ctx.profile.get_strict().section_visible("sample_data"):
            # A deployment holding real data does not offer it: a mistaken click mixed fictional
            # companies into its knowledge graph.
            raise NotFoundError("sample data is not offered on this deployment")
        res = SampleData(ctx.graph, ctx.radars).load(p)
        audit(p, "admin.sample_data", RADAR_SLUG, {"nodes": res.nodes_created, "edges": res.edges_created})
        return ok(res.to_public())

    @r.route("GET", "/api/graph/explore")
    def graph_explore(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        # About 166 table queries per call, repeatable in a loop: throttled like the Bedrock routes.
        limiter.hit(p.subject, "graph", admin=p.is_admin, role=p.role.value)
        limit = req.int_query("limit", 150, 10, 150)
        wanted = req.query.get("types")
        types = (
            list(dict.fromkeys(Registry.node_type(t) for t in wanted.split(",")))  # each type once
            if wanted
            else [t for t in NodeType if t is not NodeType.INSIGHT]
        )
        nodes = []
        for t in types:
            if t is NodeType.INSIGHT:
                continue
            nodes.extend(ctx.graph.iterate(t, cap=EXPLORE_SCAN_CAP))
        for insight_kind in sorted(_listable_kinds(p)) if not wanted or "insight" in wanted else []:
            nodes.extend(ctx.graph.iterate(NodeType.INSIGHT, insight_kind=insight_kind, cap=EXPLORE_SCAN_CAP))
        hidden = hidden_for(p)
        top = sorted((n for n in nodes if not hidden(n)), key=lambda n: (-n.degree, n.id))[:limit]
        ids = {n.id for n in top}
        edges: list[Any] = []
        for n in top:
            edges.extend(e for e in ctx.graph.edges(n.id, direction="out", limit=100) if e.target in ids)
        return ok({"nodes": [_graph_brief(n) for n in top], "edges": [e.to_public() for e in edges]})

    # ---- insights -------------------------------------------------------------------
    def _list_query(req: Request, schema: AttributeSchema) -> tuple[AttrQuery | None, Sort | None]:
        """The list's ``sort``, ``order`` and ``filter.<field>`` parameters, checked against its schema."""
        sort_raw = req.query.get("sort")
        attr_sort = None
        if sort_raw and sort_raw.startswith("attr:"):
            attr_sort = schema.field(sort_raw[5:])
            if attr_sort is None or not attr_sort.sort:
                raise ValidationError(f"{sort_raw[5:][:40]!r} is not a sortable field of this list")
            sort_raw = None
        try:
            sort = Sort(sort_raw) if sort_raw else None
        except ValueError as err:
            raise ValidationError("sort must be newest, relevance, connected, title or attr:<field>") from err
        order = req.query.get("order", "asc")
        if order not in ("asc", "desc"):
            raise ValidationError("order must be asc or desc")
        filters: dict[str, frozenset[str]] = {}
        for qk, qv in req.query.items():
            if not qk.startswith("filter."):
                continue
            f = schema.field(qk[7:])
            if f is None or not f.filter:
                raise ValidationError(f"{qk[7:][:40]!r} is not a filterable field of this list")
            values = frozenset(v.strip() for v in qv.split(",") if v.strip())
            if not values or len(values) > 50:
                raise ValidationError(f"{qk} needs 1-50 comma-separated values")
            filters[f.key] = values
        return (AttrQuery(filters, attr_sort, order == "desc") if filters or attr_sort else None), sort

    @r.route("GET", "/api/insights/{kind}")
    def insights_list(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        kind = InsightKind.parse(req.path_params["kind"])
        # Access first: a filter error on a list the caller may not read would name its fields.
        if kind.node_type in PRIVATE_OUTPUT_TYPES:
            Policy.require(p, Action.PRODUCT_READ)  # gaps, content ideas and drafts (B3-018)
        mods = ctx.modules.list_all()  # once per request: the schema and the listable kinds both need it
        if kind.insight_kind and kind.insight_kind not in _listable_kinds(p, mods):
            raise NotFoundError(f"no module produces {req.path_params['kind']}")  # a typo, not an empty list
        schema = _list_schema(kind, mods)
        attrs, sort = _list_query(req, schema)
        q = req.query.get("q") or None
        if q:
            limiter.hit(p.subject, "bedrock", admin=p.is_admin, role=p.role.value)
        if sort is Sort.CONNECTED or (attrs is not None and not q):
            limiter.hit(
                p.subject, "graph", admin=p.is_admin, role=p.role.value
            )  # re-reads up to 2,000 nodes a page (B3-053); so do filters and field sorts
        return ok(
            ctx.insights.browse(
                kind,
                query=q,
                sort=sort,
                cursor=req.query.get("cursor"),
                principal=p,
                hidden=hidden_for(p),
                include_closed=req.query.get("include_closed") == "true",
                attrs=attrs,
            )
            | {"schema": schema.to_public()}
        )

    @r.route("GET", "/api/insights/{kind}/export")
    def insights_export(req: Request) -> Response:
        """The list, with the same filters and field sort, as CSV or a Markdown table to take elsewhere."""
        p = principal_of(req)
        Policy.require(p, Action.READ)
        kind = InsightKind.parse(req.path_params["kind"])
        if kind.node_type in PRIVATE_OUTPUT_TYPES:
            Policy.require(p, Action.PRODUCT_READ)
        mods = ctx.modules.list_all()  # once per request: the schema and the listable kinds both need it
        if kind.insight_kind and kind.insight_kind not in _listable_kinds(p, mods):
            raise NotFoundError(f"no module produces {req.path_params['kind']}")
        fmt = req.query.get("format", "csv")
        if fmt not in export.FORMATS:
            raise ValidationError("format must be csv or md")
        schema = _list_schema(kind, mods)
        attrs, sort = _list_query(req, schema)
        if sort is not None and sort is not Sort.NEWEST:
            raise ValidationError("an export is ordered newest first or by a field (sort=attr:<field>)")
        limiter.hit(p.subject, "graph", admin=p.is_admin, role=p.role.value)  # reads up to 2,000 nodes
        nodes, cut = ctx.insights.export_nodes(kind, attrs=attrs, hidden=hidden_for(p), limit=export.EXPORT_MAX)
        header, body = export.rows(nodes, schema, ctx.settings.allowed_origin)
        profile = ctx.profile.get(cached=True)
        key = list_key(kind.node_type, kind.insight_kind)
        listed = profile.list_profile(key) if key else None
        label = listed.label if listed and listed.label else req.path_params["kind"]
        stamp = ctx.clock.now().date().isoformat()
        note = f"{profile.app_name}, {stamp}." + (" Only the first records are included." if cut else "")
        name = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "list"
        if fmt == "csv":
            text, mime = export.to_csv(header, body), "text/csv; charset=utf-8"
        else:
            text, mime = export.to_markdown(label, header, body, note), "text/markdown; charset=utf-8"
        return Response(
            200,
            raw=text,
            content_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.{fmt}"'},
        )

    @r.route("GET", "/api/insights/{kind}/facets")
    def insights_facets(req: Request) -> Response:
        """Counts per filterable field value and the dates coming up: what an overview draws for a list."""
        p = principal_of(req)
        Policy.require(p, Action.READ)
        kind = InsightKind.parse(req.path_params["kind"])
        if kind.node_type in PRIVATE_OUTPUT_TYPES:
            Policy.require(p, Action.PRODUCT_READ)
        mods = ctx.modules.list_all()  # once per request: the schema and the listable kinds both need it
        if kind.insight_kind and kind.insight_kind not in _listable_kinds(p, mods):
            raise NotFoundError(f"no module produces {req.path_params['kind']}")
        days_raw = req.query.get("days", str(UPCOMING_DAYS))
        # ASCII digits only: str.isdigit() accepts superscript digits, which int() then refuses with an unhandled error.
        if not re.fullmatch(r"[0-9]{1,3}", days_raw) or not 1 <= int(days_raw) <= 366:
            raise ValidationError("days must be a whole number from 1 to 366")
        schema = _list_schema(kind, mods)
        # Every Overview load asks for these, once per typed list: the same answer serves everyone who sees the
        # same records for FACETS_CACHE_SECONDS, so only a miss reads the list and counts against the caller's
        # graph limit. Keyed on what decides the answer: the list, the window, whether the caller may read
        # outputs derived from private documents, and the fields.
        sees_private = Policy.allows(p, Action.PRODUCT_READ)
        key = (kind.node_type.value, kind.insight_kind, days_raw, sees_private, json.dumps(schema.to_public()))
        now = ctx.clock.now().timestamp()
        hit = facets_cache.get(key)
        if hit is None or now - hit[0] > FACETS_CACHE_SECONDS:
            limiter.hit(p.subject, "graph", admin=p.is_admin, role=p.role.value)  # reads up to 2,000 nodes
            hit = (now, ctx.insights.facets(kind, schema, hidden=hidden_for(p), days=int(days_raw)))
            facets_cache[key] = hit
            while len(facets_cache) > FACETS_CACHE_MAX:
                facets_cache.pop(next(iter(facets_cache)))
        return ok(hit[1] | {"schema": schema.to_public()})

    @r.route("GET", "/api/insights/nodes/{type}/{uid}")
    def insights_detail(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        return ok(ctx.insights.detail(_node_id(req.path_params), p, hidden_for(p)))

    @r.route("POST", "/api/nodes/{type}/{uid}/feedback")
    def feedback(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.GIVE_FEEDBACK)
        b = Body(req.json())
        b.only("vote", "comment")
        node_id = _node_id(req.path_params)
        res = ctx.insights.give_feedback(node_id, p, b.text("vote", max_len=10), b.opt_text("comment", 2000))
        audit(p, "feedback.give", node_id, {"vote": b.raw.get("vote")})
        return ok(res)

    @r.route("DELETE", "/api/nodes/{type}/{uid}/feedback")
    def feedback_withdraw(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.GIVE_FEEDBACK)
        node_id = _node_id(req.path_params)
        res = ctx.insights.withdraw_feedback(node_id, p)
        audit(p, "feedback.withdraw", node_id)
        return ok(res)

    @r.route("GET", "/api/feedback")
    def feedback_list(req: Request) -> Response:
        Policy.require(principal_of(req), Action.AUDIT_READ)
        node_type = Registry.node_type(req.query.get("type", "signal"))
        items, cursor = ctx.insights.feedback_page(
            node_type, req.int_query("limit", 100, 1, 500), req.query.get("cursor")
        )
        return ok({"items": items, "cursor": cursor})

    @r.route("PUT", "/api/nodes/{type}/{uid}/working")
    def working_on(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MARK_WORKING)
        node_id = _node_id(req.path_params)
        res = ctx.insights.set_working(node_id, p, True)
        audit(p, "working.set", node_id)
        return ok({"working_on": res})

    @r.route("DELETE", "/api/nodes/{type}/{uid}/working")
    def working_off(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.MARK_WORKING)
        node_id = _node_id(req.path_params)
        res = ctx.insights.set_working(node_id, p, False)
        audit(p, "working.unset", node_id)
        return ok({"working_on": res})

    @r.route("GET", "/api/drafts/{type}/{uid}")
    def draft_markdown(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        node = ctx.graph.get(_node_id(req.path_params))
        if node.type is not NodeType.CONTENT_DRAFT or hidden_for(p)(node):
            raise NotFoundError("draft not found")
        # The key comes from the node id, never from attributes: agents can write attributes, and a
        # planted key would let any viewer read any object in the bucket.
        key = f"drafts/{node.id.split('/', 1)[1]}.md"
        try:
            obj = ctx.s3.get_object(Bucket=ctx.settings.assets_bucket, Key=key)
        except ClientError as err:
            if err.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise NotFoundError("this draft has no text") from err  # e.g. its save failed half-way
            raise
        limiter.hit(p.subject, "graph", admin=p.is_admin, role=p.role.value)
        if _first_read_today(p, node.id):
            # The first read per person, draft and day: a row per read let one script bury the day's
            # real changes under draft.read rows (B3-064).
            audit(p, "draft.read", node.id, {"title": node.title[:200]})
        return Response(200, raw=obj["Body"].read().decode("utf-8"), content_type="text/markdown; charset=utf-8")

    @r.route("POST", "/api/nodes/{type}/{uid}/draft")
    def draft(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.DRAFT_CONTENT)
        node_id = _node_id(req.path_params)
        if node_type_of(node_id) is not NodeType.CONTENT_IDEA:
            raise ValidationError("drafts are generated from content ideas")
        ctx.graph.get(node_id)
        b = Body(req.json())
        b.only("brief")
        # The brief is data for an agent that reads product knowledge: short, and folded like any other
        # untrusted text before it reaches the model.
        # Folded and marker-stripped, but line by line: collapsing all whitespace turned a structured
        # brief (lists, headings) into one unreadable line.
        brief = Defang.neutralize_lines(b.text("brief", required=False, max_len=DRAFT_BRIEF_MAX), DRAFT_BRIEF_MAX)
        if not ctx.modules.get("draft").enabled and not Policy.allows(p, Action.MODULE_RUN):
            # Built-ins ship disabled so an unrelated deploy never arms the fleet: that includes drafts.
            raise ValidationError("content drafts are switched off; an administrator can enable the drafts module")
        day = None
        if not p.is_admin:
            day = drafts_quota.take(
                p.actor,
                "draft",
                DRAFTS_PER_USER_PER_DAY,
                f"you have requested {DRAFTS_PER_USER_PER_DAY} drafts today; try again tomorrow",
            )
        try:
            run = ctx.module_service.start("draft", "manual", p.actor, node_id, {"brief": brief} if brief else None)
        except Exception:
            if day is not None:  # nothing started: the request does not count
                try:
                    drafts_quota.give_back(p.actor, "draft", day)
                except Exception:
                    # Its own failure must not replace the real error (a 409 "already running" used to
                    # surface as a 500) -- and must not hide it either.
                    log.exception(json.dumps({"event": "quota.give_back_failed", "actor": p.actor}))
            raise
        audit(p, "content.draft", node_id, {"run_id": run.run_id})
        return ok(run.to_public(), 202)

    # ---- radars ---------------------------------------------------------------------
    def radar_view(p: Principal, radar: Any) -> dict[str, Any]:
        """Editor addresses are for the people who manage the radar's access."""
        body = cast(dict[str, Any], radar.to_public())
        if not ctx.radars.can_admin(p, radar):
            body["editors"] = []
        return body

    @r.route("GET", "/api/radar-templates")
    def radar_templates(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        return ok({"items": [{"key": k, "purpose": v[0], "schema": v[1].to_public()} for k, v in TEMPLATES.items()]})

    @r.route("GET", "/api/radars")
    def radars_list(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        return ok(
            {"items": [{**radar_view(p, x), "can_edit": ctx.radars.can_edit(p, x)} for x in ctx.radars.list_all()]}
        )

    @r.route("GET", "/api/me/favourites")
    def my_favourites(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        return ok({"radars": ctx.radars.favourites(p.subject)})

    @r.route("PUT", "/api/me/favourites/radars/{slug}")
    def favourite_add(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        ctx.radars.set_favourite(p.subject, req.path_params["slug"], True)
        return ok({"slug": req.path_params["slug"], "favourite": True})

    @r.route("DELETE", "/api/me/favourites/radars/{slug}")
    def favourite_remove(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        ctx.radars.set_favourite(p.subject, req.path_params["slug"], False)
        return ok({"slug": req.path_params["slug"], "favourite": False})

    @r.route("GET", "/api/me/watches")
    def my_watches(req: Request) -> Response:
        """The graph records the caller watches, for the digest (CTR subscriptions)."""
        p = principal_of(req)
        Policy.require(p, Action.READ)
        hidden = hidden_for(p)
        nodes = ctx.graph.get_many(ctx.digests.watches(p.subject))
        items = [
            {"id": n.id, "type": n.type.value, "title": n.title}
            for n in sorted(nodes.values(), key=lambda n: n.title.lower())
            if not hidden(n)
        ]
        return ok({"items": items})

    @r.route("PUT", "/api/me/watches/nodes/{type}/{uid}")
    def watch_add(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        node = ctx.graph.get(_node_id(req.path_params))
        if hidden_for(p)(node):
            raise NotFoundError(f"node {node.id} not found")
        if node.type not in ENTITIES:  # only entities have records about them to follow (code review r8)
            raise ValidationError("only an organization, person, product, topic or segment can be watched")
        return ok({"id": node.id, "watching": ctx.digests.set_watch(p.subject, node.id, True)})

    @r.route("DELETE", "/api/me/watches/nodes/{type}/{uid}")
    def watch_remove(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        node_id = _node_id(req.path_params)
        ctx.digests.set_watch(p.subject, node_id, False)
        return ok({"id": node_id, "watching": False})

    @r.route("GET", "/api/me/digest")
    def my_digest(req: Request) -> Response:
        """What is new since ``since`` or the caller's last read: new conclusions, and new records about
        each watched one (CTR subscription-digest). Reading it does not mark it read."""
        p = principal_of(req)
        Policy.require(p, Action.READ)
        limiter.hit(p.subject, "graph", admin=p.is_admin, role=p.role.value)  # up to 50 watches x their links
        since = req.query.get("since", "").strip()[:40] or None
        view = reader_view(p, ctx.modules.private_writer_slugs)
        custom = custom_lists(ctx.modules.list_all())
        return ok(ctx.digests.digest(p.subject, since=since, hidden=view.hidden, custom=custom, unseen=view.unseen))

    @r.route("POST", "/api/me/digest/read")
    def digest_read(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        b = Body(req.json())
        b.only("upto")
        return ok({"read_at": ctx.digests.mark_read(p.subject, b.opt_text("upto", 120))})  # a time or a record key

    # ---- digest delivery (Slack, email) -----------------------------------------------------
    def dm_key(p: Principal) -> str | None:
        """What the caller's Slack ID is found by: their own verified email, or the alias of their federated
        sign-in. None when there is none, and then the DM option is not offered."""
        cfg = ctx.settings.digest
        if cfg.dm_resolver is DmResolver.EMAIL:
            return p.email
        provider = ctx.settings.identity_provider
        name = p.username or ""
        if provider and name.lower().startswith(f"{provider.lower()}_"):
            alias = name[len(provider) + 1 :].lower()
            return alias if ALIAS_RE.fullmatch(alias) else None
        return None

    def slack_ready() -> bool:
        cfg = ctx.settings.digest
        return (cfg.slack_channel or cfg.slack_dm) and bool(ctx.delivery_outlets.slack_configured())

    def delivery_channels(ready: bool | None = None) -> list[DigestChannel]:
        cfg = ctx.settings.digest
        if not cfg.slack_channel or not (slack_ready() if ready is None else ready):
            return []
        if cfg.slack_mode is SlackMode.WEBHOOK:
            return [DigestChannel(WEBHOOK_CHANNEL, "The Slack channel this deployment posts to")]
        return cast(list[DigestChannel], ctx.deliveries.channels())

    def delivery_options(
        p: Principal, channels: list[DigestChannel] | None = None, ready: bool | None = None
    ) -> dict[str, Any]:
        """What the caller may deliver to here. Slack is offered only once its secret holds a real token or webhook
        URL: the stack creates it with a placeholder. ``channels`` is the list already read, if the caller has it."""
        cfg = ctx.settings.digest
        if ready is None:
            ready = slack_ready()
        if channels is None:
            channels = delivery_channels(ready)
        return {
            "slack_channel": bool(channels),
            "slack_dm": cfg.slack_dm and ready and dm_key(p) is not None,
            "email": cfg.email and bool(p.email),
            "channels": [c.to_public() for c in channels],
            "email_address": p.email,
        }

    def delivery_target(p: Principal, b: Body) -> Target:
        """Where a new delivery goes, from the caller's identity or the administrator's list: never an address or
        a Slack user the request names."""
        raw_kind = b.text("kind", max_len=20)
        try:
            kind = DeliveryKind(raw_kind)
        except ValueError as err:
            raise ValidationError('kind must be "slack_channel", "slack_dm" or "email"') from err
        channel_id = b.opt_text("channel_id", 40)
        if kind is not DeliveryKind.SLACK_CHANNEL and channel_id is not None:
            raise ValidationError("channel_id is only for a Slack channel delivery")
        ready = slack_ready()
        channels = delivery_channels(ready)
        # One read of Slack's readiness and of the channel list per create (scan cycles 3 and 4).
        options = delivery_options(p, channels, ready)
        if not options[kind.value]:
            raise ValidationError(f"{kind.value} delivery is not available to you on this deployment")
        if kind is DeliveryKind.EMAIL:
            return Target(kind, str(p.email), address=p.email)
        if kind is DeliveryKind.SLACK_DM:
            return Target(kind, "Slack direct message to you", address=dm_key(p))
        listed = {c.id: c for c in channels}
        pick = channel_id or (WEBHOOK_CHANNEL if WEBHOOK_CHANNEL in listed else None)
        if pick is None or pick not in listed:
            raise ValidationError("channel_id must be one of the channels an administrator listed")
        label = listed[pick].label
        return Target(kind, label if pick == WEBHOOK_CHANNEL else f"#{label}", channel_id=pick)

    def schedule_changes(b: Body) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if b.has("cadence"):
            changes["cadence"] = b.text("cadence", max_len=10)
        if b.has("weekday"):
            changes["weekday"] = None if b.raw["weekday"] is None else b.integer("weekday", low=0, high=6)
        if b.has("hour"):
            changes["hour"] = b.integer("hour", low=0, high=23)
        if b.has("timezone"):
            changes["timezone"] = b.text("timezone", max_len=TIMEZONE_MAX)
        if b.has("enabled"):
            changes["enabled"] = b.flag("enabled")
        return changes

    @r.route("GET", "/api/me/deliveries")
    def deliveries_list(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        items = [d.to_public() for d in ctx.deliveries.for_user(p.subject)] if ctx.settings.digest.any else []
        if any(i.get("channel_id") for i in items):
            # An admin may have renamed a channel since: show the name it has now (scan cycle 29).
            names = {c.id: c.label for c in ctx.deliveries.channels()}
            for i in items:
                if i.get("channel_id") in names:
                    i["target_label"] = f"#{names[i['channel_id']]}"
        return ok({"items": items, "options": delivery_options(p)})

    @r.route("POST", "/api/me/deliveries")
    def deliveries_create(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        b = Body(req.json())
        b.only("kind", "channel_id", "cadence", "weekday", "hour", "timezone", "enabled")
        target = delivery_target(p, b)
        changes = {"cadence": "daily", "weekday": None, "enabled": True} | schedule_changes(b)
        if "hour" not in changes or "timezone" not in changes:
            raise ValidationError("hour and timezone are required")
        d = ctx.deliveries.create(
            p.subject,
            p.username or p.subject,
            target,
            cadence=changes["cadence"],
            weekday=changes["weekday"],
            hour=changes["hour"],
            timezone=changes["timezone"],
            enabled=changes["enabled"],
        )
        audit(p, "delivery.create", d.id, {"kind": d.kind.value, "channel_id": d.channel_id})
        return ok(d.to_public(), 201)

    @r.route("PATCH", "/api/me/deliveries/{did}")
    def deliveries_update(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        b = Body(req.json())
        b.only("cadence", "weekday", "hour", "timezone", "enabled")
        d = ctx.deliveries.update(p.subject, req.path_params["did"], schedule_changes(b))
        audit(p, "delivery.update", d.id, {"enabled": d.enabled, "cadence": d.cadence.value})
        return ok(d.to_public())

    @r.route("DELETE", "/api/me/deliveries/{did}")
    def deliveries_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        gone = ctx.deliveries.delete(p.subject, req.path_params["did"])
        audit(p, "delivery.delete", gone.id, {"kind": gone.kind.value})
        return ok({"id": gone.id, "deleted": True})

    @r.route("POST", "/api/me/deliveries/{did}/test")
    def deliveries_test(req: Request) -> Response:
        """Send the current digest once, now; the delivery's cursor stays where it is (one per 10 minutes)."""
        p = principal_of(req)
        Policy.require(p, Action.READ)
        d = ctx.deliveries.get(p.subject, req.path_params["did"])
        if d.suppressed:
            raise ConflictError(SUPPRESSED_MESSAGE, {"code": "suppressed"})
        if not ctx.settings.digest.allows(d.kind):
            return ok({"sent": False, "total": 0, "error": "this kind of delivery is switched off on this deployment"})
        # The rate limit first: a refusal from it must not use up the delivery's test slot (scan cycle 1, L3).
        limiter.hit(p.subject, "graph", admin=p.is_admin, role=p.role.value)  # it builds a whole digest
        d = ctx.deliveries.take_test(p.subject, d.id, d)

        try:
            result = cast(dict[str, Any], ctx.delivery_runner.test(d))
        except Exception as err:
            # The attempt is on the record whatever went wrong, with the size it had built (scan cycles 17 to 21).
            audit(p, "delivery.test", d.id, {"sent": False, "total": digest_total(err), "failed": True})
            if nothing_sent(err):
                # A store or graph error before the send (a 503 "retry shortly" or a 500): nothing can have gone
                # out, so the slot goes back. A send-side error keeps it: a timeout may have delivered, and the
                # limit also guards Slack's and the directory lookup's shared rate limits (scan cycles 21 to 23).
                try:
                    ctx.deliveries.give_back_test(p.subject, d.id, d.test_at)
                except Exception:
                    log.exception(json.dumps({"event": "digest.test_slot_kept", "delivery": d.id}))
            raise
        details = {"sent": result["sent"], "total": result["total"]}
        if result.get("suppressed"):
            # Stopped by a bounce or complaint during the build: recorded with the digest's size, then refused.
            audit(p, "delivery.test", d.id, details | {"suppressed": True})
            raise ConflictError(SUPPRESSED_MESSAGE, {"code": "suppressed"})
        audit(p, "delivery.test", d.id, details)
        return ok(result)

    @r.route("GET", "/api/admin/digest/channels")
    def digest_channels_get(req: Request) -> Response:
        Policy.require(principal_of(req), Action.PROFILE_MANAGE)
        listed, version = ctx.deliveries.stored_channels()
        return ok(channel_list(listed, version))

    @r.route("PUT", "/api/admin/digest/channels")
    def digest_channels_put(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.PROFILE_MANAGE)
        b = Body(req.json())
        b.only("channels", "version")
        # Required, so no client can skip the lost-update check (scan cycle 32); bounded like a JSON-safe integer.
        try:
            version = b.integer("version", low=0, high=MAX_LIST_VERSION - 1)
        except ValidationError as err:  # an out-of-date page (scan cycle 33)
            raise ValidationError(RELOAD_MESSAGE, {"code": "reload_needed"}) from err
        channels = DeliveryService.check_channels(b.items("channels", max_items=MAX_CHANNELS))
        # The diff is against the item this write replaced, so it describes exactly this change (scan cycle 31).
        replaced, stored = ctx.deliveries.replace_channels(channels, p.actor, version)
        before = {c.id: c.label for c in replaced}
        after = {c.id: c.label for c in channels}
        changes = channel_changes(before, after)
        audit(p, "digest.channels", "digest", changes)
        for cid in changes["renamed"]:  # one small row each, so no row outgrows the audit log's limit (c30)
            audit(p, "digest.channel_renamed", "digest", {"channel": cid, "from": before[cid], "to": after[cid]})
        return ok(channel_list(channels, stored))

    def unsubscribe_token(req: Request) -> str:
        """The token from ``?t=``, a JSON body ``{"t": ...}``, or a form post (the confirm page's own form). An
        RFC 8058 one-click post carries ``List-Unsubscribe=One-Click`` in its body and the token in the URL."""
        token = req.query.get("t", "")
        if token or not req.body:
            return token
        if "json" in req.headers.get("content-type", "") or req.body.lstrip().startswith(b"{"):
            raw = req.json().get("t")
            return raw if isinstance(raw, str) else ""
        form = parse_qs(req.body[:4096].decode("utf-8", "replace"))
        return (form.get("t") or [""])[0]

    @r.route("GET", UNSUBSCRIBE_PATH)
    def unsubscribe_page(req: Request) -> Response:
        """A confirm page with a button: mail scanners open links, so opening one changes nothing."""
        token = req.query.get("t", "")[: UnsubscribeTokens.MAX_LEN]
        if not ctx.settings.digest.email:  # no form that could only answer 404 (scan cycle 38)
            body = f"<p>{UNSUBSCRIBE_NOT_USED}</p>"
        elif token:
            action = html.escape(f"{UNSUBSCRIBE_PATH}?{urlencode({'t': token})}", quote=True)
            body = (
                "<p>Stop this digest email? Only this one delivery is turned off.</p>"
                f'<form method="post" action="{action}"><button type="submit">Unsubscribe</button></form>'
            )
        else:
            body = f"<p>{UNSUBSCRIBE_BAD_LINK} Open the link in the email again.</p>"
        return _html_page("Unsubscribe from the digest", body)

    @r.route("POST", UNSUBSCRIBE_PATH)
    def unsubscribe(req: Request) -> Response:
        """Turns off the one delivery the signed token names; needs no sign-in. The confirm page's own form gets
        every answer as a page, from ``unsubscribe_page_answer`` (scan cycles 38 to 40)."""
        if not ctx.settings.digest.email:
            raise NotFoundError("unsubscribe links are not used on this deployment")
        token = unsubscribe_token(req)
        if not token:
            raise ValidationError("t (the token from the link) is required")
        try:
            subject, delivery_id = ctx.delivery_outlets.unsubscribe.verify(token)
        except TransientDeliveryError as err:
            raise UnsubscribeRetryError("the unsubscribe link could not be checked just now.") from err  # c48
        if ctx.deliveries.switch_off(subject, delivery_id, suppress=False):
            try:
                ctx.audit.record_actor("public:unsubscribe", "", "delivery.unsubscribed", delivery_id, {})
            except Exception:
                log.exception(json.dumps({"event": "audit.write_failed", "action": "delivery.unsubscribed"}))
        return ok({"ok": True})

    @r.route("GET", "/api/radars/entries")
    def radars_entries_search(req: Request) -> Response:
        """Entries across radars by name (``q``) or by linked node (``node_id``): which radars place X."""
        Policy.require(principal_of(req), Action.READ)
        q = req.query.get("q", "").strip()[:200]
        node_id = req.query.get("node_id", "").strip()[:200]
        if node_id:
            node_type_of(node_id)
        if q and len(q) < 2:
            raise ValidationError("q must be at least 2 characters")
        return ok({"items": ctx.radars.find_entries(q, node_id)})

    @r.route("POST", "/api/radars")
    def radars_create(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.RADAR_CREATE)
        b = Body(req.json())
        b.only(
            "slug", "name", "purpose", "description", "template", "schema"
        )  # a misspelt key used to be ignored (CTR H-016)
        schema = RadarSchema.from_public(b.mapping("schema"), strict=True) if b.raw.get("schema") is not None else None
        radar = ctx.radars.create(
            p,
            b.text("slug", max_len=64),
            b.text("name", max_len=120),
            template=b.opt_text("template", 60),
            schema=schema,
            purpose=b.text("purpose", required=False, max_len=300),
            description=b.text("description", required=False, max_len=2000),
        )
        audit(p, "radar.create", radar.slug)
        return ok(radar_view(p, radar), 201)

    @r.route("GET", "/api/radars/{slug}")
    def radars_get(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        version = _pinned_version(req)
        radar, number, schema, entries = ctx.radars.version_body(req.path_params["slug"], version)
        return ok(
            {
                "radar": radar_view(p, radar),
                "version": number,
                "schema": schema.to_public(),
                "entries": [e.to_public() for e in entries],
                "can_edit": ctx.radars.can_edit(p, radar),
                "can_admin": ctx.radars.can_admin(p, radar),
            }
        )

    @r.route("PATCH", "/api/radars/{slug}")
    def radars_meta(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.RADAR_EDIT)
        b = Body(req.json())
        lock = b.integer("version_lock")
        changes = {k: v for k, v in b.raw.items() if k != "version_lock"}
        if not changes:
            # A lock-only PATCH used to bump the lock (so every collaborator's open form got a 409)
            # and write an audit row with no fields.
            raise ValidationError("nothing to change")
        before = ctx.radars.get(req.path_params["slug"])
        radar = ctx.radars.update_meta(p, req.path_params["slug"], changes, lock)
        audit(p, "radar.update", radar.slug, {"fields": sorted(changes), **_radar_delta(before, radar)})
        return ok(radar_view(p, radar))

    @r.route("PUT", "/api/radars/{slug}/entries")
    def radars_save(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.RADAR_EDIT)
        b = Body(req.json())
        b.only(
            "entries", "expected_version", "note", "allow_unmatched", "allow_empty"
        )  # a misspelt key used to be ignored (CTR H-016)
        radar = ctx.radars.save(
            p,
            req.path_params["slug"],
            _entries(b.items("entries", max_items=400)),
            b.integer("expected_version"),
            b.text("note", required=False, max_len=300),
            b.flag("allow_unmatched", required=False),
            b.flag("allow_empty", required=False),
        )
        audit(p, "radar.save", radar.slug, {"version": radar.current_version, "entries": radar.entry_count})
        return ok(radar_view(p, radar))

    @r.route("POST", "/api/radars/{slug}/import")
    def radars_import(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.RADAR_EDIT)
        b = Body(req.json())
        b.only(
            "format", "text", "mode", "dry_run", "expected_version", "allow_unmatched", "allow_empty"
        )  # a misspelt key used to be ignored (CTR H-016)
        fmt, text = b.text("format", max_len=10), b.text("text", max_len=6 * 1024 * 1024)
        mode = b.text("mode", required=False, default="replace", max_len=10)
        if b.flag("dry_run", required=False):
            return ok(ctx.radars.validate_import(p, req.path_params["slug"], text, fmt, mode))
        before = ctx.radars.get(req.path_params["slug"]).entry_count
        radar = ctx.radars.import_entries(
            p,
            req.path_params["slug"],
            text,
            fmt,
            b.integer("expected_version"),
            mode,
            b.flag("allow_unmatched", required=False),
            b.flag("allow_empty", required=False),
        )
        audit(
            p,
            "radar.import",
            radar.slug,
            {
                "format": fmt,
                "mode": mode,
                "version": radar.current_version,
                # Without the counts an allow_empty wipe looked exactly like a normal import.
                "entries_before": before,
                "entries_after": radar.entry_count,
            },
        )
        return ok(radar_view(p, radar))

    @r.route("GET", "/api/radars/{slug}/export")
    def radars_export(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        fmt = req.query.get("format", "json")
        version = _pinned_version(req)
        text = ctx.radars.export(req.path_params["slug"], fmt, version)
        ctype = "text/csv" if fmt == "csv" else "application/json"
        return Response(
            200,
            raw=text,
            content_type=f"{ctype}; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{req.path_params["slug"]}.{fmt}"'},
        )

    @r.route("GET", "/api/radars/{slug}/template")
    def radars_template(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        return Response(
            200, raw=ctx.radars.template_csv(req.path_params["slug"]), content_type="text/csv; charset=utf-8"
        )

    def _pinned_version(req: Request) -> int | None:
        """``?version=<n>`` or ``?tag=<name>``: a shared link to a tagged version keeps showing it."""
        tag = req.query.get("tag", "").strip()
        if tag:
            return int(ctx.radars.version_of_tag(req.path_params["slug"], tag[:80]))
        number: int = req.int_query("version", 0, 0, 1_000_000)
        return number or None

    @r.route("GET", "/api/radars/{slug}/tags")
    def radar_tags(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        ctx.radars.get(req.path_params["slug"])
        return ok({"items": ctx.radars.tags(req.path_params["slug"])})

    @r.route("PUT", "/api/radars/{slug}/versions/{version}/tag")
    def radar_tag_put(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)  # the radar's own editors decide (checked by the service)
        b = Body(req.json())
        b.only("tag")
        try:
            number = int(req.path_params["version"])
        except ValueError as err:
            raise ValidationError("version must be a number") from err
        out = ctx.radars.tag_version(p, req.path_params["slug"], number, b.text("tag", max_len=80))
        audit(p, "radar.tag", req.path_params["slug"], out)
        return ok(out)

    @r.route("DELETE", "/api/radars/{slug}/tags/{tag}")
    def radar_tag_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.READ)
        ctx.radars.untag(p, req.path_params["slug"], req.path_params["tag"][:80])
        audit(p, "radar.untag", req.path_params["slug"], {"tag": req.path_params["tag"][:80]})
        return ok({"deleted": True})

    @r.route("GET", "/api/radars/{slug}/entries/{key}/history")
    def radar_entry_history(req: Request) -> Response:
        """An entry's changes over the radar's newest versions (CTR HA-003/012/043/072)."""
        Policy.require(principal_of(req), Action.READ)
        return ok(ctx.radars.entry_history(req.path_params["slug"], req.path_params["key"][:200]))

    @r.route("GET", "/api/radars/{slug}/versions")
    def radars_versions(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        items, cursor = ctx.radars.versions_page(
            req.path_params["slug"], req.int_query("limit", 200, 1, 200), req.query.get("cursor")
        )
        return ok({"items": items, "cursor": cursor})

    @r.route("POST", "/api/radars/{slug}/rollback")
    def radars_rollback(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.RADAR_EDIT)
        b = Body(req.json())
        b.only("version", "expected_version", "allow_empty")  # a misspelt key used to be ignored (CTR H-016)
        radar = ctx.radars.rollback(
            p,
            req.path_params["slug"],
            b.integer("version", low=1, high=1_000_000),
            b.integer("expected_version"),
            b.flag("allow_empty", required=False),
        )
        audit(p, "radar.rollback", radar.slug, {"to": b.raw.get("version")})
        return ok(radar_view(p, radar))

    @r.route("GET", "/api/radars/{slug}/diff")
    def radars_diff(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        current = ctx.radars.get(req.path_params["slug"]).current_version
        # Without parameters: what the latest save changed.
        to_version = req.int_query("to", max(1, current), 1, 1_000_000)
        from_version = req.int_query("from", max(1, to_version - 1), 1, 1_000_000)
        return ok(ctx.radars.diff(req.path_params["slug"], from_version, to_version))

    @r.route("PUT", "/api/radars/{slug}/schema")
    def radars_schema(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.RADAR_EDIT)
        b = Body(req.json())
        b.only(
            "schema", "renames", "drop_unmatched", "expected_version"
        )  # a misspelt key used to be ignored (CTR H-016)
        renames_raw = b.mapping("renames", required=False)
        renames: dict[str, dict[str, str]] = {}
        for dim, mapping in renames_raw.items():
            if not isinstance(mapping, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()
            ):
                raise ValidationError("renames must map dimension -> {old: new}")
            renames[dim] = mapping
        before = ctx.radars.get(req.path_params["slug"]).entry_count
        radar = ctx.radars.update_schema(
            p,
            req.path_params["slug"],
            RadarSchema.from_public(b.mapping("schema"), strict=True),
            b.integer("expected_version"),
            renames,
            b.flag("drop_unmatched", required=False),
        )
        audit(
            p,
            "radar.schema",
            radar.slug,
            {
                "version": radar.current_version,
                "renames": renames,
                "entries_dropped": max(0, before - radar.entry_count),
            },
        )
        return ok(radar_view(p, radar))

    @r.route("PUT", "/api/radars/{slug}/access")
    def radars_access(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.RADAR_EDIT)
        b = Body(req.json())
        b.only("editors", "owner", "version_lock", "confirm_unknown")  # a misspelt key used to be ignored (CTR H-016)
        owner = b.mapping("owner", required=False)
        new_owner = None
        # Authorise before looking anyone up: otherwise any editor could probe which accounts exist, and
        # their role, through the error, and make an AdminGetUser call per request.
        before = ctx.radars.get(req.path_params["slug"])
        if not ctx.radars.can_admin(p, before):
            raise ForbiddenError("only the radar owner or an administrator can do this")
        if owner:
            # Only an administrator: an owner could otherwise push a radar onto any enabled editor
            # without their consent, which both bypassed the per-owner cap (give 25 away, create 25
            # more) and let one editor fill a colleague's cap so they could not create radars.
            if not p.is_admin:
                raise ForbiddenError("only an administrator can transfer a radar to another owner")
            # The owner's email comes from the user pool, never from the body: a typed email let an owner
            # show "ceo@example.com" as the owner while keeping control themself.
            user = ctx.users.get(Body(owner).text("subject", max_len=200))
            if not user.enabled or user.role is Role.VIEWER:
                raise ValidationError("the new owner must be an enabled editor or administrator")
            new_owner = (user.subject or user.username, user.email)
        # An absent "editors" leaves the list alone; an empty list still clears it.
        editors = b.texts("editors", max_items=50, max_len=254) if b.has("editors") else None
        if editors is not None and not b.flag("confirm_unknown", required=False):
            # Only the addresses being added are looked up, after the authorisation above (CTR HCB-037).
            had = {e.lower() for e in before.editors}
            unknown = sorted({e for e in editors if e.lower() not in had and not ctx.users.has_account(e)})
            if unknown:
                raise ValidationError(
                    "no account signs in with " + ", ".join(unknown) + ": check the spelling, or confirm to add anyway",
                    {"code": "unknown_editors", "unknown": unknown},
                )
        radar = ctx.radars.set_access(p, req.path_params["slug"], editors, b.integer("version_lock"), new_owner)
        details: dict[str, Any] = {"editors": len(radar.editors)}
        added = sorted(set(radar.editors) - set(before.editors))
        removed = sorted(set(before.editors) - set(radar.editors))
        if added:
            details["editors_added"] = added
        if removed:
            details["editors_removed"] = removed
        if new_owner:
            details["owner"] = {"before": before.owner, "after": radar.owner}
        audit(p, "radar.access", radar.slug, details)
        return ok(radar_view(p, radar))

    @r.route("DELETE", "/api/radars/{slug}")
    def radars_delete(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.RADAR_EDIT)
        gone = ctx.radars.delete(p, req.path_params["slug"])
        audit(p, "radar.delete", req.path_params["slug"], gone)
        return ok()

    @r.route("GET", "/api/radars/{slug}/suggestions")
    def suggestions(req: Request) -> Response:
        Policy.require(principal_of(req), Action.READ)
        return ok({"items": ctx.radars.suggestions(req.path_params["slug"])})

    @r.route("POST", "/api/radars/{slug}/suggestions/accept")
    def suggestions_accept_many(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.SUGGESTION_REVIEW)
        b = Body(req.json())
        b.only("keys")
        radar, sugs = ctx.radars.accept_many(p, req.path_params["slug"], b.texts("keys", max_items=50, max_len=200))
        for sug in sugs:  # one row per entry, as a one-by-one accept writes
            audit(
                p,
                "suggestion.accept",
                f"{radar.slug}/{sug['entry_key']}",
                {
                    "title": str((sug.get("proposal") or {}).get("title", ""))[:200],
                    "proposed_by": sug.get("created_by"),
                    "run_id": sug.get("run_id"),
                    "is_new": bool(sug.get("is_new")),
                    "edited": [],
                    "version": radar.current_version,
                    "batch": len(sugs),
                },
            )
        return ok(radar_view(p, radar))

    @r.route("POST", "/api/radars/{slug}/suggestions/{key}/accept")
    def suggestion_accept(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.SUGGESTION_REVIEW)
        b = Body(req.json())
        b.only("edits")  # a misspelt key used to be ignored (CTR H-016)
        edits = b.mapping("edits", required=False)
        allowed = {"title", "sector", "ring", "marker", "description", "node_id", "link", "sub_sector"}
        if set(edits) - allowed:
            raise ValidationError("edits may only change entry fields")
        radar, sug = ctx.radars.accept(p, req.path_params["slug"], req.path_params["key"], edits or None)
        # What was applied, by whom it was proposed and what the reviewer changed: the row used to be
        # empty while a discard's said what was rejected (B3-043).
        audit(
            p,
            "suggestion.accept",
            f"{radar.slug}/{req.path_params['key']}",
            {
                "title": str((sug.get("proposal") or {}).get("title", ""))[:200],
                "proposed_by": sug.get("created_by"),
                "run_id": sug.get("run_id"),
                "is_new": bool(sug.get("is_new")),
                "edited": sorted(edits),
                "version": radar.current_version,
            },
        )
        return ok(radar_view(p, radar))

    @r.route("DELETE", "/api/radars/{slug}/suggestions/{key}")
    def suggestion_discard(req: Request) -> Response:
        p = principal_of(req)
        Policy.require(p, Action.SUGGESTION_REVIEW)
        # The discard returns what it removed (once discarded the proposal is gone, and the row said
        # nothing about what was rejected or who proposed it); a suggestion accepted meanwhile is a 404.
        gone = ctx.radars.discard(p, req.path_params["slug"], req.path_params["key"])
        audit(
            p,
            "suggestion.discard",
            f"{req.path_params['slug']}/{req.path_params['key']}",
            {
                "title": str((gone.get("proposal") or {}).get("title", ""))[:200],
                "proposed_by": gone.get("created_by"),
                "run_id": gone.get("run_id"),
                "is_new": bool(gone.get("is_new")),
            },
        )
        return ok()

    # ---- admin ----------------------------------------------------------------------
    @r.route("GET", "/api/audit")
    def audit_day(req: Request) -> Response:
        Policy.require(principal_of(req), Action.AUDIT_READ)
        date = req.query.get("date") or ctx.clock.now().strftime("%Y-%m-%d")
        items, cursor = ctx.audit.page(date, req.int_query("limit", 200, 1, 1000), req.query.get("cursor"))
        return ok({"items": items, "cursor": cursor})

    @r.route("GET", "/api/health")
    def health(req: Request) -> Response:
        Policy.require(principal_of(req), Action.HEALTH_READ)
        runs, _ = ctx.modules.list_runs(None, 100)
        by_status: dict[str, int] = {}
        for run in runs:
            by_status[run.status.value] = by_status.get(run.status.value, 0) + 1
        budgets = []
        for m in ctx.modules.list_all():
            snap = ctx.modules.budget(m.slug, m.daily_token_budget)
            budgets.append(
                {
                    "module": m.slug,
                    "enabled": m.enabled,
                    "consumed": snap.consumed,
                    "limit": snap.limit,
                    "state": snap.state.value,
                }
            )
        andon = ctx.modules.andon()
        return ok(
            {
                "runs": by_status,
                "recent_failures": [x.to_public() for x in runs if x.status.value == "failed"][:10],
                "budgets": budgets,
                "genai_enabled": andon.genai_enabled,
            }
        )

    return r


def _audit_name(name: str, keep: int = 60) -> str:
    """A field name short enough that ten of them stay whole in one audit row, measured as the audit writes it
    (``json.dumps``, which escapes non-ASCII; code reviews r25, r26). A long one keeps its start, cut on a whole
    character, and a hash of the whole, so two names that share a start stay apart."""
    if len(json.dumps(name)) - 2 <= keep:
        return name
    # The written size so far, character by character; it only grows, so this keeps the longest prefix that fits.
    sizes = itertools.accumulate(len(json.dumps(ch)) - 2 for ch in name)
    # takewhile stops at the first character that does not fit (code review r27: a filter scanned to the end).
    cut = "".join(ch for ch, _ in itertools.takewhile(lambda pair: pair[1] <= keep, zip(name, sizes, strict=True)))
    return f"{cut}...#{hashlib.sha256(name.encode()).hexdigest()[:12]}"
