"""Tests for deploy.py using moto-backed CFN.

moto's CloudFormation support is imperfect for IAM + Batch resources, but
we can test the deployer's CFN-request shaping and output-parsing logic
by mocking boto3.client directly.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, WaiterError

from llm_batch_deploy.data import (
    BatchDeploymentPlan,
    ComputeEnvironmentConfig,
    ModelSpec,
    QueueConfig,
)
from llm_batch_deploy.deployer.deploy import (
    StackOutputs,
    _auto_detect_ecr,
    _load_outputs,
    _resolve_default_vpc,
    _stack_exists,
    _stack_in_unrecoverable_state,
    deploy,
    teardown,
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
        queues=[QueueConfig(
            name_suffix="primary",
            compute_environment_suffixes=["p4d-spot"],
        )],
    )


def _ecr_not_found() -> MagicMock:
    """ECR client mock that returns RepositoryNotFoundException.

    Default for tests that cover the fresh-create path. Drop it in as
    ``ecr_client=_ecr_not_found()`` to keep deploy()'s auto-detect from
    hitting real AWS or failing unpredictably in CI.
    """
    ecr = MagicMock()
    ecr.describe_repositories.side_effect = ClientError(
        {"Error": {"Code": "RepositoryNotFoundException", "Message": "not found"}},
        "DescribeRepositories",
    )
    return ecr


# ---------------------------------------------------------------------------
# _stack_exists
# ---------------------------------------------------------------------------
class TestStackExists:
    def test_exists(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "CREATE_COMPLETE"}],
        }
        assert _stack_exists(cfn, "foo") is True

    def test_deleted_counts_as_nonexistent(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "DELETE_COMPLETE"}],
        }
        assert _stack_exists(cfn, "foo") is False

    def test_missing_raises_mapped_to_false(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "Stack with id foo does not exist"}},
            "DescribeStacks",
        )
        assert _stack_exists(cfn, "foo") is False

    @pytest.mark.parametrize("status", [
        "ROLLBACK_COMPLETE", "ROLLBACK_FAILED", "CREATE_FAILED", "DELETE_FAILED",
        "UPDATE_ROLLBACK_FAILED",
    ])
    def test_unrecoverable_states_treated_as_nonexistent(self, status: str) -> None:
        """A stuck-in-failed-create stack still occupies the stack-name slot
        but rejects update_stack(). _stack_exists must return False so
        deploy() routes to the create-stack path; _stack_in_unrecoverable_state
        must return True so deploy() deletes the lingering slot first.

        UPDATE_ROLLBACK_FAILED is the failed-update analogue of ROLLBACK_FAILED:
        an update + auto-rollback both failed, and CFN won't accept a fresh
        update_stack() until the stack is either continue-rolled-back (often
        fails for the same root cause) or deleted. deploy() must take the
        delete + recreate path.
        """
        cfn = MagicMock()
        cfn.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": status}],
        }
        assert _stack_exists(cfn, "foo") is False
        assert _stack_in_unrecoverable_state(cfn, "foo") is True

    def test_create_complete_is_not_unrecoverable(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "CREATE_COMPLETE"}],
        }
        assert _stack_in_unrecoverable_state(cfn, "foo") is False

    def test_delete_complete_is_not_unrecoverable(self) -> None:
        """DELETE_COMPLETE frees the stack-name slot — create_stack proceeds
        without an explicit delete-first."""
        cfn = MagicMock()
        cfn.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "DELETE_COMPLETE"}],
        }
        assert _stack_in_unrecoverable_state(cfn, "foo") is False


# ---------------------------------------------------------------------------
# _resolve_default_vpc
# ---------------------------------------------------------------------------
class TestResolveDefaultVpc:
    def test_happy(self) -> None:
        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-abc"}]}
        ec2.describe_subnets.return_value = {
            "Subnets": [{"SubnetId": "subnet-1"}, {"SubnetId": "subnet-2"}],
        }
        vpc, subnets = _resolve_default_vpc(ec2)
        assert vpc == "vpc-abc"
        assert subnets == ["subnet-1", "subnet-2"]

    def test_no_default_vpc(self) -> None:
        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": []}
        with pytest.raises(RuntimeError, match="No default VPC"):
            _resolve_default_vpc(ec2)

    def test_no_subnets(self) -> None:
        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-abc"}]}
        ec2.describe_subnets.return_value = {"Subnets": []}
        with pytest.raises(RuntimeError, match="has no subnets"):
            _resolve_default_vpc(ec2)


# ---------------------------------------------------------------------------
# _load_outputs
# ---------------------------------------------------------------------------
class TestLoadOutputs:
    def test_parses_outputs(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.return_value = {
            "Stacks": [{
                "Outputs": [
                    {"OutputKey": "StagingBucketName", "OutputValue": "bucket-1"},
                    {"OutputKey": "JobDefinitionArn", "OutputValue": "arn:aws:batch:us-east-2:123:jd/abc"},
                    {"OutputKey": "EcrRepositoryUri", "OutputValue": "123.dkr.ecr.us-east-2.amazonaws.com/foo"},
                    {"OutputKey": "JobQueuePrimaryArn", "OutputValue": "arn:aws:batch:us-east-2:123:q/primary"},
                ],
            }],
        }
        out = _load_outputs(cfn, "s", _plan())
        assert isinstance(out, StackOutputs)
        assert out.staging_bucket == "bucket-1"
        assert out.job_definition_arn == "arn:aws:batch:us-east-2:123:jd/abc"
        assert out.ecr_repository_uri == "123.dkr.ecr.us-east-2.amazonaws.com/foo"
        assert out.queue_arns_by_suffix == {
            "primary": "arn:aws:batch:us-east-2:123:q/primary",
        }
        assert out.primary_queue_arn == "arn:aws:batch:us-east-2:123:q/primary"

    def test_tolerant_missing_stack(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = ClientError(
            {"Error": {"Code": "ValidationError"}}, "DescribeStacks",
        )
        out = _load_outputs(cfn, "s", _plan(), tolerant=True)
        assert out.staging_bucket == ""
        assert out.queue_arns_by_suffix == {}


# ---------------------------------------------------------------------------
# deploy()
# ---------------------------------------------------------------------------
class TestDeploy:
    def test_creates_new_stack_and_waits(self) -> None:
        cfn = MagicMock()
        # describe_stacks raises before create (once for _stack_exists,
        # once for _stack_in_unrecoverable_state), then returns outputs
        cfn.describe_stacks.side_effect = [
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            {"Stacks": [{"Outputs": [
                {"OutputKey": "StagingBucketName", "OutputValue": "bucket"},
                {"OutputKey": "JobDefinitionArn", "OutputValue": "jd-arn"},
                {"OutputKey": "EcrRepositoryUri", "OutputValue": "ecr-uri"},
                {"OutputKey": "JobQueuePrimaryArn", "OutputValue": "queue-arn"},
            ]}]},
        ]
        cfn.get_waiter.return_value.wait.return_value = None

        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-1"}]}
        ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s-1"}]}

        out = deploy(_plan(), cfn_client=cfn, ec2_client=ec2, ecr_client=_ecr_not_found())

        # create_stack call shape
        create_kwargs = cfn.create_stack.call_args.kwargs
        assert create_kwargs["StackName"] == "medgemma-27b-batch"
        assert "CAPABILITY_NAMED_IAM" in create_kwargs["Capabilities"]
        # Template parseable
        template = json.loads(create_kwargs["TemplateBody"])
        assert "Resources" in template
        # Parameters correct
        params = {p["ParameterKey"]: p["ParameterValue"] for p in create_kwargs["Parameters"]}
        assert params["VpcId"] == "vpc-1"
        assert params["SubnetIds"] == "s-1"
        # Tolerates no image URI
        assert params["ContainerImageUri"] == "REPLACE_ME_WITH_REAL_IMAGE"

        assert out.staging_bucket == "bucket"
        assert out.job_definition_arn == "jd-arn"

        # Stack-level Tags carry both Project AND Model. CFN propagates
        # stack-level tags to every taggable resource it creates, so
        # cleanup-by-Model can sweep them.
        tags = {t["Key"]: t["Value"] for t in create_kwargs["Tags"]}
        assert "Project" in tags, (
            "Stack must carry Project tag for per-project discoverability"
        )
        assert tags["Model"] == "medgemma-27b", (
            "Stack must carry Model=<resource_prefix> so cleanup automation "
            "can sweep all stacks for a given model."
        )

    def test_updates_existing_stack(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]},  # _stack_exists
            {"Stacks": [{"Outputs": [
                {"OutputKey": "StagingBucketName", "OutputValue": "bucket"},
                {"OutputKey": "JobDefinitionArn", "OutputValue": "jd"},
                {"OutputKey": "EcrRepositoryUri", "OutputValue": "ecr"},
                {"OutputKey": "JobQueuePrimaryArn", "OutputValue": "q"},
            ]}]},
        ]
        cfn.get_waiter.return_value.wait.return_value = None

        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "v"}]}
        ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s"}]}

        out = deploy(_plan(), cfn_client=cfn, ec2_client=ec2, ecr_client=_ecr_not_found())
        cfn.create_stack.assert_not_called()
        cfn.update_stack.assert_called_once()
        assert out.staging_bucket == "bucket"

    def test_no_updates_returns_outputs(self) -> None:
        """UpdateStack returning 'No updates' is not an error."""
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]},
            {"Stacks": [{"Outputs": [
                {"OutputKey": "StagingBucketName", "OutputValue": "bucket"},
                {"OutputKey": "JobDefinitionArn", "OutputValue": "jd"},
                {"OutputKey": "EcrRepositoryUri", "OutputValue": "ecr"},
                {"OutputKey": "JobQueuePrimaryArn", "OutputValue": "q"},
            ]}]},
        ]
        cfn.update_stack.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "No updates are to be performed"}},
            "UpdateStack",
        )
        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "v"}]}
        ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s"}]}

        out = deploy(_plan(), cfn_client=cfn, ec2_client=ec2, ecr_client=_ecr_not_found())
        assert out.staging_bucket == "bucket"

    def test_explicit_vpc_subnets_override(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = [
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            {"Stacks": [{"Outputs": []}]},
        ]
        cfn.get_waiter.return_value.wait.return_value = None
        ec2 = MagicMock()
        # ec2 should NOT be asked for default VPC
        deploy(
            _plan(), cfn_client=cfn, ec2_client=ec2,
            ecr_client=_ecr_not_found(),
            vpc_id="vpc-explicit",
            subnet_ids=["sub-a", "sub-b"],
            container_image_uri="my-ecr-uri:tag",
        )
        ec2.describe_vpcs.assert_not_called()
        params = {p["ParameterKey"]: p["ParameterValue"]
                  for p in cfn.create_stack.call_args.kwargs["Parameters"]}
        assert params["VpcId"] == "vpc-explicit"
        assert params["SubnetIds"] == "sub-a,sub-b"
        assert params["ContainerImageUri"] == "my-ecr-uri:tag"

    def test_wait_timeout_raises_with_events(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = [
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            {"Stacks": [{"StackStatus": "CREATE_FAILED"}]},
        ]
        cfn.get_waiter.return_value.wait.side_effect = WaiterError(
            "wait", "boom", {}
        )
        cfn.describe_stack_events.return_value = {
            "StackEvents": [{
                "Timestamp": "2026-01-01",
                "LogicalResourceId": "BatchSG",
                "ResourceStatus": "CREATE_FAILED",
                "ResourceStatusReason": "Invalid VPC",
            }],
        }
        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "v"}]}
        ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s"}]}

        with pytest.raises(RuntimeError, match="CREATE_FAILED"):
            deploy(_plan(), cfn_client=cfn, ec2_client=ec2, ecr_client=_ecr_not_found())

    def test_deletes_lingering_rollback_complete_stack_before_recreate(self) -> None:
        """Stack stuck in ROLLBACK_COMPLETE: update_stack() rejects it AND
        a fresh create_stack() hits AlreadyExistsException because the slot
        is still occupied. Deploy() must delete_stack + wait, then create.
        """
        cfn = MagicMock()
        # 1: _stack_exists -> ROLLBACK_COMPLETE -> not updateable
        # 2: _stack_in_unrecoverable_state -> True -> trigger delete-first
        # 3: _load_outputs after waiter
        cfn.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "ROLLBACK_COMPLETE"}]},
            {"Stacks": [{"StackStatus": "ROLLBACK_COMPLETE"}]},
            {"Stacks": [{"Outputs": [
                {"OutputKey": "StagingBucketName", "OutputValue": "bucket"},
                {"OutputKey": "JobDefinitionArn", "OutputValue": "jd"},
                {"OutputKey": "JobQueuePrimaryArn", "OutputValue": "q"},
            ]}]},
        ]
        cfn.get_waiter.return_value.wait.return_value = None

        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "v"}]}
        ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s"}]}

        deploy(_plan(), cfn_client=cfn, ec2_client=ec2, ecr_client=_ecr_not_found())

        cfn.update_stack.assert_not_called()
        cfn.delete_stack.assert_called_once_with(StackName="medgemma-27b-batch")
        cfn.create_stack.assert_called_once()
        # Both waiters used: stack_delete_complete then stack_create_complete.
        waiter_names = [c.args[0] for c in cfn.get_waiter.call_args_list]
        assert "stack_delete_complete" in waiter_names
        assert "stack_create_complete" in waiter_names


# ---------------------------------------------------------------------------
# teardown()
# ---------------------------------------------------------------------------
class TestTeardown:
    def test_deletes_existing(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "CREATE_COMPLETE"}],
        }
        cfn.get_waiter.return_value.wait.return_value = None

        teardown("my-stack", region="us-east-2", cfn_client=cfn)
        cfn.delete_stack.assert_called_once_with(StackName="my-stack")

    def test_noop_when_missing(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "does not exist"}},
            "DescribeStacks",
        )
        teardown("my-stack", region="us-east-2", cfn_client=cfn)
        cfn.delete_stack.assert_not_called()

    def test_no_wait(self) -> None:
        cfn = MagicMock()
        cfn.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "CREATE_COMPLETE"}],
        }
        teardown("s", region="us-east-2", cfn_client=cfn, wait=False)
        cfn.get_waiter.assert_not_called()


# ---------------------------------------------------------------------------
# _auto_detect_ecr — smooth redeploy after teardown
# ---------------------------------------------------------------------------
class TestAutoDetectEcr:
    def test_repo_missing_preserves_defaults(self) -> None:
        """Fresh account: no repo → return unchanged so CFN creates one."""
        ecr = _ecr_not_found()
        include_ecr, uri = _auto_detect_ecr(
            _plan(), include_ecr=True, container_image_uri=None, ecr_client=ecr,
        )
        assert include_ecr is True
        assert uri is None

    def test_repo_exists_flips_to_reuse(self) -> None:
        """Repo present (from previous Retain'd stack) → include_ecr=False,
        container_image_uri filled with :latest."""
        ecr = MagicMock()
        ecr.describe_repositories.return_value = {
            "repositories": [{
                "repositoryName": "medgemma-27b-batch",
                "repositoryUri": "111.dkr.ecr.us-east-2.amazonaws.com/medgemma-27b-batch",
            }],
        }
        include_ecr, uri = _auto_detect_ecr(
            _plan(), include_ecr=True, container_image_uri=None, ecr_client=ecr,
        )
        assert include_ecr is False
        assert uri == "111.dkr.ecr.us-east-2.amazonaws.com/medgemma-27b-batch:latest"

    def test_explicit_container_image_preserved(self) -> None:
        """If caller passed container_image_uri, don't overwrite it."""
        ecr = MagicMock()
        ecr.describe_repositories.return_value = {
            "repositories": [{
                "repositoryName": "medgemma-27b-batch",
                "repositoryUri": "111.dkr.ecr.us-east-2.amazonaws.com/medgemma-27b-batch",
            }],
        }
        include_ecr, uri = _auto_detect_ecr(
            _plan(), include_ecr=True,
            container_image_uri="custom:v1.2",
            ecr_client=ecr,
        )
        assert include_ecr is False
        assert uri == "custom:v1.2"  # preserved, not overwritten

    def test_include_ecr_false_is_noop(self) -> None:
        """If caller explicitly said include_ecr=False, don't even ping ECR."""
        ecr = MagicMock()
        include_ecr, uri = _auto_detect_ecr(
            _plan(), include_ecr=False,
            container_image_uri="existing:tag", ecr_client=ecr,
        )
        assert include_ecr is False
        assert uri == "existing:tag"
        ecr.describe_repositories.assert_not_called()

    def test_ecr_client_error_falls_through(self) -> None:
        """If ECR returns something unexpected (IAM denied, transient, etc.),
        we don't break — we just proceed with stack-managed ECR."""
        ecr = MagicMock()
        ecr.describe_repositories.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "DescribeRepositories",
        )
        include_ecr, uri = _auto_detect_ecr(
            _plan(), include_ecr=True, container_image_uri=None, ecr_client=ecr,
        )
        assert include_ecr is True   # unchanged
        assert uri is None


class TestDeployAutoDetectIntegration:
    def test_deploy_reuses_retained_ecr(self) -> None:
        """End-to-end: retained ECR + fresh stack create → include_ecr=False
        threaded through to the template + existing :latest filled in."""
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = [
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            {"Stacks": [{"Outputs": [
                {"OutputKey": "StagingBucketName", "OutputValue": "bucket"},
                {"OutputKey": "JobDefinitionArn", "OutputValue": "jd"},
                {"OutputKey": "JobQueuePrimaryArn", "OutputValue": "q"},
            ]}]},
        ]
        cfn.get_waiter.return_value.wait.return_value = None

        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "v"}]}
        ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s"}]}

        ecr = MagicMock()
        ecr.describe_repositories.return_value = {
            "repositories": [{
                "repositoryName": "medgemma-27b-batch",
                "repositoryUri": "111.dkr.ecr.us-east-2.amazonaws.com/medgemma-27b-batch",
            }],
        }

        stack = deploy(_plan(), cfn_client=cfn, ec2_client=ec2, ecr_client=ecr)

        # ContainerImageUri parameter should be the existing repo's :latest
        create_kwargs = cfn.create_stack.call_args.kwargs
        params = {p["ParameterKey"]: p["ParameterValue"]
                  for p in create_kwargs["Parameters"]}
        assert params["ContainerImageUri"] == (
            "111.dkr.ecr.us-east-2.amazonaws.com/medgemma-27b-batch:latest"
        )
        # Template should NOT include the EcrRepository resource
        template = json.loads(create_kwargs["TemplateBody"])
        assert "EcrRepository" not in template["Resources"]
        # Stack outputs should surface the existing repo URI even though
        # CFN didn't output it (stack wasn't managing the ECR).
        assert stack.ecr_repository_uri == (
            "111.dkr.ecr.us-east-2.amazonaws.com/medgemma-27b-batch"
        )

    def test_deploy_include_ecr_false_populates_repo_uri_from_image(self) -> None:
        """User passes include_ecr=False + container_image_uri → ecr_repository_uri
        derived from the image URI (strip tag), so notebook section 3 works."""
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = [
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks"),
            {"Stacks": [{"Outputs": [
                {"OutputKey": "StagingBucketName", "OutputValue": "bucket"},
                {"OutputKey": "JobDefinitionArn", "OutputValue": "jd"},
                {"OutputKey": "JobQueuePrimaryArn", "OutputValue": "q"},
            ]}]},
        ]
        cfn.get_waiter.return_value.wait.return_value = None
        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "v"}]}
        ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s"}]}

        stack = deploy(
            _plan(), cfn_client=cfn, ec2_client=ec2,
            include_ecr=False,
            container_image_uri="999.dkr.ecr.us-west-2.amazonaws.com/my-repo:v1.0",
        )
        assert stack.ecr_repository_uri == (
            "999.dkr.ecr.us-west-2.amazonaws.com/my-repo"
        )

    def test_no_updates_path_still_fills_ecr_uri(self) -> None:
        """The 'No updates are to be performed' early-return path also
        needs ecr_repository_uri populated — otherwise the notebook's
        section 3 hits AttributeError on the second run of deploy()."""
        cfn = MagicMock()
        cfn.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]},   # _stack_exists
            {"Stacks": [{"Outputs": [                           # _load_outputs
                {"OutputKey": "StagingBucketName", "OutputValue": "bucket"},
                {"OutputKey": "JobDefinitionArn", "OutputValue": "jd"},
                {"OutputKey": "JobQueuePrimaryArn", "OutputValue": "q"},
                # No EcrRepositoryUri output — stack wasn't managing ECR.
            ]}]},
        ]
        cfn.update_stack.side_effect = ClientError(
            {"Error": {"Code": "ValidationError", "Message": "No updates are to be performed"}},
            "UpdateStack",
        )
        ec2 = MagicMock()
        ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "v"}]}
        ec2.describe_subnets.return_value = {"Subnets": [{"SubnetId": "s"}]}

        ecr = MagicMock()
        ecr.describe_repositories.return_value = {
            "repositories": [{
                "repositoryName": "medgemma-27b-batch",
                "repositoryUri": "111.dkr.ecr.us-east-2.amazonaws.com/medgemma-27b-batch",
            }],
        }

        stack = deploy(_plan(), cfn_client=cfn, ec2_client=ec2, ecr_client=ecr)
        assert stack.ecr_repository_uri == (
            "111.dkr.ecr.us-east-2.amazonaws.com/medgemma-27b-batch"
        )
