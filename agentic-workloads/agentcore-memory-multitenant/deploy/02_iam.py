"""Step 2: IAM roles (trust policies + the permissions that don't depend on
resources created later). Memory-resource-scoped policies (which need the
Memory ARN) are attached in deploy/03_memory.py; the gateway role's
Lambda-invoke policy (which needs specific function ARNs) is attached in
deploy/05_lambdas.py. Both are least-privilege: no wildcard Resource for
lambda:InvokeFunction, and the tenant-access roles' data-plane policies are
scoped by the tenant's literal id (one static role per tenant -- see
docs/EVIDENCE.md section 1 for why this is used instead of session-tag
ABAC).

Roles created here:
  - agentcore-mt-gateway-role        (trust: bedrock-agentcore.amazonaws.com)  -> CreateGateway roleArn
  - agentcore-mt-memory-exec-role    (trust: bedrock-agentcore.amazonaws.com)  -> CreateMemory memoryExecutionRoleArn
  - agentcore-mt-interceptor-role    (trust: lambda.amazonaws.com)             -> interceptor Lambda execution role
  - agentcore-mt-tools-role          (trust: lambda.amazonaws.com)             -> tools Lambda execution role
  - agentcore-mt-tenant-access-<id>  (trust: agentcore-mt-tools-role)          -> one static role PER TENANT, assumed per-call
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import MODEL_HAIKU, REGION, all_tenant_ids
from deploy.common import account_id, caller_principal_arn, client, load_state, log, save_state


def iam_tags_list() -> list[dict]:
    from deploy.common import tags_map

    return [{"Key": k, "Value": v} for k, v in tags_map().items()]


GATEWAY_ROLE = "agentcore-mt-gateway-role"
MEMORY_EXEC_ROLE = "agentcore-mt-memory-exec-role"
INTERCEPTOR_ROLE = "agentcore-mt-interceptor-role"
TOOLS_ROLE = "agentcore-mt-tools-role"
TENANT_ACCESS_ROLE_PREFIX = "agentcore-mt-tenant-access"


def create_role_with_retry(iam, name, trust_policy, description):
    last_exc = None
    for attempt in range(6):
        try:
            resp = iam.create_role(
                RoleName=name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description=description,
                Tags=iam_tags_list(),
            )
            return resp["Role"]["Arn"]
        except iam.exceptions.MalformedPolicyDocumentException as exc:
            # IAM eventual consistency: a just-created role referenced as a trust-policy
            # Principal can briefly 404 inside policy validation. Retry with backoff.
            last_exc = exc
            log(f"  role principal not yet propagated, retrying ({attempt + 1}/6)...")
            time.sleep(5 * (attempt + 1))
    raise last_exc


def ensure_role(iam, name: str, trust_policy: dict, description: str) -> str:
    try:
        resp = iam.get_role(RoleName=name)
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust_policy))
        log(f"role exists, trust policy refreshed: {name}")
        return resp["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        arn = create_role_with_retry(iam, name, trust_policy, description)
        log(f"created role: {name}")
        # IAM has eventual consistency; give it a moment before attaching policies / using in other API calls.
        time.sleep(5)
        return arn


def put_inline_policy(iam, role_name: str, policy_name: str, policy_doc: dict) -> None:
    iam.put_role_policy(RoleName=role_name, PolicyName=policy_name, PolicyDocument=json.dumps(policy_doc))
    log(f"  attached inline policy {policy_name} -> {role_name}")


def main():
    iam = client("iam")
    acct = account_id()
    region = REGION

    # --- Gateway execution role ---
    gateway_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": acct},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{acct}:gateway/*"},
                },
            }
        ],
    }
    gateway_role_arn = ensure_role(iam, GATEWAY_ROLE, gateway_trust, "AgentCore Gateway execution role (MT memory demo)")

    # --- Memory execution role (used by the AgentCore-managed async extraction pipeline) ---
    memory_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": acct},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{acct}:memory/*"},
                },
            }
        ],
    }
    memory_exec_role_arn = ensure_role(
        iam, MEMORY_EXEC_ROLE, memory_trust, "AgentCore Memory extraction execution role (MT memory demo)"
    )
    # Permission to invoke the extraction model. Scoped to the one model used for
    # the semantic strategy's extraction/consolidation (cross-region inference
    # profile, which fans out to several underlying foundation-model ARNs).
    put_inline_policy(
        iam,
        MEMORY_EXEC_ROLE,
        "invoke-extraction-model",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    "Resource": [
                        f"arn:aws:bedrock:{region}:{acct}:inference-profile/{MODEL_HAIKU}",
                        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                    ],
                }
            ],
        },
    )

    # --- Interceptor Lambda execution role: logs only, no AWS data-plane calls. ---
    lambda_trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
    }
    interceptor_role_arn = ensure_role(iam, INTERCEPTOR_ROLE, lambda_trust, "Interceptor Lambda role (MT memory demo)")
    put_inline_policy(
        iam,
        INTERCEPTOR_ROLE,
        "logs",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                    "Resource": f"arn:aws:logs:{region}:{acct}:log-group:/aws/lambda/agentcore-mt-*:*",
                }
            ],
        },
    )

    # --- Tools Lambda execution role: logs + ability to assume the tenant-access role. ---
    tools_role_arn = ensure_role(iam, TOOLS_ROLE, lambda_trust, "Tools Lambda role (MT memory demo)")
    put_inline_policy(
        iam,
        TOOLS_ROLE,
        "logs",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                    "Resource": f"arn:aws:logs:{region}:{acct}:log-group:/aws/lambda/agentcore-mt-*:*",
                }
            ],
        },
    )

    # --- Tenant-access roles: ONE PER TENANT, each assumed by the tools Lambda.
    #
    # The common alternative is a single shared role assumed with a
    # session tag `tenant=<id>`, enforced via
    # Condition:{StringLike:{"bedrock-agentcore:namespace":"/tenant/${aws:PrincipalTag/tenant}/*"}}.
    # That needs `sts:TagSession`, which AWS Organizations SCPs commonly deny
    # (see docs/EVIDENCE.md), so this sample does not depend on it.
    #
    # DESIGN: one static IAM role per tenant
    # (agentcore-mt-tenant-access-<tenant_id>), each with its own permission
    # policy whose namespace/namespacePath/actorId conditions use that
    # tenant's *literal* id (attached in deploy/03_memory.py). The tools
    # Lambda looks up which role ARN to assume from a fixed,
    # interceptor-derived tenant_id -> role_arn map (TENANT_ROLE_ARNS_JSON
    # env var) -- the Lambda can never construct an arbitrary role ARN from
    # caller input. This keeps the same defense-in-depth property (IAM
    # blocks cross-tenant namespace access even if application code has a
    # bug) without relying on session tagging.
    valid_tenants = all_tenant_ids()
    deployer_arn = caller_principal_arn()
    tenant_access_role_arns: dict[str, str] = {}
    for tenant_id in valid_tenants:
        role_name = f"{TENANT_ACCESS_ROLE_PREFIX}-{tenant_id}"
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    # The deploying principal is also trusted here so the IAM-layer
                    # tests can assume each tenant's role directly, bypassing the
                    # Lambda, to prove the IAM policy alone blocks cross-tenant
                    # access (tests/test_isolation.py, tests/test_kb_isolation.py).
                    "Principal": {"AWS": [tools_role_arn, deployer_arn]},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        arn = ensure_role(iam, role_name, trust, f"Static per-tenant access role for tenant={tenant_id} (MT memory demo)")
        tenant_access_role_arns[tenant_id] = arn

    # The tools Lambda role needs permission to call AssumeRole on exactly
    # these role ARNs (not a wildcard).
    put_inline_policy(
        iam,
        TOOLS_ROLE,
        "assume-tenant-access-roles",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Resource": list(tenant_access_role_arns.values()),
                }
            ],
        },
    )

    state = load_state()
    state["iam"] = {
        "gateway_role_arn": gateway_role_arn,
        "memory_exec_role_arn": memory_exec_role_arn,
        "interceptor_role_arn": interceptor_role_arn,
        "tools_role_arn": tools_role_arn,
        "tenant_access_role_arns": tenant_access_role_arns,
    }
    save_state(state)
    log("IAM setup complete.")
    for k, v in state["iam"].items():
        log(f"  {k} = {v}")


if __name__ == "__main__":
    main()
