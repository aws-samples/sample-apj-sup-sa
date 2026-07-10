"""CloudFormation resource builders — IAM roles used by the Batch stack.

Three roles:

1. **BatchServiceRole** — lets AWS Batch call EC2/ECS on our behalf.
2. **EcsInstanceRole** + instance profile — attached to Batch EC2 instances;
   grants ECR pull + SSM session (for debugging).
3. **JobRole** — assumed by the container task; grants S3 R/W to the
   staging bucket.

Each builder returns ``(logical_id, resource_dict)`` so the caller stitches
them into ``Resources`` by logical id.
"""
from __future__ import annotations

from typing import Any

from ..data import BatchDeploymentPlan

TAG_KEY_PROJECT = "Project"
TAG_KEY_MODEL = "Model"


def _tags(plan: BatchDeploymentPlan) -> list[dict[str, str]]:
    # Tag policy: every IAM resource carries Project + Model tags so cleanup
    # automation can sweep all stacks for a given model.
    return [
        {"Key": TAG_KEY_PROJECT, "Value": plan.model_spec.tag_value},
        {"Key": TAG_KEY_MODEL, "Value": plan.model_spec.resource_prefix},
    ]


def batch_service_role(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """IAM role for AWS Batch service — standard managed policy."""
    lid = "BatchServiceRole"
    resource = {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "RoleName": f"{plan.model_spec.resource_prefix}-batch-service",
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "batch.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            },
            "ManagedPolicyArns": [
                "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole",
            ],
            "Tags": _tags(plan),
        },
    }
    return lid, resource


def ecs_instance_role(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """IAM role attached to EC2 instances in the Batch compute environment."""
    lid = "EcsInstanceRole"
    resource = {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "RoleName": f"{plan.model_spec.resource_prefix}-batch-ecs-instance",
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            },
            "ManagedPolicyArns": [
                "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
                "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
                "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
            ],
            "Tags": _tags(plan),
        },
    }
    return lid, resource


def ecs_instance_profile(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """Instance profile wrapping :func:`ecs_instance_role`."""
    lid = "EcsInstanceProfile"
    resource = {
        "Type": "AWS::IAM::InstanceProfile",
        "Properties": {
            "InstanceProfileName": f"{plan.model_spec.resource_prefix}-batch-ecs-instance",
            "Roles": [{"Ref": "EcsInstanceRole"}],
        },
    }
    return lid, resource


def job_role(
    plan: BatchDeploymentPlan,
    *,
    include_staging_bucket: bool = True,
) -> tuple[str, dict[str, Any]]:
    """IAM role assumed by the container itself — S3 R/W on staging bucket.

    When ``include_staging_bucket=False`` the policy references the
    ``ExistingStagingBucketName`` parameter (re-deploy path) instead of
    the in-stack ``StagingBucket`` logical resource.
    """
    if include_staging_bucket:
        bucket_arn = {"Fn::GetAtt": ["StagingBucket", "Arn"]}
        bucket_arn_glob = {"Fn::Sub": "${StagingBucket.Arn}/*"}
    else:
        bucket_arn = {"Fn::Sub": "arn:aws:s3:::${ExistingStagingBucketName}"}
        bucket_arn_glob = {"Fn::Sub": "arn:aws:s3:::${ExistingStagingBucketName}/*"}
    lid = "JobRole"
    resource = {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "RoleName": f"{plan.model_spec.resource_prefix}-batch-job",
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            },
            "Policies": [{
                "PolicyName": "StagingBucketRW",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                                "s3:PutObject",
                                "s3:DeleteObject",
                                "s3:ListBucket",
                                "s3:HeadObject",
                            ],
                            "Resource": [bucket_arn, bucket_arn_glob],
                        },
                    ],
                },
            }],
            "Tags": _tags(plan),
        },
    }
    return lid, resource


def job_execution_role(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """ECS task execution role — pulls ECR image + writes CloudWatch logs +
    fetches the HuggingFace token from Secrets Manager for the container's
    environment.

    The ECS agent (not the container) assumes this role to hydrate the
    JobDefinition's ``secrets`` block at task-start time. Permission is
    scoped to the stack's own HfTokenSecret, not wildcard.
    """
    lid = "JobExecutionRole"
    resource = {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "RoleName": f"{plan.model_spec.resource_prefix}-batch-job-exec",
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            },
            "ManagedPolicyArns": [
                "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
            ],
            "Policies": [{
                "PolicyName": "ReadHfTokenSecret",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": "secretsmanager:GetSecretValue",
                        "Resource": {"Ref": "HfTokenSecret"},
                    }],
                },
            }],
            "Tags": _tags(plan),
        },
    }
    return lid, resource
