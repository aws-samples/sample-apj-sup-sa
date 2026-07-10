"""Qwen3-Coder-Next benchmark experiments.

80B BF16 weights (~160 GiB) need either FP8 quantization on multi-GPU
L40S (g6e.12xlarge) or BF16 across A100-80G (p4de.24xlarge). The
qwen3_next hybrid (Gated DeltaNet + Gated Attention) doesn't shard
cleanly above TP=4, so high-TP plans are limited to TP=4 max.
"""
from __future__ import annotations

from vllm_ec2_bench import (
    CapacityMode,
    DeploymentPlan,
    ExperimentConfig,
)

from .model_spec import QWEN3_CODER_NEXT


_STANDARD: list[CapacityMode] = ["spot", "on-demand", "odcr"]


_QWEN3_CODER_BASE_FLAGS = (
    "--enable-auto-tool-choice "
    "--tool-call-parser qwen3_coder"
)

# 80B weights take 8-30 min to download; vLLM warmup adds 4-8 min on
# 4-8 GPUs. 60 min covers worst case.
_QWEN3_CODER_VLLM_READY_TIMEOUT_S = 3600


_PLANS: dict[str, DeploymentPlan] = {
    "exp_1": DeploymentPlan(
        experiment_id="exp_1",
        instance_type="g6e.12xlarge",
        tensor_parallel=4,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=80,
        # FP8 quant shrinks 80B weights from ~160 GiB BF16 to ~80 GiB,
        # which fits across 4xL40S (192 GiB total).
        extra_serve_flags=_QWEN3_CODER_BASE_FLAGS + " --quantization fp8",
        vllm_ready_timeout_s=_QWEN3_CODER_VLLM_READY_TIMEOUT_S,
        notes="4xL40S FP8 quant (~80 GiB), TP=4, 32K context. Recommended cheapest path.",
    ),
    "exp_2": DeploymentPlan(
        experiment_id="exp_2",
        instance_type="p4d.24xlarge",
        tensor_parallel=8,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=16384,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=64,
        # 8xA100-40G = 320 GiB total. BF16 (~160 GiB) fits with KV at 16K.
        extra_serve_flags=_QWEN3_CODER_BASE_FLAGS + " --kv-cache-dtype fp8",
        vllm_ready_timeout_s=_QWEN3_CODER_VLLM_READY_TIMEOUT_S,
        notes="8xA100-40G BF16 + fp8 KV at TP=8. 16K context to leave KV room.",
    ),
    "exp_3": DeploymentPlan(
        experiment_id="exp_3",
        instance_type="p4de.24xlarge",
        tensor_parallel=2,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=64,
        extra_serve_flags=_QWEN3_CODER_BASE_FLAGS,
        vllm_ready_timeout_s=_QWEN3_CODER_VLLM_READY_TIMEOUT_S,
        notes="2xA100-80G BF16 TP=2. Idle 6 GPUs but cleanest hybrid sharding.",
    ),
    "exp_4": DeploymentPlan(
        experiment_id="exp_4",
        instance_type="p4de.24xlarge",
        tensor_parallel=4,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=128,
        extra_serve_flags=_QWEN3_CODER_BASE_FLAGS,
        vllm_ready_timeout_s=_QWEN3_CODER_VLLM_READY_TIMEOUT_S,
        notes="4xA100-80G BF16 TP=4. Best throughput on Ampere.",
    ),
}


EXPERIMENTS: dict[str, ExperimentConfig] = {
    exp_id: ExperimentConfig(model_spec=QWEN3_CODER_NEXT, deployment=plan)
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
    return ["exp_1"]


__all__ = ["EXPERIMENTS", "get", "development_experiments"]
