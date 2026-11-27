# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Alarms on the things an operator must act on, sent to an encrypted SNS topic, a dashboard, and an
optional AWS Budget.

Thresholds are chosen so that one recurring failure is seen: modules run every few hours, so a module
that fails on every run produces one failed execution at a time, never three in a quarter hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aws_cdk import Aws, Duration, Fn, Stack
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as actions
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_stepfunctions as sfn
from constructs import Construct

from insights_radar_infra.config import DeployConfig

NAMESPACE = "AgenticInsightsRadar"
# Functions whose single failure leaves a run unrecorded or unstarted: alarm on the first error.
BOOKKEEPING = ("Invoke", "Finalize")
INDEXER_ITERATOR_AGE_MS = (
    3_600_000  # an hour behind; the graph stream keeps records for 24 hours
)
# AgentCore Runtime concurrent sessions. Two alarms, because they answer different questions:
# - per runtime (``Sessions`` with the runtime's Name/Operation/Resource): this deployment's own load,
#   which other agents in a shared account neither trip nor hide (P2-025). AgentCore publishes no
#   ``SessionCount``: the alarm on it could never fire (r3 P3-005).
# - account-wide (``ActiveSessionCount``, Service=AgentCore.Runtime): the quota, "active session
#   workloads per account", which every agent in the account shares, so another team's load can
#   exhaust it for this deployment's runs and chats.
RUNTIME_SESSIONS = 80
ACCOUNT_SESSION_QUOTA = {
    "us-east-1": 5000,
    "us-west-2": 5000,
}  # the default; 2,500 elsewhere
OTHER_REGION_SESSION_QUOTA = 2500
QUOTA_ALARM_PERCENT = 80
# A function URL answers its own errors (the handler turns every exception into an HTTP response), so
# the Lambda Errors metric of a tool gate stays at 0. These are the only signals that every agent tool
# call is failing while the runs themselves still end "succeeded" (P2-006).
URL_4XX_PERCENT = 50
URL_MIN_REQUESTS = 20
CDN_MIN_REQUESTS = 50
GATEWAY_ERRORS = 5  # web-search gateway system errors in five minutes
API_4XX_PERCENT = 50
API_MIN_REQUESTS = 50
EDGE_REGION = (
    "us-east-1"  # CloudFront and CLOUDFRONT-scope WAF publish their metrics here only
)
CLOUDFRONT_5XX_PERCENT = 5
DIGEST_LOOKUP_MISSES_PER_DAY = 1


@dataclass(frozen=True)
class Watched:
    """What the alarms and the dashboard look at."""

    functions: list[lambda_.Function]
    runs: sfn.StateMachine
    run_logs: logs.ILogGroup
    dlq: sqs.Queue
    failed_events: sqs.Queue
    api: apigw.HttpApi
    indexer: lambda_.Function
    runtimes: list[agentcore.Runtime]
    # Reached over their own IAM-authenticated function URLs, never the HTTP API.
    url_functions: list[lambda_.Function]
    distribution: cloudfront.IDistribution
    # The web ACL's ARN: its name, the WebACL dimension of the AWS/WAFV2 metrics, is the ARN's
    # third path part (``.../global/webacl/<name>/<id>``).
    web_acl_arn: str
    api_logs: logs.ILogGroup
    # The web-search gateway (webSearch=true): its errors are invisible to every other alarm (P3-031).
    gateway: agentcore.Gateway | None = None
    # Digest delivery (digestDelivery on): its dead-letter queue, and the functions whose every Lambda
    # error is a timeout or a crash (digest_send hands a transient failure back to the queue as a batch
    # item failure, so those reach the DLQ alarm after three receives, not this one). A permanent fault on
    # the deployment's side (a revoked token, a lost scope, an unverified SES identity) returns normally:
    # the sender's log counts it (scan cycles 6 and 7).
    digest_dlq: sqs.Queue | None = None
    digest_functions: list[lambda_.Function] = field(default_factory=list)
    digest_sender_logs: logs.ILogGroup | None = None
    digest_scheduler_logs: logs.ILogGroup | None = None
    digest_email: bool = False


class Monitoring(Construct):
    def __init__(
        self, scope: Construct, cid: str, key: kms.Key, config: DeployConfig
    ) -> None:
        super().__init__(scope, cid)
        self._prefix = config.prefix
        self.topic = sns.Topic(self, "Alarms", master_key=key, enforce_ssl=True)
        # CloudWatch publishes alarm notifications to the encrypted topic, so it needs the key; only for
        # alarms in this account.
        key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudWatchAlarms",
                principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com")],
                actions=["kms:Decrypt", "kms:GenerateDataKey"],
                resources=["*"],
                conditions={
                    "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:{Aws.PARTITION}:cloudwatch:{Aws.REGION}:{Aws.ACCOUNT_ID}:alarm:*"
                    },
                },
            )
        )
        # Someone is always told: the alarm email, or the first administrator when none is given.
        self.topic.add_subscription(subs.EmailSubscription(config.alarm_address))
        self._action = actions.SnsAction(self.topic)
        if config.monthly_budget_usd:
            self._budget(key, config.monthly_budget_usd)

    def _budget(self, key: kms.Key, usd: int) -> None:
        """Two monthly cost budgets at ``usd``, each alerting the topic at 80% actual and 100% forecast.

        * ``<prefix>-insights-radar-<region>``: this deployment's tagged spend (``deployment=<prefix>``).
        * ``<prefix>-insights-radar-<region>-account``: the whole account's spend (P3-009). Model
          invocations through the system-defined ``global.`` inference profiles carry no resource tag,
          so the tag budget never sees the largest line of the bill (about USD 239 of 300 a month in
          the cost estimate); only an unfiltered budget catches a runaway module's token spend.
        """
        region = Stack.of(self).region
        names = [
            f"{self._prefix}-insights-radar-{region}",
            f"{self._prefix}-insights-radar-{region}-account",
        ]
        # This deployment's own budgets, not every budget in the account (P2-074).
        only_these_budgets = {
            "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
            "ArnEquals": {
                "aws:SourceArn": [
                    f"arn:{Aws.PARTITION}:budgets::{Aws.ACCOUNT_ID}:budget/{n}"
                    for n in names
                ]
            },
        }
        key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowBudgetNotifications",
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                actions=["kms:Decrypt", "kms:GenerateDataKey"],
                resources=["*"],
                conditions=only_these_budgets,
            )
        )
        self.topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowBudgetNotifications",
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                actions=["sns:Publish"],
                resources=[self.topic.topic_arn],
                conditions=only_these_budgets,
            )
        )
        subscriber = budgets.CfnBudget.SubscriberProperty(
            subscription_type="SNS", address=self.topic.topic_arn
        )
        notifications = [
            budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type=kind,
                    comparison_operator="GREATER_THAN",
                    threshold=threshold,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=[subscriber],
            )
            for kind, threshold in (("ACTUAL", 80), ("FORECASTED", 100))
        ]
        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=names[0],
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=usd, unit="USD"),
                # This deployment's spend only: every resource carries deployment=<prefix> (stack.py).
                # Activate "deployment" as a cost allocation tag in Billing and Cost Management, or the
                # filter matches nothing and the budget reports 0 (see the implementation guide).
                cost_filters={"TagKeyValue": [f"user:deployment${self._prefix}"]},
            ),
            notifications_with_subscribers=notifications,
        )
        budgets.CfnBudget(
            self,
            "AccountBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=names[1],
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=usd, unit="USD"),
            ),
            notifications_with_subscribers=notifications,
        )

    def _alarm(
        self, cid: str, metric: cw.IMetric, description: str, threshold: float = 1
    ) -> cw.Alarm:
        alarm = cw.Alarm(
            self,
            cid,
            metric=metric,
            threshold=threshold,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description=description,
        )
        alarm.add_alarm_action(self._action)
        return alarm

    def watch(self, w: Watched) -> None:
        five = Duration.minutes(5)
        for fn in w.functions:
            single = fn.node.id in BOOKKEEPING
            self._alarm(
                f"{fn.node.id}Errors",
                fn.metric_errors(period=five, statistic="Sum"),
                f"{fn.node.id} function errors",
                threshold=1 if single or fn in w.digest_functions else 3,
            )
            self._alarm(
                f"{fn.node.id}Throttles",
                fn.metric_throttles(period=five, statistic="Sum"),
                f"{fn.node.id} function throttles",
                threshold=1 if fn is w.indexer or single else 5,
            )
        self._alarm(
            "RunsFailed",
            w.runs.metric_failed(period=Duration.minutes(15), statistic="Sum"),
            "A module run failed in the workflow",
        )
        # A task or heartbeat timeout is caught and ends the execution as failed, so the execution-level
        # timed-out metric almost never moves: count the timeout events in the workflow's own log.
        # The namespace carries the prefix: a metric filter cannot set a static dimension, and a
        # dimensionless metric in a shared namespace counts every deployment's timeouts (P2-020).
        timeouts = logs.MetricFilter(
            self,
            "RunTimeoutsFilter",
            log_group=w.run_logs,
            metric_namespace=f"{NAMESPACE}/{self._prefix}",
            metric_name="RunTimeouts",
            filter_pattern=logs.FilterPattern.any(
                logs.FilterPattern.string_value("$.type", "=", "TaskTimedOut"),
                logs.FilterPattern.string_value("$.type", "=", "ExecutionTimedOut"),
            ),
            metric_value="1",
        )
        self._alarm(
            "RunsTimedOut",
            timeouts.metric(period=Duration.minutes(15), statistic="Sum"),
            "A module run timed out (no heartbeat for 5 minutes, or past its 8-hour limit)",
        )
        # The API logs rate.workspace_limit when the deployment-wide ceiling refuses a call: a few
        # viewers can hold it there for everyone, so it is an alarm, not just a 429 (SR2-11).
        workspace = logs.MetricFilter(
            self,
            "WorkspaceRateLimitedFilter",
            log_group=w.api_logs,
            metric_namespace=f"{NAMESPACE}/{self._prefix}",
            metric_name="WorkspaceRateLimited",
            # A text log line, not a JSON event: the quoted term matches it (scan cycle 7).
            filter_pattern=logs.FilterPattern.all_terms("rate.workspace_limit"),
            metric_value="1",
        )
        self._alarm(
            "WorkspaceRateLimited",
            workspace.metric(period=five, statistic="Sum"),
            "The workspace-wide rate ceiling refused calls: raise workspaceRatePerMinute or find the caller",
        )
        self._alarm(
            "IndexerDlq",
            w.dlq.metric_approximate_number_of_messages_visible(
                period=five, statistic="Max"
            ),
            "Graph changes failed to index into vector search",
        )
        self._alarm(
            "IndexerIteratorAge",
            w.indexer.metric("IteratorAge", period=five, statistic="Maximum"),
            "The indexer is over an hour behind the graph stream, which keeps changes for 24 hours",
            threshold=INDEXER_ITERATOR_AGE_MS,
        )
        self._alarm(
            "FailedEvents",
            w.failed_events.metric_approximate_number_of_messages_visible(
                period=five, statistic="Max"
            ),
            "A documentation crawl or a scheduled sweep failed after its retries",
        )
        if w.digest_dlq is not None:
            self._alarm(
                "DigestDlq",
                w.digest_dlq.metric_approximate_number_of_messages_visible(
                    period=five, statistic="Max"
                ),
                "A digest delivery failed three times and was set aside in the digest dead-letter queue. After fixing "
                "the cause, a redriven message for a time a later send already covered sends nothing: its items went "
                "out in that later digest. If every send of one channel lands here, its secret may be unreadable or"
                " deleted: the digestDelivery row of docs/implementation-guide.md says how to bring it back",
            )
        if w.digest_sender_logs is not None:
            faults = logs.MetricFilter(
                self,
                "DigestDeploymentFaultsFilter",
                log_group=w.digest_sender_logs,
                metric_namespace=f"{NAMESPACE}/{self._prefix}",
                metric_name="DigestDeploymentFaults",
                # Lambda's Python log lines are text ("[WARNING]\t<time>\t<id>\t{...}"), not JSON
                # events, so a $.event selector never matches: the quoted term does (scan cycle 7).
                filter_pattern=logs.FilterPattern.all_terms("digest.deployment_fault"),
                metric_value="1",
            )
            # An unusable unsubscribe key fails every unsubscribe link: the API logs it, and it raises the same
            # alarm as a deployment fault in the sender, at the first one (scan cycle 43).
            logs.MetricFilter(
                self,
                "DigestUnsubscribeKeyFilter",
                log_group=w.api_logs,
                metric_namespace=f"{NAMESPACE}/{self._prefix}",
                metric_name="DigestDeploymentFaults",
                filter_pattern=logs.FilterPattern.all_terms("unsubscribe.key_unusable"),
                metric_value="1",
            )
            # Any Slack user not found alarms that day: a bot in the wrong workspace or a broken directory lookup then
            # alarms on a deployment of any size, days before LOOKUP_MISS_LIMIT switches anyone off. One person
            # without Slack keeps it raised until their delivery is switched off after that limit (scan cycle 11).
            misses = logs.MetricFilter(
                self,
                "DigestLookupMissesFilter",
                log_group=w.digest_sender_logs,
                metric_namespace=f"{NAMESPACE}/{self._prefix}",
                metric_name="DigestLookupMisses",
                filter_pattern=logs.FilterPattern.all_terms("digest.lookup_miss"),
                metric_value="1",
            )
            self._alarm(
                "DigestLookupMisses",
                misses.metric(period=Duration.days(1), statistic="Sum"),
                "A Slack user could not be found today: one person without Slack, or, if it is everyone, the bot's"
                " workspace or the directory endpoint (deliveries that find no one are switched off after 7 sends)",
                threshold=DIGEST_LOOKUP_MISSES_PER_DAY,
            )
            # A queue or cursor write that keeps failing stops every digest, or repeats one, with no Lambda
            # error: the scheduler and the sender log them (scan cycle 9).
            filters = (
                (
                    "DigestSchedulerFaultsFilter",
                    w.digest_scheduler_logs,
                    logs.FilterPattern.any_term(
                        "digest.claim_failed",
                        "digest.defer_failed",
                        "digest.enqueue_failed",
                        "digest.unclaim_failed",
                    ),
                ),
                (
                    "DigestCursorFaultsFilter",
                    w.digest_sender_logs,
                    logs.FilterPattern.all_terms("digest.advance_failed"),
                ),
            )
            # Both publish the one metric the alarm reads (scan cycle 10); a function that is not deployed has no
            # log group to filter (scan cycle 42: one loop with no untaken branch).
            for cid, group, pattern in [
                (c, g, f) for c, g, f in filters if g is not None
            ]:
                logs.MetricFilter(
                    self,
                    cid,
                    log_group=group,
                    metric_namespace=f"{NAMESPACE}/{self._prefix}",
                    metric_name="DigestQueueFaults",
                    filter_pattern=pattern,
                    metric_value="1",
                )
            self._alarm(
                "DigestQueueFaults",
                cw.Metric(
                    namespace=f"{NAMESPACE}/{self._prefix}",
                    metric_name="DigestQueueFaults",
                    period=five,
                    statistic="Sum",
                ),
                "Digests could not be claimed or queued, or a sent digest's cursor could not be saved: check the"
                " digest queue's KMS key and policy, and the ops table's grants and capacity",
            )
            self._alarm(
                "DigestDeploymentFaults",
                faults.metric(period=five, statistic="Sum"),
                "Digests were not sent because of a deployment setting (a Slack token or scope, the SES identity,"
                " the directory grant): the delivery shows the reason."
                + (
                    # Only with email: a Slack-only stack has no such output or line (scan cycle 60).
                    " Or the API logged unsubscribe.key_unusable: the unsubscribe-key secret (the"
                    " DigestUnsubscribeKeyArn output) could not be used, so unsubscribe links fail. The line's"
                    " reason is unsubscribe_not_configured (a missing or short key) or the AWS error code of the"
                    " refused read: the digestDelivery row of docs/implementation-guide.md in the source repository"
                    " says what each code means"
                    if w.digest_email
                    else ""
                ),
            )
        api_5xx = self._api_metric(w, "5xx")
        self._alarm(
            "Api5xx", api_5xx, "The HTTP API returned server errors", threshold=5
        )
        # A gate that refuses everything (a narrowed IAM grant, a KMS regression) answers 4xx or 5xx,
        # never a Lambda error: without this the whole API can be refusing calls with every alarm OK.
        self._alarm(
            "Api4xxRate",
            self._error_ratio(
                "api4xx",
                self._api_metric(w, "4xx"),
                self._api_metric(w, "Count"),
                API_MIN_REQUESTS,
            ),
            f"More than {API_4XX_PERCENT}% of HTTP API requests were refused (4xx)",
            threshold=API_4XX_PERCENT,
        )
        url_metrics = []
        for fn in w.url_functions:
            name = fn.node.id
            dims = {"FunctionName": fn.function_name}
            five_xx = cw.Metric(
                namespace="AWS/Lambda",
                metric_name="Url5xxCount",
                dimensions_map=dims,
                period=five,
                statistic="Sum",
                label=f"{name} 5xx",
            )
            four_xx = cw.Metric(
                namespace="AWS/Lambda",
                metric_name="Url4xxCount",
                dimensions_map=dims,
                period=five,
                statistic="Sum",
                label=f"{name} 4xx",
            )
            requests = cw.Metric(
                namespace="AWS/Lambda",
                metric_name="UrlRequestCount",
                dimensions_map=dims,
                period=five,
                statistic="Sum",
            )
            self._alarm(
                f"{name}Url5xx",
                five_xx,
                f"{name} function URL returned server errors",
                5,
            )
            self._alarm(
                f"{name}Url4xxRate",
                self._error_ratio(
                    f"{name.lower()}4xx", four_xx, requests, URL_MIN_REQUESTS
                ),
                f"More than {URL_4XX_PERCENT}% of {name} function URL calls were refused (4xx): "
                "agent tool calls may be failing while runs still end succeeded",
                threshold=URL_4XX_PERCENT,
            )
            url_metrics += [five_xx, four_xx]
        sessions = []
        for runtime in w.runtimes:
            name = runtime.node.id
            self._alarm(
                f"{name}SystemErrors",
                runtime.metric_system_errors(period=five, statistic="Sum"),
                f"AgentCore {name} server-side errors",
            )
            self._alarm(
                f"{name}Throttles",
                runtime.metric_throttles(period=five, statistic="Sum"),
                f"AgentCore {name} requests throttled (session or request quota)",
            )
            # A misconfigured JWT authorizer or a broken client turns every call into a 4xx that no
            # system-error alarm sees: chat is then down for every user with every alarm OK (P2-024).
            self._alarm(
                f"{name}UserErrors",
                runtime.metric_user_errors(period=five, statistic="Sum"),
                f"AgentCore {name} refused requests (4xx: bad payload, budget, authorization)",
                threshold=10,
            )
            self._alarm(
                f"{name}AuthFailures",
                self._auth_failures(runtime, five),
                f"AgentCore {name} rejected callers' tokens: the authorizer or the client is misconfigured",
                threshold=5,
            )
            session_count = runtime.metric("Sessions", period=five, statistic="Maximum")
            self._alarm(
                f"{name}Sessions",
                session_count,
                f"AgentCore {name} holds {RUNTIME_SESSIONS} or more concurrent sessions",
                threshold=RUNTIME_SESSIONS,
            )
            sessions.append(session_count)
        region = Stack.of(self).region
        quota = ACCOUNT_SESSION_QUOTA.get(region, OTHER_REGION_SESSION_QUOTA)
        account_sessions = cw.Metric(
            namespace="AWS/Bedrock-AgentCore",
            metric_name="ActiveSessionCount",
            dimensions_map={"Service": "AgentCore.Runtime"},
            period=five,
            statistic="Maximum",
            label="AgentCore Runtime sessions (account)",
        )
        self._alarm(
            "AccountSessions",
            account_sessions,
            f"The account's AgentCore Runtime sessions, every agent's, reached {QUOTA_ALARM_PERCENT}% "
            f"of the default quota ({quota} in {region}): new runs and chats may be refused",
            threshold=quota * QUOTA_ALARM_PERCENT // 100,
        )
        sessions.append(account_sessions)
        if w.gateway is not None:
            # A broken connector, or a gateway that lost its key grant, fails every web search inside the
            # runtime while runs end succeeded and every other alarm stays OK (P3-031).
            self._alarm(
                "WebSearchGatewayErrors",
                cw.Metric(
                    namespace="AWS/Bedrock-AgentCore",
                    metric_name="SystemErrors",
                    dimensions_map={
                        "Operation": "InvokeGateway",
                        "Resource": w.gateway.gateway_arn,
                    },
                    period=five,
                    statistic="Sum",
                    label="Web-search gateway system errors",
                ),
                "The AgentCore web-search gateway failed calls: web search is failing inside module runs",
                threshold=GATEWAY_ERRORS,
            )
        self._edge_alarms(w)
        self._dashboard(
            w,
            api_5xx,
            sessions,
            timeouts.metric(period=five, statistic="Sum"),
            url_metrics,
        )

    @staticmethod
    def _auth_failures(
        runtime: agentcore.Runtime, period: Duration
    ) -> cw.MathExpression:
        """AgentCore publishes ``InboundAuthorizationFailure`` per ``ExceptionType`` and
        ``ResourceId`` (the runtime ARN), not under the Name/Operation/Resource dimensions of its
        other runtime metrics, which the alarm used to read and so never saw a data point (r3
        P3-006). Which exception types appear depends on the failure (InvalidInboundToken...,
        UnauthorizedInboundToken..., ForbiddenAccessInboundToken... are all live), and an alarm
        cannot use SEARCH, so a Metrics Insights query sums every type for this runtime."""
        return cw.MathExpression(
            expression=Fn.join(
                "",
                [
                    (
                        'SELECT SUM(InboundAuthorizationFailure) FROM "AWS/Bedrock-AgentCore" '
                        "WHERE ResourceId = '"
                    ),
                    runtime.agent_runtime_arn,
                    "'",
                ],
            ),
            using_metrics={},
            label=f"{runtime.node.id} inbound authorization failures",
            period=period,
        )

    @staticmethod
    def _api_metric(w: Watched, name: str) -> cw.Metric:
        return cw.Metric(
            namespace="AWS/ApiGateway",
            metric_name=name,
            dimensions_map={"ApiId": w.api.api_id, "Stage": "$default"},
            period=Duration.minutes(5),
            statistic="Sum",
        )

    @staticmethod
    def _error_ratio(
        cid: str, errors: cw.IMetric, requests: cw.IMetric, floor: int
    ) -> cw.MathExpression:
        """Errors as a percentage of requests, reported as 0 below ``floor`` requests in the period so a
        handful of refused calls on a quiet deployment does not page anyone."""
        return cw.MathExpression(
            expression=f"IF(r > {floor}, 100 * e / r, 0)",
            using_metrics={"e": errors, "r": requests},
            label=f"{cid} error rate (%)",
            period=Duration.minutes(5),
        )

    def _edge_alarms(self, w: Watched) -> None:
        """CloudFront and the CLOUDFRONT-scope web ACL publish to us-east-1 only, and a CloudWatch alarm
        cannot read a metric from another Region, so these alarms exist only in a us-east-1 deployment.
        The dashboard shows both wherever the deployment is (widgets can read another Region)."""
        if Stack.of(self).region != EDGE_REGION:
            return
        five = Duration.minutes(5)
        self._alarm(
            "WafBlocked",
            self._waf_metric(w, five),
            "AWS WAF blocked requests to the web app: a rule may be refusing real traffic",
            threshold=20,
        )
        # With a request floor, as the other rate alarms: /api/* passes through CloudFront, so on a quiet
        # deployment one API 5xx among a handful of requests was 20-50% and paged on top of Api5xx (P3-030).
        cdn_requests = self._cdn_metric(w, "Requests", five)
        cdn_requests = cdn_requests.with_(statistic="Sum")
        self._alarm(
            "Cdn5xxRate",
            cw.MathExpression(
                expression=f"IF(r > {CDN_MIN_REQUESTS}, e, 0)",
                using_metrics={
                    "e": self._cdn_metric(w, "5xxErrorRate", five),
                    "r": cdn_requests,
                },
                label="CloudFront 5xx error rate (%)",
                period=five,
            ),
            f"CloudFront answered more than {CLOUDFRONT_5XX_PERCENT}% of at least {CDN_MIN_REQUESTS} requests "
            "with a server error",
            threshold=CLOUDFRONT_5XX_PERCENT,
        )

    @staticmethod
    def _waf_metric(w: Watched, period: Duration) -> cw.Metric:
        return cw.Metric(
            namespace="AWS/WAFV2",
            metric_name="BlockedRequests",
            # The ACL's name, not its CloudWatch metric name, and no Region dimension: a
            # CLOUDFRONT-scope ACL publishes {WebACL=<name>, Rule=ALL} (r3 P3-004).
            dimensions_map={
                "WebACL": Fn.select(2, Fn.split("/", w.web_acl_arn)),
                "Rule": "ALL",
            },
            period=period,
            statistic="Sum",
            region=EDGE_REGION,
            label="WAF blocked",
        )

    @staticmethod
    def _cdn_metric(w: Watched, name: str, period: Duration) -> cw.Metric:
        return cw.Metric(
            namespace="AWS/CloudFront",
            metric_name=name,
            dimensions_map={
                "DistributionId": w.distribution.distribution_id,
                "Region": "Global",
            },
            period=period,
            statistic="Average",
            region=EDGE_REGION,
            label=f"CloudFront {name}",
        )

    def _dashboard(
        self,
        w: Watched,
        api_5xx: cw.Metric,
        sessions: list[cw.Metric],
        timeouts: cw.Metric,
        url_metrics: list[cw.Metric],
    ) -> None:
        five = Duration.minutes(5)
        # Dashboard names are global per account, and every other fixed name in this stack carries the
        # Region: without it a second deployment with the same prefix in another Region fails late in
        # creation on the existing dashboard, and destroying either deletes the shared name (P2-008).
        board = cw.Dashboard(
            self,
            "Dashboard",
            dashboard_name=f"{self._prefix}-insights-radar-{Stack.of(self).region}",
        )
        board.add_widgets(
            cw.GraphWidget(
                title="Module runs",
                left=[
                    w.runs.metric_started(period=five, statistic="Sum"),
                    w.runs.metric_succeeded(period=five, statistic="Sum"),
                    w.runs.metric_failed(period=five, statistic="Sum"),
                    timeouts,
                ],
            ),
            cw.GraphWidget(
                title="Function errors",
                left=[fn.metric_errors(period=five) for fn in w.functions],
            ),
            cw.GraphWidget(
                title="API",
                left=[api_5xx, self._api_metric(w, "4xx")],
                right=[
                    w.indexer.metric("IteratorAge", period=five, statistic="Maximum")
                ],
            ),
            cw.GraphWidget(
                title="Agent tool gates (function URLs)",
                left=url_metrics,
            ),
            cw.GraphWidget(
                title="Edge (us-east-1)",
                left=[self._waf_metric(w, five)],
                right=[
                    self._cdn_metric(w, "5xxErrorRate", five),
                    self._cdn_metric(w, "4xxErrorRate", five),
                ],
            ),
            cw.GraphWidget(
                title="Queues and sessions",
                left=[
                    w.dlq.metric_approximate_number_of_messages_visible(
                        period=five, statistic="Max"
                    ),
                    w.failed_events.metric_approximate_number_of_messages_visible(
                        period=five, statistic="Max"
                    ),
                ],
                right=sessions,
            ),
            cw.GraphWidget(
                title="Agent runtimes",
                left=[
                    m
                    for r in w.runtimes
                    for m in (
                        r.metric_invocations(period=five),
                        r.metric_system_errors(period=five),
                    )
                ],
                right=[r.metric_throttles(period=five) for r in w.runtimes],
            ),
        )
