# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Seed bundles: a deployment's starting configuration as data.

A bundle sets up a deployment for one use without code changes: what it is called (the profile), how
each module behaves (instructions, prompt mode, fields, schedule, budget, on or off), extra custom modules,
and the public sources the Scout reads. It is applied on every deploy and is idempotent:

- a module or the profile is changed by the bundle only while nobody has edited it since the bundle last
  wrote it (the version it left is remembered), so an administrator's edits always win;
- sources are added when the module has none at that address, never removed or overwritten; a source the
  bundle added and later lists under ``retired_sources`` is turned off once (not deleted), so a bundle can
  replace a source that stopped working, and an administrator who turns it back on keeps it on;
- nothing is ever deleted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError

from insights_radar.core.errors import ConflictError, DomainError, NotFoundError, ValidationError
from insights_radar.graph.registry import NodeType, Registry
from insights_radar.modules.attributes import parse_schema
from insights_radar.modules.model import ModuleDefinition, ModuleTemplate, SourceType, ToolGrant
from insights_radar.modules.repository import ModuleRepository
from insights_radar.modules.templates import builtin_modules
from insights_radar.profile.model import parse_profile
from insights_radar.profile.repository import READ_ERRORS, ProfileRepository

BUNDLE_VERSION = 1
MAX_MODULES = 30
MAX_SOURCES = 500
SEED_ACTOR = "system:seed"
_STATE_KEY = {"pk": "SEED", "sk": "STATE"}

# What a bundle may set on a module. Grants, MCP servers and product links are not here on purpose: what a
# module may do is the template's and an administrator's decision, not a file's.
MODULE_FIELDS = {
    "name",
    "description",
    "instructions",
    "prompt_mode",
    "attribute_schema",
    "input_types",
    "interval_minutes",
    "daily_token_budget",
    "enabled",
    "insight_label",
}
CUSTOM_FIELDS = MODULE_FIELDS | {"slug", "insight_kind", "grants", "model_key"}
SOURCE_FIELDS = {"module", "name", "type", "address", "instruction", "enabled"}


@dataclass(frozen=True, slots=True)
class Bundle:
    profile: dict[str, Any] | None = None
    modules: tuple[dict[str, Any], ...] = ()
    custom_modules: tuple[dict[str, Any], ...] = ()
    sources: tuple[dict[str, Any], ...] = ()
    retired_sources: tuple[dict[str, Any], ...] = ()
    digest: str = ""


@dataclass(slots=True)
class SeedResult:
    profile: str = "unchanged"
    modules: dict[str, str] = field(default_factory=dict)
    sources_added: int = 0
    sources_skipped: int = 0
    sources_retired: int = 0
    errors: list[str] = field(default_factory=list)
    # Every step ran (with or without per-item errors); False when one raised.
    complete: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "modules": dict(sorted(self.modules.items())),
            "sources_added": self.sources_added,
            "sources_skipped": self.sources_skipped,
            "sources_retired": self.sources_retired,
            "errors": self.errors,
        }


def _module_changes(raw: dict[str, Any], where: str, errors: list[str], allowed: set[str]) -> dict[str, Any]:
    unknown = set(raw) - allowed
    if unknown:
        errors.append(f"{where} has fields a bundle may not set: {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for key in sorted((set(raw) & allowed) - {"slug"}):
        value = raw[key]
        if key == "attribute_schema":
            try:
                out[key] = parse_schema(value)
            except ValidationError as err:
                errors.extend(f"{where}.{e}" for e in err.details.get("errors", [err.message]))
        elif key == "input_types":
            try:
                out[key] = tuple(Registry.node_type(t) for t in value)
            except (TypeError, ValueError) as err:
                errors.append(f"{where}.input_types: {err}")
        elif key == "grants":
            if not isinstance(value, list) or any(v not in ToolGrant._value2member_map_ for v in value):
                errors.append(f"{where}.grants holds an unknown grant")
            else:
                out[key] = frozenset(ToolGrant(v) for v in value)
        else:
            want = _FIELD_TYPES.get(key, str)
            # bool is an int in Python: "interval_minutes": true must not pass as 1.
            if not isinstance(value, want) or (want is int and isinstance(value, bool)):
                errors.append(f"{where}.{key} must be {_TYPE_WORDS[want]}")
            else:
                out[key] = value
    return out


_BUILTINS = {m.slug: m for m in builtin_modules()}


def _wire(key: str, value: Any) -> Any:
    """A parsed bundle value in the form ``_field_value`` reads it back from a module."""
    if key == "attribute_schema":
        return value.to_public()
    if key == "input_types":
        return [t.value for t in value]
    return value


def _field_value(module: ModuleDefinition, key: str) -> Any:
    value = getattr(module, key)
    if key == "attribute_schema":
        return value.to_public()
    if key == "input_types":
        return [t.value for t in value]
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _fingerprints(module: ModuleDefinition, keys: list[str]) -> dict[str, str]:
    """A digest per field, so a later deploy can tell which of them changed since the bundle wrote them."""
    return {key: _hash(_field_value(module, key)) for key in keys}


# Each settable field's JSON type; the rest are text. Checked here because the module validator assumes
# the API's body parser already did it: "enabled": "false" would otherwise switch a module on.
_FIELD_TYPES: dict[str, type] = {
    "enabled": bool,
    "interval_minutes": int,
    "daily_token_budget": int,
}
_TYPE_WORDS: dict[type, str] = {bool: "true or false", int: "a whole number", str: "text"}


def parse_bundle(raw: Any) -> Bundle:
    """Validate a bundle document; every problem is reported at once."""
    if not isinstance(raw, dict):
        raise ValidationError("a seed bundle must be a JSON object")
    errors: list[str] = []
    unknown = set(raw) - {
        "version",
        "profile",
        "modules",
        "custom_modules",
        "sources",
        "retired_sources",
        "description",
    }
    if unknown:
        errors.append(f"unknown fields: {', '.join(sorted(unknown))}")
    if raw.get("version") != BUNDLE_VERSION:
        errors.append(f"version must be {BUNDLE_VERSION}")
    profile = raw.get("profile")
    if profile is not None:
        try:
            parse_profile(profile)
        except ValidationError as err:
            errors.extend(f"profile: {e}" for e in err.details.get("errors", [err.message]))
    lists: dict[str, list[Any]] = {}
    for name, cap in (
        ("modules", MAX_MODULES),
        ("custom_modules", MAX_MODULES),
        ("sources", MAX_SOURCES),
        ("retired_sources", MAX_SOURCES),
    ):
        value = raw.get(name, [])
        if not isinstance(value, list) or len(value) > cap or not all(isinstance(v, dict) for v in value):
            errors.append(f"{name} must be a list of at most {cap} objects")
            value = []
        lists[name] = value
    for i, m in enumerate(lists["modules"]):
        if not isinstance(m.get("slug"), str):
            errors.append(f"modules[{i}] needs a slug")
            continue
        _module_changes(m, f"modules[{i}]", errors, MODULE_FIELDS | {"slug"})
    for i, m in enumerate(lists["custom_modules"]):
        for need in ("slug", "name", "insight_kind"):
            if not isinstance(m.get(need), str):
                errors.append(f"custom_modules[{i}] needs {need}")
        _module_changes(m, f"custom_modules[{i}]", errors, CUSTOM_FIELDS)
    for i, s in enumerate(lists["sources"]):
        unknown = set(s) - SOURCE_FIELDS
        if unknown:
            errors.append(f"sources[{i}] has unknown fields: {', '.join(sorted(unknown))}")
        for need in ("module", "name", "type", "address"):
            if not isinstance(s.get(need), str):
                errors.append(f"sources[{i}] needs {need}")
        if "enabled" in s and not isinstance(s["enabled"], bool):
            errors.append(f"sources[{i}].enabled must be true or false")
        if "instruction" in s and not isinstance(s["instruction"], str):
            errors.append(f"sources[{i}].instruction must be text")
        if isinstance(s.get("type"), str) and s["type"] not in SourceType._value2member_map_:
            errors.append(f"sources[{i}].type must be one of: {', '.join(t.value for t in SourceType)}")
    for i, s in enumerate(lists["retired_sources"]):
        if set(s) != {"module", "address"} or not all(isinstance(s[k], str) for k in s):
            errors.append(f"retired_sources[{i}] needs exactly a module and an address")
    if errors:
        raise ValidationError("the seed bundle is not valid", {"errors": errors})
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return Bundle(
        profile=profile,
        modules=tuple(lists["modules"]),
        custom_modules=tuple(lists["custom_modules"]),
        sources=tuple(lists["sources"]),
        retired_sources=tuple(lists["retired_sources"]),
        digest=digest,
    )


class Seeder:
    def __init__(self, ops_table: Any, modules: ModuleRepository, profile: ProfileRepository) -> None:
        self._t = ops_table
        self._modules = modules
        self._profile = profile

    def _state(self) -> dict[str, Any]:
        item = self._t.get_item(Key=_STATE_KEY, ConsistentRead=True).get("Item") or {}
        return {
            "digest": item.get("digest", ""),
            "profile_version": int(item.get("profile_version", -1)),
            # Per module, a digest of each field the bundle wrote (an older state held one value per module:
            # it is dropped, and such a module is compared with its built-in values as if never seeded).
            "modules": {k: dict(v) for k, v in (item.get("modules") or {}).items() if isinstance(v, dict)},
            # Custom modules the bundle created, and source addresses it added: an administrator who deletes
            # one does not get it back from the next release of the bundle.
            "created": list(item.get("created") or []),
            "sources": list(item.get("sources") or []),
            # Sources the bundle turned off: once only, so turning one back on is an administrator's choice.
            "retired": list(item.get("retired") or []),
            # Addresses an administrator already had when the bundle listed them.
            "existing": list(item.get("existing") or []),
        }

    def apply(self, bundle: Bundle) -> SeedResult:
        state = self._state()
        result = SeedResult()
        if state["digest"] == bundle.digest:
            # Applied in full before and unchanged since: writing it again would only bump versions, and an
            # administrator with an editor open would then get a conflict on save.
            result.profile = "unchanged"
            return result
        try:
            self._apply(bundle, state, result)
        finally:
            # What was written is recorded even when a later step fails, or the next deploy would read the
            # bundle's own writes as an administrator's edits and never update them again. The digest only
            # marks a bundle applied in full, so a failed one is tried again.
            self._t.put_item(
                Item={
                    **_STATE_KEY,
                    # Applied in full, per-item errors included (they are reported, and retrying them
                    # unchanged would only bump versions on every deploy): marked done.
                    "digest": bundle.digest if result.complete else "",
                    "profile_version": state["profile_version"],
                    "modules": state["modules"],
                    "created": state["created"],
                    "sources": state["sources"],
                    "retired": state["retired"],
                    "existing": state["existing"],
                }
            )
        return result

    def _apply(self, bundle: Bundle, state: dict[str, Any], result: SeedResult) -> None:
        # A profile save that failed for a passing reason leaves the bundle not applied in full, so the next
        # deploy tries it again (code review r23).
        transient = bundle.profile is not None and self._apply_profile(bundle.profile, state, result)
        for raw in bundle.custom_modules:
            self._create_custom(raw, state, result)
        for raw in bundle.modules:
            self._override(raw, state, result)
        for raw in bundle.sources:
            self._add_source(raw, state, result)
        for raw in bundle.retired_sources:
            self._retire_source(raw, state, result)
        result.complete = not transient

    def _apply_profile(self, profile: dict[str, Any], state: dict[str, Any], result: SeedResult) -> bool:
        """Apply the bundle's profile; True when a read or the save failed. Any failure, passing or permanent, leaves
        the bundle to be tried again on the next deploy, and the rest of it still applies now: nothing about the
        profile was written, so a retry costs no version, and once an operator fixes the cause (a permission, a
        table) the same release applies it (code reviews r21-r27). A permanent cause is reported on every deploy
        until it is fixed, which is the point."""
        # The ownership decision needs a real read: the defaults a failed get() returns look like "never written"
        # (code review r24). put() reads again and refuses the write when the item changed in between.
        try:
            current = self._profile.get_for_write()
        except READ_ERRORS as err:
            _record_profile_error(err, result)
            return True
        if (
            current.version
            and not current.stored_invalid
            # What this code knows only: kept unknown fields would make the two differ forever (code review r19).
            and current.to_known_item() == parse_profile(profile).to_known_item()
        ):
            # Already what the bundle says (a new release changed something else): no new version.
            state["profile_version"] = current.version
            result.profile = "unchanged"
            return False
        # The bundle may set it when it wrote it last, or nobody did. By the recorded version, or by the writer: a
        # save whose reply was lost still counts as the seed's (code review r23). An item an administrator wrote
        # is kept, even when its version is unreadable.
        seed_owned = (
            current.updated_by == SEED_ACTOR
            or (current.version != 0 and current.version == state["profile_version"])
            or (current.version == 0 and current.updated_by is None)
        )
        if not seed_owned:
            result.profile = "kept (edited by an administrator)"
            return False
        try:
            saved = self._profile.put(profile, SEED_ACTOR, expected_version=current.version)
        except ConflictError as err:
            result.errors.append(f"profile: {err.message}")
            return False
        except READ_ERRORS as err:
            _record_profile_error(err, result)
            return True
        state["profile_version"] = saved.version
        result.profile = "applied"
        return False

    def _override(self, raw: dict[str, Any], state: dict[str, Any], result: SeedResult) -> None:
        slug = raw["slug"]
        try:
            current = self._modules.get(slug)
        except NotFoundError:
            result.errors.append(f"module {slug} does not exist")
            return
        changes = _module_changes(raw, slug, [], MODULE_FIELDS)
        if not changes:
            return
        # Compared field by field, not by version: the deploy's own migrations of built-in modules raise
        # versions too, and must not read as an administrator's edit. A field is the bundle's to change if it
        # still holds what the bundle last wrote there, or, a field the bundle never wrote, the built-in value
        # (a module the bundle created has every field recorded, set or defaulted, when it was created).
        seeded: dict[str, str] = state["modules"].get(slug, {})
        builtin = _BUILTINS.get(slug)
        now = _fingerprints(current, sorted(changes))
        for key, digest in now.items():
            if key in seeded:
                ours = seeded[key] == digest
            elif builtin is not None:
                ours = _hash(_field_value(builtin, key)) == digest
            else:
                ours = False  # a custom module the bundle did not create: whatever it holds is an admin's
            # An administrator's edit that the bundle now agrees with (a budget raised by hand, then in
            # the bundle) is no reason to keep the bundle's other fields away from the module.
            ours = ours or digest == _hash(_wire(key, changes[key]))
            if not ours:
                result.modules[slug] = "kept (edited by an administrator)"
                return
        wanted = {k: v for k, v in changes.items() if _hash(_field_value(current, k)) != _hash(_wire(k, v))}
        if not wanted:
            # Already as the bundle says: writing it again would only raise the version under an open editor.
            state["modules"][slug] = {**seeded, **now}
            result.modules[slug] = "unchanged"
            return
        try:
            updated = self._modules.update(slug, wanted, current.version)
        except (ValidationError, ConflictError) as err:
            result.errors.append(f"module {slug}: {err.message} {err.details or ''}".strip())
            return
        state["modules"][slug] = {**seeded, **_fingerprints(updated, sorted(changes))}
        result.modules[slug] = "applied"

    def _create_custom(self, raw: dict[str, Any], state: dict[str, Any], result: SeedResult) -> None:
        slug = raw["slug"]
        try:
            self._modules.get(slug)
        except NotFoundError:
            changes = _module_changes(raw, slug, [], CUSTOM_FIELDS)
            grants = changes.pop("grants", frozenset({ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT}))
            m = ModuleDefinition(
                slug=slug,
                name=changes.pop("name"),
                template=ModuleTemplate.CUSTOM,
                output_kind=NodeType.INSIGHT,
                grants=grants,
                **changes,
            )
            try:
                created = self._modules.create(m, SEED_ACTOR)
            except (ValidationError, ConflictError) as err:
                result.errors.append(f"custom module {slug}: {err.message} {err.details or ''}".strip())
                return
            # Every field, set or defaulted: a later bundle may set one only while it still holds this value.
            state["modules"][slug] = _fingerprints(created, sorted(MODULE_FIELDS))
            state["created"].append(slug)
            result.modules[slug] = "created"
            return
        # It exists: treat the rest like an override of an existing module.
        self._override({k: v for k, v in raw.items() if k in MODULE_FIELDS | {"slug"}}, state, result)

    def _retire_source(self, raw: dict[str, Any], state: dict[str, Any], result: SeedResult) -> None:
        key = f"{raw['module']} {raw['address']}"
        # Only a source this bundle added, and only once: one an administrator added at the same address,
        # or turned back on after a retirement, is theirs. (A seed before round 11 recorded a conflict as its own
        # source; sources carry no creator, so such an address is treated as the bundle's: list it under
        # retired_sources only when the bundle really added it.)
        if key not in state["sources"] or key in state["retired"]:
            return
        failed = False
        for source in self._modules.list_sources(raw["module"]):  # a deleted module lists none
            if source.address == raw["address"] and source.enabled:
                try:
                    self._modules.disable_source(raw["module"], source.id)
                except DomainError as err:  # one source deleted or being saved: reported, the rest of the seed goes on
                    result.errors.append(f"retiring {raw['address']!r} in {raw['module']}: {err.message}")
                    failed = True
                    continue
                result.sources_retired += 1
        if not failed:
            state["retired"].append(key)  # after the update: one that failed is tried again

    def _add_source(self, raw: dict[str, Any], state: dict[str, Any], result: SeedResult) -> None:
        key = f"{raw['module']} {raw['address']}"
        if key in state["sources"] or key in state["existing"]:
            result.sources_skipped += 1  # added by an earlier run (kept, edited or deleted: theirs now), or theirs
            return
        try:
            self._modules.add_source(
                raw["module"],
                raw["name"],
                SourceType(raw["type"]),
                raw["address"],
                instruction=raw.get("instruction", ""),
                enabled=raw.get("enabled", True),
            )
            state["sources"].append(key)
            result.sources_added += 1
        except ConflictError:
            # An administrator's own source at that address: left alone, and not the bundle's, so a later
            # retired_sources entry never turns it off.
            state["existing"].append(key)
            result.sources_skipped += 1
        except (ValidationError, NotFoundError) as err:
            result.errors.append(
                f"source {raw['name']!r} for {raw['module']}: {err.message} {err.details or ''}".strip()
            )


def _record_profile_error(err: Exception, result: SeedResult) -> None:
    """Report a failed profile read or save by its error code, or its type when it has none."""
    if isinstance(err, ClientError):
        result.errors.append(f"profile: {err.response.get('Error', {}).get('Code') or 'error'}")
    else:
        result.errors.append(f"profile: {type(err).__name__}")
