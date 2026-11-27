# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Radar application service: CRUD, versions, import/export, ACL, and curator suggestions.

Storage: radar metadata, version metadata and suggestions live in the ops table; each version's
entry list is a JSON object in S3 (``radars/<slug>/v000123.json``) so large radars never hit the
400 KB DynamoDB item limit. Saving is optimistic: the caller passes the version it edited and the
save fails with 409 if someone saved in between (the internal radar's sec. 14.3 ETag rule).

Suggestions: at most one open suggestion per entry key. Autopilot applies a suggestion once it is
older than the radar's ``auto_apply_days`` **and** the entry is unchanged since the suggestion was
made; if a human edited the entry in the meantime the suggestion is superseded (sec. 12.10).
"""

from __future__ import annotations

import collections
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, SystemClock, iso, parse_iso
from insights_radar.core.dynamo import Query
from insights_radar.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from insights_radar.core.ids import IdGenerator
from insights_radar.core.names import NameNormalizer
from insights_radar.core.urls import UrlGuard
from insights_radar.graph.repository import TABLE_KEYS, _from_ddb, _to_ddb, decode_cursor, encode_cursor
from insights_radar.identity.principal import Principal, Role
from insights_radar.identity.users import EMAIL_RE
from insights_radar.radars.codecs import EntryCodec, RadarDiff
from insights_radar.radars.model import (
    MAX_ENTRIES,
    TEMPLATES,
    EntryValidator,
    Radar,
    RadarEntry,
    RadarMode,
    RadarSchema,
)

log = logging.getLogger(__name__)
# Called with the ids a proposal cites; answers with the usable ones mapped to the site they came
# from, so autopilot can require real evidence from more than one source.
EvidenceCheck = Callable[[list[str]], dict[str, frozenset[str]]]

MAX_RADARS = 200
MAX_RADARS_PER_OWNER = 25
ADMIN_HEADROOM = 20
# An owner who is not an administrator may delete a radar with at most this many versions.
OWNER_DELETE_MAX_VERSIONS = 10
MAX_OPEN_SUGGESTIONS = 200  # per radar; a curator proposing without end would bloat the partition
DISCARD_MEMORY_DAYS = 30  # how long a discarded suggestion blocks the same proposal (CTR H-024)
MAX_VERSIONS_LISTED = 200
MAX_AUTOPILOT_APPLIES = 10  # per radar per tick: a backlog must not time the whole sweep out
MIN_EVIDENCE_DOMAINS = 2  # an unattended apply needs corroboration from more than one site
# A change removing more than half of the entries needs allow_empty, once it removes at least this many:
# tidying a three-entry radar by hand is not a wipe.
SHRINK_MIN_REMOVED = 3
# A version claim older than this outlived any save (the API Lambda times out at 30 s): an orphan.
ORPHAN_CLAIM_SECONDS = 120


class BlobStore(Protocol):
    def put(self, key: str, body: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete_prefix(self, prefix: str) -> None: ...


class AuditSink(Protocol):
    def record_actor(
        self, actor: str, email: str, action: str, target: str, details: dict[str, Any] | None = None
    ) -> None: ...


class S3BlobStore:
    def __init__(self, s3: Any, bucket: str) -> None:
        self._s3 = s3
        self._bucket = bucket

    def put(self, key: str, body: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType="application/json")

    def get(self, key: str) -> bytes:
        return bytes(self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read())

    def delete_prefix(self, prefix: str) -> None:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True})


class MemoryBlobStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, body: bytes) -> None:
        self.objects[key] = body

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def delete_prefix(self, prefix: str) -> None:
        for k in [k for k in self.objects if k.startswith(prefix)]:
            del self.objects[k]


_ENTRY_TEXT = ("key", "title", "sector", "ring")
_ENTRY_OPTIONAL = ("marker", "node_id", "link", "sub_sector")


# Words that do not tell two entry names apart: joining words, vendor prefixes and kind words (CTR HC-097).
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
# An entry's history reads at most this many of the radar's newest versions.
HISTORY_VERSIONS = 50
# A cross-radar search reads every radar's current version, up to this many radars.
MAX_RADARS_SEARCHED = 200

_FILLER_WORDS = frozenset(
    {"a", "an", "and", "by", "for", "in", "of", "on", "the", "to", "via", "with", "using", "&"}
    | {"amazon", "aws", "google", "azure", "microsoft"}
    | {"sdk", "framework", "library", "tool", "tools", "platform", "service"}
)
_WORD = re.compile(r"[^\W_]+")


def _significant_words(title: str) -> frozenset[str]:
    """The words of a title that tell it apart, singular: "Knowledge Bases" and "knowledge base" agree."""
    out = set()
    for w in _WORD.findall(NameNormalizer.title_key(title)):
        if w in _FILLER_WORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return frozenset(out)


def _near_duplicate(a: frozenset[str], b: frozenset[str]) -> bool:
    """Two titles name the same entry: the same significant words; every word of the shorter one, when it
    has two or more ("Strands Agents" and "Strands Agents Evaluation"); or two or more shared words and a
    Jaccard overlap of at least a half. One shared word is never enough: "Bedrock Guardrails" and
    "Bedrock AgentCore" are two entries (CTR HC-097)."""
    if not a or not b:
        return False
    shared = len(a & b)
    if a == b:
        return True
    if min(len(a), len(b)) >= 2 and shared == min(len(a), len(b)):
        return True
    return shared >= 2 and shared / len(a | b) >= 0.5


def _remember(radar: Radar, schema: RadarSchema, renames: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Every old name a schema edit renamed, keyed by title key, to the name it became; a name declared
    again in the new schema is no longer an old name (CTR HA-044)."""
    out: dict[str, dict[str, str]] = {dim: dict(v) for dim, v in (radar.extra.get("renamed") or {}).items()}
    for dim, pairs in renames.items():
        out.setdefault(dim, {}).update({NameNormalizer.title_key(old): new for old, new in pairs.items()})
    return {
        dim: {old: new for old, new in pairs.items() if schema.match(dim, old) is None}
        for dim, pairs in out.items()
        if any(schema.match(dim, old) is None for old in pairs)
    }


def _repeated_keys(entries: list[RadarEntry]) -> list[str]:
    counts = Counter(e.key for e in entries)
    return sorted(k for k, n in counts.items() if n > 1)


def _entry_from(raw: dict[str, Any]) -> RadarEntry:
    """Build an entry from untrusted fields (a proposal, reviewer edits), rejecting non-string values."""
    for name in _ENTRY_TEXT:
        if not isinstance(raw.get(name), str):
            raise ValidationError(f"{name} must be a string")
    for name in _ENTRY_OPTIONAL:
        if raw.get(name) is not None and not isinstance(raw[name], str):
            raise ValidationError(f"{name} must be a string")
    if not isinstance(raw.get("description", ""), str):
        raise ValidationError("description must be a string")
    return RadarEntry(
        key=raw["key"],
        title=raw["title"],
        sector=raw["sector"],
        ring=raw["ring"],
        marker=raw.get("marker"),
        description=raw.get("description", ""),
        node_id=raw.get("node_id"),
        link=raw.get("link"),
        sub_sector=raw.get("sub_sector"),
    )


class RadarService:
    def __init__(
        self,
        table: Any,
        blobs: BlobStore,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._t = table
        self._blobs = blobs
        self._clock = clock or SystemClock()
        self._ids = ids or IdGenerator(self._clock)
        self._sleep = sleep

    # ---- access ------------------------------------------------------------------------

    @staticmethod
    def can_edit(principal: Principal, radar: Radar) -> bool:
        if principal.is_admin:
            return True
        if not principal.at_least(Role.EDITOR):
            return False
        return principal.subject == radar.owner or (principal.email or "").lower() in radar.editors

    @staticmethod
    def can_admin(principal: Principal, radar: Radar) -> bool:
        return principal.is_admin or (principal.at_least(Role.EDITOR) and principal.subject == radar.owner)

    def _require_edit(self, principal: Principal, radar: Radar) -> None:
        if not self.can_edit(principal, radar):
            raise ForbiddenError("you are not an editor of this radar")

    def _require_admin(self, principal: Principal, radar: Radar) -> None:
        if not self.can_admin(principal, radar):
            raise ForbiddenError("only the radar owner or an administrator can do this")

    # ---- radar metadata ------------------------------------------------------------------

    @staticmethod
    def _radar_from_item(raw: dict[str, Any]) -> Radar:
        i = _from_ddb(raw)
        return Radar(
            slug=i["slug"],
            name=i["name"],
            schema=RadarSchema.from_public(i["schema"]),
            purpose=i.get("purpose", ""),
            description=i.get("description", ""),
            mode=RadarMode(i.get("mode", "manual")),
            auto_apply_days=int(i.get("auto_apply_days", 7)),
            owner=i.get("owner", ""),
            owner_email=i.get("owner_email", ""),
            editors=tuple(i.get("editors", [])),
            current_version=int(i.get("current_version", 0)),
            entry_count=int(i.get("entry_count", 0)),
            created_at=i.get("created_at", ""),
            updated_at=i.get("updated_at", ""),
            updated_by=i.get("updated_by", ""),
            version_lock=int(i.get("version_lock", 1)),
            auto_apply_agent=bool(i.get("auto_apply_agent", False)),
            extra={"renamed": i["renamed"]} if isinstance(i.get("renamed"), dict) else {},
        )

    def _put_radar(self, r: Radar, condition: str, values: dict[str, Any] | None = None) -> None:
        item = {
            "pk": f"RADAR#{r.slug}",
            "sk": "META",
            "gsi1pk": "RADARS",
            "gsi1sk": r.slug,
            "kind": "radar",
            **{k: v for k, v in r.to_public().items() if v not in (None, [], "")},
            **({"renamed": r.extra["renamed"]} if r.extra.get("renamed") else {}),
        }
        kwargs: dict[str, Any] = {"Item": _to_ddb(item), "ConditionExpression": condition}
        if values:
            kwargs["ExpressionAttributeValues"] = values
        try:
            self._t.put_item(**kwargs)
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError("the radar was changed by someone else; reload and retry") from err
            raise

    def _replace_radar(self, updated: Radar, read: Radar) -> None:
        """Write the whole record only if neither counter moved since ``read``: entry saves bump
        ``current_version`` and settings changes bump ``version_lock``, so a check on just one let a
        rename and a save overwrite each other (and rewind the version below an existing record)."""
        self._put_radar(
            updated,
            "current_version = :c AND version_lock = :l",
            {":c": read.current_version, ":l": read.version_lock},
        )

    def list_all(self) -> list[Radar]:
        out: list[Radar] = []
        kwargs: dict[str, Any] = {"IndexName": "gsi1", "KeyConditionExpression": Key("gsi1pk").eq("RADARS")}
        while True:
            res = self._t.query(**kwargs)
            out.extend(self._radar_from_item(i) for i in res.get("Items", []))
            if "LastEvaluatedKey" not in res:
                return out
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]

    def get(self, slug: str) -> Radar:
        res = self._t.get_item(Key={"pk": f"RADAR#{slug}", "sk": "META"}, ConsistentRead=True)
        if "Item" not in res:
            raise NotFoundError(f"radar {slug} not found")
        return self._radar_from_item(res["Item"])

    def create(
        self,
        principal: Principal,
        slug: str,
        name: str,
        *,
        template: str | None = None,
        schema: RadarSchema | None = None,
        purpose: str = "",
        description: str = "",
    ) -> Radar:
        if not principal.at_least(Role.EDITOR):
            raise ForbiddenError("editors and administrators can create radars")
        if template and schema:
            raise ValidationError("pass a template or a schema, not both")
        if schema is None:
            key = template or "blank"
            if key not in TEMPLATES:
                raise ValidationError(f"unknown template {key!r}; choose one of {', '.join(TEMPLATES)}")
            default_purpose, schema = TEMPLATES[key]
            purpose = purpose or default_purpose
        radars = self.list_all()
        # Editors share the workspace cap, so one of them must not be able to use it all up: each has a
        # cap of their own, and administrators keep headroom above the shared cap to clean up.
        if principal.is_admin:
            if len(radars) >= MAX_RADARS + ADMIN_HEADROOM:
                raise ValidationError(f"at most {MAX_RADARS + ADMIN_HEADROOM} radars")
        elif len(radars) >= MAX_RADARS:
            raise ValidationError(f"at most {MAX_RADARS} radars")
        elif sum(1 for x in radars if x.owner == principal.subject) >= MAX_RADARS_PER_OWNER:
            raise ValidationError(f"you can own at most {MAX_RADARS_PER_OWNER} radars; delete one or ask an admin")
        now = iso(self._clock.now())
        radar = Radar.validate(
            Radar(
                slug=slug,
                name=name,
                schema=RadarSchema.validate(schema),
                purpose=purpose,
                description=description,
                owner=principal.subject,
                owner_email=(principal.email or "").lower(),
                created_at=now,
                updated_at=now,
                updated_by=principal.actor,
            )
        )
        try:
            self._put_radar(radar, "attribute_not_exists(pk)")
        except ConflictError as err:
            raise ConflictError(f"a radar with slug {slug!r} already exists", {"code": "slug_taken"}) from err
        return self._write_version(radar, [], principal.actor, "created", schema_changed=True)

    META_FIELDS = frozenset({"name", "purpose", "description", "mode", "auto_apply_days", "auto_apply_agent"})

    def update_meta(self, principal: Principal, slug: str, changes: dict[str, Any], expected_lock: int) -> Radar:
        bad = set(changes) - self.META_FIELDS
        if bad:
            raise ValidationError(f"fields cannot be changed here: {', '.join(sorted(bad))}")
        radar = self.get(slug)
        self._require_admin(principal, radar)
        if radar.version_lock != expected_lock:
            raise ConflictError("the radar was changed by someone else; reload and retry")
        if changes.get("auto_apply_agent") is True and not radar.auto_apply_agent and not principal.is_admin:
            raise ForbiddenError("only an administrator can let autopilot apply agent proposals without review")
        if "auto_apply_days" in changes and radar.auto_apply_agent and not principal.is_admin:
            # The review delay is the other half of the administrator's opt-in: an owner could set it
            # to one day and have unattended applies the next morning.
            raise ForbiddenError("only an administrator can change the review delay while autopilot applies proposals")
        if "mode" in changes:
            try:
                changes = {**changes, "mode": RadarMode(changes["mode"])}
            except ValueError as err:
                raise ValidationError("mode must be manual, assist, copilot or autopilot") from err
        arming = (
            changes.get("mode") is RadarMode.AUTOPILOT
            and radar.mode is not RadarMode.AUTOPILOT
            and changes.get("auto_apply_agent", radar.auto_apply_agent) is True
        )
        if arming and not principal.is_admin:
            # The opt-in can be given while the radar is in another mode: switching into autopilot is what
            # starts unattended applies, so it takes an administrator too (B3-009).
            raise ForbiddenError(
                "only an administrator can switch a radar into autopilot while it applies agent proposals"
            )
        updated = Radar.validate(
            replace(
                radar,
                **changes,
                updated_at=iso(self._clock.now()),
                updated_by=principal.actor,
                version_lock=radar.version_lock + 1,
            )
        )
        self._replace_radar(updated, radar)
        return updated

    def set_access(
        self,
        principal: Principal,
        slug: str,
        editors: list[str] | None,
        expected_lock: int,
        new_owner: tuple[str, str] | None = None,
    ) -> Radar:
        """``editors`` of None leaves the editor list alone: a client that only transfers ownership
        (the MCP server, a script) used to silently remove every co-editor."""
        radar = self.get(slug)
        self._require_admin(principal, radar)
        if radar.version_lock != expected_lock:
            raise ConflictError("the radar was changed by someone else; reload and retry")
        if editors is None:
            editors = list(radar.editors)
        # Only addresses being added: one stored before the email rule was tightened blocked every later
        # change to the list, including removing it (CTR HCB-055).
        for e in set(editors) - set(radar.editors):
            if not EMAIL_RE.fullmatch(e.strip()):
                raise ValidationError(f"editor {e!r} must be an email address")
        owner, owner_email = new_owner if new_owner else (radar.owner, radar.owner_email)
        # The unattended-apply opt-in belongs to the administrator who gave it for this owner: the
        # new owner never agreed to it, and can shorten the review delay.
        auto_apply = radar.auto_apply_agent and owner == radar.owner
        updated = Radar.validate(
            replace(
                radar,
                editors=tuple(editors),
                owner=owner,
                owner_email=owner_email.lower(),
                auto_apply_agent=auto_apply,
                updated_at=iso(self._clock.now()),
                updated_by=principal.actor,
                version_lock=radar.version_lock + 1,
            )
        )
        self._replace_radar(updated, radar)
        return updated

    REMOVE_EDITOR_ATTEMPTS = 5

    def remove_editor(self, principal: Principal, slug: str, email: str) -> bool:
        """Take one address off a radar's editors (a deleted account), whether or not it was there.
        Unlike ``set_access`` the other editors are not re-validated, so an address stored under an
        older, looser rule cannot block the removal, and a concurrent settings change is retried
        rather than failing it. Returns whether the address was removed."""
        email = email.strip().lower()
        attempt = 0
        while True:
            attempt += 1
            radar = self.get(slug)
            self._require_admin(principal, radar)
            if email not in radar.editors:
                return False
            updated = replace(
                radar,
                editors=tuple(e for e in radar.editors if e != email),
                updated_at=iso(self._clock.now()),
                updated_by=principal.actor,
                version_lock=radar.version_lock + 1,
            )
            try:
                self._replace_radar(updated, radar)
                return True
            except ConflictError:
                if attempt >= self.REMOVE_EDITOR_ATTEMPTS:
                    raise

    def delete(self, principal: Principal, slug: str) -> dict[str, Any]:
        """Delete a radar. An owner who is not an administrator may delete only a young radar; either way
        the final version and the settings are archived under ``deleted/`` first, so a mistaken delete
        can be restored by an operator. Returns what was deleted, for the audit row."""
        radar = self.get(slug)
        self._require_admin(principal, radar)
        if not principal.is_admin and radar.current_version > OWNER_DELETE_MAX_VERSIONS:
            raise ForbiddenError(
                f"this radar has {radar.current_version} versions; ask an administrator to delete it",
                {"versions": radar.current_version},
            )
        archive: dict[str, Any] = {"radar": radar.to_public(), "deleted_by": principal.actor}
        if radar.current_version:
            archive["entries"] = [e.to_public() for e in self.entries(slug)]
        # META goes first, conditionally on both counters: a save that claims a version while the
        # delete runs used to re-create the V# item and the blob after the batch delete, leaving orphan
        # data under a deleted slug that blocked the slug for two minutes and outlived the delete.
        try:
            self._t.delete_item(
                Key={"pk": f"RADAR#{slug}", "sk": "META"},
                ConditionExpression="current_version = :c AND version_lock = :l",
                ExpressionAttributeValues={":c": radar.current_version, ":l": radar.version_lock},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            raise ConflictError("the radar was changed by someone else; reload and retry") from err
        # The archive only once the delete has won: one written first was left behind by a delete that
        # lost the race, an archive of a radar that still exists for an operator to "restore" over newer
        # data (B3-051). Its data is still readable here, until the batch delete below.
        stamp = iso(self._clock.now()).replace(":", "")
        self._blobs.put(f"deleted/radars/{slug}/{stamp}.json", json.dumps(archive, ensure_ascii=False).encode("utf-8"))
        items = Query.all_items(self._t, KeyConditionExpression=Key("pk").eq(f"RADAR#{slug}"))
        with self._t.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
        self._blobs.delete_prefix(f"radars/{slug}/")
        return {
            "name": radar.name,
            "versions": radar.current_version,
            "entries": radar.entry_count,
            "owner": radar.owner,
        }

    # ---- versions --------------------------------------------------------------------------

    @staticmethod
    def _blob_key(slug: str, version: int) -> str:
        return f"radars/{slug}/v{version:06d}.json"

    def _write_version(
        self, radar: Radar, entries: list[RadarEntry], actor: str, note: str, schema_changed: bool = False
    ) -> Radar:
        number = radar.current_version + 1
        now = iso(self._clock.now())
        # Claim the version number before writing its blob: a save that loses a race fails here, before
        # it can overwrite the winner's blob.
        self._claim_version(radar.slug, number, now, actor, note, len(entries), schema_changed)
        body = {"version": number, "schema": radar.schema.to_public(), "entries": [e.to_public() for e in entries]}
        updated = replace(radar, current_version=number, entry_count=len(entries), updated_at=now, updated_by=actor)
        try:
            self._blobs.put(self._blob_key(radar.slug, number), json.dumps(body, ensure_ascii=False).encode("utf-8"))
            self._replace_radar(updated, radar)
        except BaseException:
            # The record write may have landed with its reply lost (botocore then retries, and the retry
            # fails its own condition): the version is committed, so it must be kept, not cleaned up.
            committed = self._committed(radar.slug, number)
            if committed:
                return updated
            if committed is None:  # unknown: keep the claim (an orphan is taken over later) and fail
                raise
            # Not committed: release the claimed number (a lost race, a failed blob write), or every
            # later save would collide with an orphan version.
            self._t.delete_item(Key={"pk": f"RADAR#{radar.slug}", "sk": f"V#{number:06d}"})
            self._blobs.delete_prefix(self._blob_key(radar.slug, number))
            raise
        return updated

    COMMITTED_ATTEMPTS = 3

    def _committed(self, slug: str, number: int) -> bool | None:
        """Whether the radar record already points at ``number``; None when it cannot be read. Unknown
        must not mean "clean up": an orphan claim costs one takeover after ORPHAN_CLAIM_SECONDS, while
        deleting a committed version breaks every read of the radar.

        Retried with backoff: one throttled read used to keep the claim and make every later save of
        that radar a 409 until the claim aged out.
        """
        for attempt in range(self.COMMITTED_ATTEMPTS):
            try:
                return self.get(slug).current_version >= number
            except Exception:  # any failure means unknown
                if attempt + 1 >= self.COMMITTED_ATTEMPTS:
                    return None
                self._sleep(0.05 * 2**attempt)
        return None  # pragma: no cover - the loop always returns

    def _is_replay(self, radar: Radar, entries: list[RadarEntry], actor: str) -> bool:
        """Whether the current version was made by ``actor`` and holds exactly ``entries``."""
        head = self._t.get_item(Key={"pk": f"RADAR#{radar.slug}", "sk": f"V#{radar.current_version:06d}"}).get("Item")
        if (head or {}).get("created_by") != actor:
            return False
        current = self.entries(radar.slug)
        return len(current) == len(entries) and all(
            a.key == b.key and a.same_content(b) for a, b in zip(current, entries, strict=True)
        )

    def _claim_version(
        self, slug: str, number: int, now: str, actor: str, note: str, count: int, schema_changed: bool
    ) -> None:
        item = {
            "pk": f"RADAR#{slug}",
            "sk": f"V#{number:06d}",
            "kind": "radar_version",
            "version": number,
            "created_by": actor,
            "created_at": now,
            "note": note[:300],
            "entry_count": count,
            "schema_changed": schema_changed,
        }
        try:
            self._t.put_item(Item=item, ConditionExpression="attribute_not_exists(sk)")
            return
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        # The number is taken. Either another save holds it (a real conflict) or a save died before it
        # could clean up (its Lambda timed out): a claim older than any save can take is an orphan, and
        # is taken over, conditionally on it still being that same orphan.
        held = self._t.get_item(Key={"pk": f"RADAR#{slug}", "sk": f"V#{number:06d}"}).get("Item")
        stale = iso(self._clock.now() - timedelta(seconds=ORPHAN_CLAIM_SECONDS))
        if held is None or str(held.get("created_at", "")) >= stale:
            raise ConflictError("the radar was changed by someone else; reload and retry")
        try:
            self._t.put_item(
                Item=item,
                ConditionExpression="created_at = :old",
                ExpressionAttributeValues={":old": held["created_at"]},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError("the radar was changed by someone else; reload and retry") from err
            raise

    def version_body(self, slug: str, version: int | None = None) -> tuple[Radar, int, RadarSchema, list[RadarEntry]]:
        radar = self.get(slug)
        if version is None and radar.current_version == 0:
            # Its first version write failed after the record was created: an empty radar, not a 404.
            return radar, 0, radar.schema, []
        number = radar.current_version if version is None else version
        if not 1 <= number <= radar.current_version:
            raise NotFoundError(f"version {number} of radar {slug} not found")
        body = json.loads(self._blobs.get(self._blob_key(slug, number)))
        return radar, number, RadarSchema.from_public(body["schema"]), [_entry_from(e) for e in body["entries"]]

    def entries(self, slug: str, version: int | None = None) -> list[RadarEntry]:
        return self.version_body(slug, version)[3]

    # ---- favourites (CTR: per-user favourite radars, sec. 9.3) --------------------------------

    def favourites(self, subject: str) -> list[str]:
        """The caller's favourite radars that still exist, by slug: a deleted radar's row is left behind and
        skipped here, so a radar later created with the same slug does not inherit it."""
        rows = Query.all_items(
            self._t,
            KeyConditionExpression=Key("pk").eq(f"USER#{subject}") & Key("sk").begins_with("FAV#radar#"),
        )
        # The radar's creation time is kept with the row: a new radar with a deleted one's slug is another radar.
        live = {r.slug: r.created_at for r in self.list_all()}
        wanted = [(str(r["sk"]).removeprefix("FAV#radar#"), r.get("created")) for r in rows]
        return [slug for slug, created in wanted if slug in live and live[slug] == created]

    def set_favourite(self, subject: str, slug: str, on: bool) -> bool:
        key = {"pk": f"USER#{subject}", "sk": f"FAV#radar#{slug}"}
        if not on:
            self._t.delete_item(Key=key)
            return False
        radar = self.get(slug)  # a radar that does not exist cannot be a favourite (CTR QA-103)
        self._t.put_item(Item={**key, "kind": "favourite", "at": iso(self._clock.now()), "created": radar.created_at})
        return True

    def find_entries(self, query: str = "", node_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Entries on every radar's current version that name ``query`` (its words, singular, or its text
        inside a title) or link to ``node_id``: which radars place a thing, and where (CTR HA-076)."""
        text = NameNormalizer.title_key(query)
        words = _significant_words(query)
        if not text and not node_id:
            raise ValidationError("give a query or a node_id")
        found: list[dict[str, Any]] = []
        for radar in sorted(self.list_all(), key=lambda r: r.slug)[:MAX_RADARS_SEARCHED]:
            for e in self.entries(radar.slug):  # a radar whose first save failed has none
                by_node = bool(node_id) and e.node_id == node_id
                title = NameNormalizer.title_key(e.title)
                by_text = bool(text) and (text in title or _near_duplicate(words, _significant_words(e.title)))
                if by_node or by_text:
                    found.append(
                        {
                            "radar": radar.slug,
                            "radar_name": radar.name,
                            "version": radar.current_version,
                            **{k: v for k, v in e.to_public().items() if k != "description"},
                        }
                    )
        return found[:limit]

    # ---- version tags (CTR HA-007/008/009, HCB-056) ----------------------------------------

    @staticmethod
    def _tag_key(tag: str) -> str:
        """Tags that read alike share a key: "Q3 2026", "q3-2026" and "Q3.2026" are one tag (CTR HA-008)."""
        return re.sub(r"[\s._-]+", "", NameNormalizer.title_key(tag))

    def tag_version(self, principal: Principal, slug: str, number: int, tag: str) -> dict[str, Any]:
        """Name a version, so it can be opened and shared by its name; a tag that looks like another is
        refused, and a tag of only digits would read as a version number."""
        radar = self.get(slug)
        self._require_edit(principal, radar)
        tag = " ".join(tag.split())
        if not _TAG.fullmatch(tag) or tag.isdigit():
            raise ValidationError(
                "a tag is 1 to 40 letters, digits, spaces, dots, dashes or underscores, starts with a letter or "
                "digit and is not only digits"
            )
        if not 1 <= number <= radar.current_version:
            raise NotFoundError(f"version {number} of radar {slug} not found")
        key = self._tag_key(tag)
        item = {
            "pk": f"RADAR#{slug}",
            "sk": f"TAG#{key}",
            "kind": "radar_tag",
            "tag": tag,
            "version": number,
            "by": principal.actor,
            "at": iso(self._clock.now()),
        }
        try:
            self._t.put_item(Item=item, ConditionExpression="attribute_not_exists(sk)")
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            held = self._t.get_item(Key={"pk": f"RADAR#{slug}", "sk": f"TAG#{key}"}).get("Item") or {}
            held_version = int(held["version"]) if "version" in held else None
            raise ConflictError(
                f"the tag {held.get('tag', tag)!r} (version {held_version}) reads the same; choose another name",
                {"code": "tag_taken", "tag": held.get("tag"), "version": held_version},
            ) from err
        return {"tag": tag, "version": number}

    def untag(self, principal: Principal, slug: str, tag: str) -> bool:
        radar = self.get(slug)
        self._require_edit(principal, radar)
        res = self._t.delete_item(
            Key={"pk": f"RADAR#{slug}", "sk": f"TAG#{self._tag_key(tag)}"}, ReturnValues="ALL_OLD"
        )
        if not res.get("Attributes"):
            raise NotFoundError(f"radar {slug} has no tag {tag!r}")
        return True

    def tags(self, slug: str) -> list[dict[str, Any]]:
        """The radar's tags, newest version first."""
        rows = Query.all_items(
            self._t, KeyConditionExpression=Key("pk").eq(f"RADAR#{slug}") & Key("sk").begins_with("TAG#")
        )
        out = [{"tag": r["tag"], "version": int(r["version"]), "by": r.get("by", ""), "at": r.get("at")} for r in rows]
        return sorted(out, key=lambda t: (-t["version"], t["tag"]))

    def version_of_tag(self, slug: str, tag: str) -> int:
        item = self._t.get_item(Key={"pk": f"RADAR#{slug}", "sk": f"TAG#{self._tag_key(tag)}"}).get("Item")
        if not item:
            raise NotFoundError(f"radar {slug} has no tag {tag!r}")
        return int(item["version"])

    def entry_history(self, slug: str, key: str, depth: int = HISTORY_VERSIONS) -> dict[str, Any]:
        """One entry's story over the radar's last ``depth`` versions, oldest first (CTR HA-003/012/043/072):
        when it was added, moved ring, changed sector, had its text edited, was removed and came back,
        with who saved each version and whether the change came from an AI suggestion."""
        radar = self.get(slug)
        newest = radar.current_version
        first = max(1, newest - depth + 1)
        meta = {
            int(i["version"]): i
            for i in Query.all_items(
                self._t,
                KeyConditionExpression=Key("pk").eq(f"RADAR#{slug}")
                & Key("sk").between(f"V#{first:06d}", f"V#{newest:06d}"),
            )
        }
        events: list[dict[str, Any]] = []
        before: RadarEntry | None = None
        seen = False
        for number in range(first, newest + 1):
            entry = next((e for e in self.entries(slug, number) if e.key == key), None)
            changes = _entry_changes(before, entry, seen)
            if changes:
                m = meta.get(number, {})
                note = str(m.get("note", ""))
                by = str(m.get("created_by", ""))
                events.append(
                    {
                        "version": number,
                        "at": m.get("created_at"),
                        "by": by,
                        "note": note,
                        # A version written from suggestions (the curator's, applied by a person or on autopilot).
                        "ai": note.startswith("applied ") or by.startswith(("module:", "system:")),
                        **changes,
                    }
                )
            seen = seen or entry is not None
            before = entry
        if not seen:
            raise NotFoundError(f"entry {key} is not in the last {depth} versions of radar {slug}")
        return {
            "key": key,
            "from_version": first,
            "to_version": newest,
            "events": events,
            "current": before is not None,
        }

    def versions_page(
        self, slug: str, limit: int = MAX_VERSIONS_LISTED, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Newest versions first, with a cursor for the older ones (every version can be rolled back to,
        so every version must be discoverable)."""
        self.get(slug)
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(f"RADAR#{slug}") & Key("sk").begins_with("V#"),
            "ScanIndexForward": False,
            "Limit": max(1, min(limit, MAX_VERSIONS_LISTED)),
        }
        start = decode_cursor(cursor, TABLE_KEYS, ("pk", f"RADAR#{slug}"), ("sk", "V#"))
        if start:
            kwargs["ExclusiveStartKey"] = start
        res = self._t.query(**kwargs)
        items = [{k: _from_ddb(v) for k, v in i.items() if k not in ("pk", "sk", "kind")} for i in res.get("Items", [])]
        return items, encode_cursor(res.get("LastEvaluatedKey"))

    def save(
        self,
        principal: Principal,
        slug: str,
        entries: list[RadarEntry],
        expected_version: int,
        note: str = "",
        allow_unmatched: bool = False,
        allow_empty: bool = False,
    ) -> Radar:
        radar = self.get(slug)
        self._require_edit(principal, radar)
        return self._save(
            radar,
            entries,
            expected_version,
            principal.actor,
            note,
            allow_unmatched,
            skip_if_unchanged=True,
            shrink=None if allow_empty else "shrinking_save",
        )

    @staticmethod
    def _removed(current: list[RadarEntry], incoming: list[RadarEntry]) -> int:
        """How many existing entries ``incoming`` would remove: compared by key, not by count, so a
        replace with as many new titles as there were old ones is not "no change" (B3-047)."""
        keys = {e.key for e in incoming}
        return sum(1 for e in current if e.key not in keys)

    @classmethod
    def _guard_shrink(cls, current: list[RadarEntry], incoming: list[RadarEntry], code: str) -> None:
        """Refuse, unless confirmed, a change that removes more than half of a radar's entries (and at
        least ``SHRINK_MIN_REMOVED``): an import, a save from a stale list or an MCP client, or a
        rollback to an early version (B3-047, B3-048, B3-049)."""
        removed = cls._removed(current, incoming)
        # Emptying a radar is confirmed at any size: a save of [] over a two-entry radar wiped it (CTR HA-078).
        emptied = not incoming and bool(current)
        if not emptied and (removed < SHRINK_MIN_REMOVED or removed * 2 <= len(current)):
            return
        raise ValidationError(
            f"this would remove {removed} of {len(current)} entries (pass allow_empty to confirm)",
            {"code": code, "before": len(current), "after": len(incoming), "removed": removed},
        )

    def _new_duplicates(self, radar: Radar, clean: list[RadarEntry], rows: list[int]) -> list[int]:
        """The duplicate rows a save may not keep. Titles now compare with compatibility forms folded,
        so two stored entries that were distinct when saved ("Octank" and its full-width form) made every
        later save of the radar fail (CR2-11). A row is excused when its exact title is already stored
        and it collides only under the new folding: the radar keeps what it had, and nothing new may
        add such a pair."""
        stored = {NameNormalizer.display(e.title) for e in self.entries(radar.slug)} if radar.current_version else set()
        legacy = collections.Counter(NameNormalizer.legacy_title_key(e.title) for e in clean)
        return [
            row
            for row in rows
            if clean[row - 1].title not in stored or legacy[NameNormalizer.legacy_title_key(clean[row - 1].title)] > 1
        ]

    def _save(
        self,
        radar: Radar,
        entries: list[RadarEntry],
        expected_version: int,
        actor: str,
        note: str,
        allow_unmatched: bool,
        allow_duplicates: bool = False,
        skip_if_unchanged: bool = False,
        shrink: str | None = None,
    ) -> Radar:
        """``shrink`` names the error code for a change that would remove most entries unconfirmed."""
        if radar.current_version == expected_version + 1 and self._is_replay(radar, entries, actor):
            # The same save sent again after it committed behind a dropped reply (an edge 503): it
            # used to be a 409 "someone else saved" about the caller's own save (CTR HA-038).
            return radar
        if radar.current_version != expected_version:
            raise ConflictError(
                "someone else saved this radar; reload and retry",
                {"expected": expected_version, "actual": radar.current_version},
            )
        clean, unmatched, duplicates = EntryValidator.check(radar.schema, entries, radar.extra.get("renamed"))
        if duplicates and not allow_duplicates:
            duplicates = self._new_duplicates(radar, clean, duplicates)
        if duplicates and not allow_duplicates:
            raise ConflictError("entry titles must be unique", {"code": "duplicate_title", "rows": duplicates})
        if unmatched and not allow_unmatched:
            raise ValidationError(
                "some values do not match the radar schema",
                {"code": "unmatched_values", "unmatched": [u.to_public() for u in unmatched]},
            )
        repeated = _repeated_keys(clean)
        if repeated:
            raise ValidationError(
                f"entry keys must be unique: {', '.join(repeated[:5])}", {"code": "duplicate_key", "keys": repeated}
            )
        if shrink and radar.current_version:
            self._guard_shrink(self.entries(radar.slug), clean, shrink)
        if (
            skip_if_unchanged
            and radar.current_version
            and [e.to_public() for e in clean] == [e.to_public() for e in self.entries(radar.slug)]
        ):
            # Nothing changed: a new version and a new blob for every save of an untouched form is
            # version spam on an unbounded history. A rollback is an explicit request for a version,
            # so it is not short-circuited here.
            return radar
        return self._write_version(radar, clean, actor, note or "saved")

    def validate_import(
        self, principal: Principal, slug: str, text: str, fmt: str, mode: str = "replace"
    ) -> dict[str, Any]:
        """A dry run of exactly what ``import_entries`` would save (the same merge with existing
        entries), for an editor of the radar."""
        radar = self.get(slug)
        self._require_edit(principal, radar)
        entries = self._prepare_import(radar, text, fmt, mode)
        clean, unmatched, duplicates = EntryValidator.check(radar.schema, entries, radar.extra.get("renamed"))
        current = self.entries(slug) if radar.current_version else []
        # The real import refuses a file whose key column repeats a value: the dry run used to accept it
        # (CTR HA-033).
        repeated = _repeated_keys(clean)
        return {
            "rows": len(clean),
            "unmatched": [u.to_public() for u in unmatched],
            "duplicate_rows": duplicates,
            "duplicate_keys": repeated,
            "accepted": not unmatched and not duplicates and not repeated,
            # Saving it would remove every entry: the real import then needs allow_empty.
            "empty": not entries,
            # So does removing more than half of them: the dry run says how many would go.
            "before": radar.entry_count,
            "removed": self._removed(current, entries),
        }

    def _parse(self, text: str, fmt: str) -> list[RadarEntry]:
        if fmt == "csv":
            return EntryCodec.parse_csv(text, self._ids.uuid7)
        if fmt == "json":
            return EntryCodec.parse_json(text, self._ids.uuid7)
        raise ValidationError("format must be csv or json")

    def import_entries(
        self,
        principal: Principal,
        slug: str,
        text: str,
        fmt: str,
        expected_version: int,
        mode: str = "replace",
        allow_unmatched: bool = False,
        allow_empty: bool = False,
    ) -> Radar:
        radar = self.get(slug)
        self._require_edit(principal, radar)
        incoming = self._prepare_import(radar, text, fmt, mode)
        if not incoming and not allow_empty:
            raise ValidationError(
                "the file has no entries; this would remove every entry (pass allow_empty to confirm)",
                {"code": "empty_import"},
            )
        # The zero-row confirmation covered only the extreme: a one-row replace of a 50-entry radar
        # removed 49 of them with no confirmation at all.
        return self._save(
            radar,
            incoming,
            expected_version,
            principal.actor,
            f"imported {fmt} ({mode})",
            allow_unmatched,
            skip_if_unchanged=True,
            shrink=None if allow_empty else "shrinking_import",
        )

    def _prepare_import(self, radar: Radar, text: str, fmt: str, mode: str) -> list[RadarEntry]:
        incoming = self._parse(text, fmt)
        current = self.entries(radar.slug) if radar.current_version else []
        by_title = {NameNormalizer.title_key(e.title): e for e in current}
        # keep stable keys for entries that already exist (match by title)
        incoming = [
            replace(e, key=by_title[NameNormalizer.title_key(e.title)].key)
            if NameNormalizer.title_key(e.title) in by_title
            else e
            for e in incoming
        ]
        if mode == "merge":
            # An incoming row replaces the current entry with the same title or the same key, so a row
            # exported, renamed and re-imported updates its entry instead of duplicating its key.
            incoming_titles = {NameNormalizer.title_key(e.title) for e in incoming}
            incoming_keys = {e.key for e in incoming}
            kept = [
                e
                for e in current
                if NameNormalizer.title_key(e.title) not in incoming_titles and e.key not in incoming_keys
            ]
            incoming = kept + incoming
        elif mode != "replace":
            raise ValidationError("mode must be replace or merge")
        return incoming

    def export(self, slug: str, fmt: str, version: int | None = None) -> str:
        entries = self.entries(slug, version)
        if fmt == "csv":
            return EntryCodec.to_csv(entries)
        if fmt == "json":
            return EntryCodec.to_json(entries)
        raise ValidationError("format must be csv or json")

    def template_csv(self, slug: str) -> str:
        radar = self.get(slug)
        s = radar.schema
        rows = [
            RadarEntry(
                key="",
                title=f"Example {i + 1}",
                sector=s.sectors[i % len(s.sectors)].name,
                ring=s.rings[i % len(s.rings)].name,
                marker=s.markers[i % len(s.markers)].name if s.markers else None,
                description="Replace with your own entry",
            )
            for i in range(max(len(s.sectors), len(s.rings), len(s.markers), 1))
        ]
        return EntryCodec.to_csv(rows)

    def rollback(
        self, principal: Principal, slug: str, target: int, expected_version: int, allow_empty: bool = False
    ) -> Radar:
        radar, _, schema, entries = self.version_body(slug, target)
        self._require_edit(principal, radar)
        if target == radar.current_version:
            raise ValidationError("the radar is already at that version")
        # Names and order decide whether entries fit; colours, glyphs and labels are kept from the current
        # schema, so a colour tweak does not make every earlier version unrestorable.
        if not schema.same_identity(radar.schema):
            raise ValidationError("that version used a different schema; restore the schema first")
        return self._save(
            radar,
            entries,
            expected_version,
            principal.actor,
            f"rolled back to v{target}",
            True,
            shrink=None if allow_empty else "shrinking_rollback",
        )

    def diff(self, slug: str, from_version: int, to_version: int) -> dict[str, Any]:
        before, after = self.entries(slug, from_version), self.entries(slug, to_version)
        return {"from": from_version, "to": to_version, "changes": RadarDiff.compare(before, after)}

    def update_schema(
        self,
        principal: Principal,
        slug: str,
        schema: RadarSchema,
        expected_version: int,
        renames: dict[str, dict[str, str]] | None = None,
        drop_unmatched: bool = False,
    ) -> Radar:
        radar = self.get(slug)
        self._require_admin(principal, radar)
        new_schema = RadarSchema.validate(schema)
        current = self.entries(slug) if radar.current_version else []
        renames = renames or {}
        for dim in renames:
            if dim not in ("sector", "ring", "marker"):
                raise ValidationError("renames keys must be sector, ring or marker")
        rewritten: list[RadarEntry] = []
        dropped = 0
        for e in current:
            sector = renames.get("sector", {}).get(e.sector, e.sector)
            ring = renames.get("ring", {}).get(e.ring, e.ring)
            marker = renames.get("marker", {}).get(e.marker or "", e.marker) if e.marker else None
            canonical = (
                new_schema.match("sector", sector),
                new_schema.match("ring", ring),
                None if marker is None else new_schema.match("marker", marker),
            )
            ok = canonical[0] is not None and canonical[1] is not None and (marker is None or canonical[2] is not None)
            # Stored under the schema's own spelling, so an entry never ends up as "  tODAY ".
            entry = replace(e, sector=canonical[0] or sector, ring=canonical[1] or ring, marker=canonical[2])
            if not ok:
                if not drop_unmatched:
                    raise ValidationError(
                        f"entry {e.title!r} would no longer match the schema; add a rename or "
                        "allow dropping unmatched entries"
                    )
                dropped += 1
                continue
            rewritten.append(entry)
        if radar.current_version != expected_version:
            raise ConflictError("someone else saved this radar; reload and retry")
        updated = replace(
            radar, schema=new_schema, extra={**radar.extra, "renamed": _remember(radar, new_schema, renames)}
        )
        saved = self._write_version(
            updated,
            rewritten,
            principal.actor,
            f"schema changed ({dropped} entries dropped)" if dropped else "schema changed",
            schema_changed=True,
        )
        self._rename_open_suggestions(slug, new_schema, renames)
        return saved

    def _rename_open_suggestions(self, slug: str, schema: RadarSchema, renames: dict[str, dict[str, str]]) -> list[str]:
        """Apply a schema change to the open suggestions too. Their proposals kept the old dimension
        names, so accepting one failed with ``unmatched_values`` -- and autopilot deleted it."""
        touched: list[str] = []
        for item in Query.all_items(
            self._t, KeyConditionExpression=Key("pk").eq(f"RADAR#{slug}") & Key("sk").begins_with("SUG#")
        ):
            changed = {
                field: self._rename_dims(item[field], schema, renames)
                for field in ("proposal", "base")
                if isinstance(item.get(field), dict)
            }
            changed = {k: v for k, v in changed.items() if v != item[k]}
            if not changed:
                continue
            names = {f"#f{i}": k for i, k in enumerate(changed)}
            try:
                # The two fields only, and only while the suggestion still exists: a whole-item put
                # re-created one a reviewer discarded (or accepted) meanwhile (B3-050).
                self._t.update_item(
                    Key={"pk": item["pk"], "sk": item["sk"]},
                    UpdateExpression="SET " + ", ".join(f"{n} = :v{i}" for i, n in enumerate(names)),
                    ConditionExpression="attribute_exists(sk)",
                    ExpressionAttributeNames=names,
                    ExpressionAttributeValues={f":v{i}": _to_ddb(v) for i, v in enumerate(changed.values())},
                )
            except ClientError as err:
                if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                continue
            touched.append(str(item["sk"]).removeprefix("SUG#"))
        return touched

    @staticmethod
    def _rename_dims(body: dict[str, Any], schema: RadarSchema, renames: dict[str, dict[str, str]]) -> dict[str, Any]:
        out = dict(body)
        for dim in ("sector", "ring", "marker"):
            value = out.get(dim)
            if not isinstance(value, str) or not value:
                continue
            renamed = renames.get(dim, {}).get(value, value)
            out[dim] = schema.match(dim, renamed) or renamed
        return out

    # ---- suggestions -------------------------------------------------------------------------

    def propose(
        self,
        slug: str,
        module_actor: str,
        run_id: str,
        proposal: dict[str, Any],
        reason: str,
        entry_key: str | None = None,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        radar = self.get(slug)
        if radar.mode is RadarMode.MANUAL:
            raise ForbiddenError("this radar is in manual mode; the curator cannot propose changes")
        if not 10 <= len(reason.strip()) <= 2000:
            raise ValidationError("reason must be 10-2000 characters and cite the evidence")
        current = {e.key: e for e in (self.entries(slug) if radar.current_version else [])}
        base = current.get(entry_key) if entry_key else None
        if entry_key and base is None:
            raise NotFoundError(f"entry {entry_key} not found on radar {slug}")
        merged = {
            **(base.to_public() if base else {}),
            **{
                k: v
                for k, v in proposal.items()
                if k in ("title", "sector", "ring", "marker", "description", "node_id", "link", "sub_sector")
            },
        }
        key = entry_key or self._ids.uuid7()
        if "Item" in self._t.get_item(Key={"pk": f"RADAR#{slug}", "sk": f"SUG#{key}"}):
            raise ConflictError(
                "this entry already has an open suggestion; wait until it is reviewed", {"code": "suggestion_pending"}
            )
        open_suggestions = self._open_suggestions(slug)
        if len(open_suggestions) >= MAX_OPEN_SUGGESTIONS:
            raise ConflictError(
                f"this radar already has {MAX_OPEN_SUGGESTIONS} open suggestions; review some before proposing more"
            )
        merged["key"] = key
        if "title" not in merged or "sector" not in merged or "ring" not in merged:
            raise ValidationError("a new entry needs a title, sector and ring")
        # Open proposals for new entries count too: they would land on the same cap (CTR HC-089).
        if base is None and radar.entry_count + sum(1 for s in open_suggestions if s.get("is_new")) >= MAX_ENTRIES:
            # It could never be accepted (the save would hit the cap), and autopilot would delete it
            # every tick, so the curator spends its budget filing proposals that cannot land.
            raise ConflictError(
                f"this radar already has {MAX_ENTRIES} entries; a new one cannot be proposed",
                {"code": "radar_full"},
            )
        # Only the proposed entry is checked: an existing entry left outside the schema by a schema edit,
        # or two existing entries sharing a title, must not stop the curator proposing something else.
        (proposed,), unmatched, _ = EntryValidator.check(radar.schema, [_entry_from(merged)])
        if unmatched:
            raise ValidationError(
                "proposal does not match the radar schema", {"unmatched": [u.to_public() for u in unmatched]}
            )
        title_key = NameNormalizer.title_key(proposed.title)
        if any(NameNormalizer.title_key(e.title) == title_key for k, e in current.items() if k != key):
            raise ConflictError("an entry with that title already exists")
        if base is None:
            # "Amazon Bedrock AgentCore" beside "Bedrock AgentCore": the exact title check missed it (CTR HC-097).
            words = _significant_words(proposed.title)
            others = [e.title for k, e in current.items() if k != key] + [
                str((o.get("proposal") or {}).get("title", "")) for o in open_suggestions if o.get("is_new")
            ]
            twin = next(
                (
                    o
                    for o in others
                    if _near_duplicate(words, _significant_words(o)) and NameNormalizer.title_key(o) != title_key
                ),
                None,
            )  # the same title is answered by the checks around this one
            if twin is not None:
                raise ConflictError(
                    f"{proposed.title!r} names the same thing as {twin!r}, already on the radar or proposed",
                    {"code": "near_duplicate", "existing": twin},
                )
        if base is not None and proposed.same_content(base):
            raise ValidationError("the proposal does not change anything")
        if self._recently_discarded(slug, self._discard_key(base is None, key, proposed.to_public()), proposed):
            raise ConflictError(
                f"a reviewer discarded this same change in the last {DISCARD_MEMORY_DAYS} days; propose it again "
                "only with a different change backed by newer evidence",
                {"code": "recently_discarded"},
            )
        if base is None and any(
            NameNormalizer.title_key(str((o.get("proposal") or {}).get("title", ""))) == title_key
            for o in open_suggestions
        ):
            # A new entry's key is fresh each call, so the per-key check never fired: a runtime retry
            # filed a second open suggestion for the same title (B3-076).
            raise ConflictError(
                "a suggestion for an entry with that title is already open; wait until it is reviewed",
                {"code": "suggestion_pending"},
            )
        now = iso(self._clock.now())
        item = {
            "pk": f"RADAR#{slug}",
            "sk": f"SUG#{key}",
            "kind": "suggestion",
            "entry_key": key,
            "is_new": base is None,
            "proposal": proposed.to_public(),
            "base": base.to_public() if base else None,
            "reason": reason.strip(),
            "evidence": evidence or [],
            "created_at": now,
            "created_by": module_actor,
            "run_id": run_id,
            "gsi1pk": "SUGGESTIONS",
            "gsi1sk": f"{now}#{slug}",
        }
        try:
            # One open suggestion per entry, never silently replaced under a reviewer's eyes.
            self._t.put_item(
                Item={k: v for k, v in _to_ddb(item).items() if v is not None},
                ConditionExpression="attribute_not_exists(sk)",
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError(
                    "this entry already has an open suggestion; wait until it is reviewed",
                    {"code": "suggestion_pending"},
                ) from err
            raise
        return self._suggestion_public(item)

    @staticmethod
    def _suggestion_public(raw: dict[str, Any]) -> dict[str, Any]:
        i = _from_ddb(raw)
        return {
            k: i.get(k)
            for k in (
                "entry_key",
                "is_new",
                "proposal",
                "base",
                "reason",
                "evidence",
                "created_at",
                "created_by",
                "run_id",
            )
        }

    def suggestions(self, slug: str) -> list[dict[str, Any]]:
        self.get(slug)
        return self._open_suggestions(slug)

    def _get_suggestion(self, slug: str, key: str) -> dict[str, Any]:
        res = self._t.get_item(Key={"pk": f"RADAR#{slug}", "sk": f"SUG#{key}"}, ConsistentRead=True)
        if "Item" not in res:
            raise NotFoundError("suggestion not found")
        return self._suggestion_public(res["Item"])

    def accept(
        self, principal: Principal, slug: str, key: str, edits: dict[str, Any] | None = None
    ) -> tuple[Radar, dict[str, Any]]:
        """Apply an open suggestion; returns the radar and the suggestion that was applied, for the
        audit row."""
        radar = self.get(slug)
        self._require_edit(principal, radar)
        sug = self._get_suggestion(slug, key)
        if not self._still_applies(radar, sug):
            # The proposal carries the whole merged entry: applying it now would silently undo the edits
            # made since it was proposed.
            raise ConflictError(
                "the entry changed since this suggestion was made; discard it or edit the entry directly",
                {"code": "suggestion_stale"},
            )
        return self._apply(radar, sug, principal.actor, edits), sug

    def accept_many(self, principal: Principal, slug: str, keys: list[str]) -> tuple[Radar, list[dict[str, Any]]]:
        """Apply several open suggestions as one version (CTR H-019): accepting ten one by one made ten
        versions. All or nothing: one stale, unmatched or reviewed-meanwhile proposal refuses the lot and
        every claim is put back."""
        radar = self.get(slug)
        self._require_edit(principal, radar)
        if not keys or len(set(keys)) != len(keys):
            raise ValidationError("keys must name one or more different suggestions")
        sugs = [self._get_suggestion(slug, k) for k in keys]
        current = self.entries(slug) if radar.current_version else []
        titles: list[str] = []
        for sug in sugs:
            if not self._still_applies(radar, sug, current):
                raise ConflictError(
                    f"the entry changed since the suggestion for {sug['entry_key']} was made; discard it or edit "
                    "the entry directly",
                    {"code": "suggestion_stale", "entry_key": sug["entry_key"]},
                )
            current, title = self._stage(radar, sug, current)
            titles.append(title)
        claimed: list[dict[str, Any]] = []
        try:
            for sug in sugs:
                claimed.append(self._claim_suggestion(slug, sug["entry_key"]))
            shown = ", ".join(titles[:3]) + (f" and {len(titles) - 3} more" if len(titles) > 3 else "")
            saved = self._save(
                radar,
                current,
                radar.current_version,
                principal.actor,
                f"applied {len(sugs)} suggestion(s): {shown}"[:300],
                allow_unmatched=True,
                allow_duplicates=True,
            )
        except BaseException:
            for item in claimed:
                self._restore_suggestion(item)  # nothing was applied: every proposal is still open
            raise
        return saved, sugs

    def _still_applies(self, radar: Radar, sug: dict[str, Any], current: list[RadarEntry] | None = None) -> bool:
        """Whether the entry still equals the suggestion's base (or, for a new entry, still does not exist)."""
        if current is None:
            current = self.entries(radar.slug) if radar.current_version else []
        existing = next((e for e in current if e.key == sug["entry_key"]), None)
        base = sug.get("base")
        return (existing is None and base is None) or (
            existing is not None and base is not None and existing.same_content(_entry_from(base))
        )

    def _claim_suggestion(self, slug: str, key: str) -> dict[str, Any]:
        """Take an open suggestion off the radar before applying it, conditionally on it still being
        there: an accept used to apply a proposal a colleague (or autopilot) had discarded or applied in
        between, and override their decision (B3-073). Returns the raw item, to put it back if the
        apply then fails."""
        try:
            res = self._t.delete_item(
                Key={"pk": f"RADAR#{slug}", "sk": f"SUG#{key}"},
                ConditionExpression="attribute_exists(sk)",
                ReturnValues="ALL_OLD",
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            raise NotFoundError("suggestion not found; it was reviewed meanwhile") from err
        return dict(res["Attributes"])

    def _restore_suggestion(self, item: dict[str, Any]) -> None:
        try:
            self._t.put_item(Item=item, ConditionExpression="attribute_not_exists(sk)")
        except Exception:
            log.exception("could not restore suggestion %s after a failed apply", item.get("sk"))

    def _apply(
        self,
        radar: Radar,
        sug: dict[str, Any],
        actor: str,
        edits: dict[str, Any] | None = None,
    ) -> Radar:
        current = self.entries(radar.slug) if radar.current_version else []
        entries, title = self._stage(radar, sug, current, edits)
        claimed = self._claim_suggestion(radar.slug, sug["entry_key"])
        try:
            return self._save(
                radar,
                entries,
                radar.current_version,
                actor,
                f"applied suggestion for {title}",
                allow_unmatched=True,
                allow_duplicates=True,
            )
        except BaseException:
            self._restore_suggestion(claimed)  # nothing was applied: the proposal is still open
            raise

    def _stage(
        self, radar: Radar, sug: dict[str, Any], current: list[RadarEntry], edits: dict[str, Any] | None = None
    ) -> tuple[list[RadarEntry], str]:
        """The entries with ``sug`` applied, and the applied title; nothing is written."""
        proposal = {**sug["proposal"], **(edits or {}), "key": sug["entry_key"]}
        # Only the entry being applied is validated, as ``propose`` does: one existing entry outside
        # the schema (left by a schema edit or a rollback), or two existing entries sharing a title,
        # used to make every accept a 400 about a row the reviewer had not touched -- and made
        # autopilot delete every due proposal on that radar as "superseded".
        (clean,), unmatched, _ = EntryValidator.check(radar.schema, [_entry_from(proposal)])
        if unmatched:
            raise ValidationError(
                "the proposed entry does not match the radar schema",
                {"code": "unmatched_values", "unmatched": [u.to_public() for u in unmatched]},
            )
        entries = [e for e in current if e.key != sug["entry_key"]]
        position = next((i for i, e in enumerate(current) if e.key == sug["entry_key"]), len(entries))
        title_key = NameNormalizer.title_key(clean.title)
        if any(NameNormalizer.title_key(e.title) == title_key for e in entries):
            raise ConflictError("an entry with that title already exists", {"code": "duplicate_title"})
        entries.insert(position, clean)
        return entries, str(proposal["title"])

    def discard(self, principal: Principal, slug: str, key: str) -> dict[str, Any]:
        """Delete an open suggestion and return what it proposed, for the audit row. Conditional on it
        still being there: a discard that lost the race to an accept (or to autopilot) used to answer
        200 and record a discard for a proposal that was now on the radar (B3-074).

        The discard is remembered for DISCARD_MEMORY_DAYS so the curator cannot file the same change again
        on its next run (CTR H-024: it left no trace, and the same rejected proposal kept coming back)."""
        radar = self.get(slug)
        self._require_edit(principal, radar)
        claimed = self._claim_suggestion(slug, key)
        proposal = dict(claimed.get("proposal") or {})
        now = self._clock.now()
        remembered = self._discard_key(bool(claimed.get("is_new")), str(claimed.get("entry_key", "")), proposal)
        self._t.put_item(
            Item={
                "pk": f"RADAR#{slug}",
                "sk": f"DISCARDED#{remembered}",
                "kind": "discarded_suggestion",
                "proposal": proposal,
                "discarded_at": iso(now),
                "discarded_by": principal.actor,
                "ttl": int(now.timestamp()) + DISCARD_MEMORY_DAYS * 86400,
            }
        )
        return self._suggestion_public(claimed)

    @staticmethod
    def _discard_key(is_new: bool, entry_key: str, proposal: dict[str, Any]) -> str:
        """An edit is remembered by its entry, a new entry by its title (its key is fresh on every call)."""
        return f"NEW#{NameNormalizer.title_key(str(proposal.get('title', '')))}" if is_new else f"KEY#{entry_key}"

    def _recently_discarded(self, slug: str, discard_key: str, proposed: RadarEntry) -> bool:
        item = self._t.get_item(Key={"pk": f"RADAR#{slug}", "sk": f"DISCARDED#{discard_key}"}).get("Item")
        if not item or int(item.get("ttl", 0)) <= int(self._clock.now().timestamp()):
            return False  # never discarded, or the memory has lapsed (TTL deletes are not immediate)
        before = _entry_from({**dict(item.get("proposal") or {}), "key": proposed.key})
        return proposed.same_content(before)

    def autopilot_tick(
        self,
        usable_evidence: EvidenceCheck | None = None,
        audit: AuditSink | None = None,
        paused: bool = False,
    ) -> dict[str, int]:
        """Apply due suggestions on autopilot radars.

        Agent proposals are applied unattended only on a radar an administrator opted in
        (``auto_apply_agent``), only while GenAI is switched on (``paused`` holds them all, B3-006),
        and only when every id they cite is still usable evidence -- or a conclusion resting on it --
        from at least ``MIN_EVIDENCE_DOMAINS`` distinct sites: existence alone was satisfied by any
        node at all, including an unrelated topic or scout evidence from an attacker's page. A
        proposed link must come from one of those sites (B3-005). Anything else waits for a human.

        At most ``MAX_AUTOPILOT_APPLIES`` entries are applied per radar per tick, each radar and each
        suggestion is isolated, and a proposal that cannot be applied right now is held rather than
        deleted: one unmatched entry elsewhere on the radar, or a person saving between the read and
        the version claim, used to destroy every due proposal on that radar with only an audit row.
        """
        counts = {"applied": 0, "superseded": 0, "held": 0, "errors": 0}
        now = self._clock.now()
        for listed in self.list_all():
            if listed.mode is not RadarMode.AUTOPILOT:
                continue  # the GSI only narrows the candidates; every gate is read consistently below
            try:
                self._tick_radar(listed.slug, now, counts, usable_evidence, audit, paused)
            except Exception:
                # One radar whose blob is missing, or whose table or bucket call fails, must not stop
                # the other radars (and, through the sweep, module dispatch and the crawls).
                counts["errors"] += 1
                log.exception("autopilot failed for radar %s", listed.slug)
        return counts

    @staticmethod
    def _link_corroborated(sug: dict[str, Any], sites: set[str]) -> bool:
        """Whether an unattended apply may set the proposal's link: unchanged from the entry, or on one
        of the sites the evidence came from. One injected curator run could otherwise put an attacker's
        landing page on an entry people click (B3-005)."""
        link = (sug.get("proposal") or {}).get("link")
        if not link or link == (sug.get("base") or {}).get("link"):
            return True
        return UrlGuard.site(str(link)) in sites

    def _tick_radar(
        self,
        slug: str,
        now: datetime,
        counts: dict[str, int],
        usable_evidence: EvidenceCheck | None,
        audit: AuditSink | None,
        paused: bool,
    ) -> None:
        # Mode, delay and opt-in from a consistent read: the GSI behind list_all can still show an
        # opt-in an administrator has just switched off (B3-010).
        radar = self.get(slug)
        if radar.mode is not RadarMode.AUTOPILOT:
            return
        cutoff = now - timedelta(days=radar.auto_apply_days)
        applied_here = 0
        # Read once per radar per tick, not once per due suggestion: 200 held proposals cost 200 blob
        # reads every five minutes (B3-011).
        current: list[RadarEntry] | None = None
        batch: list[tuple[dict[str, Any], str, dict[str, Any], str]] = []
        for sug in self._open_suggestions(slug):
            if parse_iso(sug["created_at"]) > cutoff:
                continue
            if applied_here >= MAX_AUTOPILOT_APPLIES:
                counts["held"] += 1  # the rest of the backlog waits for the next tick
                continue
            target = f"{slug}/{sug['entry_key']}"
            details = {
                "title": str((sug.get("proposal") or {}).get("title", ""))[:120],
                "proposed_by": sug.get("created_by"),
                "run_id": sug.get("run_id"),
            }
            if current is None:
                current = self.entries(slug) if radar.current_version else []
            if not self._still_applies(radar, sug, current):
                self._supersede(slug, sug, audit, target, {**details, "reason": "entry changed"})
                counts["superseded"] += 1
                continue
            evidence = [str(e) for e in sug.get("evidence") or []]
            if paused or not radar.auto_apply_agent or not evidence:
                counts["held"] += 1  # left for a human reviewer
                continue
            if usable_evidence is not None:
                usable = usable_evidence(evidence)
                sites = {site for found in usable.values() for site in found}
                if (
                    set(evidence) - set(usable)
                    or len(sites) < MIN_EVIDENCE_DOMAINS
                    or not self._link_corroborated(sug, sites)
                ):
                    counts["held"] += 1  # weak, missing or single-source evidence: a human decides
                    continue
            try:
                staged, title = self._stage(radar, sug, current)
            except (ValidationError, ConflictError) as err:
                counts["held"] += 1
                log.warning("autopilot holds %s: %s", target, err.message[:200])
                continue
            try:
                claimed = self._claim_suggestion(slug, sug["entry_key"])
            except NotFoundError:
                continue  # a reviewer accepted or discarded it meanwhile
            batch.append((claimed, target, {**details, "evidence": evidence[:20]}, title))
            current = staged
            applied_here += 1
        if not batch:
            return
        # One version for the whole tick (CTR H-019): one version per proposal let a single curator run
        # add ten versions, burying the history and pushing an owner past the delete limit.
        titles = ", ".join(b[3] for b in batch[:3]) + (f" and {len(batch) - 3} more" if len(batch) > 3 else "")
        try:
            updated = self._save(
                radar,
                current or [],  # set by every staged proposal
                radar.current_version,
                "system:autopilot",
                f"applied {len(batch)} suggestion(s): {titles}"[:300],
                allow_unmatched=True,
                allow_duplicates=True,
            )
        except (ValidationError, ConflictError) as err:
            for claimed, *_ in batch:
                self._restore_suggestion(claimed)
            counts["held"] += len(batch)
            log.warning("autopilot holds %d on %s: %s", len(batch), slug, err.message[:200])
            return
        except BaseException:
            for claimed, *_ in batch:
                self._restore_suggestion(claimed)  # nothing was applied: the proposals are still open
            raise
        counts["applied"] += len(batch)
        if audit is not None:
            for _, target, details, _ in batch:
                audit.record_actor(
                    "system:autopilot",
                    "",
                    "radar.autopilot_apply",
                    target,
                    {**details, "version": updated.current_version},
                )

    def _open_suggestions(self, slug: str) -> list[dict[str, Any]]:
        items = Query.all_items(
            self._t, KeyConditionExpression=Key("pk").eq(f"RADAR#{slug}") & Key("sk").begins_with("SUG#")
        )
        return [self._suggestion_public(i) for i in items]

    def _supersede(
        self, slug: str, sug: dict[str, Any], audit: AuditSink | None, target: str, details: dict[str, Any]
    ) -> None:
        self._t.delete_item(Key={"pk": f"RADAR#{slug}", "sk": f"SUG#{sug['entry_key']}"})
        if audit is not None:  # reviewers can learn why a proposal disappeared
            audit.record_actor("system:autopilot", "", "suggestion.superseded", target, details)


def _entry_changes(before: RadarEntry | None, after: RadarEntry | None, seen: bool) -> dict[str, Any] | None:
    """What one version did to an entry: ``None`` when nothing changed."""
    if before is None and after is None:
        return None
    if before is None:
        if after is None:  # pragma: no cover - a diff of two missing rows is never asked for
            raise ValueError("a row diff needs a row on at least one side")
        return {
            "change": "restored" if seen else "added",
            "title": after.title,
            "sector": after.sector,
            "ring": after.ring,
        }
    if after is None:
        return {"change": "removed", "title": before.title, "sector": before.sector, "ring": before.ring}
    if before == after:
        return None
    out: dict[str, Any] = {"change": "changed", "title": after.title, "sector": after.sector, "ring": after.ring}
    if before.ring != after.ring:
        out["ring_from"] = before.ring
    if before.sector != after.sector:
        out["sector_from"] = before.sector
    fields = [
        f
        for f in ("title", "description", "marker", "sub_sector", "link", "node_id")
        if getattr(before, f) != getattr(after, f)
    ]
    if fields:
        out["fields"] = fields
    return out
