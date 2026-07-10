"""llm_batch_deploy — Deploy LLMs to AWS Batch for batch inference.

Top-level ``__init__`` defers imports of the heavy submodules so the
runtime container (which only installs ``runtime``'s deps and doesn't
need pydantic/boto3) can import ``llm_batch_deploy.runtime`` directly
without triggering a cascade.

Consumers should import submodules explicitly:

    from llm_batch_deploy.data import BatchDeploymentPlan        # pydantic
    from llm_batch_deploy.deployer import deploy                 # boto3
    from llm_batch_deploy.runtime import process_shard           # httpx + aioboto3

The convenience re-exports at this level (BatchDeploymentPlan,
ModelSpec, etc.) are lazy: they only trigger on first attribute access.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

if TYPE_CHECKING:
    from .data import (
        BatchDeploymentPlan,
        CapacityMode,
        ComputeEnvironmentConfig,
        JobSubmissionPlan,
        ModelSpec,
        QueueConfig,
        SubmittedShard,
    )

_LAZY_EXPORTS = {
    "BatchDeploymentPlan": "llm_batch_deploy.data",
    "CapacityMode": "llm_batch_deploy.data",
    "ComputeEnvironmentConfig": "llm_batch_deploy.data",
    "JobSubmissionPlan": "llm_batch_deploy.data",
    "ModelSpec": "llm_batch_deploy.data",
    "QueueConfig": "llm_batch_deploy.data",
    "SubmittedShard": "llm_batch_deploy.data",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module = importlib.import_module(_LAZY_EXPORTS[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'llm_batch_deploy' has no attribute {name!r}")


__all__ = [
    "BatchDeploymentPlan",
    "CapacityMode",
    "ComputeEnvironmentConfig",
    "JobSubmissionPlan",
    "ModelSpec",
    "QueueConfig",
    "SubmittedShard",
    "__version__",
]
