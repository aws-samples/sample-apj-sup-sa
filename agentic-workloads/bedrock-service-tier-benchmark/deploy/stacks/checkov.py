"""Record justified Checkov skips as CloudFormation metadata (Checkov reads ``Metadata.checkov.skip``)."""

from __future__ import annotations

from aws_cdk import CfnResource
from constructs import IConstruct

LAMBDA_NO_VPC = (
    "CKV_AWS_117",
    "By design: the function only calls regional AWS APIs (RDS Data API, Secrets Manager); a VPC adds a NAT or "
    "endpoints without reducing exposure.",
)
LAMBDA_NO_DLQ = ("CKV_AWS_116", "Invoked synchronously (API Gateway / CloudFormation); errors return to the caller.")
LAMBDA_ENV_PLAIN = ("CKV_AWS_173", "Environment holds only resource ARNs and names, no secrets.")
LAMBDA_CONCURRENCY = ("CKV_AWS_115", "Invoked only by CloudFormation during deployment.")
LOGS_DEFAULT_KEY = (
    "CKV_AWS_158",
    "Encrypted at rest with the CloudWatch Logs service key; contains request metadata only, no customer data.",
)


def skip(construct: IConstruct, *checks: tuple[str, str]) -> None:
    node = construct if isinstance(construct, CfnResource) else construct.node.default_child
    if isinstance(node, CfnResource):
        node.add_metadata("checkov", {"skip": [{"id": c, "comment": r} for c, r in checks]})
