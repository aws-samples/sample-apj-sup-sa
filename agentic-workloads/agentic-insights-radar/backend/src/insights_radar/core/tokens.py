# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Model-agnostic weighted-token budgets (ported from the internal radar sec. 50).

``weighted = input*1 + output*5 + cache_write*1.25 + cache_read*0.1``. Weighting by the
universal LLM cost shape prices cache reads correctly without a per-model dollar table, so a
model swap cannot mis-price the gate. Thresholds: 70 % warn, 90 % finish-and-exit, 100 %
closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from insights_radar.core.errors import ValidationError

HARD_MAX_DAILY_TOKENS = 100_000_000
DEFAULT_DAILY_TOKENS = 2_000_000
# What a model's tier costs relative to the balanced tier the weights were calibrated on. Without
# this the same daily budget bought roughly thirty times the dollars on the most expensive model as
# on the cheapest, and the per-module ceiling had no dollar bound at all.
TIER_MULTIPLIER = {"fast": 0.3, "balanced": 1.0, "max": 4.0}


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @property
    def raw_total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True, slots=True)
class TokenWeights:
    input: float = 1.0
    output: float = 5.0
    cache_write: float = 1.25
    cache_read: float = 0.1
    tier: float = 1.0  # the model's price tier relative to "balanced" (see TIER_MULTIPLIER)

    @classmethod
    def for_tier(cls, tier: str) -> TokenWeights:
        """Weights scaled for a model tier, so one weighted token means roughly one unit of money
        whichever model a module runs on."""
        return cls(tier=TIER_MULTIPLIER.get(tier, 1.0))

    def weigh(self, usage: TokenUsage) -> int:
        value = (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_write_tokens * self.cache_write
            + usage.cache_read_tokens * self.cache_read
        )
        return round(value * self.tier)


class BudgetState(StrEnum):
    OPEN = "open"
    WARN = "warn"
    HALF_OPEN = "half_open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    consumed: int
    limit: int

    @property
    def ratio(self) -> float:
        return 1.0 if self.limit <= 0 else self.consumed / self.limit

    @property
    def state(self) -> BudgetState:
        r = self.ratio
        if r >= 1.0:
            return BudgetState.CLOSED
        if r >= 0.9:
            return BudgetState.HALF_OPEN
        if r >= 0.7:
            return BudgetState.WARN
        return BudgetState.OPEN

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.consumed)

    @staticmethod
    def validate_limit(limit: object) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValidationError("budget limit must be an integer")
        if limit < 0 or limit > HARD_MAX_DAILY_TOKENS:
            raise ValidationError(f"budget limit must be between 0 and {HARD_MAX_DAILY_TOKENS}")
        return limit
