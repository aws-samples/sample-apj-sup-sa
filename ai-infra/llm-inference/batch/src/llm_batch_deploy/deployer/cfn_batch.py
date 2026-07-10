"""Batch CFN primitives — Compute Environments, Queues, Job Definition.

Capacity modes supported:

* ``spot`` → ``Type: MANAGED``, ``ComputeResources.Type: SPOT``.
* ``on-demand`` → ``ComputeResources.Type: EC2``.
* ``odcr`` → ``EC2`` + ``CapacityReservationOptions`` pointing at a CR group.
* ``capacity-block`` → ``Type: MANAGED``, ``ComputeResources.Type: CAPACITY_BLOCK``.
  Requires ``CapacityReservationResourceGroup`` ARN OR individual ``CapacityReservationId``.
"""
from __future__ import annotations

from typing import Any

from ..data import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    QueueConfig,
)

_TAG_KEY = "Project"
_TAG_KEY_MODEL = "Model"


def _tags(plan: BatchDeploymentPlan) -> dict[str, str]:
    # Batch resources want a dict, not a list.
    # Tag policy: every Batch resource carries Project + Model tags so
    # cleanup automation can sweep all resources for a given model.
    return {
        _TAG_KEY: plan.model_spec.tag_value,
        _TAG_KEY_MODEL: plan.model_spec.resource_prefix,
    }


def _logical_id_for_ce(ce: ComputeEnvironmentConfig) -> str:
    """Derive CloudFormation logical id (alphanumeric only) from name_suffix."""
    cleaned = "".join(c for c in ce.name_suffix.title().replace("-", "") if c.isalnum())
    return f"ComputeEnv{cleaned}"


def _logical_id_for_queue(q: QueueConfig) -> str:
    cleaned = "".join(c for c in q.name_suffix.title().replace("-", "") if c.isalnum())
    return f"JobQueue{cleaned}"


# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# Launch Template — gives Batch EC2 instances a larger EBS root volume than
# the ECS-optimized AMI default (30 GB) so there's room for the vLLM
# container image (~24 GB uncompressed) + model weight cache (~55 GB for
# MedGemma-27B class models) + OS + containerd working set.
# -----------------------------------------------------------------------------
def launch_template(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    lid = "BatchLaunchTemplate"
    resource = {
        "Type": "AWS::EC2::LaunchTemplate",
        "Properties": {
            "LaunchTemplateName": f"{plan.model_spec.resource_prefix}-batch",
            "LaunchTemplateData": {
                "BlockDeviceMappings": [{
                    # /dev/xvda is the root on most ECS-optimized AMIs
                    # (Amazon Linux 2 + AL2023). EBS-backed.
                    # Sized via plan.root_volume_gib so larger models
                    # (e.g. Llama-4-Scout at ~218 GiB BF16) actually fit.
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": plan.root_volume_gib,
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                        "Encrypted": True,
                    },
                }],
                "TagSpecifications": [{
                    "ResourceType": "instance",
                    # Tag policy: every AWS resource carries Project + Model tags
                    # so cleanup automation can sweep by model. CFN propagates
                    # stack-level tags to most resources, but EC2 instances
                    # spawned by Batch via this launch template get only the
                    # tags declared here, not the stack-level set.
                    "Tags": [
                        {"Key": _TAG_KEY, "Value": plan.model_spec.tag_value},
                        {"Key": "Model", "Value": plan.model_spec.resource_prefix},
                    ],
                }],
            },
        },
    }
    return lid, resource


# -----------------------------------------------------------------------------
# Compute Environment
# -----------------------------------------------------------------------------
def compute_environment(
    plan: BatchDeploymentPlan, ce: ComputeEnvironmentConfig,
) -> tuple[str, dict[str, Any]]:
    """Build one CE resource.

    Returns ``(logical_id, resource_dict)``. The CE's ``DependsOn`` points at
    BatchServiceRole + EcsInstanceProfile so IAM propagation completes first.
    """
    lid = _logical_id_for_ce(ce)

    # Base shape — shared by all capacity modes.
    compute_resources: dict[str, Any] = {
        "InstanceTypes": list(ce.instance_types),
        "MinvCpus": ce.min_vcpus,
        "MaxvCpus": ce.max_vcpus,
        "DesiredvCpus": ce.desired_vcpus,
        "Subnets": {"Ref": "SubnetIds"},
        "SecurityGroupIds": [{"Ref": "BatchSecurityGroup"}],
        "InstanceRole": {"Fn::GetAtt": ["EcsInstanceProfile", "Arn"]},
        "LaunchTemplate": {
            "LaunchTemplateId": {"Ref": "BatchLaunchTemplate"},
            "Version": "$Latest",
        },
        "Tags": _tags(plan),
    }

    # Capacity-mode-specific shape.
    mode = ce.capacity_mode
    if mode == "spot":
        compute_resources["Type"] = "SPOT"
        # SPOT_PRICE_CAPACITY_OPTIMIZED is AWS's recommended default
        # (introduced 2022); balances spot price against capacity
        # depth to minimize interruptions at the lowest possible price.
        compute_resources["AllocationStrategy"] = "SPOT_PRICE_CAPACITY_OPTIMIZED"
        compute_resources["SpotIamFleetRole"] = {
            "Fn::GetAtt": ["SpotFleetRole", "Arn"],
        }
    elif mode == "on-demand":
        compute_resources["Type"] = "EC2"
        compute_resources["AllocationStrategy"] = "BEST_FIT_PROGRESSIVE"
    elif mode == "odcr":
        compute_resources["Type"] = "EC2"
        compute_resources["AllocationStrategy"] = "BEST_FIT_PROGRESSIVE"
        # Point at a single pre-existing CR
        compute_resources["CapacityReservationOptions"] = {
            "CapacityReservationPreference": "open",
            "CapacityReservationTarget": {
                "CapacityReservationId": ce.capacity_reservation_id,
            },
        }
    elif mode == "capacity-block":
        # CAPACITY_BLOCK is the 2024 managed CB path in Batch.
        # Requires the CR to be an ML Capacity Block (pre-purchased).
        compute_resources["Type"] = "CAPACITY_BLOCK"
        compute_resources["CapacityReservationOptions"] = {
            "CapacityReservationPreference": "capacity-reservations-only",
            "CapacityReservationTarget": {
                "CapacityReservationId": ce.capacity_reservation_id,
            },
        }
        # Spot/on-demand strategies don't apply; CB is the whole allocation.
    else:
        raise ValueError(f"Unknown capacity mode {mode!r} on CE {ce.name_suffix}")

    # Optional: per-CE subnets override the stack parameter
    if ce.subnet_ids:
        compute_resources["Subnets"] = ce.subnet_ids
    if ce.security_group_ids:
        compute_resources["SecurityGroupIds"] = ce.security_group_ids

    depends_on: list[str] = ["BatchServiceRole", "EcsInstanceProfile", "BatchLaunchTemplate"]
    if mode == "spot":
        depends_on.append("SpotFleetRole")

    resource: dict[str, Any] = {
        "Type": "AWS::Batch::ComputeEnvironment",
        "DependsOn": depends_on,
        "Properties": {
            "Type": "MANAGED",
            "State": "ENABLED",
            # NOTE: intentionally no explicit ComputeEnvironmentName —
            # that turns the CE into a "custom-named" resource which CFN
            # refuses to replace. Any future change that requires CE
            # replacement (e.g. adding a LaunchTemplate) would fail.
            # CFN generates a name like '<stack>-<logical-id>-<hash>',
            # which is fine because consumers look it up via stack outputs.
            "ServiceRole": {"Fn::GetAtt": ["BatchServiceRole", "Arn"]},
            "ComputeResources": compute_resources,
            "Tags": _tags(plan),
        },
    }
    return lid, resource


# -----------------------------------------------------------------------------
# Spot Fleet IAM role (only needed when any CE is SPOT)
# -----------------------------------------------------------------------------
def spot_fleet_role(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """IAM role for EC2 Spot Fleet — referenced by SPOT compute environments."""
    lid = "SpotFleetRole"
    resource = {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "RoleName": f"{plan.model_spec.resource_prefix}-batch-spot-fleet",
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "spotfleet.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            },
            "ManagedPolicyArns": [
                "arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole",
            ],
            "Tags": [
                {"Key": _TAG_KEY, "Value": plan.model_spec.tag_value},
                {"Key": "Model", "Value": plan.model_spec.resource_prefix},
            ],
        },
    }
    return lid, resource


# -----------------------------------------------------------------------------
# Job Queue
# -----------------------------------------------------------------------------
def job_queue(
    plan: BatchDeploymentPlan, q: QueueConfig,
) -> tuple[str, dict[str, Any]]:
    """Build one queue resource."""
    lid = _logical_id_for_queue(q)

    ce_order = [
        {
            "Order": i + 1,
            "ComputeEnvironment": {
                "Ref": _logical_id_for_ce(
                    next(c for c in plan.compute_environments if c.name_suffix == ce_suffix)
                ),
            },
        }
        for i, ce_suffix in enumerate(q.compute_environment_suffixes)
    ]

    resource = {
        "Type": "AWS::Batch::JobQueue",
        "Properties": {
            "JobQueueName": plan.queue_name(plan.model_spec.stack_name, q),
            "State": "ENABLED",
            "Priority": q.priority,
            "ComputeEnvironmentOrder": ce_order,
            "Tags": _tags(plan),
        },
        "DependsOn": [_logical_id_for_ce(
            next(c for c in plan.compute_environments if c.name_suffix == ce_suffix)
        ) for ce_suffix in q.compute_environment_suffixes],
    }
    return lid, resource


# -----------------------------------------------------------------------------
# Instance-type → container resource requirements
# -----------------------------------------------------------------------------
# AWS Batch requires ContainerProperties.ResourceRequirements to declare
# VCPU/MEMORY/GPU. The values must be achievable on the chosen instance
# type AFTER Batch/ECS takes its agent overhead. The table below captures
# per-instance sizing with conservative agent headroom so containers land
# cleanly without the scheduler refusing to place them.
#
# Memory values are in MiB (what Batch expects). We reserve:
#   * vCPU: max(1, 4% of instance) — rounded down
#   * Memory: ~5-10% of instance (5120 MiB minimum)
# These are heuristics. Raise headroom if you see
# "Resource requirements exceed instance capacity" or container-kill events.
#
# New instance types: add an entry here. Missing entries fall back to a
# conservative default (see _resource_requirements below).
INSTANCE_RESOURCES: dict[str, dict[str, int]] = {
    # GPU instances — MedGemma-era reference set
    "p4d.24xlarge":   {"vcpus": 92,  "memory_mib": 1_048_576, "gpus": 8},   # 96 vCPU, 1152 GiB
    "p4de.24xlarge":  {"vcpus": 92,  "memory_mib": 1_048_576, "gpus": 8},
    "p5.48xlarge":    {"vcpus": 188, "memory_mib": 2_048_000, "gpus": 8},   # 192 vCPU, 2048 GiB
    "p5e.48xlarge":   {"vcpus": 188, "memory_mib": 2_048_000, "gpus": 8},
    "g5.12xlarge":    {"vcpus": 46,  "memory_mib":   180_000, "gpus": 4},   # 48 vCPU, 192 GiB
    "g6.12xlarge":    {"vcpus": 46,  "memory_mib":   180_000, "gpus": 4},
    "g6e.12xlarge":   {"vcpus": 46,  "memory_mib":   360_000, "gpus": 4},   # 48 vCPU, 384 GiB
    "g7e.2xlarge":    {"vcpus": 7,   "memory_mib":    55_000, "gpus": 1},   #  8 vCPU,  64 GiB, 1 GPU
    "g7e.12xlarge":   {"vcpus": 46,  "memory_mib":   360_000, "gpus": 4},   # 48 vCPU, 384 GiB, 4 GPU
    # Neuron (not typically batch-inference targets but for completeness)
    "inf2.24xlarge":  {"vcpus": 90,  "memory_mib":   380_000, "gpus": 0},   # 96 vCPU, 384 GiB
    "trn1.32xlarge":  {"vcpus": 124, "memory_mib":   500_000, "gpus": 0},   # 128 vCPU, 512 GiB
}


def _resource_requirements(instance_type: str) -> list[dict[str, str]]:
    """Build ResourceRequirements list from the table or a safe fallback."""
    entry = INSTANCE_RESOURCES.get(instance_type)
    if entry is None:
        # Unknown instance type — fall back to a generous-but-plausible
        # single-GPU shape and warn via the JobDefinition description at
        # stack-deploy time. Callers can override by adding to the table.
        entry = {"vcpus": 4, "memory_mib": 16_000, "gpus": 1}
    return [
        {"Type": "VCPU",   "Value": str(entry["vcpus"])},
        {"Type": "MEMORY", "Value": str(entry["memory_mib"])},
        {"Type": "GPU",    "Value": str(entry["gpus"])},
    ]


# -----------------------------------------------------------------------------
# Job Definition
# -----------------------------------------------------------------------------
def job_definition(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """The container blueprint.

    Environment variables set here are **fleet-level defaults**. The invoker
    adds per-shard overrides (MANIFEST_S3_URI, OUTPUT_PREFIX_S3_URI, etc.)
    via ``containerOverrides.environment`` at SubmitJob time.

    ResourceRequirements are derived from :data:`INSTANCE_RESOURCES` for the
    plan's first compute environment (all CEs in a plan must share the same
    instance type at the moment; the table uses that).
    """
    lid = "JobDefinition"
    ms = plan.model_spec

    # Pull resource shape from the first instance type of the first CE.
    # If the CE has multiple types (e.g., [g7e.2xlarge, g7e.12xlarge, ...]),
    # they all share the same JobDef, so the smallest type's shape governs.
    primary_instance_type = plan.compute_environments[0].instance_types[0]
    resource_requirements = _resource_requirements(primary_instance_type)

    env_defaults = [
        {"Name": "HF_MODEL_ID", "Value": ms.hf_model_id},
        {"Name": "MODEL_ID", "Value": ms.served_model_name},
        {"Name": "TENSOR_PARALLEL_SIZE", "Value": str(plan.tensor_parallel)},
        {"Name": "DATA_PARALLEL_SIZE", "Value": str(plan.data_parallel)},
        {"Name": "PIPELINE_PARALLEL_SIZE", "Value": str(plan.pipeline_parallel)},
        {"Name": "MAX_MODEL_LEN", "Value": str(plan.effective_max_model_len)},
        {"Name": "GPU_MEMORY_UTILIZATION", "Value": f"{plan.gpu_memory_utilization}"},
        {"Name": "DTYPE", "Value": ms.dtype},
        {"Name": "IN_FLIGHT_PER_JOB", "Value": str(plan.in_flight_per_job)},
        {"Name": "ENABLE_PREFIX_CACHING",
         "Value": "true" if plan.enable_prefix_caching else "false"},
        {"Name": "EXTRA_SERVE_FLAGS", "Value": plan.extra_serve_flags},
        {"Name": "VLLM_STARTUP_TIMEOUT_S", "Value": str(plan.vllm_startup_timeout_seconds)},
        {"Name": "REQUEST_TIMEOUT_S", "Value": str(plan.request_timeout_seconds)},
        # aioboto3/aiobotocore in the container needs an explicit region for
        # SigV4 signing; without it, the async S3 client sends unsigned
        # requests and S3 returns "No AWSAccessKey was presented".
        {"Name": "AWS_REGION", "Value": plan.region},
    ]

    # Plan-author-provided per-model env vars (e.g.
    # VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8=1 for gpt-oss-20b on Blackwell).
    # Reserved-name conflicts are blocked at plan-construction time by the
    # validator on BatchDeploymentPlan; defensive-deduplicate here too.
    _existing_names = {e["Name"] for e in env_defaults}
    for _name, _value in plan.extra_env_vars.items():
        if _name in _existing_names:
            continue
        env_defaults.append({"Name": _name, "Value": _value})

    container_properties = {
        "Image": {"Ref": "ContainerImageUri"},
        "Command": [],   # image's ENTRYPOINT (run.sh) handles everything
        "JobRoleArn": {"Fn::GetAtt": ["JobRole", "Arn"]},
        "ExecutionRoleArn": {"Fn::GetAtt": ["JobExecutionRole", "Arn"]},
        "ResourceRequirements": resource_requirements,
        "Environment": env_defaults,
        # HF_TOKEN is injected at task-start time by the ECS agent from
        # Secrets Manager (HfTokenSecret) — it NEVER appears in the
        # JobDefinition env, in SubmitJob args, or in describe-jobs output.
        # Both env-var names are set because vLLM / huggingface-hub
        # libraries look for different ones.
        "Secrets": [
            {"Name": "HF_TOKEN", "ValueFrom": {"Ref": "HfTokenSecret"}},
            {"Name": "HUGGING_FACE_HUB_TOKEN", "ValueFrom": {"Ref": "HfTokenSecret"}},
        ],
        "LogConfiguration": {
            "LogDriver": "awslogs",
            "Options": {
                "awslogs-group": {"Ref": "JobLogGroup"},
                "awslogs-region": {"Ref": "AWS::Region"},
                "awslogs-stream-prefix": ms.resource_prefix,
            },
        },
        "LinuxParameters": {
            "SharedMemorySize": 16384,  # 16 GiB /dev/shm for vLLM NCCL
        },
    }

    resource: dict[str, Any] = {
        "Type": "AWS::Batch::JobDefinition",
        "DependsOn": ["JobRole", "JobExecutionRole", "JobLogGroup", "HfTokenSecret"],
        "Properties": {
            "JobDefinitionName": ms.job_definition_name,
            "Type": "container",
            "PlatformCapabilities": ["EC2"],
            "ContainerProperties": container_properties,
            "RetryStrategy": {
                "Attempts": 2,
                # Only retry on infrastructure failures.
                "EvaluateOnExit": [
                    {"OnStatusReason": "Host EC2*", "Action": "RETRY"},
                    {"OnReason": "CannotInspectContainerError*", "Action": "RETRY"},
                    {"OnReason": "DockerTimeoutError*", "Action": "RETRY"},
                    # Any other non-zero exit: don't retry
                    {"OnExitCode": "*", "Action": "EXIT"},
                ],
            },
            "Timeout": {
                "AttemptDurationSeconds": plan.job_timeout_seconds,
            },
            "PropagateTags": True,
            "Tags": _tags(plan),
        },
    }
    return lid, resource
