# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Runtime wiring: builds every service once per Lambda container from environment variables.

Clients are created lazily so a cold start only pays for what the request touches.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

import boto3
from botocore.config import Config

from insights_radar.audit.log import AuditLog
from insights_radar.chat.budget import ChatBudget
from insights_radar.core.clock import Clock, SystemClock
from insights_radar.graph.moderation import RunOrphans
from insights_radar.graph.repository import GraphRepository
from insights_radar.graph.search import HybridSearch, S3VectorsIndex, TitanEmbedder
from insights_radar.identity.run_token import KmsHmac, RunTokenService
from insights_radar.identity.users import LiveRoles, UserAdmin
from insights_radar.insights.delivery import DeliveryConfig, DeliveryService
from insights_radar.insights.delivery_runner import AwsOutlets, DeliveryRunner, Outlets
from insights_radar.insights.digest import DigestService
from insights_radar.insights.service import InsightService
from insights_radar.modules.catalog import ModelCatalog
from insights_radar.modules.repository import ModuleRepository
from insights_radar.modules.service import ModuleService, StepFunctionsLauncher
from insights_radar.modules.skills import SkillRepository
from insights_radar.modules.watch import WatchStore
from insights_radar.product.knowledge import KnowledgeBase
from insights_radar.product.service import ProductService
from insights_radar.profile.repository import ProfileRepository
from insights_radar.radars.service import RadarService, S3BlobStore

# The API function has 29 s: one slow dependency call with 5 adaptive attempts and a 30 s read
# timeout outlived the function, so the client got an API Gateway 504 with no application error and
# no log line. Background functions keep the longer budget.
_BOTO = Config(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=5, read_timeout=30)
_BOTO_API = Config(retries={"max_attempts": 2, "mode": "standard"}, connect_timeout=2, read_timeout=5)


@dataclass(frozen=True, slots=True)
class Settings:
    region: str
    graph_table: str
    ops_table: str
    assets_bucket: str
    vector_bucket: str
    vector_index: str
    kb_id: str
    kb_s3_source_id: str
    run_key_id: str
    state_machine_arn: str
    user_pool_id: str
    allowed_origin: str
    module_runtime_role_arn: str
    chat_runtime_arn: str
    chat_runtime_role_arn: str
    crawler_function: str
    watch_function: str
    user_pool_client_id: str
    cognito_domain: str
    solution_version: str
    # The whole deployment's per-minute ceiling for each model-backed bucket (SR2-11).
    workspace_rate_per_minute: int = 600
    # The chat runtime's model (the deploy option chatModel): chat usage is weighted at its tier (B3-029).
    chat_model_key: str = "sonnet-5-5"
    # The deploy option seed: the key of the seed bundle the deploy uploaded to the assets bucket ("" = none).
    seed_bundle_key: str = ""
    seed_bundle_bucket: str = ""
    # Set when people sign in only through an external identity provider (its name in the user pool).
    identity_provider: str = ""
    trust_provider_email: bool = False
    # The deploy options mcpIssuer and mcpAudience: the role an MCP client assumes with its web identity token, the
    # token's issuer and audience. Empty = off.
    mcp_role_arn: str = ""
    mcp_issuer: str = ""
    mcp_audience: str = ""
    # The deploy option digestDelivery (Slack, email): all off unless the infrastructure set it.
    digest: DeliveryConfig = field(default_factory=DeliveryConfig)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = env if env is not None else dict(os.environ)

        def get(name: str, default: str = "") -> str:
            return e.get(name, default)

        return cls(
            region=get("AWS_REGION", "us-east-1"),
            graph_table=get("AIR_GRAPH_TABLE"),
            ops_table=get("AIR_OPS_TABLE"),
            assets_bucket=get("AIR_ASSETS_BUCKET"),
            vector_bucket=get("AIR_VECTOR_BUCKET"),
            vector_index=get("AIR_VECTOR_INDEX", "nodes"),
            kb_id=get("AIR_KB_ID"),
            kb_s3_source_id=get("AIR_KB_S3_SOURCE_ID"),
            run_key_id=get("AIR_RUN_KEY_ID"),
            state_machine_arn=get("AIR_STATE_MACHINE_ARN"),
            user_pool_id=get("AIR_USER_POOL_ID"),
            allowed_origin=get("AIR_ALLOWED_ORIGIN"),
            module_runtime_role_arn=get("AIR_MODULE_RUNTIME_ROLE_ARN"),
            chat_runtime_arn=get("AIR_CHAT_RUNTIME_ARN"),
            chat_runtime_role_arn=get("AIR_CHAT_RUNTIME_ROLE_ARN"),
            crawler_function=get("AIR_CRAWLER_FUNCTION"),
            watch_function=get("AIR_WATCH_FUNCTION"),
            user_pool_client_id=get("AIR_USER_POOL_CLIENT_ID"),
            cognito_domain=get("AIR_COGNITO_DOMAIN"),
            solution_version=get("AIR_SOLUTION_VERSION", "1.0.0"),
            workspace_rate_per_minute=int(get("AIR_WORKSPACE_RATE_PER_MINUTE") or 600),
            chat_model_key=get("AIR_CHAT_MODEL_KEY", "sonnet-5-5"),
            seed_bundle_key=get("AIR_SEED_BUNDLE_KEY"),
            seed_bundle_bucket=get("AIR_SEED_BUNDLE_BUCKET"),
            identity_provider=get("AIR_IDENTITY_PROVIDER"),
            trust_provider_email=get("AIR_TRUST_PROVIDER_EMAIL") == "true",
            mcp_role_arn=get("AIR_MCP_ROLE_ARN"),
            mcp_issuer=get("AIR_MCP_ISSUER"),
            mcp_audience=get("AIR_MCP_AUDIENCE"),
            digest=DeliveryConfig.from_env(e),
        )


class AppContext:
    def __init__(
        self, settings: Settings, clock: Clock | None = None, session: Any = None, request_scoped: bool = False
    ) -> None:
        self.settings = settings
        self.clock = clock or SystemClock()
        self._session = session or boto3.session.Session(region_name=settings.region)
        # The API function must answer within 29 s, so its AWS calls get a short read timeout and few
        # retries; the background functions keep the longer budget.
        self._boto = _BOTO_API if request_scoped else _BOTO
        self.request_scoped = request_scoped

    def client(self, name: str) -> Any:
        factory: Any = self._session.client
        return factory(name, config=self._boto)

    @cached_property
    def _ddb(self) -> Any:
        return self._session.resource("dynamodb", config=self._boto)

    @cached_property
    def graph_table(self) -> Any:
        return self._ddb.Table(self.settings.graph_table)

    @cached_property
    def ops_table(self) -> Any:
        return self._ddb.Table(self.settings.ops_table)

    @cached_property
    def s3(self) -> Any:
        # The regional endpoint, so presigned upload URLs work in every Region: a new bucket outside
        # us-east-1 answers the global s3.amazonaws.com name with a 307 that a browser POST cannot follow.
        region = self.settings.region
        return self._session.client(
            "s3",
            region_name=region,
            endpoint_url=f"https://s3.{region}.amazonaws.com",
            config=self._boto.merge(Config(signature_version="s3v4", s3={"addressing_style": "virtual"})),
        )

    @cached_property
    def graph(self) -> GraphRepository:
        return GraphRepository(self.graph_table, self.clock)

    @cached_property
    def search(self) -> HybridSearch:
        index = S3VectorsIndex(self.client("s3vectors"), self.settings.vector_bucket, self.settings.vector_index)
        return HybridSearch(self.graph, index, TitanEmbedder(self.client("bedrock-runtime")))

    @cached_property
    def modules(self) -> ModuleRepository:
        return ModuleRepository(self.ops_table, self.clock)

    @cached_property
    def skills(self) -> SkillRepository:
        return SkillRepository(self.ops_table, self.clock)

    @cached_property
    def watch(self) -> WatchStore:
        return WatchStore(self.ops_table)

    @cached_property
    def profile(self) -> ProfileRepository:
        return ProfileRepository(self.ops_table, self.clock)

    @cached_property
    def tokens(self) -> RunTokenService:
        return RunTokenService(KmsHmac(self.client("kms"), self.settings.run_key_id), self.clock)

    @cached_property
    def module_service(self) -> ModuleService:
        launcher = StepFunctionsLauncher(self.client("stepfunctions"), self.settings.state_machine_arn)
        return ModuleService(self.modules, self.tokens, launcher, self.clock, orphans=RunOrphans.of(self))

    @cached_property
    def knowledge(self) -> KnowledgeBase | None:
        if not self.settings.kb_id:
            return None
        return KnowledgeBase(
            self.client("bedrock-agent"),
            self.client("bedrock-agent-runtime"),
            self.settings.kb_id,
            self.settings.kb_s3_source_id,
            f"arn:aws:s3:::{self.settings.assets_bucket}",
        )

    @cached_property
    def products(self) -> ProductService:
        return ProductService(
            self.ops_table,
            self.s3,
            self.settings.assets_bucket,
            self.knowledge,
            self.clock,
            request_crawl=self.request_crawl if self.settings.crawler_function else None,
        )

    def request_crawl(self, product_id: str) -> None:
        """Crawl a product's documentation asynchronously (the crawler can run for minutes)."""
        self.client("lambda").invoke(
            FunctionName=self.settings.crawler_function,
            InvocationType="Event",
            Payload=json.dumps({"product_id": product_id}).encode("utf-8"),
        )

    def request_watch(self) -> bool:
        """Check the sources now, asynchronously (hundreds of sources take a minute or two); False when this
        deployment has no watch function."""
        if not self.settings.watch_function:
            return False
        self.client("lambda").invoke(
            FunctionName=self.settings.watch_function, InvocationType="Event", Payload=b'{"trigger": "manual"}'
        )
        return True

    @cached_property
    def insights(self) -> InsightService:
        return InsightService(self.graph, self.search, self.ops_table, self.clock)

    @cached_property
    def digests(self) -> DigestService:
        return DigestService(self.ops_table, self.graph, self.clock)

    @cached_property
    def deliveries(self) -> DeliveryService:
        return DeliveryService(self.ops_table, self.clock)

    @cached_property
    def delivery_outlets(self) -> Outlets:
        return AwsOutlets(self)

    @cached_property
    def delivery_runner(self) -> DeliveryRunner:
        return DeliveryRunner.of(self)

    @cached_property
    def radars(self) -> RadarService:
        return RadarService(self.ops_table, S3BlobStore(self.s3, self.settings.assets_bucket), self.clock)

    @cached_property
    def chat_budget(self) -> ChatBudget:
        return ChatBudget(self.modules, self.ops_table, self.clock, ModelCatalog.get(self.settings.chat_model_key).tier)

    @cached_property
    def audit(self) -> AuditLog:
        return AuditLog(self.ops_table, self.clock)

    @cached_property
    def users(self) -> UserAdmin:
        return UserAdmin(self.client("cognito-idp"), self.settings.user_pool_id)

    @cached_property
    def live_roles(self) -> LiveRoles:
        return LiveRoles(self.users)
