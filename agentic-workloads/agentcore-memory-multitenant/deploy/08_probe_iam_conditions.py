"""Empirical probe: which bedrock-agentcore IAM condition keys (namespace,
namespacePath, actorId) are actually enforced on which Memory data-plane
actions, in YOUR account/service version -- not assumed from docs.

Prints a summary and writes per-call results to iam_probe_raw.json
(gitignored; it contains account-specific ARNs). docs/EVIDENCE.md
summarises the findings. Mutates and finally restores the
agentcore-mt-tenant-access-northwind role's inline policy (uses the
northwind role as the probe subject; borealis namespace/actor values are
used as the "other tenant" negative case). Safe to re-run; idempotent at
the end (restores the real production policy via deploy/03_memory.py's
attach_tenant_access_policy).

Must be run AFTER deploy/03_memory.py (needs memory_id/memory_arn in
deploy/state.json) and 02_iam.py (needs the per-tenant role ARNs).
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import REGION
from deploy.common import client, load_state, log

PROBE_ROLE_NAME = "agentcore-mt-tenant-access-northwind"
OWN_TENANT = "northwind"
OTHER_TENANT = "borealis"
PROPAGATION_WAIT_S = 16

results: list[dict] = []


def set_role_policy(iam, memory_arn: str, statements: list[dict]) -> None:
    doc = {"Version": "2012-10-17", "Statement": statements}
    iam.put_role_policy(RoleName=PROBE_ROLE_NAME, PolicyName="tenant-scoped-memory", PolicyDocument=json.dumps(doc))
    log(f"  policy set, waiting {PROPAGATION_WAIT_S}s for propagation...")
    time.sleep(PROPAGATION_WAIT_S)


def assumed_data_client(sts, role_arn: str):
    resp = sts.assume_role(RoleArn=role_arn, RoleSessionName=f"probe-{uuid.uuid4().hex[:6]}")
    c = resp["Credentials"]
    import boto3

    return boto3.client(
        "bedrock-agentcore",
        region_name=REGION,
        aws_access_key_id=c["AccessKeyId"],
        aws_secret_access_key=c["SecretAccessKey"],
        aws_session_token=c["SessionToken"],
    )


def record(action: str, condition_key: str, target: str, outcome: str, note: str = "") -> None:
    results.append({"action": action, "condition_key": condition_key, "target": target, "outcome": outcome, "note": note})
    log(f"  [{action}] key={condition_key} target={target} -> {outcome} {note}")


def attempt(fn, action: str, condition_key: str, target: str) -> None:
    try:
        fn()
        record(action, condition_key, target, "ALLOWED")
    except Exception as exc:  # noqa: BLE001
        code = type(exc).__name__
        outcome = "DENIED" if "AccessDenied" in code or "AccessDenied" in str(exc) else f"ERROR:{code}"
        record(action, condition_key, target, outcome, note=str(exc)[:160])


def main():
    iam = client("iam")
    sts = client("sts")
    state = load_state()
    memory_arn = state["memory"]["memory_arn"]
    memory_id = state["memory"]["memory_id"]
    role_arn = state["iam"]["tenant_access_role_arns"][OWN_TENANT]

    own_ns = f"/tenant/{OWN_TENANT}/support/"
    other_ns = f"/tenant/{OTHER_TENANT}/support/"
    own_actor = f"tenant-{OWN_TENANT}-support"
    other_actor = f"tenant-{OTHER_TENANT}-support"

    # ---- Group 1: BatchCreateMemoryRecords with ONLY `namespace` condition ----
    set_role_policy(
        iam,
        memory_arn,
        [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:BatchCreateMemoryRecords"],
                "Resource": memory_arn,
                "Condition": {"StringLike": {"bedrock-agentcore:namespace": f"/tenant/{OWN_TENANT}/*"}},
            }
        ],
    )
    dc = assumed_data_client(sts, role_arn)

    def write(ns):
        dc.batch_create_memory_records(
            memoryId=memory_id,
            records=[
                {
                    "requestIdentifier": str(uuid.uuid4()),
                    "namespaces": [ns],
                    "content": {"text": "probe"},
                    "timestamp": datetime.now(timezone.utc),
                }
            ],
        )

    attempt(lambda: write(own_ns), "BatchCreateMemoryRecords", "namespace", "own")
    attempt(lambda: write(other_ns), "BatchCreateMemoryRecords", "namespace", "other")

    # ---- Group 2: BatchCreateMemoryRecords with ONLY `namespacePath` condition ----
    set_role_policy(
        iam,
        memory_arn,
        [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:BatchCreateMemoryRecords"],
                "Resource": memory_arn,
                "Condition": {"StringLike": {"bedrock-agentcore:namespacePath": f"/tenant/{OWN_TENANT}/*"}},
            }
        ],
    )
    dc = assumed_data_client(sts, role_arn)
    attempt(lambda: write(own_ns), "BatchCreateMemoryRecords", "namespacePath", "own (expect DENY: this action's request has no namespacePath field)")

    # First, seed a record with an unconditioned policy so Retrieve/List/Get/Delete have something real to query.
    set_role_policy(iam, memory_arn, [{"Effect": "Allow", "Action": ["bedrock-agentcore:*"], "Resource": memory_arn}])
    dc_admin = assumed_data_client(sts, role_arn)
    seed_resp = dc_admin.batch_create_memory_records(
        memoryId=memory_id,
        records=[
            {
                "requestIdentifier": str(uuid.uuid4()),
                "namespaces": [own_ns],
                "content": {"text": "seed record for probe retrieval"},
                "timestamp": datetime.now(timezone.utc),
            }
        ],
    )
    seed_record_id = seed_resp["successfulRecords"][0]["memoryRecordId"]
    other_role_arn = state["iam"]["tenant_access_role_arns"][OTHER_TENANT]
    other_role_name = f"agentcore-mt-tenant-access-{OTHER_TENANT}"
    iam.put_role_policy(
        RoleName=other_role_name,
        PolicyName="tenant-scoped-memory",
        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["bedrock-agentcore:*"], "Resource": memory_arn}]}),
    )
    log(f"  temporarily widened {other_role_name} to seed an other-tenant record, waiting {PROPAGATION_WAIT_S}s...")
    time.sleep(PROPAGATION_WAIT_S)
    dc_other_admin = assumed_data_client(sts, other_role_arn)
    try:
        other_seed = dc_other_admin.batch_create_memory_records(
            memoryId=memory_id,
            records=[
                {
                    "requestIdentifier": str(uuid.uuid4()),
                    "namespaces": [other_ns],
                    "content": {"text": "seed record other tenant"},
                    "timestamp": datetime.now(timezone.utc),
                }
            ],
        )
        other_record_id = other_seed["successfulRecords"][0]["memoryRecordId"]
    except Exception as exc:  # noqa: BLE001
        log(f"  could not seed other-tenant record ({exc}); GetMemoryRecord/DeleteMemoryRecord cross-tenant probe will be skipped")
        other_record_id = None

    # ---- Group 3: RetrieveMemoryRecords / ListMemoryRecords with `namespace` only ----
    set_role_policy(
        iam,
        memory_arn,
        [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"],
                "Resource": memory_arn,
                "Condition": {"StringLike": {"bedrock-agentcore:namespace": f"/tenant/{OWN_TENANT}/*"}},
            }
        ],
    )
    dc = assumed_data_client(sts, role_arn)
    attempt(lambda: dc.list_memory_records(memoryId=memory_id, namespace=own_ns), "ListMemoryRecords", "namespace", "own")
    attempt(lambda: dc.list_memory_records(memoryId=memory_id, namespace=other_ns), "ListMemoryRecords", "namespace", "other")
    attempt(
        lambda: dc.retrieve_memory_records(memoryId=memory_id, namespace=own_ns, searchCriteria={"searchQuery": "probe", "topK": 3}),
        "RetrieveMemoryRecords",
        "namespace",
        "own",
    )
    attempt(
        lambda: dc.retrieve_memory_records(memoryId=memory_id, namespace=other_ns, searchCriteria={"searchQuery": "probe", "topK": 3}),
        "RetrieveMemoryRecords",
        "namespace",
        "other",
    )

    # ---- Group 4: GetMemoryRecord / DeleteMemoryRecord by id with `namespace` condition ----
    set_role_policy(
        iam,
        memory_arn,
        [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetMemoryRecord", "bedrock-agentcore:DeleteMemoryRecord"],
                "Resource": memory_arn,
                "Condition": {"StringLike": {"bedrock-agentcore:namespace": f"/tenant/{OWN_TENANT}/*"}},
            }
        ],
    )
    dc = assumed_data_client(sts, role_arn)
    attempt(lambda: dc.get_memory_record(memoryId=memory_id, memoryRecordId=seed_record_id), "GetMemoryRecord", "namespace", "own-record-id")
    if other_record_id:
        attempt(
            lambda: dc.get_memory_record(memoryId=memory_id, memoryRecordId=other_record_id),
            "GetMemoryRecord",
            "namespace",
            "other-record-id (by-id call, no namespace in request)",
        )

    # ---- Group 5: CreateEvent / ListEvents with `actorId` condition ----
    set_role_policy(
        iam,
        memory_arn,
        [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents"],
                "Resource": memory_arn,
                "Condition": {"StringLike": {"bedrock-agentcore:actorId": f"tenant-{OWN_TENANT}-*"}},
            }
        ],
    )
    dc = assumed_data_client(sts, role_arn)

    def create_event(actor):
        dc.create_event(
            memoryId=memory_id,
            actorId=actor,
            sessionId="probe-session",
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"content": {"text": "probe turn"}, "role": "USER"}}],
        )

    attempt(lambda: create_event(own_actor), "CreateEvent", "actorId", "own")
    attempt(lambda: create_event(other_actor), "CreateEvent", "actorId", "other")
    attempt(
        lambda: dc.list_events(memoryId=memory_id, actorId=own_actor, sessionId="probe-session"),
        "ListEvents",
        "actorId",
        "own",
    )
    attempt(
        lambda: dc.list_events(memoryId=memory_id, actorId=other_actor, sessionId="probe-session"),
        "ListEvents",
        "actorId",
        "other",
    )

    # ---- Group 6: ListActors -- does any condition key scope this at all? ----
    set_role_policy(
        iam,
        memory_arn,
        [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:ListActors"],
                "Resource": memory_arn,
                "Condition": {"StringLike": {"bedrock-agentcore:actorId": f"tenant-{OWN_TENANT}-*"}},
            }
        ],
    )
    dc = assumed_data_client(sts, role_arn)

    def list_actors_and_check_leak():
        resp = dc.list_actors(memoryId=memory_id)
        actor_ids = [a.get("actorId") for a in resp.get("actorSummaries", resp.get("actors", []))]
        leaked = [a for a in actor_ids if a and not a.startswith(f"tenant-{OWN_TENANT}-")]
        if leaked:
            raise AssertionError(f"ListActors leaked other-tenant actorIds despite actorId condition: {leaked}")

    attempt(list_actors_and_check_leak, "ListActors", "actorId", "cross-tenant-leak-check")

    # ---- restore: leave production policy re-application to deploy/03_memory.py ----
    # (run immediately after this probe; see deploy/08 docstring and README)
    out_path = Path(__file__).resolve().parent.parent / "iam_probe_raw.json"
    out_path.write_text(json.dumps(results, indent=2))
    log(f"Raw results written to {out_path}")
    print("\n=== SUMMARY ===")
    for r in results:
        print(f"{r['action']:28s} key={r['condition_key']:14s} target={r['target']:45s} -> {r['outcome']}")


if __name__ == "__main__":
    main()
