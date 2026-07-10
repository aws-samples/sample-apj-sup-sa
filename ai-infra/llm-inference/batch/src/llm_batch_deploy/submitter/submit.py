"""Invoker — normalize inputs, write manifests, SubmitJob to Batch.

High-level flow::

    report = submit_batch(
        input_sources=[...],   # mix of S3 URIs + local paths
        stack_outputs=stack,   # from deploy()
        plan=...,              # BatchDeploymentPlan (for model-level defaults)
        in_flight_per_job=32,
        max_uris_per_job=200,
        queue_suffix="primary",    # or None = primary_queue_arn
    )

One Batch job per shard (a shard is a manifest of at most
``max_uris_per_job`` S3 URIs). The invoker:

1. Normalizes local paths to S3 (uploads to the staging bucket).
2. Chunks the flat URI list into shards.
3. Optionally skips URIs whose output exists (idempotency).
4. Writes one manifest file per shard to S3.
5. SubmitJob for each shard, with per-shard containerOverrides.
6. Returns a :class:`SubmissionReport` containing a DataFrame row per shard.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import pandas as pd
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..data import BatchDeploymentPlan, SubmittedShard
from ..deployer.deploy import StackOutputs
from .idempotency import filter_done
from .s3_layout import (
    S3Layout,
    chunk_uris,
    make_submission_id,
    normalize_input_sources,
    write_manifest,
)

LOG = logging.getLogger(__name__)


@dataclass
class SubmissionReport:
    """Result of submit_batch(). Holds the DataFrame + originals."""

    submission_id: str
    layout: S3Layout
    shards: list[SubmittedShard]
    skipped_done: dict[int, int]
    """Map shard_index → count of URIs skipped (idempotent)."""

    failed_submit: list[dict[str, Any]]
    """Shards that failed SubmitJob; each entry has shard_index + error."""

    def to_dataframe(self) -> pd.DataFrame:
        """One row per submitted shard."""
        rows = [
            {
                "shard_index": s.shard_index,
                "job_id": s.job_id,
                "job_name": s.job_name,
                "queue_arn": s.queue_arn,
                "input_uri_count": s.input_uri_count,
                "manifest_s3_uri": s.manifest_s3_uri,
                "output_prefix_s3_uri": s.output_prefix_s3_uri,
            }
            for s in self.shards
        ]
        return pd.DataFrame(rows)

    def summary(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "submitted": len(self.shards),
            "total_inputs": sum(s.input_uri_count for s in self.shards),
            "skipped_idempotent": sum(self.skipped_done.values()),
            "failed_submit": len(self.failed_submit),
        }


def submit_batch(
    *,
    input_sources: list[str | Path],
    stack_outputs: StackOutputs,
    plan: BatchDeploymentPlan,
    in_flight_per_job: int = 32,
    max_uris_per_job: int = 200,
    queue_suffix: str | None = None,
    overwrite: bool = False,
    tags: dict[str, str] | None = None,
    job_name_prefix: str | None = None,
    attempts: int = 2,
    submission_id: str | None = None,
    s3_client: Any | None = None,
    batch_client: Any | None = None,
) -> SubmissionReport:
    """Submit a batch of inputs to the deployed stack.

    Parameters
    ----------
    input_sources
        List of S3 URIs (``s3://bucket/key``), local files (``.json`` or
        ``.jsonl``), or local directories. Local items are uploaded to the
        staging bucket under ``staging/<sid>/inputs/``.
    stack_outputs
        From ``deploy()``.
    plan
        The plan — used for model-level defaults (not redeployed).
    in_flight_per_job
        Concurrency inside each container.
    max_uris_per_job
        Sharding granularity.
    queue_suffix
        Which queue to use. Defaults to the first queue in stack_outputs.
    overwrite
        If False, skip URIs whose output already exists.
    tags
        Extra tags merged into each job's tag set.
    job_name_prefix
        Override the default ``<resource_prefix>-<submission_id>``.
    attempts
        Batch-level retry attempts on infrastructure failures.
    submission_id
        Override the auto-generated submission id. Pass the ``submission_id``
        from a previous :class:`SubmissionReport` to reuse its output prefix:
        idempotency's ``filter_done`` will then correctly skip inputs whose
        outputs were already produced by the earlier run. Useful for resuming
        partial submissions or resubmitting to a different queue after a
        capacity crunch. Must be URL-safe (alphanumeric + dashes).
    """
    region = stack_outputs.region
    s3 = s3_client or boto3.client("s3", region_name=region)
    batch = batch_client or boto3.client("batch", region_name=region)

    # 1. Pick queue
    if queue_suffix is None:
        queue_arn = stack_outputs.primary_queue_arn
    else:
        try:
            queue_arn = stack_outputs.queue_arns_by_suffix[queue_suffix]
        except KeyError as exc:
            raise ValueError(
                f"Queue {queue_suffix!r} not in stack outputs. "
                f"Available: {list(stack_outputs.queue_arns_by_suffix)}"
            ) from exc

    # 2. Layout
    if submission_id is None:
        submission_id = make_submission_id(plan.model_spec.resource_prefix)
    else:
        # Validate user-provided id: URL-safe, reasonable length.
        if not submission_id or not all(
            c.isalnum() or c in "-_" for c in submission_id
        ):
            raise ValueError(
                f"submission_id must be alphanumeric + '-/_'; got {submission_id!r}"
            )
        if len(submission_id) > 128:
            raise ValueError(
                f"submission_id too long ({len(submission_id)}); max 128 chars."
            )
    layout = S3Layout(bucket=stack_outputs.staging_bucket, submission_id=submission_id)
    LOG.info("Submission %s → bucket %s", submission_id, layout.bucket)

    # 3. Normalize input sources → flat S3 URI list
    uris = normalize_input_sources(s3, input_sources, layout=layout)
    if not uris:
        raise ValueError("No input URIs resolved.")
    LOG.info("Normalized %d input URIs", len(uris))

    # 4. Chunk into shards
    shards_raw = chunk_uris(uris, max_per_shard=max_uris_per_job)
    LOG.info(
        "Sharded into %d jobs of up to %d URIs each",
        len(shards_raw), max_uris_per_job,
    )

    # 5. Idempotency filter
    if overwrite:
        shards_filtered = shards_raw
        skipped_done: dict[int, int] = {}
    else:
        shards_filtered, skipped_done = filter_done(s3, layout, shards_raw)
        if skipped_done:
            LOG.info(
                "Skipped %d URIs across %d shards (idempotent)",
                sum(skipped_done.values()), len(skipped_done),
            )

    # 6. Write manifests + SubmitJob per shard
    name_prefix = job_name_prefix or f"{plan.model_spec.resource_prefix}-{submission_id}"
    base_tags = {"Project": plan.model_spec.tag_value, "SubmissionId": submission_id}
    if tags:
        base_tags.update(tags)

    submitted: list[SubmittedShard] = []
    failed_submit: list[dict[str, Any]] = []
    for idx, shard in enumerate(shards_filtered):
        if not shard:
            LOG.debug("Shard %d empty (all done), skipping.", idx)
            continue

        manifest_uri = _write_shard_manifest(s3, layout, idx, shard)
        out_prefix = layout.output_prefix_uri(idx)

        try:
            job_id, job_name = _submit_one(
                batch=batch,
                queue_arn=queue_arn,
                job_definition_arn=stack_outputs.job_definition_arn,
                shard_index=idx,
                manifest_uri=manifest_uri,
                output_prefix_uri=out_prefix,
                in_flight_per_job=in_flight_per_job,
                overwrite=overwrite,
                name_prefix=name_prefix,
                tags=base_tags,
                attempts=attempts,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.error("Shard %d: SubmitJob failed: %s", idx, exc)
            failed_submit.append({"shard_index": idx, "error": str(exc)})
            continue

        submitted.append(SubmittedShard(
            shard_index=idx,
            job_id=job_id,
            job_name=job_name,
            queue_arn=queue_arn,
            manifest_s3_uri=manifest_uri,
            output_prefix_s3_uri=out_prefix,
            input_uri_count=len(shard),
        ))

    LOG.info(
        "Submitted %d/%d shards (%d failed)",
        len(submitted), len(shards_filtered), len(failed_submit),
    )
    return SubmissionReport(
        submission_id=submission_id,
        layout=layout,
        shards=submitted,
        skipped_done=skipped_done,
        failed_submit=failed_submit,
    )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _write_shard_manifest(
    s3: Any, layout: S3Layout, shard_index: int, uris: list[str],
) -> str:
    key = f"{layout.manifest_prefix}shard-{shard_index:04d}.jsonl"
    return write_manifest(s3, layout.bucket, key, uris)


def _submit_one(
    *,
    batch: Any,
    queue_arn: str,
    job_definition_arn: str,
    shard_index: int,
    manifest_uri: str,
    output_prefix_uri: str,
    in_flight_per_job: int,
    overwrite: bool,
    name_prefix: str,
    tags: dict[str, str],
    attempts: int,
) -> tuple[str, str]:
    """SubmitJob with client-side retry on throttling."""
    job_name = f"{name_prefix}-shard-{shard_index:04d}"[-128:]  # Batch name max 128

    env_overrides = [
        {"name": "MANIFEST_S3_URI", "value": manifest_uri},
        {"name": "OUTPUT_PREFIX_S3_URI", "value": output_prefix_uri},
        {"name": "IN_FLIGHT_PER_JOB", "value": str(in_flight_per_job)},
        {"name": "OVERWRITE", "value": "true" if overwrite else "false"},
        {"name": "SUBMISSION_SHARD_INDEX", "value": str(shard_index)},
    ]
    # HF_TOKEN is NO LONGER passed via containerOverrides. It lives in
    # Secrets Manager and is injected by the ECS agent at task-start
    # time via the JobDefinition's Secrets block.

    # Client-side retry on Batch throttling
    for attempt in Retrying(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    ):
        with attempt:
            resp = batch.submit_job(
                jobName=job_name,
                jobQueue=queue_arn,
                jobDefinition=job_definition_arn,
                retryStrategy={"attempts": attempts},
                containerOverrides={"environment": env_overrides},
                tags=tags,
            )
    return resp["jobId"], job_name
