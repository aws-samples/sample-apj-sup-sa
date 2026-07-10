"""Gemma 4 31B benchmark experiments.

Gemma 4 31B at BF16 is ~64 GiB. On 22 GiB GPUs (A10G, L4) requires TP=4. On
L40S (47 GiB) requires TP=2 (one replica per pair of GPUs). On Blackwell
(96 GiB) and A100-80GB it fits on a single GPU.
"""
from __future__ import annotations

from vllm_ec2_bench import (
    CapacityMode,
    DeploymentPlan,
    ExperimentConfig,
)

from .model_spec import GEMMA_4_31B


_STANDARD: list[CapacityMode] = ["spot", "on-demand", "odcr"]


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
        concurrency_high=20,
        notes="1 replica on 4x A10G; TP=4 to fit 64 GiB across 89 GiB aggregate.",
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
        concurrency_high=20,
        notes="1 replica on 4x L4; TP=4. Same shape on Ada Lovelace.",
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
        notes="2 replicas on 4x L40S (44.7 GiB each); TP=2 per replica, DP=2.",
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
        notes="1 replica on 1x Blackwell RTX PRO 6000 (96 GiB); fits with margin.",
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
        concurrency_high=40,
        notes="2 replicas on 2x Blackwell; one replica per GPU.",
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
        notes="4 replicas on 8x A100-40GB; weights (~64 GiB) need TP=2, then DP=4.",
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
        concurrency_high=80,
        notes="8 replicas on 8x A100-80GB; one replica per GPU.",
    ),
}


EXPERIMENTS: dict[str, ExperimentConfig] = {
    exp_id: ExperimentConfig(model_spec=GEMMA_4_31B, deployment=plan)
    for exp_id, plan in _PLANS.items()
}


def get(experiment_id: str) -> ExperimentConfig:
    try:
        return EXPERIMENTS[experiment_id]
    except KeyError as exc:
        raise KeyError(f"Unknown experiment id {experiment_id!r}.") from exc


def development_experiments() -> list[str]:
    return ["exp_3", "exp_4", "exp_5"]


__all__ = ["EXPERIMENTS", "get", "development_experiments"]
