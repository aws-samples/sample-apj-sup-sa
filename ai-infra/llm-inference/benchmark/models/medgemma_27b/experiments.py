"""The 11 MedGemma-27B benchmark experiments.

Each :class:`ExperimentConfig` represents the **optimum MedGemma-27B packing**
on one EC2 instance type — maximum replicas per instance-hour, using
tensor / data / (rarely) pipeline parallelism and NVIDIA MIG where it helps.

The deployer infrastructure is model-agnostic; this file is where the
MedGemma-specific TP/DP/MIG choices live. To add a new model, copy this
module and re-tune the plans for the new model's weight footprint.

Plans reference instances by string id (``instance_type="g5.12xlarge"``).
Hardware facts (vCPU / RAM / GPU count / VRAM) come from the Catalog at
launch time, not from this file — they're looked up by ``DeploymentRunner``
which does ``config.validate_against(catalog)`` automatically.
"""
from __future__ import annotations

from vllm_ec2_bench import (
    CapacityMode,
    DeploymentPlan,
    ExperimentConfig,
)

from .model_spec import MEDGEMMA_27B


# -----------------------------------------------------------------------------
# Capacity preference presets
# -----------------------------------------------------------------------------
_STANDARD: list[CapacityMode] = ["spot", "on-demand", "odcr"]
"""Commodity preference: try spot → OD → ODCR. All experiments."""


# -----------------------------------------------------------------------------
# Plans (instance_type × parallelism × capacity strategy)
# -----------------------------------------------------------------------------
_PLANS: dict[str, DeploymentPlan] = {
    "exp_1": DeploymentPlan(
        experiment_id="exp_1",
        instance_type="g5.12xlarge",
        tensor_parallel=4,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=8192,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=30,
        notes="1 replica on 4× A10G (22.4 GiB each); TP=4 shards weights across all GPUs. Oldest generation in the set.",
    ),
    "exp_2": DeploymentPlan(
        experiment_id="exp_2",
        instance_type="g6.12xlarge",
        tensor_parallel=4,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=8192,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=30,
        notes="1 replica on 4× L4 (22.4 GiB each); TP=4. Same topology as g5 but on Ada Lovelace.",
    ),
    "exp_3": DeploymentPlan(
        experiment_id="exp_3",
        instance_type="g6e.12xlarge",
        tensor_parallel=2,
        data_parallel=2,
        pipeline_parallel=1,
        max_model_len=16384,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=30,
        notes="2 replicas on 4× L40S (44.7 GiB each); each replica sharded TP=2 over 2 GPUs, DP=2 replicas in parallel.",
    ),
    "exp_4": DeploymentPlan(
        experiment_id="exp_4",
        instance_type="g7e.2xlarge",
        tensor_parallel=1,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=16384,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=20,
        notes="1 replica on 1× Blackwell RTX PRO 6000 (96 GiB); TP=1. Smallest and cheapest Blackwell SKU.",
    ),
    "exp_5": DeploymentPlan(
        experiment_id="exp_5",
        instance_type="g7e.12xlarge",
        tensor_parallel=1,
        data_parallel=2,
        pipeline_parallel=1,
        max_model_len=16384,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=30,
        notes="2 replicas on 2× Blackwell RTX PRO 6000 (96 GiB each); one replica per GPU, DP=2.",
    ),
    "exp_6": DeploymentPlan(
        experiment_id="exp_6",
        instance_type="p4d.24xlarge",
        tensor_parallel=2,
        data_parallel=4,
        pipeline_parallel=1,
        max_model_len=16384,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=50,
        notes="4 replicas on 8× A100-40GB; weights (~54 GiB) don't fit on one 40 GiB GPU so TP=2, then DP=4.",
    ),
    "exp_7": DeploymentPlan(
        experiment_id="exp_7",
        instance_type="p4de.24xlarge",
        tensor_parallel=1,
        data_parallel=8,
        pipeline_parallel=1,
        max_model_len=16384,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=50,
        notes="8 replicas on 8× A100-80GB; one replica per GPU (TP=1, DP=8). 80 GiB fits the model comfortably.",
    ),
}


# -----------------------------------------------------------------------------
# Public API: ExperimentConfigs
# -----------------------------------------------------------------------------
EXPERIMENTS: dict[str, ExperimentConfig] = {
    exp_id: ExperimentConfig(model_spec=MEDGEMMA_27B, deployment=plan)
    for exp_id, plan in _PLANS.items()
}


def get(experiment_id: str) -> ExperimentConfig:
    """Return the :class:`ExperimentConfig` for ``experiment_id`` or raise."""
    try:
        return EXPERIMENTS[experiment_id]
    except KeyError as exc:
        raise KeyError(
            f"Unknown experiment id {experiment_id!r}. Known: {sorted(EXPERIMENTS)}"
        ) from exc


def development_experiments() -> list[str]:
    """Experiments safe to run during iterative dev (cheap GPU only)."""
    return ["exp_1", "exp_2", "exp_3", "exp_4", "exp_5"]


__all__ = ["EXPERIMENTS", "get", "development_experiments"]
