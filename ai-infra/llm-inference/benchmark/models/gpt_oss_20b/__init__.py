"""gpt-oss-20b benchmark configuration."""
from pathlib import Path
from typing import TYPE_CHECKING

from .experiments import EXPERIMENTS, development_experiments, get
from .model_spec import GPT_OSS_20B
from .prompts import SEED_INPUT, SYSTEM_PROMPT

if TYPE_CHECKING:
    from vllm_ec2_bench import Catalog


CATALOG_CACHE: Path = Path(__file__).parent / "catalog_cache.json"

INSTANCE_TYPES: list[str] = sorted(
    {cfg.deployment.instance_type for cfg in EXPERIMENTS.values()}
)

DEFAULT_REGIONS: list[str] = ["us-west-2", "us-east-2", "us-east-1"]


def load_catalog(
    *,
    auto_refresh: bool = True,
    max_age_hours_prices: int = 24,
    max_age_hours_hardware: int = 24 * 30,
    offline_ok: bool = True,
    regions: list[str] | None = None,
) -> "Catalog":
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
    "GPT_OSS_20B",
    "INSTANCE_TYPES",
    "SEED_INPUT",
    "SYSTEM_PROMPT",
    "development_experiments",
    "get",
    "load_catalog",
    "refresh_catalog",
]
