# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""What the synthesised template must guarantee, independent of how the constructs are written."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from insights_radar_infra import agents, backend, frontend
from insights_radar_infra.config import DESCRIPTION, DeployConfig
from insights_radar_infra.stack import AgenticInsightsRadarStack

from .conftest import CONTEXT, ENV, Synth, build

# ---- cdk-nag -------------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["default", "disposable", "federated"])
def test_no_unsuppressed_cdk_nag_findings(
    which: str, request: pytest.FixtureRequest
) -> None:
    s: Synth = request.getfixturevalue(which)
    errors = s.annotations.find_error("*", Match.string_like_regexp("AwsSolutions-.*"))
    warnings = s.annotations.find_warning(
        "*", Match.string_like_regexp("AwsSolutions-.*")
    )
    assert [e.entry.data for e in errors] == []
    assert [w.entry.data for w in warnings] == []


def _skips(s: Synth, tool: str, key: str, reason: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for lid, r in s.template.to_json()["Resources"].items():
        for skip in r.get("Metadata", {}).get(tool, {}).get(key, []):
            assert len(skip[reason]) > 40, skip
            found.setdefault(skip["id"], set()).add(lid)
    return found


def test_scanner_skips_are_per_resource_and_explained(default: Synth) -> None:
    checkov = _skips(default, "checkov", "skip", "comment")
    assert set(checkov) == {
        "CKV_AWS_7",
        "CKV_AWS_18",
        "CKV_AWS_21",
        "CKV_AWS_111",
        "CKV_AWS_115",
        "CKV_AWS_116",
        "CKV_AWS_117",
        "CKV_AWS_158",
        "CKV_AWS_173",
        "CKV_AWS_174",
    }
    # The asynchronous workers keep their dead-letter check.
    assert not any(
        lid.startswith(("BackendCrawler", "BackendSweep"))
        for lid in checkov["CKV_AWS_116"]
    )
    assert len(checkov["CKV_AWS_7"]) == 1
    nag = _skips(default, "cfn_nag", "rules_to_suppress", "reason")
    assert set(nag) == {"F19", "W12", "W35", "W58", "W70", "W76", "W84", "W89", "W92"}
    assert len(nag["F19"]) == 1


@pytest.mark.parametrize("which", ["default", "disposable"])
def test_every_lambda_function_carries_the_shared_lambda_skips(
    which: str, request: pytest.FixtureRequest
) -> None:
    """Every function gets the shared VPC and concurrency reasons by rule, the source Watch included (scan
    cycle 1: checkov failed BackendWatch on CKV_AWS_115 and CKV_AWS_117)."""
    s: Synth = request.getfixturevalue(which)
    checkov = _skips(s, "checkov", "skip", "comment")
    functions = set(s.resources("AWS::Lambda::Function"))
    assert any(lid.startswith("BackendWatch") for lid in functions)
    assert functions <= checkov["CKV_AWS_115"]
    assert functions <= checkov["CKV_AWS_117"]


def test_description_names_the_sample(default: Synth) -> None:
    assert default.template.to_json()["Description"] == DESCRIPTION
    assert DESCRIPTION.startswith("Agentic Insights Radar on AWS (sample) v")
    assert "SO0" not in DESCRIPTION


# ---- API authorisation ---------------------------------------------------------------------------


def test_only_the_config_route_is_public(default: Synth) -> None:
    routes = {
        p["RouteKey"]: p.get("AuthorizationType")
        for p in default.props("AWS::ApiGatewayV2::Route")
    }
    assert routes == {
        "GET /api/config": "NONE",
        "ANY /api/{proxy+}": "JWT",
        "POST /api/graph/search": "JWT",
        "POST /api/graph/ask": "JWT",
        "POST /api/products/{id}/ask": "JWT",
    }


def test_jwt_authoriser_accepts_only_this_pools_clients(default: Synth) -> None:
    (auth,) = default.props("AWS::ApiGatewayV2::Authorizer")
    assert auth["AuthorizerType"] == "JWT"
    assert len(auth["JwtConfiguration"]["Audience"]) == 2
    assert "cognito-idp" in json.dumps(auth["JwtConfiguration"]["Issuer"])


# ---- Cognito -------------------------------------------------------------------------------------


def test_user_pool_requires_totp_and_admin_invitations(default: Synth) -> None:
    (pool,) = default.props("AWS::Cognito::UserPool")
    assert pool["MfaConfiguration"] == "ON"
    assert pool["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True
    policy = pool["Policies"]["PasswordPolicy"]
    assert policy["MinimumLength"] >= 12
    assert all(
        policy[k]
        for k in (
            "RequireLowercase",
            "RequireUppercase",
            "RequireNumbers",
            "RequireSymbols",
        )
    )


def test_app_clients_are_public_pkce_clients_without_password_auth(
    default: Synth,
) -> None:
    clients = default.props("AWS::Cognito::UserPoolClient")
    assert len(clients) == 2
    for c in clients:
        assert c["GenerateSecret"] is False
        assert c["AllowedOAuthFlows"] == ["code"]
        assert "ALLOW_USER_PASSWORD_AUTH" not in c["ExplicitAuthFlows"]
        assert c["PreventUserExistenceErrors"] == "ENABLED"
        assert c["EnableTokenRevocation"] is True
        assert (
            c["IdTokenValidity"] <= 60
            and c["TokenValidityUnits"]["IdToken"] == "minutes"
        )


def test_mcp_callbacks_are_loopback_only(default: Synth) -> None:
    urls = [
        u
        for c in default.props("AWS::Cognito::UserPoolClient")
        for u in c["CallbackURLs"]
        if isinstance(u, str)
    ]
    assert urls and all(
        re.fullmatch(r"http://(127\.0\.0\.1|localhost):8765/callback", u) for u in urls
    )


def test_three_role_groups(default: Synth) -> None:
    assert len(default.props("AWS::Cognito::UserPoolGroup")) == 3


# ---- Encryption and public access ----------------------------------------------------------------


def test_buckets_are_encrypted_private_and_tls_only(default: Synth) -> None:
    buckets = default.resources("AWS::S3::Bucket")
    assert len(buckets) == 3
    for b in buckets.values():
        p = b["Properties"]
        assert p["BucketEncryption"]["ServerSideEncryptionConfiguration"]
        assert set(p["PublicAccessBlockConfiguration"].values()) == {True}
    policies = json.dumps(default.props("AWS::S3::BucketPolicy"))
    assert policies.count('"aws:SecureTransport": "false"') == 3


def test_tables_use_kms_and_point_in_time_recovery(default: Synth) -> None:
    tables = default.props("AWS::DynamoDB::GlobalTable")
    assert len(tables) == 2
    for t in tables:
        assert t["SSESpecification"] == {"SSEEnabled": True, "SSEType": "KMS"}
        assert all(
            r["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"]
            for r in t["Replicas"]
        )
        assert t["TimeToLiveSpecification"] == {"AttributeName": "ttl", "Enabled": True}


def test_data_key_rotates_and_run_tokens_use_an_hmac_key(default: Synth) -> None:
    keys = default.props("AWS::KMS::Key")
    assert sorted(k.get("KeySpec", "SYMMETRIC_DEFAULT") for k in keys) == [
        "HMAC_256",
        "SYMMETRIC_DEFAULT",
    ]
    data_key = next(k for k in keys if "KeySpec" not in k)
    assert data_key["EnableKeyRotation"] is True


def test_function_and_workflow_logs_are_encrypted(default: Synth) -> None:
    ours = {
        k: v
        for k, v in default.resources("AWS::Logs::LogGroup").items()
        if k.startswith("Backend")
    }
    assert len(ours) == 12
    assert all(
        v["Properties"]["KmsKeyId"] and v["Properties"]["RetentionInDays"] == 365
        for v in ours.values()
    )


def test_agent_runtime_logs_get_the_data_key_and_retention(
    default: Synth, disposable: Synth
) -> None:
    calls = [
        json.dumps(v["Properties"])
        for k, v in default.resources("Custom::AWS").items()
        if k.startswith("Agents")
    ]
    joined = " ".join(calls)
    for action in ("createLogGroup", "putRetentionPolicy", "associateKmsKey"):
        assert joined.count(action) == 4, (
            action
        )  # on create and on update, for two runtimes
    assert "/aws/bedrock-agentcore/runtimes/" in joined and "-DEFAULT" in joined
    assert (
        "ResourceAlreadyExistsException" in joined
    )  # the service may have made it first
    assert re.search(
        r"retentionInDays\W+30\b", joined
    )  # agentLogRetentionDays, not the 365 days
    assert "deleteLogGroup" not in joined  # kept with the data by default
    custom = default.resources("Custom::AWS")
    for runtime in ("ModuleRuntime", "ChatRuntime"):
        key = next(k for k in custom if k.startswith(f"Agents{runtime}LogKey"))
        retention = next(
            k for k in custom if k.startswith(f"Agents{runtime}LogRetention")
        )
        assert (
            retention in custom[key]["DependsOn"]
        )  # serialised: no concurrent changes to one group
    gone = " ".join(
        json.dumps(v["Properties"])
        for k, v in disposable.resources("Custom::AWS").items()
        if k.startswith("Agents")
    )
    deletes = [
        st["Resource"]
        for k, v in disposable.resources("AWS::IAM::Policy").items()
        if "RuntimeLogGroup" in k
        for st in v["Properties"]["PolicyDocument"]["Statement"]
        if st["Action"] == "logs:DeleteLogGroup"
    ]
    assert len(deletes) == 2 and all(
        "-*" in json.dumps(r) for r in deletes
    )  # a replaced runtime's old group too
    assert gone.count("deleteLogGroup") == 2 and re.search(
        r"retentionInDays\W+30\b", gone
    )


def test_log_retention_follows_config(disposable: Synth) -> None:
    ours = [
        v
        for k, v in disposable.resources("AWS::Logs::LogGroup").items()
        if k.startswith(
            ("Backend", "Web", "Agents")
        )  # Web: the site deployment's own log group
    ]
    assert any(k.startswith("Web") for k in disposable.resources("AWS::Logs::LogGroup"))
    assert {v["Properties"]["RetentionInDays"] for v in ours} == {30}


def test_cloudwatch_may_use_the_key_for_this_accounts_alarms_only(
    default: Synth,
) -> None:
    (key,) = [
        r["Properties"]
        for k, r in default.resources("AWS::KMS::Key").items()
        if k.startswith("DataDataKey")
    ]
    (st,) = [
        s
        for s in key["KeyPolicy"]["Statement"]
        if s.get("Sid") == "AllowCloudWatchAlarms"
    ]
    assert st["Principal"] == {"Service": "cloudwatch.amazonaws.com"}
    assert set(st["Condition"]) == {"StringEquals", "ArnLike"}
    # The only logs-service statement is the one scoped to this account's log groups.
    logs = [
        s
        for s in key["KeyPolicy"]["Statement"]
        if "logs." in json.dumps(s.get("Principal", {}))
    ]
    assert len(logs) == 1 and "kms:EncryptionContext:aws:logs:arn" in json.dumps(
        logs[0]["Condition"]
    )


def test_model_backed_routes_have_their_own_lower_throttle(default: Synth) -> None:
    (stage,) = default.props("AWS::ApiGatewayV2::Stage")
    assert stage["DefaultRouteSettings"]["ThrottlingRateLimit"] == 50
    assert {f"{method} {path}" for path, method in backend.MODEL_BACKED_ROUTES} == set(
        stage["RouteSettings"]
    )
    # CR2-10: plain insight listing is not throttled stage-wide; its ?q= form (which embeds) is
    # limited per user and per workspace in the function, since a route setting cannot see the query.
    assert "GET /api/insights/{kind}" not in stage["RouteSettings"]
    for route in stage["RouteSettings"]:
        assert stage["RouteSettings"][route] == {
            "ThrottlingRateLimit": 10,
            "ThrottlingBurstLimit": 20,
        }


def test_the_direct_api_address_is_not_advertised(default: Synth) -> None:
    outputs = default.template.to_json()["Outputs"]
    assert "ApiUrl" not in outputs and {"AppUrl", "McpClientId", "UserPoolId"} <= set(
        outputs
    )


# ---- Compute -------------------------------------------------------------------------------------


def test_solution_functions_run_traced_python_on_arm(default: Synth) -> None:
    ours = {
        k: v["Properties"]
        for k, v in default.resources("AWS::Lambda::Function").items()
        if k.startswith("Backend")
    }
    assert len(ours) == 10
    for p in ours.values():
        assert p["Runtime"] == "python3.14"
        assert p["Architectures"] == ["arm64"]
        assert p["TracingConfig"] == {"Mode": "Active"}


def test_asynchronous_failures_are_kept_and_alarmed(default: Synth) -> None:
    fns = default.resources("AWS::Lambda::Function")
    (crawler,) = [
        v["Properties"] for k, v in fns.items() if k.startswith("BackendCrawler")
    ]
    assert crawler["DeadLetterConfig"]["TargetArn"]
    rules = default.props("AWS::Events::Rule")
    # The sweep and the source watcher: both keep their failed events.
    assert len(rules) == 2 and all(
        r["Targets"][0]["DeadLetterConfig"]["Arn"] for r in rules
    )
    (watch,) = [
        v["Properties"]
        for k, v in fns.items()
        if k.startswith("BackendWatch") and "Role" not in k
    ]
    assert watch["DeadLetterConfig"]["TargetArn"] and watch["Timeout"] == 900
    queues = default.props("AWS::SQS::Queue")
    assert len(queues) == 2 and all(q["KmsMasterKeyId"] for q in queues)
    alarms = {a["AlarmDescription"] for a in default.props("AWS::CloudWatch::Alarm")}
    assert (
        "A documentation crawl or a scheduled sweep failed after its retries" in alarms
    )


def test_state_machine_logs_everything_and_traces(default: Synth) -> None:
    (sm,) = default.props("AWS::StepFunctions::StateMachine")
    assert sm["LoggingConfiguration"]["Level"] == "ALL"
    assert sm["TracingConfiguration"] == {"Enabled": True}


def test_no_policy_grants_every_action(default: Synth) -> None:
    docs = [p["PolicyDocument"] for p in default.props("AWS::IAM::Policy")]
    docs += [
        d["PolicyDocument"]
        for r in default.props("AWS::IAM::Role")
        for d in r.get("Policies", [])
    ]
    for doc in docs:
        for st in doc["Statement"]:
            actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            assert "*" not in actions
            assert not any(a.endswith(":*") for a in actions), st


def _s3_object_resources(synth: Synth, role_prefix: str) -> list[str]:
    """Every S3 object resource the role of the function with this logical-id prefix may touch."""
    found: list[str] = []
    for policy in synth.props("AWS::IAM::Policy"):
        if not any(r.get("Ref", "").startswith(role_prefix) for r in policy["Roles"]):
            continue
        for st in policy["PolicyDocument"]["Statement"]:
            actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            if not any(a.startswith("s3:") and "Object" in a for a in actions):
                continue
            resources = (
                st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
            )
            found += [json.dumps(r) for r in resources]
    return found


@pytest.mark.parametrize(
    ("role", "prefixes"),
    # The sweep writes radar versions, deletes a queued content idea's drafts (code review r51) and the
    # objects of uploads that were never completed (docs/*).
    [
        ("BackendCrawler", ("/docs/*",)),
        ("BackendSweep", ("/radars/*", "/drafts/*", "/docs/*")),
    ],
)
def test_workers_touch_only_their_own_asset_prefix(
    default: Synth, role: str, prefixes: tuple[str, ...]
) -> None:
    # The bare bucket ARN only carries the bucket-level actions (List*, GetBucket*) of the same grant.
    objects = [r for r in _s3_object_resources(default, role) if "Fn::Join" in r]
    assert objects
    assert all(any(r.endswith(f'"{p}"]]}}') for p in prefixes) for r in objects), (
        objects
    )


# ---- Edge ----------------------------------------------------------------------------------------


def test_distribution_is_https_only_with_waf_and_logging(default: Synth) -> None:
    (d,) = default.props("AWS::CloudFront::Distribution")
    cfg = d["DistributionConfig"]
    assert cfg["WebACLId"]
    assert cfg["Logging"]
    assert cfg["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    assert [b["PathPattern"] for b in cfg["CacheBehaviors"]] == ["/api/*"]
    assert all(
        b["ViewerProtocolPolicy"] in ("https-only", "redirect-to-https")
        for b in cfg["CacheBehaviors"]
    )


def test_waf_rate_limits_and_uses_managed_rules(default: Synth) -> None:
    (acl,) = default.props("AWS::WAFv2::WebACL")
    assert acl["Scope"] == "CLOUDFRONT"
    names = {r["Name"] for r in acl["Rules"]}
    assert {
        "RateLimitPerIp",
        "AWSManagedRulesCommonRuleSet",
        "AWSManagedRulesKnownBadInputsRuleSet",
    } <= names


def _csp(s: Synth) -> str:
    (
        policy,
    ) = [  # the SPA's policy: the one that sets its CSP over the origin's (scan cycle 42)
        p
        for p in s.props("AWS::CloudFront::ResponseHeadersPolicy")
        if p["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"][
            "ContentSecurityPolicy"
        ]["Override"]
    ]
    value: Any = policy["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"][
        "ContentSecurityPolicy"
    ]
    parts = value["ContentSecurityPolicy"]["Fn::Join"][1]
    return "".join(p if isinstance(p, str) else "${}" for p in parts)


def test_content_security_policy_forbids_inline_script_and_framing(
    default: Synth,
) -> None:
    csp = _csp(default)
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";") if d.strip())
    assert directives["script-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["object-src"] == "'none'"
    assert "unsafe-eval" not in csp


def test_security_headers_are_set(default: Synth) -> None:
    (
        policy,
    ) = [  # the SPA's policy: the one that sets its CSP over the origin's (scan cycle 42)
        p
        for p in default.props("AWS::CloudFront::ResponseHeadersPolicy")
        if p["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"][
            "ContentSecurityPolicy"
        ]["Override"]
    ]
    headers = policy["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]
    assert headers["StrictTransportSecurity"]["AccessControlMaxAgeSec"] >= 31536000
    assert headers["ContentTypeOptions"]["Override"] is True
    assert headers["FrameOptions"]["FrameOption"] == "DENY"


# ---- Configuration toggles -----------------------------------------------------------------------

STATEFUL = (
    "AWS::DynamoDB::GlobalTable",
    "AWS::S3::Bucket",
    "AWS::KMS::Key",
    "AWS::Cognito::UserPool",
)


def _stateful(s: Synth, type_: str) -> list[dict[str, Any]]:
    # The run-token key only signs short-lived tokens and encrypts nothing, so it never outlives the stack.
    return [
        r for k, r in s.resources(type_).items() if not k.startswith("DataRunTokenKey")
    ]


def test_data_is_retained_by_default(default: Synth) -> None:
    for type_ in STATEFUL:
        for r in _stateful(default, type_):
            # Kept on update and delete, but removed if the first deployment fails, so a retry is clean.
            assert (r["DeletionPolicy"], r["UpdateReplacePolicy"]) == (
                "RetainExceptOnCreate",
                "Retain",
            ), type_
    (run_key,) = [
        r
        for k, r in default.resources("AWS::KMS::Key").items()
        if k.startswith("DataRunTokenKey")
    ]
    assert run_key["DeletionPolicy"] == "Delete"
    assert default.resources("Custom::S3AutoDeleteObjects") == {}
    (pool,) = default.props("AWS::Cognito::UserPool")
    assert pool["DeletionProtection"] == "ACTIVE"
    assert all(
        r["DeletionProtectionEnabled"]
        for t in default.props("AWS::DynamoDB::GlobalTable")
        for r in t["Replicas"]
    )


def test_disposable_deployment_cleans_up(disposable: Synth) -> None:
    for type_ in STATEFUL:
        for r in disposable.resources(type_).values():
            assert r["DeletionPolicy"] == "Delete", type_
    assert len(disposable.resources("Custom::S3AutoDeleteObjects")) == 3


def test_alarms_always_notify_someone(default: Synth, disposable: Synth) -> None:
    """Without an alarm email the first administrator is subscribed, so alarms never notify nobody."""
    (sub,) = default.props("AWS::SNS::Subscription")
    assert (sub["Protocol"], sub["Endpoint"]) == ("email", "admin@example.com")
    (sub,) = disposable.props("AWS::SNS::Subscription")
    assert (sub["Protocol"], sub["Endpoint"]) == ("email", "ops@example.com")
    assert "AlarmTopicArn" in default.template.to_json()["Outputs"]


def test_web_search_target_follows_the_toggle(
    default: Synth, disposable: Synth
) -> None:
    assert len(default.resources("AWS::BedrockAgentCore::GatewayTarget")) == 1
    assert disposable.resources("AWS::BedrockAgentCore::GatewayTarget") == {}


def test_prefix_names_the_resources(disposable: Synth) -> None:
    (api,) = disposable.props("AWS::ApiGatewayV2::Api")
    assert api["Name"] == "mr-test-api"


def test_access_log_retention_follows_config(disposable: Synth) -> None:
    text = json.dumps(disposable.props("AWS::S3::Bucket"))
    assert '"ExpirationInDays": 30' in text


# ---- DeployConfig --------------------------------------------------------------------------------


def _ctx(**values: str) -> Any:
    return values.get


def test_config_defaults() -> None:
    c = DeployConfig.from_context(_ctx(adminEmail=" a@example.com "))
    assert c == DeployConfig(admin_email="a@example.com")


def test_config_reads_every_key() -> None:
    c = DeployConfig.from_context(
        _ctx(
            adminEmail="a@example.com",
            prefix="acme-mr",
            alarmEmail="o@example.com",
            retainData="FALSE",
            webSearch="false",
            logRetentionDays="90",
        )
    )
    assert c == DeployConfig(
        "a@example.com", "acme-mr", "o@example.com", False, False, 90
    )


@pytest.mark.parametrize(
    ("ctx", "message"),
    [
        ({}, "first administrator"),
        ({"adminEmail": "not-an-email"}, "first administrator"),
        ({"adminEmail": "a@example.com", "prefix": "Upper"}, "prefix"),
        ({"adminEmail": "a@example.com", "prefix": "1abc"}, "prefix"),
        ({"adminEmail": "a@example.com", "prefix": "a" * 22}, "prefix"),
        ({"adminEmail": "a@example.com", "prefix": "radar-"}, "prefix"),
        ({"adminEmail": "a@example.com", "prefix": "my--radar"}, "prefix"),
        (
            {"adminEmail": "a@example.com", "prefix": "aws-radar"},
            "aws, amazon or cognito",
        ),
        (
            {"adminEmail": "a@example.com", "prefix": "myamazon"},
            "aws, amazon or cognito",
        ),
        ({"adminEmail": "a@example.com", "alarmEmail": "nope"}, "alarmEmail"),
        ({"adminEmail": "a@example.com", "logRetentionDays": "45"}, "logRetentionDays"),
    ],
)
def test_config_rejects(ctx: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DeployConfig.from_context(_ctx(**ctx))


# ---- Regions -------------------------------------------------------------------------------------


ACL = "arn:aws:wafv2:us-east-1:111122223333:global/webacl/WafWebAcl-abc/0123-4567"


def test_outside_us_east_1_the_first_phase_is_the_edge_stack_alone(
    bundles: None,
) -> None:
    (edge,) = build("eu-west-1")
    assert (
        edge.stack_name == "AgenticInsightsRadar-eu-west-1-Edge"
        and edge.region == "us-east-1"
    )
    t = Template.from_stack(edge)
    t.resource_count_is("AWS::WAFv2::WebACL", 1)
    t.has_output("WebAclArn", {})
    assert (
        t.find_resources("AWS::Lambda::Function") == {}
    )  # no cross-Region helper functions


def test_outside_us_east_1_the_second_phase_uses_the_edge_acl(bundles: None) -> None:
    edge, main = build("eu-west-1", webAclArn=ACL)
    assert main.region == "eu-west-1" and edge in main.dependencies
    t = Template.from_stack(main)
    t.resource_count_is("AWS::WAFv2::WebACL", 0)
    (dist,) = t.find_resources("AWS::CloudFront::Distribution").values()
    assert dist["Properties"]["DistributionConfig"]["WebACLId"] == ACL


@pytest.mark.parametrize(
    "bad", ["arn:aws:wafv2:eu-west-1:111122223333:regional/webacl/x/y", "not-an-arn"]
)
def test_a_web_acl_from_elsewhere_is_refused(bad: str, bundles: None) -> None:
    with pytest.raises(ValueError, match="webAclArn"):
        build("eu-west-1", webAclArn=bad)


def test_prefix_names_both_stacks(bundles: None) -> None:
    edge, main = build("eu-west-1", prefix="acme", webAclArn=ACL)
    assert (edge.stack_name, main.stack_name) == (
        "AgenticInsightsRadar-acme-eu-west-1-Edge",
        "AgenticInsightsRadar-acme",
    )


def test_outside_us_east_1_without_an_edge_acl_is_refused(bundles: None) -> None:
    app = cdk.App(context=CONTEXT)
    with pytest.raises(ValueError, match="edge stack"):
        AgenticInsightsRadarStack(
            app,
            "Main",
            DeployConfig(admin_email="a@example.com"),
            env=cdk.Environment(account=ENV.account, region="eu-west-1"),
        )


def test_region_agnostic_synthesis_is_refused(bundles: None) -> None:
    with pytest.raises(ValueError, match="AWS_REGION"):
        AgenticInsightsRadarStack(
            cdk.App(context=CONTEXT), "Main", DeployConfig(admin_email="a@example.com")
        )


# ---- Build output --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "attr", "message"),
    [
        (backend, "LAMBDA_BUNDLE", "build/lambda"),
        (agents, "AGENT_BUNDLE", "build/agents"),
    ],
)
def test_missing_build_output_is_explained(
    module: Any,
    attr: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bundles: None,
) -> None:
    monkeypatch.setattr(module, attr, tmp_path / "absent")
    with pytest.raises(FileNotFoundError, match=message):
        AgenticInsightsRadarStack(
            cdk.App(context=CONTEXT),
            "Main",
            DeployConfig(admin_email="a@example.com"),
            env=ENV,
        )


def test_without_a_built_site_nothing_is_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundles: None
) -> None:
    monkeypatch.setattr(frontend, "SITE", tmp_path / "absent")
    stack = AgenticInsightsRadarStack(
        cdk.App(context=CONTEXT),
        "Main",
        DeployConfig(admin_email="a@example.com"),
        env=ENV,
    )
    Template.from_stack(stack).resource_count_is("Custom::CDKBucketDeployment", 0)


def test_everything_waits_for_the_memory(default: Synth) -> None:
    """A CREATING memory cannot be deleted, so nothing may fail while it is being created. The data
    key is the one exception: the memory is encrypted with it (P2-019), so the key is created first
    and a dependency the other way would be a cycle."""
    resources = default.template.to_json()["Resources"]
    (memory_id,) = default.resources("AWS::BedrockAgentCore::Memory")
    (data_key,) = [
        k
        for k, r in resources.items()
        if k.startswith("DataDataKey") and r["Type"] == "AWS::KMS::Key"
    ]
    assert _depends_on(resources, memory_id, data_key)
    for lid, r in resources.items():
        if (
            lid in (memory_id, data_key)
            or r["Type"].startswith(("AWS::CDK::", "AWS::IAM::"))
            or r["Type"] == "AWS::KMS::Alias"
        ):
            continue
        assert _depends_on(resources, lid, memory_id), lid


def _depends_on(
    resources: dict[str, Any], lid: str, target: str, seen: set[str] | None = None
) -> bool:
    """Whether ``lid`` reaches ``target`` through DependsOn, Ref or GetAtt."""
    seen = seen if seen is not None else set()
    if lid in seen:
        return False
    seen.add(lid)
    r = resources[lid]
    deps = r.get("DependsOn", [])
    refs = set(
        re.findall(
            r'"(?:Ref|Fn::GetAtt)": \[?"([A-Za-z0-9]+)"',
            json.dumps(r.get("Properties", {})),
        )
    )
    direct = {*([deps] if isinstance(deps, str) else deps), *refs} & set(resources)
    return target in direct or any(
        _depends_on(resources, d, target, seen) for d in direct
    )


def test_route_throttles_wait_for_their_routes(default: Synth) -> None:
    (stage_id,) = default.resources("AWS::ApiGatewayV2::Stage")
    stage = default.template.to_json()["Resources"][stage_id]
    routes = {
        k
        for k, v in default.resources("AWS::ApiGatewayV2::Route").items()
        if v["Properties"]["RouteKey"].startswith("POST ")
    }
    assert len(routes) == len(backend.MODEL_BACKED_ROUTES) and routes <= set(
        stage["DependsOn"]
    )


def test_failed_runs_fail_their_execution(default: Synth) -> None:
    """RunsFailed / RunsTimedOut alarm on failed executions, so a failed run must not end as SUCCEEDED."""
    (sm,) = default.props("AWS::StepFunctions::StateMachine")
    definition = json.loads(
        "".join(
            p if isinstance(p, str) else "X"
            for p in sm["DefinitionString"]["Fn::Join"][1]
        )
    )
    states = definition["States"]
    assert (
        states["FinalizeFailed"]["Next"] == "RunErrored"
        and states["RunErrored"]["Type"] == "Fail"
    )
    assert states["FinalizeSucceeded"]["Next"] == "RunOutcome"
    choice = states["RunOutcome"]
    assert (
        choice["Choices"][0]["Next"] == "RunFailed"
        and states["RunFailed"]["Type"] == "Fail"
    )
    assert states[choice["Default"]]["Type"] == "Succeed"


@pytest.mark.parametrize(
    ("context", "protected"),
    [
        ({}, True),
        ({"deletionProtection": "false"}, False),
        ({"retainData": "false"}, False),
        ({"retainData": "false", "deletionProtection": "true"}, True),
    ],
)
def test_deletion_protection_can_be_deferred_for_a_fresh_install(
    context: dict[str, str], protected: bool, bundles: None
) -> None:
    """deploy.sh creates a retained deployment unprotected, then protects it, so a failed first
    deployment can still roll back."""
    (stack,) = build(**context)
    t = Template.from_stack(stack)
    (pool,) = [
        r["Properties"] for r in t.find_resources("AWS::Cognito::UserPool").values()
    ]
    assert (pool["DeletionProtection"] == "ACTIVE") is protected
    for table in t.find_resources("AWS::DynamoDB::GlobalTable").values():
        assert all(
            r["DeletionProtectionEnabled"] is protected
            for r in table["Properties"]["Replicas"]
        )


def test_dependency_walk_stops_on_a_cycle() -> None:
    cycle = {"A": {"DependsOn": ["B"]}, "B": {"DependsOn": ["A"]}}
    assert not _depends_on(cycle, "A", "C")


def test_agent_runtimes_read_only_their_own_code_zip(default: Synth) -> None:
    runtimes = default.props("AWS::BedrockAgentCore::Runtime")
    codes = {
        json.dumps(
            r["AgentRuntimeArtifact"]["CodeConfiguration"]["Code"]["S3"], sort_keys=True
        )
        for r in runtimes
    }
    assert len(runtimes) == 2 and len(codes) == 1
    (code,) = codes
    key = json.loads(code)["Prefix"]
    denies = [
        st
        for p in default.props("AWS::IAM::Policy")
        for st in p["PolicyDocument"]["Statement"]
        if st["Effect"] == "Deny" and st["Action"] == "s3:GetObject*"
    ]
    assert len(denies) == 2
    for st in denies:
        assert json.dumps(st["NotResource"]).endswith(f'/{key}"]]}}')


def test_log_groups_follow_the_data_retention_choice(
    default: Synth, disposable: Synth
) -> None:
    def policies(s: Synth) -> dict[str, str]:
        return {
            k: r["DeletionPolicy"]
            for k, r in s.resources("AWS::Logs::LogGroup").items()
        }

    kept = policies(default)
    odd = sorted(k for k, v in kept.items() if v != "RetainExceptOnCreate")
    assert not odd, odd  # kept with their data
    gone = policies(disposable)
    assert set(gone.values()) == {"Delete"}, gone  # retainData=false removes them too


def test_a_seed_bundle_is_uploaded_and_handed_to_the_seed_only(bundles: None) -> None:
    import json

    from tests.conftest import synth

    s = synth(seed="policy-watch")
    seeds = [
        f
        for f in s.template.find_resources("AWS::Lambda::Function").values()
        if f["Properties"]["Handler"] == "insights_radar.api.workers.seed"
    ]
    (fn,) = seeds
    env = fn["Properties"]["Environment"]["Variables"]
    assert "AIR_SEED_BUNDLE_BUCKET" in env and str(env["AIR_SEED_BUNDLE_KEY"]).endswith(
        ".json"
    )
    others = [
        f
        for f in s.template.find_resources("AWS::Lambda::Function").values()
        if "AIR_SEED_BUNDLE_KEY"
        in (f["Properties"].get("Environment") or {}).get("Variables", {})
    ]
    assert others == [fn]
    outputs = s.template.find_outputs("DeployOptions")
    assert json.loads(next(iter(outputs.values()))["Value"])["seed"] == "policy-watch"
    # The bundle object only, not the shared CDK bootstrap bucket every app in the account uses.
    role = fn["Properties"]["Role"]["Fn::GetAtt"][0]
    s3 = [
        st
        for p in s.template.find_resources("AWS::IAM::Policy").values()
        if {"Ref": role} in p["Properties"]["Roles"]
        for st in p["Properties"]["PolicyDocument"]["Statement"]
        if "s3:" in json.dumps(st["Action"])
    ]
    assert [st["Action"] for st in s3] == ["s3:GetObject"]
    assert json.dumps(s3[0]["Resource"]).endswith(
        '.json"]]}'
    )  # bucket ARN + "/" + the bundle's key


def test_default_stack_has_no_seed_bundle(default: object) -> None:
    for f in default.template.find_resources("AWS::Lambda::Function").values():  # type: ignore[attr-defined]
        assert "AIR_SEED_BUNDLE_KEY" not in (
            f["Properties"].get("Environment") or {}
        ).get("Variables", {})


@pytest.mark.parametrize(
    ("value", "message"),
    [("Bad Name", "seed must name a folder"), ("missing-one", "does not exist")],
)
def test_seed_option_is_checked(bundles: None, value: str, message: str) -> None:
    from tests.conftest import synth

    with pytest.raises(ValueError, match=message):
        synth(seed=value)


ZONE = "Z0123456789ABCDEFGHIJ"
CERT = "arn:aws:acm:us-east-1:111122223333:certificate/12345678-1234-1234-1234-123456789012"


def test_a_custom_domain_gets_its_own_zone_one_delegation_and_a_certificate(
    bundles: None,
) -> None:
    import json

    from tests.conftest import synth

    s = synth(domainName="radar.team.example.com", parentZoneId=ZONE)
    t = s.template
    t.resource_count_is("AWS::Route53::HostedZone", 1)
    t.has_resource_properties(
        "AWS::Route53::HostedZone", {"Name": "radar.team.example.com."}
    )
    # Exactly one record in the parent zone: the delegation.
    ns = t.find_resources("AWS::Route53::RecordSet", {"Properties": {"Type": "NS"}})
    (record,) = ns.values()
    assert record["Properties"]["HostedZoneId"] == ZONE and record["Properties"][
        "Name"
    ].startswith("radar.")
    certs = t.find_resources("AWS::CertificateManager::Certificate")
    (cert,) = certs.values()
    assert cert["Properties"]["DomainName"] == "radar.team.example.com"
    assert any(d.startswith("WebDelegation") for d in cert.get("DependsOn", []))
    t.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "Aliases": ["radar.team.example.com"],
                "ViewerCertificate": {
                    "MinimumProtocolVersion": "TLSv1.2_2021",
                    "SslSupportMethod": "sni-only",
                },
            }
        },
    )
    for kind in ("A", "AAAA"):
        aliases = t.find_resources(
            "AWS::Route53::RecordSet", {"Properties": {"Type": kind}}
        )
        assert (
            len(aliases) == 1
            and "HostedZoneId" in next(iter(aliases.values()))["Properties"]
        )
    out = json.loads(next(iter(t.find_outputs("DeployOptions").values()))["Value"])
    assert out["domainName"] == "radar.team.example.com" and out["parentZoneId"] == ZONE
    app_url = next(iter(t.find_outputs("AppUrl").values()))["Value"]
    assert app_url == "https://radar.team.example.com"


def test_a_renamed_domain_keeps_the_old_name_and_redirects_it(bundles: None) -> None:
    from tests.conftest import synth

    before = synth(domainName="old.team.example.com", parentZoneId=ZONE).template
    t = synth(
        domainName="new-name.team.example.com",
        parentZoneId=ZONE,
        redirectDomains="old.team.example.com, OLD.team.example.com",
    ).template
    zones = t.find_resources("AWS::Route53::HostedZone")
    names = {r["Properties"]["Name"]: k for k, r in zones.items()}
    # The old name keeps the logical ids it was created with, so the rename replaces nothing.
    old_ids = {
        r["Properties"]["Name"]: k
        for k, r in before.find_resources("AWS::Route53::HostedZone").items()
    }
    assert names["old.team.example.com."] == old_ids["old.team.example.com."]
    assert names["new-name.team.example.com."].startswith("WebZoneNewName")
    ns = t.find_resources("AWS::Route53::RecordSet", {"Properties": {"Type": "NS"}})
    assert sorted(r["Properties"]["Name"].split(".")[0] for r in ns.values()) == [
        "new-name",
        "old",
    ]
    before_ns = before.find_resources(
        "AWS::Route53::RecordSet", {"Properties": {"Type": "NS"}}
    )
    assert set(before_ns) <= set(ns)
    (cert,) = t.find_resources("AWS::CertificateManager::Certificate").values()
    props = cert["Properties"]
    assert props["DomainName"] == "new-name.team.example.com"
    assert props["SubjectAlternativeNames"] == ["old.team.example.com"]
    assert len(props["DomainValidationOptions"]) == 2
    assert sum(d.startswith("WebDelegation") for d in cert["DependsOn"]) == 2
    t.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "Aliases": ["new-name.team.example.com", "old.team.example.com"]
            }
        },
    )
    for kind in ("A", "AAAA"):
        assert (
            len(
                t.find_resources(
                    "AWS::Route53::RecordSet", {"Properties": {"Type": kind}}
                )
            )
            == 2
        )
    (fn,) = t.find_resources("AWS::CloudFront::Function").values()
    code = fn["Properties"]["FunctionCode"]
    # Only the listed old names redirect; the distribution's own address keeps serving the app.
    assert (
        '["old.team.example.com"].indexOf(host) !== -1' in code
        and "statusCode: 301" in code
    )
    assert (
        "https://new-name.team.example.com" in code
        and "PRIMARY" not in code
        and "OLD_HOSTS" not in code
    )
    assert (
        code.rstrip().endswith("return request;\n}")
        and "request.uri = '/index.html'" in code
    )
    out = json.loads(next(iter(t.find_outputs("DeployOptions").values()))["Value"])
    assert out["redirectDomains"] == "old.team.example.com"
    app_url = next(iter(t.find_outputs("AppUrl").values()))["Value"]
    assert app_url == "https://new-name.team.example.com"


@pytest.mark.parametrize("old", ["radar--two.example.com", "v6-radar.example.com"])
def test_redirect_names_whose_construct_ids_would_collide_are_refused(
    bundles: None, old: str
) -> None:
    from tests.conftest import synth

    with pytest.raises(ValueError, match="first labels that differ"):
        synth(
            domainName="radar-two.example.com",
            parentZoneId=ZONE,
            redirectDomains=f"first.example.com,{old}",
        )


def test_without_redirects_the_function_only_rewrites(bundles: None) -> None:
    from tests.conftest import synth

    t = synth(domainName="radar.team.example.com", parentZoneId=ZONE).template
    (fn,) = t.find_resources("AWS::CloudFront::Function").values()
    assert "301" not in fn["Properties"]["FunctionCode"]
    out = json.loads(next(iter(t.find_outputs("DeployOptions").values()))["Value"])
    assert "redirectDomains" not in out


def test_a_custom_domain_with_a_certificate_leaves_dns_alone(bundles: None) -> None:
    from tests.conftest import synth

    t = synth(domainName="radar.example.com", certificateArn=CERT).template
    t.resource_count_is("AWS::Route53::HostedZone", 0)
    t.resource_count_is("AWS::Route53::RecordSet", 0)
    t.resource_count_is("AWS::CertificateManager::Certificate", 0)
    t.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "Aliases": ["radar.example.com"],
                "ViewerCertificate": {"AcmCertificateArn": CERT},
            }
        },
    )


@pytest.mark.parametrize(
    ("ctx", "message"),
    [
        ({"domainName": "Not A Domain"}, "domainName must be a DNS name"),
        ({"parentZoneId": ZONE}, "need domainName"),
        ({"domainName": "radar.example.com"}, "exactly one of"),
        (
            {
                "domainName": "radar.example.com",
                "parentZoneId": ZONE,
                "certificateArn": CERT,
            },
            "exactly one of",
        ),
        ({"domainName": "radar.example.com", "parentZoneId": "nope"}, "hosted zone id"),
        ({"redirectDomains": "old.example.com"}, "needs domainName and parentZoneId"),
        (
            {
                "domainName": "radar.example.com",
                "certificateArn": CERT,
                "redirectDomains": "old.example.com",
            },
            "needs domainName and parentZoneId",
        ),
        (
            {
                "domainName": "radar.example.com",
                "parentZoneId": ZONE,
                "redirectDomains": "a.example.com,b.example.com,c.example.com,d.example.com",
            },
            "at most 3",
        ),
        (
            {
                "domainName": "radar.example.com",
                "parentZoneId": ZONE,
                "redirectDomains": "old.other.com",
            },
            "other names in example.com",
        ),
        (
            {
                "domainName": "radar.example.com",
                "parentZoneId": ZONE,
                "redirectDomains": "radar.example.com",
            },
            "other names in example.com",
        ),
        (
            {
                "domainName": "radar.example.com",
                "parentZoneId": ZONE,
                "redirectDomains": "Not A Name",
            },
            "other names in example.com",
        ),
        (
            {
                "domainName": "radar.example.com",
                "certificateArn": CERT.replace("us-east-1", "eu-west-1"),
            },
            "us-east-1",
        ),
    ],
)
def test_domain_options_are_checked(
    bundles: None, ctx: dict[str, str], message: str
) -> None:
    from tests.conftest import synth

    with pytest.raises(ValueError, match=message):
        synth(**ctx)


def test_a_delegated_domain_needs_us_east_1(bundles: None) -> None:
    from tests.conftest import build

    with pytest.raises(ValueError, match="us-east-1"):
        build(
            "eu-west-1",
            webAclArn="arn:aws:wafv2:us-east-1:111122223333:global/webacl/air/0",
            domainName="radar.example.com",
            parentZoneId=ZONE,
        )


# ---- federated sign-in ---------------------------------------------------------------------------

OIDC_CLIENT_ARN = "arn:aws:secretsmanager:us-east-1:111122223333:secret:air/oidc-AbCdEf"
FEDERATED = {
    "authMode": "federated",
    "oidcIssuer": "https://idp.example.com",
    "oidcClientId": "air-client",
    "oidcClientSecretArn": OIDC_CLIENT_ARN,
    "adminEmails": "Second@Example.com, third@example.com",
}


@pytest.fixture(scope="module")
def federated(bundles: None) -> Synth:
    from tests.conftest import synth

    return synth(**FEDERATED)


def test_federated_sign_in_uses_one_oidc_provider_and_keeps_the_secret_out(
    federated: Synth,
) -> None:
    t = federated.template
    (idp,) = t.find_resources("AWS::Cognito::UserPoolIdentityProvider").values()
    props = idp["Properties"]
    assert props["ProviderName"] == "SSO" and props["ProviderType"] == "OIDC"
    details = props["ProviderDetails"]
    assert details["client_id"] == "air-client"
    assert details["oidc_issuer"] == "https://idp.example.com"
    assert details["authorize_scopes"] == "openid email"
    assert (
        details["client_secret"]
        == f"{{{{resolve:secretsmanager:{OIDC_CLIENT_ARN}:SecretString:::}}}}"
    )
    assert props["AttributeMapping"]["email"] == "email"
    # The provider's email is mapped at every sign-in, which Cognito refuses for an immutable attribute.
    pool = next(iter(t.find_resources("AWS::Cognito::UserPool").values()))["Properties"]
    email = next(a for a in pool["Schema"] if a["Name"] == "email")
    assert email["Mutable"] is True
    # No account, password or invitation is made here.
    t.resource_count_is("AWS::Cognito::UserPoolUser", 0)
    t.resource_count_is("AWS::Cognito::UserPoolUserToGroupAttachment", 0)
    clients = t.find_resources("AWS::Cognito::UserPoolClient")
    assert len(clients) == 2
    for c in clients.values():
        assert c["Properties"]["SupportedIdentityProviders"] == ["SSO"]
        assert any(d.startswith("AuthOidc") for d in c.get("DependsOn", []))
    first = t.find_outputs("FirstAdmin")["FirstAdmin"]
    assert "identity provider" in first["Description"]


def test_federated_admins_come_from_the_list_through_the_token_hook(
    federated: Synth,
) -> None:
    t = federated.template
    pool = next(iter(t.find_resources("AWS::Cognito::UserPool").values()))
    hook_ref = pool["Properties"]["LambdaConfig"]["PreTokenGeneration"]["Fn::GetAtt"][0]
    fn = t.to_json()["Resources"][hook_ref]["Properties"]
    admins = json.loads(fn["Environment"]["Variables"]["ADMIN_EMAILS"])
    assert admins == ["admin@example.com", "second@example.com", "third@example.com"]
    assert fn["TracingConfig"] == {"Mode": "Active"}
    # Its environment (the admin list) and its log group are encrypted with the data key.
    assert fn["KmsKeyArn"]["Fn::GetAtt"][0].startswith("DataDataKey")
    group = t.to_json()["Resources"][fn["LoggingConfig"]["LogGroup"]["Ref"]][
        "Properties"
    ]
    assert group["KmsKeyId"]["Fn::GetAtt"][0].startswith("DataDataKey")
    # Its role may write its own log group and add members to this pool's groups, nothing else.
    role_id = fn["Role"]["Fn::GetAtt"][0]
    role = t.to_json()["Resources"][role_id]
    assert not role["Properties"].get("ManagedPolicyArns")
    grants = [
        st
        for p in t.find_resources("AWS::IAM::Policy").values()
        if {"Ref": role_id} in p["Properties"]["Roles"]
        for st in p["Properties"]["PolicyDocument"]["Statement"]
    ]
    actions = sorted(
        a
        for st in grants
        for a in ([st["Action"]] if isinstance(st["Action"], str) else st["Action"])
    )
    assert actions == [
        "cognito-idp:AdminAddUserToGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "xray:PutTelemetryRecords",  # tracing, added by CDK
        "xray:PutTraceSegments",
    ]
    pool_grant = next(
        st for st in grants if st["Action"] == "cognito-idp:AdminAddUserToGroup"
    )
    assert pool_grant["Resource"]["Fn::GetAtt"][0].startswith("AuthUsers")
    options = json.loads(t.find_outputs("DeployOptions")["DeployOptions"]["Value"])
    assert options["authMode"] == "federated" and options["oidcProviderName"] == "SSO"
    assert options["oidcTrustEmail"] == "false"
    assert fn["Environment"]["Variables"]["TRUST_PROVIDER_EMAIL"] == "false"
    assert options["adminEmails"] == "Second@Example.com,third@example.com"


def test_the_token_hook_makes_listed_emails_admin_group_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Membership, not only the claim: the API confirms every request against live groups (a claim-only
    admin was lowered to viewer on the first call)."""
    from insights_radar_infra.hooks import pre_token

    added: list[dict[str, str]] = []

    class Cognito:
        def admin_add_user_to_group(self, **kw: str) -> None:
            added.append(kw)

    monkeypatch.setattr(pre_token, "_cognito", Cognito())
    monkeypatch.setenv("ADMIN_EMAILS", json.dumps(["boss@example.com"]))

    def event(email: str, groups: list[str], verified: str = "true") -> dict[str, Any]:
        return {
            "userPoolId": "us-east-1_Pool",
            "userName": "SSO_abc",
            "request": {
                "userAttributes": {"email": email, "email_verified": verified},
                "groupConfiguration": {"groupsToOverride": groups},
            },
            "response": {},
        }

    # An address the provider has not verified never makes anyone an administrator.
    assert (
        pre_token.handler(event("boss@example.com", [], "false"), None)["response"]
        == {}
    )
    assert added == []
    out = pre_token.handler(event(" Boss@Example.com ", ["editor"]), None)
    assert out["response"]["claimsOverrideDetails"]["groupOverrideDetails"][
        "groupsToOverride"
    ] == ["editor", "admin"]
    assert added == [
        {"UserPoolId": "us-east-1_Pool", "Username": "SSO_abc", "GroupName": "admin"}
    ]
    pre_token.handler(event("boss@example.com", ["admin"]), None)  # already a member
    assert pre_token.handler(event("someone@example.com", []), None)["response"] == {}
    assert pre_token.handler({"request": {}}, None) == {"request": {}}
    assert len(added) == 1


def test_the_token_hook_makes_its_client_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    from insights_radar_infra.hooks import pre_token

    made: list[str] = []
    fake = types.ModuleType("boto3")

    def client(name: str) -> object:
        made.append(name)
        return object()

    fake.client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "boto3", fake)
    monkeypatch.setattr(pre_token, "_cognito", None)
    first = pre_token._client()
    assert pre_token._client() is first and made == ["cognito-idp"]


def test_password_sign_in_is_unchanged_without_federation(default: Synth) -> None:
    t = default.template
    t.resource_count_is("AWS::Cognito::UserPoolIdentityProvider", 0)
    t.resource_count_is("AWS::Cognito::UserPoolUser", 1)
    assert not any(
        "AIR_IDENTITY_PROVIDER"
        in r["Properties"].get("Environment", {}).get("Variables", {})
        for r in t.find_resources("AWS::Lambda::Function").values()
    )
    pool = next(iter(t.find_resources("AWS::Cognito::UserPool").values()))
    assert "PreTokenGeneration" not in pool["Properties"].get("LambdaConfig", {})


@pytest.mark.parametrize(
    ("ctx", "message"),
    [
        ({"authMode": "saml"}, "authMode must be one of"),
        ({**FEDERATED, "oidcIssuer": "http://idp.example.com"}, "oidcIssuer"),
        ({**FEDERATED, "oidcClientId": ""}, "oidcClientId"),
        (
            {**FEDERATED, "oidcClientSecretArn": "arn:aws:s3:::bucket"},
            "oidcClientSecretArn",
        ),
        ({**FEDERATED, "oidcProviderName": "Cognito"}, "oidcProviderName"),
        ({**FEDERATED, "oidcProviderName": "a_b"}, "oidcProviderName"),
        ({**FEDERATED, "adminEmails": "not-an-email"}, "adminEmails"),
        (
            {
                **FEDERATED,
                "adminEmails": ",".join(f"a{i}@example.com" for i in range(21)),
            },
            "adminEmails",
        ),
        ({**FEDERATED, "oidcClientSecretVersion": "v1"}, "oidcClientSecretVersion"),
        (
            {**FEDERATED, "oidcSignOutPrincipal": "Not A Principal"},
            "oidcSignOutPrincipal",
        ),
        (
            {**FEDERATED, "oidcSignOutPrincipal": "lambda.amazonaws.com"},
            "oidcSignOutPrincipal",
        ),
        ({"oidcIssuer": "https://idp.example.com"}, "need authMode=federated"),
        ({"adminEmails": "a@example.com"}, "need authMode=federated"),
        ({"oidcSignOutPrincipal": "killswitch.example.com"}, "need authMode=federated"),
    ],
)
def test_federation_options_are_checked(
    bundles: None, ctx: dict[str, str], message: str
) -> None:
    from tests.conftest import synth

    with pytest.raises(ValueError, match=message):
        synth(**ctx)


def test_the_api_trusts_unverified_provider_emails_only_when_told(
    federated: Synth,
) -> None:
    def api_env(s: Synth) -> dict[str, str]:
        return next(
            r["Properties"]["Environment"]["Variables"]
            for r in s.template.find_resources("AWS::Lambda::Function").values()
            if "AIR_IDENTITY_PROVIDER"
            in r["Properties"].get("Environment", {}).get("Variables", {})
        )

    assert "AIR_TRUST_PROVIDER_EMAIL" not in api_env(federated)
    from tests.conftest import synth

    trusted = synth(**{**FEDERATED, "oidcTrustEmail": "true"})
    assert api_env(trusted)["AIR_TRUST_PROVIDER_EMAIL"] == "true"


def test_federated_sign_in_uses_short_tokens_a_versioned_secret_and_a_sign_out_role(
    federated: Synth, default: Synth
) -> None:
    from tests.conftest import synth

    def clients(t: Synth) -> list[dict[str, object]]:
        return [
            c["Properties"]
            for c in t.template.find_resources("AWS::Cognito::UserPoolClient").values()
        ]

    # The identity provider's session policy: access 10 minutes, ID one hour, refresh 10 hours.
    for c in clients(federated):
        assert (
            c["AccessTokenValidity"],
            c["IdTokenValidity"],
            c["RefreshTokenValidity"],
        ) == (10, 60, 600)
        assert c["TokenValidityUnits"] == {
            "AccessToken": "minutes",
            "IdToken": "minutes",
            "RefreshToken": "minutes",
        }
    for c in clients(default):
        assert (c["AccessTokenValidity"], c["RefreshTokenValidity"]) == (60, 720)
    assert not any(
        "Cognito sign-out" in json.dumps(r)
        or "identity provider sign-out" in json.dumps(r)
        for r in federated.template.find_resources("AWS::IAM::Role").values()
    )
    assert "SignOutRoleArn" not in federated.template.find_outputs("*")
    assert "IdentityProviderName" in federated.template.find_outputs("*")

    version = "a1b2c3d4-e5f6-7a8b-9c0d-112233445566"
    s = synth(
        **{
            **FEDERATED,
            "oidcClientSecretVersion": version,
            "oidcSignOutPrincipal": "killswitch.example.com",
        }
    )
    idp = next(
        iter(
            s.template.find_resources("AWS::Cognito::UserPoolIdentityProvider").values()
        )
    )
    assert f":SecretString:::{version}}}}}" in json.dumps(
        idp["Properties"]["ProviderDetails"]["client_secret"]
    )
    roles = [
        r
        for r in s.template.find_resources("AWS::IAM::Role").values()
        if r["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Principal"]
        == {"Service": "killswitch.example.com"}
    ]
    assert len(roles) == 1
    role_id = next(
        k
        for k, v in s.template.find_resources("AWS::IAM::Role").items()
        if v == roles[0]
    )
    (policy,) = [
        p["Properties"]["PolicyDocument"]["Statement"]
        for p in s.template.find_resources("AWS::IAM::Policy").values()
        if {"Ref": role_id} in p["Properties"]["Roles"]
    ]
    assert policy == [
        {
            "Action": [
                "cognito-idp:AdminUserGlobalSignOut",
                "cognito-idp:DescribeIdentityProvider",
                "cognito-idp:ListUsers",
            ],
            "Effect": "Allow",
            "Resource": {"Fn::GetAtt": [policy[0]["Resource"]["Fn::GetAtt"][0], "Arn"]},
        }
    ]
    assert policy[0]["Resource"]["Fn::GetAtt"][0].startswith("AuthUsers")
    outputs = s.template.find_outputs("*")
    assert "SignOutRoleArn" in outputs and "UserPoolId" in outputs
    options = json.loads(outputs["DeployOptions"]["Value"])
    assert (
        options["oidcSignOutPrincipal"] == "killswitch.example.com"
        and options["oidcClientSecretVersion"] == version
    )


def test_the_chat_runtime_can_replace_a_message_the_guardrail_redacted(
    default: Synth,
) -> None:
    """A deployment, 2026-11-03: after a guardrail block Strands deletes the stored event to store the redacted one;
    without DeleteEvent the user saw "Something went wrong (SessionException)" after the guardrail's reply."""
    policies = default.template.find_resources("AWS::IAM::Policy")
    actions: set[str] = set()
    for p in policies.values():
        roles = json.dumps(p["Properties"].get("Roles", []))
        if "ChatRuntime" not in roles:
            continue
        for st in p["Properties"]["PolicyDocument"]["Statement"]:
            a = st["Action"]
            actions.update([a] if isinstance(a, str) else a)
    assert {"bedrock-agentcore:CreateEvent", "bedrock-agentcore:DeleteEvent"} <= actions


def test_api_answers_keep_their_own_security_headers(default: Synth) -> None:
    """/api/* uses the policy that does not override CSP, HSTS or Referrer-Policy, so the API's default-src
    'none' and no-referrer (an unsubscribe token in the URL is never sent on) reach the browser (scan cycles 42
    and 43)."""
    policies = default.resources("AWS::CloudFront::ResponseHeadersPolicy")
    (api_id,) = [k for k in policies if "ApiHeaders" in k]
    (spa_id,) = [k for k in policies if k != api_id]
    config = policies[api_id]["Properties"]["ResponseHeadersPolicyConfig"][
        "SecurityHeadersConfig"
    ]
    assert not config["ContentSecurityPolicy"]["Override"]
    assert (
        not config["StrictTransportSecurity"]["Override"]
        and not config["ReferrerPolicy"]["Override"]
    )
    assert (
        config["FrameOptions"]["Override"] and config["ContentTypeOptions"]["Override"]
    )
    (dist,) = default.props("AWS::CloudFront::Distribution")
    (api,) = [
        b
        for b in dist["DistributionConfig"]["CacheBehaviors"]
        if b["PathPattern"] == "/api/*"
    ]
    assert api["ResponseHeadersPolicyId"] == {"Ref": api_id}
    assert dist["DistributionConfig"]["DefaultCacheBehavior"][
        "ResponseHeadersPolicyId"
    ] == {"Ref": spa_id}
