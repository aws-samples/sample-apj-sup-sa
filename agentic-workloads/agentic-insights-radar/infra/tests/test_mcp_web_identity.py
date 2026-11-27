# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The deploy options mcpIssuer and mcpAudience: an MCP client signs in with its own id token."""

from __future__ import annotations

import json
from typing import Any

import pytest
from aws_cdk.assertions import Match

from tests.conftest import Synth
from tests.test_stack import FEDERATED

MCP = {
    **FEDERATED,
    "mcpIssuer": "https://idp.example.com",
    "mcpAudience": "https://app.example.com",
}


@pytest.fixture(scope="module")
def mcp(bundles: None) -> Synth:
    from tests.conftest import synth

    return synth(**MCP)


@pytest.fixture(scope="module")
def federated(bundles: None) -> Synth:
    from tests.conftest import synth

    return synth(**FEDERATED)


def _role(s: Synth) -> dict[str, Any]:
    roles = [
        r
        for r in s.props("AWS::IAM::Role")
        if "MCP clients" in str(r.get("Description", ""))
    ]
    assert len(roles) == 1
    return roles[0]


def test_the_role_trusts_the_issuer_for_the_audience_with_the_subject_as_session_name(
    mcp: Synth,
) -> None:
    (provider,) = mcp.props("AWS::IAM::OIDCProvider")
    assert provider["Url"] == "https://idp.example.com"
    assert provider["ClientIdList"] == ["https://app.example.com"]
    role = _role(mcp)
    assert role["MaxSessionDuration"] == 3600
    (stmt,) = role["AssumeRolePolicyDocument"]["Statement"]
    assert stmt["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert stmt["Condition"] == {
        "StringEquals": {
            "idp.example.com:aud": "https://app.example.com",
            "sts:RoleSessionName": "${idp.example.com:sub}",
        }
    }


def test_the_role_may_invoke_only_the_iam_routes(mcp: Synth) -> None:
    policies = [
        p
        for p in mcp.props("AWS::IAM::Policy")
        if any(
            r.get("Ref", "").startswith("BackendMcpRole") for r in p.get("Roles", [])
        )
    ]
    statements = [s for p in policies for s in p["PolicyDocument"]["Statement"]]
    assert [s["Action"] for s in statements] == ["execute-api:Invoke"]
    assert json.dumps(statements[0]["Resource"]).endswith('"/*/*/iam/api/*"]]}')


def test_the_iam_routes_use_the_iam_authorizer(mcp: Synth) -> None:
    routes = {r["RouteKey"]: r for r in mcp.props("AWS::ApiGatewayV2::Route")}
    assert routes["ANY /iam/api/{proxy+}"]["AuthorizationType"] == "AWS_IAM"
    assert routes["ANY /api/{proxy+}"]["AuthorizationType"] == "JWT"


def test_the_api_function_is_told_the_role_issuer_audience_and_url(mcp: Synth) -> None:
    (fn,) = [
        f
        for f in mcp.props("AWS::Lambda::Function")
        if f.get("Handler") == "insights_radar.api.app.handler"
        and "AIR_WATCH_FUNCTION" in f["Environment"]["Variables"]
    ]
    env = fn["Environment"]["Variables"]
    assert env["AIR_MCP_ISSUER"] == "https://idp.example.com"
    assert env["AIR_MCP_AUDIENCE"] == "https://app.example.com"
    assert "Fn::GetAtt" in env["AIR_MCP_ROLE_ARN"]
    # The API Gateway origin is not in the function's environment, so the public config cannot name it.
    assert "AIR_MCP_API_URL" not in env


def test_off_by_default(default: Synth, federated: Synth) -> None:
    for s in (default, federated):
        s.template.resource_count_is("AWS::IAM::OIDCProvider", 0)
        keys = [r["RouteKey"] for r in s.props("AWS::ApiGatewayV2::Route")]
        assert not any("/iam/" in k for k in keys)
        (fn,) = [
            f
            for f in s.props("AWS::Lambda::Function")
            if f.get("Handler") == "insights_radar.api.app.handler"
            and "AIR_WATCH_FUNCTION" in f["Environment"]["Variables"]
        ]
        assert not any(k.startswith("AIR_MCP_") for k in fn["Environment"]["Variables"])
        assert not s.template.find_outputs("McpApiUrl")


def test_the_options_are_kept_for_the_next_deploy(mcp: Synth) -> None:
    out = mcp.template.find_outputs("DeployOptions")["DeployOptions"]["Value"]
    options = json.loads(out) if isinstance(out, str) else out
    assert options["mcpIssuer"] == "https://idp.example.com"
    assert options["mcpAudience"] == "https://app.example.com"


@pytest.mark.parametrize(
    ("ctx", "message"),
    [
        (
            {"mcpIssuer": "https://idp.example.com", "mcpAudience": "a"},
            "need authMode=federated",
        ),
        (
            {**FEDERATED, "mcpIssuer": "http://idp.example.com", "mcpAudience": "a"},
            "mcpIssuer must be",
        ),
        ({**FEDERATED, "mcpIssuer": "https://idp.example.com"}, "mcpAudience must be"),
        ({**FEDERATED, "mcpAudience": "a"}, "mcpIssuer must be"),
        (
            {**FEDERATED, "mcpIssuer": "https://idp.example.com", "mcpAudience": "a b"},
            "mcpAudience must be",
        ),
    ],
)
def test_the_options_are_checked(
    bundles: None, ctx: dict[str, str], message: str
) -> None:
    from tests.conftest import synth

    with pytest.raises(ValueError, match=message):
        synth(**ctx)


def test_no_unsuppressed_cdk_nag_findings(mcp: Synth) -> None:
    # The deploy synthesises with cdk-nag errors as failures: the MCP role's one wildcard is suppressed
    # by an exact pattern (nag.py), anything else still fails.
    errors = mcp.annotations.find_error(
        "*", Match.string_like_regexp("AwsSolutions-.*")
    )
    warnings = mcp.annotations.find_warning(
        "*", Match.string_like_regexp("AwsSolutions-.*")
    )
    assert [e.entry.data for e in errors] == []
    assert [w.entry.data for w in warnings] == []


def test_the_model_backed_routes_keep_their_limit_under_iam(mcp: Synth) -> None:
    routes = {r["RouteKey"]: r for r in mcp.props("AWS::ApiGatewayV2::Route")}
    for key in (
        "POST /iam/api/graph/search",
        "POST /iam/api/graph/ask",
        "POST /iam/api/products/{id}/ask",
    ):
        assert routes[key]["AuthorizationType"] == "AWS_IAM"
    (stage,) = mcp.props("AWS::ApiGatewayV2::Stage")
    settings = stage["RouteSettings"]
    assert (
        settings["POST /iam/api/graph/ask"]["ThrottlingRateLimit"]
        == settings["POST /api/graph/ask"]["ThrottlingRateLimit"]
    )


def test_an_existing_provider_is_used_instead_of_a_new_one(bundles: None) -> None:
    from tests.conftest import synth

    arn = "arn:aws:iam::111122223333:oidc-provider/idp.example.com"
    s = synth(**MCP, mcpProviderArn=arn)
    s.template.resource_count_is("AWS::IAM::OIDCProvider", 0)
    (stmt,) = _role(s)["AssumeRolePolicyDocument"]["Statement"]
    assert stmt["Principal"] == {"Federated": arn}
    out = s.template.find_outputs("DeployOptions")["DeployOptions"]["Value"]
    assert json.loads(out)["mcpProviderArn"] == arn


@pytest.mark.parametrize(
    ("ctx", "message"),
    [
        ({**MCP, "mcpIssuer": "https://idp.example.com:8443"}, "without a port"),
        (
            {**MCP, "mcpIssuer": "https://idp.example.com/"},
            "without a port or a trailing slash",
        ),
        (
            {
                **MCP,
                "mcpProviderArn": "arn:aws:iam::111122223333:oidc-provider/other.example.com",
            },
            "mcpProviderArn",
        ),
        (
            {**MCP, "mcpIssuer": "https://other.example.com"},
            "mcpIssuer must be oidcIssuer",
        ),
        ({**MCP, "mcpProviderArn": "not-an-arn"}, "mcpProviderArn"),
        (
            {
                "mcpProviderArn": "arn:aws:iam::111122223333:oidc-provider/idp.example.com"
            },
            "need authMode=federated",
        ),
    ],
)
def test_the_issuer_and_provider_are_checked(
    bundles: None, ctx: dict[str, str], message: str
) -> None:
    from tests.conftest import synth

    with pytest.raises(ValueError, match=message):
        synth(**ctx)


def test_the_api_gateway_url_is_a_stack_output(mcp: Synth) -> None:
    out = mcp.template.find_outputs("McpApiUrl")["McpApiUrl"]
    assert "AIR_MCP_API_URL" in out["Description"]
    assert "BackendApi" in str(out["Value"])


def test_an_issuer_with_a_trailing_slash_matches_the_mcp_issuer(bundles: None) -> None:
    from tests.conftest import synth

    s = synth(**{**MCP, "oidcIssuer": "https://idp.example.com/"})
    s.template.resource_count_is("AWS::IAM::OIDCProvider", 1)
