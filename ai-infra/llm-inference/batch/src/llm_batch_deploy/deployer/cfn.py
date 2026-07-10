"""Top-level CloudFormation builder.

Produces a complete CFN template dict from a :class:`BatchDeploymentPlan`.
The template takes two parameters at deploy time: ``VpcId`` + ``SubnetIds``
(the deployer auto-resolves these from the default VPC if the user didn't
provide them) and ``ContainerImageUri`` (the ECR image URI to use).
"""
from __future__ import annotations

from typing import Any

from ..data import BatchDeploymentPlan
from .cfn_batch import (
    compute_environment,
    job_definition,
    job_queue,
    launch_template,
    spot_fleet_role,
)
from .cfn_iam import (
    batch_service_role,
    ecs_instance_profile,
    ecs_instance_role,
    job_execution_role,
    job_role,
)
from .cfn_network import (
    batch_security_group,
    ecr_repository,
    hf_token_secret,
    job_log_group,
    staging_bucket,
)

CFN_TEMPLATE_VERSION = "2010-09-09"


def build_template(
    plan: BatchDeploymentPlan,
    *,
    include_ecr: bool = True,
    include_staging_bucket: bool = True,
) -> dict[str, Any]:
    """Build a CloudFormation template dict for the given plan.

    Parameters
    ----------
    plan
        The deployment plan.
    include_ecr
        If True (default), the stack creates + manages its own ECR repo.
        If False, the stack expects ``ContainerImageUri`` to point at an
        already-created repo. Set False when the user maintains the image
        registry outside of this stack.
    include_staging_bucket
        If True (default), the stack creates + manages its own staging S3
        bucket. If False, the stack expects an ``ExistingStagingBucketName``
        parameter and uses that bucket without creating one. Set False on
        re-deploy after teardown — the prior stack's bucket carries
        ``DeletionPolicy: Retain``, so a fresh create_stack would otherwise
        hit ``BucketAlreadyExists``.
    """
    resources: dict[str, Any] = {}

    # IAM
    for builder in (
        batch_service_role, ecs_instance_role, ecs_instance_profile,
        job_execution_role,
    ):
        lid, res = builder(plan)
        resources[lid] = res
    # job_role's policy references the staging bucket — its references must
    # toggle in lockstep with include_staging_bucket so the stack continues
    # to grant container-side S3 R/W on the consumed (vs created) bucket.
    lid, res = job_role(plan, include_staging_bucket=include_staging_bucket)
    resources[lid] = res

    # Storage + networking + secrets
    for builder in (batch_security_group, job_log_group, hf_token_secret):
        lid, res = builder(plan)
        resources[lid] = res

    if include_staging_bucket:
        lid, res = staging_bucket(plan)
        resources[lid] = res

    if include_ecr:
        lid, res = ecr_repository(plan)
        resources[lid] = res

    # Launch template — gives EC2 instances a bigger EBS root volume so
    # the vLLM image + HF model cache actually fit.
    lid, res = launch_template(plan)
    resources[lid] = res

    # Spot Fleet role only if any CE is spot
    if any(ce.capacity_mode == "spot" for ce in plan.compute_environments):
        lid, res = spot_fleet_role(plan)
        resources[lid] = res

    # Compute environments + queues
    for ce in plan.compute_environments:
        lid, res = compute_environment(plan, ce)
        resources[lid] = res

    for q in plan.queues:
        lid, res = job_queue(plan, q)
        resources[lid] = res

    # Job definition
    lid, res = job_definition(plan)
    resources[lid] = res

    # Flatten all CEs' instance_types into one set for the stack description.
    all_instance_types = sorted({
        t for ce in plan.compute_environments for t in ce.instance_types
    })
    template: dict[str, Any] = {
        "AWSTemplateFormatVersion": CFN_TEMPLATE_VERSION,
        "Description": (
            f"Batch inference stack for {plan.model_spec.hf_model_id} "
            f"on {all_instance_types}."
        ),
        "Parameters": {
            "VpcId": {
                "Type": "AWS::EC2::VPC::Id",
                "Description": "VPC for Batch compute environments",
            },
            "SubnetIds": {
                "Type": "List<AWS::EC2::Subnet::Id>",
                "Description": "Subnets in the above VPC (>= 2 AZs recommended)",
            },
            "ContainerImageUri": {
                "Type": "String",
                "Description": (
                    "ECR image URI for the vLLM runtime container "
                    "(e.g. 123.dkr.ecr.us-west-2.amazonaws.com/foo:latest). "
                    "If include_ecr=True, point at the stack's ECR repo "
                    "once you've built and pushed the image."
                ),
            },
            "ExistingStagingBucketName": {
                "Type": "String",
                "Default": "",
                "Description": (
                    "If non-empty, the stack consumes this existing S3 "
                    "bucket instead of creating one (used on re-deploy "
                    "after teardown — prior bucket carries Retain policy)."
                ),
            },
        },
        "Resources": resources,
        "Outputs": _build_outputs(
            plan,
            include_ecr=include_ecr,
            include_staging_bucket=include_staging_bucket,
        ),
    }
    return template


def _build_outputs(
    plan: BatchDeploymentPlan,
    *,
    include_ecr: bool,
    include_staging_bucket: bool = True,
) -> dict[str, Any]:
    """Stack outputs — what the deployer surfaces to users after create-stack."""
    bucket_value: dict[str, Any] = (
        {"Ref": "StagingBucket"} if include_staging_bucket
        else {"Ref": "ExistingStagingBucketName"}
    )
    outputs: dict[str, Any] = {
        "StagingBucketName": {
            "Value": bucket_value,
            "Description": "S3 bucket for manifests + inputs + outputs.",
            "Export": {"Name": {
                "Fn::Sub": f"${{AWS::StackName}}-StagingBucket"
            }},
        },
        "JobDefinitionArn": {
            "Value": {"Ref": "JobDefinition"},
            "Description": "Batch job definition ARN.",
            "Export": {"Name": {
                "Fn::Sub": f"${{AWS::StackName}}-JobDefinitionArn"
            }},
        },
        "HfTokenSecretArn": {
            "Value": {"Ref": "HfTokenSecret"},
            "Description": (
                "ARN of the HuggingFace token secret. Notebook updates "
                "its value via PutSecretValue; container reads it via "
                "JobDefinition.secrets at task-start."
            ),
            "Export": {"Name": {
                "Fn::Sub": f"${{AWS::StackName}}-HfTokenSecretArn"
            }},
        },
    }

    # One output per queue
    from .cfn_batch import _logical_id_for_queue
    for q in plan.queues:
        lid = _logical_id_for_queue(q)
        outputs[f"{lid}Arn"] = {
            "Value": {"Ref": lid},
            "Description": f"Job queue ARN for {q.name_suffix}.",
            "Export": {"Name": {
                "Fn::Sub": f"${{AWS::StackName}}-{lid}Arn"
            }},
        }

    if include_ecr:
        outputs["EcrRepositoryUri"] = {
            "Value": {"Fn::GetAtt": ["EcrRepository", "RepositoryUri"]},
            "Description": "ECR repo for the container image.",
            "Export": {"Name": {
                "Fn::Sub": f"${{AWS::StackName}}-EcrRepositoryUri"
            }},
        }

    return outputs
