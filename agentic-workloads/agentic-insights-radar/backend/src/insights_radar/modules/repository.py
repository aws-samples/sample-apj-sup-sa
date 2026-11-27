# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Ops-table persistence for modules, sources, progress, runs, budgets and the kill switch.

Key layout (ops table):
``MODULE#<slug>/DEF``, ``MODULE#<slug>/SRC#<id>``, ``MODULE#<slug>/PROGRESS``,
``MODULE#<slug>/RUN#<run_id>`` (UUIDv7, sorts by start; gsi1 ``RUNS`` / ``<started_at>#<slug>``),
``BUDGET#<slug>/DAY#<yyyy-mm-dd>``, ``ANDON/STATE``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, SystemClock, iso, parse_iso
from insights_radar.core.dynamo import PageHook, Query
from insights_radar.core.errors import ConflictError, NotFoundError, ValidationError
from insights_radar.core.ids import IdGenerator
from insights_radar.core.tokens import BudgetSnapshot, TokenUsage, TokenWeights
from insights_radar.graph.registry import NodeType
from insights_radar.graph.repository import INDEX_KEYS, TABLE_KEYS, _from_ddb, _to_ddb, decode_cursor, encode_cursor
from insights_radar.modules.attributes import AttributeSchema, parse_schema
from insights_radar.modules.model import (
    GATED_GRANTS,
    MAX_MODULE_SKILLS,
    OUTBOUND_GRANTS,
    McpServer,
    ModuleDefinition,
    ModuleTemplate,
    ModuleValidator,
    Source,
    SourceType,
    ToolGrant,
)
from insights_radar.modules.skills import OFFERED_KEY as SKILLS_OFFERED_KEY
from insights_radar.modules.templates import (
    TEMPLATE_CHANGES,
    TEMPLATE_CHANGES_KEY,
    TEMPLATE_OVERRIDES_KEY,
    builtin_modules,
)

MAX_SOURCES = 500


def _run_floor(started_after: str) -> str:
    """The lowest run id (UUIDv7) that can belong to a run still inside the token window.

    Run ids sort by their embedded millisecond timestamp, so the window is a key range rather than a
    scan of "the latest 20 runs" (a long run followed by 20 quick failures fell out of that and its
    concurrency slot went with it). A day of margin below the window absorbs clock skew; runs that
    really started earlier are still dropped by the ``started_at`` filter.
    """
    if not started_after:
        return ""
    ms = int((parse_iso(started_after) - timedelta(days=1)).timestamp() * 1000) & ((1 << 48) - 1)
    hexed = f"{ms:012x}"
    return f"{hexed[:8]}-{hexed[8:]}-0000-0000-000000000000"


def _address_key(address: str) -> str:
    """How two source addresses are compared for uniqueness: the scheme's and host's case, the default
    port, a fragment and a trailing slash do not make a second feed; the path and query are compared
    exactly, since APIs treat their case as meaningful (B3-035)."""
    try:
        parts = urlsplit(address.strip())
        host = (parts.hostname or "").rstrip(".")
        port = parts.port
    except ValueError:
        return address.strip()
    scheme = parts.scheme.lower()
    # Only the scheme's own default port folds: http://h:443/x is not http://h/x (CR3-20).
    netloc = host if port is None or port == {"http": 80, "https": 443}.get(scheme) else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), parts.query, ""))


PROGRESS_ATTEMPTS = 5
# The fields of a built-in an administrator has set themself (extra.admin_set).
ADMIN_SET_KEY = "admin_set"
RUN_RETENTION_DAYS = 180
BUDGET_RETENTION_DAYS = 90
# Called with (action, module slug, details) the moment a seed change lands.
AuditHook = Callable[[str, str, dict[str, Any]], None]


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.BUDGET_EXHAUSTED, RunStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class ModuleRun:
    module: str
    run_id: str
    status: RunStatus
    trigger: str
    started_at: str
    requested_by: str
    finished_at: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    weighted_tokens: int = 0
    nodes_written: int = 0
    summary: str = ""
    error: str | None = None
    execution_arn: str | None = None
    target_id: str | None = None
    budget_hold: int = 0  # weighted tokens reserved for this run at start, released at finalize
    hold_day: str | None = None  # the ledger day the hold was written to
    # The model tier the run was started on: finalize weighs its usage with it, and the runtime's
    # budget guard spends at the same rate, so a later model change or a deleted module cannot change
    # what the run costs (CR2-05, CR2-08). None on a run recorded before it was stored.
    tier: str | None = None

    def to_public(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            out[f.name] = value.value if isinstance(value, StrEnum) else value
        return out


@dataclass(slots=True)
class SeedReport:
    created: list[str] = field(default_factory=list)
    migrated: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    # Not a deploy failure: an unsafe custom module the seed could not disable is refused every run and
    # tool call anyway (B3-021).
    warnings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Progress:
    last_processed_at: str | None
    remaining_history: tuple[int, ...]
    last_dispatched_at: str | None


@dataclass(frozen=True, slots=True)
class AndonState:
    genai_enabled: bool
    reason: str | None
    updated_by: str | None
    updated_at: str | None


log = logging.getLogger(__name__)


def _stored_schema(raw: Any) -> AttributeSchema:
    """A stored schema was validated when it was saved; one that no longer parses (a rule tightened
    since) is dropped rather than breaking every read of the module, and logged."""
    try:
        return parse_schema(raw)
    except ValueError:
        log.warning("module.attribute_schema_invalid_on_read")
        return AttributeSchema()


class ModuleRepository:
    def __init__(self, table: Any, clock: Clock | None = None, ids: IdGenerator | None = None) -> None:
        self._t = table
        self._clock = clock or SystemClock()
        self._ids = ids or IdGenerator(self._clock)
        self._andon_cache: tuple[float, AndonState] | None = None

    # ---- definitions ---------------------------------------------------------------

    @staticmethod
    def _def_item(m: ModuleDefinition) -> dict[str, Any]:
        item: dict[str, Any] = {
            "pk": f"MODULE#{m.slug}",
            "sk": "DEF",
            "gsi1pk": "MODULES",
            "gsi1sk": m.slug,
            "kind": "module",
            **{k: v for k, v in m.to_public().items() if v is not None and v not in ([], {})},
        }
        return dict(_to_ddb(item))

    @staticmethod
    def _def_from_item(raw: dict[str, Any]) -> ModuleDefinition:
        i = _from_ddb(raw)
        return ModuleDefinition(
            slug=i["slug"],
            name=i["name"],
            template=ModuleTemplate(i["template"]),
            output_kind=NodeType(i["output_kind"]) if i.get("output_kind") else None,
            description=i.get("description", ""),
            instructions=i.get("instructions", ""),
            insight_kind=i.get("insight_kind"),
            insight_label=i.get("insight_label"),
            input_types=tuple(NodeType(t) for t in i.get("input_types", [])),
            grants=frozenset(ToolGrant(g) for g in i.get("grants", [])),
            product_id=i.get("product_id"),
            model_key=i.get("model_key", "sonnet-5-5"),
            interval_minutes=int(i.get("interval_minutes", 360)),
            daily_token_budget=int(i.get("daily_token_budget", 2_000_000)),
            max_concurrency=int(i.get("max_concurrency", 1)),
            mcp_servers=tuple(McpServer(s["name"], s["url"]) for s in i.get("mcp_servers", [])),
            attribute_schema=_stored_schema(i.get("attribute_schema")),
            prompt_mode=str(i.get("prompt_mode", "extend")),
            system_prompt=str(i.get("system_prompt", "")),
            skills=tuple(str(s) for s in i.get("skills", [])),
            enabled=bool(i.get("enabled", False)),
            builtin=bool(i.get("builtin", False)),
            created_by=i.get("created_by", ""),
            created_at=i.get("created_at", ""),
            updated_at=i.get("updated_at", ""),
            version=int(i.get("version", 1)),
            extra=i.get("extra") or {},
        )

    def seed_builtins(self, actor: str = "system", audit: AuditHook | None = None) -> SeedReport:
        """Create any missing built-in module, and bring every existing one back within the safety rule
        its template encodes. Everything an admin edits is kept.

        Runs on every deploy. It only ever *removes* a grant a stored built-in must not hold: writing
        the template's grants, input types and MCP servers back re-armed browser and web_search on a
        scout an admin had deliberately narrowed, reverted admin-chosen input types, and deleted MCP
        servers an admin had attached, on every single deploy.

        ``audit`` is called as soon as a change lands, not after the whole seed, and one built-in that
        cannot be repaired is reported rather than aborting the ones after it (which used to be left
        both uncreated and unmigrated, with the grants already changed on the ones before it
        unrecorded).
        """
        report = SeedReport()
        for m in builtin_modules():
            now = iso(self._clock.now())
            # A new install already has every template change, so none is applied to it later.
            done = sorted(c.id for c in TEMPLATE_CHANGES if c.slug == m.slug)
            fresh = replace(m, created_by=actor, created_at=now, updated_at=now)
            if done:
                fresh = replace(fresh, extra={**fresh.extra, TEMPLATE_CHANGES_KEY: done})
            item = self._def_item(fresh)
            try:
                self._t.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
            except ClientError as err:
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
            else:
                report.created.append(m.slug)
                self._seed_audit(audit, "module.create", m.slug, {"grants": sorted(g.value for g in m.grants)})
                continue
            try:
                change = self._migrate_builtin(m)
            except Exception as failure:
                report.failed[m.slug] = f"{type(failure).__name__}: {failure}"[:300]
                continue
            if change:
                report.migrated[m.slug] = change
                self._seed_audit(audit, "module.migrate", m.slug, change)
        for custom in self.list_all():
            if custom.builtin or not custom.enabled or custom.run_refusal is None:
                continue
            try:
                change = self._disable_unsafe(custom.slug)
            except Exception as failure:
                report.warnings[custom.slug] = f"{type(failure).__name__}: {failure}"[:300]
                continue
            if change:
                report.migrated[custom.slug] = change
                self._seed_audit(audit, "module.migrate", custom.slug, change)
        return report

    def _disable_unsafe(self, slug: str) -> dict[str, Any]:
        """Turn off a custom module stored before a newer rule forbade its grant combination (SR2-04).
        Every run and tool call is refused anyway; disabling it stops the schedule from starting runs
        that fail, and the audit row tells the admin which module to fix. Its grants are the admin's
        to choose, so they are left as they are. What changed, or {} when a fresh read shows nothing to
        do (an admin fixed or disabled it meanwhile)."""
        attempt = 0
        while True:
            # A consistent read each time: the version from the GSI listing could be stale, and an admin
            # editing the module during the deploy failed the whole seed (B3-021).
            m = self.get(slug)
            if not m.enabled or m.run_refusal is None:
                return {}
            disabled = replace(m, enabled=False, updated_at=iso(self._clock.now()), version=m.version + 1)
            try:
                self._t.put_item(
                    Item=self._def_item(disabled),
                    ConditionExpression="version = :v",
                    ExpressionAttributeValues={":v": m.version},
                )
            except ClientError as err:
                attempt += 1
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException" or attempt >= PROGRESS_ATTEMPTS:
                    raise
                continue
            return {"disabled": True, "reason": m.run_refusal}

    @staticmethod
    def _seed_audit(audit: AuditHook | None, action: str, slug: str, details: dict[str, Any]) -> None:
        if audit is not None:
            audit(action, slug, details)

    GATED_GRANTS: ClassVar[frozenset[ToolGrant]] = GATED_GRANTS

    @classmethod
    def _unsafe_grants(cls, current: ModuleDefinition, template: ModuleDefinition) -> frozenset[ToolGrant]:
        """Which of a stored built-in's grants have to go, and which side of the pair to drop: the
        template says whether the module's job is the gated read or the web."""
        gated = current.grants & cls.GATED_GRANTS
        if not gated or not current.outbound:
            return frozenset()
        if gated & template.grants:
            return current.grants & OUTBOUND_GRANTS
        return gated

    def _migrate_builtin(self, template: ModuleDefinition) -> dict[str, Any]:
        """Remove the grants one stored built-in must not hold, and apply the template changes it has
        not had yet (``TEMPLATE_CHANGES``); what changed, or {} if nothing did.

        A change it has had is applied again when its effect is gone and no administrator undid it
        (``extra.template_overrides``): an upgrade that rolled back ran the older seed after this one,
        which restored the old template's grants and kept the record (r3 P3-003). The record and the
        change are one conditional write, so a record never exists without its change.

        Deliberately does not re-validate: the repair only ever removes a grant or raises a concurrency
        limit, neither of which can make a definition less valid, and a stored built-in that is invalid
        today for an unrelated reason (a model key dropped from the catalog) must not block a
        security-relevant removal.
        """
        attempt = 0
        while True:
            current = self.get(template.slug)
            unsafe = self._unsafe_grants(current, template)
            applied = list(current.extra.get(TEMPLATE_CHANGES_KEY) or [])
            overridden = set(current.extra.get(TEMPLATE_OVERRIDES_KEY) or [])
            own = [c for c in TEMPLATE_CHANGES if c.slug == template.slug]
            reverted = [c for c in own if c.id in applied and c.id not in overridden and c.undone_in(current)]
            pending = [c for c in own if c.id not in applied] + reverted
            if not unsafe and not pending:
                return {}
            removed = set(unsafe)
            concurrency = current.max_concurrency
            effective: list[str] = []
            skipped: list[str] = []
            chosen = set(current.extra.get(ADMIN_SET_KEY) or [])
            for c in pending:
                hit = c.remove_grants & current.grants
                removed |= hit
                if c.max_concurrency and concurrency == c.max_concurrency[0]:
                    if "max_concurrency" in chosen:
                        # An administrator set this value: it is their choice even when it equals the old
                        # default, and the change is recorded as overridden, not applied (B3-020).
                        skipped.append(c.id)
                        continue
                    concurrency = c.max_concurrency[1]
                    effective.append(c.id)
                elif hit:
                    effective.append(c.id)
            changed = bool(removed) or concurrency != current.max_concurrency
            extra = {**current.extra, TEMPLATE_CHANGES_KEY: sorted({*applied, *(c.id for c in pending)})}
            if skipped:
                extra[TEMPLATE_OVERRIDES_KEY] = sorted({*overridden, *skipped})
            migrated = replace(
                current,
                grants=current.grants - frozenset(removed),
                max_concurrency=concurrency,
                extra=extra,
                # Recording a change that had nothing to do is bookkeeping: no new version, so an
                # admin's open edit form is not made stale by it.
                updated_at=iso(self._clock.now()) if changed else current.updated_at,
                version=current.version + 1 if changed else current.version,
            ).with_private_mark(current)
            try:
                self._t.put_item(
                    Item=self._def_item(migrated),
                    ConditionExpression="version = :v",
                    ExpressionAttributeValues={":v": current.version},
                )
            except ClientError as err:
                attempt += 1
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                if attempt >= PROGRESS_ATTEMPTS:
                    raise ConflictError(f"module {template.slug} is being edited; seed again") from err
                continue
            change: dict[str, Any] = {}
            if removed:
                change["grants_removed"] = sorted(g.value for g in removed)
            if concurrency != current.max_concurrency:
                change["max_concurrency"] = [current.max_concurrency, concurrency]
            if effective:
                change["template_changes"] = effective
            if reverted:
                change["template_changes_reapplied"] = sorted(c.id for c in reverted)
            if skipped:
                change["template_changes_skipped"] = skipped
            return change

    def get(self, slug: str) -> ModuleDefinition:
        res = self._t.get_item(Key={"pk": f"MODULE#{slug}", "sk": "DEF"}, ConsistentRead=True)
        if "Item" not in res:
            raise NotFoundError(f"module {slug} not found", {"slug": slug})
        return self._def_from_item(res["Item"])

    def list_all(self) -> list[ModuleDefinition]:
        out: list[ModuleDefinition] = []
        kwargs: dict[str, Any] = {"IndexName": "gsi1", "KeyConditionExpression": Key("gsi1pk").eq("MODULES")}
        while True:
            res = self._t.query(**kwargs)
            out.extend(self._def_from_item(i) for i in res.get("Items", []))
            if "LastEvaluatedKey" not in res:
                return out
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]

    # A deleted module's names stay reserved forever and its private-writer status is remembered. Run
    # items expire after RUN_RETENTION_DAYS and the live module list cannot say anything about a module
    # that is gone, so without this a new module could take a deleted one's slug (and own its outputs,
    # budget and runs) or its insight_kind (and list them under its own nav entry), and deleting a
    # private writer would publish every insight, entity and piece of evidence it ever wrote.
    # One small item per reserved name, never one item of growing sets: a few thousand create/delete
    # cycles filled the old single item to DynamoDB's 400 KB limit, after which every module delete
    # failed, and every hider call read the whole item (B3-023).
    TOMBSTONE_PK = "MODULES"
    LEGACY_TOMBSTONE: ClassVar[dict[str, str]] = {"pk": "MODULES", "sk": "TOMBSTONE"}

    @classmethod
    def _tombstone_key(cls, kind: str, name: str) -> dict[str, str]:
        return {"pk": cls.TOMBSTONE_PK, "sk": f"TOMBSTONE#{kind}#{name}"}

    def _tombstone(self, m: ModuleDefinition) -> None:
        keys = [self._tombstone_key("SLUG", m.slug), self._tombstone_key("KIND", m.insight_kind or m.slug)]
        if m.private_writer:
            keys.append(self._tombstone_key("PRIVATE", m.slug))
        for key in keys:
            self._t.put_item(Item={**key, "kind": "tombstone"})

    # The legacy single item's attribute for each kind. The previous release still writes it, and
    # after a rollback it is the only record of a module that release deleted, so reads take the union
    # of it and the per-name items and the split never deletes it (SR3-01).
    _LEGACY_ATTRIBUTE: ClassVar[dict[str, str]] = {
        "SLUG": "slugs",
        "KIND": "insight_kinds",
        "PRIVATE": "private_writers",
    }

    def _legacy(self, kind: str) -> frozenset[str]:
        item = self._t.get_item(Key=dict(self.LEGACY_TOMBSTONE), ConsistentRead=True).get("Item") or {}
        return frozenset(str(name) for name in item.get(self._LEGACY_ATTRIBUTE[kind]) or ())

    def _reserved(self, kind: str, name: str) -> bool:
        if "Item" in self._t.get_item(Key=self._tombstone_key(kind, name), ConsistentRead=True):
            return True
        return name in self._legacy(kind)

    def _tombstoned(self, kind: str) -> frozenset[str]:
        prefix = f"TOMBSTONE#{kind}#"
        items = Query.all_items(
            self._t,
            KeyConditionExpression=Key("pk").eq(self.TOMBSTONE_PK) & Key("sk").begins_with(prefix),
            ProjectionExpression="sk",
        )
        return frozenset(str(i["sk"]).removeprefix(prefix) for i in items) | self._legacy(kind)

    def tombstones(self) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        """Reserved slugs, reserved insight kinds, and the slugs of deleted private writers."""
        return self._tombstoned("SLUG"), self._tombstoned("KIND"), self._tombstoned("PRIVATE")

    def split_tombstone(self, start: dict[str, Any] | None = None, on_page: PageHook | None = None) -> int:
        """Copy the legacy single tombstone item's names into one item per name, on every deploy: the
        previous release may have added names to it after a rollback. The legacy item is kept (see
        ``_LEGACY_ATTRIBUTE``). Returns how many names were new."""
        del start  # one item: nothing to resume
        item = self._t.get_item(Key=dict(self.LEGACY_TOMBSTONE), ConsistentRead=True).get("Item") or {}
        moved = 0
        for kind, attribute in self._LEGACY_ATTRIBUTE.items():
            for name in item.get(attribute) or ():
                try:
                    self._t.put_item(
                        Item={**self._tombstone_key(kind, str(name)), "kind": "tombstone"},
                        ConditionExpression="attribute_not_exists(pk)",
                    )
                    moved += 1
                except ClientError as err:
                    if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                        raise
        if on_page is not None:
            on_page(None)
        return moved

    def private_writer_slugs(self, modules: list[ModuleDefinition] | None = None) -> frozenset[str]:
        """Modules whose unstamped nodes an outbound module must not read: every live module that holds
        or ever held product_knowledge (the sticky mark), plus deleted ones that ever did (SR2-01).
        ``modules`` is a module list the caller already read, so it is not read twice."""
        live = self.list_all() if modules is None else modules
        return frozenset(m.slug for m in live if m.private_writer) | self._tombstoned("PRIVATE")

    def create(self, m: ModuleDefinition, actor: str) -> ModuleDefinition:
        if m.builtin or m.template is not ModuleTemplate.CUSTOM:
            raise ValidationError("only custom modules can be created; built-ins are seeded")
        if m.output_kind is not NodeType.INSIGHT:
            raise ValidationError("custom modules produce insight nodes")
        clash = [x for x in self.list_all() if x.insight_kind == m.insight_kind]
        if clash:
            raise ConflictError(f"insight_kind {m.insight_kind!r} is already used by module {clash[0].slug}")
        if self._reserved("SLUG", m.slug):
            raise ConflictError(f"slug {m.slug!r} was used by a deleted module; choose another")
        if m.insight_kind and self._reserved("KIND", m.insight_kind):
            raise ConflictError(f"insight_kind {m.insight_kind!r} was used by a deleted module; choose another")
        now = iso(self._clock.now())
        valid = ModuleValidator.validate(
            replace(m, created_by=actor, created_at=now, updated_at=now, version=1)
        ).with_private_mark()
        try:
            self._t.put_item(Item=self._def_item(valid), ConditionExpression="attribute_not_exists(pk)")
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError(f"module {m.slug} already exists") from err
            raise
        return valid

    IMMUTABLE = frozenset({"slug", "template", "builtin", "output_kind", "insight_kind", "created_by", "created_at"})

    def offer_skills(self, names: tuple[str, ...]) -> list[str]:
        """Attach each of ``names`` to every built-in that has not been offered it yet, once: the offer is
        recorded in ``extra.skills_offered``, so a skill an administrator detached stays detached. The
        built-ins that changed."""
        changed: list[str] = []
        for listed in self.list_all():
            # Offered everything already, as the index says: no consistent read needed (one per module, every deploy).
            if not listed.builtin or not set(names) - set(listed.extra.get(SKILLS_OFFERED_KEY) or []):
                continue
            m = self.get(listed.slug)  # consistent: the list is an index read, and the seed wrote this module just now
            offered = list(m.extra.get(SKILLS_OFFERED_KEY) or [])
            missing = [n for n in names if n not in offered]
            if not missing:
                continue
            skills = tuple(dict.fromkeys([*m.skills, *missing]))[:MAX_MODULE_SKILLS]
            offer = replace(m, skills=skills, extra={**m.extra, SKILLS_OFFERED_KEY: [*offered, *missing]})
            try:
                # The same version, like the seed's other repairs: a deploy must not make an administrator with
                # the editor open save into a conflict. Conditional, so a save racing it wins and the skills are
                # offered on the next deploy.
                self._t.put_item(
                    Item=self._def_item(offer),
                    ConditionExpression="version = :v",
                    ExpressionAttributeValues={":v": m.version},
                )
            except ClientError as err:
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                continue
            changed.append(m.slug)
        return changed

    def update(self, slug: str, changes: dict[str, Any], expected_version: int) -> ModuleDefinition:
        bad = set(changes) & self.IMMUTABLE
        if bad:
            raise ValidationError(f"fields cannot be changed: {', '.join(sorted(bad))}")
        current = self.get(slug)
        if current.version != expected_version:
            raise ConflictError("module was changed by someone else; reload and retry")
        updated = ModuleValidator.validate(
            replace(current, **changes, updated_at=iso(self._clock.now()), version=current.version + 1)
        ).with_private_mark(current)
        updated = self._record_overrides(current, updated)
        if "max_concurrency" in changes:
            # Which values an administrator chose, so a template change never takes one for an
            # untouched default (B3-020).
            chosen = sorted({*(updated.extra.get(ADMIN_SET_KEY) or []), "max_concurrency"})
            updated = replace(updated, extra={**updated.extra, ADMIN_SET_KEY: chosen})
        try:
            self._t.put_item(
                Item=self._def_item(updated),
                ConditionExpression="version = :v",
                ExpressionAttributeValues={":v": expected_version},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError("module was changed by someone else; reload and retry") from err
            raise
        return updated

    @staticmethod
    def _record_overrides(current: ModuleDefinition, updated: ModuleDefinition) -> ModuleDefinition:
        """Note which template changes an administrator's edit undoes, so the seed keeps the edit
        rather than taking it for an older release's rollback (r3 P3-003); and forget the ones the
        edit puts back in effect, so a later rollback is repaired again."""
        applied = set(current.extra.get(TEMPLATE_CHANGES_KEY) or [])
        before = set(current.extra.get(TEMPLATE_OVERRIDES_KEY) or [])
        after = set(before)
        for c in TEMPLATE_CHANGES:
            if c.slug != current.slug or c.id not in applied:
                continue
            if c.undone_by(current, updated):
                after.add(c.id)
            elif not c.undone_in(updated):
                after.discard(c.id)
        if after == before:
            return updated
        extra = {k: v for k, v in updated.extra.items() if k != TEMPLATE_OVERRIDES_KEY}
        return replace(updated, extra={**extra, **({TEMPLATE_OVERRIDES_KEY: sorted(after)} if after else {})})

    def delete(self, slug: str) -> None:
        current = self.get(slug)
        if current.builtin:
            raise ValidationError("built-in modules cannot be deleted; disable them instead")
        self._tombstone(current)  # before the definition goes, so a crash leaves the names reserved
        for s in self.list_sources(slug):
            self._t.delete_item(Key={"pk": f"MODULE#{slug}", "sk": f"SRC#{s.id}"})
        for v in self.prompt_history(slug, limit=1000):
            self._t.delete_item(Key={"pk": f"MODULE#{slug}", "sk": v["sk"]})
        self._t.delete_item(Key={"pk": f"MODULE#{slug}", "sk": "PROGRESS"})
        self._t.delete_item(Key={"pk": f"MODULE#{slug}", "sk": "DEF"})

    # ---- sources -------------------------------------------------------------------

    def list_sources(self, slug: str) -> list[Source]:
        items = Query.all_items(
            self._t, KeyConditionExpression=Key("pk").eq(f"MODULE#{slug}") & Key("sk").begins_with("SRC#")
        )
        return [self._source_from_item(i) for i in items]

    @staticmethod
    def _source_from_item(raw: dict[str, Any]) -> Source:
        i = _from_ddb(raw)
        return Source(
            id=i["id"],
            module=i["module"],
            name=i["name"],
            type=SourceType(i["type"]),
            address=i["address"],
            instruction=i.get("instruction", ""),
            cursor=i.get("cursor", ""),
            enabled=bool(i.get("enabled", True)),
            created_at=i.get("created_at", ""),
            updated_at=i.get("updated_at", ""),
            read_at=i.get("read_at", ""),
            tried_at=i.get("tried_at", ""),
        )

    def add_source(
        self,
        slug: str,
        name: str,
        type_: SourceType,
        address: str,
        instruction: str = "",
        cursor: str = "",
        enabled: bool = True,
    ) -> Source:
        module = self.get(slug)
        if ToolGrant.SOURCES not in module.grants:
            raise ValidationError(f"module {slug} does not use sources")
        existing = self.list_sources(slug)
        if len(existing) >= MAX_SOURCES:
            raise ValidationError(f"a module can have at most {MAX_SOURCES} sources")
        if any(_address_key(s.address) == _address_key(address) for s in existing):
            raise ConflictError("this module already has a source with that address", {"code": "duplicate_source"})
        now = iso(self._clock.now())
        src = Source.validate(
            Source(self._ids.short(10), slug, name, type_, address, instruction, cursor, enabled, now, now)
        )
        self._t.put_item(Item={"pk": f"MODULE#{slug}", "sk": f"SRC#{src.id}", "kind": "source", **src.to_public()})
        return src

    def update_source(self, slug: str, source_id: str, changes: dict[str, Any]) -> Source:
        allowed = {"name", "type", "address", "instruction", "cursor", "enabled"}
        bad = set(changes) - allowed
        if bad:
            raise ValidationError(f"fields cannot be changed: {', '.join(sorted(bad))}")
        for key, value in changes.items():
            if not isinstance(value, bool if key == "enabled" else str):
                raise ValidationError(f"{key} must be {'true or false' if key == 'enabled' else 'a string'}")
        current = self.get_source(slug, source_id)
        if "address" in changes and any(
            s.id != source_id and _address_key(s.address) == _address_key(changes["address"])
            for s in self.list_sources(slug)
        ):
            # The same rule as add_source: a PATCH could give a source the address another one has (B3-034).
            raise ConflictError("this module already has a source with that address", {"code": "duplicate_source"})
        if "type" in changes:
            try:
                changes = {**changes, "type": SourceType(changes["type"])}
            except ValueError as err:
                raise ValidationError(f"unknown source type {changes['type']!r}") from err
        # A new address is a new source (never read, so it sorts first: code review r15), and Start over (an
        # administrator cleared the read position) asks for the same: the watcher then takes a new baseline,
        # so the next check queues the last day's items. The checkpoint goes only once the update has landed.
        restart = ("address" in changes and changes["address"] != current.address) or (
            changes.get("cursor") == "" and bool(current.cursor or current.read_at)
        )
        if restart:
            changes = {**changes, "read_at": "", "tried_at": ""}
        updated = Source.validate(replace(current, **changes, updated_at=iso(self._clock.now())))
        public = updated.to_public()
        # Only the fields the PATCH names (and updated_at): setting every field wrote back the cursor
        # read before the update, so a rename racing the scout's cursor move rolled the cursor back
        # and the scout re-processed everything since (CR2-09).
        body = {k: public[k] for k in (*changes, "updated_at")}
        names = {f"#f{i}": k for i, k in enumerate(body)}
        try:
            # An UpdateExpression under attribute_exists, never a whole-item put: a PATCH racing a
            # delete used to re-create the deleted source.
            self._t.update_item(
                Key={"pk": f"MODULE#{slug}", "sk": f"SRC#{source_id}"},
                UpdateExpression="SET " + ", ".join(f"{n} = :v{i}" for i, n in enumerate(names)),
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues={f":v{i}": v for i, v in enumerate(body.values())},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise NotFoundError(f"source {source_id} not found") from err
            raise
        if restart:
            self._t.delete_item(Key={"pk": f"WATCH#{slug}", "sk": f"SRC#{source_id}"})
        return self.get_source(slug, source_id)

    def disable_source(self, slug: str, source_id: str) -> None:
        """Turn a source off without re-validating the rest of it: a source stored under older rules (the reason a
        bundle retires it, perhaps) must still be able to stop."""
        try:
            self._t.update_item(
                Key={"pk": f"MODULE#{slug}", "sk": f"SRC#{source_id}"},
                UpdateExpression="SET enabled = :f, updated_at = :t",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={":f": False, ":t": iso(self._clock.now())},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise NotFoundError(f"source {source_id} not found") from err
            raise

    def get_source(self, slug: str, source_id: str) -> Source:
        res = self._t.get_item(Key={"pk": f"MODULE#{slug}", "sk": f"SRC#{source_id}"}, ConsistentRead=True)
        if "Item" not in res:
            raise NotFoundError(f"source {source_id} not found")
        return self._source_from_item(res["Item"])

    def delete_source(self, slug: str, source_id: str) -> None:
        self.get_source(slug, source_id)
        self._t.delete_item(Key={"pk": f"MODULE#{slug}", "sk": f"SRC#{source_id}"})
        # Its watch checkpoint and inbox go with it (modules/watch.py keys).
        self._t.delete_item(Key={"pk": f"WATCH#{slug}", "sk": f"SRC#{source_id}"})

    def update_cursor(self, slug: str, source_id: str, cursor: str) -> Source:
        if len(cursor) > 2000:
            raise ValidationError("cursor must be at most 2000 characters")
        try:
            res = self._t.update_item(
                Key={"pk": f"MODULE#{slug}", "sk": f"SRC#{source_id}"},
                UpdateExpression="SET #c = :c, updated_at = :u",
                # Disabled too: a run could move the cursor of a source an admin had paused for
                # review, and the module would then skip everything before it.
                ConditionExpression="attribute_exists(pk) AND #e = :on",
                ExpressionAttributeNames={"#c": "cursor", "#e": "enabled"},
                ExpressionAttributeValues={":c": cursor, ":u": iso(self._clock.now()), ":on": True},
                ReturnValues="ALL_NEW",
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise NotFoundError(f"source {source_id} not found for module {slug}") from err
            raise
        return self._source_from_item(res["Attributes"])

    def mark_source_read(self, slug: str, source_id: str, ok: bool = True) -> str:
        """Stamp a fetch attempt on an enabled source: ``tried_at`` always, ``read_at`` when it succeeded.
        Only those fields: neither the cursor nor ``updated_at`` (an admin's edit time) moves."""
        now = iso(self._clock.now())
        try:
            self._t.update_item(
                Key={"pk": f"MODULE#{slug}", "sk": f"SRC#{source_id}"},
                UpdateExpression="SET tried_at = :r, read_at = :r" if ok else "SET tried_at = :r",
                ConditionExpression="attribute_exists(pk) AND #e = :on",
                ExpressionAttributeNames={"#e": "enabled"},
                ExpressionAttributeValues={":r": now, ":on": True},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise NotFoundError(f"source {source_id} not found for module {slug}") from err
            raise
        return now

    # ---- progress ------------------------------------------------------------------

    def _progress_row(self, slug: str) -> dict[str, Any]:
        res = self._t.get_item(Key={"pk": f"MODULE#{slug}", "sk": "PROGRESS"}, ConsistentRead=True)
        row: dict[str, Any] = _from_ddb(res.get("Item") or {})
        return row

    # ---- prompt history -------------------------------------------------------------

    PROMPT_FIELDS = ("instructions", "system_prompt", "prompt_mode", "skills")
    PROMPT_HISTORY_MAX = 50

    def record_prompt(self, m: ModuleDefinition, actor: str) -> None:
        """Keep this version of the module's prompt (instructions, template, mode, skills): the newest
        ``PROMPT_HISTORY_MAX`` per module, so an administrator can see what changed and go back."""
        at = iso(self._clock.now())
        self._t.put_item(
            Item=_to_ddb(
                {
                    "pk": f"MODULE#{m.slug}",
                    "sk": f"PROMPTV#{at}#{m.version:06d}",
                    "kind": "prompt_version",
                    "at": at,
                    "by": actor,
                    "module_version": m.version,
                    "instructions": m.instructions,
                    "system_prompt": m.system_prompt,
                    "prompt_mode": m.prompt_mode,
                    "skills": list(m.skills),
                }
            )
        )
        old = self.prompt_history(m.slug, limit=1000)[self.PROMPT_HISTORY_MAX :]
        for v in old:
            self._t.delete_item(Key={"pk": f"MODULE#{m.slug}", "sk": v["sk"]})

    def prompt_history(self, slug: str, limit: int = 50) -> list[dict[str, Any]]:
        """The module's kept prompt versions, newest first."""
        items = Query.all_items(
            self._t,
            KeyConditionExpression=Key("pk").eq(f"MODULE#{slug}") & Key("sk").begins_with("PROMPTV#"),
            ScanIndexForward=False,
        )[:limit]
        out = []
        for raw in items:
            i = _from_ddb(raw)
            out.append(
                {
                    "sk": str(i["sk"]),
                    "at": str(i.get("at", "")),
                    "by": str(i.get("by", "")),
                    "module_version": int(i.get("module_version", 0)),
                    "instructions": str(i.get("instructions", "")),
                    "system_prompt": str(i.get("system_prompt", "")),
                    "prompt_mode": str(i.get("prompt_mode", "extend")),
                    "skills": [str(s) for s in i.get("skills", [])],
                }
            )
        return out

    def progress(self, slug: str) -> Progress:
        return self._as_progress(self._progress_row(slug))

    @staticmethod
    def _as_progress(i: dict[str, Any]) -> Progress:
        return Progress(
            i.get("last_processed_at"),
            tuple(int(x) for x in i.get("remaining_history", [])),
            i.get("last_dispatched_at"),
        )

    def begin_progress_slot(self, slug: str) -> None:
        """A run started: its first ``remaining`` report opens a new slot in the backlog history."""
        self._t.update_item(
            Key={"pk": f"MODULE#{slug}", "sk": "PROGRESS"},
            UpdateExpression="SET slot_open = :f",
            ExpressionAttributeValues={":f": False},
        )

    def record_progress(self, slug: str, last_processed_at: str | None, remaining: int | None) -> Progress:
        """Update only the fields progress owns, so a concurrent dispatch stamp is never overwritten. The
        history is read and written back under a revision check, so parallel runs (max_concurrency > 1)
        cannot lose each other's reports."""
        attempt = 0
        while True:
            try:
                return self._record_progress(slug, last_processed_at, remaining)
            except ClientError as err:
                attempt += 1
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                if attempt >= PROGRESS_ATTEMPTS:
                    raise ConflictError("progress is being updated concurrently; retry") from err

    def _record_progress(self, slug: str, last_processed_at: str | None, remaining: int | None) -> Progress:
        row = self._progress_row(slug)
        current = self._as_progress(row)
        history = list(current.remaining_history)
        sets: list[str] = []
        values: dict[str, Any] = {}
        if remaining is not None:
            if remaining < 0:
                raise ValidationError("remaining must be >= 0")
            if not history or row.get("slot_open") is False:
                history = [remaining, *history][:5]
            else:
                history[0] = remaining
            sets += ["remaining_history = :h", "slot_open = :t"]
            values |= {":h": history, ":t": True}
        lp = current.last_processed_at
        if last_processed_at is not None and (lp is None or last_processed_at > lp):
            lp = last_processed_at
            sets.append("last_processed_at = :lp")
            values[":lp"] = lp
        if sets:
            rev = int(row.get("rev", 0))
            self._t.update_item(
                Key={"pk": f"MODULE#{slug}", "sk": "PROGRESS"},
                UpdateExpression="SET " + ", ".join([*sets, "rev = :next"]),
                ConditionExpression="attribute_not_exists(rev) OR rev = :rev",
                ExpressionAttributeValues={**values, ":rev": rev, ":next": rev + 1},
            )
        return Progress(lp, tuple(history), current.last_dispatched_at)

    def reset_progress(self, slug: str, last_processed_at: str | None) -> Progress:
        """An administrator sets the cursor to any value, earlier included, or clears it."""
        if last_processed_at is None:
            self._t.update_item(
                Key={"pk": f"MODULE#{slug}", "sk": "PROGRESS"},
                UpdateExpression="REMOVE last_processed_at ADD rev :one",
                ExpressionAttributeValues={":one": 1},
            )
        else:
            self._t.update_item(
                Key={"pk": f"MODULE#{slug}", "sk": "PROGRESS"},
                UpdateExpression="SET last_processed_at = :lp ADD rev :one",
                ExpressionAttributeValues={":lp": last_processed_at, ":one": 1},
            )
        return self.progress(slug)

    def claim_dispatch(self, slug: str, now_iso: str, not_before_iso: str) -> bool:
        """Atomically stamp ``last_dispatched_at`` if the interval has elapsed (prevents double dispatch)."""
        try:
            self._t.update_item(
                Key={"pk": f"MODULE#{slug}", "sk": "PROGRESS"},
                UpdateExpression="SET last_dispatched_at = :n",
                ConditionExpression="attribute_not_exists(last_dispatched_at) OR last_dispatched_at <= :nb",
                ExpressionAttributeValues={":n": now_iso, ":nb": not_before_iso},
            )
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release_dispatch(self, slug: str, claimed_iso: str, previous_iso: str | None) -> None:
        """Undo a claim whose dispatch was refused (already running, budget closed), so the module is tried
        again at the next sweep instead of a whole interval later. Only this sweep's own stamp is undone."""
        try:
            if previous_iso is None:
                self._t.update_item(
                    Key={"pk": f"MODULE#{slug}", "sk": "PROGRESS"},
                    UpdateExpression="REMOVE last_dispatched_at",
                    ConditionExpression="last_dispatched_at = :n",
                    ExpressionAttributeValues={":n": claimed_iso},
                )
            else:
                self._t.update_item(
                    Key={"pk": f"MODULE#{slug}", "sk": "PROGRESS"},
                    UpdateExpression="SET last_dispatched_at = :p",
                    ConditionExpression="last_dispatched_at = :n",
                    ExpressionAttributeValues={":n": claimed_iso, ":p": previous_iso},
                )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    # ---- runs ----------------------------------------------------------------------

    def new_run_id(self) -> str:
        return self._ids.uuid7()

    def finish_run(self, done: ModuleRun, previous: RunStatus) -> bool:
        """Write a terminal run only if it is still ``previous``: of two finalizes racing (a Step
        Functions retry), exactly one wins and charges the run."""
        try:
            self.put_run(done, condition=previous)
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def mark_running(self, started: ModuleRun) -> bool:
        """Record the launched run as RUNNING only if it is still QUEUED: an execution that fails at once
        can be finalized before the launcher returns, and its terminal record must win."""
        return self.finish_run(started, RunStatus.QUEUED)

    def put_run(self, run: ModuleRun, condition: RunStatus | None = None) -> ModuleRun:
        item = {
            "pk": f"MODULE#{run.module}",
            "sk": f"RUN#{run.run_id}",
            "gsi1pk": "RUNS",
            "gsi1sk": f"{run.started_at}#{run.module}",
            "kind": "run",
            **{k: v for k, v in run.to_public().items() if v is not None},
            "ttl": int(parse_iso(run.started_at).timestamp()) + RUN_RETENTION_DAYS * 86400,
        }
        if condition is None:
            self._t.put_item(Item=_to_ddb(item))
        else:
            self._t.put_item(
                Item=_to_ddb(item),
                ConditionExpression="#s = :prev",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":prev": condition.value},
            )
        return run

    @staticmethod
    def _run_from_item(raw: dict[str, Any]) -> ModuleRun:
        i = _from_ddb(raw)
        return ModuleRun(
            module=i["module"],
            run_id=i["run_id"],
            status=RunStatus(i["status"]),
            trigger=i.get("trigger", ""),
            started_at=i["started_at"],
            requested_by=i.get("requested_by", ""),
            finished_at=i.get("finished_at"),
            input_tokens=int(i.get("input_tokens", 0)),
            output_tokens=int(i.get("output_tokens", 0)),
            cache_read_tokens=int(i.get("cache_read_tokens", 0)),
            cache_write_tokens=int(i.get("cache_write_tokens", 0)),
            weighted_tokens=int(i.get("weighted_tokens", 0)),
            nodes_written=int(i.get("nodes_written", 0)),
            budget_hold=int(i.get("budget_hold", 0)),
            hold_day=i.get("hold_day"),
            summary=i.get("summary", ""),
            error=i.get("error"),
            execution_arn=i.get("execution_arn"),
            target_id=i.get("target_id"),
            tier=i.get("tier"),
        )

    def get_run(self, slug: str, run_id: str) -> ModuleRun:
        item = self._t.get_item(Key={"pk": f"MODULE#{slug}", "sk": f"RUN#{run_id}"}, ConsistentRead=True).get("Item")
        if not item:
            raise NotFoundError(f"run {run_id} not found")
        return self._run_from_item(item)

    def list_runs(
        self, slug: str | None = None, limit: int = 25, cursor: str | None = None
    ) -> tuple[list[ModuleRun], str | None]:
        kwargs: dict[str, Any] = {"Limit": max(1, min(limit, 100)), "ScanIndexForward": False}
        if slug:
            kwargs["KeyConditionExpression"] = Key("pk").eq(f"MODULE#{slug}") & Key("sk").begins_with("RUN#")
            start = decode_cursor(cursor, TABLE_KEYS, ("pk", f"MODULE#{slug}"), ("sk", "RUN#"))
        else:
            kwargs["IndexName"] = "gsi1"
            kwargs["KeyConditionExpression"] = Key("gsi1pk").eq("RUNS")
            start = decode_cursor(cursor, INDEX_KEYS, ("gsi1pk", "RUNS"))
        if start:
            kwargs["ExclusiveStartKey"] = start
        res = self._t.query(**kwargs)
        return [self._run_from_item(i) for i in res.get("Items", [])], encode_cursor(res.get("LastEvaluatedKey"))

    def live_runs(self, started_after: str) -> int:
        """Non-terminal runs of every module started at or after ``started_after`` (the run-token window),
        for the deployment-wide run cap."""
        kwargs: dict[str, Any] = {
            "IndexName": "gsi1",
            "KeyConditionExpression": Key("gsi1pk").eq("RUNS") & Key("gsi1sk").gte(started_after),
        }
        return sum(1 for i in Query.all_items(self._t, **kwargs) if not self._run_from_item(i).status.terminal)

    def running_count(self, slug: str, started_after: str = "") -> int:
        """Non-terminal runs that are still live. Runs that started before ``started_after`` are
        ignored: their run token has expired, so they can no longer act and must not hold a slot.

        Read strongly consistent and keyed on the token window rather than "the latest 20 runs": a
        long-running run followed by 20 quick failures fell out of that window, so a second run
        started despite max_concurrency 1, and two near-simultaneous starts could both see zero.
        """
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(f"MODULE#{slug}")
            & Key("sk").between(f"RUN#{_run_floor(started_after)}", "RUN#~"),
            "ConsistentRead": True,
        }
        runs = [self._run_from_item(i) for i in Query.all_items(self._t, **kwargs)]
        return sum(1 for r in runs if not r.status.terminal and r.started_at >= started_after)

    # ---- budgets -------------------------------------------------------------------

    def _day(self) -> str:
        return self._clock.now().strftime("%Y-%m-%d")

    def budget(self, slug: str, limit: int, day: str | None = None) -> BudgetSnapshot:
        """Today's ledger, or a named day's (a run that spans UTC midnight reads the day its hold is
        on, not the new one)."""
        res = self._t.get_item(Key={"pk": f"BUDGET#{slug}", "sk": f"DAY#{day or self._day()}"}, ConsistentRead=True)
        consumed = int((res.get("Item") or {}).get("weighted", Decimal(0)))
        return BudgetSnapshot(consumed, limit)

    def charge(self, slug: str, usage: TokenUsage, weights: TokenWeights | None = None) -> int:
        weighted = (weights or TokenWeights()).weigh(usage)
        if weighted <= 0:
            return 0
        res = self._t.update_item(
            Key={"pk": f"BUDGET#{slug}", "sk": f"DAY#{self._day()}"},
            UpdateExpression="ADD weighted :w, input_tokens :i, output_tokens :o, cache_read_tokens :cr, "
            "cache_write_tokens :cw SET #ttl = if_not_exists(#ttl, :ttl)",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":ttl": int(self._clock.now().timestamp()) + BUDGET_RETENTION_DAYS * 86400,
                ":w": weighted,
                ":i": usage.input_tokens,
                ":o": usage.output_tokens,
                ":cr": usage.cache_read_tokens,
                ":cw": usage.cache_write_tokens,
            },
            ReturnValues="UPDATED_NEW",
        )
        return int(res["Attributes"]["weighted"])

    def charge_weighted(self, slug: str, weighted: int) -> int:
        """Charge weighted tokens that are not model tokens (an embedding, a graph fan-out)."""
        res = self._t.update_item(
            Key={"pk": f"BUDGET#{slug}", "sk": f"DAY#{self._day()}"},
            UpdateExpression="ADD weighted :w, tool_calls :one SET #ttl = if_not_exists(#ttl, :ttl)",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":w": weighted,
                ":one": 1,
                ":ttl": int(self._clock.now().timestamp()) + BUDGET_RETENTION_DAYS * 86400,
            },
            ReturnValues="UPDATED_NEW",
        )
        return int(res["Attributes"]["weighted"])

    def reserve(self, slug: str, amount: int, limit: int) -> str | None:
        """Atomically hold ``amount`` weighted tokens of today's budget if that keeps it within ``limit``.

        Returns the ledger day the hold was written to (so it is released there), or None if refused.
        """
        if amount <= 0 or amount > limit:
            return None
        day = self._day()
        try:
            self._t.update_item(
                Key={"pk": f"BUDGET#{slug}", "sk": f"DAY#{day}"},
                UpdateExpression="ADD weighted :a SET #ttl = if_not_exists(#ttl, :ttl)",
                ConditionExpression="attribute_not_exists(weighted) OR weighted <= :max",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":a": amount,
                    ":max": limit - amount,
                    ":ttl": int(self._clock.now().timestamp()) + BUDGET_RETENTION_DAYS * 86400,
                },
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return day

    def release(self, slug: str, amount: int, day: str) -> None:
        """Give back a hold made by ``reserve`` on ``day``."""
        self._t.update_item(
            Key={"pk": f"BUDGET#{slug}", "sk": f"DAY#{day}"},
            UpdateExpression="ADD weighted :a",
            ExpressionAttributeValues={":a": -amount},
        )

    # ---- kill switch ----------------------------------------------------------------

    # The kill switch is read on the unauthenticated /api/config route, so anonymous traffic used to
    # amplify one request into one consistent DynamoDB read. Cached per container for a few seconds:
    # a run's next tool call is refused at most that late.
    ANDON_CACHE_SECONDS = 5

    def andon(self, cached: bool = False) -> AndonState:
        """Fail closed: an unreadable state reports GenAI as disabled; an absent row means enabled."""
        now = self._clock.now().timestamp()
        if cached and self._andon_cache is not None and now - self._andon_cache[0] <= self.ANDON_CACHE_SECONDS:
            return self._andon_cache[1]
        state = self._read_andon()
        self._andon_cache = (now, state)
        return state

    def _read_andon(self) -> AndonState:
        try:
            res = self._t.get_item(Key={"pk": "ANDON", "sk": "STATE"}, ConsistentRead=True)
        except ClientError:
            return AndonState(False, "kill switch state unreadable", None, None)
        item = res.get("Item")
        if not item:
            return AndonState(True, None, None, None)
        return AndonState(
            bool(item.get("genai_enabled", False)), item.get("reason"), item.get("updated_by"), item.get("updated_at")
        )

    def set_andon(self, enabled: bool, reason: str | None, actor: str) -> AndonState:
        """Writes invalidate this container's cache; other containers see it within ANDON_CACHE_SECONDS."""
        if not isinstance(enabled, bool):
            raise ValidationError("genai_enabled must be true or false")
        if reason is not None and len(reason) > 500:
            raise ValidationError("reason must be at most 500 characters")
        now = iso(self._clock.now())
        item: dict[str, Any] = {
            "pk": "ANDON",
            "sk": "STATE",
            "genai_enabled": enabled,
            "updated_by": actor,
            "updated_at": now,
        }
        if reason:
            item["reason"] = reason
        self._t.put_item(Item=item)
        state = AndonState(enabled, reason, actor, now)
        self._andon_cache = (self._clock.now().timestamp(), state)
        return state
