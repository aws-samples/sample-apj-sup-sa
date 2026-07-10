"""Resolve Batch jobs to the EC2 instances that actually ran them.

Chain:
    Batch DescribeJobs → ``container.containerInstanceArn``
    ECS DescribeContainerInstances → ``ec2InstanceId``
    EC2 DescribeInstances → InstanceType, AvailabilityZone, LaunchTime,
                            lifecycle (spot vs on-demand),
                            StateReason / StateTransitionReason (when
                            instance terminated)

Used by :mod:`llm_batch_deploy.waiter.cost` to attribute cost to actual
EC2 instance-hours rather than job container-run-hours — which matters
because AWS bills you for instance provisioning + drain time that Batch
jobs do not include in their started_at/stopped_at window.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import boto3

from .poll import JobStatus, StatusSnapshot

LOG = logging.getLogger(__name__)


@dataclass
class InstanceRecord:
    """Metadata about one EC2 instance used by a submission."""

    instance_id: str
    instance_type: str
    availability_zone: str
    lifecycle: str
    """``"spot"`` or ``"on-demand"``."""

    launch_time_ms: int
    """Epoch milliseconds. From EC2 ``LaunchTime``."""

    termination_time_ms: int | None
    """Epoch milliseconds, or None if the instance is still running.

    Best-effort: parsed from ``StateTransitionReason`` when the state is
    terminated/shutting-down; else ``None``. For the cost integral we
    fall back to max(stoppedAt across jobs on this instance) if the
    instance no longer exists in EC2's DescribeInstances view.
    """

    container_instance_arns: set[str] = field(default_factory=set)
    job_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "instance_type": self.instance_type,
            "availability_zone": self.availability_zone,
            "lifecycle": self.lifecycle,
            "launch_time_ms": self.launch_time_ms,
            "termination_time_ms": self.termination_time_ms,
            "container_instance_arns": sorted(self.container_instance_arns),
            "job_ids": list(self.job_ids),
        }


# StateTransitionReason format examples:
#   "User initiated (2026-05-05 10:43:21 GMT)"
#   "Client.InstanceInitiatedShutdown: Instance initiated shutdown"
#   "Server.SpotInstanceTermination: Marked for termination (2026-05-05 11:02:17 GMT)"
_STATE_TRANSITION_RE = re.compile(r"\((\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\s*GMT)\)")


def _parse_state_transition_time(reason: str | None) -> int | None:
    """Extract epoch-ms termination time from EC2 ``StateTransitionReason``.

    Returns None if no timestamp found or the reason is empty.
    """
    if not reason:
        return None
    m = _STATE_TRANSITION_RE.search(reason)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1).replace("  ", " "), "%Y-%m-%d %H:%M:%S GMT")
    except ValueError:
        return None
    dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def resolve_instances(
    status_snapshot: StatusSnapshot,
    *,
    ecs_cluster_arn: str | None = None,
    region: str = "us-west-2",
    ecs_client: Any | None = None,
    ec2_client: Any | None = None,
) -> list[InstanceRecord]:
    """Resolve all unique EC2 instances touched by the submission.

    Parameters
    ----------
    status_snapshot
        Final :class:`StatusSnapshot` (jobs must have
        ``container_instance_arn`` populated — see
        :func:`llm_batch_deploy.waiter.poll.poll`).
    ecs_cluster_arn
        The ECS cluster the Batch Compute Environment registers against.
        If None, inferred from the first container_instance_arn we see
        (the ARN format includes the cluster).
    region
        AWS region for ECS + EC2 clients.

    Returns
    -------
    list[InstanceRecord]
        One per unique EC2 instance across all jobs. Never empty if any
        job has a non-null ``container_instance_arn``; may be fewer
        records than jobs if Batch reused instances.

    Notes
    -----
    Best-effort: if ECS / EC2 APIs fail for an instance (terminated +
    garbage-collected, IAM denied, etc.) we skip it and log. The caller
    should fall back to a job-time-based estimate for unresolved jobs.
    """
    ecs = ecs_client or boto3.client("ecs", region_name=region)
    ec2 = ec2_client or boto3.client("ec2", region_name=region)

    # Step 1: collect unique container_instance_arns (and which jobs use them).
    jobs_by_arn: dict[str, list[JobStatus]] = {}
    for j in status_snapshot.jobs:
        if not j.container_instance_arn:
            continue
        jobs_by_arn.setdefault(j.container_instance_arn, []).append(j)

    if not jobs_by_arn:
        return []

    # Step 2: infer cluster ARN from the first container_instance_arn if
    # caller didn't pass one. ARN format:
    #   arn:aws:ecs:REGION:ACCT:container-instance/CLUSTER/UUID
    if ecs_cluster_arn is None:
        sample_arn = next(iter(jobs_by_arn))
        # Extract "CLUSTER" from ".../container-instance/CLUSTER/UUID"
        try:
            tail = sample_arn.split(":container-instance/", 1)[1]
            cluster_name = tail.split("/", 1)[0]
            # Reassemble full cluster ARN
            prefix = sample_arn.rsplit(":", 1)[0]  # "arn:aws:ecs:REGION:ACCT"
            ecs_cluster_arn = f"{prefix}:cluster/{cluster_name}"
        except (IndexError, ValueError):
            LOG.warning(
                "Could not infer cluster ARN from %s; pass ecs_cluster_arn explicitly",
                sample_arn,
            )
            return []

    # Step 3: ECS DescribeContainerInstances in chunks of 100 (API limit).
    arn_to_ec2_id: dict[str, str] = {}
    arn_list = list(jobs_by_arn)
    for i in range(0, len(arn_list), 100):
        chunk = arn_list[i : i + 100]
        try:
            resp = ecs.describe_container_instances(
                cluster=ecs_cluster_arn, containerInstances=chunk,
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning(
                "ECS describe_container_instances failed for cluster %s: %s",
                ecs_cluster_arn, exc,
            )
            continue
        for ci in resp.get("containerInstances", []):
            arn = ci.get("containerInstanceArn")
            ec2_id = ci.get("ec2InstanceId")
            if arn and ec2_id:
                arn_to_ec2_id[arn] = ec2_id

    if not arn_to_ec2_id:
        LOG.warning("No container instances resolved from ECS (cluster %s)", ecs_cluster_arn)
        return []

    # Step 4: EC2 DescribeInstances in chunks of 1000 (well under API limit).
    unique_ec2_ids = sorted(set(arn_to_ec2_id.values()))
    instance_meta: dict[str, dict[str, Any]] = {}
    try:
        paginator = ec2.get_paginator("describe_instances")
        for page in paginator.paginate(InstanceIds=unique_ec2_ids):
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    instance_meta[inst["InstanceId"]] = inst
    except Exception as exc:  # noqa: BLE001
        LOG.warning("EC2 describe_instances failed: %s", exc)

    # Step 5: assemble InstanceRecord per unique EC2 instance.
    records_by_ec2: dict[str, InstanceRecord] = {}
    for arn, ec2_id in arn_to_ec2_id.items():
        inst = instance_meta.get(ec2_id)
        if inst is None:
            # Instance garbage-collected by EC2 (usually ~1h after termination).
            # Log and skip — caller can infer from job timestamps instead.
            LOG.warning(
                "EC2 instance %s not found via DescribeInstances (may be GC'd)",
                ec2_id,
            )
            continue

        launch_dt = inst.get("LaunchTime")
        launch_ms = int(launch_dt.timestamp() * 1000) if launch_dt else 0

        # Termination time: parse from StateTransitionReason when state ≠ running.
        state = inst.get("State", {}).get("Name", "unknown")
        termination_ms: int | None = None
        if state != "running":
            termination_ms = _parse_state_transition_time(
                inst.get("StateTransitionReason")
            )

        lifecycle = inst.get("InstanceLifecycle") or "on-demand"  # spot or on-demand
        az = inst.get("Placement", {}).get("AvailabilityZone", "")
        instance_type = inst.get("InstanceType", "")

        record = records_by_ec2.get(ec2_id)
        if record is None:
            record = InstanceRecord(
                instance_id=ec2_id,
                instance_type=instance_type,
                availability_zone=az,
                lifecycle=lifecycle,
                launch_time_ms=launch_ms,
                termination_time_ms=termination_ms,
            )
            records_by_ec2[ec2_id] = record
        record.container_instance_arns.add(arn)
        for j in jobs_by_arn.get(arn, []):
            record.job_ids.append(j.job_id)

    return list(records_by_ec2.values())


def estimate_termination_from_jobs(
    record: InstanceRecord, status_snapshot: StatusSnapshot,
) -> int | None:
    """Fallback: estimate when an instance was terminated from its jobs'
    ``stoppedAt`` timestamps when EC2 doesn't know (or didn't report).

    Returns the latest stoppedAt across jobs that ran on this instance,
    or None if no such job has a stoppedAt yet.
    """
    job_ids = set(record.job_ids)
    stops = [
        j.stopped_at for j in status_snapshot.jobs
        if j.job_id in job_ids and j.stopped_at is not None
    ]
    return max(stops) if stops else None


__all__ = [
    "InstanceRecord",
    "resolve_instances",
    "estimate_termination_from_jobs",
]
