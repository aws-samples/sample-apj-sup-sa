"""Predefined BatchDeploymentPlan variants for Gemma 4 31B.

The 31B-dense model needs ~64 GiB BF16, which fits on a single 96-GiB g7e
Blackwell GPU. The vLLM recipe recommends TP=2 across 2xA100/H100 for the
"balanced" baseline; on AWS spot we hit that footprint via g6e.12xlarge
(4xL40S → TP=2 + DP=2).
"""
from llm_batch_deploy import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    QueueConfig,
)

from .model_spec import GEMMA_4_31B


def g7e_spot_single_queue() -> BatchDeploymentPlan:
    """g7e Blackwell spot (96 GiB), TP=1. Recommended."""
    return BatchDeploymentPlan(
        model_spec=GEMMA_4_31B,
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
        in_flight_per_job=80,
        max_model_len=32768,
    )


def g6e_spot_single_queue() -> BatchDeploymentPlan:
    """g6e (L40S) spot, TP=2 + DP=2 on g6e.12xlarge. Fallback."""
    return BatchDeploymentPlan(
        model_spec=GEMMA_4_31B,
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
        in_flight_per_job=48,
        max_model_len=16384,
    )
