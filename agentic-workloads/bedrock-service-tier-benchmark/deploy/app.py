#!/usr/bin/env python3
"""CDK app: always-on Bedrock service-tier benchmark (Aurora + API + worker + web)."""

from __future__ import annotations

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from stacks.data_stack import DataStack

app = cdk.App()
auth_mode = app.node.try_get_context("auth_mode") or "cognito"
if auth_mode not in ("cognito", "midway"):
    raise ValueError("auth_mode must be 'cognito' or 'midway'")
env = cdk.Environment(account=app.node.try_get_context("account"), region="us-east-1")

data = DataStack(app, "BedrockTierBench-Data", env=env)

cdk.Tags.of(app).add("project", "bedrock-tier-bench")
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
app.synth()
