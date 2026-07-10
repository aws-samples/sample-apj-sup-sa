"""Qwen3-8B benchmark experiments.

Each :class:`ExperimentConfig` represents the **optimum Qwen3-8B packing**
on one EC2 instance type — maximum replicas per instance-hour, using
tensor / data parallelism.

Qwen3-8B at BF16 is ~17 GiB. That fits a single 24 GiB A10G (g5.2xlarge),
a single L4 (g6.2xlarge), or any single L40S / Blackwell. This means we
can pack one replica per GPU on every multi-GPU host (DP=N, TP=1) and
saturate compute without any tensor-parallel overhead.
"""
from __future__ import annotations

from vllm_ec2_bench import (
    CapacityMode,
    DeploymentPlan,
    ExperimentConfig,
)

from .model_spec import QWEN3_8B


_STANDARD: list[CapacityMode] = ["spot", "on-demand", "odcr"]


_PLANS: dict[str, DeploymentPlan] = {
    "exp_1": DeploymentPlan(
        experiment_id="exp_1",
        instance_type="g5.12xlarge",
        tensor_parallel=1,
        data_parallel=4,
        pipeline_parallel=1,
        max_model_len=16384,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=80,
        notes="4 replicas on 4x A10G (22.4 GiB each); one replica per GPU (TP=1, DP=4). 8B at BF16 fits with ~5 GiB KV budget.",
    ),
    "exp_2": DeploymentPlan(
        experiment_id="exp_2",
        instance_type="g6.12xlarge",
        tensor_parallel=1,
        data_parallel=4,
        pipeline_parallel=1,
        max_model_len=16384,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=80,
        notes="4 replicas on 4x L4 (22.4 GiB each); one replica per GPU (TP=1, DP=4). Same shape as g5 but on Ada Lovelace.",
    ),
    "exp_3": DeploymentPlan(
        experiment_id="exp_3",
        instance_type="g6e.12xlarge",
        tensor_parallel=1,
        data_parallel=4,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=120,
        notes="4 replicas on 4x L40S (44.7 GiB each); one replica per GPU (TP=1, DP=4). Generous KV budget at full 32K context.",
    ),
    "exp_4": DeploymentPlan(
        experiment_id="exp_4",
        instance_type="g7e.2xlarge",
        tensor_parallel=1,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=80,
        notes="1 replica on 1x Blackwell RTX PRO 6000 (96 GiB); TP=1. Smallest single-GPU Blackwell SKU.",
    ),
    "exp_5": DeploymentPlan(
        experiment_id="exp_5",
        instance_type="g7e.12xlarge",
        tensor_parallel=1,
        data_parallel=2,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=120,
        notes="2 replicas on 2x Blackwell RTX PRO 6000 (96 GiB each); one replica per GPU (TP=1, DP=2).",
    ),
    "exp_6": DeploymentPlan(
        experiment_id="exp_6",
        instance_type="p4d.24xlarge",
        tensor_parallel=1,
        data_parallel=8,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=200,
        notes="8 replicas on 8x A100-40GB; one replica per GPU (TP=1, DP=8). 17 GiB weights leave ~22 GiB for KV cache per replica.",
    ),
    "exp_7": DeploymentPlan(
        experiment_id="exp_7",
        instance_type="p4de.24xlarge",
        tensor_parallel=1,
        data_parallel=8,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=200,
        notes="8 replicas on 8x A100-80GB; one replica per GPU (TP=1, DP=8). Even more KV headroom than p4d.",
    ),
}


EXPERIMENTS: dict[str, ExperimentConfig] = {
    exp_id: ExperimentConfig(model_spec=QWEN3_8B, deployment=plan)
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
