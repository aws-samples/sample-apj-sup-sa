#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""CDK entry point. Deploy with ``deployment/deploy.sh``, not ``cdk deploy`` directly.

deploy.sh reads back the options an existing deployment was created with (DeployOptions) and stops
before a change that deletes data. A direct ``cdk deploy -c adminEmail=you@example.com`` does neither:
every option falls back to its default, and on a deployment created before ``chatMemoryKey`` existed
the default ``data-key`` replaces the chat memory and deletes every user's chat history (r3 P3-054).
If you must, pass every option the deployment uses, ``-c chatMemoryKey=aws-owned`` included.
"""

from __future__ import annotations

import os

import aws_cdk as cdk

from insights_radar_infra.application import Application

app = cdk.App()
Application(
    app, os.environ.get("CDK_DEFAULT_ACCOUNT"), os.environ.get("CDK_DEFAULT_REGION")
).build()
app.synth()
