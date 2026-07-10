"""MedGemma-27B benchmark configuration.

Public API::

    from models.medgemma_27b import (
        MEDGEMMA_27B,          # ModelSpec
        EXPERIMENTS,           # dict[str, ExperimentConfig]
        INSTANCE_TYPES,        # list[str] — derived from EXPERIMENTS
        CATALOG_CACHE,         # Path — this model's on-disk cache
        DEFAULT_REGIONS,       # list[str] — regions priced by default
        load_catalog,          # factory: returns a loaded Catalog
        refresh_catalog,       # factory: refreshes + persists this model's cache
        SYSTEM_PROMPT,
        SEED_INPUT,
    )

The catalog **data** for this model lives at
``models/medgemma_27b/catalog_cache.json``. The framework (``vllm_ec2_bench``)
contains only the Catalog *code* — cache path + instance list come from here.
"""
from pathlib import Path
from typing import TYPE_CHECKING

from .experiments import EXPERIMENTS, development_experiments, get
from .model_spec import MEDGEMMA_27B
from .prompts import SEED_INPUT, SYSTEM_PROMPT

if TYPE_CHECKING:
    from vllm_ec2_bench import Catalog


# -----------------------------------------------------------------------------
# Catalog configuration (project decisions that the framework shouldn't know)
# -----------------------------------------------------------------------------
CATALOG_CACHE: Path = Path(__file__).parent / "catalog_cache.json"
"""On-disk catalog cache for this model. Checked into git."""

INSTANCE_TYPES: list[str] = sorted(
    {cfg.deployment.instance_type for cfg in EXPERIMENTS.values()}
)
"""Every instance type referenced by this model's experiments (sorted)."""

DEFAULT_REGIONS: list[str] = ["us-west-2", "us-east-2", "us-east-1"]
"""Regions priced by default when this model's catalog is refreshed.

Matches the project's regional convention. Notebook callers can override via
``refresh_catalog(regions=[...])`` or ``regions_extend=[...]``.
"""


# -----------------------------------------------------------------------------
# Catalog factories — thin wrappers around the framework's Catalog class.
# -----------------------------------------------------------------------------
def load_catalog(
    *,
    auto_refresh: bool = True,
    max_age_hours_prices: int = 24,
    max_age_hours_hardware: int = 24 * 30,
    offline_ok: bool = True,
    regions: list[str] | None = None,
) -> "Catalog":
    """Load this model's catalog (auto-refreshes if stale).

    Parameters
    ----------
    auto_refresh
        If True (default), refresh the cache when it's missing or stale.
    max_age_hours_prices, max_age_hours_hardware
        Staleness thresholds per section.
    offline_ok
        True (default) falls back to the on-disk cache on refresh failure.
        Pass False in the notebook to fail loudly if AWS is unreachable.
    regions
        Regions to price on refresh. Defaults to :data:`DEFAULT_REGIONS`.
    """
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
    """Refresh this model's catalog cache from AWS and persist to disk.

    Parameters
    ----------
    regions
        Regions to price. Defaults to :data:`DEFAULT_REGIONS`.
    regions_extend
        Additional regions to append.
    hardware, prices
        Control which sections are refreshed.
    """
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
    "MEDGEMMA_27B",
    "SEED_INPUT",
    "SYSTEM_PROMPT",
    "development_experiments",
    "get",
    "load_catalog",
    "refresh_catalog",
]
