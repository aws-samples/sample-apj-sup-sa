"""Pydantic data models + Catalog service for vllm_ec2_bench.

Public API:
    HardwareFacts, Family
    ModelSpec, Backend
    DeploymentPlan, CapacityMode, KNOWN_MIG_PROFILES
    ExperimentConfig
    Catalog, CatalogNotLoaded, CatalogStaleError, catalog_meta
"""
from .catalog import (
    Catalog,
    CatalogNotLoaded,
    CatalogStaleError,
    catalog_meta,
)
from .deployment import KNOWN_MIG_PROFILES, CapacityMode, DeploymentPlan
from .experiment import ExperimentConfig
from .hardware_facts import Family, HardwareFacts
from .model import Backend, ModelSpec

__all__ = [
    "Backend",
    "CapacityMode",
    "Catalog",
    "CatalogNotLoaded",
    "CatalogStaleError",
    "DeploymentPlan",
    "ExperimentConfig",
    "Family",
    "HardwareFacts",
    "KNOWN_MIG_PROFILES",
    "ModelSpec",
    "catalog_meta",
]
