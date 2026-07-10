"""Poll AWS Batch DescribeJobs and report status transitions.

Usage::

    status = wait_for_completion(report, poll_every_s=30)
    # or streaming with a callback:
    for snapshot in stream_status(report, poll_every_s=30):
        print(snapshot.summary())
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import boto3
import pandas as pd

from ..submitter.submit import SubmissionReport

LOG = logging.getLogger(__name__)

# AWS Batch job status strings
_TERMINAL_SUCCESS = {"SUCCEEDED"}
_TERMINAL_FAIL = {"FAILED"}
_IN_PROGRESS = {"SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"}

_DESCRIBE_BATCH_SIZE = 100  # AWS Batch DescribeJobs batch limit


@dataclass
class JobStatus:
    """One job's current state."""

    shard_index: int
    job_id: str
    job_name: str
    status: str
    reason: str | None = None
    exit_code: int | None = None
    created_at: int | None = None
    """``createdAt`` from Batch — epoch ms when SubmitJob returned. This
    is the closest proxy to 'right before the first job was sent': Batch
    sets it server-side when it accepts the SubmitJob request."""
    started_at: int | None = None
    stopped_at: int | None = None
    container_instance_arn: str | None = None
    """ECS container instance ARN (from ``container.containerInstanceArn``).
    Needed to resolve the underlying EC2 instance for actual-cost
    attribution. May be None for jobs that never left SUBMITTED."""


@dataclass
class StatusSnapshot:
    """One poll cycle's worth of state across all jobs in a submission."""

    submission_id: str
    jobs: list[JobStatus] = field(default_factory=list)
    """One per shard in the report."""

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(j.status for j in self.jobs))

    @property
    def succeeded(self) -> int:
        return sum(1 for j in self.jobs if j.status in _TERMINAL_SUCCESS)

    @property
    def failed(self) -> int:
        return sum(1 for j in self.jobs if j.status in _TERMINAL_FAIL)

    @property
    def in_progress(self) -> int:
        return sum(1 for j in self.jobs if j.status in _IN_PROGRESS)

    @property
    def all_terminal(self) -> bool:
        return self.in_progress == 0

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "shard_index": j.shard_index,
                "job_id": j.job_id,
                "job_name": j.job_name,
                "status": j.status,
                "reason": j.reason,
                "exit_code": j.exit_code,
                "created_at": j.created_at,
                "started_at": j.started_at,
                "stopped_at": j.stopped_at,
                "container_instance_arn": j.container_instance_arn,
            }
            for j in sorted(self.jobs, key=lambda j: j.shard_index)
        ])

    def summary(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "counts": self.counts,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "in_progress": self.in_progress,
        }


def poll(
    report: SubmissionReport,
    *,
    batch_client: Any | None = None,
    region: str = "us-west-2",
) -> StatusSnapshot:
    """Single-shot: describe all jobs in the report and build a snapshot."""
    batch = batch_client or boto3.client("batch", region_name=region)

    # Index report shards by job_id for fast lookup
    by_job_id = {s.job_id: s for s in report.shards}
    job_ids = list(by_job_id)
    jobs: list[JobStatus] = []

    # DescribeJobs accepts up to 100 per call
    for i in range(0, len(job_ids), _DESCRIBE_BATCH_SIZE):
        chunk = job_ids[i : i + _DESCRIBE_BATCH_SIZE]
        resp = batch.describe_jobs(jobs=chunk)
        for j in resp.get("jobs", []):
            shard = by_job_id.get(j["jobId"])
            if shard is None:
                continue
            # Find the exit code from attempts[-1] if available
            exit_code = None
            if j.get("attempts"):
                last = j["attempts"][-1]
                exit_code = last.get("container", {}).get("exitCode")
            jobs.append(JobStatus(
                shard_index=shard.shard_index,
                job_id=j["jobId"],
                job_name=j.get("jobName", shard.job_name),
                status=j.get("status", "UNKNOWN"),
                reason=j.get("statusReason"),
                exit_code=exit_code,
                created_at=j.get("createdAt"),
                started_at=j.get("startedAt"),
                stopped_at=j.get("stoppedAt"),
                container_instance_arn=j.get("container", {}).get(
                    "containerInstanceArn"
                ),
            ))

    # Fill in any jobs we didn't get back (shouldn't happen; defensive)
    returned_ids = {j.job_id for j in jobs}
    for jid, shard in by_job_id.items():
        if jid not in returned_ids:
            jobs.append(JobStatus(
                shard_index=shard.shard_index,
                job_id=jid, job_name=shard.job_name,
                status="UNKNOWN",
                reason="not returned by DescribeJobs",
            ))

    return StatusSnapshot(submission_id=report.submission_id, jobs=jobs)


def stream_status(
    report: SubmissionReport,
    *,
    poll_every_s: int = 30,
    batch_client: Any | None = None,
    region: str = "us-west-2",
    max_wait_s: int | None = None,
) -> Iterator[StatusSnapshot]:
    """Yield a StatusSnapshot every ``poll_every_s`` seconds until all terminal.

    If ``max_wait_s`` is set, raises TimeoutError if reached.
    """
    start = time.monotonic()
    while True:
        snapshot = poll(report, batch_client=batch_client, region=region)
        yield snapshot
        if snapshot.all_terminal:
            return
        if max_wait_s is not None and time.monotonic() - start > max_wait_s:
            raise TimeoutError(
                f"stream_status: {max_wait_s}s elapsed, "
                f"still {snapshot.in_progress} in progress"
            )
        time.sleep(poll_every_s)


def wait_for_completion(
    report: SubmissionReport,
    *,
    poll_every_s: int = 30,
    batch_client: Any | None = None,
    region: str = "us-west-2",
    max_wait_s: int | None = None,
    on_snapshot: Callable[[StatusSnapshot], None] | None = None,
) -> StatusSnapshot:
    """Block until all jobs finish. Returns the final snapshot.

    ``on_snapshot`` is invoked after each poll — useful for logging /
    progress bars from the notebook.
    """
    final: StatusSnapshot | None = None
    for snapshot in stream_status(
        report,
        poll_every_s=poll_every_s,
        batch_client=batch_client,
        region=region,
        max_wait_s=max_wait_s,
    ):
        if on_snapshot is not None:
            on_snapshot(snapshot)
        else:
            LOG.info(
                "[%s] %s", snapshot.submission_id, snapshot.summary()["counts"],
            )
        final = snapshot
    assert final is not None  # by construction (at least one yield)
    return final
