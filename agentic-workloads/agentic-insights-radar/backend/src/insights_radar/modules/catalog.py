# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Selectable Bedrock models (successor of the internal radar's agents/_shared/models.py).

Only Anthropic and Amazon families are listed: both publish ``global.`` cross-Region inference
profiles, accept prompt caching and drive tools reliably, so one invocation path serves all of
them. Verified as ``global.`` inference profiles in us-east-1 on 2 Oct 2026. Claude 4.6+ rejects
an explicit ``temperature``; the runtime omits it for those models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from insights_radar.core.errors import ValidationError


@dataclass(frozen=True, slots=True)
class ModelSpec:
    key: str
    label: str
    profile_id: str
    supports_temperature: bool
    supports_cache: bool
    # USD per 1M tokens for the global profile, display only (the gate is weighted tokens). From the AWS
    # Price List (us-east-1) on 2 Oct 2026.
    price_in_out: tuple[float, float]
    tier: str  # "fast" | "balanced" | "max"


DEFAULT_MODEL_KEY = "sonnet-5-5"
DEFAULT_CHAT_MODEL_KEY = "sonnet-5-5"

_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        "haiku-4-5",
        "Claude Haiku 4.5",
        "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        True,
        True,
        (1.0, 5.0),
        "fast",
    ),
    ModelSpec("nova-2-lite", "Amazon Nova 2 Lite", "global.amazon.nova-2-lite-v1:0", True, True, (0.3, 2.5), "fast"),
    ModelSpec(
        "sonnet-5-5", "Claude Sonnet 5.5", "global.anthropic.claude-sonnet-5-5", False, True, (2.0, 10.0), "balanced"
    ),
    ModelSpec(
        "sonnet-4-6", "Claude Sonnet 4.6", "global.anthropic.claude-sonnet-4-6", True, True, (3.0, 15.0), "balanced"
    ),
    ModelSpec("opus-5-5", "Claude Opus 5.5", "global.anthropic.claude-opus-5-5", False, True, (4.0, 20.0), "max"),
    ModelSpec("fable-5-1", "Claude Fable 5.1", "global.anthropic.claude-fable-5-1", False, True, (10.0, 50.0), "max"),
)


class ModelCatalog:
    MODELS: ClassVar[dict[str, ModelSpec]] = {m.key: m for m in _MODELS}

    @classmethod
    def get(cls, key: str | None) -> ModelSpec:
        """Unknown or empty keys fall back to the default, so a stale config never breaks a run."""
        return cls.MODELS.get(key or "", cls.MODELS[DEFAULT_MODEL_KEY])

    @classmethod
    def require(cls, key: str) -> ModelSpec:
        if key not in cls.MODELS:
            raise ValidationError(f"unknown model {key!r}; choose one of {', '.join(cls.MODELS)}")
        return cls.MODELS[key]

    @classmethod
    def public(cls) -> list[dict[str, object]]:
        return [
            {
                "key": m.key,
                "label": m.label,
                "tier": m.tier,
                "price_in_out": list(m.price_in_out),
                "default": m.key == DEFAULT_MODEL_KEY,
            }
            for m in _MODELS
        ]

    @staticmethod
    def usd_estimate(
        spec: ModelSpec, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0
    ) -> float:
        p_in, p_out = spec.price_in_out
        cost = (input_tokens + cache_write * 1.25 + cache_read * 0.1) * p_in + output_tokens * p_out
        return round(cost / 1_000_000, 6)
