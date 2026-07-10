"""Mistral-Small 3.2 24B benchmark experiments.

Mistral-Small at BF16 is ~55 GiB. It does not fit on a single 22 GiB or 40 GiB
GPU, so plans use TP>=2 on those. It fits on a single L40S (48 GiB, with care)
or comfortably on a single Blackwell (96 GiB), or on 2x A100-80GB.

NOTE: Mistral-Small-3.2-24B ships only the Mistral-native artefact (no
HF-format weights), so vLLM must be invoked with
``--tokenizer-mode mistral --config-format mistral --load-format mistral``
or it fails to load with a tokenizer/config-format error. The flags are
threaded through ``extra_serve_flags`` on every plan below.
"""
from __future__ import annotations

from vllm_ec2_bench import (
    CapacityMode,
    DeploymentPlan,
    ExperimentConfig,
)

from .model_spec import MISTRAL_SMALL_3_2_24B


_STANDARD: list[CapacityMode] = ["spot", "on-demand", "odcr"]

_MISTRAL_SERVE_FLAGS = (
    "--tokenizer-mode mistral --config-format mistral --load-format mistral"
)


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
        extra_serve_flags=_MISTRAL_SERVE_FLAGS,
        notes="1 replica on 4x A10G (22.4 GiB each); TP=4 shards weights across all GPUs.",
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
        extra_serve_flags=_MISTRAL_SERVE_FLAGS,
        notes="1 replica on 4x L4 (22.4 GiB each); TP=4. Same topology as g5 on Ada Lovelace.",
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
        concurrency_high=40,
        extra_serve_flags=_MISTRAL_SERVE_FLAGS,
        notes="2 replicas on 4x L40S (44.7 GiB each); each replica TP=2 over 2 GPUs, DP=2.",
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
        extra_serve_flags=_MISTRAL_SERVE_FLAGS,
        notes="1 replica on 1x Blackwell RTX PRO 6000 (96 GiB); TP=1. Fits comfortably.",
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
        extra_serve_flags=_MISTRAL_SERVE_FLAGS,
        notes="2 replicas on 2x Blackwell RTX PRO 6000 (96 GiB each); 1 replica per GPU.",
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
        concurrency_high=60,
        extra_serve_flags=_MISTRAL_SERVE_FLAGS,
        notes="4 replicas on 8x A100-40GB; weights (~55 GiB) need TP=2, then DP=4.",
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
        extra_serve_flags=_MISTRAL_SERVE_FLAGS,
        notes="8 replicas on 8x A100-80GB; one replica per GPU (TP=1, DP=8).",
    ),
}


EXPERIMENTS: dict[str, ExperimentConfig] = {
    exp_id: ExperimentConfig(model_spec=MISTRAL_SMALL_3_2_24B, deployment=plan)
    for exp_id, plan in _PLANS.items()
}


def get(experiment_id: str) -> ExperimentConfig:
    try:
        return EXPERIMENTS[experiment_id]
    except KeyError as exc:
        raise KeyError(
            f"Unknown experiment id {experiment_id!r}. Known: {sorted(EXPERIMENTS)}"
        ) from exc


def development_experiments() -> list[str]:
    return ["exp_1", "exp_2", "exp_3", "exp_4", "exp_5"]


__all__ = ["EXPERIMENTS", "get", "development_experiments"]
