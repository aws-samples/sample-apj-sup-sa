"""vllm_ec2_bench — deploy open-source LLMs to AWS EC2 and benchmark with LLMeter.

Model-agnostic infrastructure package. The service-oriented architecture:

1. A :class:`Catalog` wraps hardware + pricing data (cached on disk at a path
   provided by the caller — typically ``models/<name>/catalog_cache.json``).
2. :class:`DeploymentPlan` references instance types by **id** (string).
3. :class:`DeploymentRunner` takes ``(config, catalog=catalog)`` and uses the
   catalog to look up hardware specs + prices at launch time.

This package has no default cache path and no hard-coded instance-type list;
both are supplied by the model package. See ``models/medgemma_27b/`` for a
reference model config.
"""
from .data import (
    KNOWN_MIG_PROFILES,
    Backend,
    CapacityMode,
    Catalog,
    CatalogNotLoaded,
    CatalogStaleError,
    DeploymentPlan,
    ExperimentConfig,
    Family,
    HardwareFacts,
    ModelSpec,
    catalog_meta,
)
from .deployer import DeploymentRunner, DeploymentState, upsert_hf_token

__version__ = "0.3.0"

__all__ = [
    "Backend",
    "CapacityMode",
    "Catalog",
    "CatalogNotLoaded",
    "CatalogStaleError",
    "DeploymentPlan",
    "DeploymentRunner",
    "DeploymentState",
    "ExperimentConfig",
    "Family",
    "HardwareFacts",
    "KNOWN_MIG_PROFILES",
    "ModelSpec",
    "__version__",
    "catalog_meta",
    "upsert_hf_token",
]
