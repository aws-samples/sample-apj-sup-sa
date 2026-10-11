"""In-process synthesis tests for ``HviKnowledgeStack`` (FEAT-003).

Each test builds an ``App(context={...})``, instantiates Data then Knowledge
(consuming Data's typed props), and asserts on ``Template.from_stack``. No
``cdk`` CLI, no Docker, no AWS calls — assets bundle locally (handler.py + the
vendored seed JSON + the pure-Python ``requests`` package).
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.knowledge_stack import HviKnowledgeStack


def _synth(make_app, context=None):
    app = make_app(context or {})
    hvi = HviContext.from_scope(app)
    env = cdk.Environment(account=hvi.account, region=hvi.region)
    data = HviDataStack(app, "HviDataStack", hvi=hvi, env=env)
    knowledge = HviKnowledgeStack(
        app, "HviKnowledgeStack", hvi=hvi, data=data, env=env
    )
    return Template.from_stack(knowledge), knowledge


@pytest.fixture
def template(make_app):
    tmpl, _ = _synth(make_app)
    return tmpl


# ---------------------------------------------------------------- opensearch
def test_opensearch_domain_core_config(template):
    template.has_resource_properties(
        "AWS::OpenSearchService::Domain",
        {
            "DomainName": "hvi-logs",
            "ClusterConfig": Match.object_like(
                {
                    "InstanceType": "r6g.large.search",
                    "InstanceCount": 1,
                    "DedicatedMasterEnabled": False,
                    "ZoneAwarenessEnabled": False,
                }
            ),
            "EBSOptions": Match.object_like(
                {"EBSEnabled": True, "VolumeType": "gp3", "VolumeSize": 10}
            ),
            "NodeToNodeEncryptionOptions": {"Enabled": True},
            "EncryptionAtRestOptions": {"Enabled": True},
            "DomainEndpointOptions": Match.object_like(
                {
                    "EnforceHTTPS": True,
                    "TLSSecurityPolicy": "Policy-Min-TLS-1-2-2019-07",
                }
            ),
        },
    )


def test_opensearch_engine_version_is_pinned_literal(template):
    domains = template.find_resources("AWS::OpenSearchService::Domain")
    (resource,) = domains.values()
    # Pinned literal, never a Ref/lookup token.
    assert resource["Properties"]["EngineVersion"] == "OpenSearch_2.17"


def test_opensearch_engine_version_overridable(make_app):
    tmpl, _ = _synth(make_app, {"hvi:opensearchEngine": "OpenSearch_2.13"})
    domains = tmpl.find_resources("AWS::OpenSearchService::Domain")
    (resource,) = domains.values()
    assert resource["Properties"]["EngineVersion"] == "OpenSearch_2.13"


def test_opensearch_instance_type_overridable(make_app):
    tmpl, _ = _synth(
        make_app, {"hvi:opensearchInstanceType": "m6g.large.search"}
    )
    tmpl.has_resource_properties(
        "AWS::OpenSearchService::Domain",
        {"ClusterConfig": Match.object_like({"InstanceType": "m6g.large.search"})},
    )


def test_opensearch_access_policy_principals_and_action(template):
    # The L2 Domain applies access policies through a dedicated
    # Custom::OpenSearchAccessPolicy resource (updateDomainConfig) to avoid a
    # self-referential policy — assert on its rendered payload.
    policies = template.find_resources("Custom::OpenSearchAccessPolicy")
    assert len(policies) == 1
    (resource,) = policies.values()
    rendered = str(resource["Properties"]["Create"])
    assert "es:ESHttp*" in rendered
    # The deploying admin is admitted by literal user ARN; the two roles arrive
    # as typed cross-stack imports.
    assert "user/adminCLI" in rendered
    assert "LambdaRole" in rendered
    assert "AgentCoreRole" in rendered
    assert "domain/hvi-logs/*" in rendered


# --------------------------------------------------------------- graph lambda
def _graph_fn(template):
    functions = template.find_resources("AWS::Lambda::Function")
    for resource in functions.values():
        if resource["Properties"].get("FunctionName") == "hvi-graph":
            return resource
    raise AssertionError("hvi-graph function not found")


def test_graph_lambda_runtime_handler_and_vpc(template):
    fn = _graph_fn(template)
    props = fn["Properties"]
    assert props["Runtime"] == "python3.12"
    assert props["Handler"] == "handler.lambda_handler"
    assert props["Architectures"] == ["x86_64"]
    assert props["MemorySize"] == 512
    assert props["Timeout"] == 300
    # In-VPC: carries a VpcConfig with subnet + security-group references.
    vpc_config = props["VpcConfig"]
    assert vpc_config["SubnetIds"]
    assert vpc_config["SecurityGroupIds"]


def test_graph_lambda_has_three_neptune_env_keys(template):
    fn = _graph_fn(template)
    env = fn["Properties"]["Environment"]["Variables"]
    assert set(env) >= {"NEPTUNE_ENDPOINT", "NEPTUNE_PORT", "NEPTUNE_REGION"}
    assert env["NEPTUNE_PORT"] == "8182"
    # Flag off by default -> no usable endpoint (empty, fail-loud at call time).
    assert env["NEPTUNE_ENDPOINT"] == ""


# -------------------------------------------------------------------- neptune
def test_no_neptune_resources_by_default(template):
    template.resource_count_is("AWS::Neptune::DBCluster", 0)
    template.resource_count_is("AWS::Neptune::DBInstance", 0)


def test_neptune_cluster_when_flag_on(make_app):
    tmpl, _ = _synth(make_app, {"hvi:enableNeptune": True})
    tmpl.has_resource_properties(
        "AWS::Neptune::DBCluster",
        {
            "DBClusterIdentifier": "hvi-health-kg",
            "IamAuthEnabled": True,
            "StorageEncrypted": True,
            "BackupRetentionPeriod": 1,
        },
    )
    tmpl.has_resource_properties(
        "AWS::Neptune::DBInstance",
        {
            "DBInstanceIdentifier": "hvi-health-kg-1",
            "DBInstanceClass": "db.t4g.medium",
        },
    )


def test_graph_endpoint_wired_to_cluster_when_flag_on(make_app):
    tmpl, _ = _synth(make_app, {"hvi:enableNeptune": True})
    fn = _graph_fn(tmpl)
    endpoint = fn["Properties"]["Environment"]["Variables"]["NEPTUNE_ENDPOINT"]
    # Now a token reference to the cluster endpoint, not the empty string.
    assert endpoint != ""
    assert isinstance(endpoint, dict)  # Fn::GetAtt to the cluster


# ----------------------------------------------------------------- seed CRs
def test_graph_seed_absent_by_default(template):
    # Default (Neptune off) -> only the vector-index seed custom resource.
    template.resource_count_is("AWS::CloudFormation::CustomResource", 1)


def test_graph_seed_present_when_flag_on(make_app):
    tmpl, _ = _synth(make_app, {"hvi:enableNeptune": True})
    # Neptune on -> vector-index seed + graph seed.
    tmpl.resource_count_is("AWS::CloudFormation::CustomResource", 2)


def test_vector_index_seed_present_on_both_flag_states(make_app):
    off, _ = _synth(make_app, {"hvi:enableNeptune": False})
    on, _ = _synth(make_app, {"hvi:enableNeptune": True})
    # The vector seed always exists; off has exactly one CR, on has two.
    off.resource_count_is("AWS::CloudFormation::CustomResource", 1)
    on.resource_count_is("AWS::CloudFormation::CustomResource", 2)


def test_vector_seed_role_has_eshttp_and_bedrock(template):
    policies = template.find_resources("AWS::IAM::Policy")
    es_http = False
    bedrock = False
    for resource in policies.values():
        for stmt in resource["Properties"]["PolicyDocument"]["Statement"]:
            action = stmt.get("Action")
            actions = action if isinstance(action, list) else [action]
            if "es:ESHttp*" in actions:
                es_http = True
            if "bedrock:InvokeModel" in actions:
                bedrock = True
    assert es_http, "vector seed role missing es:ESHttp*"
    assert bedrock, "vector seed role missing bedrock:InvokeModel"
