# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""cdk-nag suppressions. Each names the exact wildcard it covers and why no narrower form exists.

IAM5 suppressions are matched with ``applies_to`` patterns, so a new wildcard that is not listed
here still fails synthesis. The one pattern that cannot be narrowed is ``Resource::*``: cdk-nag
reports it without the action, so a suppression cannot name the actions it is meant for. The
action allowlist lives in ``STAR_RESOURCE_ACTIONS`` and is enforced by a template test
(``tests/test_hardening.py``) rather than by cdk-nag.

Every other rule is suppressed on the single resource it is about (``BY_PATH``), not on the stack:
a stack suppression for APIG4 would also cover a second unauthenticated route, and one for SQS3 a
new queue without a dead-letter queue (P2-010).
"""

from __future__ import annotations

from aws_cdk import Stack
from cdk_nag import NagPackSuppression, NagSuppressions, RegexAppliesTo
from constructs import IConstruct


def _rx(pattern: str) -> RegexAppliesTo:
    return RegexAppliesTo(regex=f"/{pattern}/g")


IAM5 = [
    (
        (
            "states:StopExecution for an administrator's Cancel of a module run: execution ARNs are "
            "<state machine name>:<execution name>, and each run names its own execution, so the grant is "
            "on this one state machine's executions."
        ),
        [
            _rx(
                "^Resource::arn:<AWS::Partition>:states:(<AWS::Region>|[a-z0-9-]+):(<AWS::AccountId>|[0-9]+):"
                'execution:\\{"Fn::Select":\\[6,.+\\}:\\*$'
            )
        ],
    ),
    (
        (
            "X-Ray, CloudWatch Logs delivery and cloudwatch:PutMetricData do not support resource-level "
            "permissions; CDK adds them for Lambda/Step Functions tracing, Step Functions logging and the "
            "AgentCore runtime's metrics."
        ),
        [_rx("^Resource::\\*$")],
    ),
    (
        (
            "CDK KMS grants on the single data key: GenerateDataKey* and ReEncrypt* cover the variants "
            "(WithoutPlaintext, From/To) one encrypt/decrypt grant needs. The resource is the key itself."
        ),
        [_rx("^Action::kms:(GenerateDataKey|ReEncrypt)\\*$")],
    ),
    (
        (
            "CDK S3 grants on one bucket (the assets bucket): object-level actions need the object wildcard "
            "(the crawler, the sweep and the agent tool gate are limited to their own docs/, radars/ and "
            "drafts/ prefixes), and the action wildcards are CDK's standard read/write/delete sets for that "
            "bucket only."
        ),
        [
            _rx("^Action::s3:(List|GetObject|GetBucket|DeleteObject|Abort)\\*$"),
            _rx(
                "^Resource::<DataAssets[0-9A-F]+\\.Arn>/(\\*|docs/\\*|radars/\\*|drafts/\\*)$"
            ),
        ],
    ),
    (
        (
            "The MCP role (deploy options mcpIssuer/mcpAudience) may invoke this API's /iam/api routes only: "
            "an execute-api ARN is <api>/<stage>/<method>/<path>, and the route is a proxy over every method "
            "and path below /iam/api, so stage, method and the path tail are wildcards on this one API."
        ),
        [
            _rx(
                "^Resource::arn:<AWS::Partition>:execute-api:<AWS::Region>:<AWS::AccountId>:"
                "<BackendApi[0-9A-F]+>/\\*/\\*/iam/api/\\*$"
            )
        ],
    ),
    (
        "Query access to this solution's own tables' global secondary indexes (gsi1).",
        [_rx("^Resource::<Data(Graph|Ops)Table[0-9A-F]+\\.Arn>/index/\\*$")],
    ),
    (
        (
            "Global cross-Region inference: a global profile routes a request to any Region, so the model "
            "ARN of each catalog profile needs a Region wildcard. Only the catalog's own models are named."
        ),
        [
            _rx(
                "^Resource::arn:<AWS::Partition>:bedrock:\\*::foundation-model/(anthropic|amazon)\\.[a-z0-9.:-]+$"
            )
        ],
    ),
    (
        (
            "AgentCore runtime defaults from the CDK L2: its own log groups (named after the runtime id, "
            "unknown before creation) and its workload identity in the account's default directory."
        ),
        [
            _rx("^Resource::arn:<AWS::Partition>:logs:.*:log-group:\\*$"),
            _rx(
                "^Resource::arn:<AWS::Partition>:logs:.*/aws/bedrock-agentcore/runtimes/\\*.*$"
            ),
            _rx(
                "^Resource::arn:<AWS::Partition>:bedrock-agentcore:.*:workload-identity-directory/default/"
                "workload-identity/\\*$"
            ),
        ],
    ),
    (
        (
            "AgentCore direct code deployment: the runtime role reads its code zip from the CDK bootstrap asset "
            "bucket (read-only; the CDK L2 adds this grant). An explicit deny on the same role blocks "
            "s3:GetObject* on every object except the runtime's own code zip."
        ),
        [
            _rx(
                "^Resource::arn:<AWS::Partition>:s3:::cdk-[a-z0-9]+-assets-[0-9]{12}-[a-z0-9-]+/\\*$"
            )
        ],
    ),
    (
        (
            "AWS-managed Browser and Code Interpreter (account segment 'aws'); session ids are created at "
            "run time, so the session ARN is a wildcard under the managed tool."
        ),
        [
            _rx(
                "^Resource::arn:<AWS::Partition>:bedrock-agentcore:<AWS::Region>:aws:(browser|code-interpreter)/\\*$"
            )
        ],
    ),
    (
        "Step Functions invokes these functions by ARN with any version/alias qualifier (CDK LambdaInvoke).",
        [_rx("^Resource::<Backend(Invoke|Finalize)[0-9A-F]+\\.Arn>:\\*$")],
    ),
    (
        (
            "With retainData=false the stack deletes an AgentCore runtime's log group, including the old one "
            "after the runtime is replaced (a new runtime ID): DeleteLogGroup on that runtime's own name prefix."
        ),
        [
            _rx(
                "^Resource::arn:<AWS::Partition>:logs:<AWS::Region>:<AWS::AccountId>:log-group:"
                "/aws/bedrock-agentcore/runtimes/[a-z0-9_]+_(modules|chat)-\\*$"
            )
        ],
    ),
    (
        "InvokeAgentRuntime is authorised on the runtime and its endpoints (qualifiers such as DEFAULT).",
        [_rx("^Resource::<AgentsModuleRuntime[0-9A-F]+\\.AgentRuntimeArn>/\\*$")],
    ),
]

# Actions that have no resource-level permissions, so a statement for them is written with
# ``Resource: "*"``. Nothing else in this stack may use ``*`` as its resource; a template test
# fails on any other action, which is the guarantee the single IAM5 ``Resource::*`` suppression
# cannot give on its own.
STAR_RESOURCE_ACTIONS = frozenset(
    {
        "xray:PutTraceSegments",
        "xray:PutTelemetryRecords",
        "xray:GetSamplingRules",
        "xray:GetSamplingTargets",
        "logs:CreateLogDelivery",
        "logs:GetLogDelivery",
        "logs:UpdateLogDelivery",
        "logs:DeleteLogDelivery",
        "logs:ListLogDeliveries",
        "logs:PutResourcePolicy",
        "logs:DescribeResourcePolicies",
        "logs:DescribeLogGroups",
        "cloudwatch:PutMetricData",
        # CDK's own BucketDeployment role. Both actions do support a distribution ARN; CDK writes
        # them unscoped and the role is CDK's, not this solution's.
        "cloudfront:CreateInvalidation",
        "cloudfront:GetInvalidation",
    }
)

_COG8 = (
    "Threat protection needs the Cognito Plus plan (per active user cost). The pool uses "
    "required TOTP MFA, admin-only sign-up and a strong password policy; the implementation guide explains how to "
    "switch to Plus."
)
_CFR1 = (
    "Audience is the deploying organisation's staff worldwide; geo restriction is a deployment "
    "choice documented in the implementation guide."
)
_CFR4 = (
    "The distribution uses the default *.cloudfront.net certificate (no custom domain, so the "
    "solution deploys without ACM or Route 53); its viewer TLS policy is fixed by CloudFront. "
    "The implementation guide shows adding a custom domain with an ACM certificate and TLSv1.2_2021."
)
_APIG4 = (
    "Only GET /api/config is unauthenticated: the web app reads its sign-in settings there. "
    "It returns the Region, user pool, app client and Cognito domain, and the chat runtime ARN (which includes the "
    "account id). Every other route has a JWT authorizer, and this suppression covers this route only."
)
_INDEXER_DLQ = (
    "This queue is the indexer's dead-letter queue; a DLQ for a DLQ is not meaningful."
)
_FAILED_EVENTS = (
    "This queue is the dead-letter queue for the crawler and the scheduled sweep, which alarms "
    "when anything lands in it; a DLQ for a DLQ is not meaningful."
)
# One rule, the resource it is about, and why. Each is suppressed on that resource only.
BY_PATH: list[tuple[str, str, str]] = [
    ("AwsSolutions-COG8", "Auth/Users/Resource", _COG8),
    ("AwsSolutions-CFR1", "Web/Cdn/Resource", _CFR1),
    ("AwsSolutions-CFR4", "Web/Cdn/Resource", _CFR4),
    ("AwsSolutions-APIG4", "Backend/Api/GET--api--config/Resource", _APIG4),
    ("AwsSolutions-SQS3", "Backend/IndexerDlq/Resource", _INDEXER_DLQ),
    ("AwsSolutions-SQS3", "Backend/FailedEvents/Resource", _FAILED_EVENTS),
]
SLACK_STORE_REASON = (
    "Slack issues the bot token and the incoming-webhook URL and Secrets Manager cannot rotate them; the "
    "operator puts a new value when Slack reissues one. Only the digest sender and the API may read it."
)
UNSUBSCRIBE_KEY_REASON = (
    "Rotating the unsubscribe HMAC key would break the one-click unsubscribe link in every digest email "
    "already sent, which must keep working. It signs links that each turn off one subscription and "
    "encrypts no data."
)
_APIG4_UNSUBSCRIBE = (
    "The digest emails' one-click unsubscribe (RFC 8058): the mail client posts it without a session. The "
    "HMAC-signed token in the link names one subscription and can only turn that one off; GET only shows a "
    "confirmation page. The route has its own throttle of 5 requests a second, and this suppression covers "
    "this route only."
)
# Digest delivery's resources exist only when digestDelivery turns a channel on, so each is suppressed
# only when it is in the stack.
# The two digest secrets: suppressed on the Secret itself where digest.py makes it (suppress_digest_secret), so
# the suppression follows any generation suffix in the construct ID. The paths are the generation-0 ones the
# security matrix names (scan cycle 62).
DIGEST_SECRETS: list[tuple[str, str, str]] = [
    ("AwsSolutions-SMG4", "Backend/Digest/SlackSecret/Resource", SLACK_STORE_REASON),
    (
        "AwsSolutions-SMG4",
        "Backend/Digest/UnsubscribeKey/Resource",
        UNSUBSCRIBE_KEY_REASON,
    ),
]


def suppress_digest_secret(secret: IConstruct, reason: str) -> None:
    """Attach a digest secret's SMG4 suppression to it, with SLACK_STORE_REASON or UNSUBSCRIBE_KEY_REASON."""
    NagSuppressions.add_resource_suppressions(
        secret,
        [NagPackSuppression(id="AwsSolutions-SMG4", reason=reason)],
        apply_to_children=True,
    )


DIGEST_BY_PATH: list[tuple[str, str, str]] = [
    (
        "AwsSolutions-APIG4",
        "Backend/Api/GET--api--digest--unsubscribe/Resource",
        _APIG4_UNSUBSCRIBE,
    ),
    (
        "AwsSolutions-APIG4",
        "Backend/Api/POST--api--digest--unsubscribe/Resource",
        _APIG4_UNSUBSCRIBE,
    ),
]
# The reasons, for the security matrix, which quotes them word for word.
OTHER = {rule: reason for rule, _path, reason in BY_PATH}


CDK_DEPLOYMENT = [
    (
        "AwsSolutions-L1",
        "CDK's BucketDeployment singleton Lambda: CDK pins its runtime; it runs only during deployment.",
    ),
    (
        "AwsSolutions-IAM4",
        "CDK's BucketDeployment singleton Lambda uses AWSLambdaBasicExecutionRole for its logs; CDK owns it.",
    ),
    (
        "AwsSolutions-IAM5",
        (
            "CDK's BucketDeployment copies the site from the CDK asset bucket into the site bucket; CDK generates "
            "the object-level grants on those two buckets."
        ),
    ),
]


# AwsCustomResource's singleton Lambda (the runtime log groups' key and retention, agents.py).
AWS_CUSTOM_RESOURCE = "AWS679f53fac002430cb0da5b7982bd2287"
CDK_SDK_CALLS = [
    (
        "AwsSolutions-IAM4",
        (
            "CDK's AwsCustomResource singleton Lambda uses AWSLambdaBasicExecutionRole for its logs; CDK owns it. "
            "Its SDK-call policy is scoped to the two runtime log groups."
        ),
    ),
    (
        "AwsSolutions-L1",
        "CDK's AwsCustomResource singleton Lambda: CDK pins its runtime; it runs only during deployment.",
    ),
]


def _cdk_internals(stack: Stack) -> None:
    """Suppress only on CDK's own deployment helpers, so our functions stay under the full rules."""
    for child in stack.node.children:
        if child.node.id == AWS_CUSTOM_RESOURCE:
            NagSuppressions.add_resource_suppressions(
                child,
                [
                    NagPackSuppression(id=rule, reason=reason)
                    for rule, reason in CDK_SDK_CALLS
                ],
                apply_to_children=True,
            )
        if child.node.id.startswith("Custom::CDKBucketDeployment"):
            NagSuppressions.add_resource_suppressions(
                child,
                [
                    NagPackSuppression(id=rule, reason=reason)
                    for rule, reason in CDK_DEPLOYMENT
                ],
                apply_to_children=True,
            )


def _find(stack: Stack, path: str) -> IConstruct | None:
    node: IConstruct | None = stack
    for part in path.split("/"):
        node = node.node.try_find_child(part) if node is not None else None
    return node


def apply_suppressions(stack: Stack) -> None:
    _cdk_internals(stack)
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-IAM5", reason=reason, applies_to=applies
            )
            for reason, applies in IAM5
        ],
    )
    present = [entry for entry in DIGEST_BY_PATH if _find(stack, entry[1]) is not None]

    for rule, path, reason in [*BY_PATH, *present]:
        NagSuppressions.add_resource_suppressions_by_path(
            stack,
            f"{stack.node.id}/{path}",
            [NagPackSuppression(id=rule, reason=reason)],
        )
