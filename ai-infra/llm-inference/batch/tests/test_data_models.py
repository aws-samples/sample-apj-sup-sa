"""Validator tests for the Pydantic data models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm_batch_deploy import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    JobSubmissionPlan,
    ModelSpec,
    QueueConfig,
    SubmittedShard,
)


# ---------------------------------------------------------------------------
# ModelSpec
# ---------------------------------------------------------------------------
def _ms() -> ModelSpec:
    return ModelSpec(
        resource_prefix="medgemma-27b",
        hf_model_id="google/medgemma-27b-text-it",
        served_model_name="medgemma-27b",
        weight_size_gib=55.0,
        gated=True,
    )


class TestModelSpec:
    def test_happy(self) -> None:
        ms = _ms()
        assert ms.stack_name == "medgemma-27b-batch"
        assert ms.job_definition_name == "medgemma-27b-batch-jobdef"
        assert ms.container_name == "medgemma-27b-vllm"
        assert ms.tag_value == "medgemma-27b-batch"

    def test_bad_prefix(self) -> None:
        for bad in ["MedGemma_27B", "x", "Medgemma", "foo_bar", "-foo", "foo-"]:
            with pytest.raises(ValidationError, match="resource_prefix"):
                ModelSpec(
                    resource_prefix=bad,
                    hf_model_id="a/b",
                    served_model_name="x",
                    weight_size_gib=1.0,
                )

    def test_bad_hf_id(self) -> None:
        with pytest.raises(ValidationError, match="hf_model_id"):
            ModelSpec(
                resource_prefix="foo",
                hf_model_id="no-slash",
                served_model_name="x",
                weight_size_gib=1.0,
            )

    def test_frozen(self) -> None:
        ms = _ms()
        with pytest.raises(ValidationError):
            ms.weight_size_gib = 60.0  # type: ignore[misc]

    def test_default_vllm_image_supports_gemma4(self) -> None:
        # Gemma 4 (released Apr 2026) requires vLLM >= 0.11.0; v0.10.2 fails
        # at startup with `Value error, ... model type 'gemma4' but
        # Transformers does not recognize this architecture.` Pin the floor.
        ms = _ms()
        tag = ms.vllm_image.split(":")[1]
        assert tag.startswith("v0."), f"unexpected tag shape: {tag}"
        major_minor = tag.lstrip("v").split(".")
        version_tuple = (int(major_minor[0]), int(major_minor[1]))
        assert version_tuple >= (0, 11), (
            f"vllm image {ms.vllm_image} predates gemma4 support; need >=v0.11.0"
        )


# ---------------------------------------------------------------------------
# ComputeEnvironmentConfig
# ---------------------------------------------------------------------------
class TestComputeEnvironmentConfig:
    def test_happy(self) -> None:
        ce = ComputeEnvironmentConfig(name_suffix="gpu-spot", instance_types=["p4d.24xlarge"], capacity_mode="spot",)
        assert ce.max_vcpus == 96
        assert ce.min_vcpus == 0

    def test_odcr_requires_cr_id(self) -> None:
        with pytest.raises(ValidationError, match="capacity_reservation_id"):
            ComputeEnvironmentConfig(name_suffix="gpu-odcr", instance_types=["p4d.24xlarge"], capacity_mode="odcr",)

    def test_capacity_block_requires_cr_id(self) -> None:
        with pytest.raises(ValidationError, match="capacity_reservation_id"):
            ComputeEnvironmentConfig(name_suffix="gpu-cb", instance_types=["p5.48xlarge"], capacity_mode="capacity-block",)

    def test_odcr_with_cr_id(self) -> None:
        ce = ComputeEnvironmentConfig(name_suffix="gpu-odcr", instance_types=["p4d.24xlarge"], capacity_mode="odcr",
        capacity_reservation_id="cr-0123456789abcdef0",)
        assert ce.capacity_reservation_id.startswith("cr-")

    def test_min_gt_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="min_vcpus"):
            ComputeEnvironmentConfig(name_suffix="x", instance_types=["p4d.24xlarge"], min_vcpus=100,
            max_vcpus=50,)

    def test_bad_suffix(self) -> None:
        with pytest.raises(ValidationError, match="name_suffix"):
            ComputeEnvironmentConfig(name_suffix="-bad", instance_types=["p4d.24xlarge"])


# ---------------------------------------------------------------------------
# QueueConfig
# ---------------------------------------------------------------------------
class TestQueueConfig:
    def test_empty_ces_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueueConfig(
                name_suffix="primary",
                compute_environment_suffixes=[],
            )


# ---------------------------------------------------------------------------
# BatchDeploymentPlan
# ---------------------------------------------------------------------------
def _ce(suffix: str = "p4d-spot", **kw) -> ComputeEnvironmentConfig:
    defaults = dict(name_suffix=suffix, instance_types=["p4d.24xlarge"], capacity_mode="spot")
    defaults.update(kw)
    return ComputeEnvironmentConfig(**defaults)


def _queue(suffix: str = "primary", ces=None) -> QueueConfig:
    return QueueConfig(
        name_suffix=suffix,
        priority=1,
        compute_environment_suffixes=ces or ["p4d-spot"],
    )


class TestBatchDeploymentPlan:
    def test_happy_single_queue(self) -> None:
        plan = BatchDeploymentPlan(
            model_spec=_ms(),
            compute_environments=[_ce()],
            queues=[_queue()],
            tensor_parallel=2,
            data_parallel=4,
        )
        assert plan.effective_max_model_len == 16384
        assert plan.region == "us-west-2"
        # enable_prefix_caching defaults to True
        assert plan.enable_prefix_caching is True

    def test_enable_prefix_caching_can_be_disabled(self) -> None:
        plan = BatchDeploymentPlan(
            model_spec=_ms(),
            compute_environments=[_ce()],
            queues=[_queue()],
            enable_prefix_caching=False,
        )
        assert plan.enable_prefix_caching is False

    def test_queue_references_unknown_ce(self) -> None:
        with pytest.raises(ValidationError, match="unknown compute"):
            BatchDeploymentPlan(
                model_spec=_ms(),
                compute_environments=[_ce("p4d-spot")],
                queues=[_queue("primary", ["nonexistent"])],
            )

    def test_duplicate_ce_suffix(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate compute"):
            BatchDeploymentPlan(
                model_spec=_ms(),
                compute_environments=[_ce("x"), _ce("x")],
                queues=[_queue(ces=["x"])],
            )

    def test_duplicate_queue_suffix(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate queue"):
            BatchDeploymentPlan(
                model_spec=_ms(),
                compute_environments=[_ce()],
                queues=[_queue("a"), _queue("a")],
            )

    def test_override_max_model_len(self) -> None:
        plan = BatchDeploymentPlan(
            model_spec=_ms(),
            compute_environments=[_ce()],
            queues=[_queue()],
            max_model_len=8192,
        )
        assert plan.effective_max_model_len == 8192

    def test_gpu_mem_util_bounds(self) -> None:
        with pytest.raises(ValidationError):
            BatchDeploymentPlan(
                model_spec=_ms(),
                compute_environments=[_ce()],
                queues=[_queue()],
                gpu_memory_utilization=0.99,
            )

    def test_in_flight_bounds(self) -> None:
        with pytest.raises(ValidationError):
            BatchDeploymentPlan(
                model_spec=_ms(),
                compute_environments=[_ce()],
                queues=[_queue()],
                in_flight_per_job=0,
            )

    def test_request_timeout_default_is_120(self) -> None:
        """Default matches drive_inference's framework default (httpx 120s)."""
        p = BatchDeploymentPlan(
            model_spec=_ms(),
            compute_environments=[_ce()],
            queues=[_queue()],
        )
        assert p.request_timeout_seconds == 120

    def test_request_timeout_lower_bound_rejected(self) -> None:
        """request_timeout_seconds<10 is rejected — values smaller than the
        ready-poll interval would imply a misconfigured plan."""
        with pytest.raises(ValidationError):
            BatchDeploymentPlan(
                model_spec=_ms(),
                compute_environments=[_ce()],
                queues=[_queue()],
                request_timeout_seconds=5,
            )


# ---------------------------------------------------------------------------
# JobSubmissionPlan + SubmittedShard
# ---------------------------------------------------------------------------
class TestJobSubmissionPlan:
    def test_happy(self) -> None:
        p = JobSubmissionPlan(
            queue_arn="arn:aws:batch:us-east-2:123:job-queue/q",
            job_definition_arn="arn:aws:batch:us-east-2:123:job-definition/d:1",
        )
        assert p.overwrite is False
        assert p.in_flight_per_job == 32
        assert p.max_uris_per_job == 200

    def test_max_uris_bounds(self) -> None:
        with pytest.raises(ValidationError):
            JobSubmissionPlan(
                queue_arn="arn:aws:batch:us-east-2:123:job-queue/q",
                job_definition_arn="arn:aws:batch:us-east-2:123:job-definition/d:1",
                max_uris_per_job=0,
            )


class TestSubmittedShard:
    def test_happy(self) -> None:
        s = SubmittedShard(
            shard_index=0,
            job_id="abc",
            job_name="batch-inference-shard-0-abc",
            queue_arn="arn:aws:batch:us-east-2:123:job-queue/q",
            manifest_s3_uri="s3://bucket/staging/m.jsonl",
            output_prefix_s3_uri="s3://bucket/out/shard-0",
            input_uri_count=100,
        )
        assert s.shard_index == 0

    def test_zero_input_uri_count_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SubmittedShard(
                shard_index=0,
                job_id="x", job_name="x", queue_arn="x",
                manifest_s3_uri="x", output_prefix_s3_uri="x",
                input_uri_count=0,
            )


# ---------------------------------------------------------------------------
# Medgemma factories wire up
# ---------------------------------------------------------------------------
class TestMedgemmaFactories:
    def test_p4d_spot_single_queue(self) -> None:
        from models.medgemma_27b import p4d_spot_single_queue
        plan = p4d_spot_single_queue()
        assert plan.model_spec.resource_prefix == "medgemma-27b"
        assert len(plan.queues) == 1
        assert len(plan.compute_environments) == 1
        assert plan.compute_environments[0].capacity_mode == "spot"

    def test_p4d_failover(self) -> None:
        from models.medgemma_27b import p4d_spot_and_on_demand_failover
        plan = p4d_spot_and_on_demand_failover()
        assert len(plan.compute_environments) == 2
        primary = plan.queues[0]
        # Spot listed first == higher CE priority in the queue.
        assert primary.compute_environment_suffixes == ["p4d-spot", "p4d-ondemand"]

    def test_g7e_spot_single_queue(self) -> None:
        from models.medgemma_27b import g7e_spot_single_queue
        plan = g7e_spot_single_queue()
        # Multi-pool list: 2xl + 4xl + 8xl (all 1-GPU fallbacks) + 12xl
        # (2-GPU, Batch packs 2 containers). All share Blackwell 96 GiB
        # single-GPU topology so one JobDef fits all.
        assert plan.compute_environments[0].instance_types == [
            "g7e.2xlarge", "g7e.4xlarge", "g7e.8xlarge", "g7e.12xlarge",
        ]
        assert plan.compute_environments[0].capacity_mode == "spot"
        # Single GPU → no sharding, no data-parallel
        assert plan.tensor_parallel == 1
        assert plan.data_parallel == 1
        assert plan.pipeline_parallel == 1
        # Default max_model_len should be overridden to 16384
        assert plan.effective_max_model_len == 16384
        # Default concurrency raised to 100 (fits g7e.2xlarge KV budget
        # for short-prompt workloads comfortably).
        assert plan.in_flight_per_job == 100
        # Prefix caching is on by default for batch workloads
        assert plan.enable_prefix_caching is True
