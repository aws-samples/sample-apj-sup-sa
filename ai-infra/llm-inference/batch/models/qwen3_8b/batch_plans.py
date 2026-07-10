"""Predefined BatchDeploymentPlan variants for Qwen3-8B.

Qwen3-8B is small enough to fit on a single 24-GiB GPU (g5/g6) with comfortable
KV-cache budget at 32K context, or on a single L40S (g6e, 48 GiB) with even
more headroom. We default to g6e because it has both a generous VRAM pool and
broad spot availability.
"""
from llm_batch_deploy import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    QueueConfig,
)

from .model_spec import QWEN3_8B


def g6e_spot_single_queue() -> BatchDeploymentPlan:
    """g6e (L40S) spot, single queue. The recommended default for Qwen3-8B."""
    return BatchDeploymentPlan(
        model_spec=QWEN3_8B,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="g6e-spot",
                instance_types=["g6e.xlarge", "g6e.2xlarge", "g6e.4xlarge"],
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
                compute_environment_suffixes=["g6e-spot"],
            ),
        ],
        tensor_parallel=1,
        data_parallel=1,
        pipeline_parallel=1,
        in_flight_per_job=128,
        max_model_len=32768,
    )


def g7e_spot_single_queue() -> BatchDeploymentPlan:
    """g7e (Blackwell RTX PRO 6000, 96 GiB) spot. Overkill but cheap on spot."""
    return BatchDeploymentPlan(
        model_spec=QWEN3_8B,
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
        in_flight_per_job=200,
        max_model_len=32768,
    )
