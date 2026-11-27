# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Lambda functions, the HTTP API, the run workflow, the schedule and the stream indexer.

Each function has its own role with only what it uses. The HTTP API has two kinds of routes:

* ``GET /api/config``: public (the web app reads it before sign-in);
* ``/api/*``: Cognito JWT authorizer (ID token, audience = the app clients). Search and product
  knowledge questions, which call a model on every request, have their own lower throttle.

The agent runtimes do not use the HTTP API for their own calls. Each reaches its own function URL
with ``AWS_IAM`` auth: ``AgentTools`` (``/agent/tools/*``, module runtime role only) and ``ChatUsage``
(``/agent/chat/usage``, chat runtime role only). The function URLs have no resource policy, so Lambda
refuses every caller from another account before the function runs, and only a principal in this
account allowed ``lambda:InvokeFunctionUrl`` on that one function gets through. The backend also
checks that the caller is the expected runtime role and, for tools, that the run token grants the tool.
These two functions run the same code as the API but hold only what the agent routes use: no Cognito
administration, no run-token signing, no workflow starts.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import jsii
from aws_cdk import Aws, Duration, triggers
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_s3_assets as s3_assets
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from insights_radar_infra.config import RETENTION_DAYS, SOLUTION_VERSION, DeployConfig
from insights_radar_infra.data import Data
from insights_radar_infra.digest import (
    UNSUBSCRIBE_BURST,
    UNSUBSCRIBE_PATH,
    UNSUBSCRIBE_RATE,
    Digest,
)
from insights_radar_infra.search import DOCS_PREFIX, GRAPH_INDEX, Search

ROOT = Path(__file__).resolve().parents[2]
LAMBDA_BUNDLE = ROOT / "build" / "lambda"
COGNITO_ADMIN_ACTIONS = [
    "cognito-idp:AdminCreateUser",
    "cognito-idp:AdminGetUser",
    "cognito-idp:AdminDeleteUser",
    "cognito-idp:AdminEnableUser",
    "cognito-idp:AdminDisableUser",
    "cognito-idp:AdminUserGlobalSignOut",
    "cognito-idp:AdminAddUserToGroup",
    "cognito-idp:AdminRemoveUserFromGroup",
    "cognito-idp:AdminListGroupsForUser",
    "cognito-idp:ListUsers",
    "cognito-idp:ListUsersInGroup",
]
FINALIZE_RETRY_ERRORS = [
    "Lambda.ServiceException",
    "Lambda.AWSLambdaException",
    "Lambda.SdkClientException",
    "Lambda.TooManyRequestsException",
    "Lambda.Unknown",
    "States.TaskFailed",
]
# Routes that call a model (Titan embeddings or knowledge base retrieval) on every request.
# Routes that embed or retrieve with a model on every request, with the method that does it.
# GET /api/insights/{kind} embeds the ?q= it is given, so relevance listing belongs here too: it
# used to be metered only by the in-function limiter, which is per user, not per stage (P2-076).
# GET /api/insights/{kind} is not one of them: only its ``?q=`` relevance listing embeds, and a stage
# route setting cannot tell the two apart, so every insight list page (navigation, tabs, following
# cursors) shared 10 requests a second across all users and a team opening one tab together got 429s
# (CR2-10). The ``?q=`` form is limited per user and per workspace by the function's own RateLimiter.
MODEL_BACKED_ROUTES = (
    ("/api/graph/search", "POST"),
    ("/api/graph/ask", "POST"),
    ("/api/products/{id}/ask", "POST"),
)
# The MCP role's routes (deploy options mcpIssuer/mcpAudience): the API function strips this prefix.
IAM_PREFIX = "/iam"
MODEL_ROUTE_RATE = 10
MODEL_ROUTE_BURST = 20
KB_ACTIONS = [
    "bedrock:Retrieve",
    "bedrock:StartIngestionJob",
    "bedrock:ListIngestionJobs",
]

# How often the source watcher checks every Scout source. The Scout's own interval decides how often new items are
# read; checking more often than that only makes the inbox fill in smaller steps.
WATCH_INTERVAL_MINUTES = 120


@jsii.implements(apigw.IAccessLogSettings)
class _AccessLogs:
    """JSON access logs. No query strings and no header values: they could carry tokens.

    ``identity.sourceIp`` is CloudFront's edge address for every request that came through the CDN,
    which is almost all of them, so it answers no forensic question on its own. HTTP APIs offer no
    access-log variable for X-Forwarded-For (``$context.identity.sourceIp.xForwardedFor`` is refused
    at deploy), so the client address lives in the WAF request log; ``sub`` is the Cognito subject
    the JWT authorizer verified -- the one identifier that is proof rather than a claim, and the
    key that joins this log to the WAF log by time and path (P2-037)."""

    def __init__(self, destination: apigw.IAccessLogDestination) -> None:
        self._destination = destination

    @property
    def destination(self) -> apigw.IAccessLogDestination:
        return self._destination

    @property
    def format(self) -> apigateway.AccessLogFormat:
        return apigateway.AccessLogFormat.custom(
            '{"requestId":"$context.requestId","ip":"$context.identity.sourceIp",'
            '"sub":"$context.authorizer.claims.sub",'
            '"method":"$context.httpMethod","path":"$context.path","status":"$context.status",'
            '"latencyMs":"$context.responseLatency","authError":"$context.authorizer.error",'
            '"integrationError":"$context.integrationErrorMessage"}'
        )


# Item-level DynamoDB actions, the set CDK's read+write grants use. Paired with a
# dynamodb:LeadingKeys condition in ``only_partitions``, which is where the limit lives.
OPS_READ_ACTIONS = [
    "dynamodb:BatchGetItem",
    "dynamodb:ConditionCheckItem",
    "dynamodb:DescribeTable",
    "dynamodb:GetItem",
    "dynamodb:Query",
]
OPS_ITEM_ACTIONS = [
    "dynamodb:BatchGetItem",
    "dynamodb:BatchWriteItem",
    "dynamodb:ConditionCheckItem",
    "dynamodb:DeleteItem",
    "dynamodb:DescribeTable",
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:Query",
    "dynamodb:UpdateItem",
]

SEED_TIMEOUT_SECONDS = 900  # Lambda's maximum


class Backend(Construct):
    def __init__(
        self,
        scope: Construct,
        cid: str,
        config: DeployConfig,
        data: Data,
        search: Search,
        user_pool_id: str,
    ) -> None:
        super().__init__(scope, cid)
        if not LAMBDA_BUNDLE.exists():
            raise FileNotFoundError(
                "build/lambda is missing: run `python3 deployment/build.py` first"
            )
        self._config = config
        self._retention = logs.RetentionDays[RETENTION_DAYS[config.log_retention_days]]
        self._data = data
        self._search = search
        self._code = lambda_.Code.from_asset(str(LAMBDA_BUNDLE))
        self.functions: list[lambda_.Function] = []

        self.http_api = apigw.HttpApi(
            self,
            "Api",
            api_name=f"{config.prefix}-api",
            description="Agentic Insights Radar API",
            create_default_stage=False,
        )
        self.api_url = (
            f"https://{self.http_api.api_id}.execute-api.{Aws.REGION}.{Aws.URL_SUFFIX}"
        )
        self.base_env = {
            "AIR_GRAPH_TABLE": data.graph_table.table_name,
            "AIR_OPS_TABLE": data.ops_table.table_name,
            "AIR_ASSETS_BUCKET": data.assets.bucket_name,
            "AIR_VECTOR_BUCKET": search.vector_bucket_name,
            "AIR_VECTOR_INDEX": GRAPH_INDEX,
            "AIR_KB_ID": search.kb_id,
            "AIR_KB_S3_SOURCE_ID": search.documents_source_id,
            "AIR_RUN_KEY_ID": data.run_key.key_arn,
            "AIR_USER_POOL_ID": user_pool_id,
            "AIR_SOLUTION_VERSION": SOLUTION_VERSION,
        }
        self._issuer = ""
        self._audiences: list[str] = []
        self._user_pool_id = user_pool_id

    @property
    def config(self) -> DeployConfig:
        return self._config

    @property
    def data(self) -> Data:
        return self._data

    @property
    def user_pool_arn(self) -> str:
        return f"arn:{Aws.PARTITION}:cognito-idp:{Aws.REGION}:{Aws.ACCOUNT_ID}:userpool/{self._user_pool_id}"

    def set_auth(self, issuer: str, audiences: list[str]) -> None:
        """The JWT authorizer's issuer and audiences (the app clients exist only after the CDN)."""
        self._issuer = issuer
        self._audiences = audiences

    # ---- helpers --------------------------------------------------------------------------

    def function(
        self,
        name: str,
        handler: str,
        description: str,
        timeout: int = 30,
        memory: int = 1024,
        env: dict[str, str] | None = None,
        dlq: sqs.IQueue | None = None,
        reserved: int | None = None,
    ) -> lambda_.Function:
        log_group = logs.LogGroup(
            self,
            f"{name}Logs",
            retention=self._retention,
            encryption_key=self._data.key,
            removal_policy=self._data.removal,
        )
        # Own role with log access to its own log group only (instead of AWSLambdaBasicExecutionRole).
        role = iam.Role(
            self,
            f"{name}Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description=f"Agentic Insights Radar {name} function",
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[log_group.log_group_arn],
            )
        )
        fn = lambda_.Function(
            self,
            name,
            role=role,
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler=handler,
            code=self._code,
            description=description,
            timeout=Duration.seconds(timeout),
            memory_size=memory,
            environment={**self.base_env, **(env or {})},
            environment_encryption=self._data.key,
            log_group=log_group,
            tracing=lambda_.Tracing.ACTIVE,
            dead_letter_queue=dlq,
            # Only with reservedConcurrency=true: new accounts have too small a quota to reserve any.
            reserved_concurrent_executions=reserved
            if self._config.reserved_concurrency
            else None,
        )
        self.functions.append(fn)
        return fn

    def _allow(
        self, fn: lambda_.Function, actions: list[str], resources: list[str]
    ) -> None:
        fn.add_to_role_policy(iam.PolicyStatement(actions=actions, resources=resources))

    def _vectors(self, fn: lambda_.Function, actions: list[str]) -> None:
        self._allow(fn, actions, [self._search.graph_index_arn])
        self._data.key.grant_encrypt_decrypt(fn)

    def only_partitions(
        self, fn: lambda_.Function, keys: list[str], read_only: list[str] | None = None
    ) -> None:
        """Read and write only the ops-table partitions a function needs (``keys``), and read only those in
        ``read_only``. CDK's ``grant_read_write_data`` carries no condition, so the chat-usage function could
        read and overwrite module definitions and grants, MCP server URLs, the kill switch, every module's
        budget and the audit rows (P2-029)."""
        table = self._data.ops_table
        resources = [table.table_arn, f"{table.table_arn}/index/*"]
        for actions, leading in (
            (OPS_ITEM_ACTIONS, keys),
            (OPS_READ_ACTIONS, read_only or []),
        ):
            if leading:
                fn.add_to_role_policy(
                    iam.PolicyStatement(
                        actions=actions,
                        resources=resources,
                        conditions={
                            "ForAllValues:StringLike": {"dynamodb:LeadingKeys": leading}
                        },
                    )
                )
        # ForAllValues is true when the condition key is absent, and a Scan names no key, so the
        # condition above would let one through. Nothing in this solution scans a table.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["dynamodb:Scan"],
                resources=resources,
            )
        )
        self._data.key.grant_encrypt_decrypt(
            fn
        )  # the table is encrypted with the data key

    def _list_only_prefixes(self, fn: lambda_.Function, prefixes: list[str]) -> None:
        """``grant_read`` on one prefix still allows ``s3:List*`` on the whole bucket, so the tool gate
        could list the object keys of confidential ``docs/`` uploads. A deny with ``StringNotLike``
        bounds it: an absent ``s3:prefix`` (a listing of the whole bucket) is denied too (P2-030)."""
        allowed = [
            p for prefix in prefixes for p in (prefix, f"{prefix}/", f"{prefix}/*")
        ]
        fn.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["s3:ListBucket", "s3:ListBucketVersions"],
                resources=[self._data.assets.bucket_arn],
                conditions={"StringNotLike": {"s3:prefix": allowed}},
            )
        )

    def _no_version_deletes(self, fn: lambda_.Function, prefix: str = "*") -> None:
        """Bucket versioning is the documented way to recover a deleted document, and
        ``grant_delete`` includes ``s3:DeleteObject*`` -- which covers ``DeleteObjectVersion`` and so
        permanently removes the version a recovery would read. An explicit deny leaves the delete
        marker that a restore undoes, whatever a route or a path bug asks for (P2-009)."""
        fn.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["s3:DeleteObjectVersion", "s3:DeleteObjectVersionTagging"],
                resources=[self._data.assets.arn_for_objects(prefix)],
            )
        )

    def _mcp_role(self) -> iam.Role:
        """The role an MCP client assumes with an id token from mcpIssuer for mcpAudience. The session name
        must be the token's subject, so a caller cannot take another person's name; the API function maps
        that name to the account the provider's web sign-in created. One hour at most."""
        issuer = self._config.mcp_issuer or ""
        audience = self._config.mcp_audience or ""
        host = issuer.removeprefix("https://")
        provider: iam.IOidcProvider = (
            iam.OidcProviderNative.from_oidc_provider_arn(
                self, "McpIdentityProvider", self._config.mcp_provider_arn
            )
            if self._config.mcp_provider_arn
            else iam.OidcProviderNative(
                self, "McpIdentityProvider", url=issuer, client_ids=[audience]
            )
        )
        return iam.Role(
            self,
            "McpRole",
            assumed_by=iam.WebIdentityPrincipal(
                provider.oidc_provider_arn,
                conditions={
                    "StringEquals": {
                        f"{host}:aud": audience,
                        "sts:RoleSessionName": f"${{{host}:sub}}",
                    }
                },
            ),
            max_session_duration=Duration.hours(1),
            description="Agentic Insights Radar MCP clients (web identity, SigV4 to /iam/api/*)",
        )

    # ---- assembly -------------------------------------------------------------------------

    def agent_gates(self, module_role_arn: str, chat_role_arn: str) -> None:
        """The agent tool gate and the chat-usage route, each behind its own IAM-authenticated function URL.

        Built before the runtimes, which read these URLs. Their environment names no runtime and no
        workflow (a reference back would be a cycle), and neither function can administer users, sign
        run tokens or start runs.
        """
        data, search = self._data, self._search
        tools = self.function(
            "AgentTools",
            "insights_radar.api.app.handler",
            "Agent tool gate for module runs (function URL, module runtime role only)",
            timeout=29,
            memory=1536,
            env={"AIR_MODULE_RUNTIME_ROLE_ARN": module_role_arn},
            # Ten concurrent runs in tight tool loops would otherwise compete with the user API for
            # the Region's concurrency: this gate reaches neither WAF nor the API throttle, so the
            # cfn_nag skip's "load is bounded by WAF rate limits and API throttling" never held for
            # it (P2-086). Reserved only with reservedConcurrency=true, like the other reservations.
            reserved=10,
        )
        data.graph_table.grant_read_write_data(tools)
        data.ops_table.grant_read_write_data(tools)
        data.assets.grant_read(tools, "radars/*")  # radar_get reads radar versions
        data.assets.grant_put(tools, "drafts/*")  # draft_save writes the Markdown
        self._list_only_prefixes(tools, ["radars", "drafts"])
        self._vectors(tools, ["s3vectors:QueryVectors", "s3vectors:GetVectors"])
        self._allow(tools, ["bedrock:InvokeModel"], [search.embed_model_arn])
        self._allow(tools, ["bedrock:Retrieve"], [search.kb_arn])
        self._allow(tools, ["kms:VerifyMac"], [data.run_key.key_arn])

        usage = self.function(
            "ChatUsage",
            "insights_radar.api.app.handler",
            "Chat budget reservations and usage (function URL, chat runtime role only)",
            env={"AIR_CHAT_RUNTIME_ROLE_ARN": chat_role_arn},
            reserved=10,
        )
        # Chat budget rows only: the shared limits (CHAT), one row per turn's hold (CHATTURN#<id>)
        # and the shared and per-user daily ledgers (BUDGET#chat, BUDGET#chat-user-<hash>). Nothing
        # else in the ops table (P2-029).
        self.only_partitions(
            usage, ["CHAT", "CHATTURN#*", "BUDGET#chat", "BUDGET#chat-user-*"]
        )

        self.agent_tools = tools
        self.chat_usage = usage
        self.agent_tools_url = tools.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM
        ).url
        self.chat_usage_url = usage.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM
        ).url

    def build(
        self,
        module_runtime_arn: str,
        module_role_arn: str,
        chat_runtime_arn: str,
        chat_role_arn: str,
        site_url: str,
        cognito_domain: str,
        web_client_id: str,
        identity_provider: str | None = None,
        zones: Mapping[str, route53.IHostedZone] | None = None,
    ) -> None:
        """``zones``: the hosted zones the stack made, where a digest SES domain's DNS records go
        when its config names no zone."""
        data, search = self._data, self._search
        kb_arn = search.kb_arn

        # Run workflow: invoke (waits for the runtime's task token) -> finalize.
        invoke = self.function(
            "Invoke",
            "insights_radar.api.workers.invoke",
            "Starts a module run session on the module runtime",
            timeout=60,
            reserved=2,
        )
        # When the kill switch is on or AgentCore refuses the start, Invoke itself finalizes the run as cancelled
        # (workers.invoke): it writes the run record (MODULE#<slug>, RUN#<id>) and gives the hold back to the module's
        # ledger (BUDGET#<slug>). With a read-only grant that write was denied and only logged, and the run stayed
        # open with its hold charged for the rest of the UTC day. It reads the kill switch (ANDON) and nothing else.
        self.only_partitions(invoke, ["MODULE#*", "BUDGET#*"], read_only=["ANDON"])
        # BUDGET#* would also match the chat ledgers (module slugs cannot be "chat" or start "chat-": Slug.RESERVED).
        invoke.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=OPS_ITEM_ACTIONS,
                resources=[
                    data.ops_table.table_arn,
                    f"{data.ops_table.table_arn}/index/*",
                ],
                conditions={
                    "ForAnyValue:StringLike": {
                        "dynamodb:LeadingKeys": ["BUDGET#chat", "BUDGET#chat-*"]
                    }
                },
            )
        )
        self._allow(
            invoke,
            ["bedrock-agentcore:InvokeAgentRuntime"],
            [module_runtime_arn, f"{module_runtime_arn}/*"],
        )

        finalize = self.function(
            "Finalize",
            "insights_radar.api.workers.finalize",
            "Records a run's outcome and charges its budget",
            reserved=2,
        )
        data.ops_table.grant_read_write_data(finalize)
        # Finalize removes the entities a run that ended early created and left unlinked (code review r50:
        # without the graph table, every removal failed with AccessDenied and was only logged).
        data.graph_table.grant_read_write_data(finalize)

        start = tasks.LambdaInvoke(
            self,
            "RunModule",
            lambda_function=invoke,
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload=sfn.TaskInput.from_object(
                {
                    "payload": sfn.JsonPath.entire_payload,
                    "task_token": sfn.JsonPath.task_token,
                    "runtime_arn": module_runtime_arn,
                }
            ),
            heartbeat_timeout=sfn.Timeout.duration(Duration.minutes(5)),
            task_timeout=sfn.Timeout.duration(Duration.hours(8)),
            result_path="$.result",
        )

        # A throttled Invoke never ran (with reservedConcurrency=true only 2 can run at once, and the sweep
        # starts every due module together), so it is retried rather than recorded as a failed run. CDK's
        # default service-exception retry does not cover TooManyRequests.
        start.add_retry(
            errors=["Lambda.TooManyRequestsException"],
            max_attempts=8,
            backoff_rate=2,
            interval=Duration.seconds(2),
            max_delay=Duration.seconds(60),
            jitter_strategy=sfn.JitterType.FULL,
        )

        def finalize_step(name: str, outcome: dict[str, object]) -> tasks.LambdaInvoke:
            step = tasks.LambdaInvoke(
                self,
                name,
                lambda_function=finalize,
                payload=sfn.TaskInput.from_object(
                    {
                        "module": sfn.JsonPath.string_at("$.module.slug"),
                        "run_id": sfn.JsonPath.string_at("$.run_id"),
                        **outcome,
                    }
                ),
                payload_response_only=True,
                result_path=sfn.JsonPath.DISCARD,
            )
            # Finalize is idempotent (a run already recorded is returned as it is), so every failure is
            # retried: a throttled table or a lost race must not leave the run "running" for hours.
            step.add_retry(
                errors=FINALIZE_RETRY_ERRORS,
                max_attempts=6,
                backoff_rate=2,
                interval=Duration.seconds(2),
                max_delay=Duration.seconds(60),
                jitter_strategy=sfn.JitterType.FULL,
            )
            return step

        ok = finalize_step(
            "FinalizeSucceeded", {"result": sfn.JsonPath.object_at("$.result")}
        )
        failed = finalize_step(
            "FinalizeFailed", {"error": sfn.JsonPath.object_at("$.error")}
        )
        # The andon pulled before the run started: Invoke records it cancelled and fails the task with
        # GenAiDisabled. Ending here, not in RunErrored, keeps InvokeErrors and RunsFailed quiet for
        # every queued run during an incident (CTR HC-011 follow-up).
        start.add_catch(
            sfn.Succeed(
                self, "NotStartedGenAiOff", comment="The GenAI kill switch was on"
            ),
            errors=["GenAiDisabled"],
            result_path="$.error",
        )
        start.add_catch(failed, errors=["States.ALL"], result_path="$.error")
        # A success that cannot be recorded is recorded as a failure instead, so the run never stays open.
        ok.add_catch(failed, errors=["States.ALL"], result_path="$.error")
        # A failed run fails its execution, so the RunsFailed and RunsTimedOut alarms see it: both a
        # workflow error (caught above, after it is recorded) and a run the agent reported as failed.
        failed.next(
            sfn.Fail(
                self,
                "RunErrored",
                error="RunErrored",
                cause="The run failed; see the run record",
            )
        )
        outcome = (
            sfn.Choice(self, "RunOutcome")
            .when(
                sfn.Condition.and_(
                    sfn.Condition.is_present("$.result.status"),
                    sfn.Condition.string_equals("$.result.status", "failed"),
                ),
                sfn.Fail(
                    self,
                    "RunFailed",
                    error="RunFailed",
                    cause="The module reported a failed run",
                ),
            )
            .otherwise(sfn.Succeed(self, "RunDone"))
        )
        self.run_logs = sfn_logs = logs.LogGroup(
            self,
            "RunLogs",
            retention=self._retention,
            encryption_key=data.key,
            removal_policy=data.removal,
        )
        self.state_machine = sfn.StateMachine(
            self,
            "Runs",
            state_machine_name=f"{self._config.prefix}-runs",
            definition_body=sfn.DefinitionBody.from_chainable(
                start.next(ok).next(outcome)
            ),
            timeout=Duration.hours(9),
            tracing_enabled=True,
            # The execution history is left on the AWS-owned key on purpose (P2-019 partly). CDK's
            # customer-managed path adds an unconditioned delivery.logs.amazonaws.com kms:Decrypt*
            # statement to the data key, which is the confused-deputy surface P2-074 is about, and
            # the history holds no personal data: with include_execution_data=False below, what is
            # left is run metadata and an 8-hour run token that is useless without the runtime role.
            # Execution data stays out of the logs: the run input carries the run token.
            logs=sfn.LogOptions(
                destination=sfn_logs,
                level=sfn.LogLevel.ALL,
                include_execution_data=False,
            ),
        )
        # A standalone policy: the state machine references Invoke, so Invoke's own default policy must
        # not reference the state machine (a cycle).
        iam.Policy(
            self,
            "InvokeTaskFailure",
            roles=[
                cast(iam.IRole, invoke.role)
            ],  # a Function made here always has a role
            statements=[
                iam.PolicyStatement(
                    actions=["states:SendTaskFailure"],
                    resources=[self.state_machine.state_machine_arn],
                )
            ],
        )

        # Asynchronous work that fails after Lambda's retries lands here instead of disappearing.
        self.failed_events = sqs.Queue(
            self,
            "FailedEvents",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=data.key,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )

        # Documentation crawler: async, minutes per product.
        crawler = self.function(
            "Crawler",
            "insights_radar.api.workers.crawl",
            "Crawls a product's documentation into the knowledge base",
            timeout=900,
            dlq=self.failed_events,
        )
        # It parses untrusted documentation pages: its product's rows and the owed-ingestion marker only.
        self.only_partitions(crawler, ["PRODUCT#*", "KB#INGESTION"])
        # The crawler only writes and prunes crawled pages under docs/.
        data.assets.grant_read_write(crawler, f"{DOCS_PREFIX}*")
        data.assets.grant_delete(crawler, f"{DOCS_PREFIX}*")
        self._no_version_deletes(crawler, f"{DOCS_PREFIX}*")
        self._allow(crawler, ["bedrock:StartIngestionJob"], [kb_arn])
        crawler_env = {"AIR_CRAWLER_FUNCTION": crawler.function_name}

        # Source watcher: checks every Scout source for new items without a model (modules/watch.py). Up to 16
        # fetches at a time, 20 s each at most: a few hundred sources take a minute or two of the 15.
        watch = self.function(
            "Watch",
            "insights_radar.api.workers.watch",
            "Checks the Scout's sources for new items on a schedule",
            timeout=900,
            dlq=self.failed_events,
            reserved=1,
        )
        # The modules index and definitions with their sources (read) and the watch checkpoints (read and write):
        # it parses untrusted pages, so it cannot change a definition (whose grants and MCP addresses it can read)
        # and cannot touch the kill switch, budgets or audit rows.
        self.only_partitions(watch, ["WATCH#*"], read_only=["MODULES", "MODULE#*"])
        self.watch_function = watch

        # The deploy option mcpIssuer/mcpAudience: the role an MCP client assumes with its own id token.
        self.mcp_role = self._mcp_role() if self._config.mcp_web_identity else None

        # The API.
        api = self.function(
            "ApiHandler",
            "insights_radar.api.app.handler",
            "Agentic Insights Radar HTTP API",
            timeout=29,
            memory=1536,
            # No runtime role ARNs: the agent tool and chat usage gates live on their own function URLs,
            # and without the ARNs this function refuses /agent/* even if a route to it is re-added.
            env={
                "AIR_STATE_MACHINE_ARN": self.state_machine.state_machine_arn,
                "AIR_ALLOWED_ORIGIN": site_url,
                "AIR_CHAT_RUNTIME_ARN": chat_runtime_arn,
                "AIR_USER_POOL_CLIENT_ID": web_client_id,
                "AIR_COGNITO_DOMAIN": cognito_domain,
                **(
                    {"AIR_IDENTITY_PROVIDER": identity_provider}
                    if identity_provider
                    else {}
                ),
                **(
                    {"AIR_TRUST_PROVIDER_EMAIL": "true"}
                    if identity_provider and self._config.oidc_trust_email
                    else {}
                ),
                "AIR_WORKSPACE_RATE_PER_MINUTE": str(
                    self._config.workspace_rate_per_minute
                ),
                **crawler_env,
                "AIR_WATCH_FUNCTION": watch.function_name,
                **(
                    {
                        "AIR_MCP_ROLE_ARN": self.mcp_role.role_arn,
                        "AIR_MCP_ISSUER": self._config.mcp_issuer or "",
                        "AIR_MCP_AUDIENCE": self._config.mcp_audience or "",
                    }
                    if self.mcp_role is not None
                    else {}
                ),
            },
        )
        self._allow(
            api, ["lambda:InvokeFunction"], [crawler.function_arn, watch.function_arn]
        )  # unqualified only
        data.graph_table.grant_read_write_data(api)
        data.ops_table.grant_read_write_data(api)
        data.assets.grant_read_write(api)
        data.assets.grant_delete(api)
        self._no_version_deletes(api)
        self.api_function = api
        self._vectors(api, ["s3vectors:QueryVectors", "s3vectors:GetVectors"])
        self._allow(api, ["bedrock:InvokeModel"], [search.embed_model_arn])
        self._allow(api, KB_ACTIONS, [kb_arn])
        self._allow(api, ["kms:GenerateMac", "kms:VerifyMac"], [data.run_key.key_arn])
        self.state_machine.grant_start_execution(api)
        # An administrator's Cancel stops the run's execution (on this state machine's executions only).
        self.state_machine.grant_execution(api, "states:StopExecution")
        # Digest delivery (Slack, email): nothing at all unless digestDelivery turns a channel on.
        self.digest: Digest | None = None
        if self._config.digest.enabled:
            self.digest = Digest(
                self,
                "Digest",
                self._config.digest,
                api,
                self.failed_events,
                site_url,
                zones or {},
            )
        self._allow(
            api,
            COGNITO_ADMIN_ACTIONS,
            [self.user_pool_arn],
        )

        jwt = authorizers.HttpJwtAuthorizer(
            "Cognito", self._issuer, jwt_audience=self._audiences
        )
        target = integrations.HttpLambdaIntegration("Lambda", api)
        self.http_api.add_routes(
            path="/api/config", methods=[apigw.HttpMethod.GET], integration=target
        )
        self.http_api.add_routes(
            path="/api/{proxy+}",
            methods=[apigw.HttpMethod.ANY],
            integration=target,
            authorizer=jwt,
        )
        if self.mcp_role is not None:
            # The same routes for an MCP client that signs with SigV4 (a route has one authorizer). The
            # function still checks the caller is a session of this role: HTTP APIs have no resource
            # policy, so any account's signature passes the IAM authorizer.
            self.http_api.add_routes(
                path=f"{IAM_PREFIX}/api/{{proxy+}}",
                methods=[apigw.HttpMethod.ANY],
                integration=target,
                authorizer=authorizers.HttpIamAuthorizer(),
            )
            self.mcp_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["execute-api:Invoke"],
                    resources=[
                        f"arn:{Aws.PARTITION}:execute-api:{Aws.REGION}:{Aws.ACCOUNT_ID}:{self.http_api.api_id}/*/*/iam/api/*"
                    ],
                )
            )
        # The digest emails' one-click unsubscribe (RFC 8058: the mail client posts with no session). The
        # link's HMAC token names one subscription, which is all it can turn off; GET only shows a page.
        public_routes = (
            self.http_api.add_routes(
                path=UNSUBSCRIBE_PATH,
                methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST],
                integration=target,
            )
            if self.digest is not None and self._config.digest.email
            else []
        )
        # Routes that embed or retrieve with a model on every request get a lower limit of their own, so
        # one script cannot spend the whole stage allowance (or run up embedding cost) on them.
        # The MCP role's twins of these routes get the same limit (its catch-all route would otherwise give
        # them only the stage limit, and the client calls API Gateway directly, past CloudFront's WAF).
        model_routes: list[tuple[str, str, apigw.IHttpRouteAuthorizer]] = [
            (path, method, jwt) for path, method in MODEL_BACKED_ROUTES
        ]
        if self.mcp_role is not None:
            model_routes += [
                (f"{IAM_PREFIX}{path}", method, authorizers.HttpIamAuthorizer())
                for path, method in MODEL_BACKED_ROUTES
            ]
        costly_routes = [
            route
            for path, method, authorizer in model_routes
            for route in self.http_api.add_routes(
                path=path,
                methods=[apigw.HttpMethod[method]],
                integration=target,
                authorizer=authorizer,
            )
        ]
        access_logs = logs.LogGroup(
            self,
            "ApiAccessLogs",
            retention=self._retention,
            encryption_key=data.key,
            removal_policy=data.removal,
        )
        stage = apigw.HttpStage(
            self,
            "Stage",
            http_api=self.http_api,
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=50, burst_limit=100),
            access_log_settings=_AccessLogs(apigw.LogGroupLogDestination(access_logs)),
        )
        cfn_stage = cast(apigw.CfnStage, stage.node.default_child)
        cfn_stage.route_settings = {
            f"{method} {path}": {
                "ThrottlingRateLimit": MODEL_ROUTE_RATE,
                "ThrottlingBurstLimit": MODEL_ROUTE_BURST,
            }
            for path, method, _authorizer in model_routes
        } | {
            f"{method} {UNSUBSCRIBE_PATH}": {
                "ThrottlingRateLimit": UNSUBSCRIBE_RATE,
                "ThrottlingBurstLimit": UNSUBSCRIBE_BURST,
            }
            for method in ("GET", "POST")
            if public_routes
        }
        for route in [
            *costly_routes,
            *public_routes,
        ]:  # RouteSettings may only name a route that already exists
            stage.node.add_dependency(route)
        self.stage = stage

        # Scheduler: dispatch due modules, autopilot radars, refresh ingestion status.
        sweep = self.function(
            "Sweep",
            "insights_radar.api.workers.sweep",
            "Every 5 minutes: dispatch due modules, radar autopilot, ingestion status",
            timeout=120,
            env={
                "AIR_STATE_MACHINE_ARN": self.state_machine.state_machine_arn,
                **crawler_env,
            },
            dlq=self.failed_events,
            reserved=1,
        )
        self._allow(sweep, ["lambda:InvokeFunction"], [crawler.function_arn])
        data.ops_table.grant_read_write_data(sweep)
        # Read and write: the sweep deletes the nodes an administrator queued for deletion (a node with more
        # links than one request removes), which a read-only grant refused, so a queued delete never finished.
        data.graph_table.grant_read_write_data(sweep)
        # Radar autopilot only adds new radar versions: read and put, never delete, so a bug in the
        # sweep cannot remove a radar's version history (P2-031).
        data.assets.grant_read(sweep, "radars/*")
        data.assets.grant_put(sweep, "radars/*")
        self._no_version_deletes(sweep, "radars/*")
        # A queued delete of a content idea takes its drafts' markdown with it (graph/moderation.py NodeRemoval):
        # delete on drafts/* only, never a version (code review r51). Radar versions stay put-only (P2-031).
        data.assets.grant_delete(sweep, "drafts/*")
        self._no_version_deletes(sweep, "drafts/*")
        # An upload that was never completed leaves its object under docs/, where the knowledge base syncs from,
        # without the size and type check complete_upload makes: the sweep removes it once the form has
        # expired (ProductService.sweep_expired_uploads). Without this grant every delete was AccessDenied and
        # only logged. Delete, never a version, so a recovery from versioning still works.
        data.assets.grant_delete(sweep, "docs/*")
        self._no_version_deletes(sweep, "docs/*")
        self.state_machine.grant_start_execution(sweep)
        self._allow(sweep, ["kms:GenerateMac"], [data.run_key.key_arn])
        self._allow(
            sweep, ["bedrock:ListIngestionJobs", "bedrock:StartIngestionJob"], [kb_arn]
        )
        rule_name = f"{self._config.prefix}-sweep"
        events.Rule(
            self,
            "Every5Minutes",
            rule_name=rule_name,
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[
                targets.LambdaFunction(
                    sweep, retry_attempts=0, dead_letter_queue=self.failed_events
                )
            ],
        )
        watch_rule_name = f"{self._config.prefix}-watch"
        events.Rule(
            self,
            "WatchSources",
            rule_name=watch_rule_name,
            schedule=events.Schedule.rate(Duration.minutes(WATCH_INTERVAL_MINUTES)),
            targets=[
                targets.LambdaFunction(
                    watch, retry_attempts=0, dead_letter_queue=self.failed_events
                )
            ],
        )
        # The queue is KMS-encrypted: its writers need the data key, EventBridge only for these rules.
        rule_names = [rule_name, watch_rule_name]
        writers = [crawler, sweep, watch]
        if self.digest is not None:
            rule_names.append(self.digest.rule_name)
            writers += [fn for fn in (self.digest.schedule, self.digest.feedback) if fn]
        for writer in writers:
            data.key.grant(writer, "kms:GenerateDataKey", "kms:Decrypt")
        data.key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowSweepRuleDeadLetters",
                principals=[iam.ServicePrincipal("events.amazonaws.com")],
                actions=["kms:GenerateDataKey", "kms:Decrypt"],
                resources=["*"],
                # A literal ARN: referencing the rule would make the key depend on the function it encrypts.
                conditions={
                    "ArnEquals": {
                        "aws:SourceArn": [
                            f"arn:{Aws.PARTITION}:events:{Aws.REGION}:{Aws.ACCOUNT_ID}:rule/{name}"
                            for name in rule_names
                        ]
                    }
                },
            )
        )

        # Graph -> vector index, from the table stream.
        index = self.function(
            "Indexer",
            "insights_radar.api.workers.index",
            "Keeps the graph vector index in step with the graph table; invoked by hand, re-indexes",
            # A stream batch takes seconds; a hand-invoked reindex page of 500 nodes takes minutes.
            timeout=900,
        )
        self._vectors(index, ["s3vectors:PutVectors", "s3vectors:DeleteVectors"])
        # The reindex reads the nodes (and records the stream's failures) itself (CTR HC-074, HC-106).
        data.graph_table.grant_read_data(index)
        data.ops_table.grant_read_write_data(index)
        self._allow(index, ["bedrock:InvokeModel"], [search.embed_model_arn])
        dlq = sqs.Queue(
            self,
            "IndexerDlq",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=data.key,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        index.add_event_source(
            sources.DynamoEventSource(
                data.graph_table,
                starting_position=lambda_.StartingPosition.TRIM_HORIZON,
                batch_size=50,
                max_batching_window=Duration.seconds(5),
                bisect_batch_on_error=True,
                report_batch_item_failures=True,
                retry_attempts=5,
                on_failure=sources.SqsDlq(dlq),
            )
        )
        self.indexer_dlq = dlq
        self.indexer = index

        # Seed built-in modules on every release (never overwrites edits), and run the release's
        # one-time data migrations: the graph's list index and entity name keys, and the working-on
        # marks' by-user index (CR2-02, CR2-11, CR2-12). Those scan both tables, hence the graph grant
        # and Lambda's longest timeout: at 300 s a graph of tens of thousands of nodes timed the seed out
        # and rolled the whole update back (P3-013). Moving the migrations out of the deploy, resumable,
        # is a backend change still to come.
        seed_env: dict[str, str] = {}
        bundle: s3_assets.Asset | None = None
        if self._config.seed:
            path = ROOT / "deployment" / "seeds" / self._config.seed / "bundle.json"
            if not path.is_file():
                raise ValueError(
                    f"seed {self._config.seed!r}: {path.relative_to(ROOT)} does not exist"
                )
            # Uploaded with the stack's other assets; a changed bundle changes the key, so the Seed runs again.
            bundle = s3_assets.Asset(self, "SeedBundle", path=str(path))
            seed_env = {
                "AIR_SEED_BUNDLE_BUCKET": bundle.s3_bucket_name,
                "AIR_SEED_BUNDLE_KEY": bundle.s3_object_key,
            }
        seed = self.function(
            "Seed",
            "insights_radar.api.workers.seed",
            "Creates missing built-in modules, applies the seed bundle and migrates stored data for a release",
            timeout=SEED_TIMEOUT_SECONDS,
            env=seed_env,
        )
        if bundle is not None:
            # The bundle object only: the asset bucket is the account's shared CDK bootstrap bucket, which
            # holds every CDK app's assets (grant_read would open all of it).
            seed.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=[bundle.bucket.arn_for_objects(bundle.s3_object_key)],
                )
            )
        data.ops_table.grant_read_write_data(seed)
        data.graph_table.grant_read_write_data(seed)
        trigger = triggers.Trigger(
            self,
            "SeedOnDeploy",
            handler=seed,
            execute_on_handler_change=True,
            timeout=Duration.seconds(SEED_TIMEOUT_SECONDS),
        )
        trigger.execute_after(data.ops_table, data.graph_table)
        self._seed = trigger

    def seed_last(self, *others: Construct) -> None:
        """Run the Seed after every other resource of the stack (``others``, and the rest of this
        construct) is created or updated (r3 P3-003). Its data migrations cannot be rolled back: run
        mid-update, a later resource's failure rolled the code back over migrated data, and the
        previous release's seed then undid part of it. Last, any other resource's failure happens
        before the seed runs. (A failure after it, in the stack's own cleanup, can still roll back:
        the seed therefore re-checks each module rather than trusting its change record.)"""
        # An imported resource (an existing OIDC provider, deploy option mcpProviderArn) is a child but no
        # construct: there is nothing of it to wait for.
        mine = [
            c
            for c in self.node.children
            if c is not self._seed and isinstance(c, Construct)
        ]
        self._seed.execute_after(*mine, *others)


def function_url_access(fn: lambda_.IFunction) -> list[iam.PolicyStatement]:
    """What a principal in this account needs to call ``fn`` through its IAM-authenticated URL, and only so."""
    return [
        iam.PolicyStatement(
            actions=["lambda:InvokeFunctionUrl"],
            resources=[fn.function_arn],
            conditions={"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"}},
        ),
        iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[fn.function_arn],
            conditions={"Bool": {"lambda:InvokedViaFunctionUrl": "true"}},
        ),
    ]
