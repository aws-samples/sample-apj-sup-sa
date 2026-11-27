# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Support for the digest delivery scenario matrix (``test_delivery_matrix.py``).

A ``World`` is the real API application, the real scheduler/sender/feedback workers and the real delivery
services over moto, with every way out faked: Slack's Web API and webhooks (``MatrixHttp``), the directory lookup
(real ``DirectorySlackIds`` with SigV4 over a fake STS and ``MatrixHttp``), SES (``FakeSes``) and SQS (``FakeSqs``).
Callers are real user-pool users confirmed live (``LiveRoles``), signed in through a federated provider so a
Slack DM can be resolved by email or by alias.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from insights_radar.api import workers
from insights_radar.api.app import Application
from insights_radar.core.clock import FixedClock, parse_iso
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.identity.principal import Role
from insights_radar.identity.users import LiveRoles
from insights_radar.insights.delivery import Delivery, DeliveryConfig, DmResolver, SlackMode
from insights_radar.insights.delivery_transport import (
    DirectorySlackIds,
    EmailSlackIds,
    HttpResponse,
    SlackClient,
    SlackIdResolver,
    SlackWebhook,
)
from tests.integration import harness
from tests.integration.harness import SLACK_CHANNEL, SLACK_WEBHOOK, Client, FakeHttp, FakeOutlets, TestContext

CHANNEL = SLACK_CHANNEL
CHANNEL_LABEL = "radar-news"
APP = "https://app.example.com"
QUEUE = "https://sqs.us-east-1.amazonaws.com/111122223333/digest"
DIRECTORY_ENDPOINT = "https://abc123def4.execute-api.us-east-1.amazonaws.com/prod/slack-id"
DIRECTORY_ROLE = "arn:aws:iam::111122223333:role/directory-lookup"
BOT_VALUE = "xoxb-test-token"
WEBHOOK = SLACK_WEBHOOK
PLACEHOLDER = "GeneratedPlaceholder0123456789abcdef"
DEFAULT_SCHEDULE = {"cadence": "daily", "hour": 9, "timezone": "Europe/London"}
PROVIDER = "Federate"
KINDS = ("slack_channel", "slack_dm", "email")
ROLE_NAMES = ("viewer", "editor", "admin", "disabled", "anon")
PUBLIC_KEYS = {
    "suppressed",
    "stop_reason",
    "id",
    "kind",
    "target_label",
    "channel_id",
    "cadence",
    "weekday",
    "hour",
    "timezone",
    "enabled",
    "next_at",
    "last_sent_at",
    "last_status",
    "last_error",
}


def config(**env: str) -> DeliveryConfig:
    base = {
        "DIGEST_SLACK_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:111122223333:secret:slack",
        "DIGEST_UNSUB_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:111122223333:secret:unsub",
        "DIGEST_QUEUE_URL": QUEUE,
        "DIGEST_EMAIL_SENDER": "no-reply@example.com",
        "DIGEST_SES_CONFIG_SET": "air-digest",
        "APP_URL": APP,
    }
    return DeliveryConfig.from_env({**base, **env})


@dataclass(frozen=True)
class Mode:
    config: DeliveryConfig
    expect: tuple[bool, bool, bool]  # what is offered: slack_channel, slack_dm, email
    token: str = BOT_VALUE
    webhook_url: str = WEBHOOK


BOT = {"DIGEST_SLACK_MODE": "bot"}
SES = {"DIGEST_EMAIL_MODE": "ses"}
DIRECTORY = {
    "DIGEST_DM_RESOLVER": "directory",
    "DIGEST_DIRECTORY_ENDPOINT": DIRECTORY_ENDPOINT,
    "DIGEST_DIRECTORY_ROLE_ARN": DIRECTORY_ROLE,
}
MODES: dict[str, Mode] = {
    "all_off": Mode(config(), (False, False, False)),
    "slack_bot": Mode(config(**BOT), (True, False, False)),
    "webhook": Mode(config(DIGEST_SLACK_MODE="webhook", DIGEST_DM_RESOLVER="email"), (True, False, False)),
    "webhook_placeholder": Mode(config(DIGEST_SLACK_MODE="webhook"), (False, False, False), webhook_url=PLACEHOLDER),
    "dm_email": Mode(config(**BOT, DIGEST_DM_RESOLVER="email"), (True, True, False)),
    "dm_directory": Mode(config(**BOT, **DIRECTORY), (True, True, False)),
    "dm_directory_unconfigured": Mode(config(**BOT, DIGEST_DM_RESOLVER="directory"), (True, False, False)),
    "email_ses": Mode(config(**SES), (False, False, True)),
    "placeholder_secret": Mode(
        config(**BOT, DIGEST_DM_RESOLVER="email", **SES), (False, False, True), token=PLACEHOLDER
    ),
    "full": Mode(config(**BOT, DIGEST_DM_RESOLVER="email", **SES), (True, True, True)),
}


class FakeSqs:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.visibility: list[dict[str, Any]] = []
        self.fail: int = 0  # how many sends to refuse
        self.ambiguous: int = 0  # how many sends to store and then fail anyway (the client timed out)

    def send_message(self, **kw: Any) -> dict[str, Any]:
        if self.fail:
            self.fail -= 1
            raise ClientError({"Error": {"Code": "KMS.DisabledException", "Message": "m"}}, "SendMessage")
        self.messages.append(kw)
        if self.ambiguous:
            self.ambiguous -= 1
            raise ReadTimeoutError(endpoint_url="https://sqs.us-east-1.amazonaws.com/")
        return {"MessageId": f"m{len(self.messages)}"}

    def change_message_visibility(self, **kw: Any) -> None:
        self.visibility.append(kw)

    def bodies(self) -> list[dict[str, Any]]:
        """The queued messages without their claim, which is random: each one must carry it (scan cycle 3)."""
        out = []
        for m in self.messages:
            body = json.loads(m["MessageBody"])
            claim = body.pop("claim", "")
            assert len(claim) == 32 and all(c in "0123456789abcdef" for c in claim), body
            out.append(body)
        return out


class FakeSts:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def assume_role(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        if self.error:
            raise self.error
        return {"Credentials": {"AccessKeyId": "ASIAEXAMPLE", "SecretAccessKey": "secret", "SessionToken": "tok"}}


class MatrixHttp(FakeHttp):
    """Slack (FakeHttp) plus the directory alias lookup, answered from ``directory_ids`` or ``directory_replies``."""

    def __init__(self) -> None:
        super().__init__()
        self.directory_ids: dict[str, str] = {}
        self.directory_replies: list[HttpResponse | Exception] = []

    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        if url.startswith(DIRECTORY_ENDPOINT):
            self.calls.append({"method": method, "url": url, "headers": headers, "body": None, "timeout": timeout})
            if self.directory_replies:
                reply = self.directory_replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply
            alias = parse_qs(urlsplit(url).query).get("alias", [""])[0]
            if alias in self.directory_ids:
                return HttpResponse(200, {}, json.dumps({"slackId": self.directory_ids[alias]}).encode())
            return HttpResponse(404, {}, b'{"message": "not found"}')
        return super().request(method, url, headers, body, timeout)

    def directory_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["url"].startswith(DIRECTORY_ENDPOINT)]

    def slack_calls(self, method: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["url"].endswith(method)]


class MatrixOutlets(FakeOutlets):
    """FakeOutlets that follow the deployment's mode: a webhook is configured only with a real hooks URL, and
    the directory resolver is the real client over a fake STS."""

    def __init__(self, ctx: MatrixContext) -> None:
        super().__init__(ctx.clock)
        self._ctx = ctx
        self.http = MatrixHttp()
        self.sts = FakeSts()
        self.slack = SlackClient(lambda fresh: self.token, self.http)
        self.webhook = SlackWebhook(lambda: self.webhook_url, self.http)

    def slack_configured(self) -> bool:
        if self._ctx.settings.digest.slack_mode is SlackMode.WEBHOOK:
            return self.webhook_url.startswith("https://hooks.slack.com/")
        return self.token.startswith("xoxb-")

    @property
    def slack_ids(self) -> SlackIdResolver:
        cfg = self._ctx.settings.digest
        if cfg.dm_resolver is DmResolver.DIRECTORY:
            return DirectorySlackIds(cfg.directory_endpoint, cfg.directory_role_arn, self.sts, self.http, "us-east-1")
        return EmailSlackIds(self.slack)


@dataclass
class Person:
    role: str
    subject: str
    email: str
    alias: str
    username: str  # as the token names it: Federate_<alias>
    real: str  # the user pool's own username


class MatrixContext(TestContext):
    __test__ = False

    def __init__(self, graph_table: Any, ops_table: Any, clock: FixedClock, mode: Mode) -> None:
        super().__init__(graph_table, ops_table, clock)
        self.sqs = FakeSqs()
        self.settings = replace(self.settings, digest=mode.config, identity_provider=PROVIDER)
        self.delivery_outlets = MatrixOutlets(self)
        self.delivery_outlets.token = mode.token
        self.delivery_outlets.webhook_url = mode.webhook_url
        self.aliases: dict[str, str] = {}
        real_get = self.users.get

        def get(name: str) -> Any:
            real = self.aliases.get(name, name)
            view = real_get(real)
            return replace(view, username=name) if real != name else view

        self.users.get = get  # type: ignore[method-assign]
        self.live_roles = LiveRoles(self.users)

    def client(self, name: str) -> Any:
        return self.sqs if name == "sqs" else super().client(name)


class World:
    """One deployment, its people and its fakes."""

    def __init__(self, ctx: MatrixContext, people: dict[str, Person]) -> None:
        self.ctx = ctx
        self.api = Client(Application(ctx))
        self.people = people

    # ---- configuration ----------------------------------------------------------------------------------------

    @property
    def http(self) -> MatrixHttp:
        return self.ctx.delivery_outlets.http

    @property
    def out(self) -> MatrixOutlets:
        return self.ctx.delivery_outlets

    @property
    def ses(self) -> Any:
        return self.ctx.delivery_outlets.ses_client

    def set_mode(self, name: str) -> None:
        mode = MODES[name]
        self.ctx.settings = replace(self.ctx.settings, digest=mode.config)
        self.out.token = mode.token
        self.out.webhook_url = mode.webhook_url

    def set_config(self, cfg: DeliveryConfig) -> None:
        self.ctx.settings = replace(self.ctx.settings, digest=cfg)

    def now(self) -> datetime:
        return self.ctx.clock.now()

    def at(self, when: datetime | str) -> None:
        target = parse_iso(when) if isinstance(when, str) else when
        self.ctx.clock.advance(target - self.ctx.clock.now())

    def later(self, **kw: float) -> None:
        self.ctx.clock.advance(timedelta(**kw))

    # ---- people and the API ------------------------------------------------------------------------------------

    def subject(self, role: str) -> str:
        return self.people[role].subject

    def call(
        self, method: str, path: str, body: Any = None, role: str = "admin", **kw: Any
    ) -> tuple[int, Any, dict[str, str]]:
        return self.api.call(method, path, body, role=role, **kw)

    def create(
        self, role: str = "viewer", kind: str = "email", *, auto_channel: bool = True, **body: Any
    ) -> tuple[int, Any]:
        payload: dict[str, Any] = {**DEFAULT_SCHEDULE, "kind": kind, **body}
        if (
            kind == "slack_channel"
            and auto_channel
            and "channel_id" not in payload
            and self.ctx.settings.digest.slack_mode is SlackMode.BOT
        ):
            payload["channel_id"] = CHANNEL
        s, b, _ = self.api.call("POST", "/api/me/deliveries", payload, role=role)
        return s, b

    def made(self, role: str = "viewer", kind: str = "email", **body: Any) -> dict[str, Any]:
        s, b = self.create(role, kind, **body)
        assert s == 201, b
        return dict(b)

    def listed(self, role: str = "viewer") -> dict[str, Any]:
        s, b, _ = self.api.call("GET", "/api/me/deliveries", role=role)
        assert s == 200, b
        return dict(b)

    def stored(self, role: str, did: str) -> Delivery:
        return self.ctx.deliveries.get(self.subject(role), did)

    def item(self, role: str, did: str) -> dict[str, Any] | None:
        key = {"pk": f"USER#{self.subject(role)}", "sk": f"DELIVERY#{did}"}
        return self.ctx.ops_table.get_item(Key=key, ConsistentRead=True).get("Item")

    def patch(self, role: str, did: str, body: Any) -> tuple[int, Any]:
        s, b, _ = self.api.call("PATCH", f"/api/me/deliveries/{did}", body, role=role)
        return s, b

    def test_send(self, role: str, did: str) -> tuple[int, Any, dict[str, str]]:
        return self.api.call("POST", f"/api/me/deliveries/{did}/test", {}, role=role)

    def set_channels(self, *channels: tuple[str, str]) -> tuple[int, Any]:
        s, b, _ = self.api.call(
            "PUT",
            "/api/admin/digest/channels",
            {"channels": [{"id": i, "label": la} for i, la in channels]},
            role="admin",
        )
        return s, b

    # ---- the workers -------------------------------------------------------------------------------------------

    def send(self, role_or_subject: str, did: str, message_id: str = "m1", due: str | None = None) -> dict[str, Any]:
        """One queue message for one delivery. Each message id stands for its own scheduled time unless ``due``
        names one: two messages for the same time are what the scheduler's duplicate check is for."""
        subject = self.people[role_or_subject].subject if role_or_subject in self.people else role_or_subject
        body = json.dumps({"subject": subject, "id": did, "due": due or f"due-{message_id}"})
        return self.receive(body, message_id)

    def receive(self, body: str, message_id: str) -> dict[str, Any]:
        record = {
            "messageId": message_id,
            "body": body,
            "receiptHandle": "rh",
            "attributes": {"ApproximateReceiveCount": "1"},
        }
        return workers.digest_send({"Records": [record]}, None, self.ctx)

    def redeliver(self, i: int = -1, message_id: str = "m-again") -> dict[str, Any]:
        """Receive a message the sender itself put back on the queue (a Slack rate limit)."""
        return self.receive(str(self.ctx.sqs.messages[i]["MessageBody"]), message_id)

    def tick(self) -> dict[str, Any]:
        return workers.digest_schedule({}, None, self.ctx)

    def feedback(self, *messages: Any) -> dict[str, Any]:
        records = [{"Sns": {"Message": m if isinstance(m, str) else json.dumps(m)}} for m in messages]
        return workers.digest_feedback({"Records": records}, None, self.ctx)

    # ---- records -----------------------------------------------------------------------------------------------

    def node(self, node_type: NodeType, title: str, **kw: Any) -> Node:
        return self.ctx.graph.create(
            Node(id="", type=node_type, title=title, summary=kw.pop("summary", "s"), **kw), "u"
        )

    def signal(self, title: str = "Tariff change", **kw: Any) -> Node:
        return self.node(NodeType.SIGNAL, title, **kw)

    def watch(self, role: str, title: str, facts: int = 1) -> Node:
        org, _ = self.ctx.graph.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title=title), "u")
        s, b, _ = self.api.call("PUT", f"/api/me/watches/nodes/{org.id}", {}, role=role)
        assert s == 200, b
        for i in range(facts):
            fact = self.ctx.graph.create(
                Node(
                    id="", type=NodeType.FACT, title=f"{title} fact {i}", evidence_url=f"https://evidence.example/{i}"
                ),
                "u",
            )
            self.ctx.graph.link(fact.id, EdgeType.ABOUT, org.id, "u")
        return org

    # ---- what went out -----------------------------------------------------------------------------------------

    def posts(self) -> list[dict[str, Any]]:
        return self.http.posts()

    def post_text(self, i: int = -1) -> str:
        return json.dumps(self.posts()[i]["body"], ensure_ascii=False)

    def mails(self) -> list[dict[str, Any]]:
        return list(self.ses.sent)

    def mail_html(self, i: int = -1) -> str:
        return str(self.mails()[i]["Content"]["Simple"]["Body"]["Html"]["Data"])

    def mail_text(self, i: int = -1) -> str:
        return str(self.mails()[i]["Content"]["Simple"]["Body"]["Text"]["Data"])

    def outgoing(self) -> int:
        """Messages that left: Slack posts and SES mails."""
        return len(self.posts()) + len(self.mails())

    def delivered_text(self, kind: str) -> str:
        return self.mail_html() + self.mail_text() if kind == "email" else self.post_text()


def build_world(
    graph_table: Any,
    ops_table: Any,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
    mode: str = "full",
    channels: tuple[tuple[str, str], ...] = ((CHANNEL, CHANNEL_LABEL),),
) -> World:
    ctx = MatrixContext(graph_table, ops_table, clock, MODES[mode])
    people: dict[str, Person] = {}
    for i, role in enumerate(("viewer", "editor", "admin", "disabled")):
        pool_role = {"editor": Role.EDITOR, "admin": Role.ADMIN}.get(role, Role.VIEWER)
        view = ctx.users.invite(f"{role}@example.com", pool_role)
        alias = f"m{role}"
        username = f"{PROVIDER}_{alias}"
        ctx.aliases[username] = view.username
        people[role] = Person(role, view.subject, view.email, alias, username, view.username)
        groups = f"[{pool_role.value}]" if pool_role is not Role.VIEWER else "[]"
        monkeypatch.setitem(
            harness.ROLES,
            role,
            {
                "sub": view.subject,
                "email": view.email,
                "email_verified": "true",
                "cognito:groups": groups,
                "cognito:username": username,
            },
        )
        ctx.delivery_outlets.http.directory_ids[alias] = f"U0DIRS{i:05d}"
    ctx.users.set_enabled(people["disabled"].real, False)
    if channels:
        from insights_radar.insights.delivery import DigestChannel

        ctx.deliveries.replace_channels([DigestChannel(i, la) for i, la in channels], "test", 0)
    return World(ctx, people)


def oracle_next(cadence: str, weekday: int | None, hour: int, zone_name: str, after: datetime) -> datetime:
    """An independent answer for ``next_at``: walk UTC in 15-minute steps (every offset in use is a multiple of
    15 minutes) and take, for each local day, the first instant whose local wall time is at or past ``hour``:00
    (the jump for a skipped hour, the first pass of a repeated one); the first such instant after ``after`` on
    a matching day wins."""
    zone = ZoneInfo(zone_name)
    t = after.replace(minute=after.minute - after.minute % 15, second=0, microsecond=0)
    t -= timedelta(days=1)
    seen: set[Any] = set()
    end = after + timedelta(days=10)
    while t < end:
        local = t.astimezone(zone)
        day = local.date()
        if day not in seen and local.hour >= hour:
            seen.add(day)
            if t > after and (cadence == "daily" or day.weekday() == weekday):
                return t
        t += timedelta(minutes=15)
    raise AssertionError("no time within 10 days")


@dataclass
class Expect:
    """What a scenario expects to have happened."""

    status: int
    extra: dict[str, Any] = field(default_factory=dict)
