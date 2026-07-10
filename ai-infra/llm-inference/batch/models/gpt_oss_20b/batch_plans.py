"""Predefined BatchDeploymentPlan variants for openai/gpt-oss-20b.

Two pools:

* **g7e.2xlarge** (1x Blackwell RTX PRO 6000, 96 GiB) — preferred. MXFP4
  native, ~13 GiB resident weights, comfortable KV-cache headroom at full
  131K context. Requires ``VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8=1``.
* **p4d.24xlarge** (8xA100-40G, BF16) — fallback when g7e spot is tight.
  MXFP4 is not native on Ampere, so the model decompresses to BF16 (~42 GiB).
  TP=8 across the 8xA100-40G keeps per-rank residency to ~6 GiB and leaves
  the rest for KV. Requires the Triton attention backend
  (``VLLM_ATTENTION_BACKEND=TRITON_ATTN_VLLM_V1``) because FlashInfer's
  attention path doesn't support attention sinks on non-Blackwell.

Common ``extra_serve_flags`` cover the OpenAI tool-call/reasoning parsers
and the fp8 KV cache shape expected by the ``openai_gptoss`` parser.
"""
from llm_batch_deploy import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    QueueConfig,
)

from .model_spec import GPT_OSS_20B


_GPT_OSS_SERVE_FLAGS = (
    "--tool-call-parser openai "
    "--enable-auto-tool-choice "
    "--reasoning-parser openai_gptoss "
    "--kv-cache-dtype fp8"
)


def g7e_spot_single_queue() -> BatchDeploymentPlan:
    """g7e.2xlarge spot (Blackwell native MXFP4 via Marlin). Recommended default."""
    return BatchDeploymentPlan(
        model_spec=GPT_OSS_20B,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="g7e-spot",
                instance_types=["g7e.2xlarge", "g7e.4xlarge"],
                capacity_mode="spot",
                min_vcpus=0,
                max_vcpus=64,
                desired_vcpus=0,
            ),
        ],
        queues=[
            QueueConfig(
                name_suffix="primary",
                priority=1,
                compute_environment_suffixes=["g7e-spot"],
            ),
        ],
        tensor_parallel=1,
        data_parallel=1,
        pipeline_parallel=1,
        in_flight_per_job=128,
        max_model_len=131072,
        extra_serve_flags=_GPT_OSS_SERVE_FLAGS,
        # vLLM v0.20.2 `select_gpt_oss_mxfp4_moe_backend` rejects the FlashInfer
        # TRTLLM MXFP4 kernel on g7e RTX PRO 6000 (SM_120) — that kernel only
        # supports data-center Blackwell (SM_100). Leave moe_backend on auto;
        # vLLM picks MARLIN_MXFP4 which supports SM_120 + attention sinks.
        # Original VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8=1 caused engine init
        # to ValueError before the model could load.
    )


def p4d_spot_single_queue() -> BatchDeploymentPlan:
    """p4d.24xlarge spot, TP=8 BF16 (fallback when Blackwell is unavailable).

    On A100 the MXFP4 weights decompress to BF16 (~42 GiB); TP=8 across the
    8x A100-40G keeps per-rank residency manageable. The Triton attention
    backend is required because FlashInfer's attention kernel does not
    support attention sinks on non-Blackwell hardware. ``--async-scheduling``
    is added to keep prefill and decode overlapped on the slower interconnect.
    """
    return BatchDeploymentPlan(
        model_spec=GPT_OSS_20B,
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
        max_model_len=65536,  # cap context to leave KV room on A100-40G
        extra_serve_flags=_GPT_OSS_SERVE_FLAGS + " --async-scheduling",
        # Triton attention backend for attention-sink + sliding-window on
        # Ampere. FlashInfer does not support attention sinks on non-Blackwell.
        extra_env_vars={"VLLM_ATTENTION_BACKEND": "TRITON_ATTN_VLLM_V1"},
    )
