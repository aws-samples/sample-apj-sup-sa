"""In-process synthesis tests for ``HviDataStack`` (FEAT-002).

Every test builds an ``App(context={...})``, instantiates the stack, and
asserts on ``Template.from_stack``. No ``cdk`` CLI, no Docker, no AWS calls —
the seed-handler assets bundle locally and no OmniVoice artifact is involved on
this stack.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stacks.context import HviContext
from stacks.data_stack import HviDataStack


def _synth(make_app, context=None):
    app = make_app(context or {})
    hvi = HviContext.from_scope(app)
    stack = HviDataStack(
        app,
        "HviDataStack",
        hvi=hvi,
        env=cdk.Environment(account=hvi.account, region=hvi.region),
    )
    return Template.from_stack(stack), stack


@pytest.fixture
def template(make_app):
    tmpl, _ = _synth(make_app)
    return tmpl


# ------------------------------------------------------------------ DynamoDB
def test_exactly_eight_tables(template):
    template.resource_count_is("AWS::DynamoDB::Table", 8)


def test_all_tables_pay_per_request_and_retained(template):
    tables = template.find_resources("AWS::DynamoDB::Table")
    assert len(tables) == 8
    for logical_id, resource in tables.items():
        assert resource["Properties"]["BillingMode"] == "PAY_PER_REQUEST", logical_id
        assert resource["DeletionPolicy"] == "Retain", logical_id


def test_call_state_keys_and_ttl(template):
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "hvi-call-state",
            "KeySchema": [
                {"AttributeName": "callId", "KeyType": "HASH"},
                {"AttributeName": "turnIndex", "KeyType": "RANGE"},
            ],
            "TimeToLiveSpecification": {
                "AttributeName": "expiresAt",
                "Enabled": True,
            },
        },
    )


def test_ttl_enabled_on_call_state_only(template):
    tables = template.find_resources("AWS::DynamoDB::Table")
    with_ttl = [
        r["Properties"]["TableName"]
        for r in tables.values()
        if "TimeToLiveSpecification" in r["Properties"]
    ]
    assert with_ttl == ["hvi-call-state"]


def test_call_session_has_both_named_gsis(template):
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "hvi-call-session",
            "GlobalSecondaryIndexes": Match.array_with(
                [
                    Match.object_like(
                        {
                            "IndexName": "phone-index",
                            "KeySchema": [
                                {"AttributeName": "phone", "KeyType": "HASH"},
                                {"AttributeName": "updatedAt", "KeyType": "RANGE"},
                            ],
                            "Projection": {"ProjectionType": "ALL"},
                        }
                    ),
                    Match.object_like(
                        {
                            "IndexName": "status-index",
                            "KeySchema": [
                                {"AttributeName": "status", "KeyType": "HASH"},
                                {"AttributeName": "updatedAt", "KeyType": "RANGE"},
                            ],
                            "Projection": {"ProjectionType": "ALL"},
                        }
                    ),
                ]
            ),
        },
    )


def test_crm_cache_has_phone_index(template):
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "hvi-crm-cache",
            "GlobalSecondaryIndexes": Match.array_with(
                [
                    Match.object_like(
                        {
                            "IndexName": "phone-index",
                            "KeySchema": [
                                {"AttributeName": "phone", "KeyType": "HASH"},
                            ],
                            "Projection": {"ProjectionType": "ALL"},
                        }
                    ),
                ]
            ),
        },
    )


def test_tables_tagged_managed_by_cdk(template):
    # NIT-3 deviation: ManagedBy=hvi-cdk, Project unchanged.
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "hvi-form-schema",
            "Tags": Match.array_with(
                [
                    {"Key": "ManagedBy", "Value": "hvi-cdk"},
                    {"Key": "Project", "Value": "health-voice-intake"},
                ]
            ),
        },
    )


# -------------------------------------------------------------- archive bucket
def test_archive_bucket_security_posture(template):
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "BucketName": "hvi-archive-111122223333-ap-south-1",
            "VersioningConfiguration": {"Status": "Enabled"},
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {
                        "ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                        "BucketKeyEnabled": True,
                    }
                ]
            },
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )


def test_archive_bucket_retained_and_no_object_lock(template):
    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 1
    (resource,) = buckets.values()
    assert resource["DeletionPolicy"] == "Retain"
    assert "ObjectLockConfiguration" not in resource["Properties"]
    assert "ObjectLockEnabled" not in resource["Properties"]


def test_archive_bucket_tls_only_deny_policy(template):
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Effect": "Deny",
                                "Condition": {
                                    "Bool": {"aws:SecureTransport": "false"}
                                },
                            }
                        )
                    ]
                )
            }
        },
    )


# -------------------------------------------------------------------- networking
def test_three_subnets(template):
    template.resource_count_is("AWS::EC2::Subnet", 3)


def test_vpc_cidr_block_typed_prop(make_app):
    # MEDIUM-1: the read-only typed prop Phase 3 consumes for pjsip local_net,
    # and the synthesised VPC still carries the same literal CidrBlock.
    tmpl, stack = _synth(make_app)
    assert stack.vpc_cidr_block == "10.42.0.0/16"
    tmpl.has_resource_properties(
        "AWS::EC2::VPC", {"CidrBlock": "10.42.0.0/16"}
    )


def test_nat_present_by_default(template):
    template.resource_count_is("AWS::EC2::NatGateway", 1)


def test_nat_absent_when_skip_nat(make_app):
    tmpl, _ = _synth(make_app, {"hvi:skipNat": True})
    tmpl.resource_count_is("AWS::EC2::NatGateway", 0)
    tmpl.resource_count_is("AWS::EC2::EIP", 0)


def test_gateway_endpoints_for_ddb_and_s3(template):
    template.resource_count_is("AWS::EC2::VPCEndpoint", 2)
    endpoints = template.find_resources("AWS::EC2::VPCEndpoint")
    for resource in endpoints.values():
        assert resource["Properties"]["VpcEndpointType"] == "Gateway"
    service_fragments = [
        str(r["Properties"]["ServiceName"]) for r in endpoints.values()
    ]
    assert any("dynamodb" in s for s in service_fragments)
    assert any("s3" in s for s in service_fragments)


def test_three_security_groups_with_names(template):
    template.resource_count_is("AWS::EC2::SecurityGroup", 3)
    for name in ("hvi-lambda-sg", "hvi-neptune-sg", "hvi-infer-sg"):
        template.has_resource_properties(
            "AWS::EC2::SecurityGroup", {"GroupName": name}
        )


def test_security_group_ingress_rules(template):
    ingress = template.find_resources("AWS::EC2::SecurityGroupIngress")
    ports = sorted(
        (r["Properties"]["FromPort"], r["Properties"]["ToPort"])
        for r in ingress.values()
    )
    # Neptune 8182 from lambda + from infer; infer 8080 from lambda.
    assert ports == [(8080, 8080), (8182, 8182), (8182, 8182)]
    for r in ingress.values():
        assert r["Properties"]["IpProtocol"] == "tcp"


def test_neptune_subnet_group(template):
    template.has_resource_properties(
        "AWS::Neptune::DBSubnetGroup",
        {"DBSubnetGroupName": "hvi-neptune-subnets"},
    )


# ------------------------------------------------------------------------ roles
def test_lambda_role_trust_and_key_statements(template):
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "RoleName": "hvi-lambda-role",
            "AssumeRolePolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Principal": {"Service": "lambda.amazonaws.com"},
                                "Action": "sts:AssumeRole",
                            }
                        )
                    ]
                )
            },
        },
    )


def test_sagemaker_role_trust(template):
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "RoleName": "hvi-sagemaker-role",
            "AssumeRolePolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {"Principal": {"Service": "sagemaker.amazonaws.com"}}
                        )
                    ]
                )
            },
        },
    )


def test_sagemaker_artifacts_read_is_static_arn(template):
    # MEDIUM-4: artifacts read is a literal-ARN statement, no Ref/ImportValue.
    policies = template.find_resources("AWS::IAM::Policy")
    found = None
    for resource in policies.values():
        for stmt in resource["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "ReadModelArtifacts":
                found = stmt
    assert found is not None, "ReadModelArtifacts statement not found"
    assert found["Resource"] == [
        "arn:aws:s3:::hvi-artifacts-111122223333-ap-south-1",
        "arn:aws:s3:::hvi-artifacts-111122223333-ap-south-1/*",
    ]


def test_agentcore_role_lambda_path_trust_and_logs(template):
    # Default agentCompute=lambda: trusts lambda, has /aws/lambda logs via the
    # managed basic-execution policy.
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "RoleName": "hvi-agentcore-role",
            "AssumeRolePolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {"Principal": {"Service": "lambda.amazonaws.com"}}
                        )
                    ]
                )
            },
            "ManagedPolicyArns": Match.array_with(
                [
                    {
                        "Fn::Join": [
                            "",
                            Match.array_with(
                                [
                                    Match.string_like_regexp(
                                        "AWSLambdaBasicExecutionRole"
                                    )
                                ]
                            ),
                        ]
                    }
                ]
            ),
        },
    )


def test_agentcore_role_agentcore_cr_path(make_app):
    tmpl, _ = _synth(make_app, {"hvi:agentCompute": "agentcore-cr"})
    role = None
    for resource in tmpl.find_resources("AWS::IAM::Role").values():
        if resource["Properties"].get("RoleName") == "hvi-agentcore-role":
            role = resource
    assert role is not None
    principal = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
        "Principal"
    ]["Service"]
    assert principal == "bedrock-agentcore.amazonaws.com"
    # No /aws/lambda logs capability on this path (no basic-exec managed policy).
    assert "ManagedPolicyArns" not in role["Properties"]


# ------------------------------------------------------------------ seed CRs
def test_two_seed_custom_resources(template):
    template.resource_count_is("AWS::CloudFormation::CustomResource", 2)


def test_seed_handlers_are_python312(template):
    functions = template.find_resources("AWS::Lambda::Function")
    runtimes = [
        f["Properties"].get("Runtime") for f in functions.values()
    ]
    # Both seed handlers are python3.12 (provider framework fn is nodejs).
    assert runtimes.count("python3.12") >= 2


def test_seed_handler_roles_scoped_to_put_item(template):
    policies = template.find_resources("AWS::IAM::Policy")
    put_item_policies = 0
    for resource in policies.values():
        for stmt in resource["Properties"]["PolicyDocument"]["Statement"]:
            action = stmt.get("Action")
            actions = action if isinstance(action, list) else [action]
            if "dynamodb:PutItem" in actions and "dynamodb:BatchWriteItem" in actions:
                put_item_policies += 1
    # One PutItem/BatchWriteItem policy per seed handler (form schema + CRM).
    assert put_item_policies >= 2
