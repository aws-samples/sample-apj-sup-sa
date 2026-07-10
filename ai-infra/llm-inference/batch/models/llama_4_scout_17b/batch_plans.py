"""Predefined BatchDeploymentPlan variants for Llama 4 Scout 17B-16E (109B MoE).

Llama-4-Scout BF16 weights are ~218 GiB. The smallest non-p5 instance that
fits this footprint is **p4d.24xlarge** (8xA100-40G = 320 GiB total). On
p4d we run TP=8 and rely on ``--kv-cache-dtype fp8`` to roughly double the
effective KV-cache budget (Llama 4 supports very long contexts).

For larger KV cache or higher concurrency, **p4de.24xlarge** (8xA100-80G =
640 GiB total) is the on-demand-friendlier alternative when spot is tight.

We do NOT define a g6e.48xlarge plan: 8xL40S only gives 384 GiB which leaves
no room for KV cache after weights. If you really need an L40S-class plan,
quantize first (FP8 or AWQ) — that's a separate plan to add.
"""
from llm_batch_deploy import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    QueueConfig,
)

from .model_spec import LLAMA_4_SCOUT_17B


# 218 GiB BF16 weights take 18-75 min to download from HuggingFace at
# typical 50-200 MiB/s. The default vllm_startup_timeout_seconds=900 (15 min)
# would have the driver give up before vLLM finishes loading. Bump to 5400s
# (90 min) to cover the worst-case HF throughput dip plus warmup.
_LLAMA_SCOUT_VLLM_STARTUP_TIMEOUT_S = 5400

# A100-40G/80G with TP=8 on a 109B-MoE generates 30-50 tok/s per stream.
# At in_flight_per_job=64-128, decode requests queue inside vLLM; observed
# wall-clock per request can cross 120s (the framework default) at long
# max_tokens (>=1024) or under contention. Bump to 600s (10 min) so a
# slow request retries on actual server-side error, not a client-side
# timeout that misclassifies a healthy-but-slow generation as a 5xx.
_LLAMA_SCOUT_REQUEST_TIMEOUT_S = 600


def p4d_spot_single_queue() -> BatchDeploymentPlan:
    """p4d.24xlarge (8xA100-40G) spot, TP=8 BF16 + fp8 KV-cache. Recommended."""
    return BatchDeploymentPlan(
        model_spec=LLAMA_4_SCOUT_17B,
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
        max_model_len=65536,
        # 8xA100-40G = 320 GiB total VRAM. After BF16 weights (~218 GiB)
        # there isn't enough room for a BF16 KV cache at 64K context;
        # fp8 KV halves the budget back into a workable range.
        extra_serve_flags="--kv-cache-dtype fp8",
        # ~218 GiB BF16 weights + image + AMI — bump above the 300 GiB default
        # so a future alternate-precision artifact (FP8/AWQ) can coexist on
        # disk without ENOSPC mid-pull.
        root_volume_gib=400,
        vllm_startup_timeout_seconds=_LLAMA_SCOUT_VLLM_STARTUP_TIMEOUT_S,
        request_timeout_seconds=_LLAMA_SCOUT_REQUEST_TIMEOUT_S,
    )


def p4de_spot_single_queue() -> BatchDeploymentPlan:
    """p4de.24xlarge (8xA100-80G) spot, TP=8 BF16. Roomier KV than p4d."""
    return BatchDeploymentPlan(
        model_spec=LLAMA_4_SCOUT_17B,
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
        tensor_parallel=8,
        data_parallel=1,
        pipeline_parallel=1,
        in_flight_per_job=128,
        max_model_len=131072,
        root_volume_gib=400,
        vllm_startup_timeout_seconds=_LLAMA_SCOUT_VLLM_STARTUP_TIMEOUT_S,
        request_timeout_seconds=_LLAMA_SCOUT_REQUEST_TIMEOUT_S,
    )
