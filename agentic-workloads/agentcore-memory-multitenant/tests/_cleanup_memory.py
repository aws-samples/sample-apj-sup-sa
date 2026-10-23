"""Deletes memory records for one shopper_id, across all tenant namespaces
(test cleanup utility only -- NOT part of the deploy pipeline, and mostly
unnecessary now that tests generate a fresh uuid-based shopper_id per test
run, which never collides with prior runs). Useful for manually wiping a
specific demo persona's data, e.g. after a UI demo session.

Usage: python3 tests/_cleanup_memory.py [shopper_id]  (default: anonymous)
"""
import json
import sys
import uuid
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import REGION, TENANTS, private_namespace, shared_namespace

SHOPPER_ID = sys.argv[1] if len(sys.argv) > 1 else "anonymous"

state = json.loads((Path(__file__).resolve().parent.parent / "deploy" / "state.json").read_text())
sts = boto3.client("sts", region_name=REGION)
memory_id = state["memory"]["memory_id"]


def assumed(tenant_id):
    role_arn = state["iam"]["tenant_access_role_arns"][tenant_id]
    resp = sts.assume_role(RoleArn=role_arn, RoleSessionName=f"cleanup-{uuid.uuid4().hex[:8]}")
    c = resp["Credentials"]
    return boto3.client(
        "bedrock-agentcore",
        region_name=REGION,
        aws_access_key_id=c["AccessKeyId"],
        aws_secret_access_key=c["SecretAccessKey"],
        aws_session_token=c["SessionToken"],
    )


total = 0
for tenant_id, cfg in TENANTS.items():
    client = assumed(tenant_id)
    namespaces = [shared_namespace(tenant_id, SHOPPER_ID)] + [
        private_namespace(tenant_id, a, SHOPPER_ID) for a in cfg["agents"]
    ]
    for ns in namespaces:
        next_token = None
        while True:
            kwargs = {"memoryId": memory_id, "namespace": ns, "maxResults": 100}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = client.list_memory_records(**kwargs)
            records = resp.get("memoryRecordSummaries", resp.get("memoryRecords", []))
            for r in records:
                rid = r["memoryRecordId"]
                try:
                    client.delete_memory_record(memoryId=memory_id, memoryRecordId=rid)
                    total += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  failed to delete {rid}: {exc}")
            next_token = resp.get("nextToken")
            if not next_token:
                break
    print(f"cleaned {tenant_id} (shopper_id={SHOPPER_ID})")

print(f"Deleted {total} records total.")
