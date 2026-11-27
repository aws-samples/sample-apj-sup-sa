# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Checkov and cfn_nag skips, written into each resource's ``Metadata`` so they travel with the template.

Every skip names the resource kind it applies to and the mechanism that makes the check not apply.
Skips attach per resource, never per stack, so a new resource is scanned in full. cdk-nag
suppressions live in ``nag.py``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

import jsii
from aws_cdk import Aspects, CfnResource, IAspect, Stack
from constructs import IConstruct

Skip = tuple[str, str, str]  # (tool, rule id, reason)

_NO_VPC = (
    "No VPC: the functions reach only AWS service endpoints and, for the crawler and the source watcher, the "
    "public internet; there is nothing private to reach, and a VPC would add NAT cost and no isolation."
)
_NO_RESERVED = (
    "No reserved concurrency by default: new accounts have a 10-execution regional quota, and reserving any "
    "of it makes the deployment fail. reservedConcurrency=true reserves some for the run bookkeeping functions "
    "and caps the two agent gates at 10 each. User traffic is bounded by WAF rate limits and API throttling; "
    "agent traffic reaches neither, so it is bounded by the per-module token budgets, the per-run tool caps and "
    "each module's max_concurrency instead."
)
_LOGS_IN_POLICY = (
    "Log permissions are granted on the function's own log group in its role's separate default "
    "policy, which this rule does not inspect."
)
LAMBDA: list[Skip] = [
    ("checkov", "CKV_AWS_117", _NO_VPC),
    ("cfn_nag", "W89", _NO_VPC),
    ("checkov", "CKV_AWS_115", _NO_RESERVED),
    ("cfn_nag", "W92", _NO_RESERVED),
    ("cfn_nag", "W58", _LOGS_IN_POLICY),
]
SYNCHRONOUS: list[Skip] = [
    (
        "checkov",
        "CKV_AWS_116",
        (
            "Invoked synchronously (API Gateway, a function URL, Step Functions, a deploy-time trigger) or from a "
            "DynamoDB stream with its own on-failure queue; the caller receives the error, so a "
            "function-level DLQ would never receive anything."
        ),
    ),
]
SQS_DRIVEN: list[Skip] = [
    (
        "checkov",
        "CKV_AWS_116",
        (
            "Invoked by an SQS event source mapping: a message that fails returns to its queue, and after "
            "three receives the queue's own redrive policy moves it to an alarmed dead-letter queue, so a "
            "function-level DLQ would never receive anything."
        ),
    ),
]
_HELPER = "CDK-owned deployment helper"
CDK_HELPER: list[Skip] = [
    (
        "checkov",
        "CKV_AWS_116",
        f"{_HELPER}; runs only during stack operations and reports to CloudFormation.",
    ),
    ("checkov", "CKV_AWS_173", f"{_HELPER}; its environment holds no secrets."),
    (
        "checkov",
        "CKV_AWS_158",
        f"{_HELPER}'s log group; no application data is logged.",
    ),
    ("cfn_nag", "W84", f"{_HELPER}'s log group; no application data is logged."),
    (
        "checkov",
        "CKV_AWS_111",
        f"{_HELPER}'s policy, scoped by CDK to the buckets it copies between.",
    ),
]
_STAR = (
    "Resource '*' only on actions with no resource-level permissions: X-Ray, CloudWatch Logs delivery and "
    "cloudwatch:PutMetricData, plus the CloudFront invalidation actions CDK's own BucketDeployment role asks "
    "for unscoped (those two do support a distribution ARN; the role is CDK's, not this solution's). "
    "nag.STAR_RESOURCE_ACTIONS lists them and a template test fails on any other action with Resource '*'."
)
_SPCM = (
    "One role per function: its policy lists that function's grants, each scoped to named resources of this "
    "solution. Splitting it would not reduce what the role can do."
)
POLICY: list[Skip] = [("cfn_nag", "W12", _STAR), ("cfn_nag", "W76", _SPCM)]
_HMAC = "HMAC KMS keys do not support automatic rotation. The key signs short-lived run tokens and encrypts no data."
HMAC_KEY: list[Skip] = [("checkov", "CKV_AWS_7", _HMAC), ("cfn_nag", "F19", _HMAC)]
_TLS = (
    "Default *.cloudfront.net certificate, whose TLS policy CloudFront fixes. The implementation guide shows "
    "adding a custom domain with TLSv1.2_2021."
)
DISTRIBUTION: list[Skip] = [("checkov", "CKV_AWS_174", _TLS), ("cfn_nag", "W70", _TLS)]
_SELF_LOG = "This is the access-log bucket; logging it into itself would loop."
LOG_BUCKET: list[Skip] = [
    ("checkov", "CKV_AWS_18", _SELF_LOG),
    ("cfn_nag", "W35", _SELF_LOG),
    (
        "checkov",
        "CKV_AWS_21",
        (
            "Access logs are append-only and expire by lifecycle rule; versioning adds cost and "
            "no recovery."
        ),
    ),
]
# Synchronous or stream-driven solution functions (the asynchronous Crawler, Sweep, source Watch and
# digest scheduler and feedback have DLQs; the digest sender's queue has its own).
_SYNCHRONOUS_FUNCTIONS = (
    "BackendApiHandler",
    "BackendInvoke",
    "BackendFinalize",
    "BackendIndexer",
    "BackendSeed",
    "BackendAgentTools",
    "BackendChatUsage",
    "AuthPreToken",  # Cognito waits for it at every sign-in
)
_CDK_HELPERS = (
    "CustomCDKBucketDeployment",
    "AWSCDKTriggerCustomResourceProvider",
    "CustomS3AutoDeleteObjectsCustomResourceProvider",
    "AWSCDKCfnUtilsProvider",
    "AWS679f53fac002430cb0da5b7982bd2287",  # AwsCustomResource: the runtime log groups (agents.py)
)
# The exact policies that hold a Resource '*' statement or more than ten statements. A prefix such as
# "Backend" would skip W12 and W76 on a new policy whatever it contained (P2-034); these are named,
# and nag.STAR_RESOURCE_ACTIONS plus its template test is what actually bounds the wildcard.
_STAR_POLICIES = (
    "AgentsChatRuntimeExecutionRoleDefaultPolicy",
    "AgentsModuleRuntimeExecutionRoleDefaultPolicy",
    "BackendAgentToolsRoleDefaultPolicy",
    "BackendApiHandlerRoleDefaultPolicy",
    "BackendChatUsageRoleDefaultPolicy",
    "BackendCrawlerRoleDefaultPolicy",
    "BackendDigestFeedbackRoleDefaultPolicy",
    "BackendDigestScheduleRoleDefaultPolicy",
    "BackendDigestSendRoleDefaultPolicy",
    "BackendFinalizeRoleDefaultPolicy",
    "BackendIndexerRoleDefaultPolicy",
    "BackendInvokeRoleDefaultPolicy",
    "BackendRunsRoleDefaultPolicy",
    "BackendSeedRoleDefaultPolicy",
    "BackendSweepRoleDefaultPolicy",
    "CustomCDKBucketDeployment",
)


@jsii.implements(IAspect)
class ScannerSkips:
    """An aspect, so it also sees resources CDK adds while synthesising."""

    def __init__(self, stack: Stack) -> None:
        self._stack = stack
        self._rules: list[tuple[str, Callable[[str], bool], list[Skip]]] = [
            (
                "AWS::Lambda::Function",
                lambda lid: lid.startswith(_CDK_HELPERS),
                LAMBDA + CDK_HELPER,
            ),
            (
                "AWS::Lambda::Function",
                lambda lid: lid.startswith(_SYNCHRONOUS_FUNCTIONS),
                LAMBDA + SYNCHRONOUS,
            ),
            (
                "AWS::Lambda::Function",
                lambda lid: lid.startswith(
                    (
                        "BackendCrawler",
                        "BackendSweep",
                        "BackendWatch",
                        "BackendDigestSchedule",
                        "BackendDigestFeedback",
                    )
                ),
                LAMBDA,
            ),
            (
                "AWS::Lambda::Function",
                lambda lid: lid.startswith("BackendDigestSend"),
                LAMBDA + SQS_DRIVEN,
            ),
            (
                "AWS::Logs::LogGroup",
                lambda lid: lid.startswith(_CDK_HELPERS),
                CDK_HELPER,
            ),
            ("AWS::IAM::Policy", lambda lid: lid.startswith(_CDK_HELPERS), CDK_HELPER),
            ("AWS::IAM::Policy", lambda lid: lid.startswith(_STAR_POLICIES), POLICY),
            ("AWS::KMS::Key", lambda lid: lid.startswith("DataRunTokenKey"), HMAC_KEY),
            ("AWS::CloudFront::Distribution", lambda lid: True, DISTRIBUTION),
            (
                "AWS::S3::Bucket",
                lambda lid: lid.startswith("DataAccessLogs"),
                LOG_BUCKET,
            ),
        ]

    def apply(self) -> None:
        Aspects.of(self._stack).add(self)

    def visit(self, node: IConstruct) -> None:
        if not isinstance(node, CfnResource):
            return
        lid = self._stack.resolve(node.logical_id)
        by_tool: dict[str, dict[str, str]] = defaultdict(dict)
        for type_, match, skips in self._rules:
            if node.cfn_resource_type == type_ and match(lid):
                for tool, rule, reason in skips:
                    by_tool[tool][rule] = reason
        if "checkov" in by_tool:
            node.add_metadata(
                "checkov",
                {
                    "skip": [
                        {"id": i, "comment": c} for i, c in by_tool["checkov"].items()
                    ]
                },
            )
        if "cfn_nag" in by_tool:
            node.add_metadata(
                "cfn_nag",
                {
                    "rules_to_suppress": [
                        {"id": i, "reason": r} for i, r in by_tool["cfn_nag"].items()
                    ]
                },
            )
