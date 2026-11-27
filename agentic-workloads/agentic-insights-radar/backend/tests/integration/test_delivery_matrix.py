# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Digest delivery scenario matrix: the API, the scheduler, the sender and the SES feedback worker end to end
on moto, with Slack, the directory lookup, SES and SQS faked (no network). See ``delivery_matrix_support`` for the
world.

Areas (each test id names its scenario): options and create by kind x role x mode; create and patch validation;
schedules and DST; ownership and limits; the scheduler; the sender (outcomes, transient and permanent errors,
live subscriber checks); visibility; send test now; unsubscribe; SES feedback; rendering; the admin channel
list; deploy config parsing. Bugs found are xfail(strict=True) with an id from matrix-bugs.md.
"""

from __future__ import annotations

import base64
import html
import itertools
import json
import re
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpcore
import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError

from insights_radar.core.clock import FixedClock, iso, parse_iso
from insights_radar.core.errors import ConflictError, ValidationError
from insights_radar.graph.registry import NodeType
from insights_radar.identity.principal import Role
from insights_radar.insights.delivery import (
    LOOKUP_MISS_LIMIT,
    DeliveryKind,
    DeliveryStatus,
    DigestChannel,
    DueDelivery,
    Target,
)
from insights_radar.insights.delivery_render import slack_escape
from insights_radar.insights.delivery_transport import (
    SLACK_AUTH,
    SLACK_PERMANENT,
    HttpcoreClient,
    HttpResponse,
    SlackClient,
    SlackWebhook,
)
from tests.integration.delivery_matrix_support import (
    APP,
    BOT_VALUE,
    CHANNEL,
    CHANNEL_LABEL,
    DEFAULT_SCHEDULE,
    DIRECTORY_ROLE,
    KINDS,
    MODES,
    PUBLIC_KEYS,
    QUEUE,
    ROLE_NAMES,
    WEBHOOK,
    World,
    build_world,
    config,
    oracle_next,
)
from tests.integration.harness import DIGITS, SLACK_DM

# A fake value the stack would hold, passed by name.
NOT_A_REAL_VALUE = "NotARealToken00000000000000000"

TOMORROW_9_LONDON = "2026-10-03T08:00:00.000Z"
WEBHOOK_LABEL = "The Slack channel this deployment posts to"
OK_LOOKUP = HttpResponse(200, {}, b'{"ok": true, "user": {"id": "U0123456789"}}')
OK_OPEN = HttpResponse(200, {}, json.dumps({"ok": True, "channel": {"id": SLACK_DM}}).encode())
MISSING = object()


@pytest.fixture
def make(graph_table: Any, ops_table: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> Any:
    def build(mode: str = "full", **kw: Any) -> World:
        return build_world(graph_table, ops_table, clock, monkeypatch, mode, **kw)

    return build


@pytest.fixture
def w(make: Any) -> World:
    return make()


def slack_error(code: str, status: int = 200, headers: dict[str, str] | None = None) -> HttpResponse:
    return HttpResponse(status, headers or {}, json.dumps({"ok": False, "error": code}).encode())


def prefix(kind: str) -> list[HttpResponse | Exception]:
    """The replies a DM needs before its post: the user lookup and the opened DM."""
    return [OK_LOOKUP, OK_OPEN] if kind == "slack_dm" else []


def ready(w: World, kind: str, role: str = "viewer", title: str = "Fresh signal") -> str:
    """A delivery with something new to send."""
    d = w.made(role, kind)
    w.later(minutes=1)
    w.signal(title)
    w.later(minutes=1)
    return str(d["id"])


def all_deliveries(w: World) -> list[dict[str, Any]]:
    return [i for i in w.ctx.ops_table.scan()["Items"] if str(i.get("sk", "")).startswith("DELIVERY#")]


def mrkdwn(post: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for b in post["body"]["blocks"]:
        if isinstance(b.get("text"), dict):
            out.append(b["text"]["text"])
        for e in b.get("elements", []):
            out.append(e["text"])
    return out


# =====================================================================================================================
# 1. Options: what each mode offers each caller (GET /api/me/deliveries)
# =====================================================================================================================


@pytest.mark.parametrize("role", ROLE_NAMES)
@pytest.mark.parametrize("mode", list(MODES))
def test_options_by_mode_and_role(make: Any, mode: str, role: str) -> None:
    w = make(mode)
    s, b, _ = w.call("GET", "/api/me/deliveries", role=role)
    if role == "anon":
        assert s == 401
        return
    if role == "disabled":
        assert s == 401 and b["code"] == "account_disabled"
        return
    sc, dm, em = MODES[mode].expect
    if not sc:
        channels: list[dict[str, str]] = []
    elif mode == "webhook":
        channels = [{"id": "webhook", "label": WEBHOOK_LABEL}]
    else:
        channels = [{"id": CHANNEL, "label": CHANNEL_LABEL}]
    assert s == 200
    assert b == {
        "items": [],
        "options": {
            "slack_channel": sc,
            "slack_dm": dm,
            "email": em,
            "channels": channels,
            "email_address": w.people[role].email,
        },
    }


# =====================================================================================================================
# 2. Create, then the first scheduled send, for every kind x mode x role
# =====================================================================================================================


@pytest.mark.parametrize("role", ROLE_NAMES)
@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("kind", KINDS)
def test_create_and_first_send(make: Any, kind: str, mode: str, role: str) -> None:
    w = make(mode)
    s, b = w.create(role, kind)
    if role == "anon":
        assert s == 401 and all_deliveries(w) == []
        return
    if role == "disabled":
        assert s == 401 and b["code"] == "account_disabled" and all_deliveries(w) == []
        return
    allowed = MODES[mode].expect[KINDS.index(kind)]
    if not allowed:
        assert s == 400 and f"{kind} delivery is not available to you" in b["message"]
        assert all_deliveries(w) == []
        return
    person = w.people[role]
    assert s == 201, b
    assert set(b) == PUBLIC_KEYS
    assert b["kind"] == kind and b["enabled"] is True and b["next_at"] == TOMORROW_9_LONDON
    assert b["last_status"] is None and b["last_sent_at"] is None and b["last_error"] is None
    item = w.item(role, b["id"])
    assert item is not None and item["due_pk"] == "DELIVERY_DUE"
    if kind == "email":
        assert b["target_label"] == person.email and b["channel_id"] is None and item["address"] == person.email
    elif kind == "slack_dm":
        assert b["target_label"] == "Slack direct message to you" and b["channel_id"] is None
        assert item["address"] == (person.alias if mode == "dm_directory" else person.email)
    elif mode == "webhook":
        assert b["target_label"] == WEBHOOK_LABEL and b["channel_id"] == "webhook"
    else:
        assert b["target_label"] == f"#{CHANNEL_LABEL}" and b["channel_id"] == CHANNEL
    # The first scheduled send carries a new record out through the configured way.
    w.later(minutes=5)
    w.signal("Matrix signal")
    w.later(minutes=5)
    assert w.send(role, b["id"]) == {"batchItemFailures": []}
    stored = w.stored(role, b["id"])
    assert stored.last_status is DeliveryStatus.OK and stored.cursor == iso(w.now())
    assert stored.last_sent_at == iso(w.now()) and stored.last_error is None
    if kind == "email":
        mail = w.mails()[-1]
        assert mail["Destination"] == {"ToAddresses": [person.email]} and len(w.mails()) == 1
        assert mail["FromEmailAddress"] == "no-reply@example.com" and mail["ConfigurationSetName"] == "air-digest"
        assert "Matrix signal" in w.mail_html() and w.posts() == []
        return
    assert len(w.posts()) == 1 and w.mails() == [] and "Matrix signal" in w.post_text()
    post = w.posts()[-1]
    if kind == "slack_channel" and mode == "webhook":
        assert post["url"] == WEBHOOK and "channel" not in post["body"]
    elif kind == "slack_channel":
        assert post["body"]["channel"] == CHANNEL and post["headers"]["authorization"] == f"Bearer {BOT_VALUE}"
    elif mode == "dm_directory":
        directory = w.http.directory_calls()
        assert len(directory) == 1 and f"alias={person.alias}" in directory[0]["url"]
        assert directory[0]["headers"]["authorization"].startswith("AWS4-HMAC-SHA256")
        assert w.out.sts.calls[0]["RoleArn"] == DIRECTORY_ROLE
        assert w.http.slack_calls("conversations.open")[0]["body"] == {"users": w.http.directory_ids[person.alias]}
        assert post["body"]["channel"] == SLACK_DM
    else:
        assert w.http.slack_calls("users.lookupByEmail")[0]["body"] == f"email={quote(person.email)}"
        assert w.http.slack_calls("conversations.open")[0]["body"] == {"users": "U0123456789"}
        assert post["body"]["channel"] == SLACK_DM


# =====================================================================================================================
# 3. Create validation
# =====================================================================================================================


def post_body(w: World, overrides: dict[str, Any], kind: str = "email") -> tuple[int, Any]:
    body: dict[str, Any] = {**DEFAULT_SCHEDULE, "kind": kind}
    for k, v in overrides.items():
        if v is MISSING:
            body.pop(k, None)
        else:
            body[k] = v
    s, b, _ = w.call("POST", "/api/me/deliveries", body, role="viewer")
    return s, b


BAD_KINDS = [
    ("fax", "kind must be"),
    ("", "kind must be"),
    ("SLACK_DM", "kind must be"),
    ("Email", "kind must be"),
    ("slack-channel", "kind must be"),
    ("sms", "kind must be"),
    (" email", "kind must be"),
    ("email ", "kind must be"),
    ("x" * 21, "at most 20"),
    (5, "must be a string"),
    (True, "must be a string"),
    ([], "must be a string"),
    ({}, "must be a string"),
    (None, "kind is required"),
    (MISSING, "kind is required"),
]


@pytest.mark.parametrize(("kind", "message"), BAD_KINDS, ids=[repr(k)[:20] for k, _ in BAD_KINDS])
def test_create_refuses_a_bad_kind(w: World, kind: Any, message: str) -> None:
    s, b = post_body(w, {"kind": kind})
    assert s == 400 and message in b["message"] and all_deliveries(w) == []


CHANNEL_CASES = [
    ("listed", CHANNEL, 201, ""),
    ("listed_private_group", "G0123456789", 201, ""),
    ("not_listed", "C9999999999", 400, "one of the channels"),
    ("not_listed_shortest", "C" + DIGITS[:8], 400, "one of the channels"),
    ("not_listed_group", "G0000000000", 400, "one of the channels"),
    ("lowercase", CHANNEL.lower(), 400, "one of the channels"),
    ("hash_name", "#general", 400, "one of the channels"),
    ("dm_id", SLACK_DM, 400, "one of the channels"),
    ("user_id", "U0123456789", 400, "one of the channels"),
    ("too_short", "C0123", 400, "one of the channels"),
    ("trailing_dash", "C012345678-", 400, "one of the channels"),
    ("webhook_in_bot_mode", "webhook", 400, "one of the channels"),
    ("empty", "", 400, "one of the channels"),
    ("missing", MISSING, 400, "one of the channels"),
    ("null", None, 400, "one of the channels"),
    ("integer", 123, 400, "must be a string"),
    ("too_long", "C" + "0" * 40, 400, "at most 40"),
    ("listed_with_space", f" {CHANNEL}", 400, "one of the channels"),
]


@pytest.mark.parametrize(
    ("channel_id", "status", "message"), [c[1:] for c in CHANNEL_CASES], ids=[c[0] for c in CHANNEL_CASES]
)
def test_create_slack_channel_needs_a_listed_channel(make: Any, channel_id: Any, status: int, message: str) -> None:
    w = make(channels=((CHANNEL, CHANNEL_LABEL), ("G0123456789", "private-grp")))
    s, b = post_body(w, {"channel_id": channel_id}, kind="slack_channel")
    assert s == status, b
    if status == 201:
        assert b["channel_id"] == channel_id and b["target_label"].startswith("#")
    else:
        assert message in b["message"] and all_deliveries(w) == []


@pytest.mark.parametrize("kind", ["email", "slack_dm"])
@pytest.mark.parametrize("channel_id", [CHANNEL, "G0123456789", "webhook"])
def test_channel_id_only_for_a_channel_delivery(w: World, kind: str, channel_id: str) -> None:
    s, b = post_body(w, {"channel_id": channel_id}, kind=kind)
    assert s == 400 and "only for a Slack channel" in b["message"]


@pytest.mark.parametrize("kind", ["email", "slack_dm"])
def test_channel_id_null_is_no_channel(w: World, kind: str) -> None:
    s, b = post_body(w, {"channel_id": None}, kind=kind)
    assert s == 201 and b["channel_id"] is None


BAD_CADENCE = [
    ("hourly", 'cadence must be "daily" or "weekly"'),
    ("monthly", "cadence must be"),
    ("Daily", "cadence must be"),
    ("WEEKLY", "cadence must be"),
    ("", "cadence must be"),
    (" daily", "cadence must be"),
    ("fortnightly", "at most 10"),
    (1, "must be a string"),
    (None, "cadence is required"),
    (["daily"], "must be a string"),
]


@pytest.mark.parametrize(("cadence", "message"), BAD_CADENCE, ids=[repr(c) for c, _ in BAD_CADENCE])
def test_create_refuses_a_bad_cadence(w: World, cadence: Any, message: str) -> None:
    s, b = post_body(w, {"cadence": cadence, "weekday": 1})
    assert s == 400 and message in b["message"] and all_deliveries(w) == []


BAD_WEEKDAY = [
    (-1, "between 0 and 6"),
    (7, "between 0 and 6"),
    (8, "between 0 and 6"),
    (100, "between 0 and 6"),
    ("1", "must be an integer"),
    (1.5, "must be an integer"),
    (True, "must be an integer"),
    (None, "needs weekday 0 (Monday) to 6 (Sunday)"),
    (MISSING, "needs weekday 0 (Monday) to 6 (Sunday)"),
]


@pytest.mark.parametrize(("weekday", "message"), BAD_WEEKDAY, ids=[repr(x) for x, _ in BAD_WEEKDAY])
def test_create_weekly_refuses_a_bad_weekday(w: World, weekday: Any, message: str) -> None:
    s, b = post_body(w, {"cadence": "weekly", "weekday": weekday})
    assert s == 400 and message in b["message"] and all_deliveries(w) == []


@pytest.mark.parametrize("weekday", [0, 3, 6])
def test_create_daily_ignores_a_weekday(w: World, weekday: int) -> None:
    s, b = post_body(w, {"weekday": weekday})
    assert s == 201 and b["weekday"] is None and b["next_at"] == TOMORROW_9_LONDON
    assert "weekday" not in (w.item("viewer", b["id"]) or {})


BAD_HOUR = [
    (-1, "between 0 and 23"),
    (24, "between 0 and 23"),
    (25, "between 0 and 23"),
    (99, "between 0 and 23"),
    (-24, "between 0 and 23"),
    ("9", "must be an integer"),
    (9.5, "must be an integer"),
    (9.0, "must be an integer"),
    (True, "must be an integer"),
    (False, "must be an integer"),
    ([9], "must be an integer"),
    (None, "hour is required"),
    (MISSING, "hour and timezone are required"),
]


@pytest.mark.parametrize(("hour", "message"), BAD_HOUR, ids=[repr(x) for x, _ in BAD_HOUR])
def test_create_refuses_a_bad_hour(w: World, hour: Any, message: str) -> None:
    s, b = post_body(w, {"hour": hour})
    assert s == 400 and message in b["message"] and all_deliveries(w) == []


BAD_ZONES = [
    ("Mars/Base", "unknown time zone"),
    ("../../etc/passwd", "IANA time zone name"),
    ("/etc/localtime", "IANA time zone name"),
    ("Europe/london", "unknown time zone"),
    ("utc", "unknown time zone"),
    ("", "IANA time zone name"),
    (" UTC", "IANA time zone name"),
    ("UTC ", "IANA time zone name"),
    ("Europe/London\n", "IANA time zone name"),
    ("America/New York", "IANA time zone name"),
    ("A" * 65, "at most 64"),
    ("Etc/GMT+15", "unknown time zone"),
    ("+05:00", "IANA time zone name"),
    ("GMT+5", "unknown time zone"),
    ("localtime", "unknown time zone"),
    ("Europe/London/A/B/C", "IANA time zone name"),
    ("Europe\\London", "IANA time zone name"),
    (5, "must be a string"),
    (None, "timezone is required"),
    (MISSING, "hour and timezone are required"),
]


@pytest.mark.parametrize(("zone", "message"), BAD_ZONES, ids=[repr(z)[:24] for z, _ in BAD_ZONES])
def test_create_refuses_a_bad_timezone(w: World, zone: Any, message: str) -> None:
    s, b = post_body(w, {"timezone": zone})
    assert s == 400 and message in b["message"] and all_deliveries(w) == []


GOOD_ZONES = [
    "UTC",
    "Etc/UTC",
    "GMT",
    "Etc/GMT+12",
    "Etc/GMT-14",
    "America/New_York",
    "Europe/London",
    "Australia/Lord_Howe",
    "Asia/Kathmandu",
    "Asia/Kolkata",
    "Pacific/Chatham",
    "Pacific/Kiritimati",
    "Pacific/Pago_Pago",
    "America/St_Johns",
    "America/Argentina/Buenos_Aires",
    "Australia/Sydney",
    "Asia/Tokyo",
    "Africa/Casablanca",
    "Europe/Dublin",
    "America/Sao_Paulo",
    "Asia/Tehran",
    "Australia/Eucla",
    "Antarctica/Troll",
    "US/Eastern",
]


@pytest.mark.parametrize("zone", GOOD_ZONES)
def test_create_accepts_an_iana_zone_and_schedules_it(w: World, zone: str) -> None:
    s, b = post_body(w, {"timezone": zone, "hour": 7})
    assert s == 201, b
    assert b["timezone"] == zone and b["next_at"] == iso(oracle_next("daily", None, 7, zone, w.now()))


BAD_ENABLED = [
    ("yes", "true or false"),
    (1, "true or false"),
    ("true", "true or false"),
    (None, "enabled is required"),
    (0, "true or false"),
]


@pytest.mark.parametrize(("enabled", "message"), BAD_ENABLED, ids=[repr(x) for x, _ in BAD_ENABLED])
def test_create_refuses_a_bad_enabled(w: World, enabled: Any, message: str) -> None:
    s, b = post_body(w, {"enabled": enabled})
    assert s == 400 and message in b["message"] and all_deliveries(w) == []


@pytest.mark.parametrize("kind", KINDS)
def test_create_disabled_is_stored_but_never_due(w: World, kind: str) -> None:
    s, b = w.create("viewer", kind, enabled=False)
    assert s == 201 and b["enabled"] is False and b["next_at"] is None
    item = w.item("viewer", b["id"]) or {}
    assert "due_pk" not in item and item["next_at"]
    assert w.ctx.deliveries.due(w.now() + timedelta(days=30)) == []


@pytest.mark.parametrize(
    "field",
    [
        "address",
        "target_label",
        "id",
        "next_at",
        "cursor",
        "subject",
        "suppressed",
        "user",
        "channel",
        "to",
        "slack_user",
    ],
)
def test_create_refuses_fields_that_name_a_target_or_state(w: World, field: str) -> None:
    s, b = post_body(w, {field: "victim@example.com"})
    assert s == 400 and "unknown fields" in b["message"] and field in b["message"] and all_deliveries(w) == []


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("[]", "must be a JSON object"),
        ('"email"', "must be a JSON object"),
        ("{", "not valid JSON"),
        ("null", "must be a JSON object"),
    ],
    ids=["list", "string", "broken", "null"],
)
def test_create_refuses_a_body_that_is_not_an_object(w: World, raw: str, message: str) -> None:
    s, b, _ = w.call("POST", "/api/me/deliveries", role="viewer", raw_body=raw)
    assert s == 400 and message in b["message"]


def test_create_with_no_body_needs_a_kind(w: World) -> None:
    s, b, _ = w.call("POST", "/api/me/deliveries", role="viewer")
    assert s == 400 and "kind is required" in b["message"]


# =====================================================================================================================
# 4. Schedules: next_at for every hour and weekday, DST changes, weekly wrap, and the scheduler across DST
# =====================================================================================================================


@pytest.mark.parametrize("hour", range(24))
def test_daily_next_at_for_every_hour(w: World, hour: int) -> None:
    s, b = post_body(w, {"hour": hour})
    assert s == 201
    # 08:00 UTC is 09:00 BST: hours up to 9 are tomorrow, later ones today.
    day = 3 if hour <= 9 else 2
    expected = datetime(2026, 10, day, hour, tzinfo=ZoneInfo("Europe/London")).astimezone(UTC)
    assert b["next_at"] == iso(expected) == iso(oracle_next("daily", None, hour, "Europe/London", w.now()))


WEEKLY_ZONES = ["UTC", "Pacific/Kiritimati", "Pacific/Pago_Pago"]


@pytest.mark.parametrize("zone", WEEKLY_ZONES)
@pytest.mark.parametrize("weekday", range(7))
def test_weekly_next_at_for_every_weekday(w: World, weekday: int, zone: str) -> None:
    s, b = post_body(w, {"cadence": "weekly", "weekday": weekday, "hour": 9, "timezone": zone})
    assert s == 201 and b["weekday"] == weekday
    when = parse_iso(b["next_at"])
    local = when.astimezone(ZoneInfo(zone))
    assert local.weekday() == weekday and (local.hour, local.minute) == (9, 0)
    assert w.now() < when <= w.now() + timedelta(days=7)
    assert when == oracle_next("weekly", weekday, 9, zone, w.now())


DST_STARTS = [
    ("America/New_York", "2026-10-31T12:00:00Z"),  # back: 01:00 happens twice on 1 Nov
    ("America/New_York", "2027-03-13T12:00:00Z"),  # forward: no 02:00 on 14 Mar
    ("Europe/London", "2026-10-24T12:00:00Z"),  # back: 01:00 twice on 25 Oct
    ("Europe/London", "2027-03-27T12:00:00Z"),  # forward: no 01:00 on 28 Mar
    ("Australia/Lord_Howe", "2026-10-03T12:00:00Z"),  # forward half an hour: no 02:00-02:30 on 4 Oct
    ("Australia/Lord_Howe", "2027-04-03T12:00:00Z"),  # back half an hour: 01:30-02:00 twice on 4 Apr
    ("Australia/Sydney", "2026-10-03T12:00:00Z"),  # forward: no 02:00 on 4 Oct
    ("Australia/Sydney", "2027-04-03T12:00:00Z"),  # back: 02:00 twice on 4 Apr
    ("Asia/Kathmandu", "2026-10-24T12:00:00Z"),  # +05:45, no DST
    ("UTC", "2026-10-24T12:00:00Z"),
]


@pytest.mark.parametrize("hour", [0, 1, 2, 3, 9, 23])
@pytest.mark.parametrize(("zone", "start"), DST_STARTS, ids=[f"{z}@{s[:10]}" for z, s in DST_STARTS])
def test_next_at_around_dst_changes(w: World, zone: str, start: str, hour: int) -> None:
    w.at(start)
    s, b = post_body(w, {"hour": hour, "timezone": zone})
    assert s == 201
    assert b["next_at"] == iso(oracle_next("daily", None, hour, zone, w.now()))


HANDPICKED = [
    # zone, now, hour, expected next_at
    ("America/New_York", "2027-03-14T05:00:00Z", 2, "2027-03-14T07:00:00.000Z"),  # skipped 02:00 = the jump (03:00)
    ("America/New_York", "2026-11-01T04:00:00Z", 1, "2026-11-01T05:00:00.000Z"),  # first 01:00 (EDT)
    ("America/New_York", "2026-11-01T05:00:00Z", 1, "2026-11-02T06:00:00.000Z"),  # not the second 01:00 (EST)
    ("America/New_York", "2026-11-01T05:30:00Z", 1, "2026-11-02T06:00:00.000Z"),
    ("Europe/London", "2027-03-28T00:30:00Z", 1, "2027-03-28T01:00:00.000Z"),  # skipped 01:00 = 02:00 BST
    ("Europe/London", "2026-10-24T23:30:00Z", 1, "2026-10-25T00:00:00.000Z"),  # first 01:00 (BST)
    ("Europe/London", "2026-10-25T00:00:00Z", 1, "2026-10-26T01:00:00.000Z"),
    ("Australia/Lord_Howe", "2026-10-03T15:00:00Z", 2, "2026-10-03T15:30:00.000Z"),  # skipped 02:00 = 02:30 +11
    ("Australia/Lord_Howe", "2026-10-03T12:00:00Z", 9, "2026-10-03T22:00:00.000Z"),  # 09:00 +11 after the change
    ("Australia/Lord_Howe", "2026-10-02T12:00:00Z", 9, "2026-10-02T22:30:00.000Z"),  # 09:00 +10:30 before it
    ("Australia/Lord_Howe", "2027-04-03T14:00:00Z", 1, "2027-04-03T14:00:00.000Z"),  # strictly after: next day
    ("Australia/Sydney", "2026-10-03T15:00:00Z", 2, "2026-10-03T16:00:00.000Z"),  # skipped 02:00 = 03:00 AEDT
    ("Australia/Sydney", "2027-04-03T15:00:00Z", 2, "2027-04-03T15:00:00.000Z"),  # first 02:00 (AEDT)
    ("Asia/Kathmandu", "2026-10-02T08:00:00Z", 9, "2026-10-03T03:15:00.000Z"),  # +05:45
    ("UTC", "2026-10-02T08:00:00Z", 8, "2026-10-03T08:00:00.000Z"),  # exactly now: strictly after
    ("Pacific/Chatham", "2026-10-02T08:00:00Z", 9, "2026-10-02T19:15:00.000Z"),  # +13:45
]


@pytest.mark.parametrize(
    ("zone", "now", "hour", "expected"), HANDPICKED, ids=[f"{z}-{n[:16]}-h{h}" for z, n, h, _ in HANDPICKED]
)
def test_next_at_handpicked_dst_cases(w: World, zone: str, now: str, hour: int, expected: str) -> None:
    w.at(now)
    s, b = post_body(w, {"hour": hour, "timezone": zone})
    if expected == now.replace("Z", ".000Z"):
        # 01:00 at exactly now: the answer is the next day's 01:00 (strictly after now).
        assert s == 201 and parse_iso(b["next_at"]) > w.now()
        assert b["next_at"] == iso(oracle_next("daily", None, hour, zone, w.now()))
        return
    assert s == 201 and b["next_at"] == expected


SCHEDULE_RUNS = [
    ("America/New_York", "2026-10-28T12:00:00Z", 1),
    ("America/New_York", "2026-10-28T12:00:00Z", 2),
    ("America/New_York", "2026-10-28T12:00:00Z", 9),
    ("America/New_York", "2027-03-10T12:00:00Z", 2),
    ("America/New_York", "2027-03-10T12:00:00Z", 3),
    ("America/New_York", "2027-03-10T12:00:00Z", 9),
    ("Europe/London", "2026-10-21T12:00:00Z", 1),
    ("Europe/London", "2026-10-21T12:00:00Z", 9),
    ("Europe/London", "2027-03-24T12:00:00Z", 1),
    ("Europe/London", "2027-03-24T12:00:00Z", 23),
    ("Australia/Lord_Howe", "2026-09-30T12:00:00Z", 2),
    ("Australia/Lord_Howe", "2026-09-30T12:00:00Z", 9),
    ("Australia/Lord_Howe", "2027-03-31T12:00:00Z", 1),
    ("Australia/Sydney", "2027-03-31T12:00:00Z", 2),
    ("Asia/Kathmandu", "2026-10-21T12:00:00Z", 9),
    ("UTC", "2026-10-21T12:00:00Z", 0),
]


@pytest.mark.parametrize(
    ("zone", "start", "hour"), SCHEDULE_RUNS, ids=[f"{z}@{s[:10]}-h{h}" for z, s, h in SCHEDULE_RUNS]
)
def test_scheduler_keeps_one_send_per_local_day_across_dst(w: World, zone: str, start: str, hour: int) -> None:
    w.at(start)
    d = w.made("viewer", "email", hour=hour, timezone=zone)
    tz = ZoneInfo(zone)
    days = []
    for _ in range(8):
        next_at = str(w.stored("viewer", d["id"]).next_at)
        w.at(parse_iso(next_at) - timedelta(seconds=1))
        assert w.tick()["queued"] == 0  # not a second early
        w.at(next_at)
        assert w.tick() == {"queued": 1, "skipped": 0, "failed": 0}
        assert w.tick()["queued"] == 0  # and once
        local = parse_iso(next_at).astimezone(tz)
        days.append(local.date())
        assert local.hour >= hour and (local.hour, local.minute) <= (hour + 1, 30)
        assert parse_iso(next_at) == oracle_next("daily", None, hour, zone, parse_iso(next_at) - timedelta(seconds=1))
    assert all((b - a).days == 1 for a, b in itertools.pairwise(days))  # no local day skipped or repeated
    assert len(w.ctx.sqs.messages) == 8


@pytest.mark.parametrize(
    ("zone", "hour", "passed"),
    [
        ("UTC", 7, True),
        ("UTC", 9, False),
        ("Europe/London", 8, True),
        ("Europe/London", 10, False),
        ("Pacific/Kiritimati", 21, True),
        ("Pacific/Kiritimati", 23, False),
    ],
)
def test_weekly_on_todays_weekday_wraps_a_week_once_the_hour_passed(
    w: World, zone: str, hour: int, passed: bool
) -> None:
    today = w.now().astimezone(ZoneInfo(zone))
    s, b = post_body(w, {"cadence": "weekly", "weekday": today.weekday(), "hour": hour, "timezone": zone})
    assert s == 201
    local = parse_iso(b["next_at"]).astimezone(ZoneInfo(zone))
    assert (local.date() - today.date()).days == (7 if passed else 0) and local.hour == hour


@pytest.mark.parametrize("weekday", range(7))
def test_weekly_scheduler_runs_every_seven_days(w: World, weekday: int) -> None:
    d = w.made("viewer", "email", cadence="weekly", weekday=weekday, hour=6, timezone="America/New_York")
    stamps = []
    for _ in range(3):
        nxt = str(w.stored("viewer", d["id"]).next_at)
        w.at(nxt)
        assert w.tick()["queued"] == 1
        stamps.append(parse_iso(nxt))
    assert [s.astimezone(ZoneInfo("America/New_York")).weekday() for s in stamps] == [weekday] * 3
    gaps = {(b - a) for a, b in itertools.pairwise(stamps)}
    assert gaps <= {timedelta(days=7), timedelta(days=7, hours=1)}  # one week; one hour more across 1 Nov


# =====================================================================================================================
# 5. Ownership and limits
# =====================================================================================================================


@pytest.mark.parametrize("sixth", KINDS)
def test_at_most_five_deliveries_whatever_the_kind(w: World, sixth: str) -> None:
    for kind in ("email", "slack_dm", "slack_channel", "email", "slack_dm"):
        assert w.create("viewer", kind)[0] == 201
    s, b = w.create("viewer", sixth)
    assert s == 400 and "at most 5 deliveries" in b["message"]
    assert len(w.ctx.deliveries.for_user(w.subject("viewer"))) == 5


def test_disabled_deliveries_count_toward_the_five(w: World) -> None:
    for _ in range(5):
        assert w.create("viewer", "email", enabled=False)[0] == 201
    assert w.create("viewer", "email")[0] == 400


def test_deleting_one_makes_room(w: World) -> None:
    ids = [w.made("viewer", "email")["id"] for _ in range(5)]
    assert w.call("DELETE", f"/api/me/deliveries/{ids[2]}", role="viewer")[0] == 200
    assert w.create("viewer", "email")[0] == 201
    assert w.create("viewer", "email")[0] == 400


@pytest.mark.parametrize("other", ["editor", "admin"])
def test_the_five_are_per_person(w: World, other: str) -> None:
    for _ in range(5):
        w.made("viewer", "email")
    assert w.create(other, "email")[0] == 201


@pytest.mark.parametrize("owner_kind", KINDS)
@pytest.mark.parametrize("caller", ["editor", "admin"])
@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [("PATCH", "", {"enabled": False}), ("DELETE", "", None), ("POST", "/test", {})],
    ids=["patch", "delete", "test"],
)
def test_someone_elses_delivery_is_not_found(
    w: World, owner_kind: str, caller: str, method: str, suffix: str, body: Any
) -> None:
    d = w.made("viewer", owner_kind)
    before = w.item("viewer", d["id"])
    s, b, _ = w.call(method, f"/api/me/deliveries/{d['id']}{suffix}", body, role=caller)
    assert s == 404 and b["code"] == "not_found"
    assert w.item("viewer", d["id"]) == before and w.outgoing() == 0
    assert w.listed(caller)["items"] == []


BAD_IDS = [
    "not-an-id",
    "0123456789AB",
    "0123456789a",
    "0123456789abc",
    "g123456789ab",
    "0123456789a%20",
    "............",
]


@pytest.mark.parametrize("did", BAD_IDS)
@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [("PATCH", "", {"hour": 3}), ("DELETE", "", None), ("POST", "/test", {})],
    ids=["patch", "delete", "test"],
)
def test_malformed_ids_are_not_found(w: World, did: str, method: str, suffix: str, body: Any) -> None:
    w.made("viewer", "email")
    s, _, _ = w.call(method, f"/api/me/deliveries/{did}{suffix}", body, role="viewer")
    assert s == 404


def test_the_list_shows_only_my_own_in_creation_order(w: World) -> None:
    mine = []
    for k in KINDS:
        mine.append(w.made("viewer", k)["id"])
        w.later(seconds=1)  # one millisecond apart: same-millisecond creates are ordered by id
    w.made("editor", "email")
    listed = w.listed("viewer")["items"]
    assert [d["id"] for d in listed] == mine and all(set(d) == PUBLIC_KEYS for d in listed)


@pytest.mark.parametrize("role", ["anon", "disabled"])
@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [("PATCH", "", {"hour": 3}), ("DELETE", "", None), ("POST", "/test", {})],
    ids=["patch", "delete", "test"],
)
def test_signed_out_or_disabled_callers_change_nothing(
    w: World, role: str, method: str, suffix: str, body: Any
) -> None:
    d = w.made("viewer", "email")
    before = w.item("viewer", d["id"])
    assert w.call(method, f"/api/me/deliveries/{d['id']}{suffix}", body, role=role)[0] == 401
    assert w.item("viewer", d["id"]) == before


@pytest.mark.parametrize("kind", KINDS)
def test_delete_removes_the_delivery_and_its_schedule(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    s, b, _ = w.call("DELETE", f"/api/me/deliveries/{d['id']}", role="viewer")
    assert s == 200 and b == {"id": d["id"], "deleted": True}
    assert w.item("viewer", d["id"]) is None and w.ctx.deliveries.due(w.now() + timedelta(days=9)) == []
    assert w.call("DELETE", f"/api/me/deliveries/{d['id']}", role="viewer")[0] == 404


# =====================================================================================================================
# 6. PATCH
# =====================================================================================================================


BAD_PATCHES = [
    ("cadence_hourly", {"cadence": "hourly"}, "cadence must be"),
    ("cadence_int", {"cadence": 5}, "must be a string"),
    ("cadence_null", {"cadence": None}, "cadence is required"),
    ("weekly_without_weekday", {"cadence": "weekly"}, "needs weekday"),
    ("weekly_null_weekday", {"cadence": "weekly", "weekday": None}, "needs weekday"),
    ("weekday_7", {"cadence": "weekly", "weekday": 7}, "between 0 and 6"),
    ("weekday_neg", {"cadence": "weekly", "weekday": -1}, "between 0 and 6"),
    ("weekday_str", {"cadence": "weekly", "weekday": "1"}, "must be an integer"),
    ("weekday_bool", {"cadence": "weekly", "weekday": True}, "must be an integer"),
    ("hour_24", {"hour": 24}, "between 0 and 23"),
    ("hour_neg", {"hour": -1}, "between 0 and 23"),
    ("hour_str", {"hour": "9"}, "must be an integer"),
    ("hour_float", {"hour": 9.5}, "must be an integer"),
    ("hour_null", {"hour": None}, "hour is required"),
    ("tz_unknown", {"timezone": "Mars/Base"}, "unknown time zone"),
    ("tz_empty", {"timezone": ""}, "IANA time zone name"),
    ("tz_path", {"timezone": "../etc/passwd"}, "IANA time zone name"),
    ("tz_int", {"timezone": 5}, "must be a string"),
    ("tz_long", {"timezone": "A" * 65}, "at most 64"),
    ("tz_null", {"timezone": None}, "timezone is required"),
    ("enabled_str", {"enabled": "yes"}, "true or false"),
    ("enabled_int", {"enabled": 1}, "true or false"),
    ("enabled_null", {"enabled": None}, "enabled is required"),
    ("kind", {"kind": "slack_dm"}, "unknown fields"),
    ("channel_id", {"channel_id": CHANNEL}, "unknown fields"),
    ("address", {"address": "victim@example.com"}, "unknown fields"),
    ("id", {"id": "0123456789ab"}, "unknown fields"),
    ("next_at", {"next_at": "2026-10-02T09:00:00.000Z"}, "unknown fields"),
    ("cursor", {"cursor": "x"}, "unknown fields"),
    ("target_label", {"target_label": "x"}, "unknown fields"),
    ("suppressed", {"suppressed": False}, "unknown fields"),
]


@pytest.mark.parametrize(("body", "message"), [c[1:] for c in BAD_PATCHES], ids=[c[0] for c in BAD_PATCHES])
def test_patch_refuses_and_changes_nothing(w: World, body: dict[str, Any], message: str) -> None:
    d = w.made("viewer", "email")
    before = w.item("viewer", d["id"])
    s, b = w.patch("viewer", d["id"], body)
    assert s == 400 and message in b["message"]
    assert w.item("viewer", d["id"]) == before


@pytest.mark.parametrize("raw", ["[]", "{", '"x"'])
def test_patch_refuses_a_body_that_is_not_an_object(w: World, raw: str) -> None:
    d = w.made("viewer", "email")
    s, _, _ = w.call("PATCH", f"/api/me/deliveries/{d['id']}", role="viewer", raw_body=raw)
    assert s == 400


GOOD_PATCHES = [
    ("hour", {"hour": 11}, {"hour": 11, "cadence": "daily"}),
    ("zone", {"timezone": "Asia/Kathmandu"}, {"timezone": "Asia/Kathmandu"}),
    (
        "weekly",
        {"cadence": "weekly", "weekday": 2, "hour": 7, "timezone": "Asia/Jakarta"},
        {"cadence": "weekly", "weekday": 2},
    ),
    ("weekday_only_on_daily_is_dropped", {"weekday": 4}, {"weekday": None, "cadence": "daily"}),
    ("empty", {}, {"hour": 9, "cadence": "daily"}),
    ("all_fields", {"cadence": "daily", "hour": 0, "timezone": "UTC", "enabled": True}, {"hour": 0}),
]


@pytest.mark.parametrize(("body", "expect"), [c[1:] for c in GOOD_PATCHES], ids=[c[0] for c in GOOD_PATCHES])
def test_patch_recomputes_next_at(w: World, body: dict[str, Any], expect: dict[str, Any]) -> None:
    d = w.made("viewer", "email")
    w.later(hours=3)
    s, b = w.patch("viewer", d["id"], body)
    assert s == 200 and set(b) == PUBLIC_KEYS
    for k, v in expect.items():
        assert b[k] == v, k
    assert b["next_at"] == iso(oracle_next(b["cadence"], b["weekday"], b["hour"], b["timezone"], w.now()))
    item = w.item("viewer", d["id"]) or {}
    assert item["due_sk"] == f"{b['next_at']}#{w.subject('viewer')}#{d['id']}"


def test_patch_from_weekly_back_to_daily_drops_the_weekday(w: World) -> None:
    d = w.made("viewer", "email", cadence="weekly", weekday=5)
    s, b = w.patch("viewer", d["id"], {"cadence": "daily"})
    assert s == 200 and b["weekday"] is None and "weekday" not in (w.item("viewer", d["id"]) or {})


@pytest.mark.parametrize("kind", KINDS)
def test_patch_switch_off_and_on(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    s, b = w.patch("viewer", d["id"], {"enabled": False})
    assert s == 200 and b["enabled"] is False and b["next_at"] is None
    assert "due_pk" not in (w.item("viewer", d["id"]) or {})
    w.at("2026-10-03T08:00:00Z")
    assert w.tick()["queued"] == 0
    s, b = w.patch("viewer", d["id"], {"enabled": True})
    assert s == 200 and b["next_at"] == "2026-10-04T08:00:00.000Z"
    assert (w.item("viewer", d["id"]) or {})["due_pk"] == "DELIVERY_DUE"


def test_patch_keeps_the_cursor_and_last_send(w: World) -> None:
    did = ready(w, "email")
    w.send("viewer", did)
    before = w.stored("viewer", did)
    w.later(minutes=1)
    s, b = w.patch("viewer", did, {"hour": 6})
    after = w.stored("viewer", did)
    assert s == 200 and b["last_status"] == "ok" and b["last_sent_at"] == before.last_sent_at
    assert after.cursor == before.cursor and after.last_status is DeliveryStatus.OK


# =====================================================================================================================
# 7. The scheduler
# =====================================================================================================================


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("offset", "queued"),
    [(-60, 0), (-1, 0), (0, 1), (1, 1), (14 * 60, 1)],
    ids=["minute_early", "second_early", "on_time", "second_late", "tick_late"],
)
def test_due_selection(w: World, kind: str, offset: int, queued: int) -> None:
    d = w.made("viewer", kind)
    w.at(parse_iso(d["next_at"]) + timedelta(seconds=offset))
    assert w.tick() == {"queued": queued, "skipped": 0, "failed": 0}
    if queued:
        assert w.ctx.sqs.bodies() == [{"subject": w.subject("viewer"), "id": d["id"], "due": d["next_at"]}]
        assert w.ctx.sqs.messages[0]["QueueUrl"] == QUEUE
        assert w.stored("viewer", d["id"]).next_at == "2026-10-04T08:00:00.000Z"
    else:
        assert w.stored("viewer", d["id"]).next_at == d["next_at"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_second_tick_queues_nothing(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    w.at(d["next_at"])
    assert w.tick()["queued"] == 1
    for _ in range(3):
        assert w.tick() == {"queued": 0, "skipped": 0, "failed": 0}
    assert len(w.ctx.sqs.messages) == 1


@pytest.mark.parametrize("how", ["disabled", "suppressed", "deleted", "unsubscribed"])
def test_switched_off_deliveries_are_not_due(w: World, how: str) -> None:
    d = w.made("viewer", "email")
    sub = w.subject("viewer")
    if how == "disabled":
        w.patch("viewer", d["id"], {"enabled": False})
    elif how == "suppressed":
        w.ctx.deliveries.switch_off(sub, d["id"], suppress=True, reason="bounced")
    elif how == "deleted":
        w.call("DELETE", f"/api/me/deliveries/{d['id']}", role="viewer")
    else:
        token = w.out.unsubscribe.issue(sub, d["id"])
        assert w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 200
    w.later(days=8)
    assert w.tick() == {"queued": 0, "skipped": 0, "failed": 0} and w.ctx.sqs.messages == []


def test_only_what_is_due_is_queued(w: World) -> None:
    ids = {h: w.made("viewer", "email", hour=h, timezone="UTC")["id"] for h in (9, 10, 11, 12)}
    w.at("2026-10-02T11:00:00Z")
    assert w.tick()["queued"] == 3
    assert {m["id"] for m in w.ctx.sqs.bodies()} == {ids[9], ids[10], ids[11]}


def test_every_persons_due_delivery_is_queued(w: World) -> None:
    made = {(r, w.made(r, "email")["id"]) for r in ("viewer", "editor", "admin")}
    w.at(TOMORROW_9_LONDON)
    assert w.tick()["queued"] == 3
    assert {(m["subject"], m["id"]) for m in w.ctx.sqs.bodies()} == {(w.subject(r), i) for r, i in made}


def test_missed_ticks_send_once_then_follow_the_schedule(w: World) -> None:
    d = w.made("viewer", "email")
    w.at("2026-10-06T10:00:00Z")
    assert w.tick()["queued"] == 1 and w.tick()["queued"] == 0
    assert w.stored("viewer", d["id"]).next_at == "2026-10-07T08:00:00.000Z"


def test_weekly_is_not_queued_on_other_days(w: World) -> None:
    d = w.made("viewer", "email", cadence="weekly", weekday=0, hour=9, timezone="UTC")  # Mondays
    for day in range(3, 5):
        w.at(f"2026-10-0{day}T09:00:00Z")
        assert w.tick()["queued"] == 0
    w.at("2026-10-05T09:00:00Z")
    assert w.tick()["queued"] == 1
    assert w.stored("viewer", d["id"]).next_at == "2026-10-12T09:00:00.000Z"


def test_a_refused_message_puts_the_time_back(w: World) -> None:
    d = w.made("viewer", "email")
    w.at(d["next_at"])
    w.ctx.sqs.fail = 1
    assert w.tick() == {"queued": 0, "skipped": 0, "failed": 1}
    assert w.stored("viewer", d["id"]).next_at == d["next_at"]
    assert w.tick() == {"queued": 1, "skipped": 0, "failed": 0}


def test_one_refusal_does_not_stop_the_rest(w: World) -> None:
    w.made("viewer", "email")
    w.made("editor", "email")
    w.at(TOMORROW_9_LONDON)
    w.ctx.sqs.fail = 1
    assert w.tick() == {"queued": 1, "skipped": 0, "failed": 1}
    assert w.tick() == {"queued": 1, "skipped": 0, "failed": 0}


@pytest.mark.parametrize("mode", ["no_queue", "all_off"])
def test_scheduler_does_nothing_without_a_queue_or_a_channel(w: World, mode: str) -> None:
    w.made("viewer", "email")
    if mode == "no_queue":
        w.set_config(config(DIGEST_SLACK_MODE="bot", DIGEST_EMAIL_MODE="ses", DIGEST_QUEUE_URL=""))
    else:
        w.set_mode("all_off")
    w.at(TOMORROW_9_LONDON)
    assert w.tick() == {"queued": 0, "skipped": 0, "failed": 0}
    assert w.stored("viewer", w.ctx.deliveries.for_user(w.subject("viewer"))[0].id).next_at == TOMORROW_9_LONDON


def test_two_racing_ticks_queue_once(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    w.made("viewer", "email")
    w.at(TOMORROW_9_LONDON)
    real = w.ctx.deliveries.due
    monkeypatch.setattr(w.ctx.deliveries, "due", lambda now, limit=200: real(now) + real(now))
    assert w.tick() == {"queued": 1, "skipped": 1, "failed": 0}


def test_an_edit_between_query_and_claim_wins(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    d = w.made("viewer", "email")
    w.at(TOMORROW_9_LONDON)
    real = w.ctx.deliveries.due

    def then_edit(now: datetime, limit: int = 200) -> list[DueDelivery]:
        found = real(now)
        w.patch("viewer", d["id"], {"hour": 15})
        return found

    monkeypatch.setattr(w.ctx.deliveries, "due", then_edit)
    assert w.tick() == {"queued": 0, "skipped": 1, "failed": 0}
    assert w.stored("viewer", d["id"]).next_at == "2026-10-03T14:00:00.000Z"


def test_junk_in_the_due_index_is_skipped(w: World) -> None:
    d = w.made("viewer", "email")
    for sk in ("2000-01-01#nope", "2000-01-01#s#BAD-ID", "garbage", "2000-01-01##0123456789ab"):
        w.ctx.ops_table.put_item(Item={"pk": "JUNK", "sk": sk, "due_pk": "DELIVERY_DUE", "due_sk": sk})
    w.at(d["next_at"])
    assert w.tick() == {"queued": 1, "skipped": 0, "failed": 0}


@pytest.mark.parametrize("kind", KINDS)
def test_tick_then_send_full_cycle(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    w.signal("Cycle signal")
    w.at(d["next_at"])
    assert w.tick()["queued"] == 1
    msg = w.ctx.sqs.messages[0]
    record = {"messageId": "q1", "body": msg["MessageBody"], "receiptHandle": "rh"}
    from insights_radar.api import workers

    assert workers.digest_send({"Records": [record]}, None, w.ctx) == {"batchItemFailures": []}
    assert w.outgoing() == 1 and "Cycle signal" in w.delivered_text(kind)
    stored = w.stored("viewer", d["id"])
    assert stored.cursor == iso(w.now()) and stored.last_status is DeliveryStatus.OK


# =====================================================================================================================
# 8. The sender: outcomes and the cursor
# =====================================================================================================================


@pytest.mark.parametrize("kind", KINDS)
def test_empty_digest_sends_nothing_and_moves_the_cursor(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    w.later(hours=1)
    assert w.send("viewer", d["id"]) == {"batchItemFailures": []}
    assert w.outgoing() == 0 and w.http.calls == []  # no Slack lookup either
    stored = w.stored("viewer", d["id"])
    assert stored.last_status is DeliveryStatus.EMPTY and stored.cursor == iso(w.now())
    assert stored.last_sent_at is None and stored.last_error is None


@pytest.mark.parametrize("kind", KINDS)
def test_content_is_sent_once_and_the_cursor_moves(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    w.later(minutes=1)
    w.signal("One")
    w.signal("Two")
    w.later(minutes=1)
    w.send("viewer", d["id"])
    text = w.delivered_text(kind)
    assert "One" in text and "Two" in text and w.outgoing() == 1
    stored = w.stored("viewer", d["id"])
    assert stored.cursor == iso(w.now()) and stored.last_sent_at == iso(w.now())
    w.later(minutes=1)
    w.send("viewer", d["id"])  # nothing new since
    assert w.outgoing() == 1 and w.stored("viewer", d["id"]).last_status is DeliveryStatus.EMPTY
    w.later(minutes=1)
    w.signal("Three")
    w.later(minutes=1)
    w.send("viewer", d["id"])
    text = w.delivered_text(kind)
    assert "Three" in text and "One" not in text and w.outgoing() == 2


@pytest.mark.parametrize("kind", KINDS)
def test_a_duplicate_queue_message_sends_nothing_twice(w: World, kind: str) -> None:
    did = ready(w, kind)
    w.send("viewer", did, "m1")
    w.later(seconds=30)
    w.send("viewer", did, "m1")
    assert w.outgoing() == 1


@pytest.mark.parametrize("kind", ["slack_channel", "email"])
def test_a_list_cut_at_twenty_sends_the_rest_next_time(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    for i in range(25):
        w.signal(f"Item {i:02d}")
        w.later(seconds=1)
    w.send("viewer", d["id"])
    first = w.delivered_text(kind)
    assert "(20+)" in first and "Item 00" in first and "Item 19" in first and "Item 20" not in first
    cursor = str(w.stored("viewer", d["id"]).cursor)
    assert "#" in cursor and cursor < iso(w.now())  # the key of the 20th record, not now
    w.later(minutes=1)
    w.send("viewer", d["id"])
    second = w.delivered_text(kind)
    assert "Item 20" in second and "Item 24" in second and "Item 19" not in second and "(5)" in second


@pytest.mark.parametrize("kind", KINDS)
def test_reading_in_the_app_does_not_move_a_delivery(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    w.signal("Unread by mail")
    w.later(minutes=1)
    assert w.call("POST", "/api/me/digest/read", {}, role="viewer")[0] == 200
    w.later(minutes=1)
    w.send("viewer", d["id"])
    assert "Unread by mail" in w.delivered_text(kind)
    assert w.ctx.digests.last_read(w.subject("viewer")) is not None


@pytest.mark.parametrize("kind", KINDS)
def test_a_disabled_delivery_is_skipped(w: World, kind: str) -> None:
    did = ready(w, kind)
    w.patch("viewer", did, {"enabled": False})
    assert w.send("viewer", did) == {"batchItemFailures": []}
    assert w.outgoing() == 0 and w.stored("viewer", did).last_status is None
    assert w.ctx.delivery_runner.deliver(w.subject("viewer"), did) == "skipped"


@pytest.mark.parametrize("kind", KINDS)
def test_a_deleted_delivery_is_skipped_and_not_brought_back(w: World, kind: str) -> None:
    did = ready(w, kind)
    w.call("DELETE", f"/api/me/deliveries/{did}", role="viewer")
    assert w.send("viewer", did) == {"batchItemFailures": []}
    assert w.outgoing() == 0 and w.item("viewer", did) is None


OFF_FOR = {"slack_channel": "email_ses", "slack_dm": "slack_bot", "email": "dm_email"}


@pytest.mark.parametrize("kind", KINDS)
def test_a_kind_switched_off_after_creation_is_not_sent(w: World, kind: str) -> None:
    did = ready(w, kind)
    w.set_mode(OFF_FOR[kind])
    assert w.ctx.delivery_runner.deliver(w.subject("viewer"), did) == "off"
    assert w.send("viewer", did) == {"batchItemFailures": []}
    assert w.outgoing() == 0 and w.stored("viewer", did).last_status is None


def test_a_suppressed_email_is_skipped(w: World) -> None:
    did = ready(w, "email")
    w.ctx.deliveries.switch_off(w.subject("viewer"), did, suppress=True, reason="bounced")
    assert w.send("viewer", did) == {"batchItemFailures": []} and w.mails() == []


def test_a_channel_removed_by_an_admin_stops_and_comes_back(w: World) -> None:
    did = ready(w, "slack_channel")
    assert w.set_channels()[0] == 200
    w.send("viewer", did)
    stored = w.stored("viewer", did)
    assert w.posts() == [] and stored.last_status is DeliveryStatus.ERROR and stored.cursor is None
    assert stored.last_error == "an administrator removed this channel from the list"
    w.set_channels((CHANNEL, CHANNEL_LABEL))
    w.send("viewer", did)
    assert len(w.posts()) == 1 and w.stored("viewer", did).last_status is DeliveryStatus.OK


def test_a_webhook_delivery_stops_when_the_deployment_moves_to_the_bot(make: Any) -> None:
    w = make("webhook")
    did = ready(w, "slack_channel")
    w.set_mode("slack_bot")
    w.send("viewer", did)
    assert w.posts() == [] and "made for the webhook channel" in str(w.stored("viewer", did).last_error)
    for _ in range(3):  # the operator can switch back: never counted, in either direction (scan cycle 6)
        w.send("viewer", did)
    assert w.stored("viewer", did).enabled is True and "fails" not in (w.item("viewer", did) or {})


def test_a_removed_channel_is_counted_even_when_the_digest_is_empty(w: World) -> None:
    """Scan cycle 6: the target is checked before the digest is built, so an empty day neither hides the fault
    nor resets the count of failed sends."""
    d = w.made("viewer", "slack_channel")
    assert w.set_channels()[0] == 200
    for _ in range(3):
        w.later(hours=1)  # nothing new: the digest is empty every time
        w.send("viewer", d["id"])
    stored = w.stored("viewer", d["id"])
    assert w.posts() == [] and stored.enabled is False
    assert "an administrator removed this channel from the list" in str(stored.last_error)


def _faults(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if '"digest.' in r.getMessage() and "_fault" in r.getMessage()
    ]


@pytest.mark.parametrize("code", ["missing_scope", "not_in_channel", "channel_not_found"])
def test_a_deployment_fault_is_logged_for_the_alarm(w: World, caplog: pytest.LogCaptureFixture, code: str) -> None:
    did = ready(w, "slack_channel")
    w.http.replies = [slack_error(code)]
    with caplog.at_level("WARNING"):
        w.send("viewer", did)
    assert _faults(caplog) == [{"event": "digest.deployment_fault", "delivery": did, "code": code}]


def test_a_mode_switch_is_logged_but_not_alarmed(w: World, caplog: pytest.LogCaptureFixture) -> None:
    """Scan cycle 7: faults the operator chose or cannot fix stay out of the alarm, so it is not raised every day."""
    did = ready(w, "slack_channel")
    w.set_mode("webhook")
    with caplog.at_level("WARNING"):
        w.send("viewer", did)
    assert _faults(caplog) == [{"event": "digest.delivery_fault", "delivery": did, "code": "channel_removed"}]


@pytest.mark.parametrize("change", ["disabled", "deleted"])
def test_a_gone_owner_is_switched_off_even_after_a_mode_switch(w: World, change: str) -> None:
    """Scan cycle 7: the owner's account is checked before the target, so a mode switch never keeps a gone
    owner's delivery alive for good."""
    did = ready(w, "slack_channel", role="editor")
    w.set_mode("webhook")
    LIVE[change][0](w)
    for _ in range(3):
        w.later(hours=1)
        w.send("editor", did)
    stored = w.stored("editor", did)
    assert w.posts() == [] and stored.enabled is False and "channel" not in str(stored.last_error)


def test_send_test_now_reports_a_dead_target_when_nothing_is_new(w: World) -> None:
    d = w.made("viewer", "slack_channel")
    assert w.set_channels()[0] == 200
    got = w.ctx.delivery_runner.test(w.stored("viewer", d["id"]))
    assert got == {"sent": False, "total": 0, "error": "an administrator removed this channel from the list"}


def test_a_bot_channel_delivery_is_not_rerouted_to_the_webhook(w: World) -> None:
    did = ready(w, "slack_channel")
    w.set_mode("webhook")
    w.send("viewer", did)
    assert w.posts() == []  # the subscriber picked #radar-news, not the webhook's channel
    assert "not available with the webhook" in str(w.stored("viewer", did).last_error)
    for _ in range(3):  # a deployment setting the operator can change back: never counted (scan cycle 5)
        w.send("viewer", did)
    assert w.stored("viewer", did).enabled is True


def test_an_unset_unsubscribe_key_sends_no_mail(w: World) -> None:
    did = ready(w, "email")
    w.out.key = "short"
    assert w.send("viewer", did) == {"batchItemFailures": []}
    stored = w.stored("viewer", did)
    assert w.mails() == [] and stored.last_error == "the unsubscribe key is not set" and stored.cursor is None


@pytest.mark.parametrize("kind", ["slack_channel", "slack_dm"])
def test_a_placeholder_token_at_send_time_is_not_configured(w: World, kind: str) -> None:
    did = ready(w, kind)
    w.out.token = NOT_A_REAL_VALUE
    w.send("viewer", did)
    assert w.http.calls == [] and w.stored("viewer", did).last_error == "Slack is not configured on this deployment"


def test_a_placeholder_webhook_at_send_time_is_not_configured(make: Any) -> None:
    w = make("webhook")
    did = ready(w, "slack_channel")
    w.out.webhook_url = "GeneratedPlaceholder0123456789"
    w.send("viewer", did)
    assert w.http.calls == [] and w.stored("viewer", did).last_error == "Slack is not configured on this deployment"


# ---- the subscriber is checked at every send ----------------------------------------------------------------------


def _disable(w: World) -> None:
    w.ctx.users.set_enabled(w.people["editor"].real, False)


def _delete(w: World) -> None:
    w.ctx.cognito.admin_delete_user(UserPoolId=w.ctx.settings.user_pool_id, Username=w.people["editor"].real)


def _readdress(w: World) -> None:
    w.ctx.cognito.admin_update_user_attributes(
        UserPoolId=w.ctx.settings.user_pool_id,
        Username=w.people["editor"].real,
        UserAttributes=[{"Name": "email", "Value": "moved@example.com"}],
    )


LIVE = {
    "disabled": (_disable, "your account is disabled"),
    "deleted": (_delete, "your account no longer exists"),
    "address_changed": (_readdress, "your email address changed: delete this delivery and add it again"),
}


@pytest.mark.parametrize("change", list(LIVE))
@pytest.mark.parametrize("kind", ["slack_dm", "email"])
def test_a_personal_delivery_stops_when_the_subscriber_changes(w: World, kind: str, change: str) -> None:
    did = ready(w, kind, role="editor")
    act, message = LIVE[change]
    act(w)
    assert w.send("editor", did) == {"batchItemFailures": []}
    stored = w.stored("editor", did)
    assert w.outgoing() == 0 and stored.last_status is DeliveryStatus.ERROR and stored.last_error == message
    assert stored.cursor is None


@pytest.mark.parametrize("kind", ["slack_dm", "email"])
def test_a_demoted_subscriber_gets_a_viewers_digest(w: World, kind: str) -> None:
    d = w.made("editor", kind)
    w.node(NodeType.GAP, "Editors only gap")
    w.later(minutes=1)
    w.ctx.users.set_role(w.people["editor"].real, Role.VIEWER)
    w.send("editor", d["id"])
    assert w.outgoing() == 0 and w.stored("editor", d["id"]).last_status is DeliveryStatus.EMPTY


@pytest.mark.parametrize("change", ["address_changed"])
def test_a_channel_delivery_does_not_depend_on_the_subscribers_address(w: World, change: str) -> None:
    did = ready(w, "slack_channel", role="editor")
    LIVE[change][0](w)
    w.send("editor", did)
    assert len(w.posts()) == 1


@pytest.mark.parametrize("change", ["disabled", "deleted"])
def test_a_channel_delivery_stops_when_the_subscriber_is_gone(w: World, change: str) -> None:
    did = ready(w, "slack_channel", role="editor")
    LIVE[change][0](w)
    w.send("editor", did)
    assert w.posts() == [] and w.stored("editor", did).last_status is DeliveryStatus.ERROR


# =====================================================================================================================
# 9. The sender: transient and permanent errors
# =====================================================================================================================


PERMANENT_CODES = sorted(SLACK_PERMANENT - {"slack_not_configured"})


@pytest.mark.parametrize("code", PERMANENT_CODES)
@pytest.mark.parametrize("kind", ["slack_channel", "slack_dm"])
def test_permanent_slack_errors_are_recorded_and_not_retried(w: World, kind: str, code: str) -> None:
    did = ready(w, kind)
    tries = 2 if code in SLACK_AUTH else 1  # a revoked token is read again once
    w.http.replies = [*prefix(kind), *[slack_error(code)] * tries]
    assert w.send("viewer", did) == {"batchItemFailures": []}
    stored = w.stored("viewer", did)
    assert stored.last_status is DeliveryStatus.ERROR and stored.last_error == f"Slack: {code}"
    assert stored.cursor is None and stored.last_sent_at is None
    assert len(w.posts()) == tries and w.ctx.sqs.visibility == []


@pytest.mark.parametrize("code", sorted(SLACK_AUTH))
@pytest.mark.parametrize("kind", ["slack_channel", "slack_dm"])
def test_a_rotated_token_is_read_again_and_the_post_goes_through(w: World, kind: str, code: str) -> None:
    did = ready(w, kind)
    w.http.replies = [*prefix(kind), slack_error(code)]
    w.send("viewer", did)
    assert len(w.posts()) == 2 and w.stored("viewer", did).last_status is DeliveryStatus.OK


TRANSIENT_HTTP = [
    ("http_500", HttpResponse(500, {}, b""), "Slack answered 500", None),
    ("http_502", HttpResponse(502, {}, b"bad gateway"), "Slack answered 502", None),
    ("http_503", HttpResponse(503, {}, b""), "Slack answered 503", None),
    ("http_504", HttpResponse(504, {}, b""), "Slack answered 504", None),
    ("429_1s", HttpResponse(429, {"retry-after": "1"}, b""), "Slack rate limit", 1),
    ("429_30s", HttpResponse(429, {"retry-after": "30"}, b""), "Slack rate limit", 30),
    ("429_120s", HttpResponse(429, {"retry-after": "120"}, b""), "Slack rate limit", 120),
    ("429_900s", HttpResponse(429, {"retry-after": "900"}, b""), "Slack rate limit", 900),
    ("429_capped", HttpResponse(429, {"retry-after": "3600"}, b""), "Slack rate limit", 900),
    ("429_garbled", HttpResponse(429, {"retry-after": "soon"}, b""), "Slack rate limit", 30),
    ("429_no_header", HttpResponse(429, {}, b""), "Slack rate limit", 30),
    ("ok_false_ratelimited", slack_error("ratelimited", headers={"retry-after": "45"}), "Slack rate limit", 45),
    ("not_json", HttpResponse(200, {}, b"<html>"), "Slack answered 200 without JSON", None),
    ("json_list", HttpResponse(200, {}, b"[1]"), "Slack answered 200 without JSON", None),
    ("internal_error", slack_error("internal_error"), "Slack: internal_error", None),
    ("fatal_error", slack_error("fatal_error"), "Slack: fatal_error", None),
    ("service_unavailable", slack_error("service_unavailable"), "Slack: service_unavailable", None),
    ("request_timeout", slack_error("request_timeout"), "Slack: request_timeout", None),
    ("new_code", slack_error("Brand New-Code!"), "Slack: brand_new_code", None),
]


@pytest.mark.parametrize(
    ("reply", "message", "wait"), [c[1:] for c in TRANSIENT_HTTP], ids=[c[0] for c in TRANSIENT_HTTP]
)
@pytest.mark.parametrize("kind", ["slack_channel", "slack_dm"])
def test_transient_slack_errors_keep_the_cursor_and_retry(
    w: World, kind: str, reply: HttpResponse, message: str, wait: int | None
) -> None:
    did = ready(w, kind)
    w.http.replies = [*prefix(kind), reply]
    out = w.send("viewer", did)
    stored = w.stored("viewer", did)
    assert stored.last_status is DeliveryStatus.ERROR and stored.last_error == f"{message} (will retry)"
    assert stored.cursor is None and w.ctx.sqs.visibility == []
    w.later(minutes=1)
    if wait is None:
        assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
        assert w.ctx.sqs.messages == []
        retried = w.send("viewer", did)  # the queue receives the same message again
    else:
        # A rate limit does not use up a receive: the send goes back on the queue, delayed by Retry-After, as a
        # new message that keeps the first one's claim on the scheduled time (scan cycle 1, L6).
        assert out == {"batchItemFailures": []}
        (again,) = w.ctx.sqs.messages
        assert again["QueueUrl"] == QUEUE and again["DelaySeconds"] == wait
        assert json.loads(again["MessageBody"]) == {
            "subject": w.subject("viewer"),
            "id": did,
            "due": "due-m1",
            "claim": "m1",
            "requeues": 1,
        }
        retried = w.redeliver()
    # The retry goes through and clears the error.
    assert retried == {"batchItemFailures": []}
    stored = w.stored("viewer", did)
    assert stored.last_status is DeliveryStatus.OK and stored.last_error is None and stored.cursor == iso(w.now())


NETWORK_ERRORS = [
    httpcore.ReadTimeout,
    httpcore.ConnectTimeout,
    httpcore.WriteTimeout,
    httpcore.PoolTimeout,
    httpcore.ConnectError,
    httpcore.ReadError,
    httpcore.WriteError,
    httpcore.RemoteProtocolError,
]


@pytest.mark.parametrize("error", NETWORK_ERRORS, ids=[e.__name__ for e in NETWORK_ERRORS])
@pytest.mark.parametrize("target", ["slack_channel", "slack_dm", "webhook"])
def test_network_failures_through_the_real_http_client_are_retried(
    make: Any, monkeypatch: pytest.MonkeyPatch, target: str, error: type[Exception]
) -> None:
    w = make("webhook" if target == "webhook" else "full")
    did = ready(w, "slack_channel" if target == "webhook" else target)
    calls: list[str] = []

    def fail(method: str, url: str, **kw: Any) -> Any:
        calls.append(url)
        assert kw["extensions"]["timeout"]["read"] == 10.0
        raise error("boom")

    monkeypatch.setattr(httpcore, "request", fail)
    real = HttpcoreClient()
    w.out.slack = SlackClient(lambda fresh: w.out.token, real)
    w.out.webhook = SlackWebhook(lambda: w.out.webhook_url, real)
    assert w.send("viewer", did) == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    stored = w.stored("viewer", did)
    assert stored.last_error == f"network error: {error.__name__} (will retry)" and stored.cursor is None
    assert len(calls) == 1 and calls[0].startswith("https://")


WEBHOOK_REPLIES = [
    ("invalid_payload", HttpResponse(400, {}, b"invalid_payload"), False, "Slack webhook: invalid_payload"),
    ("action_prohibited", HttpResponse(403, {}, b"action_prohibited"), False, "Slack webhook: action_prohibited"),
    ("no_service", HttpResponse(404, {}, b"no_service"), False, "Slack webhook: no_service"),
    ("archived", HttpResponse(410, {}, b"channel_is_archived"), False, "Slack webhook: channel_is_archived"),
    ("no_active_hooks", HttpResponse(404, {}, b"no_active_hooks"), False, "Slack webhook: no_active_hooks"),
    ("empty_400", HttpResponse(400, {}, b""), False, "Slack webhook: http_400"),
    ("invalid_token", HttpResponse(401, {}, b"invalid_token"), False, "Slack webhook: invalid_token"),
    ("429", HttpResponse(429, {"retry-after": "60"}, b""), True, "Slack rate limit (will retry)"),
    ("500", HttpResponse(500, {}, b""), True, "Slack answered 500 (will retry)"),
    ("502", HttpResponse(502, {}, b"x"), True, "Slack answered 502 (will retry)"),
    ("503", HttpResponse(503, {}, b""), True, "Slack answered 503 (will retry)"),
]


@pytest.mark.parametrize(
    ("reply", "transient", "message"), [c[1:] for c in WEBHOOK_REPLIES], ids=[c[0] for c in WEBHOOK_REPLIES]
)
def test_webhook_answers(make: Any, reply: HttpResponse, transient: bool, message: str) -> None:
    w = make("webhook")
    did = ready(w, "slack_channel")
    w.http.replies = [reply]
    failures = w.send("viewer", did)["batchItemFailures"]
    stored = w.stored("viewer", did)
    requeued = len(w.ctx.sqs.messages) == 1  # a rate limit goes back on the queue instead of failing
    assert (bool(failures) or requeued) is transient and stored.last_error == message and stored.cursor is None
    assert requeued is (reply.status == 429)


SES_PERMANENT_CODES = [
    "MessageRejected",
    "MailFromDomainNotVerifiedException",
    "AccountSuspendedException",
    "NotFoundException",
    "BadRequestException",
    "AccessDeniedException",
]


@pytest.mark.parametrize("code", SES_PERMANENT_CODES)
def test_permanent_ses_errors_are_recorded_and_not_retried(w: World, code: str) -> None:
    did = ready(w, "email")
    w.ses.errors = [ClientError({"Error": {"Code": code, "Message": "m"}}, "SendEmail")]
    assert w.send("viewer", did) == {"batchItemFailures": []}
    stored = w.stored("viewer", did)
    assert stored.last_error == f"SES: {code}" and stored.cursor is None and w.mails() == []
    assert stored.last_status is DeliveryStatus.ERROR


SES_TRANSIENT = [
    (
        "TooManyRequests",
        ClientError({"Error": {"Code": "TooManyRequestsException"}}, "SendEmail"),
        "SES: TooManyRequestsException",
    ),
    (
        "LimitExceeded",
        ClientError({"Error": {"Code": "LimitExceededException"}}, "SendEmail"),
        "SES: LimitExceededException",
    ),
    ("Throttling", ClientError({"Error": {"Code": "Throttling"}}, "SendEmail"), "SES: Throttling"),
    ("InternalFailure", ClientError({"Error": {"Code": "InternalFailure"}}, "SendEmail"), "SES: InternalFailure"),
    (
        "ServiceUnavailable",
        ClientError({"Error": {"Code": "ServiceUnavailable"}}, "SendEmail"),
        "SES: ServiceUnavailable",
    ),
    (
        "SendingPaused",
        ClientError({"Error": {"Code": "SendingPausedException"}}, "SendEmail"),
        "SES: SendingPausedException",
    ),
    (
        "EndpointConnection",
        EndpointConnectionError(endpoint_url="https://email.us-east-1.amazonaws.com"),
        "SES: EndpointConnectionError",
    ),
    ("ReadTimeout", ReadTimeoutError(endpoint_url="https://email.us-east-1.amazonaws.com"), "SES: ReadTimeoutError"),
    (
        "ConnectTimeout",
        ConnectTimeoutError(endpoint_url="https://email.us-east-1.amazonaws.com"),
        "SES: ConnectTimeoutError",
    ),
]


@pytest.mark.parametrize(("error", "message"), [c[1:] for c in SES_TRANSIENT], ids=[c[0] for c in SES_TRANSIENT])
def test_transient_ses_errors_keep_the_cursor_and_retry(w: World, error: Exception, message: str) -> None:
    did = ready(w, "email")
    w.ses.errors = [error]
    assert w.send("viewer", did) == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    stored = w.stored("viewer", did)
    assert stored.last_error == f"{message} (will retry)" and stored.cursor is None and w.mails() == []
    w.later(minutes=1)
    w.send("viewer", did)
    assert len(w.mails()) == 1 and w.stored("viewer", did).last_status is DeliveryStatus.OK


@pytest.mark.parametrize("kind", KINDS)
def test_a_permanent_error_is_tried_again_at_the_next_schedule(w: World, kind: str) -> None:
    did = ready(w, kind)
    if kind == "email":
        w.ses.errors = [ClientError({"Error": {"Code": "MessageRejected"}}, "SendEmail")]
    else:
        w.http.replies = [*prefix(kind), slack_error("not_in_channel")]
    w.send("viewer", did)
    assert w.stored("viewer", did).last_status is DeliveryStatus.ERROR and w.outgoing() == (1 if kind != "email" else 0)
    w.later(days=1)
    w.send("viewer", did, "m2")
    stored = w.stored("viewer", did)
    assert stored.last_status is DeliveryStatus.OK and stored.last_error is None


DM_LOOKUPS = [
    ("users_not_found", [slack_error("users_not_found")], False, "no Slack user has your email address"),
    ("user_not_found", [slack_error("user_not_found")], False, "no Slack user has your email address"),
    (
        "lookup_bad_id",
        [HttpResponse(200, {}, b'{"ok": true, "user": {"id": "nope"}}')],
        False,
        "no Slack user has your email address",
    ),
    ("lookup_no_user", [HttpResponse(200, {}, b'{"ok": true}')], False, "no Slack user has your email address"),
    ("lookup_missing_scope", [slack_error("missing_scope")], False, "Slack: missing_scope"),
    ("lookup_500", [HttpResponse(500, {}, b"")], True, "Slack answered 500 (will retry)"),
    ("open_user_disabled", [OK_LOOKUP, slack_error("user_disabled")], False, "Slack: user_disabled"),
    ("open_cannot_dm_bot", [OK_LOOKUP, slack_error("cannot_dm_bot")], False, "Slack: cannot_dm_bot"),
    (
        "open_no_channel",
        [OK_LOOKUP, HttpResponse(200, {}, b'{"ok": true, "channel": {}}')],
        True,
        "Slack opened no DM (will retry)",
    ),
    (
        "open_not_a_dm",
        [OK_LOOKUP, HttpResponse(200, {}, json.dumps({"ok": True, "channel": {"id": CHANNEL}}).encode())],
        True,
        "Slack opened no DM (will retry)",
    ),
]


@pytest.mark.parametrize(
    ("replies", "transient", "message"), [c[1:] for c in DM_LOOKUPS], ids=[c[0] for c in DM_LOOKUPS]
)
def test_dm_by_email_lookup_and_open_failures(
    w: World, replies: list[HttpResponse], transient: bool, message: str
) -> None:
    did = ready(w, "slack_dm")
    w.http.replies = list(replies)
    failures = w.send("viewer", did)["batchItemFailures"]
    stored = w.stored("viewer", did)
    assert bool(failures) is transient and stored.last_error == message and w.posts() == []
    assert stored.cursor is None


DIRECTORY_REPLIES = [
    ("not_found", HttpResponse(404, {}, b"{}"), False, "no Slack user for your alias"),
    ("denied_401", HttpResponse(401, {}, b"{}"), False, "the Slack lookup refused this deployment"),
    ("denied_403", HttpResponse(403, {}, b"{}"), False, "the Slack lookup refused this deployment"),
    ("throttled", HttpResponse(429, {}, b"{}"), True, "the Slack lookup answered 429 (will retry)"),
    ("server_500", HttpResponse(500, {}, b""), True, "the Slack lookup answered 500 (will retry)"),
    ("server_503", HttpResponse(503, {}, b""), True, "the Slack lookup answered 503 (will retry)"),
    ("bad_id", HttpResponse(200, {}, b'{"slackId": "not-an-id"}'), False, "no Slack user for your alias"),
    # Scan cycles 8 and 9: an answer the deployment cannot use (a proxy page, a wrong path or query) says so.
    ("not_json", HttpResponse(200, {}, b"<html>"), False, "the Slack lookup did not answer with JSON"),
    ("bad_request_400", HttpResponse(400, {}, b"{}"), False, "the Slack lookup answered 400"),
    ("moved_301", HttpResponse(301, {}, b""), False, "the Slack lookup answered 301"),
]


def _misses(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(r.getMessage()) for r in caplog.records if '"digest.lookup_miss"' in r.getMessage()]


def test_a_directory_lookup_miss_feeds_the_rate_alarm_and_switches_off_late(
    make: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Scan cycles 9 and 10: no Slack user for an alias is logged for the DigestLookupMisses rate alarm, never
    for the per-fault alarm, and switched off only after LOOKUP_MISS_LIMIT sends, so one missing person's misses
    stop and cannot hold the alarm in ALARM."""
    w = make("dm_directory")
    did = ready(w, "slack_dm")
    for n in range(LOOKUP_MISS_LIMIT):
        assert w.stored("viewer", did).enabled is True, n
        w.http.directory_replies = [HttpResponse(404, {}, b"{}")]
        with caplog.at_level("WARNING"):
            w.send("viewer", did)
    misses = [{"event": "digest.lookup_miss", "delivery": did, "code": "slack_user_not_found"}] * LOOKUP_MISS_LIMIT
    assert _misses(caplog) == misses and _faults(caplog) == []
    stored = w.stored("viewer", did)
    assert stored.enabled is False and f"switched off after {LOOKUP_MISS_LIMIT}" in str(stored.last_error)


@pytest.mark.parametrize(
    "reply", [HttpResponse(400, {}, b"{}"), HttpResponse(200, {}, b"<html>")], ids=["bad_request", "not_json"]
)
def test_an_unusable_directory_answer_alarms_at_once(
    make: Any, caplog: pytest.LogCaptureFixture, reply: HttpResponse
) -> None:
    """Scan cycle 10: a wrong path, a proxy or a login page is never about one person: the first one alarms."""
    w = make("dm_directory")
    did = ready(w, "slack_dm")
    w.http.directory_replies = [reply]
    with caplog.at_level("WARNING"):
        w.send("viewer", did)
    assert _faults(caplog) == [{"event": "digest.deployment_fault", "delivery": did, "code": "directory_bad_answer"}]
    assert _misses(caplog) == [] and "fails" not in (w.item("viewer", did) or {})


@pytest.mark.parametrize("code", ["user_disabled", "cannot_dm_bot"])
def test_a_deactivated_slack_account_switches_the_dm_off(w: World, code: str) -> None:
    """Scan cycle 8: the person's own Slack account, which no setting produces, counts like a gone account."""
    did = ready(w, "slack_dm")
    for _ in range(3):
        w.http.replies = [*prefix("slack_dm")[:1], slack_error(code)]
        w.send("viewer", did)
    stored = w.stored("viewer", did)
    assert stored.enabled is False and f"Slack: {code}" in str(stored.last_error)


def test_an_empty_day_neither_resets_the_count_nor_hides_the_error(w: World) -> None:
    """Scan cycles 9 and 10: an empty day tried nothing, so a counted fault stands (its count, its error, shown as
    failed); any other error, a passing one or one already fixed, goes."""
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "Slack: user_disabled")
    w.later(hours=1)
    w.send("viewer", d["id"])  # nothing new
    stored = w.stored("viewer", d["id"])
    assert stored.last_status is DeliveryStatus.ERROR and stored.last_error == "Slack: user_disabled"
    assert stored.fails == 1


def test_an_empty_day_clears_an_uncounted_error(w: World) -> None:
    d = w.made("viewer", "slack_dm")
    w.ctx.deliveries.record(w.subject("viewer"), d["id"], DeliveryStatus.ERROR, "network error (will retry)")
    w.later(hours=1)
    w.send("viewer", d["id"])  # nothing new
    stored = w.stored("viewer", d["id"])
    assert stored.last_status is DeliveryStatus.EMPTY and stored.last_error is None


def test_user_is_bot_is_not_counted(w: World) -> None:
    """Scan cycle 9: Slack also answers user_is_bot about the calling token, so it switches nobody off."""
    did = ready(w, "slack_dm")
    for _ in range(3):
        w.http.replies = [*prefix("slack_dm")[:1], slack_error("user_is_bot")]
        w.send("viewer", did)
    assert w.stored("viewer", did).enabled is True


def test_a_real_send_clears_the_count(w: World) -> None:
    did = ready(w, "slack_dm")
    for _ in range(2):
        w.http.replies = [*prefix("slack_dm")[:1], slack_error("user_disabled")]
        w.send("viewer", did)
    w.send("viewer", did)  # Slack takes it this time
    assert "fails" not in (w.item("viewer", did) or {}) and w.stored("viewer", did).last_error is None


def test_a_dead_target_fails_before_the_digest_is_built(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    did = ready(w, "slack_channel")
    assert w.set_channels()[0] == 200
    built: list[str] = []
    digests = w.ctx.delivery_runner._digests
    real = digests.digest
    monkeypatch.setattr(digests, "digest", lambda *a, **k: built.append("x") or real(*a, **k))
    w.send("viewer", did)
    assert w.ctx.delivery_runner.test(w.stored("viewer", did))["sent"] is False and built == []


@pytest.mark.parametrize(
    ("reply", "transient", "message"), [c[1:] for c in DIRECTORY_REPLIES], ids=[c[0] for c in DIRECTORY_REPLIES]
)
def test_dm_by_directory_lookup_failures(make: Any, reply: HttpResponse, transient: bool, message: str) -> None:
    w = make("dm_directory")
    did = ready(w, "slack_dm")
    w.http.directory_replies = [reply]
    failures = w.send("viewer", did)["batchItemFailures"]
    stored = w.stored("viewer", did)
    assert bool(failures) is transient and stored.last_error == message and w.posts() == []


@pytest.mark.parametrize(
    ("body", "found"),
    [
        ({"slack_id": "U0NESTED01"}, "U0NESTED01"),
        ({"user": {"slackUserId": "W0ENTERPR1"}}, "W0ENTERPR1"),
        ({"data": {"id": "U0WRAPPED1"}}, "U0WRAPPED1"),
    ],
    ids=["snake", "nested", "wrapped"],
)
def test_dm_by_directory_reads_the_usual_answer_shapes(make: Any, body: dict[str, Any], found: str) -> None:
    w = make("dm_directory")
    did = ready(w, "slack_dm")
    w.http.directory_replies = [HttpResponse(200, {}, json.dumps(body).encode())]
    w.send("viewer", did)
    assert w.http.slack_calls("conversations.open")[0]["body"] == {"users": found} and len(w.posts()) == 1


def test_dm_by_directory_never_searches_deeper_than_one_record(make: Any) -> None:
    """Scan cycle 3: an ID two records down may be someone else's (a manager's), so it is not taken."""
    w = make("dm_directory")
    did = ready(w, "slack_dm")
    w.http.directory_replies = [HttpResponse(200, {}, json.dumps({"data": {"user": {"id": "U0DEEPDEEP"}}}).encode())]
    w.send("viewer", did)
    assert w.http.slack_calls("conversations.open") == [] and w.posts() == []
    assert w.stored("viewer", did).last_error == "no Slack user for your alias"


def test_dm_by_directory_role_failure_is_retried(make: Any) -> None:
    w = make("dm_directory")
    did = ready(w, "slack_dm")
    w.out.sts.error = ClientError({"Error": {"Code": "AccessDenied"}}, "AssumeRole")
    assert w.send("viewer", did)["batchItemFailures"]
    assert w.stored("viewer", did).last_error == "could not assume the Slack lookup role (will retry)"


# =====================================================================================================================
# 10. Visibility: a channel reads what every viewer may; a DM or an email the subscriber's own view
# =====================================================================================================================


RECORDS = {
    "public_signal": lambda w: w.signal("Public signal title"),
    "private_signal": lambda w: w.signal("Private signal title", private=True),
    "gap": lambda w: w.node(NodeType.GAP, "Roadmap gap title"),
    "content_idea": lambda w: w.node(NodeType.CONTENT_IDEA, "Content idea title"),
}
TITLES = {
    "public_signal": "Public signal title",
    "private_signal": "Private signal title",
    "gap": "Roadmap gap title",
    "content_idea": "Content idea title",
}


@pytest.mark.parametrize("record", list(RECORDS))
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("role", ["viewer", "editor", "admin"])
def test_visibility(w: World, role: str, kind: str, record: str) -> None:
    d = w.made(role, kind)
    w.later(minutes=1)
    w.signal("Always visible")
    RECORDS[record](w)
    w.later(minutes=1)
    w.send(role, d["id"])
    text = w.delivered_text(kind)
    shown = record == "public_signal" or (kind != "slack_channel" and role != "viewer")
    assert (TITLES[record] in text) is shown
    assert "Always visible" in text


@pytest.mark.parametrize("role", ["editor", "admin"])
def test_a_channel_test_send_by_a_privileged_subscriber_is_still_a_viewers(w: World, role: str) -> None:
    d = w.made(role, "slack_channel")
    w.node(NodeType.GAP, "Roadmap gap title")
    w.signal("Shared title", private=True)
    w.signal("Public title")
    s, b, _ = w.test_send(role, d["id"])
    assert s == 200 and b == {"sent": True, "total": 1}
    assert (
        "Public title" in w.post_text() and "Roadmap gap" not in w.post_text() and "Shared title" not in w.post_text()
    )


@pytest.mark.parametrize("kind", KINDS)
def test_a_watched_private_record_is_not_listed_to_a_viewer(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    org = w.watch("viewer", "Acme", facts=1)
    w.ctx.graph._table.update_item(
        Key={"pk": f"N#{org.id}", "sk": "META"},
        UpdateExpression="SET #p = :t",
        ExpressionAttributeNames={"#p": "private"},
        ExpressionAttributeValues={":t": True},
    )
    w.later(minutes=1)
    w.send("viewer", d["id"])
    assert w.outgoing() == 0 or "Acme" not in w.delivered_text(kind)


# =====================================================================================================================
# 11. Send test now
# =====================================================================================================================


@pytest.mark.parametrize("kind", KINDS)
def test_test_send_with_nothing_new(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    s, b, _ = w.test_send("viewer", d["id"])
    assert s == 200 and b == {"sent": False, "total": 0} and w.outgoing() == 0


@pytest.mark.parametrize("kind", KINDS)
def test_test_send_sends_without_moving_anything(w: World, kind: str) -> None:
    did = ready(w, kind)
    before = w.item("viewer", did) or {}
    s, b, _ = w.test_send("viewer", did)
    assert s == 200 and b == {"sent": True, "total": 1} and w.outgoing() == 1
    after = w.item("viewer", did) or {}
    assert after.get("cursor") is None and after.get("last_status") is None and after["next_at"] == before["next_at"]
    assert after["test_at"] == iso(w.now())
    w.send("viewer", did)  # the scheduled send still has it
    assert w.outgoing() == 2


@pytest.mark.parametrize("minutes", range(10))
def test_test_send_rate_limit_counts_down(w: World, minutes: int) -> None:
    d = w.made("viewer", "email")
    assert w.test_send("viewer", d["id"])[0] == 200
    w.later(minutes=minutes)
    s, b, headers = w.test_send("viewer", d["id"])
    wait = 600 - minutes * 60
    assert s == 429 and b["code"] == "rate_limited"
    assert b["details"] == {"retry_after": wait, "limit_per_10_minutes": 1} and headers["Retry-After"] == str(wait)


@pytest.mark.parametrize(("seconds", "status"), [(599, 429), (600, 200), (601, 200), (3600, 200)])
def test_test_send_rate_limit_edge(w: World, seconds: int, status: int) -> None:
    d = w.made("viewer", "email")
    w.test_send("viewer", d["id"])
    w.later(seconds=seconds)
    s, b, _ = w.test_send("viewer", d["id"])
    assert s == status
    if status == 429:
        assert b["details"]["retry_after"] == 1


def test_a_rate_limited_test_sends_nothing(w: World) -> None:
    did = ready(w, "slack_channel")
    w.test_send("viewer", did)
    w.test_send("viewer", did)
    assert len(w.posts()) == 1


def test_the_rate_limit_is_per_delivery(w: World) -> None:
    a = w.made("viewer", "email")
    b = w.made("viewer", "slack_dm")
    assert w.test_send("viewer", a["id"])[0] == 200
    assert w.test_send("viewer", b["id"])[0] == 200
    assert w.test_send("viewer", a["id"])[0] == 429


def test_test_send_on_a_suppressed_delivery_conflicts(w: World) -> None:
    d = w.made("viewer", "email")
    w.ctx.deliveries.switch_off(w.subject("viewer"), d["id"], suppress=True, reason="bounced")
    s, b, _ = w.test_send("viewer", d["id"])
    assert s == 409 and b["details"]["code"] == "suppressed" and w.mails() == []


@pytest.mark.parametrize("kind", KINDS)
def test_test_send_works_on_a_paused_delivery(w: World, kind: str) -> None:
    did = ready(w, kind)
    w.patch("viewer", did, {"enabled": False})
    assert w.test_send("viewer", did)[1] == {"sent": True, "total": 1}


@pytest.mark.parametrize("kind", KINDS)
def test_test_send_for_a_kind_switched_off_takes_no_rate_limit(w: World, kind: str) -> None:
    did = ready(w, kind)
    w.set_mode(OFF_FOR[kind])
    for _ in range(2):
        s, b, _ = w.test_send("viewer", did)
        assert s == 200 and b == {
            "sent": False,
            "total": 0,
            "error": "this kind of delivery is switched off on this deployment",
        }
    assert w.outgoing() == 0


TEST_ERRORS = [
    ("slack_channel", slack_error("channel_not_found"), "Slack: channel_not_found"),
    ("slack_channel", HttpResponse(503, {}, b""), "Slack answered 503"),
    ("slack_dm", slack_error("users_not_found"), "no Slack user has your email address"),
    ("slack_dm", HttpResponse(429, {"retry-after": "9"}, b""), "Slack rate limit"),
]


@pytest.mark.parametrize(("kind", "reply", "message"), TEST_ERRORS, ids=[f"{k}-{m[:20]}" for k, _, m in TEST_ERRORS])
def test_test_send_reports_the_error(w: World, kind: str, reply: HttpResponse, message: str) -> None:
    did = ready(w, kind)
    w.http.replies = [reply]
    s, b, _ = w.test_send("viewer", did)
    assert s == 200 and b == {"sent": False, "total": 1, "error": message}
    assert w.stored("viewer", did).last_status is None  # a test records nothing


def test_test_send_reports_an_ses_rejection(w: World) -> None:
    did = ready(w, "email")
    w.ses.errors = [ClientError({"Error": {"Code": "MessageRejected"}}, "SendEmail")]
    assert w.test_send("viewer", did)[1] == {"sent": False, "total": 1, "error": "SES: MessageRejected"}


# =====================================================================================================================
# 12. Unsubscribe
# =====================================================================================================================


def mailed(w: World, role: str = "viewer") -> tuple[str, str]:
    """An email delivery and its unsubscribe token, as the mail carries it."""
    did = ready(w, "email", role=role)
    w.send(role, did)
    header = next(h["Value"] for h in w.mails()[-1]["Content"]["Simple"]["Headers"] if h["Name"] == "List-Unsubscribe")
    token = header.strip("<>").split("?t=", 1)[1]
    return did, token


def unsubscribed(w: World, role: str, did: str) -> bool:
    item = w.item(role, did) or {}
    return item.get("enabled") is False and "due_pk" not in item and item.get("suppressed") is False


def form(token: str) -> str:
    return f"t={quote(token)}"


VALID_WAYS = {
    "query": lambda w, t: w.call("POST", "/api/digest/unsubscribe", role="anon", query={"t": t}),
    "json_body": lambda w, t: w.call("POST", "/api/digest/unsubscribe", {"t": t}, role="anon"),
    "json_body_without_type": lambda w, t: w.call(
        "POST", "/api/digest/unsubscribe", role="anon", headers={}, raw_body=json.dumps({"t": t})
    ),
    "form_body": lambda w, t: w.call(
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        raw_body=form(t),
        headers={"content-type": "application/x-www-form-urlencoded"},
    ),
    "one_click": lambda w, t: w.call(
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        query={"t": t},
        raw_body="List-Unsubscribe=One-Click",
        headers={"content-type": "application/x-www-form-urlencoded"},
    ),
    "signed_in_as_someone_else": lambda w, t: w.call("POST", "/api/digest/unsubscribe", {"t": t}, role="editor"),
}


@pytest.mark.parametrize("way", list(VALID_WAYS))
def test_unsubscribe_works_signed_out(w: World, way: str) -> None:
    did, token = mailed(w)
    s, b, _ = VALID_WAYS[way](w, token)
    assert s == 200 and b == {"ok": True} and unsubscribed(w, "viewer", did)
    assert w.stored("viewer", did).last_status is DeliveryStatus.OK  # not marked as an error or suppressed


def test_unsubscribe_from_a_base64_form_post(w: World) -> None:
    did, token = mailed(w)
    event = {
        "rawPath": "/api/digest/unsubscribe",
        "requestContext": {"http": {"method": "POST", "path": "/api/digest/unsubscribe"}},
        "headers": {"content-type": "application/x-www-form-urlencoded"},
        "queryStringParameters": None,
        "body": base64.b64encode(form(token).encode()).decode(),
        "isBase64Encoded": True,
    }
    res = w.api.app.handle(event)
    assert res["statusCode"] == 200 and unsubscribed(w, "viewer", did)


def test_unsubscribe_from_a_browser_shows_a_page(w: World) -> None:
    did, token = mailed(w)
    s, page, headers = w.call(
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        raw_body=form(token),
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "text/html"},
    )
    assert s == 200 and headers["Content-Type"].startswith("text/html") and "You are unsubscribed" in page
    assert unsubscribed(w, "viewer", did)


def test_unsubscribe_turns_off_only_that_delivery(w: World) -> None:
    did, token = mailed(w)
    other = w.made("viewer", "email")["id"]
    dm = w.made("viewer", "slack_dm")["id"]
    theirs = w.made("editor", "email")["id"]
    assert w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 200
    assert unsubscribed(w, "viewer", did)
    assert w.stored("viewer", other).enabled and w.stored("viewer", dm).enabled and w.stored("editor", theirs).enabled
    w.at(TOMORROW_9_LONDON)
    assert w.tick()["queued"] == 3


def test_a_token_for_another_delivery_turns_off_that_one_only(w: World) -> None:
    a = w.made("viewer", "email")["id"]
    b = w.made("viewer", "email")["id"]
    token_b = w.out.unsubscribe.issue(w.subject("viewer"), b)
    assert w.call("POST", "/api/digest/unsubscribe", {"t": token_b}, role="anon")[0] == 200
    assert w.stored("viewer", a).enabled and not w.stored("viewer", b).enabled


@pytest.mark.parametrize("who", ["missing_delivery", "deleted_delivery", "other_subject"])
def test_a_token_naming_no_delivery_changes_nothing(w: World, who: str) -> None:
    did, token = mailed(w)
    if who == "missing_delivery":
        token = w.out.unsubscribe.issue(w.subject("viewer"), "0123456789ab")
    elif who == "deleted_delivery":
        w.call("DELETE", f"/api/me/deliveries/{did}", role="viewer")
    else:
        token = w.out.unsubscribe.issue(w.subject("editor"), did)
    before = all_deliveries(w)
    assert w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[:2] == (200, {"ok": True})
    assert all_deliveries(w) == before  # nothing created, nothing switched off


def _flip(s: str, i: int) -> str:
    return s[:i] + ("A" if s[i] != "A" else "B") + s[i + 1 :]


TAMPER = {
    "signature_flipped": lambda t: _flip(t, len(t) - 2),
    "payload_flipped": lambda t: _flip(t, 5),
    "version_v2": lambda t: "v2" + t[2:],
    "no_version": lambda t: t.split(".", 1)[1],
    "extra_part": lambda t: t + ".x",
    "empty_signature": lambda t: t.rsplit(".", 1)[0] + ".",
    "not_base64": lambda t: "v1.!!!!.@@@@",
    "too_long": lambda t: "v1." + "A" * 400 + "." + "A" * 300,
    "swapped_parts": lambda t: "v1." + t.split(".")[2] + "." + t.split(".")[1],
    "garbage": lambda t: "hello",
    "empty_payload": lambda t: "v1.." + t.split(".")[2],
    "truncated": lambda t: t[:-10],
}


@pytest.mark.parametrize("how", list(TAMPER))
def test_a_tampered_token_is_refused(w: World, how: str) -> None:
    did, token = mailed(w)
    s, b, _ = w.call("POST", "/api/digest/unsubscribe", {"t": TAMPER[how](token)}, role="anon")
    assert s == 400 and b["message"] == "this unsubscribe link is not valid"
    assert w.stored("viewer", did).enabled


def test_a_token_signed_with_another_key_is_refused(w: World) -> None:
    did, token = mailed(w)
    w.out.key = "z" * 48
    assert w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 400
    assert w.stored("viewer", did).enabled


def test_a_forged_token_for_someone_else_is_refused(w: World) -> None:
    _, token = mailed(w)
    victim = w.made("editor", "email")["id"]
    body = base64.urlsafe_b64encode(f"{w.subject('editor')}\n{victim}\n9999999999".encode()).decode().rstrip("=")
    forged = f"v1.{body}.{token.split('.')[2]}"
    assert w.call("POST", "/api/digest/unsubscribe", {"t": forged}, role="anon")[0] == 400
    assert w.stored("editor", victim).enabled


@pytest.mark.parametrize("value", [5, None, [], {}, True], ids=["int", "null", "list", "object", "bool"])
def test_a_token_that_is_not_text_is_refused(w: World, value: Any) -> None:
    did, _ = mailed(w)
    assert w.call("POST", "/api/digest/unsubscribe", {"t": value}, role="anon")[0] == 400
    assert w.stored("viewer", did).enabled


@pytest.mark.parametrize(
    ("raw", "ctype"),
    [
        (None, None),
        ("nothing=here", "application/x-www-form-urlencoded"),
        ("List-Unsubscribe=One-Click", "application/x-www-form-urlencoded"),
        ("{}", "application/json"),
    ],
    ids=["no_body", "form_without_t", "one_click_without_t", "json_without_t"],
)
def test_unsubscribe_without_a_token_is_refused(w: World, raw: str | None, ctype: str | None) -> None:
    s, b, _ = w.call(
        "POST", "/api/digest/unsubscribe", role="anon", raw_body=raw, headers={"content-type": ctype} if ctype else {}
    )
    assert s == 400 and "is required" in b["message"]


@pytest.mark.parametrize(
    ("age", "status"),
    [
        (timedelta(days=179), 200),
        (timedelta(days=180), 200),
        (timedelta(days=180, seconds=1), 410),
        (timedelta(days=400), 410),
    ],
    ids=["179d", "180d_exactly", "180d_plus_1s", "400d"],
)
def test_unsubscribe_links_expire_after_180_days(w: World, age: timedelta, status: int) -> None:
    did, token = mailed(w)
    w.later(seconds=age.total_seconds())
    s, b, _ = w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")
    assert s == status
    if status == 410:
        assert b["code"] == "link_expired" and w.stored("viewer", did).enabled
    else:
        assert not w.stored("viewer", did).enabled


@pytest.mark.parametrize("link_case", ["valid", "tampered", "expired", "none", "script"])
def test_unsubscribe_get_changes_nothing(w: World, link_case: str) -> None:
    did, token = mailed(w)
    t = {
        "valid": token,
        "tampered": TAMPER["signature_flipped"](token),
        "expired": token,
        "none": "",
        "script": '"><script>alert(1)</script>',
    }[link_case]
    if link_case == "expired":
        w.later(days=200)
    before = w.item("viewer", did)
    for _ in range(3):  # a mail scanner opens it more than once
        s, page, headers = w.call("GET", "/api/digest/unsubscribe", role="anon", query={"t": t} if t else None)
        assert s == 200 and headers["Content-Type"].startswith("text/html")
    assert w.item("viewer", did) == before
    if link_case == "none":
        assert "incomplete" in page
    else:
        assert '<form method="post"' in page and "<script>" not in page
        assert f"t={quote(t, safe='')}" in html.unescape(page) or link_case == "script"


def test_unsubscribe_when_email_is_off_is_not_found(w: World) -> None:
    did, token = mailed(w)
    w.set_mode("dm_email")
    assert w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 404
    assert w.stored("viewer", did).enabled


def test_unsubscribe_twice_and_after_pausing_is_fine(w: World) -> None:
    did, token = mailed(w)
    w.patch("viewer", did, {"enabled": False})
    for _ in range(2):
        assert w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 200
    assert unsubscribed(w, "viewer", did)


def test_the_owner_can_switch_an_unsubscribed_delivery_back_on(w: World) -> None:
    did, token = mailed(w)
    w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")
    s, b = w.patch("viewer", did, {"enabled": True})
    assert s == 200 and b["enabled"] is True and b["next_at"]


def test_an_unreadable_key_is_a_retry_later(w: World) -> None:
    from insights_radar.insights.delivery_transport import TransientDeliveryError

    did, token = mailed(w)

    def unavailable() -> str:
        raise TransientDeliveryError("secret_unavailable")

    w.out.unsubscribe._key = unavailable
    assert w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 503
    assert w.stored("viewer", did).enabled


def test_unsubscribe_is_audited(w: World) -> None:
    did, token = mailed(w)
    w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")
    rows = [i for i in w.ctx.ops_table.scan()["Items"] if i.get("action") == "delivery.unsubscribed"]
    assert len(rows) == 1 and rows[0].get("target") == did


# =====================================================================================================================
# 13. SES feedback
# =====================================================================================================================


def ses_msg(
    kind: str, did: str, subject: str, to: list[str] | None = None, key: str = "eventType", **extra: Any
) -> dict[str, Any]:
    return {
        key: kind,
        "mail": {"destination": to or ["viewer@example.com"], "tags": {"delivery": [did], "subscriber": [subject]}},
        **extra,
    }


@pytest.mark.parametrize("subtype", ["General", "NoEmail", "Suppressed", "OnAccountSuppressionList", "MailboxFull"])
def test_a_permanent_bounce_suppresses(w: World, subtype: str) -> None:
    d = w.made("viewer", "email")
    out = w.feedback(
        ses_msg("Bounce", d["id"], w.subject("viewer"), bounce={"bounceType": "Permanent", "bounceSubType": subtype})
    )
    assert out == {"suppressed": 1, "ignored": 0}
    stored = w.stored("viewer", d["id"])
    assert stored.suppressed and not stored.enabled and stored.last_status is DeliveryStatus.SUPPRESSED
    assert stored.last_error == f"the address bounced ({subtype})"
    assert w.listed("viewer")["items"][0]["last_status"] == "suppressed"


def test_a_bounce_without_a_subtype(w: World) -> None:
    d = w.made("viewer", "email")
    w.feedback(ses_msg("Bounce", d["id"], w.subject("viewer"), bounce={"bounceType": "Permanent"}))
    assert w.stored("viewer", d["id"]).last_error == "the address bounced (permanent)"


@pytest.mark.parametrize("bounce_type", ["Transient", "Undetermined", None])
def test_a_soft_bounce_is_ignored(w: World, bounce_type: str | None) -> None:
    d = w.made("viewer", "email")
    out = w.feedback(ses_msg("Bounce", d["id"], w.subject("viewer"), bounce={"bounceType": bounce_type}))
    assert out == {"suppressed": 0, "ignored": 1} and w.stored("viewer", d["id"]).enabled


@pytest.mark.parametrize(
    ("kind", "key"), [("Complaint", "eventType"), ("Complaint", "notificationType"), ("Bounce", "notificationType")]
)
def test_event_and_notification_formats(w: World, kind: str, key: str) -> None:
    d = w.made("viewer", "email")
    out = w.feedback(ses_msg(kind, d["id"], w.subject("viewer"), key=key, bounce={"bounceType": "Permanent"}))
    assert out["suppressed"] == 1
    expected = "the digest was reported as spam" if kind == "Complaint" else "the address bounced (permanent)"
    assert w.stored("viewer", d["id"]).last_error == expected


@pytest.mark.parametrize(
    ("to", "suppressed"),
    [(["VIEWER@EXAMPLE.COM"], 1), (["a@example.com", "viewer@example.com"], 1), (["other@example.com"], 0), ([], 0)],
    ids=["case", "among_many", "other_address", "none"],
)
def test_feedback_must_name_the_address_the_delivery_sends_to(w: World, to: list[str], suppressed: int) -> None:
    d = w.made("viewer", "email")
    msg = ses_msg("Complaint", d["id"], w.subject("viewer"))
    msg["mail"]["destination"] = to
    assert w.feedback(msg)["suppressed"] == suppressed


@pytest.mark.parametrize(
    "case", ["other_subscriber", "slack_channel", "slack_dm", "deleted", "unknown_id", "bad_id", "bad_subject"]
)
def test_feedback_that_names_no_email_delivery_is_ignored(w: World, case: str) -> None:
    kind = case if case.startswith("slack") else "email"
    d = w.made("viewer", kind)
    did, subject = d["id"], w.subject("viewer")
    if case == "other_subscriber":
        subject = w.subject("editor")
    elif case == "deleted":
        w.call("DELETE", f"/api/me/deliveries/{did}", role="viewer")
    elif case == "unknown_id":
        did = "0123456789ab"
    elif case == "bad_id":
        did = "../../x"
    elif case == "bad_subject":
        subject = "has space!"
    assert w.feedback(ses_msg("Complaint", did, subject)) == {"suppressed": 0, "ignored": 1}
    if case not in ("deleted",):
        assert w.stored("viewer", d["id"]).enabled
    else:
        assert w.item("viewer", d["id"]) is None


@pytest.mark.parametrize("event_type", ["Delivery", "Send", "Open", "Click", "Reject", "DeliveryDelay", ""])
def test_other_ses_events_are_ignored(w: World, event_type: str) -> None:
    d = w.made("viewer", "email")
    assert w.feedback(ses_msg(event_type, d["id"], w.subject("viewer")))["suppressed"] == 0
    assert w.stored("viewer", d["id"]).enabled


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1]",
        "5",
        "null",
        json.dumps({"eventType": "Complaint"}),
        json.dumps({"eventType": "Complaint", "mail": {"tags": {"delivery": "0123456789ab"}}}),
    ],
    ids=["text", "list", "number", "null", "no_mail", "tag_not_list"],
)
def test_malformed_feedback_is_ignored(w: World, raw: str) -> None:
    w.made("viewer", "email")
    assert w.feedback(raw) == {"suppressed": 0, "ignored": 1}


def test_feedback_handles_several_records(w: World) -> None:
    a = w.made("viewer", "email")["id"]
    b = w.made("viewer", "email")["id"]
    c = w.made("viewer", "email")["id"]
    sub = w.subject("viewer")
    out = w.feedback(
        ses_msg("Complaint", a, sub),
        ses_msg("Delivery", b, sub),
        ses_msg("Bounce", c, sub, bounce={"bounceType": "Permanent"}),
    )
    assert out == {"suppressed": 2, "ignored": 1}
    assert not w.stored("viewer", a).enabled and w.stored("viewer", b).enabled and w.stored("viewer", c).suppressed


def test_feedback_when_email_is_off_is_ignored(w: World) -> None:
    d = w.made("viewer", "email")
    w.set_mode("dm_email")
    assert w.feedback(ses_msg("Complaint", d["id"], w.subject("viewer"))) == {"suppressed": 0, "ignored": 1}


SUPPRESSED_AFTER = ["patch_on", "patch_on_with_hour", "test_send", "send", "tick", "unsubscribe"]


@pytest.mark.parametrize("then", SUPPRESSED_AFTER)
def test_a_suppressed_delivery_stays_off(w: World, then: str) -> None:
    did, token = mailed(w)
    sent = len(w.mails())
    w.feedback(ses_msg("Bounce", did, w.subject("viewer"), bounce={"bounceType": "Permanent"}))
    if then == "patch_on":
        s, b = w.patch("viewer", did, {"enabled": True})
        assert s == 409 and b["details"]["code"] == "suppressed"
    elif then == "patch_on_with_hour":
        assert w.patch("viewer", did, {"enabled": True, "hour": 3})[0] == 409
        assert w.patch("viewer", did, {"hour": 3})[0] == 200  # still editable while off
    elif then == "test_send":
        assert w.test_send("viewer", did)[0] == 409
    elif then == "send":
        w.later(minutes=1)
        w.signal("After the bounce")
        w.send("viewer", did, "m2")
    elif then == "tick":
        w.later(days=3)
        assert w.tick()["queued"] == 0
    else:
        assert w.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 200
    stored = w.stored("viewer", did)
    assert stored.suppressed and not stored.enabled and len(w.mails()) == sent


def test_suppression_leaves_other_deliveries_alone(w: World) -> None:
    a = w.made("viewer", "email")["id"]
    b = w.made("viewer", "email")["id"]
    w.feedback(ses_msg("Complaint", a, w.subject("viewer")))
    assert w.stored("viewer", b).enabled and not w.stored("viewer", b).suppressed


# =====================================================================================================================
# 14. Rendering safety
# =====================================================================================================================


SLACK_TITLES = [
    ("channel_mention", "<!channel> urgent", "<!channel"),
    ("here_mention", "Look <!here>", "<!here"),
    ("user_mention", "Ping <@U0123456789>", "<@U0"),
    ("evil_link", "<https://evil.example|click me>", "<https://evil"),
    ("ampersand", "Tariffs & duties", None),
    ("angles", "1 < 2 > 0", None),
    ("pre_escaped", "&lt;b&gt; literal", None),
    ("fullwidth_angle", "\uff1c!channel\uff1e fullwidth", "<!channel"),
    ("script", "<script>alert(1)</script>", "<script"),
    ("mrkdwn_chars", "*bold* _it_ ~strike~ `code`", None),
]


@pytest.mark.parametrize(("title", "raw"), [c[1:] for c in SLACK_TITLES], ids=[c[0] for c in SLACK_TITLES])
@pytest.mark.parametrize("kind", ["slack_channel", "slack_dm"])
def test_slack_escapes_untrusted_titles(w: World, kind: str, title: str, raw: str | None) -> None:
    did = ready(w, kind, title=title)
    w.send("viewer", did)
    post = w.posts()[-1]
    texts = " ".join(mrkdwn(post))
    if raw:
        assert raw not in texts and raw not in post["body"]["text"]
    import unicodedata

    from insights_radar.insights.delivery_render import _neutral

    # Shown as written, but with Slack's styling marks mapped to look-alikes (scan cycles 5 and 6).
    assert slack_escape(_neutral(unicodedata.normalize("NFKC", title))) in texts
    # Every "<" left in the message opens one of the app's own links.
    for m in re.finditer(r"<([^>]*)>", texts):
        assert m.group(1).startswith(APP + "/"), m.group(0)


HTML_TITLES = [
    ("script", "<script>alert(1)</script>"),
    ("attribute_break", '"><img src=x onerror=alert(1)>'),
    ("single_quote", "' onmouseover='x"),
    ("ampersand", "Tariffs & duties"),
    ("bold", "<b>bold</b>"),
    ("comment", "<!-- hidden -->"),
]


@pytest.mark.parametrize("title", [t for _, t in HTML_TITLES], ids=[i for i, _ in HTML_TITLES])
def test_email_html_escapes_untrusted_titles(w: World, title: str) -> None:
    did = ready(w, "email", title=title)
    w.send("viewer", did)
    page = w.mail_html()
    assert title not in page and html.escape(title, quote=True) in page
    assert title in w.mail_text()  # the plain-text part is plain text


@pytest.mark.parametrize("summary", ["<script>x</script>", '"onload="x', "a & b"])
def test_email_html_escapes_untrusted_summaries(w: World, summary: str) -> None:
    d = w.made("viewer", "email")
    w.signal("Plain title", summary=summary)
    w.later(minutes=1)
    w.send("viewer", d["id"])
    assert summary not in w.mail_html() and html.escape(summary, quote=True) in w.mail_html()


@pytest.mark.parametrize("kind", KINDS)
def test_invisible_characters_are_removed(w: World, kind: str) -> None:
    did = ready(w, kind, title="Tar\u200biff \u202eturn\u2066ed\u2069 x")
    w.send("viewer", did)
    text = w.delivered_text(kind)
    assert "Tariff turned x" in text and "\u202e" not in text and "\u200b" not in text


@pytest.mark.parametrize("kind", KINDS)
def test_long_titles_are_cut_at_150(w: World, kind: str) -> None:
    title = "T" * 120 + " " + "x" * 79
    did = ready(w, kind, title=title)
    w.send("viewer", did)
    text = w.delivered_text(kind)
    assert title[:149] + "\u2026" in text and title not in text


def test_a_long_list_stays_inside_slacks_section_limit(w: World) -> None:
    d = w.made("viewer", "slack_channel")
    for i in range(20):
        w.signal(f"{i:02d} " + "L" * 190)
        w.later(seconds=1)
    w.send("viewer", d["id"])
    post = w.posts()[-1]
    sections = [b["text"]["text"] for b in post["body"]["blocks"] if b["type"] == "section"]
    assert all(len(t) <= 3000 for t in sections)
    # The list goes on in the next section rather than being cut: all 20 rows are posted.
    assert len(sections) > 1 and sum(t.count("\u2022 ") for t in sections) == 20
    assert not any(re.search(r"\u2026 and \d+ more in the app", t) for t in sections)


def test_many_watched_records_stay_inside_fifty_blocks(w: World) -> None:
    d = w.made("viewer", "slack_dm")
    for i in range(50):
        w.watch("viewer", f"Org {i:02d}")
    w.later(minutes=1)
    w.send("viewer", d["id"])
    blocks = w.posts()[-1]["body"]["blocks"]
    assert len(blocks) <= 50 and blocks[0]["type"] == "header" and blocks[-1]["type"] == "context"
    # The watched records are one group since scan cycle 4, with a section per record since scan cycle 14:
    # every record is shown, or what does not fit is its "... and more in the app".
    # Counted on the watched headings only ("Org NN*: N new"), so list rows and list tails cannot stand in.
    texts = " ".join(b.get("text", {}).get("text", "") for b in blocks)
    shown = sum(1 for i in range(50) if re.search(rf"Org {i:02d}(?:>)?\*: \d+ new", texts))
    assert shown == 50 or f"{50 - shown} more watched record" in texts
    assert all(len(b["text"]["text"]) <= 3000 for b in blocks if b["type"] == "section")


def test_many_watched_records_in_an_email_are_cut_at_25(w: World) -> None:
    d = w.made("viewer", "email")
    for i in range(30):
        w.watch("viewer", f"Org {i:02d}")
    w.later(minutes=1)
    w.send("viewer", d["id"])
    assert "and 5 more watched records in the app" in w.mail_html()
    assert "Org 24" in w.mail_html() and "Org 25" not in w.mail_html()


@pytest.mark.parametrize("kind", KINDS)
def test_watched_records_are_listed_with_their_count(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    w.watch("viewer", "Acme", facts=3)
    w.signal("Shared news")
    w.later(minutes=1)
    w.send("viewer", d["id"])
    text = w.delivered_text(kind)
    assert "Shared news" in text
    if kind == "slack_channel":  # one person's watch list is not posted to the whole channel (scan cycle 1, M3)
        assert "Watched records" not in text and "Acme" not in text and "(1)" in text
    else:
        assert "Watched records" in text and "Acme fact 2" in text and "3 new" in text


@pytest.mark.parametrize("kind", KINDS)
def test_links_point_only_at_the_app(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    sig = w.signal("Linked signal")
    w.watch("viewer", "Acme")
    w.later(minutes=1)
    w.send("viewer", d["id"])
    text = w.delivered_text(kind)
    urls = re.findall(r"https?://[^\s\"'<>|\\]+", text)
    assert urls and all(u.startswith(APP + "/") for u in urls), urls
    kind_part, _, uid = sig.id.partition("/")
    assert f"{APP}/insights/nodes/{kind_part}/{uid}" in text
    assert "evidence.example" not in text


def test_email_carries_unsubscribe_and_manage_links(w: World) -> None:
    did, token = mailed(w)
    mail = w.mails()[-1]
    headers = {h["Name"]: h["Value"] for h in mail["Content"]["Simple"]["Headers"]}
    assert headers["List-Unsubscribe"] == f"<{APP}/api/digest/unsubscribe?t={token}>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert f'href="{APP}/unsubscribe?t={token}"' in w.mail_html() and f'href="{APP}/whats-new"' in w.mail_html()
    assert f"Unsubscribe: {APP}/unsubscribe?t={token}" in w.mail_text()
    assert mail["EmailTags"] == [
        {"Name": "delivery", "Value": did},
        {"Name": "subscriber", "Value": w.subject("viewer")},
    ]


@pytest.mark.parametrize(("count", "word"), [(1, "record"), (2, "records"), (5, "records")])
def test_email_subject_counts(w: World, count: int, word: str) -> None:
    d = w.made("viewer", "email")
    for i in range(count):
        w.signal(f"S{i}")
    w.later(minutes=1)
    w.send("viewer", d["id"])
    subject = w.mails()[-1]["Content"]["Simple"]["Subject"]["Data"]
    assert subject.endswith(f": {count} new {word}") and "\n" not in subject


def test_slack_message_shape(w: World) -> None:
    did = ready(w, "slack_channel")
    w.send("viewer", did)
    body = w.posts()[-1]["body"]
    assert body["unfurl_links"] is False and body["unfurl_media"] is False
    assert body["text"].endswith("1 new record since 25 Sep 2026 08:01 UTC") or "1 new record" in body["text"]
    assert body["blocks"][0]["type"] == "header" and len(body["blocks"][0]["text"]["text"]) <= 150
    assert "Found and summarised from public sources by AI agents" in json.dumps(body["blocks"][-1])


@pytest.mark.parametrize("kind", ["slack_channel", "email"])
def test_a_cut_list_says_there_is_more(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    for i in range(22):
        w.signal(f"Many {i}")
        w.later(seconds=1)
    w.send("viewer", d["id"])
    text = w.delivered_text(kind)
    assert "(20+)" in text and "and more in the app" in text


APP_URLS = [
    ("trailing_slash", "https://app.example.com/", True, "https://app.example.com/insights/nodes/"),
    ("port", "https://app.example.com:8443", True, "https://app.example.com:8443/insights/nodes/"),
    ("http", "http://app.example.com", False, None),
    ("path", "https://app.example.com/base", False, None),
    ("javascript", "javascript:alert(1)", False, None),
    ("empty", "", False, None),
]


@pytest.mark.parametrize(("url", "valid", "link"), [c[1:] for c in APP_URLS], ids=[c[0] for c in APP_URLS])
def test_app_url_decides_the_links(w: World, url: str, valid: bool, link: str | None) -> None:
    w.set_config(config(DIGEST_SLACK_MODE="bot", DIGEST_DM_RESOLVER="email", DIGEST_EMAIL_MODE="ses", APP_URL=url))
    assert w.listed("viewer")["options"]["email"] is valid  # no mail goes out without an unsubscribe link
    did = ready(w, "slack_channel")
    w.send("viewer", did)
    text = w.post_text()
    if link:
        assert link in text and "//insights" not in text
    else:
        assert "<http" not in text and "Linked" not in text and "Fresh signal" in text


# =====================================================================================================================
# 15. The admin channel list
# =====================================================================================================================


@pytest.mark.parametrize("cid", [CHANNEL, "G0123456789", "C" + DIGITS[:8], "CABCDEFGHIJ", "C" + "0" * 20])
def test_admin_lists_a_valid_channel(w: World, cid: str) -> None:
    s, b = w.set_channels((cid, "label"))
    assert s == 200 and b["channels"] == [{"id": cid, "label": "label"}] and b["clashes"] == []
    assert w.call("GET", "/api/admin/digest/channels")[1] == b  # the same list, version and clashes (c31)
    assert w.listed("viewer")["options"]["channels"] == [{"id": cid, "label": "label"}]


@pytest.mark.parametrize(
    "cid",
    [
        CHANNEL.lower(),
        SLACK_DM,
        "U0123456789",
        "C0123",
        "#general",
        "C01234567-",
        "C 0123456789",
        "",
        "C0123456789\n",
        123,
        None,
    ],
)
def test_admin_refuses_a_bad_channel_id(w: World, cid: Any) -> None:
    s, b, _ = w.call("PUT", "/api/admin/digest/channels", {"channels": [{"id": cid, "label": "x"}]})
    assert s == 400 and "Slack channel ID" in b["message"]
    assert w.ctx.deliveries.channels() == [DigestChannel(CHANNEL, CHANNEL_LABEL)]  # unchanged


@pytest.mark.parametrize(
    ("label", "stored"),
    [("x", "x"), ("a" * 80, "a" * 80), (" padded ", "padded"), ("\u00e9moji \U0001f680", "\u00e9moji \U0001f680")],
    ids=["one", "eighty", "padded", "unicode"],
)
def test_admin_label_kept(w: World, label: str, stored: str) -> None:
    s, b = w.set_channels((CHANNEL, label))
    assert s == 200 and b["channels"][0]["label"] == stored


@pytest.mark.parametrize(
    "label",
    ["", "   ", "a" * 81, "a\nb", "a\tb", "a\x7fb", "a\x85b", 5, None],
    ids=["empty", "blank", "81", "newline", "tab", "del", "c1", "int", "null"],
)
def test_admin_refuses_a_bad_label(w: World, label: Any) -> None:
    s, b, _ = w.call("PUT", "/api/admin/digest/channels", {"channels": [{"id": CHANNEL, "label": label}]})
    assert s == 400 and "label" in b["message"]


@pytest.mark.parametrize(("n", "status"), [(0, 200), (1, 200), (19, 200), (20, 200), (21, 400), (40, 400)])
def test_admin_list_holds_at_most_twenty(w: World, n: int, status: int) -> None:
    s, b, _ = w.call(
        "PUT", "/api/admin/digest/channels", {"channels": [{"id": f"C{i:010d}", "label": f"c{i}"} for i in range(n)]}
    )
    assert s == status
    if status == 200:
        assert len(w.ctx.deliveries.channels()) == n
    else:
        assert "at most 20" in b["message"]


BAD_LISTS = [
    ("duplicate", {"channels": [{"id": CHANNEL, "label": "a"}, {"id": CHANNEL, "label": "b"}]}, "listed twice"),
    ("extra_field", {"channels": [{"id": CHANNEL, "label": "a", "x": 1}]}, "an object with id and label"),
    ("string_entry", {"channels": [CHANNEL]}, "an object with id and label"),
    ("int_entry", {"channels": [5]}, "an object with id and label"),
    ("null_entry", {"channels": [None]}, "an object with id and label"),
    ("list_entry", {"channels": [[CHANNEL, "a"]]}, "an object with id and label"),
    ("missing_label", {"channels": [{"id": CHANNEL}]}, "label"),
    ("not_a_list", {"channels": CHANNEL}, "must be a list"),
    ("object", {"channels": {"id": CHANNEL}}, "must be a list"),
    ("missing", {}, "channels is required"),
    ("unknown_field", {"channels": [], "mode": "x"}, "unknown fields"),
]


@pytest.mark.parametrize(("body", "message"), [c[1:] for c in BAD_LISTS], ids=[c[0] for c in BAD_LISTS])
def test_admin_refuses_a_bad_list(w: World, body: dict[str, Any], message: str) -> None:
    s, b, _ = w.call("PUT", "/api/admin/digest/channels", body)
    assert s == 400 and message in b["message"]
    assert w.ctx.deliveries.channels() == [DigestChannel(CHANNEL, CHANNEL_LABEL)]


@pytest.mark.parametrize(
    ("role", "status"), [("viewer", 403), ("editor", 403), ("disabled", 401), ("anon", 401), ("admin", 200)]
)
@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_admin_channel_list_is_for_admins(w: World, method: str, role: str, status: int) -> None:
    body = {"channels": [{"id": "C9999999999", "label": "hijack"}]} if method == "PUT" else None
    s, _, _ = w.call(method, "/api/admin/digest/channels", body, role=role)
    assert s == status
    expected = (
        [DigestChannel("C9999999999", "hijack")]
        if (method == "PUT" and status == 200)
        else [DigestChannel(CHANNEL, CHANNEL_LABEL)]
    )
    assert w.ctx.deliveries.channels() == expected


def test_admin_put_replaces_the_list_and_new_channels_can_be_picked(w: World) -> None:
    w.set_channels(("C1111111111", "one"), ("C2222222222", "two"))
    assert [c["id"] for c in w.listed("viewer")["options"]["channels"]] == ["C1111111111", "C2222222222"]
    assert w.create("viewer", "slack_channel", channel_id=CHANNEL)[0] == 400
    s, b = w.create("viewer", "slack_channel", channel_id="C2222222222")
    assert s == 201 and b["target_label"] == "#two"


def test_an_empty_channel_list_offers_no_channel(w: World) -> None:
    w.set_channels()
    opts = w.listed("viewer")["options"]
    assert opts["slack_channel"] is False and opts["channels"] == [] and opts["slack_dm"] is True


def test_admin_channel_change_is_audited(w: World) -> None:
    w.set_channels(("C1111111111", "one"))
    rows = [i for i in w.ctx.ops_table.scan()["Items"] if i.get("action") == "digest.channels"]
    assert len(rows) == 1


# =====================================================================================================================
# 16. Deploy config parsing (what the infrastructure hands every function)
# =====================================================================================================================


CONFIGS = [
    ("bot_upper", {"DIGEST_SLACK_MODE": "BOT"}, (True, False, False)),
    ("bot_padded", {"DIGEST_SLACK_MODE": " Bot "}, (True, False, False)),
    ("bot_typo", {"DIGEST_SLACK_MODE": "bots"}, (False, False, False)),
    ("slack_on", {"DIGEST_SLACK_MODE": "on"}, (False, False, False)),
    ("webhook_upper", {"DIGEST_SLACK_MODE": "WEBHOOK"}, (True, False, False)),
    ("bot_no_secret", {"DIGEST_SLACK_MODE": "bot", "DIGEST_SLACK_SECRET_ARN": ""}, (False, False, False)),
    ("dm_resolver_typo", {"DIGEST_SLACK_MODE": "bot", "DIGEST_DM_RESOLVER": "mail"}, (True, False, False)),
    (
        "dm_directory_role_only",
        {"DIGEST_SLACK_MODE": "bot", "DIGEST_DM_RESOLVER": "directory", "DIGEST_DIRECTORY_ROLE_ARN": DIRECTORY_ROLE},
        (True, False, False),
    ),
    ("ses_upper", {"DIGEST_EMAIL_MODE": "SES"}, (False, False, True)),
    ("ses_padded", {"DIGEST_EMAIL_MODE": "ses "}, (False, False, True)),
    ("smtp", {"DIGEST_EMAIL_MODE": "smtp"}, (False, False, False)),
    ("ses_no_sender", {"DIGEST_EMAIL_MODE": "ses", "DIGEST_EMAIL_SENDER": ""}, (False, False, False)),
    ("ses_no_unsub_key", {"DIGEST_EMAIL_MODE": "ses", "DIGEST_UNSUB_SECRET_ARN": ""}, (False, False, False)),
    ("ses_no_app_url", {"DIGEST_EMAIL_MODE": "ses", "APP_URL": ""}, (False, False, False)),
]


@pytest.mark.parametrize(("env", "expect"), [c[1:] for c in CONFIGS], ids=[c[0] for c in CONFIGS])
def test_deploy_config_turns_on_only_what_is_complete(
    w: World, env: dict[str, str], expect: tuple[bool, bool, bool]
) -> None:
    w.set_config(config(**env))
    opts = w.listed("viewer")["options"]
    assert (opts["slack_channel"], opts["slack_dm"], opts["email"]) == expect
    for kind, on in zip(KINDS, expect):
        assert (w.create("viewer", kind)[0] == 201) is on


def test_an_unverified_email_is_no_address(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.integration import harness

    claims = dict(harness.ROLES["viewer"] or {})
    claims["email_verified"] = "false"
    monkeypatch.setitem(harness.ROLES, "viewer", claims)
    opts = w.listed("viewer")["options"]
    assert opts["email"] is False and opts["email_address"] is None and opts["slack_dm"] is False
    assert w.create("viewer", "email")[0] == 400 and w.create("viewer", "slack_dm")[0] == 400


# =====================================================================================================================
# 17. Scan cycle 1 fixes (reviews/c1): each test failed before its fix
# =====================================================================================================================


def hide_list(w: World, key: str) -> None:
    profile = w.ctx.profile.get()
    w.ctx.profile.put({"lists": {key: {"visible": False}}}, "user:test", profile.version, frozenset())


@pytest.mark.parametrize("kind", KINDS)
def test_m1_a_busy_hidden_list_does_not_hold_the_cursor_back(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    hide_list(w, "signals")
    for i in range(25):  # more hidden signals than one list holds
        w.signal(f"Hidden {i:02d}")
        w.later(seconds=1)
    w.node(NodeType.TREND, "Visible trend")
    w.later(minutes=1)
    w.send("viewer", d["id"])
    text = w.delivered_text(kind)
    assert "Visible trend" in text and "Hidden" not in text
    stored = w.stored("viewer", d["id"])
    assert stored.cursor == iso(w.now())  # not the 20th hidden signal's key
    w.later(minutes=1)
    w.send("viewer", d["id"], "m2")
    assert w.outgoing() == 1 and w.stored("viewer", d["id"]).last_status is DeliveryStatus.EMPTY


def test_m1_a_hidden_list_alone_sends_nothing(w: World) -> None:
    d = w.made("viewer", "email")
    hide_list(w, "signals")
    for i in range(25):
        w.signal(f"Hidden {i:02d}")
    w.later(minutes=1)
    w.send("viewer", d["id"])
    assert w.outgoing() == 0 and w.stored("viewer", d["id"]).cursor == iso(w.now())


def _bad_zone(w: World, did: str) -> None:
    key = {"pk": f"USER#{w.subject('viewer')}", "sk": f"DELIVERY#{did}"}
    w.ctx.ops_table.update_item(
        Key=key,
        UpdateExpression="SET #z = :z",
        ExpressionAttributeNames={"#z": "timezone"},
        ExpressionAttributeValues={":z": "Mars/Olympus_Mons"},
    )


def _bad_channel(w: World, did: str) -> None:
    key = {"pk": f"USER#{w.subject('viewer')}", "sk": f"DELIVERY#{did}"}
    w.ctx.ops_table.update_item(
        Key=key,
        UpdateExpression="SET #c = :c",
        ExpressionAttributeNames={"#c": "channel"},
        ExpressionAttributeValues={":c": "carrier_pigeon"},
    )


@pytest.mark.parametrize("how", ["bad_zone", "bad_channel", "throttled"])
def test_m2_one_bad_due_row_does_not_stop_the_tick(w: World, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    bad = w.made("viewer", "email")
    good = w.made("editor", "email")
    if how == "bad_zone":
        _bad_zone(w, bad["id"])
    elif how == "bad_channel":
        _bad_channel(w, bad["id"])
    else:
        real = w.ctx.deliveries.claim

        def throttled(due: DueDelivery, now: datetime) -> str | None:
            if due.id == bad["id"]:
                raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")
            return real(due, now)

        monkeypatch.setattr(w.ctx.deliveries, "claim", throttled)
    w.at(TOMORROW_9_LONDON)
    assert w.tick() == {"queued": 1, "skipped": 0, "failed": 1}
    assert [b["id"] for b in w.ctx.sqs.bodies()] == [good["id"]]
    w.later(days=1)
    assert w.tick() == {"queued": 1, "skipped": 0, "failed": 1}  # the bad row is still left, the good one goes


def test_m2_a_failed_unclaim_is_counted_and_the_tick_goes_on(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    w.made("viewer", "email")
    w.made("editor", "email")
    w.at(TOMORROW_9_LONDON)
    w.ctx.sqs.fail = 1

    def boom(*a: Any) -> None:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")

    monkeypatch.setattr(w.ctx.deliveries, "unclaim", boom)
    assert w.tick() == {"queued": 1, "skipped": 0, "failed": 1}


@pytest.mark.parametrize("kind", KINDS)
def test_m3_a_channel_post_never_carries_the_watch_list(w: World, kind: str) -> None:
    d = w.made("viewer", kind)
    w.watch("viewer", "Secret Target Corp", facts=2)
    w.later(minutes=1)
    w.send("viewer", d["id"])
    if kind == "slack_channel":  # only the watch list had news: nothing is posted, and the cursor moves on
        assert w.outgoing() == 0 and w.stored("viewer", d["id"]).last_status is DeliveryStatus.EMPTY
    else:
        assert "Secret Target Corp" in w.delivered_text(kind)


def test_m3_a_channel_test_send_never_carries_the_watch_list(w: World) -> None:
    d = w.made("viewer", "slack_channel")
    w.watch("viewer", "Secret Target Corp")
    w.signal("Shared news")
    w.later(minutes=1)
    s, b, _ = w.test_send("viewer", d["id"])
    assert s == 200 and b["sent"] is True and b["total"] == 1
    assert "Shared news" in w.post_text() and "Secret Target Corp" not in w.post_text()


@pytest.mark.parametrize("kind", KINDS)
def test_l2_a_second_message_for_the_same_time_sends_nothing(w: World, kind: str) -> None:
    did = ready(w, kind, title="First")
    w.send("viewer", did, "m1", due=TOMORROW_9_LONDON)
    assert w.outgoing() == 1
    w.later(minutes=1)
    w.signal("Arrived since")
    w.later(minutes=15)
    assert w.send("viewer", did, "m2", due=TOMORROW_9_LONDON) == {"batchItemFailures": []}
    assert w.outgoing() == 1  # the scheduler's second message for that time is dropped
    w.send("viewer", did, "m3", due="2026-10-04T08:00:00.000Z")  # the next scheduled time sends as usual
    assert w.outgoing() == 2 and "Arrived since" in w.delivered_text(kind)


def test_l2_a_duplicate_waits_for_nothing_while_the_first_retries(w: World) -> None:
    did = ready(w, "slack_channel")
    w.http.replies = [HttpResponse(500, {}, b"")]
    assert w.send("viewer", did, "m1", due=TOMORROW_9_LONDON)["batchItemFailures"]
    assert w.send("viewer", did, "m2", due=TOMORROW_9_LONDON) == {"batchItemFailures": []}
    assert len(w.posts()) == 1  # the 500 was the only attempt
    w.send("viewer", did, "m1", due=TOMORROW_9_LONDON)  # the first message's own retry keeps its claim
    assert len(w.posts()) == 2 and w.stored("viewer", did).last_status is DeliveryStatus.OK


def test_l2_an_ambiguous_queue_failure_sends_one_digest(w: World) -> None:
    d = w.made("viewer", "email")
    w.signal("Once only")
    w.at(TOMORROW_9_LONDON)
    w.ctx.sqs.ambiguous = 1  # SQS stored the message, but the client saw a timeout
    assert w.tick() == {"queued": 0, "skipped": 0, "failed": 1}
    assert w.stored("viewer", d["id"]).next_at == TOMORROW_9_LONDON  # put back
    w.later(minutes=15)
    assert w.tick()["queued"] == 1
    bodies = [m["MessageBody"] for m in w.ctx.sqs.messages]
    assert len(bodies) == 2 and json.loads(bodies[0])["due"] == json.loads(bodies[1])["due"]
    assert w.receive(bodies[0], "sqs-0") == {"batchItemFailures": []}
    w.later(minutes=1)
    w.signal("Arrived between the two")  # it waits for the next scheduled digest
    w.later(minutes=1)
    assert w.receive(bodies[1], "sqs-1") == {"batchItemFailures": []}
    assert len(w.mails()) == 1 and "Arrived between" not in w.mail_text()


def _account_is(w: World, subject: str) -> None:
    """The user pool names ``subject`` as the account's ``sub``: a delivery stops when they differ (scan cycle 2)."""
    real = w.ctx.users.get
    w.ctx.users.get = lambda name: replace(real(name), subject=subject)  # type: ignore[method-assign]


def test_l4_a_subject_outside_the_tag_alphabet_is_still_suppressed(w: World) -> None:
    viewer = w.people["viewer"]
    subject = "local:a_b.c@d+e"
    _account_is(w, subject)
    d = w.ctx.deliveries.create(
        subject,
        viewer.username,
        Target(DeliveryKind.EMAIL, viewer.email, address=viewer.email),
        **DEFAULT_SCHEDULE,
        weekday=None,
    )
    w.signal("For an odd subject")
    w.later(minutes=1)
    w.send(subject, d.id)
    (mail,) = w.mails()
    tags = {t["Name"]: t["Value"] for t in mail["EmailTags"]}
    assert re.fullmatch(r"[A-Za-z0-9_-]+", tags["subscriber"]) and tags["subscriber"] != subject
    msg = ses_msg("Complaint", d.id, tags["subscriber"])
    assert w.feedback(msg) == {"suppressed": 1, "ignored": 0}
    assert w.ctx.deliveries.get(subject, d.id).suppressed


def test_l5_two_racing_creates_keep_one(w: World) -> None:
    """Two creates at the fifth place, interleaved: both count four, both write, both recount six. Before the fix
    both gave their place back; now the one ranked first keeps it."""
    for _ in range(4):
        w.made("viewer", "email")
        w.later(seconds=1)
    service, subject, viewer = w.ctx.deliveries, w.subject("viewer"), w.people["viewer"]
    real = service.for_user
    counted, recounted = threading.Barrier(2, timeout=20), threading.Barrier(2, timeout=20)

    def in_step(subj: str, *, consistent: bool = False) -> list[Any]:
        (recounted if consistent else counted).wait()
        return real(subj, consistent=consistent)

    outcomes: list[str] = []

    def one() -> None:
        try:
            service.create(
                subject,
                viewer.username,
                Target(DeliveryKind.EMAIL, viewer.email, address=viewer.email),
                **DEFAULT_SCHEDULE,
                weekday=None,
            )
            outcomes.append("created")
        except ValidationError:
            outcomes.append("refused")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(service, "for_user", in_step)
        threads = [threading.Thread(target=one) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    assert sorted(outcomes) == ["created", "refused"]
    assert len(service.for_user(subject)) == 5


def test_l6_a_rate_limit_put_back_too_often_counts_as_a_failure(w: World) -> None:
    did = ready(w, "slack_channel")
    body = json.dumps({"subject": w.subject("viewer"), "id": did, "due": "d1", "claim": "m0", "requeues": 8})
    w.http.replies = [HttpResponse(429, {"retry-after": "30"}, b"")]
    assert w.receive(body, "m9") == {"batchItemFailures": [{"itemIdentifier": "m9"}]}
    assert w.ctx.sqs.messages == []
    assert w.ctx.sqs.visibility == [{"QueueUrl": QUEUE, "ReceiptHandle": "rh", "VisibilityTimeout": 30}]


def test_l6_a_refused_requeue_falls_back_to_the_hold_back(w: World) -> None:
    did = ready(w, "slack_dm")
    w.http.replies = [*prefix("slack_dm"), HttpResponse(429, {"retry-after": "45"}, b"")]
    w.ctx.sqs.fail = 1
    assert w.send("viewer", did) == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert w.ctx.sqs.visibility == [{"QueueUrl": QUEUE, "ReceiptHandle": "rh", "VisibilityTimeout": 45}]


def test_l6_a_rate_limited_send_can_be_put_back_repeatedly_then_goes_through(w: World) -> None:
    did = ready(w, "slack_channel")
    w.http.replies = [HttpResponse(429, {"retry-after": "5"}, b"")]
    w.send("viewer", did)
    for n in range(2, 5):
        w.http.replies = [HttpResponse(429, {"retry-after": "5"}, b"")]
        assert w.redeliver(message_id=f"again-{n}") == {"batchItemFailures": []}
        assert json.loads(w.ctx.sqs.messages[-1]["MessageBody"])["requeues"] == n
    assert w.redeliver(message_id="last") == {"batchItemFailures": []}
    assert w.stored("viewer", did).last_status is DeliveryStatus.OK and len(w.posts()) == 5


def test_l2_a_failed_claim_write_is_retried(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    did = ready(w, "email")
    real = w.ctx.ops_table.update_item

    def throttled(**kw: Any) -> Any:
        if "send_slot" in str(kw.get("UpdateExpression", "")):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")
        return real(**kw)

    monkeypatch.setattr(w.ctx.ops_table, "update_item", throttled)
    assert w.send("viewer", did) == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert w.mails() == []


def test_l4_a_subject_too_long_for_a_tag_is_mailed_without_one(w: World) -> None:
    viewer = w.people["viewer"]
    subject = "a." * 100  # 200 characters: 300 once escaped, past SES's 256
    _account_is(w, subject)
    d = w.ctx.deliveries.create(
        subject,
        viewer.username,
        Target(DeliveryKind.EMAIL, viewer.email, address=viewer.email),
        **DEFAULT_SCHEDULE,
        weekday=None,
    )
    w.signal("Untagged")
    w.later(minutes=1)
    w.send(subject, d.id)
    (mail,) = w.mails()
    assert [t["Name"] for t in mail["EmailTags"]] == ["delivery"]


# =====================================================================================================================
# Scan cycle 11: counts kept apart, the counted message, races, unreadable rows
# =====================================================================================================================


def test_c11_owner_faults_and_lookup_misses_are_counted_apart(w: World) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    for _ in range(2):
        assert not w.ctx.deliveries.fail(subject, d["id"], "no Slack user has your email address", lookup=True)
    assert not w.ctx.deliveries.fail(subject, d["id"], "your account is disabled")  # 1 of 3, not 3 of 3
    stored = w.stored("viewer", d["id"])
    assert stored.enabled is True and (stored.fails, stored.misses) == (1, 2)


def test_c11_an_empty_day_shows_the_counted_fault_not_the_last_error(w: World) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "Slack: user_disabled")
    w.ctx.deliveries.record(subject, d["id"], DeliveryStatus.ERROR, "Slack answered 503 (will retry)")
    w.later(hours=1)
    w.send("viewer", d["id"])  # nothing new
    stored = w.stored("viewer", d["id"])
    assert stored.last_status is DeliveryStatus.ERROR and stored.last_error == "Slack: user_disabled"


def test_c11_an_owner_edit_during_an_empty_send_wins(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "Slack: user_disabled")
    w.later(hours=1)
    real = w.ctx.deliveries.advance

    def edit_first(delivery: Any, cursor: str, status: DeliveryStatus, **k: Any) -> bool:
        monkeypatch.setattr(w.ctx.deliveries, "advance", real)  # the edit happens once, before the first write
        w.ctx.deliveries.update(subject, d["id"], {"hour": (d["hour"] + 1) % 24})  # clears the count
        return real(delivery, cursor, status, **k)

    monkeypatch.setattr(w.ctx.deliveries, "advance", edit_first)
    w.send("viewer", d["id"])
    stored = w.stored("viewer", d["id"])
    assert (stored.fails, stored.misses) == (0, 0) and stored.cursor == iso(w.now())
    assert stored.last_status is DeliveryStatus.EMPTY and stored.last_error is None


def test_c11_a_real_send_that_lost_the_cursor_race_clears_the_counts(w: World) -> None:
    did = ready(w, "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, did, "no Slack user has your email address", lookup=True)
    stale = w.stored("viewer", did)
    w.ctx.deliveries.advance(stale, iso(w.now()), DeliveryStatus.EMPTY)  # another sender moved the cursor first
    assert w.ctx.deliveries.advance(stale, iso(w.now()), DeliveryStatus.OK) is False
    stored = w.stored("viewer", did)
    assert (stored.fails, stored.misses, stored.fault, stored.miss_fault) == (0, 0, None, None)


def test_c12_an_unreadable_row_alarms_once_a_day_and_shows_no_internal_text(
    w: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Scan cycle 12: a value or code the row cannot be read with may be a code fault on every row, so it is
    alarmed; it waits a day, so one bad row alarms once a day; the owner is not shown the exception."""
    d = w.made("viewer", "email")
    w.later(days=2)

    def broken(*a: Any) -> Any:
        raise TypeError("'NoneType' object has no attribute 'tzinfo'")

    monkeypatch.setattr(w.ctx.deliveries, "claim", broken)
    with caplog.at_level("WARNING"):
        w.ctx.delivery_runner.schedule(w.ctx.sqs, w.now())
    events = [json.loads(r.getMessage())["event"] for r in caplog.records if r.getMessage().startswith("{")]
    assert "digest.claim_failed" in events and "digest.schedule_invalid" not in events
    stored = w.stored("viewer", d["id"])
    assert stored.next_at == iso(w.now() + timedelta(days=1)) and "tzinfo" not in str(stored.last_error)


def test_c12_a_bad_schedule_is_shown_not_alarmed(
    w: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    d = w.made("viewer", "email")
    w.later(days=2)

    def bad(*a: Any) -> Any:
        raise ValidationError("unknown time zone: Mars/Olympus")

    monkeypatch.setattr(w.ctx.deliveries, "claim", bad)
    with caplog.at_level("WARNING"):
        w.ctx.delivery_runner.schedule(w.ctx.sqs, w.now())
    events = [json.loads(r.getMessage())["event"] for r in caplog.records if r.getMessage().startswith("{")]
    assert "digest.schedule_invalid" in events and "digest.claim_failed" not in events
    assert (
        w.stored("viewer", d["id"]).last_error == "its schedule cannot be worked out: unknown time zone: Mars/Olympus"
    )


def test_c11_a_store_error_on_claim_is_a_queue_fault(
    w: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    w.made("viewer", "email")
    w.later(days=2)

    def throttled(*a: Any) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "UpdateItem")

    monkeypatch.setattr(w.ctx.deliveries, "claim", throttled)
    with caplog.at_level("WARNING"):
        w.ctx.delivery_runner.schedule(w.ctx.sqs, w.now())
    events = [json.loads(r.getMessage())["event"] for r in caplog.records if r.getMessage().startswith("{")]
    assert "digest.claim_failed" in events


def test_c12_a_schedule_edit_keeps_the_lookup_miss_count(w: World) -> None:
    """Scan cycle 12: a schedule edit cannot fix a missing Slack user, so the 7-send limit still applies."""
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "no Slack user has your email address", lookup=True)
    w.ctx.deliveries.fail(subject, d["id"], "your account is disabled")
    w.ctx.deliveries.update(subject, d["id"], {"hour": (d["hour"] + 1) % 24})
    stored = w.stored("viewer", d["id"])
    assert (stored.fails, stored.misses) == (0, 1)
    # c13: the owner's message goes with the owner's count; the lookup miss keeps its own.
    assert stored.fault is None and stored.miss_fault == "no Slack user has your email address"


def test_c13_an_empty_day_after_an_edit_shows_the_lookup_message_not_the_owner_one(w: World) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "no Slack user has your email address", lookup=True)
    w.ctx.deliveries.fail(subject, d["id"], "your account is disabled")
    w.ctx.deliveries.update(subject, d["id"], {"hour": (d["hour"] + 1) % 24})
    w.later(days=2)
    w.send("viewer", d["id"])  # nothing new
    stored = w.stored("viewer", d["id"])
    assert stored.last_status is DeliveryStatus.ERROR and stored.last_error == "no Slack user has your email address"


def test_c13_turning_a_delivery_back_on_starts_the_lookup_count_again(w: World) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    for _ in range(LOOKUP_MISS_LIMIT):
        w.ctx.deliveries.fail(subject, d["id"], "no Slack user has your email address", lookup=True)
    assert w.stored("viewer", d["id"]).enabled is False
    w.ctx.deliveries.update(subject, d["id"], {"enabled": True})
    stored = w.stored("viewer", d["id"])
    assert stored.enabled is True and (stored.misses, stored.miss_fault) == (0, None)
    assert not w.ctx.deliveries.fail(subject, d["id"], "no Slack user has your email address", lookup=True)
    assert w.stored("viewer", d["id"]).enabled is True  # one miss is one, not the eighth


def test_c14_a_bounce_during_a_send_is_shown_from_suppressed(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scan cycle 14: the stop is shown from `suppressed`, which no outcome writer touches, so a send that was
    already running cannot hide it."""
    d = w.made("viewer", "email")
    subject = w.subject("viewer")
    w.later(hours=1)
    real = w.ctx.deliveries.advance

    def bounce_first(delivery: Any, cursor: str, status: DeliveryStatus, **k: Any) -> Any:
        w.ctx.deliveries.switch_off(subject, d["id"], suppress=True, reason="the address bounced")
        return real(delivery, cursor, status, **k)

    monkeypatch.setattr(w.ctx.deliveries, "advance", bounce_first)
    w.send("viewer", d["id"])  # nothing new
    stored = w.stored("viewer", d["id"])
    assert stored.suppressed is True and stored.to_public()["suppressed"] is True


def test_c14_a_complaint_while_the_digest_is_built_stops_the_send(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scan cycle 14: the row is read again just before sending, so no mail goes to an address that just
    complained."""
    did = ready(w, "email")
    subject = w.subject("viewer")
    runner = w.ctx.delivery_runner
    real = runner.prepare

    def complain_while_building(d: Any) -> Any:
        p = real(d)
        w.ctx.deliveries.switch_off(subject, did, suppress=True, reason="the digest was reported as spam")
        return p

    monkeypatch.setattr(runner, "prepare", complain_while_building)
    assert runner.deliver(subject, did) == "skipped"
    assert w.mails() == []


def test_c14_an_edit_during_an_empty_send_keeps_the_owner_message_cleared(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "no Slack user has your email address", lookup=True)
    w.ctx.deliveries.fail(subject, d["id"], "your account is disabled")
    w.later(hours=1)
    real = w.ctx.deliveries.advance

    def edit_first(delivery: Any, cursor: str, status: DeliveryStatus, **k: Any) -> bool:
        monkeypatch.setattr(w.ctx.deliveries, "advance", real)  # the edit happens once, before the first write
        w.ctx.deliveries.update(subject, d["id"], {"hour": (d["hour"] + 1) % 24})  # clears fails and fault
        return real(delivery, cursor, status, **k)

    monkeypatch.setattr(w.ctx.deliveries, "advance", edit_first)
    w.send("viewer", d["id"])  # nothing new
    assert w.stored("viewer", d["id"]).last_error != "your account is disabled"


def test_c14_a_switch_off_during_an_owner_edit_is_not_undone(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    real = w.ctx.deliveries.get

    def switched_off_after_read(s: str, did: str, *a: Any, **k: Any) -> Any:
        got = real(s, did, *a, **k)
        monkeypatch.setattr(w.ctx.deliveries, "get", real)
        w.ctx.deliveries.switch_off(s, did, suppress=False, reason="switched off after 7 failed sends in a row")
        return got

    monkeypatch.setattr(w.ctx.deliveries, "get", switched_off_after_read)
    with pytest.raises(ConflictError):
        w.ctx.deliveries.update(subject, d["id"], {"hour": (d["hour"] + 1) % 24})
    assert w.stored("viewer", d["id"]).enabled is False


def test_c14_a_pause_and_resume_keeps_the_lookup_count(w: World) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "no Slack user has your email address", lookup=True)
    w.ctx.deliveries.update(subject, d["id"], {"enabled": False})
    w.ctx.deliveries.update(subject, d["id"], {"enabled": True})
    assert w.stored("viewer", d["id"]).misses == 1


def test_c15_an_edit_during_an_empty_send_keeps_the_lookup_fault_standing(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scan cycle 15: when the owner's pair was cleared meanwhile, only that pair is dropped; the lookup-miss
    count still stands and keeps status error with its own message."""
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "no Slack user for your alias", lookup=True)
    w.ctx.deliveries.fail(subject, d["id"], "your account is disabled")
    w.later(hours=1)
    real = w.ctx.deliveries.advance
    edited = []

    def edit_first(delivery: Any, cursor: str, status: DeliveryStatus, **k: Any) -> bool:
        if not edited:
            edited.append(1)
            w.ctx.deliveries.update(subject, d["id"], {"hour": (d["hour"] + 1) % 24})  # clears fails and fault
        return real(delivery, cursor, status, **k)

    monkeypatch.setattr(w.ctx.deliveries, "advance", edit_first)
    w.send("viewer", d["id"])  # nothing new
    stored = w.stored("viewer", d["id"])
    assert stored.last_status is DeliveryStatus.ERROR and stored.last_error == "no Slack user for your alias"
    assert stored.misses == 1


def test_c15_send_test_now_does_not_mail_an_address_that_just_complained(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    did = ready(w, "email")
    subject = w.subject("viewer")
    runner = w.ctx.delivery_runner
    real = runner.prepare

    def complain_while_building(d: Any) -> Any:
        p = real(d)
        w.ctx.deliveries.switch_off(subject, did, suppress=True, reason="the digest was reported as spam")
        return p

    monkeypatch.setattr(runner, "prepare", complain_while_building)
    got = runner.test(w.stored("viewer", did))  # c18: an answer the route audits, then turns into its 409
    assert got["suppressed"] is True and got["sent"] is False and got["total"] > 0 and w.mails() == []


def test_c15_the_stop_reason_survives_a_later_write(w: World) -> None:
    d = w.made("viewer", "email")
    subject = w.subject("viewer")
    w.ctx.deliveries.switch_off(subject, d["id"], suppress=True, reason="the address bounced (MailboxFull)")
    w.ctx.deliveries.record(subject, d["id"], DeliveryStatus.EMPTY, None)  # a send that was already running
    public = w.stored("viewer", d["id"]).to_public()
    assert public["suppressed"] is True and public["stop_reason"] == "the address bounced (MailboxFull)"


def test_c15_a_pause_a_switch_off_got_to_first_is_accepted(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    real = w.ctx.deliveries.get

    def switched_off_after_read(s: str, did: str, *a: Any, **k: Any) -> Any:
        got = real(s, did, *a, **k)
        monkeypatch.setattr(w.ctx.deliveries, "get", real)
        w.ctx.deliveries.switch_off(s, did, suppress=False, reason="switched off after 3 failed sends in a row")
        return got

    monkeypatch.setattr(w.ctx.deliveries, "get", switched_off_after_read)
    assert w.ctx.deliveries.update(subject, d["id"], {"enabled": False}).enabled is False


def test_c15_an_edit_after_a_switch_on_is_told_it_was_switched_on(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.update(subject, d["id"], {"enabled": False})
    real = w.ctx.deliveries.get

    def switched_on_after_read(s: str, did: str, *a: Any, **k: Any) -> Any:
        got = real(s, did, *a, **k)
        monkeypatch.setattr(w.ctx.deliveries, "get", real)
        w.ctx.deliveries.update(s, did, {"enabled": True})  # another tab
        return got

    monkeypatch.setattr(w.ctx.deliveries, "get", switched_on_after_read)
    with pytest.raises(ConflictError, match="switched on"):
        w.ctx.deliveries.update(subject, d["id"], {"hour": (d["hour"] + 1) % 24})


def test_c16_a_test_send_on_a_delivery_deleted_meanwhile_says_so(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    did = ready(w, "slack_channel")
    subject = w.subject("viewer")
    runner = w.ctx.delivery_runner
    real = runner.prepare

    def delete_while_building(d: Any) -> Any:
        p = real(d)
        w.ctx.deliveries.delete(subject, did)
        return p

    monkeypatch.setattr(runner, "prepare", delete_while_building)
    got = runner.test(w.stored("viewer", did))
    assert got["error"] == "this delivery was deleted" and w.posts() == []


def test_c16_a_pause_a_bounce_got_to_first_names_the_bounce(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    d = w.made("viewer", "email")
    subject = w.subject("viewer")
    real = w.ctx.deliveries.get

    def bounced_after_read(s: str, did: str, *a: Any, **k: Any) -> Any:
        got = real(s, did, *a, **k)
        monkeypatch.setattr(w.ctx.deliveries, "get", real)
        w.ctx.deliveries.switch_off(s, did, suppress=True, reason="the address bounced")
        return got

    monkeypatch.setattr(w.ctx.deliveries, "get", bounced_after_read)
    with pytest.raises(ConflictError) as err:
        w.ctx.deliveries.update(subject, d["id"], {"enabled": False})
    assert err.value.details == {"code": "suppressed"}


def test_c16_a_cursor_race_on_an_empty_day_makes_no_retry(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "your account is disabled")
    w.ctx.deliveries.fail(subject, d["id"], "no Slack user for your alias", lookup=True)
    stale = w.stored("viewer", d["id"])
    w.later(hours=1)
    assert w.ctx.deliveries.advance(stale, iso(w.now()), DeliveryStatus.EMPTY) is True  # the first sender
    calls: list[Any] = []
    real = w.ctx.deliveries.advance
    monkeypatch.setattr(w.ctx.deliveries, "advance", lambda *a, **k: calls.append(1) or real(*a, **k))
    later = iso(w.now() + timedelta(minutes=1))  # a second sender built its own digest
    assert w.ctx.deliveries.advance(stale, later, DeliveryStatus.EMPTY) is False  # the second one
    assert len(calls) == 1  # no retry: the cursor had moved


def test_c19_the_owner_edit_retry_needs_no_read(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The empty-day retry starts from the row the failed write returned: find() is never called (c18 #5)."""
    d = w.made("viewer", "slack_dm")
    subject = w.subject("viewer")
    w.ctx.deliveries.fail(subject, d["id"], "your account is disabled")
    w.later(hours=1)
    real = w.ctx.deliveries.advance

    def edit_first(delivery: Any, cursor: str, status: DeliveryStatus, **k: Any) -> Any:
        monkeypatch.setattr(w.ctx.deliveries, "advance", real)
        w.ctx.deliveries.update(subject, d["id"], {"hour": (d["hour"] + 1) % 24})  # clears fails and fault
        monkeypatch.setattr(w.ctx.deliveries, "find", lambda *a: (_ for _ in ()).throw(AssertionError("read")))
        return real(delivery, cursor, status, **k)

    monkeypatch.setattr(w.ctx.deliveries, "advance", edit_first)
    w.send("viewer", d["id"])  # nothing new
    monkeypatch.undo()
    stored = w.stored("viewer", d["id"])
    assert stored.fails == 0 and stored.last_error is None and stored.cursor is not None


@pytest.mark.parametrize("kind", ["slack_channel", "slack_dm"])
def test_a_refused_message_is_never_posted_a_second_time(w: World, kind: str) -> None:
    """Scan cycle 7: one post per send; a size refusal is recorded like any permanent error, never re-posted."""
    did = ready(w, kind)
    w.http.replies = [*prefix(kind), slack_error("msg_blocks_too_long")]
    w.send("viewer", did)
    assert len(w.posts()) == 1 and w.stored("viewer", did).last_error == "Slack: msg_blocks_too_long"
