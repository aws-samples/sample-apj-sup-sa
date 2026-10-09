"""CloudFormation custom resource: an Omni space plus an admin access grant.

CloudFormation has no AWS::CloudWatchOmni::* resource types yet, so the CDK
Provider framework invokes this handler. It vendors a hash-pinned boto3 1.43
bundle because the Lambda runtime's built-in boto3 predates the cloudwatchomni
service model.

Properties (all strings):
  DomainId                    domain ID, name, or ARN (account- or org-scoped)
  SpaceName
  DataAccessRoleArn           the space access role
  AgentCoreEvaluationRoleArn  optional
  AdminGroupId                optional Identity Center group ID -> SPACE_ADMIN (recommended for people)
  ViewerGroupId               optional Identity Center group ID -> READ (Viewer)
  AdminPrincipalArn           optional IAM role/user ARN -> SPACE_ADMIN (break-glass / automation)
  AdoptExistingSpace          "true" to manage a space that already exists in this Region
  RetainOnDelete              "true" (default) keeps the space when the stack is deleted
"""

import logging
import os
import re
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

REGION = os.environ["AWS_REGION"]
omni = boto3.client("cloudwatchomni", region_name=REGION)
cloudformation = boto3.client("cloudformation", region_name=REGION)
GRANT_NAME = "omni-samples-admin"
ACTIVE_SPACE_STATUS = "ACTIVE"
TRANSITIONAL_SPACE_STATUSES = {"MOVING"}


def on_event(event, _context):
    log.info("request %s %s", event["RequestType"], {k: v for k, v in event.items() if k != "ResponseURL"})
    props = event["ResourceProperties"]
    if event["RequestType"] == "Create":
        return create(props)
    if event["RequestType"] == "Update":
        return update(event["PhysicalResourceId"], props, event.get("OldResourceProperties", {}))
    return delete(event["PhysicalResourceId"], props, event.get("StackId"))


# --- domain -----------------------------------------------------------------

def resolve_domain(ref):
    """Return (domain_id, endpoint_url, domain_arn) for a domain ID, name, or ARN."""
    for page in omni.get_paginator("list_domains").paginate():
        for d in page["items"]:
            if ref in (d["domainId"], d["name"], d["domainArn"]):
                return d["domainId"], endpoint_url(d), d["domainArn"]
    raise RuntimeError(f"Domain {ref!r} not found. List yours with: aws cloudwatchomni list-domains")


def endpoint_url(summary):
    """Best-effort sign-in URL. It's informational, so a read failure must not fail the space."""
    try:
        # Account domains are read with GetDomain. Organization domains are read with
        # GetDomainForOrganization, which only the management account may call; member
        # accounts get AccessDenied and fall back to the documented URL format.
        if ":organization-domain/" in summary["domainArn"]:
            d = omni.get_domain_for_organization(domainId=summary["domainId"])["organizationDomain"]
        else:
            d = omni.get_domain(domainId=summary["domainId"])["domain"]
        return (d.get("customEndpointUrls") or [d.get("domainEndpointUrl", "")])[0]
    except (BotoCoreError, ClientError) as exc:
        error_code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        log.info("domain read failed (%s); using the documented URL format", error_code)
        return f"https://{summary['name']}.cloudwatch-omni.global.app.aws"


# --- space ------------------------------------------------------------------

def spaces_in_region():
    found = []
    for page in omni.get_paginator("list_spaces").paginate():
        found += [s for s in page["items"] if s["region"] == REGION]
    return found


def wait_active(space_id, timeout=480):
    deadline = time.time() + timeout
    while True:
        space = omni.get_space(spaceId=space_id)["space"]
        status = space["status"]
        if status == ACTIVE_SPACE_STATUS:
            return space
        if status not in TRANSITIONAL_SPACE_STATUSES:
            raise RuntimeError(f"Space {space_id} is {status}: {space.get('statusReason', '')}")
        if time.time() > deadline:
            raise RuntimeError(f"Space {space_id} is {status}: {space.get('statusReason', '')}")
        time.sleep(10)


def wait_deleted(space_id, timeout=60):
    deadline = time.time() + timeout
    while True:
        try:
            omni.get_space(spaceId=space_id)
        except omni.exceptions.ResourceNotFoundException:
            return
        if time.time() > deadline:
            raise RuntimeError(f"Timed out waiting for cleanup of space {space_id}")
        time.sleep(5)


def rollback_created_space(space_id):
    """Best-effort cleanup for a space created by a failed Create invocation."""
    try:
        omni.delete_space(spaceId=space_id)
        wait_deleted(space_id)
        log.info("rolled back newly created space %s", space_id)
    except omni.exceptions.ResourceNotFoundException:
        log.info("newly created space %s was already deleted", space_id)
    except Exception:
        # Cleanup is best-effort and must never replace the failure that triggered it.
        log.exception("failed to roll back newly created space %s", space_id)


def create(props):
    domain_id, url, domain_arn = resolve_domain(props["DomainId"])
    existing = spaces_in_region()
    adopted = False
    created = False
    if existing:
        space = existing[0]
        if props.get("AdoptExistingSpace") != "true":
            raise RuntimeError(
                f"A space already exists in {REGION}: {space['name']} ({space['spaceId']}). Omni allows one "
                "space per account per Region. Re-deploy with -c adoptExistingSpace=true to use it."
            )
        log.info("adopting existing space %s", space["spaceId"])
        if space["domainArn"] != domain_arn:
            raise RuntimeError(
                f"Space {space['spaceId']} belongs to {space['domainArn']}, not configured domain {domain_arn}."
            )
        adopted = True
        space_id = space["spaceId"]
    else:
        request = {
            "name": props["SpaceName"],
            "domainId": domain_id,
            "dataAccessRoleArn": props["DataAccessRoleArn"],
            "tags": {"project": "omni-samples"},
        }
        if props.get("AgentCoreEvaluationRoleArn"):
            request["agentCoreEvaluationRoleArn"] = props["AgentCoreEvaluationRoleArn"]
        try:
            space_id = omni.create_space(**request)["space"]["spaceId"]
        except ClientError as exc:
            if "management account" in str(exc):
                raise RuntimeError(
                    f"{exc}. Domain {props['DomainId']!r} is an organization domain, and spaces under it "
                    "live in member accounts. Deploy this stack with credentials for a member account."
                ) from exc
            raise
        except BotoCoreError:
            log.exception(
                "create_space transport failure; the request may have succeeded. "
                "Check aws cloudwatchomni list-spaces before retrying."
            )
            raise
        log.info("created space %s", space_id)
        created = True

    # create-space never tries to assume the role, so a wrong trust policy shows up
    # only when the space is used. Reading it back at least confirms it reached ACTIVE.
    try:
        space = wait_active(space_id)
        grant_ids = ensure_grants(domain_id, space_id, props)
    except Exception:
        if created:
            rollback_created_space(space_id)
        raise
    return response(space, url, adopted, grant_ids)


def update(space_id, props, old):
    if old.get("AdoptExistingSpace") == "true" and props.get("AdoptExistingSpace") != "true":
        raise RuntimeError("Cannot un-adopt a space; retain and remove the stack before managing it separately.")
    if (props["DomainId"] != old.get("DomainId")
            or props["DataAccessRoleArn"] != old.get("DataAccessRoleArn")
            or props.get("AgentCoreEvaluationRoleArn") != old.get("AgentCoreEvaluationRoleArn")):
        raise RuntimeError(
            "Changing the domain, space access role, or AgentCore evaluation role needs a new space. "
            "Delete the space first."
        )
    domain_id, url, _ = resolve_domain(props["DomainId"])
    if props["SpaceName"] != old.get("SpaceName") and old.get("AdoptExistingSpace") != "true":
        omni.update_space(spaceId=space_id, name=props["SpaceName"])
    space = wait_active(space_id)
    grant_ids = ensure_grants(domain_id, space_id, props)
    remove_stale_grants(space_id, old, props)
    return response(space, url, old.get("AdoptExistingSpace") == "true", grant_ids)


def is_create_rollback(stack_id):
    """Return whether CloudFormation is deleting this resource during create rollback."""
    if not stack_id:
        return False
    try:
        status = cloudformation.describe_stacks(StackName=stack_id)["Stacks"][0]["StackStatus"]
    except (BotoCoreError, ClientError):
        log.exception("could not determine stack status for %s; preserving the space", stack_id)
        return False
    return status == "ROLLBACK_IN_PROGRESS"


def delete(space_id, props, stack_id=None):
    adopted = props.get("AdoptExistingSpace") == "true"
    retained = props.get("RetainOnDelete", "true") == "true"
    if adopted or (retained and not is_create_rollback(stack_id)):
        log.info("retaining space %s (RetainOnDelete or adopted)", space_id)
        return {"PhysicalResourceId": space_id}
    try:
        omni.delete_space(spaceId=space_id)  # destroys the space and its telemetry
        log.info("deleted space %s", space_id)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    return {"PhysicalResourceId": space_id}


# --- access grant -------------------------------------------------------------

def to_principal(arn):
    """Map an IAM or sts assumed-role ARN to an Omni grant principal."""
    m = re.match(r"arn:(aws[\w-]*):sts::(\d{12}):assumed-role/([^/]+)/", arn)
    if m:  # assumed-role sessions don't carry the role path; this assumes the role has none
        arn = f"arn:{m[1]}:iam::{m[2]}:role/{m[3]}"
    if ":role/" in arn:
        return {"principalType": "IAM_ROLE", "principalId": arn}
    if ":user/" in arn:
        return {"principalType": "IAM_USER", "principalId": arn}
    if arn.endswith(":root"):
        return {"principalType": "IAM_ROOT", "principalId": arn}
    raise RuntimeError(f"AdminPrincipalArn must be an IAM role, user, or root ARN, got {arn!r}")


def configured_grants(props):
    """Return grants managed by the custom resource for these properties."""
    wanted = []
    if props.get("AdminGroupId"):
        wanted.append(("omni-space-admins", {"principalType": "IDC_GROUP", "principalId": props["AdminGroupId"]}, "SPACE_ADMIN"))
    if props.get("ViewerGroupId"):
        wanted.append(("omni-viewers", {"principalType": "IDC_GROUP", "principalId": props["ViewerGroupId"]}, "READ"))
    if props.get("AdminPrincipalArn"):
        wanted.append((GRANT_NAME, to_principal(props["AdminPrincipalArn"]), "SPACE_ADMIN"))
    return wanted


def ensure_grants(domain_id, space_id, props):
    """Grant configured principals and undo partial changes on failure."""
    grant_ids = []
    created_grant_ids = []
    try:
        for name, principal, permission in configured_grants(props):
            grant_id, created = ensure_grant(domain_id, space_id, name, principal, permission)
            grant_ids.append(grant_id)
            if created:
                created_grant_ids.append(grant_id)
    except Exception:
        for grant_id in reversed(created_grant_ids):
            try:
                omni.delete_access_grant(grantId=grant_id)
                log.info("rolled back newly created access grant %s", grant_id)
            except Exception:
                log.exception("failed to roll back newly created access grant %s", grant_id)
        raise
    return ",".join(grant_ids)


def remove_stale_grants(space_id, old_props, new_props):
    """Revoke grants for principals removed or replaced during an update."""
    new_keys = {(principal["principalType"], principal["principalId"], permission)
                for _, principal, permission in configured_grants(new_props)}
    for name, principal, permission in configured_grants(old_props):
        key = (principal["principalType"], principal["principalId"], permission)
        if key in new_keys:
            continue
        for page in omni.get_paginator("list_access_grants").paginate(
            spaceId=space_id, principalType=principal["principalType"],
            principalId=principal["principalId"], permission=permission,
        ):
            for grant in page["items"]:
                if grant.get("name") != name:
                    continue
                omni.delete_access_grant(grantId=grant["grantId"])
                log.info("revoked stale %s grant from %s %s (%s)", permission,
                         principal["principalType"], principal["principalId"], grant["grantId"])


def ensure_grant(domain_id, space_id, name, principal, permission):
    for page in omni.get_paginator("list_access_grants").paginate(
        spaceId=space_id, principalType=principal["principalType"], principalId=principal["principalId"]
    ):
        for grant in page["items"]:
            if grant["permission"] == permission:
                return grant["grantId"], False
    grant = omni.create_access_grant(
        domainId=domain_id, spaceId=space_id, name=name, principal=principal,
        permission=permission, tags={"project": "omni-samples"},
    )["accessGrant"]
    log.info("granted %s to %s %s (%s)", permission, principal["principalType"], principal["principalId"], grant["grantId"])
    return grant["grantId"], True


def response(space, url, adopted, grant_ids):
    return {
        "PhysicalResourceId": space["spaceId"],
        "Data": {
            "SpaceId": space["spaceId"],
            "SpaceArn": space["spaceArn"],
            "DomainEndpointUrl": url,
            "Adopted": "true" if adopted else "false",
            "GrantIds": grant_ids,
        },
    }
