# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Bedrock model construction from the shared model catalog."""

from __future__ import annotations

from dataclasses import dataclass

from botocore.config import Config
from insights_radar.modules.catalog import ModelCatalog, ModelSpec
from strands.models import BedrockModel
from strands.models.model import CacheConfig

# Autonomous turns are capped so one runaway turn cannot burn a large slice of the budget.
MODULE_MAX_TOKENS = 8192
# A draft is one draft_save call carrying the whole article, so the Drafts module may answer at length.
DRAFT_MAX_TOKENS = 16384
CHAT_MAX_TOKENS = 4096
# One retry layer: Strands retries throttling and transient errors (retry.py), so botocore makes one
# attempt. A stream that sends nothing for 5 minutes is abandoned, so a stalled call cannot hold a run
# (and its concurrency slot) for hours: at worst the 6 Strands attempts of 5 minutes each.
BOTO = Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 1, "mode": "standard"})


@dataclass(frozen=True, slots=True)
class Guardrail:
    identifier: str
    version: str


class ModelFactory:
    def __init__(self, region: str, guardrail: Guardrail | None = None) -> None:
        self._region = region
        self._guardrail = guardrail

    @staticmethod
    def spec(key: str | None) -> ModelSpec:
        return ModelCatalog.get(key)

    def build(self, key: str | None, max_tokens: int) -> BedrockModel:
        spec = self.spec(key)
        kwargs: dict[str, object] = {
            "model_id": spec.profile_id,
            "region_name": self._region,
            "max_tokens": max_tokens,
            "boto_client_config": BOTO,
        }
        if spec.supports_cache:
            kwargs["cache_config"] = CacheConfig(strategy="auto", system_prompt_ttl=True, tools_ttl=True)
        if self._guardrail:
            kwargs.update(
                guardrail_id=self._guardrail.identifier,
                guardrail_version=self._guardrail.version,
                guardrail_trace="disabled",
                # Evaluate the newest user turn only: re-scanning the whole conversation on every
                # turn multiplies guardrail cost and blocks on text the model already accepted.
                guardrail_latest_message=True,
            )
        return BedrockModel(**kwargs)  # type: ignore[arg-type]
