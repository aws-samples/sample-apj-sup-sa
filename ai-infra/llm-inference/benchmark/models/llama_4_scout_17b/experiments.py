"""Llama 4 Scout 17B-16E benchmark experiments.

Llama-4-Scout has ~218 GiB BF16 weights. It does NOT fit on smaller hosts in
the lineup. The two viable instance types are p4d.24xlarge (8x A100-40GB,
320 GiB) and p4de.24xlarge (8x A100-80GB, 640 GiB). On p4d we need
``--kv-cache-dtype fp8`` to get any usable context window.
"""
from __future__ import annotations

from vllm_ec2_bench import (
    CapacityMode,
    DeploymentPlan,
    ExperimentConfig,
)

from .model_spec import LLAMA_4_SCOUT_17B


_STANDARD: list[CapacityMode] = ["spot", "on-demand", "odcr"]


# 218 GiB BF16 weights take 18-75 min to download from HuggingFace at typical
# 50-200 MiB/s. The DeploymentPlan default vllm_ready_timeout_s=2400 (40 min)
# would expire before vLLM finished loading on slow throughput days. Bump to
# 5400s (90 min) to cover the worst case plus warmup.
_LLAMA_SCOUT_READY_TIMEOUT_S = 5400


_PLANS: dict[str, DeploymentPlan] = {
    "exp_6": DeploymentPlan(
        experiment_id="exp_6",
        instance_type="p4d.24xlarge",
        tensor_parallel=8,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=40,
        # 8xA100-40G = 320 GiB total VRAM. After ~218 GiB BF16 weights there
        # isn't enough room for a BF16 KV cache at 32K context; fp8 KV halves
        # the per-token KV bytes and brings it back into a workable range.
        extra_serve_flags="--kv-cache-dtype fp8",
        vllm_ready_timeout_s=_LLAMA_SCOUT_READY_TIMEOUT_S,
        notes="1 replica on 8x A100-40GB; TP=8 to fit 218 GiB. Requires --kv-cache-dtype fp8 to leave KV-cache budget at 32K.",
    ),
    "exp_7": DeploymentPlan(
        experiment_id="exp_7",
        instance_type="p4de.24xlarge",
        tensor_parallel=8,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=65536,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=60,
        vllm_ready_timeout_s=_LLAMA_SCOUT_READY_TIMEOUT_S,
        notes="1 replica on 8x A100-80GB; TP=8. 640 GiB aggregate VRAM allows BF16 KV cache at 64K.",
    ),
}


EXPERIMENTS: dict[str, ExperimentConfig] = {
    exp_id: ExperimentConfig(model_spec=LLAMA_4_SCOUT_17B, deployment=plan)
    for exp_id, plan in _PLANS.items()
}


def get(experiment_id: str) -> ExperimentConfig:
    try:
        return EXPERIMENTS[experiment_id]
    except KeyError as exc:
        raise KeyError(f"Unknown experiment id {experiment_id!r}.") from exc


def development_experiments() -> list[str]:
    return ["exp_6"]


__all__ = ["EXPERIMENTS", "get", "development_experiments"]
