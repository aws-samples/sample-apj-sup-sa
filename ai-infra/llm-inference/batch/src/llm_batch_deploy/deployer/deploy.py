"""Deploy + teardown for the CFN stack.

``deploy(plan, ...)`` → create_stack / update_stack + wait for complete +
return parsed ``StackOutputs``.
``teardown(stack_name, ...)`` → delete_stack + wait.

The deployer also handles the parameter-resolution dance: if the user
doesn't supply ``vpc_id`` / ``subnet_ids``, we find the default VPC's
subnets. If they don't supply ``container_image_uri``, we default to
a placeholder that must be updated before jobs will run (the CFN stack
creates the ECR repo even without the image yet).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import ClientError, WaiterError

from ..data import BatchDeploymentPlan
from .cfn import build_template
from .cfn_batch import _logical_id_for_queue

LOG = logging.getLogger(__name__)

_TERMINAL_COMPLETE = {
    "CREATE_COMPLETE", "UPDATE_COMPLETE",
}
_TERMINAL_FAILURE = {
    "CREATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED",
    "UPDATE_ROLLBACK_COMPLETE", "UPDATE_ROLLBACK_FAILED",
    "DELETE_FAILED",
}

# Stack states that occupy the stack-name slot but reject update_stack().
# DELETE_COMPLETE is special-cased separately (slot is free; create_stack
# proceeds without an explicit delete-first). The rest still hold the slot,
# so deploy() must delete_stack and wait before issuing create_stack —
# otherwise CFN responds with AlreadyExistsException.
#
# UPDATE_ROLLBACK_FAILED occurs when an update fails AND the auto-rollback
# itself also fails — CFN offers only continue-update-rollback (often fails
# again for the same root cause) or delete-stack. update_stack() is rejected,
# so deploy() must treat this like ROLLBACK_FAILED: delete + recreate.
_NOT_UPDATABLE_STATES = frozenset({
    "DELETE_COMPLETE",
    "ROLLBACK_COMPLETE",
    "ROLLBACK_FAILED",
    "CREATE_FAILED",
    "DELETE_FAILED",
    "UPDATE_ROLLBACK_FAILED",
})
_REQUIRES_DELETE_BEFORE_CREATE = _NOT_UPDATABLE_STATES - {"DELETE_COMPLETE"}


@dataclass
class StackOutputs:
    """Parsed output of a successful deploy().

    Maps to values the invoker / waiter / collector need.
    """

    stack_name: str
    region: str
    staging_bucket: str
    job_definition_arn: str
    ecr_repository_uri: str | None
    queue_arns_by_suffix: dict[str, str]
    """Keyed by QueueConfig.name_suffix."""
    hf_token_secret_arn: str = ""
    """ARN of the HuggingFace token secret. Update its value via
    ``upsert_hf_token`` before submitting jobs."""

    @property
    def primary_queue_arn(self) -> str:
        """Convenience — first queue in insertion order."""
        return next(iter(self.queue_arns_by_suffix.values()))


def deploy(
    plan: BatchDeploymentPlan,
    *,
    stack_name: str | None = None,
    vpc_id: str | None = None,
    subnet_ids: list[str] | None = None,
    container_image_uri: str | None = None,
    include_ecr: bool = True,
    include_staging_bucket: bool = True,
    cfn_client: Any | None = None,
    ec2_client: Any | None = None,
    ecr_client: Any | None = None,
    s3_client: Any | None = None,
    wait: bool = True,
    wait_timeout_s: int = 1800,
) -> StackOutputs:
    """Create or update the stack and return its outputs.

    If ``vpc_id`` / ``subnet_ids`` are None, uses the default VPC.
    If ``container_image_uri`` is None, uses a placeholder ``REPLACE_ME`` —
    the stack will still create cleanly; jobs will fail to start until
    the user re-deploys with a real image URI.

    If ``include_ecr`` is True (default) but an ECR repo with the model's
    expected name already exists (from a prior Retain'd stack), this
    function transparently flips to ``include_ecr=False`` and uses the
    existing repo's ``:latest`` tag. This makes re-deploys after
    ``teardown()`` work without manual intervention.
    """
    stack_name = stack_name or plan.model_spec.stack_name
    cfn = cfn_client or boto3.client("cloudformation", region_name=plan.region)
    ec2 = ec2_client or boto3.client("ec2", region_name=plan.region)

    # Auto-detect existing ECR (retained from a prior stack's teardown).
    # If found, switch to include_ecr=False and reuse its :latest tag.
    include_ecr, container_image_uri = _auto_detect_ecr(
        plan,
        include_ecr=include_ecr,
        container_image_uri=container_image_uri,
        ecr_client=ecr_client,
    )

    # Same Retain pattern as ECR — staging bucket survives teardown, so a
    # second create_stack would fail with `BucketAlreadyExists`. Detect
    # the case and let the stack consume the existing bucket via a
    # parameter rather than creating a new one. Keeps re-deploy idempotent.
    include_staging_bucket, existing_staging_bucket_name = (
        _auto_detect_staging_bucket(
            plan,
            include_staging_bucket=include_staging_bucket,
            s3_client=s3_client,
        )
    )

    # Resolve parameters
    if not vpc_id or not subnet_ids:
        vpc_id, subnet_ids = _resolve_default_vpc(ec2)
    if not container_image_uri:
        container_image_uri = "REPLACE_ME_WITH_REAL_IMAGE"

    template_body = json.dumps(build_template(
        plan,
        include_ecr=include_ecr,
        include_staging_bucket=include_staging_bucket,
    ))
    parameters = [
        {"ParameterKey": "VpcId", "ParameterValue": vpc_id},
        {"ParameterKey": "SubnetIds", "ParameterValue": ",".join(subnet_ids)},
        {"ParameterKey": "ContainerImageUri", "ParameterValue": container_image_uri},
    ]
    if not include_staging_bucket and existing_staging_bucket_name:
        parameters.append({
            "ParameterKey": "ExistingStagingBucketName",
            "ParameterValue": existing_staging_bucket_name,
        })

    if _stack_exists(cfn, stack_name):
        LOG.info("Updating existing stack %s", stack_name)
        try:
            cfn.update_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=parameters,
                Capabilities=["CAPABILITY_NAMED_IAM"],
            )
            waiter_name = "stack_update_complete"
        except ClientError as exc:
            if "No updates are to be performed" in str(exc):
                LOG.info("Stack already up to date.")
                outputs = _load_outputs(cfn, stack_name, plan)
                _fill_ecr_uri_from_image(outputs, container_image_uri)
                return outputs
            raise
    else:
        # _stack_exists() returns False for DELETE_COMPLETE / ROLLBACK_COMPLETE
        # / ROLLBACK_FAILED / CREATE_FAILED / DELETE_FAILED. DELETE_COMPLETE is
        # safe to create over directly; the rest still occupy the stack-name
        # slot, so create_stack() would hit AlreadyExistsException. Delete the
        # lingering stack first and wait for completion before creating.
        if _stack_in_unrecoverable_state(cfn, stack_name):
            LOG.info("Deleting lingering stack %s before recreating", stack_name)
            cfn.delete_stack(StackName=stack_name)
            cfn.get_waiter("stack_delete_complete").wait(
                StackName=stack_name,
                WaiterConfig={"Delay": 15, "MaxAttempts": 240},
            )
        LOG.info("Creating stack %s in %s", stack_name, plan.region)
        cfn.create_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Parameters=parameters,
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Tags=[
                {"Key": "Project", "Value": plan.model_spec.tag_value},
                # Tag policy: every AWS resource carries Model=<resource_prefix>
                # so cleanup automation can sweep by model. CFN propagates
                # stack-level tags to every resource it creates.
                {"Key": "Model", "Value": plan.model_spec.resource_prefix},
            ],
            OnFailure="DO_NOTHING",  # preserve on failure for debugging
        )
        waiter_name = "stack_create_complete"

    if not wait:
        outputs = _load_outputs(cfn, stack_name, plan, tolerant=True)
        _fill_ecr_uri_from_image(outputs, container_image_uri)
        return outputs

    LOG.info("Waiting for stack %s (timeout %ds)…", waiter_name, wait_timeout_s)
    waiter = cfn.get_waiter(waiter_name)
    try:
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={"Delay": 15, "MaxAttempts": wait_timeout_s // 15},
        )
    except WaiterError as exc:
        status = _stack_status(cfn, stack_name)
        events = _latest_events(cfn, stack_name, n=5)
        raise RuntimeError(
            f"Stack {stack_name} finished in {status}. Last events:\n"
            + "\n".join(events)
        ) from exc

    outputs = _load_outputs(cfn, stack_name, plan)

    # If the stack isn't managing the ECR resource (user-opt or
    # auto-detect), CFN has no EcrRepositoryUri output. Populate it from
    # the container image URI so downstream code (notebook section 3
    # build+push, build_and_push.sh) still has it.
    _fill_ecr_uri_from_image(outputs, container_image_uri)

    LOG.info("Stack ready: %s", outputs)
    return outputs


def teardown(
    stack_name: str,
    *,
    region: str,
    cfn_client: Any | None = None,
    wait: bool = True,
    wait_timeout_s: int = 1800,
) -> None:
    """Delete the stack. Retained resources (bucket, ECR) survive."""
    cfn = cfn_client or boto3.client("cloudformation", region_name=region)

    if not _stack_exists(cfn, stack_name):
        LOG.info("Stack %s does not exist.", stack_name)
        return

    LOG.info("Deleting stack %s …", stack_name)
    cfn.delete_stack(StackName=stack_name)

    if not wait:
        return

    waiter = cfn.get_waiter("stack_delete_complete")
    try:
        waiter.wait(
            StackName=stack_name,
            WaiterConfig={"Delay": 15, "MaxAttempts": wait_timeout_s // 15},
        )
    except WaiterError as exc:
        status = _stack_status(cfn, stack_name)
        raise RuntimeError(f"Stack {stack_name} delete finished in {status}") from exc
    LOG.info("Stack %s deleted.", stack_name)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _fill_ecr_uri_from_image(
    outputs: "StackOutputs", container_image_uri: str | None,
) -> None:
    """If outputs has no ecr_repository_uri (stack didn't manage the ECR),
    derive it from the container image URI by stripping the tag.

    Modifies ``outputs`` in-place. No-op when ``outputs`` already has a
    URI, ``container_image_uri`` is None, or the URI doesn't look like an
    ECR URI (e.g. placeholder ``REPLACE_ME_WITH_REAL_IMAGE``).
    """
    if outputs.ecr_repository_uri is not None:
        return
    if not container_image_uri or "dkr.ecr" not in container_image_uri:
        return
    outputs.ecr_repository_uri = container_image_uri.rsplit(":", 1)[0]


def _auto_detect_ecr(
    plan: BatchDeploymentPlan,
    *,
    include_ecr: bool,
    container_image_uri: str | None,
    ecr_client: Any | None = None,
) -> tuple[bool, str | None]:
    """If the model's ECR repo already exists, skip stack-managed ECR creation.

    Rationale: :data:`DeletionPolicy` on the ECR resource is ``Retain``,
    so pushed images survive ``teardown()``. But a retained resource
    cannot be re-created with the same name by a new ``create_stack``
    (CloudFormation returns ``AlreadyExists``). This helper detects the
    common "redeploy after teardown" case and transparently flips
    ``include_ecr`` to False, filling in ``container_image_uri`` from
    the existing repo's ``:latest`` tag when the caller didn't override.

    If the caller explicitly passed ``include_ecr=False``, this helper
    is a no-op — it honors the user's intent.

    Returns
    -------
    tuple[bool, str | None]
        ``(effective_include_ecr, effective_container_image_uri)``.
    """
    if not include_ecr:
        return include_ecr, container_image_uri

    repo_name = f"{plan.model_spec.resource_prefix}-batch"
    ecr = ecr_client or boto3.client("ecr", region_name=plan.region)
    try:
        resp = ecr.describe_repositories(repositoryNames=[repo_name])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "RepositoryNotFoundException":
            return include_ecr, container_image_uri
        LOG.debug(
            "ECR describe_repositories failed (%s); proceeding with stack-managed ECR",
            exc,
        )
        return include_ecr, container_image_uri
    except Exception as exc:  # noqa: BLE001
        LOG.debug(
            "ECR describe_repositories failed (%s); proceeding with stack-managed ECR",
            exc,
        )
        return include_ecr, container_image_uri

    repos = resp.get("repositories", [])
    if not repos:
        return include_ecr, container_image_uri
    repo_uri = repos[0].get("repositoryUri", "")
    LOG.warning(
        "ECR repo %r already exists (%s) — likely retained from a prior stack. "
        "Skipping stack-managed ECR creation and reusing the existing repo.",
        repo_name, repo_uri,
    )

    effective_uri = container_image_uri
    if not effective_uri and repo_uri:
        effective_uri = f"{repo_uri}:latest"
        LOG.info("Using existing image URI: %s", effective_uri)

    return False, effective_uri


def _auto_detect_staging_bucket(
    plan: BatchDeploymentPlan,
    *,
    include_staging_bucket: bool,
    s3_client: Any | None = None,
) -> tuple[bool, str | None]:
    """If the staging bucket already exists, skip stack-managed creation.

    The staging bucket carries ``DeletionPolicy: Retain`` (same shape as
    ECR — survives ``teardown()`` so users don't lose data accidentally).
    Same redeploy hazard: a second ``create_stack`` would hit
    ``BucketAlreadyExists`` because S3 names are globally unique. Detect
    the case and let the stack consume the existing bucket via a
    parameter (``ExistingStagingBucketName``) rather than create a new
    one.

    Returns
    -------
    tuple[bool, str | None]
        ``(effective_include_staging_bucket, existing_bucket_name_or_None)``.
    """
    if not include_staging_bucket:
        return include_staging_bucket, None

    bucket_name = (
        f"{plan.model_spec.resource_prefix}-batch-"
        f"{_account_id(plan)}-{plan.region}"
    )
    s3 = s3_client or boto3.client("s3", region_name=plan.region)
    try:
        s3.head_bucket(Bucket=bucket_name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket", "NotFound"):
            return include_staging_bucket, None
        # 403 means it exists but we can't access it — treat as "exists"
        # to avoid AlreadyExists during create_stack. The stack-internal
        # IAM role still has full access via tag/name conditions.
        if code == "403":
            LOG.warning(
                "Staging bucket %r exists but head_bucket returned 403; "
                "assuming the prior stack's bucket and consuming it.",
                bucket_name,
            )
            return False, bucket_name
        LOG.debug(
            "S3 head_bucket failed (%s); proceeding with stack-managed bucket",
            exc,
        )
        return include_staging_bucket, None
    except Exception as exc:  # noqa: BLE001
        LOG.debug(
            "S3 head_bucket failed (%s); proceeding with stack-managed bucket",
            exc,
        )
        return include_staging_bucket, None

    LOG.warning(
        "Staging bucket %r already exists — likely retained from a prior "
        "stack. Skipping stack-managed bucket creation and consuming it.",
        bucket_name,
    )
    return False, bucket_name


def _account_id(plan: BatchDeploymentPlan) -> str:
    """Resolve the current AWS account id for staging-bucket name lookup.

    Uses STS rather than baking the id into the plan, so the same plan
    factories work in any account without recompiling. Cached at the
    function level via a module-level dict to avoid repeated STS calls
    on the deployer's hot path.
    """
    cached = _ACCOUNT_ID_CACHE.get(plan.region)
    if cached:
        return cached
    sts = boto3.client("sts", region_name=plan.region)
    account_id = sts.get_caller_identity()["Account"]
    _ACCOUNT_ID_CACHE[plan.region] = account_id
    return account_id


_ACCOUNT_ID_CACHE: dict[str, str] = {}


def _describe_stack_status(cfn: Any, stack_name: str) -> str | None:
    """Return the current StackStatus, or None if the stack doesn't exist
    (or is missing a StackStatus key in test fixtures).
    """
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
    except ClientError as exc:
        if "does not exist" in str(exc):
            return None
        raise
    stacks = resp.get("Stacks", [])
    if not stacks:
        return None
    return stacks[0].get("StackStatus")


def _stack_exists(cfn: Any, stack_name: str) -> bool:
    """True iff a stack with this name is in an updateable state.

    CFN keeps DELETE_COMPLETE stacks queryable for ~90 days, and any
    failed-create state (ROLLBACK_COMPLETE / ROLLBACK_FAILED /
    CREATE_FAILED / DELETE_FAILED) leaves the stack in an inert state from
    which update_stack() is rejected. Treat all such states as
    "doesn't exist" so deploy() routes to create_stack(); the caller is
    responsible for deleting the lingering slot first via
    `_stack_in_unrecoverable_state` before the create.
    """
    status = _describe_stack_status(cfn, stack_name)
    if status is None:
        return False
    return status not in _NOT_UPDATABLE_STATES


def _stack_in_unrecoverable_state(cfn: Any, stack_name: str) -> bool:
    """True iff the stack still occupies its name slot but blocks both
    update_stack() and a fresh create_stack() (which would raise
    AlreadyExistsException). The caller must delete_stack and wait before
    creating again.
    """
    status = _describe_stack_status(cfn, stack_name)
    return status in _REQUIRES_DELETE_BEFORE_CREATE


def _stack_status(cfn: Any, stack_name: str) -> str:
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
        return resp["Stacks"][0]["StackStatus"]
    except (ClientError, IndexError):
        return "UNKNOWN"


def _latest_events(cfn: Any, stack_name: str, *, n: int = 5) -> list[str]:
    try:
        resp = cfn.describe_stack_events(StackName=stack_name)
    except ClientError:
        return []
    lines: list[str] = []
    for evt in resp.get("StackEvents", [])[:n]:
        ts = evt.get("Timestamp")
        rid = evt.get("LogicalResourceId", "?")
        status = evt.get("ResourceStatus", "?")
        reason = evt.get("ResourceStatusReason", "")
        lines.append(f"  {ts} {rid:<30} {status} {reason}")
    return lines


def _resolve_default_vpc(ec2: Any) -> tuple[str, list[str]]:
    """Find the default VPC and its subnets."""
    resp = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpcs = resp.get("Vpcs", [])
    if not vpcs:
        raise RuntimeError(
            "No default VPC. Pass vpc_id + subnet_ids explicitly."
        )
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}],
    )["Subnets"]
    subnet_ids = [s["SubnetId"] for s in subnets]
    if not subnet_ids:
        raise RuntimeError(f"Default VPC {vpc_id} has no subnets.")
    return vpc_id, subnet_ids


def _load_outputs(
    cfn: Any, stack_name: str, plan: BatchDeploymentPlan,
    *, tolerant: bool = False,
) -> StackOutputs:
    """Parse stack outputs into a StackOutputs object."""
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
    except ClientError:
        if tolerant:
            return StackOutputs(
                stack_name=stack_name, region=plan.region,
                staging_bucket="", job_definition_arn="",
                ecr_repository_uri=None, queue_arns_by_suffix={},
            )
        raise

    outputs_dict = {
        o["OutputKey"]: o["OutputValue"]
        for o in resp["Stacks"][0].get("Outputs", [])
    }

    # Build the queue_arns_by_suffix mapping
    queue_arns: dict[str, str] = {}
    for q in plan.queues:
        key = f"{_logical_id_for_queue(q)}Arn"
        if key in outputs_dict:
            queue_arns[q.name_suffix] = outputs_dict[key]

    return StackOutputs(
        stack_name=stack_name,
        region=plan.region,
        staging_bucket=outputs_dict.get("StagingBucketName", ""),
        job_definition_arn=outputs_dict.get("JobDefinitionArn", ""),
        ecr_repository_uri=outputs_dict.get("EcrRepositoryUri"),
        queue_arns_by_suffix=queue_arns,
        hf_token_secret_arn=outputs_dict.get("HfTokenSecretArn", ""),
    )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def main() -> int:
    """CLI: ``llm-batch-deploy deploy|teardown …``.

    Since we don't have a model registry (yet), the CLI takes a Python
    import path to a factory function that returns a BatchDeploymentPlan.
    """
    import argparse
    import importlib

    parser = argparse.ArgumentParser(prog="llm-batch-deploy")
    sub = parser.add_subparsers(dest="command", required=True)

    dp = sub.add_parser("deploy", help="Deploy a Batch stack.")
    dp.add_argument(
        "plan", help="Python import path to a plan factory, "
        "e.g. 'models.medgemma_27b:p4d_spot_single_queue'",
    )
    dp.add_argument("--stack-name")
    dp.add_argument("--vpc-id")
    dp.add_argument("--subnet-ids", nargs="+")
    dp.add_argument("--image-uri")
    dp.add_argument("--no-wait", action="store_true")
    dp.add_argument("-v", "--verbose", action="count", default=0)

    td = sub.add_parser("teardown", help="Delete a stack.")
    td.add_argument("stack_name")
    td.add_argument("--region", required=True)
    td.add_argument("--no-wait", action="store_true")
    td.add_argument("-v", "--verbose", action="count", default=0)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING - min(args.verbose, 2) * 10,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.command == "deploy":
        module_path, _, fn_name = args.plan.rpartition(":")
        if not module_path:
            parser.error("--plan must be 'module:function'")
        mod = importlib.import_module(module_path)
        plan = getattr(mod, fn_name)()

        outputs = deploy(
            plan,
            stack_name=args.stack_name,
            vpc_id=args.vpc_id,
            subnet_ids=args.subnet_ids,
            container_image_uri=args.image_uri,
            wait=not args.no_wait,
        )
        print(json.dumps({
            "stack_name": outputs.stack_name,
            "region": outputs.region,
            "staging_bucket": outputs.staging_bucket,
            "job_definition_arn": outputs.job_definition_arn,
            "ecr_repository_uri": outputs.ecr_repository_uri,
            "queue_arns": outputs.queue_arns_by_suffix,
        }, indent=2))
        return 0

    if args.command == "teardown":
        teardown(
            args.stack_name,
            region=args.region,
            wait=not args.no_wait,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["StackOutputs", "deploy", "teardown", "main"]
