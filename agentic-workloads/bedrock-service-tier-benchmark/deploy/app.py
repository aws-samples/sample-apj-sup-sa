#!/usr/bin/env python3
"""CDK app: always-on Bedrock service-tier benchmark (Aurora + API + worker + web)."""

from __future__ import annotations

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from stacks.api_stack import ApiStack
from stacks.data_stack import DataStack
from stacks.web_stack import WebStack
from stacks.worker_stack import WorkerStack

app = cdk.App()
auth_mode = app.node.try_get_context("auth_mode") or "cognito"
if auth_mode not in ("cognito", "midway"):
    raise ValueError("auth_mode must be 'cognito' or 'midway'")
env = cdk.Environment(account=app.node.try_get_context("account"), region="us-east-1")

ctx = app.node.try_get_context
data = DataStack(app, "BedrockTierBench-Data", min_acu=float(ctx("aurora_min_acu") or 0), env=env)
web = WebStack(app, "BedrockTierBench-Web", domain_name=ctx("domain_name") or "", env=env)
api = ApiStack(
    app,
    "BedrockTierBench-Api",
    data=data,
    web_origin=web.origin,
    auth_mode=auth_mode,
    federate={
        "issuer_url": ctx("federate_issuer_url") or "",
        "client_id": ctx("federate_client_id") or "",
        "client_secret_name": ctx("federate_client_secret_name") or "bedrock-tier-bench/federate-client-secret",
    },
    env=env,
)
WorkerStack(
    app,
    "BedrockTierBench-Worker",
    data=data,
    schedule_expression=ctx("schedule_expression") or "cron(0 18 * * ? *)",
    benchmark_args=ctx("benchmark_args") or "--preset quick",
    agent_model_id=ctx("agent_model_id") or "us.anthropic.claude-sonnet-5-5",
    arch=ctx("worker_arch") or "arm64",
    env=env,
)

cdk.Tags.of(app).add("project", "bedrock-tier-bench")
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
app.synth()
