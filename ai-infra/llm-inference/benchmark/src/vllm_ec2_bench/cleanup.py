"""Emergency / bulk cleanup helpers.

These are project-wide (not per-experiment) — intended for the "oh no, my
kernel crashed and I don't know what's left running" case.
"""
from __future__ import annotations

import logging

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)

_PROJECT_TAG_KEY = "Project"


def terminate_all_tagged_instances(
    region: str,
    project_tag_value: str,
) -> list[str]:
    """Terminate every non-terminated instance tagged ``Project=project_tag_value``.

    Returns the list of instance IDs terminated (may be empty).
    """
    ec2 = boto3.client("ec2", region_name=region)
    resp = ec2.describe_instances(
        Filters=[
            {"Name": f"tag:{_PROJECT_TAG_KEY}", "Values": [project_tag_value]},
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            },
        ],
    )
    ids: list[str] = []
    for reservation in resp.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            ids.append(inst["InstanceId"])
    if ids:
        LOG.info("Emergency sweep terminating: %s", ids)
        ec2.terminate_instances(InstanceIds=ids)
    return ids


def cleanup_tagged_security_groups(
    region: str,
    project_tag_value: str,
) -> list[str]:
    """Delete every SG tagged ``Project=project_tag_value`` that isn't in use.

    Returns the list of SG IDs deleted. Skips (with a warning) any SG that
    still has a dependency (ENI attached, etc.).
    """
    ec2 = boto3.client("ec2", region_name=region)
    resp = ec2.describe_security_groups(
        Filters=[{"Name": f"tag:{_PROJECT_TAG_KEY}", "Values": [project_tag_value]}]
    )
    deleted: list[str] = []
    for sg in resp.get("SecurityGroups", []):
        sg_id = sg["GroupId"]
        try:
            ec2.delete_security_group(GroupId=sg_id)
            deleted.append(sg_id)
        except ClientError as exc:
            LOG.warning("Skipping SG %s (%s): %s", sg_id, sg.get("GroupName"), exc)
    return deleted


__all__ = [
    "cleanup_tagged_security_groups",
    "terminate_all_tagged_instances",
]
