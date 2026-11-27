# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Digest deliveries: a person's subscriptions that send the what's-new digest to Slack or by email.

A delivery is ``USER#<sub>`` / ``DELIVERY#<id>`` in the ops table, so a person can only ever address their own.
Each keeps its own cursor (the ``until`` of the last digest it sent), separate from the in-app read point, so
reading in the app does not drop a mail and a mail does not mark the app read. An enabled delivery carries the
sparse ``due_pk`` / ``due_sk`` pair the scheduler queries on the ``due`` index; a disabled or suppressed one does
not, so the scheduler never sees it.

Where a delivery may go is decided here and in the routes, never by the request: an email goes only to the
caller's own verified address, a Slack DM only to the caller, a Slack channel only to one an administrator
listed. Everything is off unless the deployment turned the channel on (``DeliveryConfig``).
"""

from __future__ import annotations

import functools
import math
import re
import unicodedata
import zoneinfo
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, iso, parse_iso
from insights_radar.core.dynamo import Query
from insights_radar.core.errors import ConflictError, NotFoundError, RateLimitedError, ValidationError
from insights_radar.core.ids import IdGenerator
from insights_radar.core.names import NameNormalizer

MAX_DELIVERIES = 5
MAX_CHANNELS = 20
TEST_INTERVAL_SECONDS = 600
CHANNEL_LABEL_MAX = 80
ERROR_MAX = 300
TIMEZONE_MAX = 64
DUE_INDEX = "due"
DUE_PARTITION = "DELIVERY_DUE"
DUE_BATCH = 200  # deliveries one scheduler tick queues at most; the rest wait for the next tick
_WIRE = TypeDeserializer()  # an item a failed conditional write returns (ALL_OLD)

CLAIM_RETRY = timedelta(hours=1)  # a due row that could not be claimed moves this far back in the queue
SCHEDULE_RETRY = timedelta(days=1)  # and this far when its own schedule cannot be worked out (scan cycle 5)
CHANGED_MESSAGE = "this delivery was just switched {state}: reload the page and try again"
DUPLICATE_CHANNEL = "you already have a delivery to this channel: change its schedule instead"
LOOKUP_MISS_LIMIT = 7  # sends that found no Slack user, before the delivery is switched off (scan cycle 10)
# What a real send clears: every count of failed sends and the messages kept with them (scan cycle 13).
COUNT_FIELDS = ("fails", "misses", "fault", "miss_fault")
MAX_PERMANENT_FAILURES = 3  # sends that fail for the owner's own cause, with no successful send or owner edit
# between them, before the delivery is switched off (deployment faults neither count nor reset) (scan cycle 5)
CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")
# In webhook mode there is one fixed channel, the webhook's own: deliveries name it by this id.
WEBHOOK_CHANNEL = "webhook"
DELIVERY_ID_RE = re.compile(r"^[0-9a-f]{12}$")  # one pattern for every reader of a delivery id
_ZONE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,3}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
CHANNELS_KEY = {"pk": "DIGEST", "sk": "CHANNELS"}
CHANNELS_CHANGED_MESSAGE = "the channel list changed since you loaded it"
MAX_LIST_VERSION = 2**53 - 1  # the largest integer a browser holds exactly: versions a client quotes stay below it


SUPPRESSED_MESSAGE = (
    "this address bounced or reported the digest as spam, so this delivery stays off; delete it and add a new one "
    "once the address works again"
)


class DeliveryKind(StrEnum):
    SLACK_CHANNEL = "slack_channel"
    SLACK_DM = "slack_dm"
    EMAIL = "email"


class Cadence(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class DeliveryStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    SUPPRESSED = "suppressed"


class SlackMode(StrEnum):
    OFF = "off"
    BOT = "bot"
    WEBHOOK = "webhook"


class DmResolver(StrEnum):
    NONE = "none"
    EMAIL = "email"
    DIRECTORY = "directory"


class EmailMode(StrEnum):
    OFF = "off"
    SES = "ses"


def _enum[E: StrEnum](cls: type[E], raw: str, default: E) -> E:
    """An unknown value is the default (off): a typo in the deploy config never turns a channel on."""
    try:
        return cls(raw.strip().lower())
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    """The deploy option ``digestDelivery``, as the infrastructure hands it to every function (all off by default)."""

    slack_mode: SlackMode = SlackMode.OFF
    dm_resolver: DmResolver = DmResolver.NONE
    email_mode: EmailMode = EmailMode.OFF
    email_sender: str = ""
    ses_config_set: str = ""
    slack_secret_arn: str = ""
    unsub_secret_arn: str = ""
    queue_url: str = ""
    directory_endpoint: str = ""
    directory_role_arn: str = ""
    app_url: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str]) -> DeliveryConfig:
        def get(name: str) -> str:
            return env.get(name, "").strip()

        app_url = get("APP_URL").rstrip("/")
        return cls(
            slack_mode=_enum(SlackMode, get("DIGEST_SLACK_MODE"), SlackMode.OFF),
            dm_resolver=_enum(DmResolver, get("DIGEST_DM_RESOLVER"), DmResolver.NONE),
            email_mode=_enum(EmailMode, get("DIGEST_EMAIL_MODE"), EmailMode.OFF),
            email_sender=get("DIGEST_EMAIL_SENDER"),
            ses_config_set=get("DIGEST_SES_CONFIG_SET"),
            slack_secret_arn=get("DIGEST_SLACK_SECRET_ARN"),
            unsub_secret_arn=get("DIGEST_UNSUB_SECRET_ARN"),
            queue_url=get("DIGEST_QUEUE_URL"),
            directory_endpoint=get("DIGEST_DIRECTORY_ENDPOINT"),
            directory_role_arn=get("DIGEST_DIRECTORY_ROLE_ARN"),
            # Links in a digest point here; anything but an https origin gives digests without links.
            app_url=app_url if re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", app_url) else "",
        )

    @property
    def slack_channel(self) -> bool:
        return self.slack_mode is not SlackMode.OFF and bool(self.slack_secret_arn)

    @property
    def slack_dm(self) -> bool:
        """A DM needs the bot (a webhook posts to one fixed channel) and a way to find the person's Slack ID; the
        directory resolver fails closed until both its endpoint and role are configured."""
        if self.slack_mode is not SlackMode.BOT or not self.slack_secret_arn:
            return False
        if self.dm_resolver is DmResolver.DIRECTORY:
            return bool(self.directory_endpoint and self.directory_role_arn)
        return self.dm_resolver is DmResolver.EMAIL

    @property
    def email(self) -> bool:
        # The unsubscribe link needs the key and the app's address: no mail goes out without one.
        return (
            self.email_mode is EmailMode.SES
            and bool(self.email_sender)
            and bool(self.unsub_secret_arn)
            and bool(self.app_url)
        )

    @property
    def any(self) -> bool:
        return self.slack_channel or self.slack_dm or self.email

    def allows(self, kind: DeliveryKind) -> bool:
        return {
            DeliveryKind.SLACK_CHANNEL: self.slack_channel,
            DeliveryKind.SLACK_DM: self.slack_dm,
            DeliveryKind.EMAIL: self.email,
        }[kind]


@functools.cache
def _zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


class Schedule:
    """When a delivery is due: every day, or one weekday, at a whole hour of an IANA time zone."""

    @staticmethod
    def zone(name: str) -> zoneinfo.ZoneInfo:
        """Only a name of the time-zone database: ZoneInfo would also open a file path."""
        if not isinstance(name, str) or len(name) > TIMEZONE_MAX or not _ZONE_RE.fullmatch(name):
            raise ValidationError("timezone must be an IANA time zone name such as Europe/London")
        if name not in _zones():
            raise ValidationError(f"unknown time zone {name!r}")
        return zoneinfo.ZoneInfo(name)

    @staticmethod
    def next_at(cadence: Cadence, weekday: int | None, hour: int, zone: zoneinfo.ZoneInfo, after: datetime) -> datetime:
        """The first ``hour``:00 local time (on ``weekday`` for a weekly one) strictly after ``after``.

        Days are counted on the local calendar, never as 24-hour steps, so a DST change neither skips nor
        repeats a day. An hour that does not exist that day (clocks go forward) is sent at the moment the
        clocks jump; an hour that happens twice (clocks go back) is sent at its first occurrence only."""
        local = after.astimezone(zone).date()
        for offset in range(9):
            day = local + timedelta(days=offset)
            if cadence is Cadence.WEEKLY and day.weekday() != weekday:
                continue
            when = _wall(day, hour, zone)
            if when > after:
                return when
        raise AssertionError("a daily or weekly schedule always has a time within 9 days")  # pragma: no cover


def _wall(day: date, hour: int, zone: zoneinfo.ZoneInfo) -> datetime:
    """``hour``:00 on ``day`` in ``zone``, as UTC. fold=0 picks the first of a repeated hour and the offset before
    a skipped one (which lands on the jump: 02:00 that does not exist is 03:00 new time)."""
    return datetime.combine(day, time(hour), tzinfo=zone).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Delivery:
    id: str
    subject: str
    username: str
    kind: DeliveryKind
    target_label: str
    cadence: Cadence
    hour: int
    timezone: str
    enabled: bool = True
    channel_id: str | None = None
    # The verified address an email goes to, or the key a DM is resolved by (the email or the alias).
    address: str | None = None
    weekday: int | None = None
    next_at: str | None = None
    cursor: str | None = None
    last_sent_at: str | None = None
    last_status: DeliveryStatus | None = None
    last_error: str | None = None
    test_at: str | None = None
    suppressed: bool = False
    created_at: str = ""
    fails: int = 0  # sends in a row that failed for the owner's own cause (switched off at MAX_PERMANENT_FAILURES)
    misses: int = 0  # sends in a row that found no Slack user (switched off at LOOKUP_MISS_LIMIT), counted apart
    fault: str | None = None  # the message of the last owner-cause failure, kept while `fails` stands (cycle 11)
    miss_fault: str | None = None  # the message of the last lookup miss, kept while `misses` stands (scan cycle 13)
    stop_reason: str | None = None  # why a bounce or complaint stopped it, never written over (scan cycle 15)

    @property
    def sendable(self) -> bool:
        """On, and not stopped by a bounce or complaint (scan cycle 15)."""
        return self.enabled and not self.suppressed

    @property
    def due(self) -> bool:
        return self.sendable and bool(self.next_at)

    def to_public(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "target_label": self.target_label,
            "channel_id": self.channel_id,
            "cadence": self.cadence.value,
            "weekday": self.weekday,
            "hour": self.hour,
            "timezone": self.timezone,
            "enabled": self.enabled,
            "next_at": self.next_at if self.due else None,
            "last_sent_at": self.last_sent_at,
            "last_status": self.last_status.value if self.last_status else None,
            "last_error": self.last_error,
            # A bounce or complaint stopped it for good: shown from this, not from last_status, which a send that
            # was already running may write over (scan cycle 14).
            "suppressed": self.suppressed,
            "stop_reason": self.stop_reason if self.suppressed else None,
        }
        return body

    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "pk": f"USER#{self.subject}",
            "sk": f"DELIVERY#{self.id}",
            "kind": "delivery",
            "id": self.id,
            "subject": self.subject,
            "username": self.username,
            "channel": self.kind.value,
            "target_label": self.target_label,
            "cadence": self.cadence.value,
            "hour": self.hour,
            "timezone": self.timezone,
            "enabled": self.enabled,
            "suppressed": self.suppressed,
            "created_at": self.created_at,
        }
        optional = {
            "channel_id": self.channel_id,
            "address": self.address,
            "weekday": self.weekday,
            "next_at": self.next_at,
            "cursor": self.cursor,
            "last_sent_at": self.last_sent_at,
            "last_status": self.last_status.value if self.last_status else None,
            "last_error": self.last_error,
            "test_at": self.test_at,
        }
        item.update({k: v for k, v in optional.items() if v is not None})
        if self.due:
            item.update(_due_keys(self.subject, self.id, str(self.next_at)))
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Delivery:
        def opt(name: str) -> str | None:
            value = item.get(name)
            return str(value) if value is not None else None

        weekday = item.get("weekday")
        status = item.get("last_status")
        return cls(
            id=str(item["id"]),
            subject=str(item["subject"]),
            username=str(item.get("username") or item["subject"]),
            kind=DeliveryKind(str(item["channel"])),
            target_label=str(item.get("target_label", "")),
            cadence=Cadence(str(item["cadence"])),
            hour=int(item["hour"]),
            timezone=str(item["timezone"]),
            enabled=bool(item.get("enabled", False)),
            channel_id=opt("channel_id"),
            address=opt("address"),
            weekday=int(weekday) if weekday is not None else None,
            next_at=opt("next_at"),
            cursor=opt("cursor"),
            last_sent_at=opt("last_sent_at"),
            last_status=DeliveryStatus(str(status)) if status else None,
            last_error=opt("last_error"),
            test_at=opt("test_at"),
            suppressed=bool(item.get("suppressed", False)),
            created_at=str(item.get("created_at", "")),
            fails=int(item.get("fails", 0) or 0),
            misses=int(item.get("misses", 0) or 0),
            fault=opt("fault"),
            miss_fault=opt("miss_fault"),
            stop_reason=opt("stop_reason"),
        )


def _due_keys(subject: str, delivery_id: str, next_at: str) -> dict[str, str]:
    return {"due_pk": DUE_PARTITION, "due_sk": f"{next_at}#{subject}#{delivery_id}"}


def _hash_like(ch: str) -> bool:
    """'#' in any form: whatever NFKC turns into '#' (full-width, small), and the music sharp sign (scan cycle 29)."""
    return ch == "\u266f" or unicodedata.normalize("NFKC", ch) == "#"


def channel_name(label: str) -> str:
    """A channel label as stored and shown after '#': the display form other names get (NFC, single spaces, no
    invisible format characters), then without what a person may type before the name: any form of '#', spaces,
    and a combining mark that would sit on the '#' the app adds (scan cycles 23 to 29)."""
    name = NameNormalizer.display(label)
    start = 0
    while start < len(name) and (
        _hash_like(name[start]) or name[start].isspace() or unicodedata.category(name[start]).startswith("M")
    ):
        start += 1
    return name[start:]


def _stored_name(label: str, channel_id: str) -> str:
    """A stored label read back with the same rule as a saved one; the channel ID when nothing readable is left."""
    name = channel_name(label)
    return name if has_name(name) else channel_id


def has_name(name: str) -> bool:
    """Whether a channel name shows something a person can read: at least one letter or digit."""
    return any(ch.isalnum() for ch in name)


@dataclass(frozen=True, slots=True)
class DigestChannel:
    id: str
    label: str

    def to_public(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label}


@dataclass(frozen=True, slots=True)
class Target:
    """Where a new delivery goes, as the route decided it from the caller's identity or the admin list."""

    kind: DeliveryKind
    label: str
    channel_id: str | None = None
    address: str | None = None


@dataclass(frozen=True, slots=True)
class DueDelivery:
    subject: str
    id: str
    next_at: str


def clean_error(text: str) -> str:
    """An error as stored and shown: one line, no control characters, bounded."""
    return _CONTROL.sub(" ", str(text)).strip()[:ERROR_MAX]


def _code(err: ClientError) -> str:
    return str(err.response.get("Error", {}).get("Code", ""))


class DeliveryService:
    def __init__(self, table: Any, clock: Clock, ids: IdGenerator | None = None) -> None:
        self._t = table
        self._clock = clock
        self._ids = ids or IdGenerator(clock)

    @staticmethod
    def _key(subject: str, delivery_id: str) -> dict[str, str]:
        return {"pk": f"USER#{subject}", "sk": f"DELIVERY#{delivery_id}"}

    # ---- reads ------------------------------------------------------------------------------------

    def for_user(self, subject: str, *, consistent: bool = False) -> list[Delivery]:
        rows = Query.all_items(
            self._t,
            KeyConditionExpression=Key("pk").eq(f"USER#{subject}") & Key("sk").begins_with("DELIVERY#"),
            ConsistentRead=consistent,
        )
        return sorted((Delivery.from_item(r) for r in rows), key=lambda d: (d.created_at, d.id))

    def get(self, subject: str, delivery_id: str) -> Delivery:
        """The caller's own delivery; any other id, someone else's included, is not found."""
        if not DELIVERY_ID_RE.fullmatch(delivery_id or ""):
            raise NotFoundError("delivery not found")
        item = self._t.get_item(Key=self._key(subject, delivery_id), ConsistentRead=True).get("Item")
        if not item:
            raise NotFoundError("delivery not found")
        return Delivery.from_item(item)

    def find(self, subject: str, delivery_id: str) -> Delivery | None:
        try:
            return self.get(subject, delivery_id)
        except NotFoundError:
            return None

    # ---- writes by the owner ------------------------------------------------------------------------

    @staticmethod
    def check_schedule(cadence: str, weekday: int | None, hour: int, timezone: str) -> tuple[Cadence, int | None]:
        try:
            kind = Cadence(cadence)
        except ValueError as err:
            raise ValidationError('cadence must be "daily" or "weekly"') from err
        if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
            raise ValidationError("hour must be a whole hour 0-23")
        Schedule.zone(timezone)
        if kind is Cadence.WEEKLY:
            if weekday is None or isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
                raise ValidationError("a weekly delivery needs weekday 0 (Monday) to 6 (Sunday)")
            return kind, weekday
        return kind, None

    def _next(self, cadence: Cadence, weekday: int | None, hour: int, timezone: str, after: datetime) -> str:
        return iso(Schedule.next_at(cadence, weekday, hour, Schedule.zone(timezone), after))

    def create(
        self,
        subject: str,
        username: str,
        target: Target,
        *,
        cadence: str,
        weekday: int | None,
        hour: int,
        timezone: str,
        enabled: bool = True,
    ) -> Delivery:
        kind, day = self.check_schedule(cadence, weekday, hour, timezone)
        before = self.for_user(subject)
        if len(before) >= MAX_DELIVERIES:
            raise ValidationError(f"at most {MAX_DELIVERIES} deliveries: delete one first")
        if target.kind is DeliveryKind.SLACK_CHANNEL and any(
            d.kind is DeliveryKind.SLACK_CHANNEL and d.channel_id == target.channel_id for d in before
        ):
            # One delivery per person per channel: the channel does not get the same digest twice (scan cycle 2).
            raise ConflictError(DUPLICATE_CHANNEL, {"code": "duplicate_channel"})
        now = self._clock.now()
        delivery = Delivery(
            id=self._ids.short(12),
            subject=subject,
            username=username,
            kind=target.kind,
            target_label=target.label,
            cadence=kind,
            hour=hour,
            timezone=timezone,
            enabled=enabled,
            channel_id=target.channel_id,
            address=target.address,
            weekday=day,
            next_at=self._next(kind, day, hour, timezone, now),
            created_at=iso(now),
        )
        self._t.put_item(Item=delivery.to_item(), ConditionExpression="attribute_not_exists(pk)")
        # Two creates at once can both pass the count. Each counts what it saw before it wrote, plus the other
        # new ones that rank ahead of it (older, then by id); only one with five ahead gives its place back, so
        # the first of two racing creates still succeeds (scan cycle 1, L5).
        seen = {d.id for d in before}
        rank = (delivery.created_at, delivery.id)
        ahead = [
            d
            for d in self.for_user(subject, consistent=True)
            if d.id != delivery.id and (d.id in seen or (d.created_at, d.id) < rank)
        ]
        if len(ahead) >= MAX_DELIVERIES:
            self._t.delete_item(Key=self._key(subject, delivery.id))
            raise ValidationError(f"at most {MAX_DELIVERIES} deliveries: delete one first")
        if target.kind is DeliveryKind.SLACK_CHANNEL and any(
            d.kind is DeliveryKind.SLACK_CHANNEL and d.channel_id == target.channel_id for d in ahead
        ):
            # Two creates for one channel at once (a double click, two tabs): the one ranked first keeps it, the
            # other gives its place back, by the same consistent read as the cap (scan cycle 3).
            self._t.delete_item(Key=self._key(subject, delivery.id))
            raise ConflictError(DUPLICATE_CHANNEL, {"code": "duplicate_channel"})
        return delivery

    def update(self, subject: str, delivery_id: str, changes: dict[str, Any]) -> Delivery:
        """Change the schedule or switch it on or off; the target never changes. Only these fields are written,
        so a send finishing at the same time keeps its cursor and status."""
        current = self.get(subject, delivery_id)
        hour = changes.get("hour", current.hour)
        timezone = changes.get("timezone", current.timezone)
        enabled = changes.get("enabled", current.enabled)
        if not isinstance(enabled, bool):
            raise ValidationError("enabled must be true or false")
        # Switching off without changing the schedule never needs it worked out, whatever else the body repeats: a
        # delivery whose zone has left tzdata can still be paused from the app (scan cycles 6 and 7).
        same_schedule = (
            changes.get("cadence", current.cadence.value) == current.cadence.value
            and changes.get("weekday", current.weekday) == current.weekday
            and hour == current.hour
            and timezone == current.timezone
        )
        pause_only = same_schedule and not enabled
        if pause_only:
            kind, day = current.cadence, current.weekday
        else:
            kind, day = self.check_schedule(
                changes.get("cadence", current.cadence.value), changes.get("weekday", current.weekday), hour, timezone
            )
        merged = replace(current, hour=hour, timezone=timezone, enabled=enabled)
        if merged.enabled and current.suppressed:
            raise ConflictError(SUPPRESSED_MESSAGE, {"code": "suppressed"})
        merged = replace(
            merged,
            cadence=kind,
            weekday=day,
            next_at=(
                current.next_at
                if pause_only
                else self._next(kind, day, merged.hour, merged.timezone, self._clock.now())
            ),
        )
        values: dict[str, Any] = {
            ":c": merged.cadence.value,
            ":h": merged.hour,
            ":z": merged.timezone,
            ":e": merged.enabled,
            ":n": merged.next_at,
        }
        # HOUR and TIMEZONE are DynamoDB reserved words: every schedule field goes by an alias.
        names = {"#c": "cadence", "#h": "hour", "#z": "timezone", "#e": "enabled", "#n": "next_at", "#w": "weekday"}
        sets = ["#c = :c", "#h = :h", "#z = :z", "#e = :e", "#n = :n"]
        # An owner who changed it starts a fresh count of their own failures; a schedule edit cannot fix a missing
        # Slack user, so the lookup-miss count stays and still switches it off at LOOKUP_MISS_LIMIT (scan cycle 12),
        # unless the owner turns it back on after that limit switched it off (scan cycles 13 and 14).
        removes: list[str] = ["fails", "fault"]
        if merged.enabled and not current.enabled and current.misses >= LOOKUP_MISS_LIMIT:
            removes += ["misses", "miss_fault"]
        if merged.weekday is None:
            removes.append("#w")
        else:
            sets.append("#w = :w")
            values[":w"] = merged.weekday
        if merged.due:
            sets += ["due_pk = :dp", "due_sk = :ds"]
            keys = _due_keys(subject, delivery_id, str(merged.next_at))
            values.update({":dp": keys["due_pk"], ":ds": keys["due_sk"]})
        else:
            removes += ["due_pk", "due_sk"]
        expression = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
        # A switch-off that lands between the read and this write still wins, and the owner is told (scan cycle 14).
        condition = "attribute_exists(pk) AND #e = :was"
        values[":was"] = current.enabled
        if merged.enabled:  # a bounce that lands between the read and this write still wins
            condition += " AND suppressed = :f"
            values[":f"] = False
        try:
            self._t.update_item(
                Key=self._key(subject, delivery_id),
                UpdateExpression=expression,
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise
            fresh = self.get(subject, delivery_id)  # deleted meanwhile: not found
            if fresh.suppressed:  # a bounce or complaint got there first: always say so (scan cycle 16)
                raise ConflictError(SUPPRESSED_MESSAGE, {"code": "suppressed"}) from err
            if set(changes) <= {"enabled"} and fresh.enabled == merged.enabled:
                return fresh  # already as asked: a pause that a switch-off got to first (scan cycle 15)
            state = "on" if fresh.enabled else "off"
            raise ConflictError(CHANGED_MESSAGE.format(state=state), {"code": "changed"}) from err
        return merged

    def delete(self, subject: str, delivery_id: str) -> Delivery:
        current = self.get(subject, delivery_id)
        self._t.delete_item(Key=self._key(subject, delivery_id))
        return current

    def give_back_test(self, subject: str, delivery_id: str, stamp: str | None) -> None:
        """Undo a test stamp when the test failed before its send was attempted, so nothing went out (scan cycles
        21 to 23); only our own stamp, so a later test's is kept."""
        if not stamp:
            return
        try:
            self._t.update_item(
                Key=self._key(subject, delivery_id),
                UpdateExpression="REMOVE test_at",
                ConditionExpression="test_at = :t",
                ExpressionAttributeValues={":t": stamp},
            )
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise

    def take_test(self, subject: str, delivery_id: str, current: Delivery | None = None) -> Delivery:
        """One "send test now" per delivery per ``TEST_INTERVAL_SECONDS``: an atomic, conditional stamp. ``current``
        is the row the caller has just read, if any (the stamp itself is conditional, so it is not read again)."""
        if current is None:
            current = self.get(subject, delivery_id)
        now = self._clock.now()
        cutoff = iso(now - timedelta(seconds=TEST_INTERVAL_SECONDS))
        try:
            self._t.update_item(
                Key=self._key(subject, delivery_id),
                UpdateExpression="SET test_at = :now",
                ConditionExpression="attribute_exists(pk) AND (attribute_not_exists(test_at) OR test_at <= :cut)",
                ExpressionAttributeValues={":now": iso(now), ":cut": cutoff},
            )
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise
            latest = self.get(subject, delivery_id)
            last = parse_iso(latest.test_at) if latest.test_at else now
            wait = max(1, TEST_INTERVAL_SECONDS - int((now - last).total_seconds()))
            raise RateLimitedError(
                "one test send per delivery every 10 minutes", {"retry_after": wait, "limit_per_10_minutes": 1}
            ) from err
        return replace(current, test_at=iso(now))

    # ---- admin channel list -------------------------------------------------------------------------

    @staticmethod
    def _parse_channels(item: dict[str, Any]) -> tuple[list[DigestChannel], int]:
        """The stored list, each name normalised as it is shown, and its version (0 before c31 or when unset)."""
        raw = item.get("channels")
        version = item.get("version", 0)
        out: list[DigestChannel] = []
        seen: set[str] = set()
        for c in raw if isinstance(raw, list) else []:
            if isinstance(c, dict) and isinstance(c.get("id"), str) and CHANNEL_ID_RE.fullmatch(c["id"]):
                cid = c["id"]
                if cid in seen:  # a hand-edited list may repeat an ID: the first one counts (c35, c36)
                    continue
                seen.add(cid)
                label = c.get("label")  # a hand-edited label that is not text falls back to the ID (c37)
                out.append(DigestChannel(cid, _stored_name(label if isinstance(label, str) else cid, cid)))
        number = isinstance(version, (int, Decimal)) and not isinstance(version, bool)
        # A version edited by hand (a string, a fraction, a negative) still reads as a whole number the next save
        # can quote: the condition matches the range [n, n + 1), and the save writes n + 1 (scan cycle 32).
        whole = max(0, math.floor(version)) if number else 0
        # A number no client can quote reads as 0, which the 0 condition matches; the ranges never overlap (c33, c34).
        return out, whole if whole < MAX_LIST_VERSION else 0

    def stored_channels(self) -> tuple[list[DigestChannel], int]:
        """The list as the administrator saved it, and its version: what the admin form edits (scan cycle 31)."""
        return self._parse_channels(self._t.get_item(Key=CHANNELS_KEY, ConsistentRead=True).get("Item") or {})

    @staticmethod
    def clashes(channels: list[DigestChannel]) -> list[list[str]]:
        """The IDs of each group of two or more channels whose names read the same, in list order (a list saved
        before c29 can hold them): subscribers see all but the first as '<name> (<id>)', and the form flags every
        member while two or more still match (scan cycles 31 to 35)."""
        groups: dict[str, list[str]] = {}
        for c in channels:
            groups.setdefault(NameNormalizer.lookalike_key(c.label), []).append(c.id)
        return [ids for ids in groups.values() if len(ids) > 1]

    def channels(self) -> list[DigestChannel]:
        """The list as subscribers see it: a name that reads the same as an earlier one shows its channel ID, so
        no two picker entries look alike (scan cycles 30, 31 and 34; the admin form gets the stored names)."""
        listed, _ = self.stored_channels()
        clash = {cid for group in self.clashes(listed) for cid in group[1:]}
        # '<name> (<id>)': the ID makes it unique, and no other label can read the same (scan cycle 34).
        return [DigestChannel(c.id, f"{c.label} ({c.id})") if c.id in clash else c for c in listed]

    def channel(self, channel_id: str) -> DigestChannel | None:
        return next((c for c in self.channels() if c.id == channel_id), None)

    @staticmethod
    def check_channels(raw: list[Any]) -> list[DigestChannel]:
        if len(raw) > MAX_CHANNELS:
            raise ValidationError(f"at most {MAX_CHANNELS} channels")
        out: list[DigestChannel] = []
        for i, c in enumerate(raw):
            if not isinstance(c, dict) or set(c) - {"id", "label"}:
                raise ValidationError(f"channel {i + 1}: an object with id and label")
            cid, label = c.get("id"), c.get("label")
            if not isinstance(cid, str) or not CHANNEL_ID_RE.fullmatch(cid):
                raise ValidationError(f"channel {i + 1}: id must be a Slack channel ID such as C0123456789")
            if not isinstance(label, str) or not label.strip() or len(label) > 4 * CHANNEL_LABEL_MAX:
                raise ValidationError(f"channel {i + 1}: label must be 1-{CHANNEL_LABEL_MAX} characters")
            if _CONTROL.search(label):
                raise ValidationError(f"channel {i + 1}: label must not contain control characters")
            if any(o.id == cid for o in out):
                raise ValidationError(f"channel {cid} is listed twice")
            name = channel_name(label)  # shown as '#<label>', so a typed '#' goes (scan cycles 23 to 25)
            if not has_name(name):
                raise ValidationError(f"channel {i + 1}: label needs a letter or digit (the '#' is added for you)")
            if len(name) > CHANNEL_LABEL_MAX:  # the name as stored, after normalising (scan cycle 28)
                raise ValidationError(f"channel {i + 1}: label must be 1-{CHANNEL_LABEL_MAX} characters")
            key = NameNormalizer.lookalike_key(name)
            twin = next((j for j, o in enumerate(out) if NameNormalizer.lookalike_key(o.label) == key), None)
            if twin is not None:  # c29; the earlier row is named as it is stored (c32)
                raise ValidationError(f"channel {i + 1}: reads the same as channel {twin + 1} (#{out[twin].label})")
            out.append(DigestChannel(cid, name))
        return out

    def replace_channels(
        self, channels: list[DigestChannel], actor: str, version: int
    ) -> tuple[list[DigestChannel], int]:
        """Store ``channels`` and return the list this write replaced and the new version. The write happens only
        if the stored version is still ``version`` (the one the caller loaded), else a 409, so two saves cannot
        silently undo each other and no version number is issued twice (scan cycles 31 to 33)."""
        try:
            return self._put_channels(channels, actor, version)
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise
            raise ConflictError(CHANNELS_CHANGED_MESSAGE, {"code": "channels_changed"}) from err

    def _put_channels(
        self, channels: list[DigestChannel], actor: str, expected: int
    ) -> tuple[list[DigestChannel], int]:
        if expected == 0:  # no list yet, one from before c31, or a version no client can quote (c32, c33)
            cond = "attribute_not_exists(#v) OR NOT attribute_type(#v, :n) OR #v < :next OR #v >= :max"
            values: dict[str, Any] = {":n": "N", ":next": 1, ":max": MAX_LIST_VERSION}
        else:
            cond = "#v >= :v AND #v < :next"
            values = {":v": expected, ":next": expected + 1}
        res = self._t.put_item(
            Item={
                **CHANNELS_KEY,
                "kind": "digest_channels",
                "channels": [c.to_public() for c in channels],
                "version": expected + 1,
                "updated_by": actor,
                "updated_at": iso(self._clock.now()),
            },
            ConditionExpression=cond,
            ExpressionAttributeNames={"#v": "version"},
            ExpressionAttributeValues=values,
            ReturnValues="ALL_OLD",
        )
        before, _ = self._parse_channels(res.get("Attributes") or {})
        return before, expected + 1

    # ---- the scheduler and the sender ---------------------------------------------------------------

    def due(self, now: datetime, limit: int = DUE_BATCH) -> list[DueDelivery]:
        res = self._t.query(
            IndexName=DUE_INDEX,
            KeyConditionExpression=Key("due_pk").eq(DUE_PARTITION) & Key("due_sk").lte(f"{iso(now)}#~"),
            Limit=limit,
        )
        out: list[DueDelivery] = []
        for item in res.get("Items", []):
            next_at, _, rest = str(item.get("due_sk", "")).partition("#")
            subject, _, delivery_id = rest.rpartition("#")
            if subject and DELIVERY_ID_RE.fullmatch(delivery_id):
                out.append(DueDelivery(subject, delivery_id, next_at))
        return out

    def claim(self, due: DueDelivery, now: datetime) -> str | None:
        """Move a due delivery on to its next time, if nobody else did: the new time, or None when it changed
        (the owner edited, disabled or deleted it, or another tick took it)."""
        current = self.find(due.subject, due.id)
        if current is None or not current.due or current.next_at != due.next_at:
            return None
        nxt = self._next(current.cadence, current.weekday, current.hour, current.timezone, now)
        return nxt if self._move(due, due.next_at, nxt) else None

    def take_send(self, subject: str, delivery_id: str, due: str, token: str) -> bool:
        """One send per scheduled time. The first queue message for ``due`` takes it (``token`` names that
        message); another message for the same time is refused, so a delivery the scheduler queued twice (SQS
        stored the message but the client saw a failure, and the time was put back) sends once. The same message
        received again after a transient failure keeps it, and a message for a later time takes it over. A message
        for an earlier time (an old one redriven from the dead-letter queue) is refused: due times are ISO text, so
        ``send_slot < :due`` holds only when the slot's time is earlier (scan cycle 38)."""
        mine = f"{due}|{token}"
        try:
            self._t.update_item(
                Key=self._key(subject, delivery_id),
                UpdateExpression="SET send_slot = :m",
                ConditionExpression=(
                    "attribute_exists(pk) AND (attribute_not_exists(send_slot) OR send_slot = :m OR send_slot < :due)"
                ),
                ExpressionAttributeValues={":m": mine, ":due": f"{due}|"},
            )
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise
            return False
        return True

    def defer(self, due: DueDelivery, now: datetime, wait: timedelta = CLAIM_RETRY) -> bool:
        """Move a due row whose claim failed back by ``CLAIM_RETRY``, so the rows behind it at the head of the due
        index are queued (the scheduler reads only the oldest ``DUE_BATCH``) (scan cycle 2)."""
        return self._move(due, due.next_at, iso(now + wait))

    def unclaim(self, due: DueDelivery, claimed: str) -> None:
        """Put the time back after the queue refused the message, so the next tick tries again."""
        self._move(due, claimed, due.next_at)

    def _move(self, due: DueDelivery, before: str, after: str) -> bool:
        keys = _due_keys(due.subject, due.id, after)
        try:
            self._t.update_item(
                Key=self._key(due.subject, due.id),
                UpdateExpression="SET next_at = :n, due_pk = :dp, due_sk = :ds",
                ConditionExpression="next_at = :o AND enabled = :t AND suppressed = :f",
                ExpressionAttributeValues={
                    ":n": after,
                    ":o": before,
                    ":t": True,
                    ":f": False,
                    ":dp": keys["due_pk"],
                    ":ds": keys["due_sk"],
                },
            )
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise
            return False
        return True

    def advance(
        self, d: Delivery, cursor: str, status: DeliveryStatus, *, retry: bool = True, token: str | None = None
    ) -> bool | None:
        """Record a finished send and move the cursor to ``cursor``, only if it is still the one the digest was
        built from: a second sender that got there first keeps its place (False: the cursor was not moved). None:
        the delivery was deleted meanwhile, as the failed write itself says (scan cycle 18). ``token`` names this
        send: written with the cursor, it lets a retry whose earlier answer was lost know its own write (c20)."""
        values: dict[str, Any] = {":c": cursor}
        head = "SET #cur = :c"
        if token:  # this send's mark, written with the cursor (scan cycles 20 and 21)
            head += ", advanced_by = :tok"
            values[":tok"] = token
        if d.cursor is None:
            condition = "attribute_exists(pk) AND attribute_not_exists(#cur)"
        else:
            condition = "attribute_exists(pk) AND #cur = :old"
            values[":old"] = d.cursor
        pending = False
        if status is DeliveryStatus.OK:
            # Slack or SES took it: the error and every count go.
            values.update({":s": status.value, ":at": iso(self._clock.now())})
            expression = head + ", last_status = :s, last_sent_at = :at REMOVE last_error, " + ", ".join(COUNT_FIELDS)
        elif d.fails > 0 or d.misses > 0:
            # An empty day tried nothing: a counted fault still stands, shown as failed with its own message, not
            # whatever was written last (scan cycles 9 to 11). Only while the count stands (scan cycle 11).
            pending = True
            values[":s"] = DeliveryStatus.ERROR.value
            expression = head + ", last_status = :s"
            # Every writer sets or clears a count together with its message, so one of the two pairs is there.
            if d.fails > 0:
                shown, still = d.fault, "attribute_exists(fails)"
            else:
                shown, still = d.miss_fault, "attribute_exists(misses)"
            if shown:
                expression += ", last_error = :fe"
                values[":fe"] = shown
            # The count whose message is shown must still stand: an owner edit that cleared it wins (scan cycle 14).
            condition += " AND " + still
        else:
            # An empty day with no count: any other error, a passing one or one already fixed, goes (scan cycle 10).
            values[":s"] = status.value
            expression = head + ", last_status = :s REMOVE last_error"
        try:
            self._t.update_item(
                Key=self._key(d.subject, d.id),
                UpdateExpression=expression,
                ConditionExpression=condition,
                ExpressionAttributeNames={"#cur": "cursor"},
                ExpressionAttributeValues=values,
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise
            raw = cast(dict[str, Any] | None, err.response.get("Item"))  # in the wire format
            old = {k: _WIRE.deserialize(v) for k, v in raw.items()} if raw else None
            if not old:
                return None  # deleted meanwhile: nothing to record, and no late write brings it back
            if token and old.get("advanced_by") == token:
                # Our own earlier attempt (or its retry from the owner-edit path) went through and only its answer
                # was lost: no other sender writes this send's token (scan cycles 19 and 20).
                return True
            if pending and retry:
                # Perhaps an owner edit cleared that count meanwhile: try again from the row the failed write saw,
                # but only if the cursor is still this digest's; otherwise another sender got there first
                # (scan cycles 12 to 18).
                fresh = Delivery.from_item(old)
                if fresh.cursor == d.cursor:
                    return self.advance(fresh, cursor, status, retry=False, token=token)
                return False
            if status is DeliveryStatus.OK:  # an empty day writes nothing over another sender's outcome
                self.record(d.subject, d.id, status, None, sent=True)
            return False
        return True

    def record(
        self, subject: str, delivery_id: str, status: DeliveryStatus, error: str | None, *, sent: bool = False
    ) -> None:
        """Write a send's outcome on the delivery, if it still exists (a deleted one is not brought back)."""
        values: dict[str, Any] = {":s": status.value}
        sets = ["last_status = :s"]
        removes: list[str] = []
        if error:
            sets.append("last_error = :e")
            values[":e"] = clean_error(error)
        else:
            removes.append("last_error")
        if sent:  # Slack or SES took it: the counts of failed sends start again (scan cycle 11)
            sets.append("last_sent_at = :at")
            values[":at"] = iso(self._clock.now())
            removes += list(COUNT_FIELDS)
        expression = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
        try:
            self._t.update_item(
                Key=self._key(subject, delivery_id),
                UpdateExpression=expression,
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues=values,
            )
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise

    def fail(self, subject: str, delivery_id: str, error: str, *, lookup: bool = False) -> bool:
        """Record a scheduled send that failed for good. After ``MAX_PERMANENT_FAILURES`` in a row for the owner's
        own cause, or ``LOOKUP_MISS_LIMIT`` that found no Slack user (``lookup``), each counted on its own (scan
        cycle 11), the delivery is switched off with the reason on it, for its owner to see, instead of failing at
        every send (scan cycle 2). True when it was switched off."""
        counter, message, limit = (
            ("misses", "miss_fault", LOOKUP_MISS_LIMIT) if lookup else ("fails", "fault", MAX_PERMANENT_FAILURES)
        )
        try:
            res = self._t.update_item(
                Key=self._key(subject, delivery_id),
                UpdateExpression=f"SET last_status = :s, last_error = :e, {message} = :e ADD {counter} :one",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={":s": DeliveryStatus.ERROR.value, ":e": clean_error(error), ":one": 1},
                ReturnValues="UPDATED_NEW",
            )
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise
            return False
        if int(res.get("Attributes", {}).get(counter, 0)) < limit:
            return False
        reason = f"switched off after {limit} failed sends in a row: {error}"
        return self.switch_off(subject, delivery_id, suppress=False, reason=reason)

    def switch_off(self, subject: str, delivery_id: str, *, suppress: bool, reason: str | None = None) -> bool:
        """Turn a delivery off: an unsubscribe link (``suppress`` False), failed sends (``suppress`` False with a
        ``reason``, shown to the owner) or an SES bounce or complaint (True, and it stays off). False when there is
        no such delivery."""
        values: dict[str, Any] = {":f": False}
        sets = ["enabled = :f"]
        if suppress:
            sets += ["suppressed = :t", "last_status = :s", "last_error = :e", "stop_reason = :e"]
            values.update(
                {":t": True, ":s": DeliveryStatus.SUPPRESSED.value, ":e": clean_error(reason or "suppressed")}
            )
        elif reason:
            sets += ["last_status = :s", "last_error = :e"]
            values.update({":s": DeliveryStatus.ERROR.value, ":e": clean_error(reason)})
        try:
            self._t.update_item(
                Key=self._key(subject, delivery_id),
                UpdateExpression="SET " + ", ".join(sets) + " REMOVE due_pk, due_sk",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues=values,
            )
        except ClientError as err:
            if _code(err) != "ConditionalCheckFailedException":
                raise
            return False
        return True
