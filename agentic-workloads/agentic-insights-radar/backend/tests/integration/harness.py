# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Shared harness: an AppContext twin on moto plus an HTTP API v2 event builder."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import boto3

from insights_radar.api.app import Application
from insights_radar.api.context import Settings
from insights_radar.audit.log import AuditLog
from insights_radar.chat.budget import ChatBudget
from insights_radar.core.clock import FixedClock
from insights_radar.graph.moderation import RunOrphans
from insights_radar.graph.repository import GraphRepository
from insights_radar.graph.search import HashEmbedder, HybridSearch, InMemoryVectorIndex
from insights_radar.identity.run_token import LocalHmac, RunTokenService
from insights_radar.identity.users import UserAdmin
from insights_radar.insights.delivery import DeliveryService
from insights_radar.insights.delivery_runner import DeliveryRunner
from insights_radar.insights.delivery_transport import (
    EmailSlackIds,
    HttpResponse,
    SesSender,
    SlackClient,
    SlackIdResolver,
    SlackWebhook,
    UnsubscribeTokens,
)
from insights_radar.insights.digest import DigestService
from insights_radar.insights.service import InsightService
from insights_radar.modules.repository import ModuleRepository, ModuleRun
from insights_radar.modules.service import ModuleService
from insights_radar.modules.skills import SkillRepository
from insights_radar.modules.watch import WatchStore
from insights_radar.product.knowledge import KnowledgeBase
from insights_radar.product.service import ProductService
from insights_radar.profile.repository import ProfileRepository
from insights_radar.radars.service import MemoryBlobStore, RadarService

# A fake Slack bot value, passed by name.
FAKE_BOT_VALUE = "xoxb-test-token"

RUNTIME_ROLE = "arn:aws:iam::111122223333:role/mr-module-runtime"
CALLER_ARN = "arn:aws:sts::111122223333:assumed-role/mr-module-runtime/session-1"
CHAT_ROLE = "arn:aws:iam::111122223333:role/mr-chat-runtime"
CHAT_CALLER_ARN = "arn:aws:sts::111122223333:assumed-role/mr-chat-runtime/session-9"
# Made-up Slack ids and webhook URL, built from parts: written whole, "C" or "D" and ten digits reads as a hex
# secret and a hooks.slack.com URL as a Slack token to the secret scanners (scan cycle 1).
DIGITS = "0123456789"
SLACK_CHANNEL = "C" + DIGITS
SLACK_DM = "D" + DIGITS
SLACK_WEBHOOK = "https://hooks.slack.com/services/" + "T000/B000/secret"


class FakeLauncher:
    def __init__(self) -> None:
        self.stopped: list[tuple[str, str]] = []
        self.payloads: list[dict[str, Any]] = []

    def launch(self, run: ModuleRun, payload: dict[str, Any]) -> str:
        self.payloads.append(payload)
        return f"arn:aws:states:us-east-1:111122223333:execution:mr:{run.run_id}"

    def stop(self, execution_arn: str, cause: str) -> None:
        self.stopped.append((execution_arn, cause))


class _Exc(Exception):
    pass


class FakeAgent:
    class exceptions:
        ServiceQuotaExceededException = type("ServiceQuotaExceededException", (_Exc,), {})
        ConflictException = type("ConflictException", (_Exc,), {})

    def __init__(self) -> None:
        self.n = 0

    def create_data_source(self, **kw: Any) -> dict[str, Any]:
        self.n += 1
        return {"dataSource": {"dataSourceId": f"DS{self.n}"}}

    def update_data_source(self, **kw: Any) -> None: ...

    def delete_data_source(self, **kw: Any) -> None: ...

    def start_ingestion_job(self, **kw: Any) -> dict[str, Any]:
        return {"ingestionJob": {"ingestionJobId": "J"}}

    def list_ingestion_jobs(self, **kw: Any) -> dict[str, Any]:
        # Started after anything the tests write, so it covers every upload and crawl.
        return {"ingestionJobSummaries": [{"status": "COMPLETE", "startedAt": "2100-01-01T00:00:00.000Z"}]}


class FakeRuntime:
    def retrieve(self, **kw: Any) -> dict[str, Any]:
        return {
            "retrievalResults": [
                {
                    "content": {"text": "Dual motors rated 50k cycles"},
                    "score": 0.7,
                    "location": {"s3Location": {"uri": "s3://assets/docs/p/d/spec.pdf"}},
                    "metadata": {"filename": "spec.pdf", "product_id": "p"},
                }
            ]
        }


class FakeHttp:
    """Slack's side of the conversation: records every request; answers from ``replies`` first, else a success
    shaped like the method's real answer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.replies: list[HttpResponse | Exception] = []

    def request(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = (body or b"").decode()
        self.calls.append({"method": method, "url": url, "headers": headers, "body": parsed, "timeout": timeout})
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        if url.endswith("conversations.open"):
            return HttpResponse(200, {}, json.dumps({"ok": True, "channel": {"id": SLACK_DM}}).encode())
        if url.endswith("users.lookupByEmail"):
            return HttpResponse(200, {}, b'{"ok": true, "user": {"id": "U0123456789"}}')
        if url.startswith("https://hooks.slack.com/"):
            return HttpResponse(200, {}, b"ok")
        return HttpResponse(200, {}, b'{"ok": true, "ts": "1.2"}')

    def posts(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["url"].endswith("chat.postMessage") or "hooks.slack.com" in c["url"]]


class FakeSes:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.errors: list[Exception] = []

    def send_email(self, **kw: Any) -> dict[str, Any]:
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(kw)
        return {"MessageId": f"m-{len(self.sent)}"}


class FakeSlackIds:
    def __init__(self) -> None:
        self.ids: dict[str, str] = {}

    def slack_id(self, key: str) -> str:
        from insights_radar.insights.delivery_transport import PermanentDeliveryError

        if key not in self.ids:
            raise PermanentDeliveryError("slack_user_not_found", "no Slack user for your alias")
        return self.ids[key]


class FakeOutlets:
    """The delivery clients over fakes: a Slack bot token and webhook URL, SES, the unsubscribe key."""

    def __init__(self, clock: FixedClock) -> None:
        self.http = FakeHttp()
        self.ses_client = FakeSes()
        self.token = FAKE_BOT_VALUE
        self.webhook_url = SLACK_WEBHOOK
        self.key = "k" * 48
        self.directory = FakeSlackIds()
        self.by_email = True
        self.slack = SlackClient(lambda fresh: self.token, self.http)
        self.webhook = SlackWebhook(lambda: self.webhook_url, self.http)
        self.ses = SesSender(self.ses_client, "no-reply@example.com", "air-digest")
        self.unsubscribe = UnsubscribeTokens(lambda: self.key, clock)

    def slack_configured(self) -> bool:
        return self.token.startswith("xoxb-")

    @property
    def slack_ids(self) -> SlackIdResolver:
        return EmailSlackIds(self.slack) if self.by_email else self.directory


class TokenRoles:
    """LiveRoles stand-in that trusts the token."""

    def confirm(self, principal: Any) -> Any:
        return principal


class TestContext:
    __test__ = False

    def __init__(self, graph_table: Any, ops_table: Any, clock: FixedClock) -> None:
        self.clock = clock
        self.settings = replace(
            Settings.from_env({}),
            assets_bucket="example-assets-111122223333-us-east-1",
            module_runtime_role_arn=RUNTIME_ROLE,
            allowed_origin="https://app.example.com",
            user_pool_id="",
            chat_runtime_arn="arn:chat",
            chat_runtime_role_arn=CHAT_ROLE,
        )
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket="example-assets-111122223333-us-east-1")
        cognito = boto3.client("cognito-idp", region_name="us-east-1")
        # Like the real pool: users sign in by email, so Cognito usernames are UUIDs, not the email.
        pool = cognito.create_user_pool(PoolName="mr", UsernameAttributes=["email"])["UserPool"]["Id"]
        for g in ("admin", "editor", "viewer"):
            cognito.create_group(UserPoolId=pool, GroupName=g)
        self.settings = replace(self.settings, user_pool_id=pool)
        self.users = UserAdmin(cognito, pool)
        # The canned test callers (u-viewer, u-editor, u-admin) are not pool users, so their roles are
        # taken from the token; tests of the live check build a LiveRoles over real pool users.
        self.live_roles = TokenRoles()
        self.cognito = cognito
        self.graph = GraphRepository(graph_table, clock)
        self.index = InMemoryVectorIndex()
        self.search = HybridSearch(self.graph, self.index, HashEmbedder())
        self.modules = ModuleRepository(ops_table, clock)
        self.skills = SkillRepository(ops_table, clock)
        self.modules.seed_builtins()
        self.profile = ProfileRepository(ops_table, clock)
        self.tokens = RunTokenService(LocalHmac(b"k" * 32), clock)
        self.launcher = FakeLauncher()
        self.knowledge = KnowledgeBase(FakeAgent(), FakeRuntime(), "KB", "S3DS", "arn:aws:s3:::assets")
        self.products = ProductService(
            ops_table, self.s3, "example-assets-111122223333-us-east-1", self.knowledge, clock
        )
        self.insights = InsightService(self.graph, self.search, ops_table, clock)
        self.radars = RadarService(ops_table, MemoryBlobStore(), clock)
        self.digests = DigestService(ops_table, self.graph, clock)
        self.audit = AuditLog(ops_table, clock)
        # After the graph, the insights and the audit log, which finalize's orphan cleanup uses.
        self.module_service = ModuleService(
            self.modules, self.tokens, self.launcher, clock, orphans=RunOrphans.of(self)
        )
        self.chat_budget = ChatBudget(self.modules, ops_table)
        self.ops_table = ops_table
        self.deliveries = DeliveryService(ops_table, clock)
        self.delivery_outlets = FakeOutlets(clock)
        self.watch = WatchStore(ops_table)
        self.watch_requests = 0
        self.watch_enabled = True

    @property
    def delivery_runner(self) -> DeliveryRunner:
        """Built at each use, so a test that changes the delivery settings gets them."""
        return DeliveryRunner.of(self)

    def request_watch(self) -> bool:
        self.watch_requests += 1
        return self.watch_enabled

    def client(self, name: str) -> Any:
        return boto3.client(name, region_name="us-east-1")

    def unstamp(self, *node_ids: str) -> None:
        """Make nodes look as they were written before nodes were stamped private or public (SR2-01)."""
        for node_id in node_ids:
            self.graph._table.update_item(
                Key={"pk": f"N#{node_id}", "sk": "META"},
                UpdateExpression="REMOVE #p",
                ExpressionAttributeNames={"#p": "private"},
            )

    def reindex(self) -> None:
        from insights_radar.graph.indexer import StreamIndexer
        from insights_radar.graph.registry import NodeType

        emb = HashEmbedder()
        for t in NodeType:
            if t is NodeType.INSIGHT:
                continue
            for n in self.graph.iterate(t):
                self.index.put(n.id, emb.embed(n.search_text), StreamIndexer.metadata(n))
        for m in self.modules.list_all():
            if m.insight_kind:
                for n in self.graph.iterate(NodeType.INSIGHT, insight_kind=m.insight_kind):
                    self.index.put(n.id, emb.embed(n.search_text), StreamIndexer.metadata(n))


ROLES: dict[str, dict[str, Any] | None] = {
    "anon": None,
    "viewer": {"sub": "u-viewer", "email": "viewer@example.com", "cognito:groups": "[viewer]"},
    "editor": {"sub": "u-editor", "email": "editor@example.com", "cognito:groups": "[editor]"},
    "admin": {"sub": "u-admin", "email": "admin@example.com", "cognito:groups": "[admin]"},
}


class Client:
    def __init__(self, app: Application) -> None:
        self.app = app

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        role: str = "admin",
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        raw_body: str | None = None,
        iam_arn: str | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        if (
            method == "PUT"
            and path == "/api/admin/digest/channels"
            and isinstance(body, dict)
            and "version" not in body
        ):
            # The route requires the version the form loaded (scan cycle 32): quote the current one, as the form
            # does. Tests of the version itself pass their own, or a raw body.
            got = self.call("GET", path, role="admin")[1]
            body = {**body, "version": got.get("version", 0) if isinstance(got, dict) else 0}
        claims = ROLES[role]
        ctx: dict[str, Any] = {"http": {"method": method, "path": path}}
        if claims is not None:
            ctx["authorizer"] = {"jwt": {"claims": claims}}
        if iam_arn:
            ctx["authorizer"] = {"iam": {"userArn": iam_arn}}
        event = {
            "rawPath": path,
            "requestContext": ctx,
            "headers": headers or {"origin": "https://app.example.com"},
            "queryStringParameters": query or None,
            "body": raw_body if raw_body is not None else (json.dumps(body) if body is not None else None),
        }
        res = self.app.handle(event)
        text = res["body"]
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = text
        return res["statusCode"], parsed, res["headers"]

    def tool(
        self, tool: str, token: str | None, args: dict[str, Any] | None = None, iam_arn: str | None = CALLER_ARN
    ) -> tuple[int, Any]:
        status, body, _ = self.call(
            "POST",
            f"/agent/tools/{tool}",
            args or {},
            role="anon",
            headers={"x-mr-run-token": token} if token else {},
            iam_arn=iam_arn,
        )
        return status, body


def ensure_running(ctx: TestContext, slug: str, run_id: str = "run-1", target_id: str | None = None) -> None:
    """Store a running run for a hand-minted run token (tools refuse a run that has ended); a run the
    test stored itself is kept."""
    from insights_radar.core.errors import NotFoundError
    from insights_radar.modules.repository import ModuleRun, RunStatus

    try:
        ctx.modules.get_run(slug, run_id)
    except NotFoundError:
        ctx.modules.put_run(
            ModuleRun(slug, run_id, RunStatus.RUNNING, "manual", "2026-10-05T00:00:00Z", "u-admin", target_id=target_id)
        )
