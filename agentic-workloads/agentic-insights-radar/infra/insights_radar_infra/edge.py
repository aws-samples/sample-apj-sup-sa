# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The CloudFront web ACL.

AWS WAF only attaches web ACLs with CLOUDFRONT scope that live in us-east-1. In us-east-1 the ACL
is part of the main stack; in any other Region it is deployed as a separate us-east-1 stack and
its ARN is passed to the main stack as the ``webAclArn`` context value (see ``Application``).
"""

from __future__ import annotations

import json
from typing import Any

from aws_cdk import Aws, CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_wafv2 as waf
from constructs import Construct

from insights_radar_infra.config import DESCRIPTION, RETENTION_DAYS, DeployConfig

RATE_LIMITED = "rate_limited"
RATE_WINDOW_SECONDS = 300
RATE_LIMITED_BODY = json.dumps(
    {
        "code": RATE_LIMITED,
        "message": "Too many requests from this network address. Wait about five minutes and try again.",
        "details": {"retry_after": RATE_WINDOW_SECONDS},
    }
)

EDGE_REGION = "us-east-1"


# Headers that carry credentials are replaced with REDACTED in the WAF log.
REDACTED_HEADERS = ("authorization", "cookie", "x-mr-run-token")


class WebAcl(Construct):
    """The web ACL and its full request log (``aws-waf-logs-<prefix>-<Region>``, the main stack's Region)."""

    @staticmethod
    def metric_name(prefix: str) -> str:
        """The ACL's visibility-config metric name. It is NOT the ``WebACL`` dimension of the
        AWS/WAFV2 metrics: a CLOUDFRONT-scope ACL publishes them under its own name, which
        CloudFormation generates (monitoring reads it from the ACL's ARN, r3 P3-004)."""
        return f"{prefix}-web"

    def __init__(
        self,
        scope: Construct,
        cid: str,
        config: DeployConfig,
        region: str,
        key: kms.IKey,
    ) -> None:
        super().__init__(scope, cid)
        prefix = config.prefix

        def managed(
            name: str, priority: int, count: tuple[str, ...] = ()
        ) -> waf.CfnWebACL.RuleProperty:
            return waf.CfnWebACL.RuleProperty(
                name=name,
                priority=priority,
                override_action=waf.CfnWebACL.OverrideActionProperty(none={}),
                statement=waf.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=waf.CfnWebACL.ManagedRuleGroupStatementProperty(
                        vendor_name="AWS",
                        name=name,
                        rule_action_overrides=[
                            waf.CfnWebACL.RuleActionOverrideProperty(
                                name=r,
                                action_to_use=waf.CfnWebACL.RuleActionProperty(
                                    count={}
                                ),
                            )
                            for r in count
                        ]
                        or None,
                    )
                ),
                visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True,
                    metric_name=name,
                    sampled_requests_enabled=True,
                ),
            )

        rate = waf.CfnWebACL.RuleProperty(
            name="RateLimitPerIp",
            priority=0,
            # 429, not the default 403: the MCP server told a throttled client to rephrase (round 33 review).
            # The body has the API's error shape, so the app and the MCP server show it and the wait
            # (round 34 review); the limit is counted over the WAF's default five-minute window.
            action=waf.CfnWebACL.RuleActionProperty(
                block=waf.CfnWebACL.BlockActionProperty(
                    custom_response=waf.CfnWebACL.CustomResponseProperty(
                        response_code=429,
                        custom_response_body_key=RATE_LIMITED,
                        response_headers=[
                            waf.CfnWebACL.CustomHTTPHeaderProperty(
                                name="Retry-After", value=str(RATE_WINDOW_SECONDS)
                            )
                        ],
                    )
                )
            ),
            statement=waf.CfnWebACL.StatementProperty(
                rate_based_statement=waf.CfnWebACL.RateBasedStatementProperty(
                    limit=config.waf_rate_limit, aggregate_key_type="IP"
                )
            ),
            visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="RateLimitPerIp",
                sampled_requests_enabled=True,
            ),
        )
        acl = waf.CfnWebACL(
            self,
            "WebAcl",
            scope="CLOUDFRONT",
            default_action=waf.CfnWebACL.DefaultActionProperty(allow={}),
            custom_response_bodies={
                RATE_LIMITED: waf.CfnWebACL.CustomResponseBodyProperty(
                    content_type="APPLICATION_JSON", content=RATE_LIMITED_BODY
                )
            },
            visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=self.metric_name(prefix),
                sampled_requests_enabled=True,
            ),
            rules=[
                rate,
                # Radar imports and module instructions are larger than the rule set's 8 KB body limit;
                # the API enforces its own 6 MB cap and validates every field.
                managed(
                    "AWSManagedRulesCommonRuleSet", 1, count=("SizeRestrictions_BODY",)
                ),
                managed("AWSManagedRulesKnownBadInputsRuleSet", 2),
                managed("AWSManagedRulesAmazonIpReputationList", 3),
            ],
        )
        self.arn = acl.attr_arn
        name = f"aws-waf-logs-{prefix}-{region}"  # WAF only writes to groups named aws-waf-logs-*
        group = logs.LogGroup(
            self,
            "Logs",
            log_group_name=name,
            retention=logs.RetentionDays[RETENTION_DAYS[config.log_retention_days]],
            encryption_key=key,
            removal_policy=RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE
            if config.retain_data
            else RemovalPolicy.DESTROY,
        )
        logging = waf.CfnLoggingConfiguration(
            self,
            "Logging",
            resource_arn=acl.attr_arn,
            # The group ARN without the trailing ":*" CloudWatch adds; WAF refuses that form.
            log_destination_configs=[
                f"arn:{Aws.PARTITION}:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:{name}"
            ],
            redacted_fields=[
                waf.CfnLoggingConfiguration.FieldToMatchProperty(
                    single_header={"Name": h}
                )
                for h in REDACTED_HEADERS
            ]
            # The whole query string, not only the headers (P2-036). /auth/callback carries the
            # OAuth authorization code and state of every sign-in, and a search is GET ?q=<terms>:
            # both are credentials or personal data, and this log is kept for logRetentionDays
            # (365 by default). AWS WAF cannot redact one query parameter, so the string goes.
            + [waf.CfnLoggingConfiguration.FieldToMatchProperty(query_string={})],
        )
        logging.node.add_dependency(group)


class EdgeStack(Stack):
    """Holds the web ACL when the solution is deployed outside us-east-1."""

    def __init__(
        self,
        scope: Construct,
        cid: str,
        config: DeployConfig,
        region: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, cid, description=f"{DESCRIPTION} - edge", **kwargs)
        # The edge stack has no data key of its own: one small key encrypts the WAF log.
        key = kms.Key(
            self,
            "LogKey",
            # The alias names the deployment and the Region it serves: this key is kept on delete,
            # and several deployments' edge keys otherwise live in us-east-1 indistinguishable.
            alias=f"{config.prefix}-waf-logs-{region}",
            description=f"Agentic Insights Radar ({config.prefix}) WAF request log for {region}",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE
            if config.retain_data
            else RemovalPolicy.DESTROY,
        )
        key.add_to_resource_policy(logs_key_statement())
        self.acl = WebAcl(self, "Waf", config, region, key)
        CfnOutput(
            self,
            "WebAclArn",
            value=self.acl.arn,
            description="Pass to the main stack as -c webAclArn=...",
        )


def logs_key_statement() -> iam.PolicyStatement:
    """CloudWatch Logs may use a key for this account's log groups in this Region only."""
    return iam.PolicyStatement(
        sid="AllowCloudWatchLogs",
        principals=[iam.ServicePrincipal(f"logs.{Aws.REGION}.amazonaws.com")],
        # The exact actions CloudWatch Logs uses for encrypted log groups (no wildcards).
        actions=[
            "kms:Encrypt",
            "kms:Decrypt",
            "kms:ReEncryptFrom",
            "kms:ReEncryptTo",
            "kms:GenerateDataKey",
            "kms:GenerateDataKeyWithoutPlaintext",
            "kms:DescribeKey",
        ],
        resources=["*"],
        conditions={
            "ArnLike": {
                "kms:EncryptionContext:aws:logs:arn": f"arn:{Aws.PARTITION}:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:*"
            }
        },
    )
