"""Network + storage CFN primitives for the Batch stack.

* StagingBucket (S3) — inputs, outputs, manifests.
* EcrRepository (optional) — image registry.
* BatchSecurityGroup — default deny, egress-only (NATed) for EC2 instances.
* JobLogGroup — CloudWatch log group for Batch jobs.
"""
from __future__ import annotations

from typing import Any

from ..data import BatchDeploymentPlan

_TAG_KEY = "Project"
_TAG_KEY_MODEL = "Model"


def _tags(plan: BatchDeploymentPlan) -> list[dict[str, str]]:
    # Tag policy: every AWS resource carries Project + Model tags so cleanup
    # automation can sweep all resources for a given model.
    return [
        {"Key": _TAG_KEY, "Value": plan.model_spec.tag_value},
        {"Key": _TAG_KEY_MODEL, "Value": plan.model_spec.resource_prefix},
    ]


def staging_bucket(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """S3 bucket for manifests, inputs (uploaded from notebook), outputs.

    Name uses ``${AWS::AccountId}-${AWS::Region}`` suffix to guarantee
    global uniqueness. Bucket is retained on stack delete so data isn't
    lost if users accidentally tear down.
    """
    lid = "StagingBucket"
    resource = {
        "Type": "AWS::S3::Bucket",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {
            "BucketName": {
                "Fn::Sub": (
                    f"{plan.model_spec.resource_prefix}-batch"
                    "-${AWS::AccountId}-${AWS::Region}"
                ),
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [{
                    "ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                }],
            },
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "VersioningConfiguration": {"Status": "Enabled"},
            "LifecycleConfiguration": {
                "Rules": [{
                    "Id": "staging-expiry",
                    "Status": "Enabled",
                    "Prefix": "staging/",
                    "ExpirationInDays": 30,
                }],
            },
            "Tags": _tags(plan),
        },
    }
    return lid, resource


def ecr_repository(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """ECR repo for the vLLM runtime image.

    Users can opt out (and supply an existing image URI) by filtering
    this resource out in :func:`build_template`.
    """
    lid = "EcrRepository"
    resource = {
        "Type": "AWS::ECR::Repository",
        "DeletionPolicy": "Retain",
        "Properties": {
            "RepositoryName": f"{plan.model_spec.resource_prefix}-batch",
            "ImageScanningConfiguration": {"ScanOnPush": True},
            "ImageTagMutability": "MUTABLE",
            "EmptyOnDelete": False,
            "Tags": _tags(plan),
        },
    }
    return lid, resource


def batch_security_group(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """SG for the Batch EC2 instances.

    Egress-only. No inbound because jobs talk outbound to S3 + HF + ECR.
    Requires VpcId; the deployer substitutes this in a parameter.
    """
    lid = "BatchSecurityGroup"
    resource = {
        "Type": "AWS::EC2::SecurityGroup",
        "Properties": {
            "GroupDescription": (
                f"{plan.model_spec.resource_prefix} Batch compute environment"
            ),
            "VpcId": {"Ref": "VpcId"},
            "SecurityGroupEgress": [{
                "IpProtocol": "-1",
                "CidrIp": "0.0.0.0/0",
                "Description": "all egress",
            }],
            "Tags": _tags(plan) + [{
                "Key": "Name",
                "Value": f"{plan.model_spec.resource_prefix}-batch",
            }],
        },
    }
    return lid, resource


def job_log_group(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """CloudWatch log group for Batch job containers."""
    lid = "JobLogGroup"
    resource = {
        "Type": "AWS::Logs::LogGroup",
        "DeletionPolicy": "Delete",
        "Properties": {
            "LogGroupName": f"/aws/batch/{plan.model_spec.resource_prefix}",
            "RetentionInDays": 14,
            "Tags": _tags(plan),
        },
    }
    return lid, resource


def hf_token_secret(plan: BatchDeploymentPlan) -> tuple[str, dict[str, Any]]:
    """Secrets Manager secret for the HuggingFace access token.

    Created with a **placeholder value** by CFN. The notebook has a cell
    that ``PutSecretValue``s the real token after the stack is up. The
    Batch container reads the value from this secret via the
    JobDefinition's ``secrets`` block — the token never transits through
    SubmitJob and never appears in ``describe-jobs`` output.

    On stack deletion, the secret is scheduled for deletion with a
    recovery window (default 30d) rather than hard-deleted, so you can
    restore it if you teardown by mistake.
    """
    lid = "HfTokenSecret"
    resource = {
        "Type": "AWS::SecretsManager::Secret",
        "Properties": {
            "Name": f"{plan.model_spec.resource_prefix}-batch/hf-token",
            "Description": (
                f"HuggingFace access token for {plan.model_spec.hf_model_id} "
                "(read by Batch container via JobDefinition.secrets). "
                "Placeholder value on stack create; update via the notebook."
            ),
            "SecretString": "PLACEHOLDER_UPDATE_VIA_NOTEBOOK",
            "Tags": _tags(plan),
        },
    }
    return lid, resource
