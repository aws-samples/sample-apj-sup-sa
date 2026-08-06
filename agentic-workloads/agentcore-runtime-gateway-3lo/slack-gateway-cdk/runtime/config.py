# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Runtime settings, loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    region: str
    user_pool_id: str
    client_id: str
    client_secret: str
    username: str
    password: str
    gateway_url: str
    mcp_protocol_version: str
    model_id: str

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            region=os.environ.get("AWS_REGION", "us-east-1"),
            user_pool_id="",
            client_id="",
            client_secret="",
            username="",
            password="",
            gateway_url=os.environ["GATEWAY_URL"],
            mcp_protocol_version=os.environ.get(
                "MCP_PROTOCOL_VERSION", "2025-11-25"
            ),
            model_id=os.environ.get(
                "BEDROCK_MODEL_ID",
                "global.anthropic.claude-sonnet-4-6",
            ),
        )
