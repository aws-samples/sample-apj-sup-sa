"""Step 3: the single AgentCore Memory resource shared by all tenants.

Namespace design (see config/tenants.py for the canonical helpers):
  - namespaceKeys: `tenantid` (allowedValues = the 3 tenant ids), `agentid`
    (allowedValues = every agent id + the literal "shared"), and
    `shopperid` (regexPattern -- shopper ids are open-ended, not a fixed
    small set, so this key uses a pattern instead of an allowlist).
  - One namespaceTemplate: "/tenant/{tenantid}/{agentid}/{shopperid}/"
      -> "/tenant/borealis/shared/dana/"   tenant-wide shared memory for shopper "dana"
      -> "/tenant/borealis/support/dana/"  order-support agent's private memory for "dana"
      -> "/tenant/borealis/merch/dana/"    shopping-assistant agent's private memory for "dana"

This is a deliberate simplification of the more obvious
"/tenant/{tenantid}/shared/" + "/tenant/{tenantid}/agent/{agentid}/" pattern:
collapsing "shared"/agent-id into one `agentid` namespaceKey keeps us at 3
of the 5 allowed keys (after adding per-shopper scoping) while producing
the same two logical kinds of namespace per tenant, now also partitioned
per shopper. Documented here and in the README.

IMPORTANT: namespaceKeys cannot be changed on an existing Memory resource
(confirmed: UpdateMemory does not accept namespaceKeys). Adding the
`shopperid` key therefore DELETES and RECREATES the Memory resource. This
is acceptable for this demo (synthetic data only, no real customer data,
and the IAM policies below are reattached to the new ARN automatically),
but would NOT be an acceptable operation against a Memory resource holding
real data -- namespace-shape changes need to be planned before go-live.

One semantic (long-term) strategy is defined via a `customMemoryStrategy`
with an explicit `semanticOverride` so extraction/consolidation use a known
model (Haiku, also the default chat model), rather than whatever the
service's default extraction model is.

This script also attaches the two IAM policies that depend on the Memory
ARN (which doesn't exist until after CreateMemory runs):
  1. memory-exec-role: BatchCreateMemoryRecords/BatchUpdateMemoryRecords/
     BatchDeleteMemoryRecords on this Memory resource -- used by AgentCore's
     own async extraction pipeline, not reachable by tenant callers.
  2. tenant-access-role: the per-tenant data-plane policy that is the core
     of the "defense in depth" isolation layer, scoped with
     bedrock-agentcore:namespace / actorId conditions using each tenant's
     literal id.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import MODEL_HAIKU, all_agent_ids, all_tenant_ids
from deploy.common import account_id, client, load_state, log, save_state, tags_map, wait_until

MEMORY_NAME = "agentcore_mt_demo_memory"
NAMESPACE_TEMPLATE = "/tenant/{tenantid}/{agentid}/{shopperid}/"
SHOPPER_ID_REGEX = r"^[a-z0-9][a-z0-9_-]{0,31}$"


def _create_memory(ctl, memory_exec_role_arn: str) -> str:
    tenant_values = all_tenant_ids()
    agent_values = all_agent_ids() + ["shared"]

    resp = ctl.create_memory(
        name=MEMORY_NAME,
        description="Multi-tenant retail SHOPPING ASSISTANT memory (demo). One resource, "
        "namespace-isolated per tenant, per agent, and per shopper.",
        eventExpiryDuration=90,
        memoryExecutionRoleArn=memory_exec_role_arn,
        namespaceKeys=[
            {"key": "tenantid", "validation": {"allowedValues": tenant_values}},
            {"key": "agentid", "validation": {"allowedValues": agent_values}},
            {"key": "shopperid", "validation": {"regexPattern": SHOPPER_ID_REGEX}},
        ],
        memoryStrategies=[
            {
                "customMemoryStrategy": {
                    "name": "tenant_semantic_memory",
                    "description": "Semantic long-term facts extracted per tenant/agent/shopper namespace.",
                    "namespaceTemplates": [NAMESPACE_TEMPLATE],
                    "configuration": {
                        "semanticOverride": {
                            "extraction": {
                                "modelId": MODEL_HAIKU,
                                "appendToPrompt": "This is a retail shopping assistant. Extract durable "
                                "shopper preferences (size, budget, style), order issues, and "
                                "product-interest notes as short factual statements. Never invent data.",
                            },
                            "consolidation": {
                                "modelId": MODEL_HAIKU,
                                "appendToPrompt": "Merge duplicate or overlapping shopper preference/order "
                                "facts into one concise statement. Keep tenant/agent/shopper context "
                                "implicit (namespace already scopes it); do not restate those ids.",
                            },
                        }
                    },
                }
            }
        ],
        tags=tags_map(),
    )
    memory_id = resp["memory"]["id"]
    log(f"created memory: {memory_id}")
    return memory_id


def ensure_memory(ctl, memory_exec_role_arn: str) -> str:
    state = load_state()
    memory_id = state.get("memory", {}).get("memory_id")
    existing_template = state.get("memory", {}).get("namespace_template")

    if memory_id and existing_template == NAMESPACE_TEMPLATE:
        try:
            resp = ctl.get_memory(memoryId=memory_id)
            log(f"memory exists with matching namespace template: {memory_id} status={resp['memory']['status']}")
            return memory_id
        except Exception:
            pass
    elif memory_id and existing_template and existing_template != NAMESPACE_TEMPLATE:
        # namespaceKeys/template changed (e.g. adding per-shopper scoping) --
        # namespaceKeys cannot be updated in place, so delete and recreate.
        log(f"namespace template changed ({existing_template!r} -> {NAMESPACE_TEMPLATE!r}); "
            f"deleting old memory {memory_id} and recreating")
        try:
            ctl.delete_memory(memoryId=memory_id)
            wait_until(
                lambda: _is_gone(ctl, memory_id), timeout_s=120, interval_s=5, what=f"memory {memory_id} deletion"
            )
        except Exception as exc:  # noqa: BLE001
            log(f"  (delete of old memory raised {exc}; proceeding to create new one anyway)")
        return _create_memory(ctl, memory_exec_role_arn)

    for page in ctl.get_paginator("list_memories").paginate():
        for m in page.get("memories", []):
            if m.get("name") == MEMORY_NAME:
                log(f"found existing memory by name: {m['id']}")
                return m["id"]

    return _create_memory(ctl, memory_exec_role_arn)


def _is_gone(ctl, memory_id: str) -> bool:
    try:
        m = ctl.get_memory(memoryId=memory_id)["memory"]
        return m["status"] == "DELETED"
    except Exception:
        return True


def wait_for_active(ctl, memory_id: str) -> dict:
    def check():
        m = ctl.get_memory(memoryId=memory_id)["memory"]
        log(f"  memory status={m['status']}")
        if m["status"] == "FAILED":
            raise RuntimeError(f"Memory creation FAILED: {m}")
        return m if m["status"] == "ACTIVE" else None

    return wait_until(check, timeout_s=300, interval_s=10, what=f"memory {memory_id} ACTIVE")


def attach_memory_exec_policy(iam, memory_arn: str) -> None:
    iam.put_role_policy(
        RoleName="agentcore-mt-memory-exec-role",
        PolicyName="memory-extraction-write",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "bedrock-agentcore:BatchCreateMemoryRecords",
                            "bedrock-agentcore:BatchUpdateMemoryRecords",
                            "bedrock-agentcore:BatchDeleteMemoryRecords",
                            "bedrock-agentcore:ListEvents",
                            "bedrock-agentcore:GetEvent",
                            "bedrock-agentcore:ListMemoryRecords",
                        ],
                        "Resource": memory_arn,
                    }
                ],
            }
        ),
    )
    log("attached memory-extraction-write policy -> agentcore-mt-memory-exec-role")


def attach_tenant_access_policy(iam, tenant_id: str, memory_arn: str) -> None:
    """Static, per-tenant isolation policy (see deploy/02_iam.py for why this
    is used instead of session-tag ABAC: sts:TagSession is commonly denied
    by AWS Organizations SCPs).

    Condition keys actually enforced, verified empirically in
    deploy/08_probe_iam_conditions.py (see docs/EVIDENCE.md for the full matrix):

      - `bedrock-agentcore:namespace` IS enforced for BatchCreateMemoryRecords
        (checked against each entry of the request's `namespaces` list),
        ListMemoryRecords and RetrieveMemoryRecords (checked against the
        request's `namespace` field).
      - `bedrock-agentcore:namespacePath` is NOT populated for any of the
        above (none of our calls use a `namespacePath` request field) --
        including it in the SAME Condition block as `namespace` makes the
        whole condition always-false (StringLike ANDs all listed keys, and a
        missing key never matches), which is a real bug we hit and fixed.
      - `bedrock-agentcore:actorId` IS enforced for CreateEvent and
        ListEvents (checked against the request's `actorId` field).
      - GetMemoryRecord and DeleteMemoryRecord take only a `memoryRecordId`;
        neither `namespace` nor `actorId` is populated as a condition key
        for these by-ID actions (confirmed: even a same-tenant namespace
        condition denied a legitimate own-record GetMemoryRecord call). IAM
        CANNOT be used to scope these two actions by tenant in this service
        version. We grant them unconditioned (resource-scoped to this one
        Memory ARN only) and the tools Lambda's `tool_forget` instead does
        an application-layer ownership check (GetMemoryRecord first, verify
        the record's namespaces all start with this tenant's prefix, only
        then DeleteMemoryRecord). This is a documented limitation, not an
        assumption -- see README "Limitations".
      - ListActors takes no actorId/namespace request field either and is
        therefore not grantable in a tenant-scoped way; it is deliberately
        left out of this policy and out of the tool surface entirely.
    """
    role_name = f"agentcore-mt-tenant-access-{tenant_id}"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "NamespaceScopedDataPlane",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:BatchCreateMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                ],
                "Resource": memory_arn,
                "Condition": {"StringLike": {"bedrock-agentcore:namespace": f"/tenant/{tenant_id}/*"}},
            },
            {
                "Sid": "ActorScopedEvents",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents"],
                "Resource": memory_arn,
                "Condition": {"StringLike": {"bedrock-agentcore:actorId": f"tenant-{tenant_id}-*"}},
            },
            {
                "Sid": "ByIdRecordOpsNotIamScopable",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetMemoryRecord", "bedrock-agentcore:DeleteMemoryRecord"],
                "Resource": memory_arn,
            },
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="tenant-scoped-memory",
        PolicyDocument=json.dumps(policy),
    )
    log(f"attached tenant-scoped-memory policy -> {role_name}")


def main():
    ctl = client("bedrock-agentcore-control")
    iam = client("iam")
    state = load_state()
    memory_exec_role_arn = state["iam"]["memory_exec_role_arn"]

    memory_id = ensure_memory(ctl, memory_exec_role_arn)
    memory = wait_for_active(ctl, memory_id)
    memory_arn = memory["arn"]

    attach_memory_exec_policy(iam, memory_arn)
    for tenant_id in all_tenant_ids():
        attach_tenant_access_policy(iam, tenant_id, memory_arn)

    state["memory"] = {
        "memory_id": memory_id,
        "memory_arn": memory_arn,
        "namespace_template": NAMESPACE_TEMPLATE,
    }
    save_state(state)
    log("Memory setup complete.")
    log(f"  memory_id={memory_id}")
    log(f"  memory_arn={memory_arn}")


if __name__ == "__main__":
    main()
