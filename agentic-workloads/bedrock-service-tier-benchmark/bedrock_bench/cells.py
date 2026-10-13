"""Expand config × registry into cells, and build the adapter for a cell.

A **cell** is one tier in one *context*. A context is every other dimension:
(model, endpoint, API, scope, region, prompt size, cache mode). Tiers in the
same context are always compared against each other, never across contexts.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Api, BenchmarkConfig, CacheMode, Endpoint, PromptSize, Scope, Tier
from .registry import ModelSpec, Offering


@dataclass(frozen=True)
class Cell:
    spec: ModelSpec
    offering: Offering
    region: str
    size: PromptSize
    cache: CacheMode
    tier: Tier

    @property
    def endpoint(self) -> Endpoint:
        return self.offering.endpoint

    @property
    def api(self) -> Api:
        return self.offering.api

    @property
    def scope(self) -> Scope:
        return self.offering.scope

    @property
    def model_id(self) -> str:
        return self.offering.model_id

    @property
    def context_key(self) -> str:
        """Identifies the comparison group (everything except tier)."""
        return "|".join(
            (
                self.spec.key,
                self.endpoint.value,
                self.api.value,
                self.scope.value,
                self.region,
                self.size.value,
                self.cache.value,
            )
        )

    @property
    def label(self) -> str:
        return f"{self.context_key}|{self.tier.value}"

    @property
    def domain(self) -> str:
        """Pacing domain: requests to the same model id + endpoint + region run serially."""
        return f"{self.endpoint.value}|{self.model_id}|{self.region}"

    def dims(self) -> dict[str, str]:
        return {
            "model": self.spec.key,
            "family": self.spec.family,
            "display_name": self.spec.display_name,
            "model_id": self.model_id,
            "endpoint": self.endpoint.value,
            "api": self.api.value,
            "scope": self.scope.value,
            "region": self.region,
            "prompt_size": self.size.value,
            "cache": self.cache.value,
            "tier": self.tier.value,
        }


def _cache_ok(spec: ModelSpec, size: PromptSize, cache: CacheMode) -> bool:
    if cache is CacheMode.COLD:
        return True
    if cache is CacheMode.WARM_IMPLICIT and not spec.cache.implicit:
        return False
    if cache is CacheMode.WARM_EXPLICIT and not spec.cache.explicit:
        return False
    # Warm only makes sense when the prefix can be cached at all.
    minimum = spec.cache.min_tokens or 1024
    return size.input_tokens >= minimum


def expand_cells(config: BenchmarkConfig, specs: list[ModelSpec]) -> list[Cell]:
    """Every supported cell implied by ``config``.

    A context is kept only when at least one non-default tier *and* the default
    tier are both available, so every tier has its baseline. Each offering is
    benchmarked from the first configured region it serves.
    """
    wanted_tiers = set(config.tiers) | {Tier.DEFAULT}
    cells: list[Cell] = []
    for spec in specs:
        for off in spec.offerings:
            if off.endpoint not in config.endpoints or off.api not in config.apis:
                continue
            if off.scope not in config.scopes:
                continue
            region = next((r for r in config.regions if r in off.regions), None)
            if region is None:
                continue
            tiers = [t for t in off.tiers if t in wanted_tiers]
            if Tier.DEFAULT not in tiers or len(tiers) < 2:
                continue
            tiers.sort(key=lambda t: list(Tier).index(t))
            for size in config.prompt_sizes:
                for cache in config.cache_modes:
                    if not _cache_ok(spec, size, cache):
                        continue
                    cells.extend(Cell(spec, off, region, size, cache, t) for t in tiers)
    return cells
