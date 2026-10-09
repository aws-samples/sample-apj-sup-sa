#!/usr/bin/env python3
"""CDK app for step 0: the CloudWatch Omni prerequisites for the samples."""

import os
import sys
from pathlib import Path

import aws_cdk as cdk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.env_file import load_env
from omni_prereqs.stack import OmniPrereqsStack


load_env(ROOT / ".env")
app = cdk.App()
OmniPrereqsStack(
    app, "OmniSamplesPrereqs",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("AWS_REGION") or os.environ.get("CDK_DEFAULT_REGION"),
    ),
    description="CloudWatch Omni samples, step 0: space, dataset integration, roles, shared resources",
)
app.synth()
