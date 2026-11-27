# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Every DynamoDB call a Lambda makes is allowed by its role in Terraform.

r147: user_detail moved to policy.batch_get (BatchGetItem) and every unit test passed against moto, which checks
no IAM, while the deployed dashboard answered 500 (AccessDenied). This reads each Lambda's code and the Terraform
file that holds its role, so a new call without its grant fails here instead of on the live stack.

What it checks is the action name. Resource and leading-key scoping are reviewed by hand (docs/security.md) and the
E2E matrix exercises them against the deployed roles; policy.tf holds both the interceptor's and the usage sync's
roles, so for those two the check is against the actions that file grants.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ("get_item", "put_item", "update_item", "delete_item", "query", "scan", "batch_get_item", "batch_write_item",
       "transact_write_items", "transact_get_items")
# lambda file -> the Terraform file that holds its role
ROLES = {"dashboard_api.py": "dashboard.tf", "interceptor.py": "policy.tf", "usage_sync.py": "policy.tf"}


def camel(op):
    return "dynamodb:" + "".join(part.title() for part in op.split("_"))


def called_ops(code):
    ops = set(re.findall(r"\.(" + "|".join(OPS) + r")\(", code))
    ops |= set(re.findall(r'paginate(?:_token)?\([a-z_]*ddb[a-z_]*, "(' + "|".join(OPS) + r')"', code))
    if "policy.batch_get(" in code:
        ops.add("batch_get_item")
    if "policy.policy_items(" in code:  # one Query on the POLICY partition
        ops.add("query")
    return ops


def test_the_dashboard_calls_what_it_should():
    assert {"get_item", "transact_write_items", "batch_get_item"} <= called_ops((ROOT / "lambda" / "dashboard_api.py").read_text())


@pytest.mark.parametrize("lam,tf", sorted(ROLES.items()))
def test_every_dynamodb_call_a_lambda_makes_is_granted_to_its_role(lam, tf):
    ops = called_ops((ROOT / "lambda" / lam).read_text())
    assert ops, f"no DynamoDB call found in {lam}: the pattern no longer matches the code"
    granted = set(re.findall(r'"(dynamodb:[A-Za-z]+)"', (ROOT / "terraform" / tf).read_text()))
    missing = sorted(camel(op) for op in ops if camel(op) not in granted)
    assert not missing, f"terraform/{tf} grants none of {missing}, which lambda/{lam} calls"


def test_each_lambda_role_writes_logs_only_to_its_own_log_group():
    """r193: every Lambda role had AWSLambdaBasicExecutionRole (log writes on *), so a compromised function could
    write into the other functions' decision logs. Each role now has one inline own-logs policy on its own group."""
    tf = "".join((ROOT / "terraform" / f).read_text() for f in ("policy.tf", "dashboard.tf", "gateway.tf", "websearch.tf"))
    assert "AWSLambdaBasicExecutionRole" not in tf
    for res, role in (("interceptor_logs", "interceptor"), ("usage_sync_logs", "usage_sync"), ("dashboard_logs", "dashboard")):
        m = re.search(r'resource "aws_iam_role_policy" "%s" \{(.*?)\n\}' % res, tf, re.S)
        assert m, f"aws_iam_role_policy.{res} is missing"
        body = m.group(1)
        assert f"role = aws_iam_role.{role}.id" in body
        assert re.findall(r'"(logs:[A-Za-z]+)"', body) == ["logs:CreateLogStream", "logs:PutLogEvents"]
        assert re.search(r'Resource = "\$\{aws_cloudwatch_log_group\.%s\.arn\}:\*"' % role, body), body
    # No other policy grants a logs: action to these roles.
    assert len(re.findall(r'"logs:PutLogEvents"', tf)) == 3


@pytest.mark.parametrize("tf,role,prefix", [("gateway.tf", "gateway", "gw"), ("websearch.tf", "websearch", "web")])
def test_only_this_stacks_gateways_may_assume_the_gateway_roles(tf, role, prefix):
    """r193: both bedrock-agentcore trust policies trusted any AgentCore gateway in the account (aws:SourceAccount
    only). Each now also names the stack's own gateway with ArnLike aws:SourceArn."""
    text = (ROOT / "terraform" / tf).read_text()
    block = text[text.index(f'resource "aws_iam_role" "{role}"'):]
    block = block[:block.index("\n}\n")]
    assert 'Service = "bedrock-agentcore.amazonaws.com"' in block
    assert '"aws:SourceAccount" = local.acct' in block
    assert ('ArnLike      = { "aws:SourceArn" = "arn:aws:bedrock-agentcore:${var.region}:${local.acct}:gateway/${var.name}-%s-*" }'
            % prefix) in block


@pytest.mark.parametrize("tf,fn", [("dashboard.tf", "dashboard"), ("policy.tf", "usage_sync")])
def test_checkov_skip_reasons_match_how_the_function_is_invoked(tf, fn):
    """r196 threat model T-068: the dashboard and usage-sync functions carried the interceptor's skip reasons, which
    name a gateway that fronts neither and a synchronous call EventBridge does not make."""
    body = (ROOT / "terraform" / tf).read_text().split(f'resource "aws_lambda_function" "{fn}" {{', 1)[1].split("\n}\n", 1)[0]
    skips = dict(re.findall(r"#checkov:skip=(CKV_AWS_11[56]):(.*)", body))
    assert set(skips) == {"CKV_AWS_115", "CKV_AWS_116"} and all("gateway" not in v for v in skips.values()), skips
    assert ("asynchronously by its EventBridge schedule" in skips["CKV_AWS_116"]) is (fn == "usage_sync")
