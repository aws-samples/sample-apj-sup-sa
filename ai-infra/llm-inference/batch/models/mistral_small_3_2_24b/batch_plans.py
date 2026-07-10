"""Predefined BatchDeploymentPlan variants for Mistral-Small-3.2-24B.

24B BF16 weights (~55 GiB) need either a single 96-GiB GPU (g7e) or two L40S
(g6e.12xlarge with TP=2). g7e is generally cheaper per-token; g6e is the
fallback when g7e spot is tight.

NOTE: Mistral-Small-3.2-24B ships only the Mistral-native artefact (no
HF-format weights), so vLLM must be invoked with
``--tokenizer-mode mistral --config-format mistral --load-format mistral``
or it fails to load with a tokenizer/config-format error. The flags are
threaded through ``extra_serve_flags`` below.
"""
from llm_batch_deploy import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    QueueConfig,
)

from .model_spec import MISTRAL_SMALL_3_2_24B


_MISTRAL_SERVE_FLAGS = (
    "--tokenizer-mode mistral --config-format mistral --load-format mistral"
)


def g7e_spot_single_queue() -> BatchDeploymentPlan:
    """g7e Blackwell spot, TP=1. Cheapest path on g7e.2xlarge."""
    return BatchDeploymentPlan(
        model_spec=MISTRAL_SMALL_3_2_24B,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="g7e-spot",
                instance_types=["g7e.2xlarge", "g7e.4xlarge", "g7e.8xlarge"],
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
        in_flight_per_job=64,
        max_model_len=32768,
        extra_serve_flags=_MISTRAL_SERVE_FLAGS,
    )


def g6e_spot_single_queue() -> BatchDeploymentPlan:
    """g6e (L40S) spot, TP=2. Fallback when g7e is unavailable."""
    return BatchDeploymentPlan(
        model_spec=MISTRAL_SMALL_3_2_24B,
        region="us-west-2",
        compute_environments=[
            ComputeEnvironmentConfig(
                name_suffix="g6e-spot",
                instance_types=["g6e.12xlarge"],   # 4xL40S, TP=2 + DP=2
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
        max_model_len=32768,
        extra_serve_flags=_MISTRAL_SERVE_FLAGS,
    )
