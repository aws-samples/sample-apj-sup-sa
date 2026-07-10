"""Predefined BatchDeploymentPlan variants for Qwen/Qwen3-Coder-Next.

Two pools:

* **g6e.12xlarge** (4xL40S, 192 GiB total) — preferred. FP8 quantization
  drops weights to ~80 GiB resident; TP=4 spreads them across the 4 L40Ss
  with comfortable KV-cache headroom at 32K context.
* **p4de.24xlarge** (8xA100-80G, 640 GiB total) — fallback. BF16 fits
  with TP=2 leaving 4 ranks idle, OR TP=8 + DP=1 for max KV at 32K. We
  default to TP=2 because the qwen3_next hybrid architecture's
  Gated DeltaNet layers don't pipeline as well across many ranks.

The qwen3_next architecture requires vLLM >= 0.15.0; the repo pin (v0.20.2)
covers it.
"""
from llm_batch_deploy import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    QueueConfig,
)

from .model_spec import QWEN3_CODER_NEXT


_QWEN3_CODER_SERVE_FLAGS = (
    "--enable-auto-tool-choice "
    "--tool-call-parser qwen3_coder"
)

# 80B parameters: BF16 weights ~160 GiB take 8-30 min to download from HF.
# vLLM warmup on 4-8 GPUs adds another 4-8 min. Bump default 900s startup
# timeout to 3600s (60 min).
_QWEN3_CODER_VLLM_STARTUP_TIMEOUT_S = 3600


def g6e_spot_single_queue() -> BatchDeploymentPlan:
    """g6e.12xlarge spot (4xL40S, FP8 quant). Recommended default."""
    return BatchDeploymentPlan(
        model_spec=QWEN3_CODER_NEXT,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="g6e-spot",
                instance_types=["g6e.12xlarge"],
                capacity_mode="spot",
                min_vcpus=0,
                max_vcpus=96,
                desired_vcpus=0,
            ),
        ],
        queues=[
            QueueConfig(
                name_suffix="primary",
                priority=1,
                compute_environment_suffixes=["g6e-spot"],
            ),
        ],
        tensor_parallel=4,
        data_parallel=1,
        pipeline_parallel=1,
        in_flight_per_job=64,
        max_model_len=32768,
        # FP8 weight-quantization shrinks the 80B weights from ~160 GiB BF16
        # to ~80 GiB, which fits comfortably across 4xL40S (192 GiB total).
        extra_serve_flags=_QWEN3_CODER_SERVE_FLAGS + " --quantization fp8",
        vllm_startup_timeout_seconds=_QWEN3_CODER_VLLM_STARTUP_TIMEOUT_S,
        # FP8 weight quantize requires extra disk for the on-the-fly
        # quantized cache during cold start. Bump the EBS root from 300
        # to 400 GiB so the BF16-source-on-disk + FP8-quantized-cache +
        # image + AMI all fit.
        root_volume_gib=400,
    )


def p4d_spot_single_queue() -> BatchDeploymentPlan:
    """p4d.24xlarge spot (8xA100-40G, BF16 TP=8). Use when p4de capacity
    is tight.

    Per-rank weight share at TP=8 is ~20 GiB (160 GiB / 8) — fits 40 GiB
    A100 with KV-cache headroom at 16K context.
    """
    return BatchDeploymentPlan(
        model_spec=QWEN3_CODER_NEXT,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="p4d-spot",
                instance_types=["p4d.24xlarge"],
                capacity_mode="spot",
                min_vcpus=0,
                max_vcpus=96,
                desired_vcpus=0,
            ),
        ],
        queues=[
            QueueConfig(
                name_suffix="primary",
                priority=1,
                compute_environment_suffixes=["p4d-spot"],
            ),
        ],
        tensor_parallel=8,
        data_parallel=1,
        pipeline_parallel=1,
        in_flight_per_job=64,
        max_model_len=16384,  # cap context to leave KV room on 40 GiB A100
        extra_serve_flags=_QWEN3_CODER_SERVE_FLAGS,
        vllm_startup_timeout_seconds=_QWEN3_CODER_VLLM_STARTUP_TIMEOUT_S,
        root_volume_gib=400,
    )


def p4de_spot_single_queue() -> BatchDeploymentPlan:
    """p4de.24xlarge spot (8xA100-80G, BF16). Fallback when g6e is tight."""
    return BatchDeploymentPlan(
        model_spec=QWEN3_CODER_NEXT,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="p4de-spot",
                instance_types=["p4de.24xlarge"],
                capacity_mode="spot",
                min_vcpus=0,
                max_vcpus=96,
                desired_vcpus=0,
            ),
        ],
        queues=[
            QueueConfig(
                name_suffix="primary",
                priority=1,
                compute_environment_suffixes=["p4de-spot"],
            ),
        ],
        # TP=2 across 2 of the 8 GPUs; the Gated DeltaNet hybrid attn
        # doesn't shard as cleanly across high TP. The remaining 6 GPUs
        # are idle in this batch plan; for bulk throughput, deploy
        # TP=2/DP=4 in a future variant once that's verified.
        tensor_parallel=2,
        data_parallel=1,
        pipeline_parallel=1,
        in_flight_per_job=32,
        max_model_len=32768,
        extra_serve_flags=_QWEN3_CODER_SERVE_FLAGS,
        vllm_startup_timeout_seconds=_QWEN3_CODER_VLLM_STARTUP_TIMEOUT_S,
        # BF16 80B weights ~160 GiB — bump EBS root so download + image fit.
        root_volume_gib=400,
    )
