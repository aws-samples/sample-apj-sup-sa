# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Digest delivery: the schedule (time zones, DST, weekdays), the deploy config, the stored item."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from insights_radar.core.errors import ValidationError
from insights_radar.insights.delivery import (
    Cadence,
    Delivery,
    DeliveryConfig,
    DeliveryKind,
    DeliveryService,
    DeliveryStatus,
    DmResolver,
    EmailMode,
    Schedule,
    SlackMode,
    clean_error,
)


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def nxt(cadence: Cadence, weekday: int | None, hour: int, tz: str, after: datetime) -> datetime:
    return Schedule.next_at(cadence, weekday, hour, Schedule.zone(tz), after)


def test_daily_is_the_next_whole_hour_in_the_zone() -> None:
    # 08:00 UTC is 18:00 in Sydney (AEST, +10): 09:00 Sydney is tomorrow, 23:00 UTC today.
    assert nxt(Cadence.DAILY, None, 9, "Australia/Sydney", utc(2026, 6, 2, 8)) == utc(2026, 6, 2, 23)
    # Exactly at the hour: strictly after, so the next day.
    assert nxt(Cadence.DAILY, None, 8, "UTC", utc(2026, 6, 2, 8)) == utc(2026, 6, 3, 8)
    assert nxt(Cadence.DAILY, None, 9, "UTC", utc(2026, 6, 2, 8, 59)) == utc(2026, 6, 2, 9)


def test_weekly_lands_on_its_weekday_python_numbering() -> None:
    # 2026-10-02 is a Friday (weekday 4). Monday is 0.
    friday = utc(2026, 10, 2, 8)
    assert friday.weekday() == 4
    assert nxt(Cadence.WEEKLY, 0, 9, "Europe/London", friday) == utc(2026, 10, 5, 8)  # Mon 09:00 BST
    assert nxt(Cadence.WEEKLY, 4, 10, "Europe/London", friday) == utc(2026, 10, 2, 9)  # later today
    assert nxt(Cadence.WEEKLY, 4, 9, "Europe/London", friday) == utc(2026, 10, 9, 8)  # 09:00 BST passed: next week
    assert nxt(Cadence.WEEKLY, 6, 23, "Pacific/Kiritimati", friday).weekday() in (5, 6)


def test_dst_spring_forward_hour_that_does_not_exist() -> None:
    # 8 March 2026, New York: 02:00 local does not exist; it is sent when the clocks jump (03:00 EDT, 07:00 UTC).
    got = nxt(Cadence.DAILY, None, 2, "America/New_York", utc(2026, 3, 8, 0))
    assert got == utc(2026, 3, 8, 7)
    # The day after, 02:00 EDT is 06:00 UTC: one send per local day, none skipped.
    assert nxt(Cadence.DAILY, None, 2, "America/New_York", got) == utc(2026, 3, 9, 6)


def test_dst_fall_back_repeated_hour_is_sent_once() -> None:
    # 1 November 2026, New York: 01:00 happens twice (EDT, then EST). Only the first is used.
    first = nxt(Cadence.DAILY, None, 1, "America/New_York", utc(2026, 11, 1, 0))
    assert first == utc(2026, 11, 1, 5)  # 01:00 EDT
    again = nxt(Cadence.DAILY, None, 1, "America/New_York", first)
    assert again == utc(2026, 11, 2, 6)  # the next day 01:00 EST, not the second 01:00 an hour later


def test_dst_keeps_local_hour_across_the_change() -> None:
    before = nxt(Cadence.WEEKLY, 0, 9, "Europe/Berlin", utc(2026, 10, 20))
    after = nxt(Cadence.WEEKLY, 0, 9, "Europe/Berlin", before)
    assert before == utc(2026, 10, 26, 8)  # Monday after the change: CET, +1
    assert after == utc(2026, 11, 2, 8)
    summer = nxt(Cadence.WEEKLY, 0, 9, "Europe/Berlin", utc(2026, 10, 13))
    assert summer == utc(2026, 10, 19, 7)  # CEST, +2
    assert after - summer == timedelta(days=14, hours=1)


@pytest.mark.parametrize(
    "name",
    ["", "Not/AZone", "../etc/passwd", "/usr/share/zoneinfo/UTC", "Europe/London\n", "a" * 80, 7, "Europe//London"],
)
def test_invalid_time_zones_are_refused(name: object) -> None:
    with pytest.raises(ValidationError):
        Schedule.zone(name)  # type: ignore[arg-type]


def test_check_schedule() -> None:
    assert DeliveryService.check_schedule("daily", 3, 7, "UTC") == (Cadence.DAILY, None)
    assert DeliveryService.check_schedule("weekly", 0, 7, "UTC") == (Cadence.WEEKLY, 0)
    for args in (
        ("hourly", None, 7, "UTC"),
        ("weekly", None, 7, "UTC"),
        ("weekly", 7, 7, "UTC"),
        ("weekly", True, 7, "UTC"),
        ("weekly", "1", 7, "UTC"),
        ("daily", None, 24, "UTC"),
        ("daily", None, -1, "UTC"),
        ("daily", None, True, "UTC"),
        ("daily", None, "7", "UTC"),
        ("daily", None, 7, "Mars/Olympus"),
    ):
        with pytest.raises(ValidationError):
            DeliveryService.check_schedule(*args)  # type: ignore[arg-type]


def test_config_is_off_unless_set() -> None:
    off = DeliveryConfig.from_env({})
    assert not off.any and not off.slack_channel and not off.slack_dm and not off.email
    assert DeliveryConfig() == off
    typo = DeliveryConfig.from_env({"DIGEST_SLACK_MODE": "on", "DIGEST_EMAIL_MODE": "smtp", "DIGEST_DM_RESOLVER": "x"})
    assert (typo.slack_mode, typo.email_mode, typo.dm_resolver) == (SlackMode.OFF, EmailMode.OFF, DmResolver.NONE)


def test_config_turns_each_channel_on() -> None:
    env = {
        "DIGEST_SLACK_MODE": " BOT ",
        "DIGEST_DM_RESOLVER": "email",
        "DIGEST_EMAIL_MODE": "ses",
        "DIGEST_EMAIL_SENDER": "no-reply@example.com",
        "DIGEST_SES_CONFIG_SET": "cfg",
        "DIGEST_SLACK_SECRET_ARN": "arn:slack",
        "DIGEST_UNSUB_SECRET_ARN": "arn:unsub",
        "DIGEST_QUEUE_URL": "https://sqs/q",
        "APP_URL": "https://app.example.com/",
    }
    c = DeliveryConfig.from_env(env)
    assert c.slack_channel and c.slack_dm and c.email and c.any
    assert c.app_url == "https://app.example.com"
    assert all(c.allows(k) for k in DeliveryKind)
    # No secret, no channel; no sender or key or app address, no email.
    assert not DeliveryConfig.from_env({**env, "DIGEST_SLACK_SECRET_ARN": ""}).slack_channel
    assert not DeliveryConfig.from_env({**env, "DIGEST_SLACK_SECRET_ARN": ""}).slack_dm
    assert not DeliveryConfig.from_env({**env, "DIGEST_UNSUB_SECRET_ARN": ""}).email
    assert not DeliveryConfig.from_env({**env, "APP_URL": "http://app.example.com"}).email
    assert DeliveryConfig.from_env({**env, "APP_URL": "javascript:alert(1)"}).app_url == ""
    # A webhook posts to one channel: no DMs. The directory lookup fails closed until it has its endpoint and role.
    hook = DeliveryConfig.from_env({**env, "DIGEST_SLACK_MODE": "webhook"})
    assert hook.slack_channel and not hook.slack_dm
    directory = DeliveryConfig.from_env({**env, "DIGEST_DM_RESOLVER": "directory"})
    assert not directory.slack_dm
    directory = DeliveryConfig.from_env(
        {
            **env,
            "DIGEST_DM_RESOLVER": "directory",
            "DIGEST_DIRECTORY_ENDPOINT": "https://x",
            "DIGEST_DIRECTORY_ROLE_ARN": "arn:r",
        }
    )
    assert directory.slack_dm
    assert not DeliveryConfig.from_env({**env, "DIGEST_DM_RESOLVER": "none"}).slack_dm


def test_item_round_trip_and_due_keys() -> None:
    d = Delivery(
        id="0123456789ab",
        subject="s-1",
        username="user-1",
        kind=DeliveryKind.EMAIL,
        target_label="a@example.com",
        cadence=Cadence.WEEKLY,
        hour=9,
        timezone="UTC",
        address="a@example.com",
        weekday=2,
        next_at="2026-10-07T09:00:00.000Z",
        cursor="2026-10-01T00:00:00.000Z",
        last_status=DeliveryStatus.OK,
        created_at="2026-10-01T00:00:00.000Z",
    )
    item = d.to_item()
    assert item["due_pk"] == "DELIVERY_DUE" and item["due_sk"] == "2026-10-07T09:00:00.000Z#s-1#0123456789ab"
    assert Delivery.from_item(item) == d
    public = d.to_public()
    assert "cursor" not in public and "address" not in public and "subject" not in public
    assert public["next_at"] == d.next_at and public["last_status"] == "ok" and public["weekday"] == 2
    off = Delivery.from_item({**item, "enabled": False})
    assert "due_pk" not in off.to_item() and off.to_public()["next_at"] is None
    bare = Delivery.from_item({k: v for k, v in item.items() if k not in ("username", "weekday", "last_status")})
    assert bare.username == "s-1" and bare.weekday is None and bare.last_status is None


def test_clean_error() -> None:
    assert clean_error("bad\nthing\x00 here ") == "bad thing  here"
    assert len(clean_error("x" * 1000)) == 300
