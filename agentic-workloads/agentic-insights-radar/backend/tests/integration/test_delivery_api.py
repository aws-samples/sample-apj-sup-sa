# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Digest delivery end to end on moto: the routes, the scheduler, the sender, SES feedback and the unsubscribe
link, with Slack, SES and the queue faked (no network)."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError

from insights_radar.api import workers
from insights_radar.api.app import Application
from insights_radar.api.context import Settings
from insights_radar.core.clock import FixedClock, iso
from insights_radar.core.errors import NotFoundError, RateLimitedError, ValidationError
from insights_radar.core.ratelimit import RateLimiter
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.identity.principal import Role
from insights_radar.insights import delivery as delivery_mod
from insights_radar.insights.delivery import DeliveryConfig, DeliveryStatus, DueDelivery
from insights_radar.insights.delivery_runner import AwsOutlets, DeliveryRunner, Queued, parse_message
from insights_radar.insights.delivery_transport import HttpResponse, TransientDeliveryError
from tests.integration import harness
from tests.integration.harness import SLACK_CHANNEL, SLACK_DM, Client, TestContext

# A fake value the stack would hold, passed by name.
NOT_A_REAL_VALUE = "NotARealToken0000000000000000000"

# Fake values the stack would hold; passed by name so no literal sits next to a credential field.
STACK_PLACEHOLDER = "placeholder-from-the-stack"
LEASE_1, LEASE_2 = "t1", "t2"
KEY_ARN = "arn:key"

ON = DeliveryConfig.from_env(
    {
        "DIGEST_SLACK_MODE": "bot",
        "DIGEST_DM_RESOLVER": "email",
        "DIGEST_EMAIL_MODE": "ses",
        "DIGEST_EMAIL_SENDER": "no-reply@example.com",
        "DIGEST_SES_CONFIG_SET": "air-digest",
        "DIGEST_SLACK_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:111122223333:secret:slack",
        "DIGEST_UNSUB_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:111122223333:secret:unsub",
        "DIGEST_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/111122223333/digest",
        "APP_URL": "https://app.example.com",
    }
)
CHANNEL = SLACK_CHANNEL
DELIVERY = {"cadence": "daily", "hour": 9, "timezone": "Europe/London"}


class FakeSqs:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.visibility: list[dict[str, Any]] = []
        self.fail = False

    def send_message(self, **kw: Any) -> dict[str, Any]:
        if self.fail:
            raise ClientError({"Error": {"Code": "KMS.DisabledException", "Message": "m"}}, "SendMessage")
        self.messages.append(kw)
        return {"MessageId": "m"}

    def change_message_visibility(self, **kw: Any) -> None:
        self.visibility.append(kw)


class DigestContext(TestContext):
    __test__ = False

    def __init__(self, *a: Any) -> None:
        super().__init__(*a)
        self.sqs = FakeSqs()
        self.settings = replace(self.settings, digest=ON)

    def client(self, name: str) -> Any:
        assert name == "sqs"
        return self.sqs


@pytest.fixture
def ctx(graph_table: Any, ops_table: Any, clock: FixedClock) -> DigestContext:
    return DigestContext(graph_table, ops_table, clock)


@pytest.fixture(autouse=True)
def harness_users_exist(ctx: DigestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """The harness callers (u-viewer, u-editor, u-admin) are accounts the sender can look up: every
    delivery kind now stops when its subscriber's account is gone (matrix BUG-1)."""
    real = ctx.users.get
    roles = {"u-viewer": Role.VIEWER, "u-editor": Role.EDITOR, "u-admin": Role.ADMIN}
    made: dict[str, Any] = {}

    def get(name: str) -> Any:
        try:
            return real(name)
        except NotFoundError:
            if name not in roles:
                raise
            if name not in made:
                view = ctx.users.invite(f"{name}@harness.example.com", roles[name])
                made[name] = replace(view, username=name, subject=name, email=f"{name.removeprefix('u-')}@example.com")
            return made[name]

    monkeypatch.setattr(ctx.users, "get", get)


@pytest.fixture
def api(ctx: DigestContext) -> Client:
    return Client(Application(ctx))


def user(ctx: DigestContext, monkeypatch: pytest.MonkeyPatch, name: str, email: str, role: Role) -> str:
    """A real user-pool user (the sender confirms the role and address live) callable as ``name``."""
    view = ctx.users.invite(email, role)
    groups = f"[{role.value}]" if role is not Role.VIEWER else "[]"
    monkeypatch.setitem(
        harness.ROLES,
        name,
        {"sub": view.subject, "email": view.email, "cognito:groups": groups, "cognito:username": view.username},
    )
    return view.subject


def set_channels(api: Client, *channels: tuple[str, str]) -> None:
    body = {"channels": [{"id": i, "label": la} for i, la in channels]}
    s, b, _ = api.call("PUT", "/api/admin/digest/channels", body)
    assert s == 200, b


def create(api: Client, role: str, **body: Any) -> tuple[int, Any]:
    s, b, _ = api.call("POST", "/api/me/deliveries", {**DELIVERY, **body}, role=role)
    return s, b


def signal(ctx: DigestContext, title: str = "Tariff change", **kw: Any) -> Node:
    return ctx.graph.create(Node(id="", type=NodeType.SIGNAL, title=title, summary="s", **kw), "u")


def send(ctx: DigestContext, subject: str, delivery_id: str, count: int = 1) -> dict[str, Any]:
    body = json.dumps({"subject": subject, "id": delivery_id, "due": "x"})
    event = {
        "Records": [
            {
                "messageId": "m1",
                "body": body,
                "receiptHandle": "rh",
                "attributes": {"ApproximateReceiveCount": str(count)},
            }
        ]
    }
    return workers.digest_send(event, None, ctx)


# ---- off by default -------------------------------------------------------------------------------------------


def test_everything_is_off_by_default(graph_table: Any, ops_table: Any, clock: FixedClock) -> None:
    ctx = TestContext(graph_table, ops_table, clock)
    api = Client(Application(ctx))
    s, b, _ = api.call("GET", "/api/me/deliveries", role="viewer")
    assert s == 200 and b == {
        "items": [],
        "options": {
            "slack_channel": False,
            "slack_dm": False,
            "email": False,
            "channels": [],
            "email_address": "viewer@example.com",
        },
    }
    for kind in ("slack_channel", "slack_dm", "email"):
        s, b = create(api, "viewer", kind=kind, **({"channel_id": CHANNEL} if kind == "slack_channel" else {}))
        assert s == 400 and "not available" in b["message"]
    assert workers.digest_schedule({}, None, ctx) == {"queued": 0, "skipped": 0, "failed": 0}
    record = {"messageId": "m", "body": json.dumps({"subject": "u-viewer", "id": "0123456789ab"})}
    assert workers.digest_send({"Records": [record]}, None, ctx) == {"batchItemFailures": []}
    assert workers.digest_feedback({"Records": [{}, {}]}, None, ctx) == {"suppressed": 0, "ignored": 2}
    assert api.call("POST", "/api/digest/unsubscribe", {"t": "x"}, role="anon")[0] == 404
    page = api.call("GET", "/api/digest/unsubscribe", role="anon", query={"t": "x"})[1]
    assert "not used on this deployment" in page and "<form" not in page  # c38: no form that could only fail
    s, page, _ = api.call(  # c41: the form post with email off is a page too
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "text/html"},
        raw_body="t=x",
    )
    assert s == 404 and "not used on this deployment" in page


# ---- create, list, change, delete -----------------------------------------------------------------------------


def test_email_goes_only_to_the_callers_own_verified_address(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    s, b = create(api, "viewer", kind="email")
    assert s == 201, b
    assert b["kind"] == "email" and b["target_label"] == "viewer@example.com" and b["enabled"] is True
    assert b["next_at"] == "2026-10-03T08:00:00.000Z"  # 09:00 BST tomorrow; 08:00 UTC now
    assert b["last_status"] is None and "address" not in b and "cursor" not in b
    # Nothing in the request can name another address.
    s, b = create(api, "viewer", kind="email", address="victim@example.com")
    assert s == 400 and "unknown fields" in b["message"]
    # An unverified address is no address: the option is off and the create is refused.
    monkeypatch.setitem(
        harness.ROLES, "unverified", {"sub": "u-unv", "email": "x@example.com", "email_verified": "false"}
    )
    s, b, _ = api.call("GET", "/api/me/deliveries", role="unverified")
    assert b["options"]["email"] is False and b["options"]["email_address"] is None
    assert create(api, "unverified", kind="email")[0] == 400


def test_validation(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "radar-news"))
    bad = [
        {"kind": "fax"},
        {"kind": "email", "timezone": "Mars/Base"},
        {"kind": "email", "timezone": "../../etc/passwd"},
        {"kind": "email", "hour": 24},
        {"kind": "email", "hour": "9"},
        {"kind": "email", "cadence": "weekly"},  # no weekday
        {"kind": "email", "cadence": "weekly", "weekday": 7},
        {"kind": "email", "cadence": "hourly"},
        {"kind": "email", "enabled": "yes"},
        {"kind": "email", "channel_id": CHANNEL},
        {"kind": "slack_channel", "channel_id": "C9999999999"},  # not on the admin list
        {"kind": "slack_channel", "channel_id": SLACK_DM},
        {"kind": "slack_channel"},
    ]
    for body in bad:
        s, b = create(api, "viewer", **body)
        assert s == 400, (body, b)
    s, b, _ = api.call("POST", "/api/me/deliveries", {"kind": "email", "hour": 9}, role="viewer")
    assert s == 400 and "required" in b["message"]
    s, b = create(api, "viewer", kind="email", cadence="weekly", weekday=0, enabled=False)
    assert s == 201 and b["weekday"] == 0 and b["enabled"] is False and b["next_at"] is None


def test_at_most_five_deliveries(api: Client, ctx: DigestContext) -> None:
    for _ in range(5):
        assert create(api, "viewer", kind="email")[0] == 201
    s, b = create(api, "viewer", kind="email")
    assert s == 400 and "at most 5" in b["message"]
    assert create(api, "editor", kind="email")[0] == 201  # per person


def test_a_racing_sixth_create_gives_its_place_back(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(5):
        create(api, "viewer", kind="email")
        ctx.clock.advance(timedelta(seconds=1))
    real = ctx.deliveries.for_user
    calls = {"n": 0}

    def stale(subject: str, **kw: Any) -> Any:
        calls["n"] += 1
        return real(subject)[:4] if calls["n"] == 1 else real(subject, **kw)  # the count read before the other

    monkeypatch.setattr(ctx.deliveries, "for_user", stale)
    assert create(api, "viewer", kind="email")[0] == 400
    monkeypatch.undo()
    assert len(ctx.deliveries.for_user("u-viewer")) == 5


def test_someone_elses_delivery_is_not_found(api: Client, ctx: DigestContext) -> None:
    _, mine = create(api, "viewer", kind="email")
    did = mine["id"]
    for method, path, body in (
        ("PATCH", f"/api/me/deliveries/{did}", {"enabled": False}),
        ("DELETE", f"/api/me/deliveries/{did}", None),
        ("POST", f"/api/me/deliveries/{did}/test", {}),
    ):
        s, b, _ = api.call(method, path, body, role="editor")
        assert s == 404, (method, b)
    assert api.call("PATCH", "/api/me/deliveries/not-an-id", {"hour": 3}, role="viewer")[0] == 404
    assert api.call("GET", "/api/me/deliveries", role="editor")[1]["items"] == []
    assert [d["id"] for d in api.call("GET", "/api/me/deliveries", role="viewer")[1]["items"]] == [did]
    assert api.call("GET", "/api/me/deliveries", role="anon")[0] == 401


def test_change_schedule_and_switch_off(api: Client, ctx: DigestContext) -> None:
    _, d = create(api, "viewer", kind="email")
    path = f"/api/me/deliveries/{d['id']}"
    s, b, _ = api.call(
        "PATCH", path, {"cadence": "weekly", "weekday": 2, "hour": 7, "timezone": "Asia/Jakarta"}, role="viewer"
    )
    assert s == 200 and b["cadence"] == "weekly" and b["weekday"] == 2 and b["hour"] == 7
    assert b["next_at"] == "2026-10-07T00:00:00.000Z"  # Wednesday 07:00 WIB (+7)
    assert api.call("PATCH", path, {"cadence": "daily", "weekday": None}, role="viewer")[1]["weekday"] is None
    assert api.call("PATCH", path, {"cadence": "weekly"}, role="viewer")[0] == 400
    assert api.call("PATCH", path, {"timezone": "Nowhere/Land"}, role="viewer")[0] == 400
    assert api.call("PATCH", path, {"channel_id": CHANNEL}, role="viewer")[0] == 400  # the target never changes
    s, b, _ = api.call("PATCH", path, {"enabled": False}, role="viewer")
    assert b["enabled"] is False and b["next_at"] is None
    assert ctx.deliveries.due(ctx.clock.now() + timedelta(days=30)) == []
    s, b, _ = api.call("PATCH", path, {"enabled": True}, role="viewer")
    assert b["next_at"] and len(ctx.deliveries.due(ctx.clock.now() + timedelta(days=30))) == 1
    s, b, _ = api.call("DELETE", path, role="viewer")
    assert s == 200 and b == {"id": d["id"], "deleted": True}
    assert api.call("DELETE", path, role="viewer")[0] == 404


def test_a_suppressed_delivery_stays_off(api: Client, ctx: DigestContext) -> None:
    _, d = create(api, "viewer", kind="email")
    assert ctx.deliveries.switch_off("u-viewer", d["id"], suppress=True, reason="bounced")
    path = f"/api/me/deliveries/{d['id']}"
    s, b, _ = api.call("PATCH", path, {"enabled": True}, role="viewer")
    assert s == 409 and b["details"]["code"] == "suppressed"
    assert api.call("PATCH", path, {"hour": 5, "enabled": False}, role="viewer")[0] == 200
    assert api.call("POST", f"{path}/test", {}, role="viewer")[0] == 409
    listed = api.call("GET", "/api/me/deliveries", role="viewer")[1]["items"][0]
    assert listed["last_status"] == "suppressed" and listed["last_error"] == "bounced"
    assert not ctx.deliveries.switch_off("u-viewer", "0123456789ab", suppress=True)


def test_a_bounce_between_read_and_write_wins(api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    _, d = create(api, "viewer", kind="email")
    real_get = ctx.deliveries.get
    first = {"done": False}

    def get_then_bounce(subject: str, delivery_id: str) -> Any:
        found = real_get(subject, delivery_id)
        if not first["done"]:
            first["done"] = True
            ctx.deliveries.switch_off(subject, delivery_id, suppress=True)
        return found

    monkeypatch.setattr(ctx.deliveries, "get", get_then_bounce)
    s, _, _ = api.call("PATCH", f"/api/me/deliveries/{d['id']}", {"enabled": True}, role="viewer")
    assert s == 409


def test_update_raises_other_dynamo_errors(ctx: DigestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    d = ctx.deliveries.create(
        "s",
        "s",
        delivery_mod.Target(delivery_mod.DeliveryKind.EMAIL, "a", address="a@example.com"),
        cadence="daily",
        weekday=None,
        hour=1,
        timezone="UTC",
    )
    boom = ClientError({"Error": {"Code": "InternalServerError", "Message": "m"}}, "UpdateItem")

    def fail(**kw: Any) -> None:
        raise boom

    monkeypatch.setattr(ctx.ops_table, "update_item", fail)
    for call in (
        lambda: ctx.deliveries.update("s", d.id, {"hour": 2}),
        lambda: ctx.deliveries.take_test("s", d.id),
        lambda: ctx.deliveries.claim(DueDelivery("s", d.id, str(d.next_at)), ctx.clock.now()),
        lambda: ctx.deliveries.advance(d, "c", DeliveryStatus.OK),
        lambda: ctx.deliveries.record("s", d.id, DeliveryStatus.OK, None),
        lambda: ctx.deliveries.switch_off("s", d.id, suppress=False),
    ):
        with pytest.raises(ClientError):
            call()


# ---- Slack channels and DMs ------------------------------------------------------------------------------------


def test_admin_channel_list(api: Client, ctx: DigestContext) -> None:
    assert api.call("GET", "/api/admin/digest/channels")[1] == {"channels": [], "version": 0, "clashes": []}
    set_channels(api, (CHANNEL, " radar-news "), ("G0123456789", "private"))
    assert api.call("GET", "/api/admin/digest/channels")[1]["channels"][0] == {"id": CHANNEL, "label": "radar-news"}
    assert api.call("GET", "/api/admin/digest/channels", role="editor")[0] == 403
    assert api.call("PUT", "/api/admin/digest/channels", {"channels": []}, role="editor")[0] == 403
    bad = [
        [{"id": "C01"}],
        [{"id": "#general", "label": "g"}],
        [{"id": CHANNEL, "label": ""}],
        [{"id": CHANNEL, "label": "x" * 81}],
        [{"id": CHANNEL, "label": "a\nb"}],
        [{"id": CHANNEL, "label": "a", "extra": 1}],
        [{"id": CHANNEL, "label": "a"}, {"id": CHANNEL, "label": "b"}],
        [CHANNEL],
        [{"id": f"C{i:010d}", "label": "x"} for i in range(21)],
    ]
    for channels in bad:
        assert api.call("PUT", "/api/admin/digest/channels", {"channels": channels})[0] == 400, channels
    s, b, _ = api.call("GET", "/api/me/deliveries", role="viewer")
    assert b["options"]["slack_channel"] is True and len(b["options"]["channels"]) == 2
    s, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    assert s == 201 and d["target_label"] == "#radar-news" and d["channel_id"] == CHANNEL
    # A stored list with junk in it shows only real channel ids.
    ctx.ops_table.put_item(Item={**delivery_mod.CHANNELS_KEY, "channels": [{"id": "bad"}, "x", {"id": CHANNEL}]})
    assert [c.id for c in ctx.deliveries.channels()] == [CHANNEL]
    ctx.ops_table.put_item(Item={**delivery_mod.CHANNELS_KEY, "channels": "nope"})
    assert ctx.deliveries.channels() == []


def test_channel_digest_is_built_with_viewer_visibility(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = user(ctx, monkeypatch, "pat", "pat@example.com", Role.EDITOR)
    set_channels(api, (CHANNEL, "radar-news"))
    _, chan = create(api, "pat", kind="slack_channel", channel_id=CHANNEL)
    _, dm = create(api, "pat", kind="slack_dm")
    assert dm["target_label"] == "Slack direct message to you"
    signal(ctx, "Public signal")
    ctx.graph.create(Node(id="", type=NodeType.GAP, title="Secret roadmap gap", summary="s"), "u")
    ctx.graph.create(Node(id="", type=NodeType.TREND, title="Private trend", summary="s", private=True), "u")
    http = ctx.delivery_outlets.http
    assert send(ctx, editor, chan["id"]) == {"batchItemFailures": []}
    channel_post = json.dumps(http.posts()[-1]["body"])
    assert "Public signal" in channel_post
    assert "Secret roadmap gap" not in channel_post and "Private trend" not in channel_post
    assert http.posts()[-1]["body"]["channel"] == CHANNEL
    send(ctx, editor, dm["id"])
    dm_post = http.posts()[-1]["body"]
    assert dm_post["channel"] == SLACK_DM  # conversations.open with the id users.lookupByEmail found
    assert "Secret roadmap gap" in json.dumps(dm_post) and "Private trend" in json.dumps(dm_post)
    lookups = [c for c in http.calls if c["url"].endswith("users.lookupByEmail")]
    assert lookups[-1]["body"] == "email=pat%40example.com"


def test_dm_by_directory_alias_from_the_federated_sign_in(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx.settings = replace(
        ctx.settings,
        identity_provider="Federate",
        digest=replace(
            ON,
            dm_resolver=delivery_mod.DmResolver.DIRECTORY,
            directory_endpoint="https://directory.example.com/x",
            directory_role_arn="arn:aws:iam::111122223333:role/directory",
        ),
    )
    view = ctx.users.invite("lee@example.com", Role.VIEWER)
    monkeypatch.setitem(
        harness.ROLES, "lee", {"sub": view.subject, "email": "lee@example.com", "cognito:username": "Federate_Lee-1"}
    )
    monkeypatch.setattr(ctx.users, "get", lambda name: replace(view, username=name))
    s, b, _ = api.call("GET", "/api/me/deliveries", role="lee")
    assert b["options"]["slack_dm"] is True
    s, d = create(api, "lee", kind="slack_dm")
    assert s == 201
    ctx.delivery_outlets.by_email = False
    ctx.delivery_outlets.directory.ids["lee-1"] = "U0999999999"
    signal(ctx)
    send(ctx, view.subject, d["id"])
    assert ctx.delivery_outlets.http.calls[0]["body"] == {"users": "U0999999999"}
    # No federated alias (a password user, or another provider): no DM.
    monkeypatch.setitem(harness.ROLES, "pw", {"sub": "u-pw", "email": "pw@example.com", "cognito:username": "u-pw"})
    assert api.call("GET", "/api/me/deliveries", role="pw")[1]["options"]["slack_dm"] is False
    monkeypatch.setitem(harness.ROLES, "odd", {"sub": "u-odd", "cognito:username": "Federate_has space"})
    assert api.call("GET", "/api/me/deliveries", role="odd")[1]["options"]["slack_dm"] is False
    assert create(api, "odd", kind="slack_dm")[0] == 400


def test_webhook_mode_posts_to_its_one_channel(api: Client, ctx: DigestContext) -> None:
    ctx.settings = replace(ctx.settings, digest=replace(ON, slack_mode=delivery_mod.SlackMode.WEBHOOK))
    s, b, _ = api.call("GET", "/api/me/deliveries", role="viewer")
    assert b["options"]["channels"] == [{"id": "webhook", "label": "The Slack channel this deployment posts to"}]
    assert b["options"]["slack_dm"] is False
    s, d = create(api, "viewer", kind="slack_channel")
    assert s == 201 and d["channel_id"] == "webhook"
    assert create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)[0] == 400
    signal(ctx)
    send(ctx, "u-viewer", d["id"])
    post = ctx.delivery_outlets.http.posts()[-1]
    assert post["url"] == ctx.delivery_outlets.webhook_url and "channel" not in post["body"]


# ---- sending --------------------------------------------------------------------------------------------------


def test_an_empty_digest_sends_nothing_but_moves_the_cursor(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    assert send(ctx, "u-viewer", d["id"]) == {"batchItemFailures": []}
    assert ctx.delivery_outlets.http.posts() == []
    stored = ctx.deliveries.get("u-viewer", d["id"])
    assert stored.last_status is DeliveryStatus.EMPTY and stored.cursor == iso(ctx.clock.now())
    assert stored.last_sent_at is None


def test_a_send_moves_only_its_own_cursor(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    first = signal(ctx, "First")
    ctx.clock.advance(timedelta(minutes=5))
    send(ctx, "u-viewer", d["id"])
    stored = ctx.deliveries.get("u-viewer", d["id"])
    assert stored.last_status is DeliveryStatus.OK and stored.last_sent_at == iso(ctx.clock.now())
    assert "First" in json.dumps(ctx.delivery_outlets.http.posts()[-1]["body"])
    assert ctx.digests.last_read("u-viewer") is None  # the app's read point is untouched
    # Reading in the app does not change what the delivery sends next.
    api.call("POST", "/api/me/digest/read", {}, role="viewer")
    ctx.clock.advance(timedelta(minutes=5))
    signal(ctx, "Second")
    send(ctx, "u-viewer", d["id"])
    body = json.dumps(ctx.delivery_outlets.http.posts()[-1]["body"])
    assert "Second" in body and "First" not in body and first.id not in body


def test_permanent_and_transient_slack_errors(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    signal(ctx)
    http = ctx.delivery_outlets.http
    http.replies = [HttpResponse(200, {}, b'{"ok": false, "error": "not_in_channel"}')]
    assert send(ctx, "u-viewer", d["id"]) == {"batchItemFailures": []}  # not retried
    stored = ctx.deliveries.get("u-viewer", d["id"])
    assert stored.last_status is DeliveryStatus.ERROR and stored.last_error == "Slack: not_in_channel"
    assert stored.cursor is None  # nothing was delivered, so nothing is skipped next time
    http.replies = [HttpResponse(500, {}, b"")]
    assert send(ctx, "u-viewer", d["id"]) == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert ctx.deliveries.get("u-viewer", d["id"]).last_error == "Slack answered 500 (will retry)"
    assert ctx.sqs.visibility == []
    http.replies = [HttpResponse(429, {"retry-after": "120"}, b"")]
    assert send(ctx, "u-viewer", d["id"]) == {"batchItemFailures": []}  # put back on the queue, not failed
    assert ctx.sqs.visibility == []
    (again,) = ctx.sqs.messages
    assert again["DelaySeconds"] == 120 and json.loads(again["MessageBody"])["requeues"] == 1
    assert ctx.deliveries.get("u-viewer", d["id"]).cursor is None
    ctx.sqs.fail = True  # the queue refuses the new message: held back and failed, as before
    http.replies = [HttpResponse(429, {"retry-after": "120"}, b"")]
    assert send(ctx, "u-viewer", d["id"])["batchItemFailures"]
    assert ctx.sqs.visibility == [{"QueueUrl": ON.queue_url, "ReceiptHandle": "rh", "VisibilityTimeout": 120}]
    ctx.sqs.fail = False
    send(ctx, "u-viewer", d["id"])  # works now
    stored = ctx.deliveries.get("u-viewer", d["id"])
    assert stored.last_status is DeliveryStatus.OK and stored.last_error is None and stored.cursor


def test_a_removed_channel_stops_its_deliveries(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    set_channels(api)
    signal(ctx)
    send(ctx, "u-viewer", d["id"])
    assert ctx.delivery_outlets.http.posts() == []
    assert "removed this channel" in str(ctx.deliveries.get("u-viewer", d["id"]).last_error)


def test_unexpected_failures_are_retried(api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(ctx.digests, "digest", boom)
    assert send(ctx, "u-viewer", d["id"])["batchItemFailures"] == [{"itemIdentifier": "m1"}]


def test_messages_naming_nothing_are_dropped(ctx: DigestContext) -> None:
    for body in (
        "{",
        "[]",
        json.dumps({"subject": "a b", "id": "0123456789ab"}),
        json.dumps({"subject": "s", "id": "../x"}),
    ):
        assert workers.digest_send({"Records": [{"messageId": "m", "body": body}]}, None, ctx) == {
            "batchItemFailures": []
        }
    assert parse_message(json.dumps({"subject": "s-1", "id": "0123456789ab"})) == Queued("s-1", "0123456789ab")
    # A delivery deleted, disabled or switched off since it was queued is skipped.
    assert ctx.delivery_runner.deliver("s-1", "0123456789ab") == "skipped"


def test_a_kind_switched_off_after_queueing_is_not_sent(api: Client, ctx: DigestContext) -> None:
    _, d = create(api, "viewer", kind="email")
    ctx.settings = replace(ctx.settings, digest=replace(ON, email_mode=delivery_mod.EmailMode.OFF))
    assert ctx.delivery_runner.deliver("u-viewer", d["id"]) == "off"
    stored = ctx.deliveries.get("u-viewer", d["id"])
    assert api.call("POST", f"/api/me/deliveries/{d['id']}/test", {}, role="viewer")[1] == {
        "sent": False,
        "total": 0,
        "error": "this kind of delivery is switched off on this deployment",
    }
    with pytest.raises(Exception, match="switched off"):
        ctx.delivery_runner.prepare(stored)


def test_cursor_race_keeps_the_winners_place(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    signal(ctx)
    before = ctx.deliveries.get("u-viewer", d["id"])
    runner = ctx.delivery_runner
    p = runner.prepare(before)
    # Another sender finished first and moved the cursor.
    assert ctx.deliveries.advance(before, "2026-10-02T07:59:00.000Z", DeliveryStatus.OK)
    assert ctx.deliveries.advance(before, p.cursor, DeliveryStatus.OK) is False  # a race, not a deletion
    assert ctx.deliveries.get("u-viewer", d["id"]).cursor == "2026-10-02T07:59:00.000Z"
    moved = ctx.deliveries.get("u-viewer", d["id"])
    assert ctx.deliveries.advance(replace(moved, cursor="something-else"), "x", DeliveryStatus.EMPTY) is False
    # Through the sender: the conditional write fails, the send is still recorded.
    real = ctx.deliveries.advance

    def racing(dd: Any, cursor: str, status: DeliveryStatus, **k: Any) -> Any:
        ctx.deliveries.record(dd.subject, dd.id, DeliveryStatus.OK, None)
        return real(replace(dd, cursor="stale"), cursor, status, **k)

    ctx.deliveries.advance = racing  # type: ignore[method-assign]
    signal(ctx, "Another")
    assert send(ctx, "u-viewer", d["id"]) == {"batchItemFailures": []}
    # A deleted delivery is never brought back by a late status write.
    ctx.deliveries.delete("u-viewer", d["id"])
    ctx.deliveries.record("u-viewer", d["id"], DeliveryStatus.ERROR, "late")
    assert not real(moved, "x", DeliveryStatus.OK)
    assert ctx.deliveries.find("u-viewer", d["id"]) is None


def test_email_send_with_unsubscribe_link(api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    sub = user(ctx, monkeypatch, "pat", "pat@example.com", Role.VIEWER)
    _, d = create(api, "pat", kind="email")
    signal(ctx, "<b>Bold</b> & co")
    ctx.graph.create(Node(id="", type=NodeType.GAP, title="Viewer may not see", summary="s"), "u")
    send(ctx, sub, d["id"])
    sent = ctx.delivery_outlets.ses_client.sent[-1]
    assert sent["Destination"] == {"ToAddresses": ["pat@example.com"]}
    assert sent["EmailTags"] == [{"Name": "delivery", "Value": d["id"]}, {"Name": "subscriber", "Value": sub}]
    html_part = sent["Content"]["Simple"]["Body"]["Html"]["Data"]
    assert "&lt;b&gt;Bold&lt;/b&gt; &amp; co" in html_part and "Viewer may not see" not in html_part
    header = next(h["Value"] for h in sent["Content"]["Simple"]["Headers"] if h["Name"] == "List-Unsubscribe")
    url = header.strip("<>")
    assert url.startswith("https://app.example.com/api/digest/unsubscribe?t=v1.")
    token = parse_qs(urlsplit(url).query)["t"][0]
    assert f'href="https://app.example.com/unsubscribe?t={token}"' in html_part
    # GET only shows a confirm page.
    s, page, headers = api.call("GET", "/api/digest/unsubscribe", role="anon", query={"t": token})
    assert s == 200 and headers["Content-Type"].startswith("text/html") and '<form method="post"' in page
    assert "form-action 'self'" in headers["Content-Security-Policy"]
    assert ctx.deliveries.get(sub, d["id"]).enabled is True
    # One click (RFC 8058): a form post with the token in the URL; no sign-in.
    s, b, _ = api.call(
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        query={"t": token},
        headers={"content-type": "application/x-www-form-urlencoded"},
        raw_body="List-Unsubscribe=One-Click",
    )
    assert s == 200 and b == {"ok": True}
    stored = ctx.deliveries.get(sub, d["id"])
    assert stored.enabled is False and stored.next_at and stored.to_public()["next_at"] is None
    assert ctx.deliveries.due(ctx.clock.now() + timedelta(days=9)) == []
    # Unsubscribing twice, or after the delivery was deleted, is still fine.
    assert api.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 200
    ctx.deliveries.delete(sub, d["id"])
    assert api.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 200


def test_unsubscribe_link_forms_and_refusals(api: Client, ctx: DigestContext) -> None:
    _, d = create(api, "viewer", kind="email")
    token = ctx.delivery_outlets.unsubscribe.issue("u-viewer", d["id"])
    s, page, _ = api.call(
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "text/html"},
        raw_body=f"t={token}",
    )
    assert s == 200 and "You are unsubscribed" in page
    assert ctx.deliveries.get("u-viewer", d["id"]).enabled is False
    other = [
        ({"t": token[:-3] + "AAA"}, 400),
        ({"t": "v1.abc"}, 400),
        ({"t": 5}, 400),
        ({}, 400),
    ]
    for body, status in other:
        assert api.call("POST", "/api/digest/unsubscribe", body, role="anon")[0] == status, body
    assert (
        api.call(
            "POST",
            "/api/digest/unsubscribe",
            role="anon",
            raw_body="nothing=here",
            headers={"content-type": "text/plain"},
        )[0]
        == 400
    )
    ctx.clock.advance(timedelta(days=181))
    s, b, _ = api.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")
    assert s == 410 and b["code"] == "link_expired"
    s, page, headers = api.call(  # c38: the confirm page's form gets the refusal as a page
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "text/html"},
        raw_body=f"t={token}",
    )
    assert s == 410 and "expired" in page and page.startswith("<!DOCTYPE html>")
    assert "/whats-new" in page  # c39, c40: the other way to stop it
    assert {k.lower(): v for k, v in headers.items()}["content-type"].startswith("text/html")
    s, page, _ = api.call("GET", "/api/digest/unsubscribe", role="anon")
    assert s == 200 and "incomplete" in page
    s, page, _ = api.call("GET", "/api/digest/unsubscribe", role="anon", query={"t": '"><script>x</script>'})
    assert "<script>" not in page

    # The key cannot be read: try again later (a 503), never "forged".
    def unavailable() -> str:
        raise TransientDeliveryError("secret_unavailable")

    ctx.delivery_outlets.unsubscribe._key = unavailable
    assert api.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 503
    # Other routes still need a sign-in.
    assert api.call("POST", "/api/digest/other", {}, role="anon")[0] == 401


def test_unsubscribe_audit_failure_is_not_an_error(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, d = create(api, "viewer", kind="email")
    token = ctx.delivery_outlets.unsubscribe.issue("u-viewer", d["id"])

    def broken(*a: Any, **k: Any) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(ctx.audit, "record_actor", broken)
    assert api.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")[0] == 200


def test_the_subscriber_is_checked_live(api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    sub = user(ctx, monkeypatch, "pat", "pat@example.com", Role.EDITOR)
    _, mail = create(api, "pat", kind="email")
    _, dm = create(api, "pat", kind="slack_dm")
    ctx.graph.create(Node(id="", type=NodeType.GAP, title="Editors only", summary="s"), "u")
    # Demoted to viewer: the next digest is a viewer's.
    username = harness.ROLES["pat"]["cognito:username"]  # type: ignore[index]
    ctx.users.set_role(username, Role.VIEWER)
    send(ctx, sub, mail["id"])
    assert ctx.deliveries.get(sub, mail["id"]).last_status is DeliveryStatus.EMPTY
    # The address changed in the user pool: no mail to the old one.
    ctx.cognito.admin_update_user_attributes(
        UserPoolId=ctx.settings.user_pool_id,
        Username=username,
        UserAttributes=[{"Name": "email", "Value": "new@example.com"}],
    )
    signal(ctx)
    sent = len(ctx.delivery_outlets.ses_client.sent)
    send(ctx, sub, mail["id"])
    assert len(ctx.delivery_outlets.ses_client.sent) == sent
    assert "email address changed" in str(ctx.deliveries.get(sub, mail["id"]).last_error)
    send(ctx, sub, dm["id"])  # found by email too
    assert "email address changed" in str(ctx.deliveries.get(sub, dm["id"]).last_error)
    # Disabled, then gone.
    ctx.users.set_enabled(username, False)
    send(ctx, sub, dm["id"])
    assert ctx.deliveries.get(sub, dm["id"]).last_error == "your account is disabled"
    ctx.cognito.admin_delete_user(UserPoolId=ctx.settings.user_pool_id, Username=username)
    send(ctx, sub, dm["id"])
    # The third permanent failure in a row switches it off, with the reason for its owner (scan cycle 2).
    gone = ctx.deliveries.get(sub, dm["id"])
    assert gone.last_error == "switched off after 3 failed sends in a row: your account no longer exists"
    assert gone.enabled is False and not gone.due
    assert ctx.delivery_outlets.http.posts() == []


def test_hidden_lists_are_not_delivered(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    profile = ctx.profile.get()
    ctx.profile.put(
        {"lists": {"signals": {"visible": False}, "trends": {"label": "Shifts", "order": 0}}},
        "user:u-admin",
        profile.version,
        frozenset(),
    )
    signal(ctx, "Hidden list")
    ctx.graph.create(Node(id="", type=NodeType.TREND, title="Shown trend", summary="s"), "u")
    send(ctx, "u-viewer", d["id"])
    body = json.dumps(ctx.delivery_outlets.http.posts()[-1]["body"])
    assert "Hidden list" not in body and "*Shifts* (1)" in body


def test_custom_module_lists_are_delivered_under_their_label(api: Client, ctx: DigestContext) -> None:
    body = {
        "slug": "tariffs",
        "name": "Tariffs",
        "insight_kind": "tariff_item",
        "insight_label": "Tariff changes",
        "grants": ["kg_read", "kg_write_output"],
    }
    assert api.call("POST", "/api/modules", body)[0] == 201
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    ctx.graph.create(
        Node(id="", type=NodeType.INSIGHT, insight_kind="tariff_item", title="Steel duty up", summary="s"), "u"
    )
    send(ctx, "u-viewer", d["id"])
    body_text = json.dumps(ctx.delivery_outlets.http.posts()[-1]["body"])
    label = api.call("GET", "/api/nav")[1]["custom"][0]["label"]
    assert "Steel duty up" in body_text and f"*{label}* (1)" in body_text


def test_watched_records_in_a_delivery(api: Client, ctx: DigestContext) -> None:
    """An email (or a DM) carries the subscriber's watched records; a channel post does not (scan cycle 1, M3)."""
    set_channels(api, (CHANNEL, "n"))
    _, chan = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    _, mail = create(api, "viewer", kind="email")
    acme, _ = ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title="Acme"), "u")
    api.call("PUT", f"/api/me/watches/nodes/{acme.id}", {}, role="viewer")
    fact = ctx.graph.create(
        Node(id="", type=NodeType.FACT, title="Acme recalls", evidence_url="https://a.example/2"), "u"
    )
    ctx.graph.link(fact.id, EdgeType.ABOUT, acme.id, "u")
    send(ctx, "u-viewer", chan["id"])
    assert ctx.delivery_outlets.http.posts() == []  # only the watch list was new: nothing for the channel
    assert ctx.deliveries.get("u-viewer", chan["id"]).last_status is DeliveryStatus.EMPTY
    send(ctx, "u-viewer", mail["id"])
    body = json.dumps(ctx.delivery_outlets.ses_client.sent[-1])
    assert "Watched records" in body and "Acme recalls" in body


# ---- send test now ----------------------------------------------------------------------------------------------


def test_send_test_now(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    path = f"/api/me/deliveries/{d['id']}/test"
    s, b, _ = api.call("POST", path, {}, role="viewer")
    assert s == 200 and b == {"sent": False, "total": 0}  # nothing new: nothing sent
    s, b, headers = api.call("POST", path, {}, role="viewer")
    assert s == 429 and b["details"]["retry_after"] == 600 and headers["Retry-After"] == "600"
    ctx.clock.advance(timedelta(minutes=4))
    assert api.call("POST", path, {}, role="viewer")[1]["details"]["retry_after"] == 360
    ctx.clock.advance(timedelta(minutes=6))
    signal(ctx)
    s, b, _ = api.call("POST", path, {}, role="viewer")
    assert s == 200 and b == {"sent": True, "total": 1}
    stored = ctx.deliveries.get("u-viewer", d["id"])
    assert stored.cursor is None and stored.last_status is None  # a test moves nothing
    ctx.clock.advance(timedelta(minutes=10))
    ctx.delivery_outlets.http.replies = [HttpResponse(200, {}, b'{"ok": false, "error": "channel_not_found"}')]
    s, b, _ = api.call("POST", path, {}, role="viewer")
    assert b == {"sent": False, "total": 1, "error": "Slack: channel_not_found"}
    ctx.clock.advance(timedelta(minutes=10))
    ctx.delivery_outlets.token = STACK_PLACEHOLDER
    assert api.call("POST", path, {}, role="viewer")[1]["error"] == "Slack is not configured on this deployment"


# ---- the scheduler ---------------------------------------------------------------------------------------------


def test_the_scheduler_queues_what_is_due(api: Client, ctx: DigestContext) -> None:
    _, a = create(api, "viewer", kind="email")  # 09:00 London: 08:00 UTC tomorrow
    _, b = create(api, "editor", kind="email", cadence="weekly", weekday=6, timezone="UTC")  # Sundays 09:00
    assert workers.digest_schedule({}, None, ctx) == {"queued": 0, "skipped": 0, "failed": 0}
    ctx.clock.advance(timedelta(days=1))  # Saturday 08:00 UTC: a is due, b is not
    assert workers.digest_schedule({}, None, ctx) == {"queued": 1, "skipped": 0, "failed": 0}
    body = json.loads(ctx.sqs.messages[0]["MessageBody"])
    claim = body.pop("claim")  # names the message for the duplicate check, in the body so a redrive keeps it
    assert re.fullmatch(r"[0-9a-f]{32}", claim)
    assert body == {"subject": "u-viewer", "id": a["id"], "due": "2026-10-03T08:00:00.000Z"}
    assert ctx.sqs.messages[0]["QueueUrl"] == ON.queue_url
    assert ctx.deliveries.get("u-viewer", a["id"]).next_at == "2026-10-04T08:00:00.000Z"
    assert workers.digest_schedule({}, None, ctx)["queued"] == 0  # not twice
    # Missed ticks: one send, then back on the schedule.
    ctx.clock.advance(timedelta(days=3))
    assert workers.digest_schedule({}, None, ctx)["queued"] == 2
    assert ctx.deliveries.get("u-editor", b["id"]).next_at == "2026-10-11T09:00:00.000Z"


def test_the_queue_refusing_puts_the_time_back(api: Client, ctx: DigestContext) -> None:
    _, a = create(api, "viewer", kind="email")
    ctx.clock.advance(timedelta(days=1))
    ctx.sqs.fail = True
    assert workers.digest_schedule({}, None, ctx) == {"queued": 0, "skipped": 0, "failed": 1}
    assert ctx.deliveries.get("u-viewer", a["id"]).next_at == "2026-10-03T08:00:00.000Z"
    ctx.sqs.fail = False
    assert workers.digest_schedule({}, None, ctx)["queued"] == 1


def test_claims_lose_to_edits(api: Client, ctx: DigestContext) -> None:
    _, a = create(api, "viewer", kind="email")
    due = DueDelivery("u-viewer", a["id"], a["next_at"])
    now = ctx.clock.now() + timedelta(days=1)
    api.call("PATCH", f"/api/me/deliveries/{a['id']}", {"hour": 11}, role="viewer")
    assert ctx.deliveries.claim(due, now) is None  # the owner moved it
    assert ctx.deliveries.claim(DueDelivery("u-viewer", "0123456789ab", "x"), now) is None
    stored = ctx.deliveries.get("u-viewer", a["id"])
    fresh = DueDelivery("u-viewer", a["id"], str(stored.next_at))
    ctx.ops_table.update_item(
        Key={"pk": "USER#u-viewer", "sk": f"DELIVERY#{a['id']}"},
        UpdateExpression="SET enabled = :f",
        ExpressionAttributeValues={":f": False},
    )
    assert ctx.deliveries.claim(fresh, now) is None
    assert not ctx.deliveries._move(fresh, str(stored.next_at), "y")
    # Junk in the index is skipped.
    ctx.ops_table.put_item(Item={"pk": "JUNK", "sk": "1", "due_pk": "DELIVERY_DUE", "due_sk": "2000-01-01#nope"})
    assert [x.id for x in ctx.deliveries.due(now)] == [a["id"]]


def test_scheduler_skips_without_a_queue(api: Client, ctx: DigestContext) -> None:
    create(api, "viewer", kind="email")
    ctx.settings = replace(ctx.settings, digest=replace(ON, queue_url=""))
    ctx.clock.advance(timedelta(days=1))
    assert workers.digest_schedule({}, None, ctx) == {"queued": 0, "skipped": 0, "failed": 0}
    assert ctx.delivery_runner.schedule(ctx.sqs, ctx.clock.now()) == {"queued": 0, "skipped": 0, "failed": 0}


def test_hold_back_needs_a_handle(ctx: DigestContext) -> None:
    workers._hold_back(ctx, {}, 30)
    workers._hold_back(ctx, {"receiptHandle": "r"}, None)
    assert ctx.sqs.visibility == []
    workers._hold_back(ctx, {"receiptHandle": "r"}, 10**6)
    assert ctx.sqs.visibility[0]["VisibilityTimeout"] == 43_200


# ---- SES feedback ----------------------------------------------------------------------------------------------


def ses_event(
    kind: str, delivery_id: str, subject: str, to: str = "viewer@example.com", **extra: Any
) -> dict[str, Any]:
    msg = {
        "eventType": kind,
        "mail": {"destination": [to], "tags": {"delivery": [delivery_id], "subscriber": [subject]}},
        **extra,
    }
    return {"Records": [{"Sns": {"Message": json.dumps(msg)}}]}


def test_bounces_and_complaints_suppress(api: Client, ctx: DigestContext) -> None:
    _, a = create(api, "viewer", kind="email")
    _, b = create(api, "viewer", kind="email")
    transient = ses_event("Bounce", a["id"], "u-viewer", bounce={"bounceType": "Transient"})
    assert workers.digest_feedback(transient, None, ctx) == {"suppressed": 0, "ignored": 1}
    wrong_to = ses_event("Bounce", a["id"], "u-viewer", to="other@example.com", bounce={"bounceType": "Permanent"})
    assert workers.digest_feedback(wrong_to, None, ctx)["suppressed"] == 0
    bounce = ses_event(
        "Bounce",
        a["id"],
        "u-viewer",
        to="VIEWER@example.com",
        bounce={"bounceType": "Permanent", "bounceSubType": "General"},
    )
    assert workers.digest_feedback(bounce, None, ctx) == {"suppressed": 1, "ignored": 0}
    stored = ctx.deliveries.get("u-viewer", a["id"])
    assert stored.suppressed and not stored.enabled and stored.last_status is DeliveryStatus.SUPPRESSED
    assert stored.last_error == "the address bounced (General)"
    complaint = {
        "Records": [
            {
                "Sns": {
                    "Message": json.dumps(
                        {
                            "notificationType": "Complaint",
                            "mail": {
                                "destination": ["viewer@example.com"],
                                "tags": {"delivery": [b["id"]], "subscriber": ["u-viewer"]},
                            },
                        }
                    )
                }
            }
        ]
    }
    assert workers.digest_feedback(complaint, None, ctx)["suppressed"] == 1
    assert ctx.deliveries.get("u-viewer", b["id"]).last_error == "the digest was reported as spam"


def test_feedback_ignores_anything_else(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, chan = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    records = [
        {},
        {"Sns": {"Message": "not json"}},
        {"Sns": {"Message": "[1]"}},
        {"Sns": {"Message": 5}},
        {"Sns": {"Message": json.dumps({"eventType": "Delivery"})}},
        {"Sns": {"Message": json.dumps({"eventType": "Complaint", "mail": {"tags": {"delivery": "x"}}})}},
        {"Sns": {"Message": json.dumps({"eventType": "Complaint", "mail": {"tags": {"delivery": ["0123456789ab"]}}})}},
        {
            "Sns": {
                "Message": json.dumps(
                    {
                        "eventType": "Complaint",
                        "mail": {"tags": {"delivery": ["0123456789ab"], "subscriber": ["bad subject!"]}},
                    }
                )
            }
        },
        ses_event("Complaint", chan["id"], "u-viewer")["Records"][0],  # not an email delivery
        ses_event("Complaint", "0123456789ab", "u-viewer")["Records"][0],  # no such delivery
    ]
    assert workers.digest_feedback({"Records": records}, None, ctx) == {"suppressed": 0, "ignored": len(records)}


# ---- wiring ----------------------------------------------------------------------------------------------------


class _Ctx:
    def __init__(self, request_scoped: bool, resolver: str) -> None:
        self.request_scoped = request_scoped
        self.settings = replace(
            Settings.from_env({}),
            region="eu-west-1",
            digest=replace(ON, dm_resolver=delivery_mod.DmResolver(resolver)),
        )
        self.clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
        self.clients: list[str] = []

    def client(self, name: str) -> Any:
        self.clients.append(name)

        class SM:
            def get_secret_value(self, SecretId: str) -> dict[str, str]:
                return {
                    "SecretString": json.dumps(
                        {"slack_bot_token": "xoxb-1", "webhook_url": "https://hooks.slack.com/x", "key": "q" * 40}
                    )
                }

        return SM()


def test_a_placeholder_slack_secret_is_not_offered(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "n"))
    ctx.delivery_outlets.token = NOT_A_REAL_VALUE
    options = api.call("GET", "/api/me/deliveries", role="viewer")[1]["options"]
    assert options["slack_channel"] is False and options["slack_dm"] is False and options["channels"] == []
    assert options["email"] is True
    assert create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)[0] == 400
    assert create(api, "viewer", kind="slack_dm")[0] == 400


def test_aws_outlets_build_the_real_clients() -> None:
    api_ctx = _Ctx(True, "email")
    outlets = AwsOutlets(api_ctx)
    assert outlets.slack._timeout == 4.0 and outlets.webhook._url() == "https://hooks.slack.com/x"
    assert outlets.slack._token(False) == "xoxb-1"
    assert type(outlets.slack_ids).__name__ == "EmailSlackIds"
    assert outlets.ses._config_set == "air-digest" and outlets.ses._sender == "no-reply@example.com"
    token = outlets.unsubscribe.issue("s", "0123456789ab")
    assert outlets.unsubscribe.verify(token) == ("s", "0123456789ab")
    worker_ctx = _Ctx(False, "directory")
    directory = AwsOutlets(worker_ctx).slack_ids
    assert (
        type(directory).__name__ == "DirectorySlackIds"
        and directory._region == "eu-west-1"
        and directory._timeout == 5.0
    )
    assert {"secretsmanager", "sesv2", "sts"} <= set(api_ctx.clients + worker_ctx.clients)
    assert outlets.slack_configured() is True


@pytest.mark.parametrize(
    ("mode", "secret", "ready"),
    [
        ("bot", "GeneratedPlaceholder0123456789ab", False),
        ("bot", json.dumps({"slack_bot_token": "xoxb-real"}), True),
        ("webhook", "https://hooks.slack.com/services/T/B/x", True),
        ("webhook", "xoxb-not-a-webhook", False),
        ("bot", None, False),
    ],
)
def test_aws_outlets_know_a_placeholder(mode: str, secret: str | None, ready: bool) -> None:
    ctx = _Ctx(True, "email")
    ctx.settings = replace(ctx.settings, digest=replace(ctx.settings.digest, slack_mode=delivery_mod.SlackMode(mode)))

    class SM:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            if secret is None:
                raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "m"}}, "GetSecretValue")
            return {"SecretString": secret}

    ctx.client = lambda name: SM()  # type: ignore[method-assign]
    assert AwsOutlets(ctx).slack_configured() is ready


@pytest.mark.parametrize(
    ("secret", "key"),
    [
        (json.dumps({"slack_bot_token": "xoxb-real", "directory_api_key": "k-123"}), "k-123"),
        (json.dumps({"slack_bot_token": "xoxb-real"}), None),
        ("xoxb-plain-token", None),
    ],
)
def test_c4_the_directory_api_key_comes_from_the_slack_secret(secret: str, key: str | None) -> None:
    """Scan cycle 4: the directory lookup's API key sits in the Slack secret's JSON as directory_api_key; a plain
    token has none."""
    ctx = _Ctx(False, "directory")

    class SM:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            return {"SecretString": secret}

    ctx.client = lambda name: SM()  # type: ignore[method-assign]
    outlets = AwsOutlets(ctx)
    assert outlets.slack_ids._api_key == outlets._directory_api_key  # type: ignore[attr-defined]
    assert outlets._directory_api_key() == key


def test_c5_a_directory_api_key_stored_later_is_picked_up_warm() -> None:
    """Scan cycles 5 and 6: the directory lookup's key added later, then rotated, reaches a warm container within five
    minutes; a secret without the key is not read on every lookup, and a failed re-read keeps the value already held."""
    ctx = _Ctx(False, "directory")
    stored = [json.dumps({"slack_bot_token": "xoxb-real"})]
    reads: list[int] = []
    broken = [False]

    class SM:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            reads.append(1)
            if broken[0]:
                raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "GetSecretValue")
            return {"SecretString": stored[0]}

    ctx.client = lambda name: SM()  # type: ignore[method-assign]
    outlets = AwsOutlets(ctx)
    clock = [0.0]
    outlets._secrets._now = lambda: clock[0]
    assert outlets._directory_api_key() is None
    stored[0] = json.dumps({"slack_bot_token": "xoxb-real", "directory_api_key": "k-9"})
    clock[0] = 120.0
    assert outlets._directory_api_key() is None and len(reads) == 1  # no read per lookup while the key is missing
    clock[0] = 301.0
    assert outlets._directory_api_key() == "k-9" and len(reads) == 2
    stored[0] = json.dumps({"slack_bot_token": "xoxb-real", "directory_api_key": "k-10"})  # its owner rotated it
    clock[0] = 602.0
    assert outlets._directory_api_key() == "k-10" and len(reads) == 3
    broken[0] = True
    clock[0] = 903.0
    assert outlets._directory_api_key() == "k-10" and len(reads) == 4  # the read failed: the held value still serves
    with pytest.raises(TransientDeliveryError):  # a fresh read is asked for a reason: its failure is raised
        outlets._secrets.get(ctx.settings.digest.slack_secret_arn, True)


def test_app_context_wires_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    from insights_radar.api.context import AppContext

    settings = Settings.from_env({"DIGEST_SLACK_MODE": "bot", "DIGEST_SLACK_SECRET_ARN": "arn", "AIR_OPS_TABLE": "t"})
    assert settings.digest.slack_channel
    app = AppContext(settings, request_scoped=True)
    assert app.request_scoped is True
    assert type(app.deliveries).__name__ == "DeliveryService"
    assert type(app.delivery_outlets).__name__ == "AwsOutlets"
    assert type(app.delivery_runner).__name__ == "DeliveryRunner"


def test_service_guards(ctx: DigestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    d = ctx.deliveries.create(
        "s",
        "s",
        delivery_mod.Target(delivery_mod.DeliveryKind.EMAIL, "a", address="a@example.com"),
        cadence="daily",
        weekday=None,
        hour=1,
        timezone="UTC",
    )
    with pytest.raises(delivery_mod.ValidationError):
        ctx.deliveries.update("s", d.id, {"enabled": "yes"})
    with pytest.raises(delivery_mod.ValidationError):
        delivery_mod.DeliveryService.check_channels([{"id": CHANNEL, "label": "x"}] * 21)
    # A tick that loses the claim skips the delivery.
    monkeypatch.setattr(ctx.deliveries, "claim", lambda due, now: None)
    assert ctx.delivery_runner.schedule(ctx.sqs, ctx.clock.now() + timedelta(days=2)) == {
        "queued": 0,
        "skipped": 1,
        "failed": 0,
    }


# ---- scan cycle 1 fixes (reviews/c1) ------------------------------------------------------------------------------


def _tariff_module(api: Client) -> None:
    body = {
        "slug": "tariffs",
        "name": "Tariffs",
        "insight_kind": "tariff_item",
        "insight_label": "Tariff changes",
        "grants": ["kg_read", "kg_write_output"],
    }
    assert api.call("POST", "/api/modules", body)[0] == 201


def test_l1_the_profile_name_of_a_custom_list_wins(api: Client, ctx: DigestContext) -> None:
    _tariff_module(api)
    profile = ctx.profile.get()
    lists = {"insight:tariff_item": {"label": "Competitor tariffs", "item": "Tariff"}}
    ctx.profile.put({"lists": lists}, "user:u-admin", profile.version, frozenset())
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    ctx.graph.create(
        Node(id="", type=NodeType.INSIGHT, insight_kind="tariff_item", title="Steel duty up", summary="s"), "u"
    )
    send(ctx, "u-viewer", d["id"])
    body_text = json.dumps(ctx.delivery_outlets.http.posts()[-1]["body"])
    assert "*Competitor tariffs* (1)" in body_text and "Tariff changes" not in body_text


def test_m1_a_hidden_custom_list_does_not_hold_the_cursor_back(api: Client, ctx: DigestContext) -> None:
    _tariff_module(api)
    profile = ctx.profile.get()
    lists = {"insight:tariff_item": {"label": "Tariffs", "item": "Tariff", "visible": False}}
    ctx.profile.put({"lists": lists}, "user:u-admin", profile.version, frozenset())
    _, d = create(api, "viewer", kind="email")
    for i in range(25):
        ctx.graph.create(
            Node(id="", type=NodeType.INSIGHT, insight_kind="tariff_item", title=f"Hidden {i}", summary="s"), "u"
        )
        ctx.clock.advance(timedelta(seconds=1))
    signal(ctx, "Visible after them")
    ctx.clock.advance(timedelta(minutes=1))
    send(ctx, "u-viewer", d["id"])
    sent = json.dumps(ctx.delivery_outlets.ses_client.sent[-1])
    assert "Visible after them" in sent and "Hidden" not in sent
    assert ctx.deliveries.get("u-viewer", d["id"]).cursor == iso(ctx.clock.now())


def test_l3_a_rate_limited_test_send_keeps_its_slot(api: Client, ctx: DigestContext) -> None:
    _, d = create(api, "viewer", kind="email")
    signal(ctx)
    path = f"/api/me/deliveries/{d['id']}/test"

    def refuse(self: Any, *a: Any, **k: Any) -> int:
        raise RateLimitedError("too many requests; wait a minute and try again", {"retry_after": 30})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(RateLimiter, "hit", refuse)
        assert api.call("POST", path, {}, role="viewer")[0] == 429
    s, b, _ = api.call("POST", path, {}, role="viewer")
    assert s == 200 and b["sent"] is True, b  # the refused call did not use up the 10-minute slot
    assert ctx.deliveries.get("u-viewer", d["id"]).test_at == iso(ctx.clock.now())


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_l7_the_app_and_a_delivery_hide_the_same_records(api: Client, ctx: DigestContext, role: str) -> None:
    """The in-app digest and an emailed one use one reader rule (digest.reader_view), so they cannot drift."""
    _, d = create(api, role, kind="email")
    signal(ctx, "Open signal")
    ctx.graph.create(Node(id="", type=NodeType.GAP, title="Private gap", summary="s"), "u")
    ctx.graph.create(Node(id="", type=NodeType.TREND, title="Private trend", summary="s", private=True), "u")
    ctx.clock.advance(timedelta(minutes=1))
    s, app, _ = api.call("GET", "/api/me/digest", role=role)
    assert s == 200
    in_app = {r["title"] for rows in app["new"].values() for r in rows}
    send(ctx, f"u-{role}", d["id"])
    mail = json.dumps(ctx.delivery_outlets.ses_client.sent[-1])
    for title in ("Open signal", "Private gap", "Private trend"):
        assert (title in in_app) is (title in mail), title
    assert ("Private gap" in in_app) is (role == "editor")


# ---- scan cycle 2 fixes (reviews/c2) ------------------------------------------------------------------------------


def test_c2_a_new_account_with_an_old_username_inherits_nothing(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 4: the account is matched by ``sub`` as well as by username."""
    set_channels(api, (CHANNEL, "n"))
    _, ch = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    _, mail = create(api, "viewer", kind="email")
    signal(ctx)
    real = ctx.users.get
    monkeypatch.setattr(ctx.users, "get", lambda name: replace(real(name), subject="another-sub"))
    for did in (ch["id"], mail["id"]):
        send(ctx, "u-viewer", did)
        assert ctx.deliveries.get("u-viewer", did).last_error == "your account no longer exists"
    assert ctx.delivery_outlets.http.posts() == [] and ctx.delivery_outlets.ses_client.sent == []


def test_c2_dm_keys_from_another_resolver_are_not_looked_up(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 3: a DM made under ``email`` is refused with a clear message once the deployment uses ``directory``, and
    one made under ``directory`` is refused under ``email``; neither key reaches the other resolver."""
    _, dm = create(api, "viewer", kind="slack_dm")
    assert ctx.deliveries.get("u-viewer", dm["id"]).address == "viewer@example.com"
    signal(ctx)
    directory = replace(
        ON,
        dm_resolver=delivery_mod.DmResolver.DIRECTORY,
        directory_endpoint="https://directory.example.com/x",
        directory_role_arn="arn:aws:iam::111122223333:role/directory",
    )
    ctx.settings = replace(ctx.settings, digest=directory)
    ctx.delivery_outlets.by_email = False
    send(ctx, "u-viewer", dm["id"])
    stored = ctx.deliveries.get("u-viewer", dm["id"])
    assert stored.last_error is not None and "delete this DM delivery and add it again" in stored.last_error
    assert ctx.delivery_outlets.http.calls == [] and ctx.delivery_outlets.directory.ids == {}
    alias = replace(stored, id="0123456789ab", address="viewer")
    ctx.ops_table.put_item(Item=alias.to_item())
    ctx.settings = replace(ctx.settings, digest=ON)
    ctx.delivery_outlets.by_email = True
    send(ctx, "u-viewer", alias.id)
    assert "add it again" in str(ctx.deliveries.get("u-viewer", alias.id).last_error)
    assert ctx.delivery_outlets.http.calls == []


def test_c2_a_failed_cursor_write_after_a_send_is_not_resent(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 6: Slack took the digest, then the cursor write fails: retried, then left, never redelivered."""
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    signal(ctx)
    real = ctx.deliveries.advance
    calls: list[int] = []

    def flaky(*a: Any, **k: Any) -> bool:
        calls.append(1)
        if len(calls) < 3:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "m"}}, "U")
        return real(*a, **k)

    monkeypatch.setattr(ctx.deliveries, "advance", flaky)
    assert send(ctx, "u-viewer", d["id"]) == {"batchItemFailures": []}
    assert len(calls) == 3 and ctx.deliveries.get("u-viewer", d["id"]).cursor  # moved on the third try
    assert len(ctx.delivery_outlets.http.posts()) == 1

    def down(*a: Any, **k: Any) -> bool:
        raise ClientError({"Error": {"Code": "InternalServerError", "Message": "m"}}, "U")

    ctx.clock.advance(timedelta(minutes=1))
    signal(ctx, "Second")
    monkeypatch.setattr(ctx.deliveries, "advance", down)
    assert send(ctx, "u-viewer", d["id"]) == {"batchItemFailures": []}  # logged and left, not retried
    assert len(ctx.delivery_outlets.http.posts()) == 2


def test_c2_a_row_that_cannot_be_claimed_moves_out_of_the_way(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 7: a due row whose claim hits a store fault moves back an hour, so the rows behind it are queued."""
    _, good = create(api, "viewer", kind="email")
    _, bad = create(api, "editor", kind="email")  # u-editor sorts first at the same time
    ctx.clock.advance(timedelta(days=1))
    real = ctx.deliveries.claim

    def claim(due: DueDelivery, now: datetime) -> str | None:
        if due.id == bad["id"]:
            raise ClientError(
                {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "m"}}, "UpdateItem"
            )
        return real(due, now)

    monkeypatch.setattr(ctx.deliveries, "claim", claim)
    # One row is read per tick, as at the head of a full batch: the bad one comes first.
    monkeypatch.setattr(delivery_mod, "DUE_BATCH", 1)
    real_due = ctx.deliveries.due
    monkeypatch.setattr(ctx.deliveries, "due", lambda now: real_due(now, limit=1))
    assert workers.digest_schedule({}, None, ctx) == {"queued": 0, "skipped": 0, "failed": 1}
    moved = ctx.deliveries.get("u-editor", bad["id"]).next_at
    assert moved == iso(ctx.clock.now() + delivery_mod.CLAIM_RETRY)
    assert workers.digest_schedule({}, None, ctx) == {"queued": 1, "skipped": 0, "failed": 0}
    assert json.loads(ctx.sqs.messages[0]["MessageBody"])["id"] == good["id"]
    # A defer that fails too is logged; the tick still finishes.
    ctx.clock.advance(timedelta(hours=2))
    monkeypatch.setattr(ctx.deliveries, "defer", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert workers.digest_schedule({}, None, ctx)["failed"] == 1


def test_c2_permanent_failures_switch_a_delivery_off_after_three(api: Client, ctx: DigestContext) -> None:
    """Finding 7: three of the owner's own permanent failures in a row (here: an admin took the channel off the
    list) switch it off with the reason; a success or an edit resets the count, and a transient failure does not
    count."""
    set_channels(api, (CHANNEL, "n"), ("C2222222222", "m"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    signal(ctx)
    http = ctx.delivery_outlets.http

    def removed() -> None:
        set_channels(api, ("C2222222222", "m"))
        send(ctx, "u-viewer", d["id"])

    removed()
    removed()
    set_channels(api, (CHANNEL, "n"), ("C2222222222", "m"))
    http.replies = [HttpResponse(500, {}, b"")]
    send(ctx, "u-viewer", d["id"])  # transient: not counted
    send(ctx, "u-viewer", d["id"])  # works: the count starts again
    assert ctx.deliveries.get("u-viewer", d["id"]).last_status is DeliveryStatus.OK
    for _ in range(2):
        ctx.clock.advance(timedelta(minutes=1))
        signal(ctx)
        removed()
    assert ctx.deliveries.get("u-viewer", d["id"]).enabled is True
    assert api.call("PATCH", f"/api/me/deliveries/{d['id']}", {"hour": 10}, role="viewer")[0] == 200  # resets
    for _ in range(3):
        removed()  # the records of the two failures above are still unsent
    _, listed, _ = api.call("GET", "/api/me/deliveries", role="viewer")
    (item,) = listed["items"]
    assert item["enabled"] is False and item["next_at"] is None
    assert item["last_error"] == (
        "switched off after 3 failed sends in a row: an administrator removed this channel from the list"
    )
    assert workers.digest_schedule({}, None, ctx)["queued"] == 0
    set_channels(api, (CHANNEL, "n"))
    assert api.call("PATCH", f"/api/me/deliveries/{d['id']}", {"enabled": True}, role="viewer")[0] == 200
    assert ctx.deliveries.get("u-viewer", d["id"]).due


def test_c2_fail_on_a_deleted_delivery_and_other_errors(ctx: DigestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ctx.deliveries.fail("nobody", "0123456789ab", "x") is False

    def throttled(**k: Any) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "m"}}, "U")

    monkeypatch.setattr(ctx.deliveries._t, "update_item", throttled)
    with pytest.raises(ClientError):
        ctx.deliveries.fail("nobody", "0123456789ab", "x")


def test_c2_one_delivery_per_person_per_channel(api: Client, ctx: DigestContext) -> None:
    """Finding 10: a second delivery by the same person to the same channel is refused; another channel, or
    another person, is fine."""
    set_channels(api, (CHANNEL, "n"), ("C2222222222", "m"))
    assert create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)[0] == 201
    s, b = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL, hour=17)
    assert s == 409 and "already have a delivery to this channel" in b["message"]
    assert create(api, "viewer", kind="slack_channel", channel_id="C2222222222")[0] == 201
    assert create(api, "editor", kind="slack_channel", channel_id=CHANNEL)[0] == 201
    assert create(api, "viewer", kind="email")[0] == 201 and create(api, "viewer", kind="email")[0] == 201


def test_c2_a_slack_token_stored_later_is_picked_up_warm() -> None:
    """Finding 1: the placeholder is not kept for the container's life: once a minute it is read again, so the
    real token is used without a cold start; a ready token is not read again."""
    ctx = _Ctx(True, "email")
    secret = {"value": "GeneratedPlaceholder0123456789ab"}
    reads: list[str] = []

    class SM:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            reads.append(SecretId)
            return {"SecretString": secret["value"]}

    ctx.client = lambda name: SM()  # type: ignore[method-assign]
    outlets = AwsOutlets(ctx)
    clock = [1000.0]
    outlets._secrets._now = lambda: clock[0]
    assert outlets.slack_configured() is False
    secret["value"] = json.dumps({"slack_bot_token": "xoxb-real"})
    clock[0] += 30
    assert outlets.slack_configured() is False and len(reads) == 1  # not yet: at most once a minute
    clock[0] += 31
    assert outlets.slack_configured() is True and len(reads) == 2
    clock[0] += 299
    assert outlets.slack_configured() is True and len(reads) == 2  # a real token stays cached ...
    clock[0] += 3600
    assert outlets.slack_configured() is True and len(reads) == 3  # ... for five minutes (scan cycle 6)


def test_c2_one_send_reads_the_module_list_once(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader's view and the lists share one module read per send; a later send reads it again."""
    from insights_radar.insights import delivery_runner

    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    reads: list[int] = []
    real = ctx.modules.list_all
    monkeypatch.setattr(ctx.modules, "list_all", lambda: reads.append(1) or real())
    now = [100.0]
    monkeypatch.setattr(delivery_runner.time, "monotonic", lambda: now[0])
    signal(ctx)
    send(ctx, "u-viewer", d["id"])
    assert len(reads) == 1 and len(ctx.delivery_outlets.http.posts()) == 1
    now[0] += delivery_runner.MODULES_MEMO_SECONDS + 1
    ctx.clock.advance(timedelta(minutes=1))
    signal(ctx, "Later")
    send(ctx, "u-viewer", d["id"])
    assert len(reads) == 2


# ---- scan cycle 3 ------------------------------------------------------------------------------------------------


def _send_body(ctx: DigestContext, body: dict[str, Any], message_id: str, context: Any = None) -> dict[str, Any]:
    event = {
        "Records": [
            {
                "messageId": message_id,
                "body": json.dumps(body),
                "receiptHandle": "rh",
                "attributes": {"ApproximateReceiveCount": "1"},
            }
        ]
    }
    return workers.digest_send(event, context, ctx)


def test_c3_a_deployment_fault_switches_nobody_off(api: Client, ctx: DigestContext) -> None:
    """Finding 1: a fault that is the deployment's (the bot not in the channel, a revoked token) is shown but never
    counted, so it switches nobody off and sends resume once the operator fixes it."""
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    signal(ctx)
    http = ctx.delivery_outlets.http
    for code in ("not_in_channel", "invalid_auth", "not_in_channel", "missing_scope"):
        http.replies = [HttpResponse(200, {}, json.dumps({"ok": False, "error": code}).encode())] * 2
        assert send(ctx, "u-viewer", d["id"]) == {"batchItemFailures": []}
    got = ctx.deliveries.get("u-viewer", d["id"])
    assert got.enabled is True and got.due and got.last_status is DeliveryStatus.ERROR
    assert got.last_error == "Slack: missing_scope"
    http.replies = []
    send(ctx, "u-viewer", d["id"])  # fixed: it works again, with nothing to turn back on
    assert ctx.deliveries.get("u-viewer", d["id"]).last_status is DeliveryStatus.OK


def test_c3_a_redriven_message_is_still_sent(api: Client, ctx: DigestContext) -> None:
    """Finding 2: the duplicate check reads the claim from the message body, so a message redriven from the
    dead-letter queue under a new message id is sent, and only once."""
    _, d = create(api, "viewer", kind="slack_dm")
    signal(ctx)
    body = {"subject": "u-viewer", "id": d["id"], "due": "2026-10-03T08:00:00.000Z", "claim": "c" * 32}
    http = ctx.delivery_outlets.http
    http.replies = [HttpResponse(500, {}, b"")]
    assert _send_body(ctx, body, "m1")["batchItemFailures"] == [{"itemIdentifier": "m1"}]
    before = len(http.posts())
    assert _send_body(ctx, body, "m-redriven") == {"batchItemFailures": []}  # the DLQ redrive
    assert len(http.posts()) > before
    sent = len(http.posts())
    assert _send_body(ctx, body, "m-again") == {"batchItemFailures": []}
    assert len(http.posts()) == sent  # the same claim twice: one send


class _ShortRun:
    def get_remaining_time_in_millis(self) -> int:
        return 1_000


def test_c3_a_send_that_would_run_out_of_time_waits_for_a_fresh_run(api: Client, ctx: DigestContext) -> None:
    """Finding 4: with too little of the sender's run left, nothing is sent and the message is retried."""
    _, d = create(api, "viewer", kind="slack_dm")
    signal(ctx)
    body = {"subject": "u-viewer", "id": d["id"], "due": "2026-10-03T08:00:00.000Z", "claim": "d" * 32}
    out = _send_body(ctx, body, "m1", _ShortRun())
    assert out["batchItemFailures"] == [{"itemIdentifier": "m1"}]
    assert ctx.delivery_outlets.http.posts() == []
    assert _send_body(ctx, body, "m1", object()) == {"batchItemFailures": []}  # no clock on the context: sent
    assert ctx.delivery_outlets.http.posts()


def test_c3_a_schedule_that_cannot_be_worked_out_is_shown_not_counted(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """c3 finding 7, revised in c4: a schedule that cannot be worked out (most often a zone a tzdata update dropped)
    is shown to the owner but never counted, since the cause may be the deployment's; and when the owner fixed the
    row first (the defer is refused), no stale error is written."""
    _, d = create(api, "viewer", kind="email")
    ctx.clock.advance(timedelta(days=1))

    def claim(due: DueDelivery, now: datetime) -> str | None:
        raise ValidationError("unknown time zone: Mars/Olympus")

    monkeypatch.setattr(ctx.deliveries, "claim", claim)
    for _ in range(4):
        assert workers.digest_schedule({}, None, ctx)["failed"] == 1
        # c5: it moves a day back, not an hour, so the same error is not logged every hour.
        assert ctx.deliveries.get("u-viewer", d["id"]).next_at == iso(ctx.clock.now() + delivery_mod.SCHEDULE_RETRY)
        ctx.clock.advance(timedelta(hours=2))
        assert workers.digest_schedule({}, None, ctx)["failed"] == 0  # not retried an hour later
        ctx.clock.advance(timedelta(days=1))
    got = ctx.deliveries.get("u-viewer", d["id"])
    assert got.enabled is True and got.last_status is DeliveryStatus.ERROR
    assert got.last_error == "its schedule cannot be worked out: unknown time zone: Mars/Olympus"
    monkeypatch.setattr(ctx.deliveries, "defer", lambda *a: False)  # the owner's edit won the race
    written: list[Any] = []
    monkeypatch.setattr(ctx.deliveries, "record", lambda *a, **k: written.append(a))
    ctx.clock.advance(timedelta(days=1))
    assert workers.digest_schedule({}, None, ctx)["failed"] == 1
    assert written == []  # nothing written over the row the owner just fixed


def test_c6_a_delivery_whose_zone_is_gone_can_still_be_paused(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """c6 finding 7: switching off alone never works the schedule out, so a zone tzdata dropped does not stop the
    owner pausing it; any other change still needs a zone that works."""
    _, d = create(api, "viewer", kind="email")
    before = ctx.deliveries.get("u-viewer", d["id"]).next_at

    def bad(*a: Any) -> Any:
        raise ValidationError("unknown time zone: Mars/Olympus")

    monkeypatch.setattr(ctx.deliveries, "check_schedule", bad)
    with pytest.raises(ValidationError):
        ctx.deliveries.update("u-viewer", d["id"], {"enabled": False, "hour": (d["hour"] + 1) % 24})
    with pytest.raises(ValidationError):
        ctx.deliveries.update("u-viewer", d["id"], {"enabled": True})
    got = ctx.deliveries.update("u-viewer", d["id"], {"enabled": False})
    assert got.enabled is False and got.next_at == before
    assert ctx.deliveries.get("u-viewer", d["id"]).enabled is False
    # c7: a body that repeats the unchanged schedule pauses too.
    cur = ctx.deliveries.get("u-viewer", d["id"])
    full = {
        "enabled": False,
        "cadence": cur.cadence.value,
        "weekday": cur.weekday,
        "hour": cur.hour,
        "timezone": cur.timezone,
    }
    assert ctx.deliveries.update("u-viewer", d["id"], full).enabled is False


def test_c3_two_creates_for_one_channel_at_once_keep_one(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 8: when two creates for the same channel race past the first check, the consistent read after the
    write keeps the first and refuses the second."""
    set_channels(api, (CHANNEL, "n"))
    assert create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)[0] == 201
    real = ctx.deliveries.for_user

    def stale(subject: str, consistent: bool = False) -> Any:
        return real(subject, consistent=True) if consistent else []

    monkeypatch.setattr(ctx.deliveries, "for_user", stale)
    ctx.clock.advance(timedelta(seconds=1))  # the first create ranks ahead: it is older
    s, b = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL, hour=17)
    assert s == 409 and "already have a delivery to this channel" in b["message"]
    assert len(real("u-viewer")) == 1


def test_c17_a_test_send_stopped_mid_build_is_still_audited(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, d = create(api, "viewer", kind="email")
    signal(ctx)
    rows: list[tuple[str, str, Any]] = []

    def record(p: Any, action: str, target: str, details: Any = None) -> None:
        rows.append((action, target, details))

    monkeypatch.setattr(ctx.audit, "record", record)
    real = DeliveryRunner.prepare  # ctx.delivery_runner is built per call, so the class is patched

    def complain_while_building(self: DeliveryRunner, dd: Any) -> Any:
        p = real(self, dd)
        ctx.deliveries.switch_off("u-viewer", d["id"], suppress=True, reason="the digest was reported as spam")
        return p

    monkeypatch.setattr(DeliveryRunner, "prepare", complain_while_building)
    status, out, *_ = api.call("POST", f"/api/me/deliveries/{d['id']}/test", {}, role="viewer")
    assert status == 409 and out["details"] == {"code": "suppressed"}, out
    assert ("delivery.test", d["id"], {"sent": False, "total": 1, "suppressed": True}) in rows  # c18: its size


def test_c17_a_send_test_now_reads_the_row_once_before_the_build(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, d = create(api, "viewer", kind="email")
    reads: list[str] = []
    real = ctx.deliveries.get
    monkeypatch.setattr(ctx.deliveries, "get", lambda s, did, *a, **k: reads.append(did) or real(s, did, *a, **k))
    assert api.call("POST", f"/api/me/deliveries/{d['id']}/test", {}, role="viewer")[0] == 200
    assert reads == [d["id"]]  # the route's read; take_test reuses it (scan cycle 17)
    # Without a row from the caller it still reads one itself.
    ctx.clock.advance(timedelta(seconds=delivery_mod.TEST_INTERVAL_SECONDS + 1))
    assert ctx.deliveries.take_test("u-viewer", d["id"]).id == d["id"] and len(reads) == 2


def test_c17_a_delivery_deleted_mid_send_is_logged_as_deleted_not_as_a_race(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    signal(ctx)
    real = ctx.deliveries.advance

    def deleted_first(dd: Any, cursor: str, status: DeliveryStatus, **k: Any) -> bool:
        ctx.deliveries.delete(dd.subject, dd.id)
        return real(dd, cursor, status, **k)

    monkeypatch.setattr(ctx.deliveries, "advance", deleted_first)
    with caplog.at_level("WARNING"):
        ctx.delivery_runner.deliver("u-viewer", d["id"])
    events = [r.getMessage() for r in caplog.records]
    assert any('"digest.deleted"' in e for e in events)
    assert not any('"digest.cursor_race"' in e for e in events)


def test_c18_a_test_send_that_fails_any_other_way_is_still_audited(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, d = create(api, "viewer", kind="email")
    signal(ctx)
    rows: list[tuple[str, Any]] = []

    def record(p: Any, action: str, target: str, details: Any = None) -> None:
        rows.append((action, details))

    def broken(self: DeliveryRunner, dd: Any) -> Any:
        raise RuntimeError("the graph table is down")

    monkeypatch.setattr(ctx.audit, "record", record)
    monkeypatch.setattr(DeliveryRunner, "prepare", broken)
    assert api.call("POST", f"/api/me/deliveries/{d['id']}/test", {}, role="viewer")[0] == 500
    assert ("delivery.test", {"sent": False, "total": 0, "failed": True}) in rows


def test_c18_no_read_after_the_send_can_fail_the_message(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A lost cursor race is told from the failed write itself: a broken read after the send cannot requeue it."""
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    signal(ctx)
    real = ctx.deliveries.advance

    def raced(dd: Any, cursor: str, status: DeliveryStatus, **k: Any) -> Any:
        assert real(dd, "2026-10-02T07:59:00.000Z", DeliveryStatus.OK)  # another sender first
        monkeypatch.setattr(ctx.deliveries, "find", lambda *a: (_ for _ in ()).throw(RuntimeError("throttled")))
        return real(dd, cursor, status, **k)

    monkeypatch.setattr(ctx.deliveries, "advance", raced)
    with caplog.at_level("WARNING"):
        assert ctx.delivery_runner.deliver("u-viewer", d["id"]) == "ok"
    assert any('"digest.cursor_race"' in r.getMessage() for r in caplog.records)


def test_c18_advance_says_none_for_a_deleted_delivery_and_writes_nothing(api: Client, ctx: DigestContext) -> None:
    _, d = create(api, "viewer", kind="email")
    stored = ctx.deliveries.get("u-viewer", d["id"])
    ctx.deliveries.delete("u-viewer", d["id"])
    for status in (DeliveryStatus.OK, DeliveryStatus.EMPTY):
        assert ctx.deliveries.advance(stored, "c", status) is None
    assert ctx.deliveries.find("u-viewer", d["id"]) is None


def test_c19_a_retry_after_a_lost_answer_recognises_its_own_write(
    api: Client, ctx: DigestContext, caplog: pytest.LogCaptureFixture
) -> None:
    set_channels(api, (CHANNEL, "n"))
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    before = ctx.deliveries.get("u-viewer", d["id"])
    cursor = "2026-10-02T07:59:00.000Z"
    assert ctx.deliveries.advance(before, cursor, DeliveryStatus.OK, token=LEASE_1) is True  # committed
    # The same send again (its answer was lost): its own token is there, so it moved (c20: by token).
    assert ctx.deliveries.advance(before, cursor, DeliveryStatus.OK, token=LEASE_1) is True
    # Another sender with the same cut cursor and outcome is still a race (c20 #3).
    assert ctx.deliveries.advance(before, cursor, DeliveryStatus.OK, token=LEASE_2) is False
    # A bounce that rewrote the status meanwhile does not hide our own write (c20 #4).
    ctx.deliveries.switch_off("u-viewer", d["id"], suppress=True, reason="the address bounced")
    assert ctx.deliveries.advance(before, cursor, DeliveryStatus.OK, token=LEASE_1) is True
    assert ctx.deliveries.get("u-viewer", d["id"]).last_status is DeliveryStatus.SUPPRESSED


def test_c19_a_test_that_fails_after_the_build_is_audited_with_its_size(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, d = create(api, "viewer", kind="email")
    signal(ctx)
    rows: list[Any] = []

    def record(p: Any, action: str, target: str, details: Any = None) -> None:
        rows.append((action, details))

    def throttled(self: DeliveryRunner, p: Any) -> None:
        raise RuntimeError("throttled")

    monkeypatch.setattr(ctx.audit, "record", record)
    monkeypatch.setattr(DeliveryRunner, "dispatch", throttled)
    assert api.call("POST", f"/api/me/deliveries/{d['id']}/test", {}, role="viewer")[0] == 500
    assert ("delivery.test", {"sent": False, "total": 1, "failed": True}) in rows


def test_c20_a_throttle_during_a_test_send_stays_a_503_and_is_audited(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insights_radar.core.errors import ServiceUnavailableError

    _, d = create(api, "viewer", kind="email")
    signal(ctx)
    rows: list[Any] = []

    def record(p: Any, action: str, target: str, details: Any = None) -> None:
        rows.append((action, details))

    def busy(self: DeliveryRunner, p: Any) -> None:
        raise ServiceUnavailableError("the graph is busy; retry shortly")

    monkeypatch.setattr(ctx.audit, "record", record)
    monkeypatch.setattr(DeliveryRunner, "dispatch", busy)
    assert api.call("POST", f"/api/me/deliveries/{d['id']}/test", {}, role="viewer")[0] == 503
    assert ("delivery.test", {"sent": False, "total": 1, "failed": True}) in rows


def test_c20_a_lost_answer_on_the_owner_edit_path_is_its_own_write(api: Client, ctx: DigestContext) -> None:
    _, d = create(api, "viewer", kind="email")
    ctx.deliveries.fail("u-viewer", d["id"], "your account is disabled")
    stale = ctx.deliveries.get("u-viewer", d["id"])
    ctx.deliveries.update("u-viewer", d["id"], {"hour": 3})  # clears fails: the recursion writes "empty"
    assert ctx.deliveries.advance(stale, "c1", DeliveryStatus.EMPTY, token=LEASE_1) is True
    # Its answer was lost: the retry from the same stale row sees its own token, not a race.
    assert ctx.deliveries.advance(stale, "c1", DeliveryStatus.EMPTY, token=LEASE_1) is True


def test_c21_a_boto_throttle_before_the_send_is_a_503_and_gives_the_slot_back(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, d = create(api, "viewer", kind="email")
    signal(ctx)
    path = f"/api/me/deliveries/{d['id']}/test"
    rows: list[Any] = []

    def record(p: Any, action: str, target: str, details: Any = None) -> None:
        rows.append((action, details))

    def throttled(*a: Any, **k: Any) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "m"}}, "GetItem")

    real_find = ctx.deliveries.find
    monkeypatch.setattr(ctx.audit, "record", record)
    monkeypatch.setattr(ctx.deliveries, "find", throttled)  # the post-build re-read, before the send
    assert api.call("POST", path, {}, role="viewer")[0] == 503  # the real ClientError path in app.py
    assert ("delivery.test", {"sent": False, "total": 1, "failed": True}) in rows
    assert ctx.deliveries.get("u-viewer", d["id"]).test_at is None  # nothing went out: the slot went back
    monkeypatch.setattr(ctx.deliveries, "find", real_find)
    assert api.call("POST", path, {}, role="viewer")[0] == 200  # the retry the 503 asked for is allowed
    # c23: an error during the send keeps the slot (it may have gone out).
    monkeypatch.setattr(DeliveryRunner, "dispatch", lambda self, p: (_ for _ in ()).throw(RuntimeError("x")))
    ctx.clock.advance(timedelta(seconds=delivery_mod.TEST_INTERVAL_SECONDS + 1))
    signal(ctx, "Another")
    assert api.call("POST", path, {}, role="viewer")[0] == 500
    assert ctx.deliveries.get("u-viewer", d["id"]).test_at is not None


def test_c21_giving_back_a_test_slot_keeps_a_later_stamp(api: Client, ctx: DigestContext) -> None:
    _, d = create(api, "viewer", kind="email")
    first = ctx.deliveries.take_test("u-viewer", d["id"]).test_at
    ctx.clock.advance(timedelta(seconds=delivery_mod.TEST_INTERVAL_SECONDS + 1))
    later = ctx.deliveries.take_test("u-viewer", d["id"]).test_at
    ctx.deliveries.give_back_test("u-viewer", d["id"], first)  # not ours any more: kept
    assert ctx.deliveries.get("u-viewer", d["id"]).test_at == later
    ctx.deliveries.give_back_test("u-viewer", d["id"], None)  # nothing to give back
    ctx.deliveries.delete("u-viewer", d["id"])
    ctx.deliveries.give_back_test("u-viewer", d["id"], later)  # a deleted row is left alone


def test_c23_the_slot_comes_back_only_when_nothing_can_have_gone_out(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from botocore.exceptions import ReadTimeoutError

    _, d = create(api, "viewer", kind="email")
    path = f"/api/me/deliveries/{d['id']}/test"
    later = timedelta(seconds=delivery_mod.TEST_INTERVAL_SECONDS + 1)
    # Nothing new is an answer: it keeps the slot.
    assert api.call("POST", path, {}, role="viewer")[1] == {"sent": False, "total": 0}
    assert api.call("POST", path, {}, role="viewer")[0] == 429
    ctx.clock.advance(later)
    signal(ctx)
    real_dispatch = DeliveryRunner.dispatch

    def ses_busy(self: DeliveryRunner, p: Any) -> None:
        raise TransientDeliveryError("TooManyRequestsException", "SES: TooManyRequestsException")

    # A send-side error (a throttle here) keeps the slot: the limit also guards the shared rate limits.
    monkeypatch.setattr(DeliveryRunner, "dispatch", ses_busy)
    status, body, *_ = api.call("POST", path, {}, role="viewer")
    assert status == 200 and body["sent"] is False and body["error"]
    assert api.call("POST", path, {}, role="viewer")[0] == 429
    ctx.clock.advance(later)

    def timed_out(*a: Any, **k: Any) -> Any:
        raise ReadTimeoutError(endpoint_url="https://dynamodb")

    # A timeout during the send keeps it: the mail may have gone out (c23 medium).
    monkeypatch.setattr(DeliveryRunner, "dispatch", timed_out)
    assert api.call("POST", path, {}, role="viewer")[0] == 500
    assert ctx.deliveries.get("u-viewer", d["id"]).test_at is not None
    ctx.clock.advance(later)
    # A timeout reading the store before the send gives it back: nothing can have gone out.
    monkeypatch.setattr(DeliveryRunner, "dispatch", real_dispatch)
    real_find = ctx.deliveries.find
    monkeypatch.setattr(ctx.deliveries, "find", timed_out)
    assert api.call("POST", path, {}, role="viewer")[0] == 500
    assert ctx.deliveries.get("u-viewer", d["id"]).test_at is None
    # A test that went out keeps it.
    monkeypatch.setattr(ctx.deliveries, "find", real_find)
    assert api.call("POST", path, {}, role="viewer")[1]["sent"] is True
    assert api.call("POST", path, {}, role="viewer")[0] == 429


def test_c23_a_channel_label_typed_with_a_hash_is_not_shown_twice(api: Client, ctx: DigestContext) -> None:
    set_channels(api, (CHANNEL, "#market-news"))
    assert [c.label for c in ctx.deliveries.channels()] == ["market-news"]


def test_c25_a_send_side_error_is_shown_as_the_provider_gave_it(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, d = create(api, "viewer", kind="email")
    signal(ctx)

    def timed_out(self: DeliveryRunner, p: Any) -> None:
        raise TransientDeliveryError("network", "SES: ReadTimeoutError")

    monkeypatch.setattr(DeliveryRunner, "dispatch", timed_out)
    body = api.call("POST", f"/api/me/deliveries/{d['id']}/test", {}, role="viewer")[1]
    # c25: shown as SES gave it; whether it arrived cannot be known here (accepted, reviews/c25).
    assert body == {"sent": False, "total": 1, "error": "SES: ReadTimeoutError"}


def test_c24_channel_labels_lose_a_typed_hash_and_hash_alone_is_refused(api: Client, ctx: DigestContext) -> None:
    from insights_radar.insights.delivery import CHANNELS_KEY

    ctx.ops_table.put_item(Item={**CHANNELS_KEY, "channels": [{"id": CHANNEL, "label": "#old-name"}]})
    assert [c.label for c in ctx.deliveries.channels()] == ["old-name"]  # stored before c23
    status, body, *_ = api.call(
        "PUT", "/api/admin/digest/channels", {"channels": [{"id": CHANNEL, "label": "#"}]}, role="admin"
    )
    assert status == 400 and "letter or digit" in body["message"]  # c27
    for label in ("# #", " #  # ", "#\u200b", "\ufeff", "#\u2060\u00ad"):  # c25, c26: spaces or invisible characters
        status, body, *_ = api.call(
            "PUT", "/api/admin/digest/channels", {"channels": [{"id": CHANNEL, "label": label}]}, role="admin"
        )
        assert status == 400
    set_channels(api, (CHANNEL, "# #news"))
    assert [c.label for c in ctx.deliveries.channels()] == ["news"]


def test_c27_a_channel_label_is_normalised_once_and_stored_that_way(api: Client, ctx: DigestContext) -> None:
    from insights_radar.insights.delivery import CHANNELS_KEY

    # Invisible characters anywhere, before or between the hashes, are removed first (c27 #1, #2, #9).
    for label in ("#\u200b#", "\u200b#", "#\u2060#\u00ad"):
        status, *_ = api.call(
            "PUT", "/api/admin/digest/channels", {"channels": [{"id": CHANNEL, "label": label}]}, role="admin"
        )
        assert status == 400, label
    set_channels(api, (CHANNEL, "\u200b#\u202emarket  news"))
    assert [c.label for c in ctx.deliveries.channels()] == ["market news"]  # bidi override and double space gone
    # A stored label with nothing readable falls back to the channel ID when read (c27 #5).
    ctx.ops_table.put_item(Item={**CHANNELS_KEY, "channels": [{"id": CHANNEL, "label": "#\ufeff"}]})
    assert [c.label for c in ctx.deliveries.channels()] == [CHANNEL]


def test_c28_the_limit_applies_to_the_stored_name_and_every_hash_form_goes(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def put(label: str) -> int:
        return int(
            api.call(
                "PUT", "/api/admin/digest/channels", {"channels": [{"id": CHANNEL, "label": label}]}, role="admin"
            )[0]
        )

    assert put("\ufb2c" * 80) == 400  # 240 characters once normalised (c28 #1)
    assert put("e\u0301" * 41) == 200  # 82 typed, 41 stored
    for label, stored in (("\uff03news", "news"), ("\ufe5f\ufe5fnews", "news"), ("#\u0301news", "news")):
        assert put(label) == 200
        assert [c.label for c in ctx.deliveries.channels()] == [stored], label
    # A rename is audited with the names (c28 #4).
    rows: list[Any] = []

    def record(p: Any, action: str, target: str, details: Any = None) -> None:
        rows.append((action, details))

    monkeypatch.setattr(ctx.audit, "record", record)
    assert put("all-hands") == 200
    assert ("digest.channels", {"added": [], "removed": [], "renamed": [CHANNEL]}) in rows  # c30: IDs only
    assert ("digest.channel_renamed", {"channel": CHANNEL, "from": "news", "to": "all-hands"}) in rows  # own row


def test_c29_channel_changes_are_audited_as_lists_that_fit(api: Client, ctx: DigestContext) -> None:
    from insights_radar.api.routes import channel_changes

    before = {f"C{i:010d}": "x" * 42 for i in range(20)}
    after = {f"C{i:010d}": "y" * 42 for i in range(5, 25)}
    got = channel_changes(before, after)
    assert len(got["added"]) == 5 and len(got["removed"]) == 5 and len(got["renamed"]) == 15
    assert all(r.startswith("C") and len(r) == 11 for r in got["renamed"])  # c30: IDs only


def test_c29_hash_lookalikes_go_duplicate_names_are_refused_and_the_list_shows_current_names(
    api: Client, ctx: DigestContext
) -> None:
    set_channels(api, (CHANNEL, "\u266fnews"))
    assert [c.label for c in ctx.deliveries.channels()] == ["news"]  # the music sharp sign is a '#' too
    other = "C9999999999"
    status, body, *_ = api.call(
        "PUT",
        "/api/admin/digest/channels",
        {"channels": [{"id": CHANNEL, "label": "news"}, {"id": other, "label": "#NEWS"}]},
        role="admin",
    )
    assert status == 400 and "reads the same as channel 1" in body["message"]  # c32 wording
    _, d = create(api, "viewer", kind="slack_channel", channel_id=CHANNEL)
    set_channels(api, (CHANNEL, "all-hands"))
    items = api.call("GET", "/api/me/deliveries", role="viewer")[1]["items"]
    assert [i["target_label"] for i in items if i["id"] == d["id"]] == ["#all-hands"]


def test_c30_renaming_twenty_cjk_channels_keeps_every_audit_row_whole(api: Client, ctx: DigestContext) -> None:
    from insights_radar.audit.log import _details_json, _split

    ids = [f"C{i:010d}" for i in range(20)]
    api.call(
        "PUT",
        "/api/admin/digest/channels",
        {"channels": [{"id": c, "label": "\u65b0\u805e" * 7 + str(n)} for n, c in enumerate(ids)]},
        role="admin",
    )
    rows: list[Any] = []

    def record(p: Any, action: str, target: str, details: Any = None) -> None:
        rows.append((action, details))

    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    mp.setattr(ctx.audit, "record", record)
    try:
        status = api.call(
            "PUT",
            "/api/admin/digest/channels",
            {"channels": [{"id": c, "label": "\u65b0\u805e" * 7 + str(n) + "\u65b0"} for n, c in enumerate(ids)]},
            role="admin",
        )[0]
    finally:
        mp.undo()
    assert status == 200
    assert [a for a, _ in rows].count("digest.channel_renamed") == 20
    for _, details in rows:  # no row, main or continued, collapses to the truncated marker
        for part in _split(details):
            assert "truncated" not in _details_json(part)


def test_c30_lookalike_names_clash_rows_are_numbered_from_one_and_stored_clashes_show_the_id(
    api: Client, ctx: DigestContext
) -> None:
    from insights_radar.insights.delivery import CHANNELS_KEY

    other = "C9999999999"
    status, body, *_ = api.call(
        "PUT",
        "/api/admin/digest/channels",
        {"channels": [{"id": CHANNEL, "label": "finance"}, {"id": other, "label": "\ufb01nance"}]},
        role="admin",
    )
    assert status == 400 and body["message"].startswith("channel 2:")  # the second row, as the form numbers it
    ctx.ops_table.put_item(
        Item={**CHANNELS_KEY, "channels": [{"id": CHANNEL, "label": "news"}, {"id": other, "label": "\u266fnews"}]}
    )
    assert [c.label for c in ctx.deliveries.channels()] == ["news", f"news ({other})"]  # saved before c29
    got = api.call("GET", "/api/admin/digest/channels", role="admin")[1]  # c31: the admin sees the stored names
    assert got["channels"] == [{"id": CHANNEL, "label": "news"}, {"id": other, "label": "news"}]
    assert got["clashes"] == [[CHANNEL, other]]


def test_c31_a_stale_version_is_refused_and_the_audit_diffs_against_what_was_replaced(
    api: Client, ctx: DigestContext
) -> None:
    other = "C9999999999"
    url = "/api/admin/digest/channels"
    first = api.call("PUT", url, {"channels": [{"id": CHANNEL, "label": "news"}], "version": 0}, role="admin")
    assert first[0] == 200 and first[1]["version"] == 1
    # Admin A and admin B both load version 1.
    assert api.call("GET", url, role="admin")[1]["version"] == 1
    a = api.call(
        "PUT",
        url,
        {"channels": [{"id": CHANNEL, "label": "news"}, {"id": other, "label": "ops"}], "version": 1},
        role="admin",
    )
    assert a[0] == 200 and a[1]["version"] == 2
    status, body, *_ = api.call(
        "PUT", url, {"channels": [{"id": CHANNEL, "label": "renamed"}], "version": 1}, role="admin"
    )
    assert status == 409 and body["details"]["code"] == "channels_changed"
    assert [c.id for c in ctx.deliveries.channels()] == [CHANNEL, other]  # A's channel survived
    assert api.call("PUT", url, {"channels": [], "version": 0}, role="admin")[0] == 409  # 0 once a list exists
    for bad in (-1, "2", True, 1.5):
        assert api.call("PUT", url, {"channels": [], "version": bad}, role="admin")[0] == 400, bad
    # A list stored before c31 has no version: 0 matches it.
    ctx.ops_table.put_item(Item={"pk": "DIGEST", "sk": "CHANNELS", "channels": [{"id": CHANNEL, "label": "x"}]})
    assert api.call("GET", url, role="admin")[1]["version"] == 0
    rows: list[Any] = []
    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    mp.setattr(ctx.audit, "record", lambda p, action, target, details=None: rows.append((action, target, details)))
    try:
        assert api.call("PUT", url, {"channels": [{"id": other, "label": "y"}], "version": 0}, role="admin")[0] == 200
    finally:
        mp.undo()
    # The diff is against the item the put replaced, not an earlier read.
    assert ("digest.channels", "digest", {"added": [other], "removed": [CHANNEL], "renamed": []}) in rows


def test_c31_a_stored_clash_keeps_its_name_on_an_unrelated_save_and_case_folding_alone_is_no_clash(
    api: Client, ctx: DigestContext
) -> None:
    other, third = "C9999999999", "C8888888888"
    url = "/api/admin/digest/channels"
    ctx.ops_table.put_item(
        Item={
            "pk": "DIGEST",
            "sk": "CHANNELS",
            "channels": [{"id": CHANNEL, "label": "news"}, {"id": other, "label": "\u266fnews"}],
        }
    )
    got = api.call("GET", url, role="admin")[1]
    assert got["clashes"] == [[CHANNEL, other]]
    # Saving the clash back is refused with the row named, so the stored name is never replaced by the ID.
    status, body, *_ = api.call(
        "PUT", url, {"channels": [*got["channels"], {"id": third, "label": "ops"}], "version": 0}, role="admin"
    )
    assert status == 400 and body["message"].startswith("channel 2:")
    assert api.call("GET", url, role="admin")[1]["channels"][1]["label"] == "news"
    for a, b2 in (("Stra\u00dfe-news", "Strasse-news"), ("\u03c3news", "\u03c2news")):
        assert (
            api.call("PUT", url, {"channels": [{"id": CHANNEL, "label": a}, {"id": other, "label": b2}]}, role="admin")[
                0
            ]
            == 200
        ), a
    for a, b2 in (("finance", "\ufb01nance"), ("team's", "team\u2019s"), ("News", "news")):
        assert (
            api.call("PUT", url, {"channels": [{"id": CHANNEL, "label": a}, {"id": other, "label": b2}]}, role="admin")[
                0
            ]
            == 400
        ), a


def test_c32_the_route_requires_a_bounded_version(api: Client) -> None:
    import json as _json

    url = "/api/admin/digest/channels"
    status, body, *_ = api.call("PUT", url, raw_body=_json.dumps({"channels": []}), role="admin")
    assert status == 400 and body["details"] == {"code": "reload_needed"} and "reload" in body["message"]  # c33
    for bad in (10**40, 2**53, 2**53 - 1):  # c34: the largest version a client quotes is 2**53 - 2
        assert api.call("PUT", url, {"channels": [], "version": bad}, role="admin")[0] == 400, bad
    assert api.call("PUT", url, {"channels": [], "version": 0}, role="admin")[0] == 200


def test_c32_a_hand_edited_version_can_still_be_saved_over(api: Client, ctx: DigestContext) -> None:
    from decimal import Decimal

    url = "/api/admin/digest/channels"
    for raw, shown in (
        ("3", 0),
        (Decimal("2.5"), 2),
        (Decimal(-4), 0),
        (True, 0),
        (10**20, 0),
        (2**53, 0),
        (2**53 - 1, 0),
        (2**53 - 2, 2**53 - 2),
    ):
        ctx.ops_table.put_item(Item={"pk": "DIGEST", "sk": "CHANNELS", "version": raw, "channels": []})
        got = api.call("GET", url, role="admin")[1]
        assert got["version"] == shown, raw
        status, body, *_ = api.call("PUT", url, {"channels": [], "version": shown}, role="admin")
        assert status == 200 and body["version"] == shown + 1, (raw, body)


def test_c32_a_stale_409_and_the_message_names_the_earlier_row(api: Client, ctx: DigestContext) -> None:
    url = "/api/admin/digest/channels"
    assert api.call("PUT", url, {"channels": [], "version": 0}, role="admin")[0] == 200
    status, body, *_ = api.call("PUT", url, {"channels": [], "version": 0}, role="admin")  # a retried save
    assert status == 409 and body["details"] == {"code": "channels_changed"}  # c33: no guess at whose save
    status, body, *_ = api.call(
        "PUT",
        url,
        {
            "channels": [{"id": CHANNEL, "label": "finance"}, {"id": "C9999999999", "label": "\ufb01nance"}],
            "version": 1,
        },
        role="admin",
    )
    assert status == 400 and body["message"] == "channel 2: reads the same as channel 1 (#finance)"


def test_c33_a_clash_of_three_is_one_group_and_the_subscriber_view_shows_two_by_id(
    api: Client, ctx: DigestContext
) -> None:
    b2, c3 = "C9999999999", "C8888888888"
    ctx.ops_table.put_item(
        Item={
            "pk": "DIGEST",
            "sk": "CHANNELS",
            "channels": [
                {"id": CHANNEL, "label": "news"},
                {"id": b2, "label": "News"},
                {"id": c3, "label": "\u266fnews"},
            ],
        }
    )
    got = api.call("GET", "/api/admin/digest/channels", role="admin")[1]
    assert got["clashes"] == [[CHANNEL, b2, c3]]
    assert [c.label for c in ctx.deliveries.channels()] == ["news", f"News ({b2})", f"news ({c3})"]


def test_c35_a_repeated_stored_id_counts_once(api: Client, ctx: DigestContext) -> None:
    ctx.ops_table.put_item(
        Item={
            "pk": "DIGEST",
            "sk": "CHANNELS",
            "channels": [{"id": CHANNEL, "label": "news"}, {"id": CHANNEL, "label": "news"}],
        }
    )
    got = api.call("GET", "/api/admin/digest/channels", role="admin")[1]
    assert got["channels"] == [{"id": CHANNEL, "label": "news"}] and got["clashes"] == []
    assert [c.id for c in ctx.deliveries.channels()] == [CHANNEL]  # the subscriber view and send-time check (c36)
    assert ctx.deliveries.channel(CHANNEL) == ctx.deliveries.channels()[0]


def test_c37_a_stored_label_that_is_not_text_shows_the_channel_id(api: Client, ctx: DigestContext) -> None:
    from decimal import Decimal

    for bad in (None, Decimal(123), True, ["x"]):
        ctx.ops_table.put_item(Item={"pk": "DIGEST", "sk": "CHANNELS", "channels": [{"id": CHANNEL, "label": bad}]})
        assert [c.label for c in ctx.deliveries.channels()] == [CHANNEL], bad


def test_c38_an_older_message_cannot_take_the_send_slot_back(api: Client, ctx: DigestContext) -> None:
    _, d = create(api, "viewer", kind="email")
    take = ctx.deliveries.take_send
    mon, tue, wed = (iso(datetime(2026, 11, day, 8, tzinfo=UTC)) for day in (10, 11, 12))  # as production writes them
    assert take("u-viewer", d["id"], tue, "tue")
    assert not take("u-viewer", d["id"], mon, "mon")  # redriven from the DLQ: refused
    assert not take("u-viewer", d["id"], tue, "dup")  # the same time twice: refused
    assert take("u-viewer", d["id"], tue, "tue")  # the same message again keeps it
    assert take("u-viewer", d["id"], wed, "wed")  # a later time takes it over


def test_c39_a_throttled_unsubscribe_from_the_form_is_a_503_page_with_retry_after(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from botocore.exceptions import ClientError

    _, d = create(api, "viewer", kind="email")
    token = ctx.delivery_outlets.unsubscribe.issue("u-viewer", d["id"])

    def throttled(*a: Any, **k: Any) -> Any:
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "UpdateItem")

    monkeypatch.setattr(type(ctx.deliveries), "switch_off", throttled)
    s, page, headers = api.call(
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "text/html"},
        raw_body=f"t={token}",
    )
    low = {k.lower(): v for k, v in headers.items()}
    assert (
        s == 503 and low["content-type"].startswith("text/html") and low["retry-after"] == "2"
    )  # c40: as the JSON answer
    assert "Try the link again later, or sign in" in page and "whats-new" in page  # c50: the app path too

    # c40: any other failure is a page too, with the JSON answer's status
    def broken(*a: Any, **k: Any) -> Any:
        raise ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}}, "UpdateItem")

    monkeypatch.setattr(type(ctx.deliveries), "switch_off", broken)
    s, page, headers = api.call(
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "text/html"},
        raw_body=f"t={token}",
    )
    assert s == 500 and page.startswith("<!DOCTYPE html>") and "Something went wrong" in page
    assert "Try the link again later, or sign in" in page and "will not help" not in page  # c51: a 500 may pass
    s, page, headers = api.call(  # c42: this answer's own headers, with an allowed Origin so CORS headers are set
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "accept": "text/html",
            "origin": "https://app.example.com",
        },
        raw_body="nothing=here",
    )
    assert s == 400 and "incomplete or no longer valid" in page and "T (" not in page
    assert any(h.lower() == "access-control-allow-origin" for h in headers)
    for k in ("vary", "access-control-allow-origin", "retry-after", "content-type"):  # c41: no case duplicates
        assert sum(1 for h in headers if h.lower() == k) <= 1, k


def test_c48_a_deleted_unsubscribe_key_through_the_outlets_is_a_503_with_retry_after_60(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from botocore.exceptions import ClientError

    _, d = create(api, "viewer", kind="email")
    token = ctx.delivery_outlets.unsubscribe.issue("u-viewer", d["id"])

    class Deleted:  # Secrets Manager with the secret gone; the real SecretReader attaches the cause (c49)
        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "x"}}, "GetSecretValue")

    # The production wiring end to end: AwsOutlets.unsubscribe's lambda over a real SecretReader.
    from types import SimpleNamespace

    from insights_radar.insights.delivery_runner import AwsOutlets
    from insights_radar.insights.delivery_transport import SecretReader

    stub = SimpleNamespace(
        _secrets=SecretReader(Deleted()), _config=SimpleNamespace(unsub_secret_arn=KEY_ARN), _ctx=ctx
    )
    monkeypatch.setitem(vars(ctx.delivery_outlets), "unsubscribe", AwsOutlets.unsubscribe.func(stub))  # type: ignore[arg-type]
    s, b, headers = api.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")
    low = {k.lower(): v for k, v in headers.items()}
    assert s == 503 and b["code"] == "unsubscribe_retry" and low["retry-after"] == "60"
    assert "in a minute" not in b["message"]  # the client adds the wait itself
    assert any("unsubscribe.key_unusable" in r.getMessage() for r in caplog.records)  # the alarm's line
    assert any('"reason": "ResourceNotFoundException"' in r.getMessage() for r in caplog.records)  # c50: the cause


def test_c50_a_short_key_answers_the_form_that_retrying_will_not_help(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from insights_radar.insights.delivery_transport import UnsubscribeTokens

    _, d = create(api, "viewer", kind="email")
    token = ctx.delivery_outlets.unsubscribe.issue("u-viewer", d["id"])
    short = UnsubscribeTokens(lambda: "short", ctx.clock)
    monkeypatch.setitem(vars(ctx.delivery_outlets), "unsubscribe", short)
    s, b, headers = api.call("POST", "/api/digest/unsubscribe", {"t": token}, role="anon")
    assert s == 503 and b["code"] == "unsubscribe_unavailable"
    assert "what's new" in b["message"].lower() and "try the link again" not in b["message"]
    assert all(h.lower() != "retry-after" for h in headers)
    s, page, headers = api.call(
        "POST",
        "/api/digest/unsubscribe",
        role="anon",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "text/html"},
        raw_body=f"t={token}",
    )
    assert s == 503 and page.startswith("<!DOCTYPE html>") and all(h.lower() != "retry-after" for h in headers)
    assert "trying the link again will not help" in page and "turn the delivery off" in page
    assert "Try the link again later" not in page
    assert any('"reason": "unsubscribe_not_configured"' in r.getMessage() for r in caplog.records)


# ---- scan cycle 54: the backend coverage gate (8 lines that no test reached since c22) ---------------------------


def test_c54_the_confirm_page_reads_no_code_from_a_body_that_is_not_a_json_object() -> None:
    from insights_radar.api.routes import _answer_code

    assert _answer_code({"body": "not json"}) == ""
    assert _answer_code({"body": "[1, 2]"}) == ""
    assert _answer_code({"body": None}) == "" and _answer_code({}) == ""
    assert _answer_code({"body": '{"code": "unsubscribe_unavailable"}'}) == "unsubscribe_unavailable"


def test_c54_a_test_slot_that_cannot_be_given_back_is_logged_and_the_error_stands(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, d = create(api, "viewer", kind="email")
    signal(ctx)

    def throttled(*a: Any, **k: Any) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "m"}}, "GetItem")

    def lost(*a: Any, **k: Any) -> Any:
        raise RuntimeError("the give-back write failed too")

    monkeypatch.setattr(ctx.deliveries, "find", throttled)
    monkeypatch.setattr(ctx.deliveries, "give_back_test", lost)
    assert api.call("POST", f"/api/me/deliveries/{d['id']}/test", {}, role="viewer")[0] == 503  # the first error
    assert any("digest.test_slot_kept" in r.getMessage() for r in caplog.records)


def test_c54_store_writes_raise_errors_that_are_not_a_lost_condition(
    api: Client, ctx: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, d = create(api, "viewer", kind="email")
    stamp = ctx.deliveries.take_test("u-viewer", d["id"]).test_at

    def throttled(*a: Any, **k: Any) -> Any:
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "m"}}, "UpdateItem")

    store = ctx.deliveries
    monkeypatch.setattr(store._t, "update_item", throttled)
    with pytest.raises(ClientError):
        store.give_back_test("u-viewer", d["id"], stamp)
    monkeypatch.setattr(store, "_put_channels", throttled)
    with pytest.raises(ClientError):
        store.replace_channels([], "u-admin", 0)


def test_c54_a_standing_miss_count_with_no_message_still_records_the_failed_send(
    api: Client, ctx: DigestContext
) -> None:
    _, d = create(api, "viewer", kind="email")
    ctx.ops_table.update_item(  # a stale message from an earlier send, and a miss count with no message of its own
        Key={"pk": "USER#u-viewer", "sk": f"DELIVERY#{d['id']}"},
        UpdateExpression="SET misses = :m, last_error = :e",
        ExpressionAttributeValues={":m": 1, ":e": "an earlier message"},
    )
    row = replace(ctx.deliveries.get("u-viewer", d["id"]), miss_fault=None)
    assert row.misses == 1
    assert ctx.deliveries.advance(row, "2026-11-15T00:00:00Z", DeliveryStatus.EMPTY) is True
    after = ctx.deliveries.get("u-viewer", d["id"])
    assert after.cursor == "2026-11-15T00:00:00Z" and after.last_status == DeliveryStatus.ERROR
    assert after.last_error == "an earlier message"  # c57: with no message to show, none is written over it


def test_c54_transport_guards() -> None:
    from insights_radar.insights.delivery_transport import PermanentDeliveryError, _find_slack_id

    with pytest.raises(ValueError, match="unknown category"):
        PermanentDeliveryError("x", category="nonsense")
    assert _find_slack_id(["not", "a", "record"], "bob") is None and _find_slack_id(None, "bob") is None
