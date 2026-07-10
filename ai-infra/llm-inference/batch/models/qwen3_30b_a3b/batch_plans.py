"""Predefined BatchDeploymentPlan variants for Qwen3-30B-A3B (MoE).

30B BF16 (~62 GiB) doesn't fit on a single 48-GiB L40S, so we either
TP=2 across two L40S (g6e.12xlarge has 4 → DP=2 with TP=2 each) or use
a single g7e.2xlarge (96 GiB Blackwell, comfortable). g7e is the
recommended path for Qwen3-30B-A3B because its sole-GPU footprint
keeps KV cache fast and avoids cross-GPU NCCL overhead on MoE.
"""
from llm_batch_deploy import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    QueueConfig,
)

from .model_spec import QWEN3_30B_A3B


def g7e_spot_single_queue() -> BatchDeploymentPlan:
    """g7e Blackwell spot, TP=1. Recommended default for Qwen3-30B-A3B."""
    return BatchDeploymentPlan(
        model_spec=QWEN3_30B_A3B,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="g7e-spot",
                instance_types=["g7e.2xlarge", "g7e.4xlarge", "g7e.8xlarge",
                                "g7e.12xlarge"],
                capacity_mode="spot",
                min_vcpus=0,
                max_vcpus=80,
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
        max_model_len=32768,
    )


def g6e_spot_single_queue() -> BatchDeploymentPlan:
    """g6e (L40S) spot, TP=2 + DP=2 on g6e.12xlarge. Fallback when g7e is tight."""
    return BatchDeploymentPlan(
        model_spec=QWEN3_30B_A3B,
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
        tensor_parallel=2,
        data_parallel=2,
        pipeline_parallel=1,
        in_flight_per_job=64,
        max_model_len=16384,
    )
