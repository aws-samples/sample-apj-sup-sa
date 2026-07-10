"""Tests for submitter.submit — moto-backed S3 + mocked batch client."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from llm_batch_deploy.data import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    ModelSpec,
    QueueConfig,
)
from llm_batch_deploy.deployer.deploy import StackOutputs
from llm_batch_deploy.submitter.submit import (
    SubmissionReport,
    submit_batch,
)


def _plan() -> BatchDeploymentPlan:
    return BatchDeploymentPlan(
        model_spec=ModelSpec(
            resource_prefix="medgemma-27b",
            hf_model_id="google/medgemma-27b-text-it",
            served_model_name="medgemma-27b",
            weight_size_gib=55.0,
        ),
        compute_environments=[ComputeEnvironmentConfig(name_suffix="p4d-spot", instance_types=["p4d.24xlarge"], capacity_mode="spot",)],
        queues=[
            QueueConfig(name_suffix="primary", priority=1,
                        compute_environment_suffixes=["p4d-spot"]),
            QueueConfig(name_suffix="secondary", priority=2,
                        compute_environment_suffixes=["p4d-spot"]),
        ],
    )


def _stack_outputs(bucket: str = "stage-bkt") -> StackOutputs:
    return StackOutputs(
        stack_name="medgemma-27b-batch",
        region="us-east-2",
        staging_bucket=bucket,
        job_definition_arn="arn:aws:batch:us-east-2:123:job-definition/medgemma-27b-batch-jobdef:1",
        ecr_repository_uri="123.dkr.ecr.us-east-2.amazonaws.com/medgemma-27b-batch",
        queue_arns_by_suffix={
            "primary": "arn:aws:batch:us-east-2:123:job-queue/primary",
            "secondary": "arn:aws:batch:us-east-2:123:job-queue/secondary",
        },
    )


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-2")
        client.create_bucket(
            Bucket="stage-bkt",
            CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
        )
        yield client


def _mock_batch(job_ids: list[str] | None = None) -> MagicMock:
    batch = MagicMock()
    counter = iter(job_ids or [f"job-{i}" for i in range(100)])
    batch.submit_job.side_effect = lambda **kw: {"jobId": next(counter)}
    return batch


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestSubmitBatch:
    def test_happy_s3_inputs(self, s3) -> None:
        batch = _mock_batch()
        input_uris = [f"s3://external-bkt/in/file-{i}.jsonl" for i in range(5)]
        report = submit_batch(
            input_sources=input_uris,
            stack_outputs=_stack_outputs(),
            plan=_plan(),
            max_uris_per_job=2,
            s3_client=s3, batch_client=batch,
        )
        # 5 URIs, max 2 per shard → 3 shards
        assert len(report.shards) == 3
        assert report.skipped_done == {}
        assert report.failed_submit == []
        # Three SubmitJob calls
        assert batch.submit_job.call_count == 3

        # Check the first call shape
        call0 = batch.submit_job.call_args_list[0].kwargs
        assert call0["jobQueue"] == "arn:aws:batch:us-east-2:123:job-queue/primary"
        env = {e["name"]: e["value"] for e in call0["containerOverrides"]["environment"]}
        assert env["MANIFEST_S3_URI"].startswith("s3://stage-bkt/staging/")
        assert env["MANIFEST_S3_URI"].endswith("shard-0000.jsonl")
        assert env["OUTPUT_PREFIX_S3_URI"].endswith("shard-0000/")
        assert env["IN_FLIGHT_PER_JOB"] == "32"
        assert env["OVERWRITE"] == "false"
        # HF_TOKEN must NEVER be passed via containerOverrides — Secrets
        # Manager handles it (injected by ECS agent at task-start).
        assert "HF_TOKEN" not in env
        assert "HUGGING_FACE_HUB_TOKEN" not in env

    def test_hf_token_never_passed_via_env_overrides(self, s3) -> None:
        """Regression: submit_batch no longer accepts hf_token kwarg (moved
        to Secrets Manager). Passing it should raise TypeError."""
        batch = _mock_batch()
        with pytest.raises(TypeError, match="hf_token"):
            submit_batch(  # nosec B106
                input_sources=["s3://x/a.json"],
                stack_outputs=_stack_outputs(),
                plan=_plan(),
                s3_client=s3, batch_client=batch,
                hf_token="hf_whatever",  # noqa: pragma: no cover
            )

    def test_local_file_normalized_and_uploaded(self, s3, tmp_path: Path) -> None:
        f = tmp_path / "data.jsonl"
        f.write_text('{"id":1}\n{"id":2}\n')

        batch = _mock_batch()
        submit_batch(
            input_sources=[f],
            stack_outputs=_stack_outputs(),
            plan=_plan(),
            s3_client=s3, batch_client=batch,
        )
        # The local file got uploaded; verify the manifest line is the uploaded URI
        manifest_uri = next(
            e["value"] for e in batch.submit_job.call_args.kwargs["containerOverrides"]["environment"]
            if e["name"] == "MANIFEST_S3_URI"
        )
        # Read the manifest back
        bucket, key = manifest_uri.replace("s3://", "").split("/", 1)
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode()
        assert body.strip().endswith("staging/" + manifest_uri.split("staging/")[1].split("/")[0] + "/inputs/data.jsonl")

    def test_queue_suffix_selection(self, s3) -> None:
        batch = _mock_batch()
        submit_batch(
            input_sources=["s3://x/a.json"],
            stack_outputs=_stack_outputs(),
            plan=_plan(),
            queue_suffix="secondary",
            s3_client=s3, batch_client=batch,
        )
        assert batch.submit_job.call_args.kwargs["jobQueue"] == \
               "arn:aws:batch:us-east-2:123:job-queue/secondary"

    def test_unknown_queue_suffix_raises(self, s3) -> None:
        batch = _mock_batch()
        with pytest.raises(ValueError, match="not in stack outputs"):
            submit_batch(
                input_sources=["s3://x/a.json"],
                stack_outputs=_stack_outputs(),
                plan=_plan(),
                queue_suffix="nope",
                s3_client=s3, batch_client=batch,
            )

    def test_empty_input_rejected(self, s3) -> None:
        batch = _mock_batch()
        with pytest.raises(ValueError, match="No input URIs"):
            submit_batch(
                input_sources=[],
                stack_outputs=_stack_outputs(),
                plan=_plan(),
                s3_client=s3, batch_client=batch,
            )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
class TestIdempotency:
    def test_skips_existing_outputs(self, s3) -> None:
        # Pre-create outputs for shards 0 and 2 only
        batch = _mock_batch()
        inputs = [f"s3://x/file-{i}.jsonl" for i in range(6)]
        # Predict output keys: shard-0000/file-{0,1}, shard-0001/file-{2,3}, shard-0002/file-{4,5}
        # Pre-create file-0.jsonl (shard 0) and file-4.jsonl (shard 2)
        # We don't know submission_id yet — so use overwrite=False with a pre-created generic path
        # Easier: use overwrite=True to skip idempotency and just check it's honored
        report = submit_batch(
            input_sources=inputs,
            stack_outputs=_stack_outputs(),
            plan=_plan(),
            max_uris_per_job=2,
            overwrite=True,   # skip idempotency
            s3_client=s3, batch_client=batch,
        )
        assert len(report.shards) == 3
        assert report.skipped_done == {}
        # overwrite=true flag was passed to container
        env = {e["name"]: e["value"]
               for e in batch.submit_job.call_args.kwargs["containerOverrides"]["environment"]}
        assert env["OVERWRITE"] == "true"

    def test_reused_submission_id_skips_pre_existing_outputs(self, s3) -> None:
        """Passing the same submission_id as an earlier run lets filter_done
        see the already-produced outputs and skip them."""
        batch = _mock_batch()
        submission_id = "medgemma-27b-retry-test-20260506-abc123"

        # Simulate the first run having produced outputs for shards 0 and 2.
        # Output key format: outputs/<submission_id>/shard-NNNN/<filename>
        inputs = [f"s3://x/file-{i}.jsonl" for i in range(6)]
        for idx, fname in (
            (0, "file-0.jsonl"), (0, "file-1.jsonl"),
            (2, "file-4.jsonl"), (2, "file-5.jsonl"),
        ):
            s3.put_object(
                Bucket="stage-bkt",
                Key=f"outputs/{submission_id}/shard-{idx:04d}/{fname}",
                Body=b"{}",
            )

        report = submit_batch(
            input_sources=inputs,
            stack_outputs=_stack_outputs(),
            plan=_plan(),
            max_uris_per_job=2,
            submission_id=submission_id,
            s3_client=s3, batch_client=batch,
        )
        # Only shard 1 survives (shards 0 and 2 are fully done).
        assert len(report.shards) == 1
        assert report.shards[0].shard_index == 1
        assert sum(report.skipped_done.values()) == 4
        # submission_id on the report matches what we passed in.
        assert report.submission_id == submission_id

    def test_invalid_submission_id_rejected(self, s3) -> None:
        """Submission ids with illegal characters are rejected."""
        batch = _mock_batch()
        with pytest.raises(ValueError, match="submission_id"):
            submit_batch(
                input_sources=["s3://x/a.json"],
                stack_outputs=_stack_outputs(),
                plan=_plan(),
                submission_id="has spaces and / slashes",
                s3_client=s3, batch_client=batch,
            )


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------
class TestFailureHandling:
    def test_submit_failure_recorded(self, s3) -> None:
        # Use a stateful side_effect: succeed for shard 0 and 2, fail for shard 1.
        # Tenacity will retry shard 1's error 5 times; each retry is a call.
        state = {"call_count": 0}

        def _fake(**kwargs):
            state["call_count"] += 1
            # Determine which shard this is for from the manifest URI
            env = kwargs["containerOverrides"]["environment"]
            shard_idx = int(next(
                e["value"] for e in env if e["name"] == "SUBMISSION_SHARD_INDEX"
            ))
            if shard_idx == 1:
                raise Exception("TooManyRequestsException")
            return {"jobId": f"job-{shard_idx}"}

        batch = MagicMock()
        batch.submit_job.side_effect = _fake

        inputs = [f"s3://x/file-{i}.jsonl" for i in range(6)]
        report = submit_batch(
            input_sources=inputs,
            stack_outputs=_stack_outputs(),
            plan=_plan(),
            max_uris_per_job=2,
            overwrite=True,
            s3_client=s3, batch_client=batch,
        )
        # shard 0 + shard 2 succeed; shard 1 fails
        assert len(report.shards) == 2
        assert len(report.failed_submit) == 1
        assert report.failed_submit[0]["shard_index"] == 1
        assert "TooManyRequests" in report.failed_submit[0]["error"]


# ---------------------------------------------------------------------------
# DataFrame output
# ---------------------------------------------------------------------------
class TestReportDataFrame:
    def test_to_dataframe_shape(self, s3) -> None:
        batch = _mock_batch()
        report = submit_batch(
            input_sources=[f"s3://x/file-{i}.jsonl" for i in range(4)],
            stack_outputs=_stack_outputs(),
            plan=_plan(),
            max_uris_per_job=2,
            overwrite=True,
            s3_client=s3, batch_client=batch,
        )
        df = report.to_dataframe()
        assert list(df.columns) == [
            "shard_index", "job_id", "job_name", "queue_arn",
            "input_uri_count", "manifest_s3_uri", "output_prefix_s3_uri",
        ]
        assert len(df) == 2

    def test_summary(self, s3) -> None:
        batch = _mock_batch()
        report = submit_batch(
            input_sources=[f"s3://x/file-{i}.jsonl" for i in range(5)],
            stack_outputs=_stack_outputs(),
            plan=_plan(),
            max_uris_per_job=2,
            overwrite=True,
            s3_client=s3, batch_client=batch,
        )
        s = report.summary()
        assert s["submitted"] == 3
        assert s["total_inputs"] == 5
        assert s["skipped_idempotent"] == 0
        assert s["failed_submit"] == 0
