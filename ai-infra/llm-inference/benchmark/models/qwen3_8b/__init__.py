"""Qwen3-8B benchmark configuration.

Public API::

    from models.qwen3_8b import (
        QWEN3_8B,              # ModelSpec
        EXPERIMENTS,           # dict[str, ExperimentConfig]
        INSTANCE_TYPES,        # list[str] -- derived from EXPERIMENTS
        CATALOG_CACHE,         # Path -- this model's on-disk cache
        DEFAULT_REGIONS,       # list[str] -- regions priced by default
        load_catalog,          # factory: returns a loaded Catalog
        refresh_catalog,       # factory: refreshes + persists this model's cache
        SYSTEM_PROMPT,
        SEED_INPUT,
    )
"""
from pathlib import Path
from typing import TYPE_CHECKING

from .experiments import EXPERIMENTS, development_experiments, get
from .model_spec import QWEN3_8B
from .prompts import SEED_INPUT, SYSTEM_PROMPT

if TYPE_CHECKING:
    from vllm_ec2_bench import Catalog


CATALOG_CACHE: Path = Path(__file__).parent / "catalog_cache.json"
"""On-disk catalog cache for this model. Checked into git."""

INSTANCE_TYPES: list[str] = sorted(
    {cfg.deployment.instance_type for cfg in EXPERIMENTS.values()}
)
"""Every instance type referenced by this model's experiments (sorted)."""

DEFAULT_REGIONS: list[str] = ["us-west-2", "us-east-2", "us-east-1"]


def load_catalog(
    *,
    auto_refresh: bool = True,
    max_age_hours_prices: int = 24,
    max_age_hours_hardware: int = 24 * 30,
    offline_ok: bool = True,
    regions: list[str] | None = None,
) -> "Catalog":
    """Load this model's catalog (auto-refreshes if stale)."""
    from vllm_ec2_bench import Catalog

    return Catalog(CATALOG_CACHE).load(
        auto_refresh=auto_refresh,
        max_age_hours_prices=max_age_hours_prices,
        max_age_hours_hardware=max_age_hours_hardware,
        refresh_regions=regions or DEFAULT_REGIONS,
        refresh_instance_types=INSTANCE_TYPES,
        offline_ok=offline_ok,
    )


def refresh_catalog(
    *,
    regions: list[str] | None = None,
    regions_extend: list[str] | None = None,
    hardware: bool = True,
    prices: bool = True,
) -> "Catalog":
    """Refresh this model's catalog cache from AWS and persist to disk."""
    from vllm_ec2_bench import Catalog

    return Catalog(CATALOG_CACHE).refresh(
        regions=regions or DEFAULT_REGIONS,
        regions_extend=regions_extend,
        instance_types=INSTANCE_TYPES,
        hardware=hardware,
        prices=prices,
    )


__all__ = [
    "CATALOG_CACHE",
    "DEFAULT_REGIONS",
    "EXPERIMENTS",
    "INSTANCE_TYPES",
    "QWEN3_8B",
    "SEED_INPUT",
    "SYSTEM_PROMPT",
    "development_experiments",
    "get",
    "load_catalog",
    "refresh_catalog",
]
