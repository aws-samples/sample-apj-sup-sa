"""Submission-time types — what the invoker passes to SubmitJob.

Kept separate from BatchDeploymentPlan so a single deployment can serve
many distinct submissions with different concurrency knobs.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class JobSubmissionPlan(BaseModel):
    """Per-submission knobs (one SubmissionReport is born from this)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    queue_arn: str
    """Which queue to submit to. Looked up from deployer outputs."""

    job_definition_arn: str
    """Job definition to use (from deployer outputs)."""

    job_name_prefix: str = Field("batch-inference", min_length=1, max_length=60)
    """Prefix for Batch job names; suffixed with a shard index + short uuid."""

    in_flight_per_job: int = Field(32, ge=1, le=1024)
    """Concurrency inside each container's asyncio driver."""

    max_uris_per_job: int = Field(200, ge=1, le=10000)
    """Max input URIs packed into one manifest (one manifest = one Batch job).

    Keeps SubmitJob env-var size under the ~30 KB ceiling AND bounds the
    blast radius of a job failure.
    """

    attempts: int = Field(2, ge=1, le=10)
    """Batch retry attempts on infrastructure failures."""

    tags: dict[str, str] = Field(default_factory=dict)
    """Tags merged into SubmitJob's tags. The stack's Project tag is added
    automatically."""

    overwrite: bool = False
    """If False, inputs whose predicted output already exists in S3 are
    skipped."""


class SubmittedShard(BaseModel):
    """One Batch job's worth of work. Produced by the invoker; consumed
    by the waiter + collector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    shard_index: int = Field(..., ge=0)
    job_id: str
    job_name: str
    queue_arn: str
    manifest_s3_uri: str
    output_prefix_s3_uri: str
    input_uri_count: int = Field(..., ge=1)
