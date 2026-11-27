# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Runtime configuration from environment variables set by the CDK stack."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from insights_radar_agents.models import Guardrail


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    region: str
    api_url: str
    gateway_url: str | None
    guardrail: Guardrail | None
    memory_id: str | None
    chat_model_key: str | None
    # Each runtime's own IAM-authenticated function URL for its /agent route (empty on older stacks,
    # which served the route on the HTTP API).
    agent_tools_url: str = ""
    chat_usage_url: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RuntimeSettings:
        e = os.environ if env is None else env
        guardrail_id = e.get("AIR_GUARDRAIL_ID") or ""
        return cls(
            region=e.get("AWS_REGION") or e.get("AWS_DEFAULT_REGION") or "us-east-1",
            api_url=(e.get("AIR_API_URL") or "").rstrip("/"),
            gateway_url=e.get("AIR_GATEWAY_URL") or None,
            guardrail=Guardrail(guardrail_id, e.get("AIR_GUARDRAIL_VERSION") or "DRAFT") if guardrail_id else None,
            memory_id=e.get("AIR_MEMORY_ID") or None,
            chat_model_key=e.get("AIR_CHAT_MODEL_KEY") or None,
            agent_tools_url=(e.get("AIR_AGENT_TOOLS_URL") or "").rstrip("/"),
            chat_usage_url=(e.get("AIR_CHAT_USAGE_URL") or "").rstrip("/"),
        )


# HTTP client loggers log every request URL at INFO. An admin-attached MCP server URL can carry an API
# key in its query string, and chat API URLs carry users' search text, so these stay at WARNING. So does
# strands_tools: its browser logs every whole navigate action, query included, and its code interpreter
# the model-written session descriptions, beside the runtime's own hashed navigation line (P3-083).
QUIET_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2", "mcp", "strands_tools")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
