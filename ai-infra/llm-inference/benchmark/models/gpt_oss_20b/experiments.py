"""gpt-oss-20b benchmark experiments.

The model needs different vLLM configurations on Blackwell (native MXFP4)
vs. Ampere (BF16 expansion). Both share the same OpenAI tool-call /
reasoning parser flags.
"""
from __future__ import annotations

from vllm_ec2_bench import (
    CapacityMode,
    DeploymentPlan,
    ExperimentConfig,
)

from .model_spec import GPT_OSS_20B


_STANDARD: list[CapacityMode] = ["spot", "on-demand", "odcr"]


# Common serve flags across every experiment: OpenAI tool-call parser,
# auto tool-choice, gpt-oss reasoning parser, and fp8 KV cache (the
# parser expects fp8 quantize for cache).
_GPT_OSS_BASE_FLAGS = (
    "--tool-call-parser openai "
    "--enable-auto-tool-choice "
    "--reasoning-parser openai_gptoss "
    "--kv-cache-dtype fp8"
)


_PLANS: dict[str, DeploymentPlan] = {
    "exp_1": DeploymentPlan(
        experiment_id="exp_1",
        instance_type="g7e.2xlarge",
        tensor_parallel=1,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=131072,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=80,
        extra_serve_flags=_GPT_OSS_BASE_FLAGS,
        # Blackwell RTX PRO 6000 (g7e, SM_120) keeps MXFP4 weights via vLLM's
        # auto-selected MoE backend (MARLIN_MXFP4). The FlashInfer TRTLLM
        # MXFP4 kernel that we tried initially only supports data-center
        # Blackwell (SM_100, B200) — on SM_120 the oracle rejects it with
        # "kernel does not support current device cuda" and fails engine
        # init outright. Leaving moe_backend on auto picks MARLIN, which
        # keeps ~13 GiB resident weights and supports attention sinks.
        notes="Blackwell native MXFP4 (Marlin) on 1x g7e.2xlarge. ~13 GiB resident, full 131K context.",
    ),
    "exp_2": DeploymentPlan(
        experiment_id="exp_2",
        instance_type="g7e.12xlarge",
        tensor_parallel=1,
        data_parallel=4,
        pipeline_parallel=1,
        max_model_len=65536,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=120,
        extra_serve_flags=_GPT_OSS_BASE_FLAGS,
        # Same SM_120 FlashInfer rejection as exp_1 — auto-pick MARLIN_MXFP4.
        notes="4 replicas on 4x Blackwell RTX PRO 6000 (96 GiB each); Marlin MXFP4, DP=4 TP=1.",
    ),
    "exp_3": DeploymentPlan(
        experiment_id="exp_3",
        instance_type="g6e.2xlarge",
        tensor_parallel=1,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=40,
        # On L40S (Ada Lovelace, no MXFP4 native), the MXFP4 weights
        # decompress to BF16 (~42 GiB) which still fits a 48 GiB L40S with
        # tighter context (32K). FlashInfer MoE kernel is a no-op here.
        extra_serve_flags=_GPT_OSS_BASE_FLAGS,
        notes="1x L40S. BF16 expansion (~42 GiB). 32K context cap. Slowest but cheapest.",
    ),
    "exp_4": DeploymentPlan(
        experiment_id="exp_4",
        instance_type="g6e.12xlarge",
        tensor_parallel=2,
        data_parallel=2,
        pipeline_parallel=1,
        max_model_len=32768,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=80,
        extra_serve_flags=_GPT_OSS_BASE_FLAGS,
        notes="4x L40S. 2 replicas at TP=2 (BF16, ~42 GiB) for 32K context.",
    ),
    "exp_5": DeploymentPlan(
        experiment_id="exp_5",
        instance_type="p4d.24xlarge",
        tensor_parallel=8,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=65536,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=160,
        # On A100 (Ampere) MXFP4 is not native; weights decompress to BF16
        # (~42 GiB). TP=8 splits to ~6 GiB per rank. FlashInfer attention
        # does not support attention sinks on non-Blackwell, so the Triton
        # backend is required. Async scheduling overlaps prefill+decode on
        # the slower NVLink fabric.
        extra_serve_flags=_GPT_OSS_BASE_FLAGS + " --async-scheduling",
        extra_env_vars={"VLLM_ATTENTION_BACKEND": "TRITON_ATTN_VLLM_V1"},
        notes="8x A100-40G. BF16 TP=8 with Triton attention backend (sinks unsupported on FlashInfer Ampere).",
    ),
    "exp_6": DeploymentPlan(
        experiment_id="exp_6",
        instance_type="p4de.24xlarge",
        tensor_parallel=8,
        data_parallel=1,
        pipeline_parallel=1,
        max_model_len=131072,
        region="us-west-2",
        capacity_preference=_STANDARD,
        concurrency_high=200,
        extra_serve_flags=_GPT_OSS_BASE_FLAGS + " --async-scheduling",
        extra_env_vars={"VLLM_ATTENTION_BACKEND": "TRITON_ATTN_VLLM_V1"},
        notes="8x A100-80G. BF16 TP=8 + Triton; full 131K context with the fatter VRAM.",
    ),
}


EXPERIMENTS: dict[str, ExperimentConfig] = {
    exp_id: ExperimentConfig(model_spec=GPT_OSS_20B, deployment=plan)
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
    return ["exp_1", "exp_3"]


__all__ = ["EXPERIMENTS", "get", "development_experiments"]
