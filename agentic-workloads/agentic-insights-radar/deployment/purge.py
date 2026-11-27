#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Delete what a removed stack kept because of retainData=true. Used by ``destroy.sh --purge``.

Two steps, so the list is taken while the stack still exists and nothing else is ever touched:

    purge.py plan  <stack> <plan.json> [<stack>@<region> ...]
                                         record the stack's tables, buckets, user pool, keys,
                                         vector bucket, backup vault, log groups and secrets (before
                                         cdk destroy). Each extra <stack>@<region> is recorded
                                         too, with its own Region, and is allowed not to exist:
                                         that is how a deployment outside us-east-1 records the
                                         us-east-1 edge stack's WAF log group and KMS key, which
                                         are kept on delete and whose fixed log-group name would
                                         otherwise break the next deploy.
    purge.py apply <plan.json> [--delete-backups]
                                         delete each recorded resource that still exists, in the
                                         Region it was recorded in (after cdk destroy)

Only the AWS CLI is needed. KMS keys are scheduled for deletion after 7 days (the minimum).

Backups are the one exception to "apply deletes everything recorded". A backup vault
(``backupPlan=true``) outlives the stack on purpose, and its recovery points are encrypted with
the data key, so scheduling that key for deletion would make every recovery point unreadable
seven days later. ``apply`` therefore keeps a vault, and keeps every recorded KMS key that the
surviving vault or one of its recovery points still needs, unless ``--delete-backups`` says to
delete the backups as well.

Two kinds of backup are not stack resources, so ``plan`` looks for them itself:

* a vault that ``backupPlan`` true to false left behind (CloudFormation kept it and stopped owning it):
  every vault in the Region whose ``EncryptionKeyArn`` is one of the recorded keys is recorded (P3-012);
* on-demand DynamoDB backups of the recorded tables, which the guide asks for before every upgrade and
  which are encrypted with the table's key: while one exists, ``apply`` keeps that Region's recorded
  keys, and ``--delete-backups`` deletes them first (P3-056).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - runs the AWS CLI with fixed arguments, never a shell
import sys
import tempfile
import time
from typing import Any

KINDS = (
    "AWS::DynamoDB::GlobalTable",
    "AWS::DynamoDB::Table",
    "AWS::S3::Bucket",
    "AWS::Cognito::UserPool",
    "AWS::KMS::Key",
    "AWS::S3Vectors::VectorBucket",
    "AWS::Backup::BackupVault",
    "AWS::Logs::LogGroup",
    "AWS::SecretsManager::Secret",
)
# Words only the digest secrets' descriptions carry (infra/insights_radar_infra/digest.py).
DIGEST_SECRETS = ("incoming-webhook URL for digest delivery", "digest emails' one-click unsubscribe links")
# How long ``apply`` waits for AWS Backup to finish deleting a vault's recovery points.
DELETE_POLL_SECONDS = 5
DELETE_POLL_TRIES = 24
AWS = shutil.which("aws") or "aws"
# Physical IDs as CloudFormation reports them (names, key IDs, pool IDs, ARNs) and log group names. A plan file that holds
# anything else was not written by ``plan`` and is refused before any call is made.
SAFE_ID = re.compile(r"^[A-Za-z0-9/][A-Za-z0-9:/._-]{0,511}$")  # never a leading "-" (an option)
SAFE_REGION = re.compile(r"^[a-z0-9-]{1,32}$")
# CloudFormation's error code for a stack that does not exist.
NO_SUCH_STACK = ("ValidationError",)


class AwsError(RuntimeError):
    pass


def aws(*args: str, missing: tuple[str, ...] = (), region: str | None = None) -> Any:
    """Run one AWS CLI call. Returns None only when the call failed with one of the ``missing`` error codes
    (the resource does not exist); any other failure (an expired session, AccessDenied, throttling) raises,
    so a purge never reports data as gone when it could not look."""
    # An argument list (no shell) for a fixed executable; the values come from AWS API responses and
    # from IDs checked against SAFE_ID and Regions checked against SAFE_REGION.
    where = ["--region", region] if region else []
    out = subprocess.run(  # noqa: S603  # nosec B603 B607
        ["aws", *args, *where, "--output", "json"], capture_output=True, text=True, check=False, executable=AWS
    )
    if out.returncode != 0:
        if any(f"({code})" in out.stderr for code in missing):
            return None
        raise AwsError(f"aws {' '.join(args[:2])}: {out.stderr.strip()}")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def service_log_groups(resources: list[dict[str, str]], region: str | None) -> list[str]:
    """Log groups the services create for the stack's functions and agent runtimes, which CloudFormation
    does not own. Lambda's are listed whether or not they exist yet: the stack's custom resources run, and
    can create theirs, during destroy."""
    names = []
    for r in resources:
        if r["ResourceType"] == "AWS::Lambda::Function":
            names.append(f"/aws/lambda/{r['PhysicalResourceId']}")
        elif r["ResourceType"] == "AWS::BedrockAgentCore::Runtime":
            runtime = r["PhysicalResourceId"].rsplit("/", 1)[-1]
            prefix = f"/aws/bedrock-agentcore/runtimes/{runtime}-"
            found = aws("logs", "describe-log-groups", "--log-group-name-prefix", prefix, region=region)["logGroups"]
            names += [g["logGroupName"] for g in found] or [f"{prefix}DEFAULT"]
    return names


def current_region() -> str | None:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or None


def stack_items(stack: str, region: str | None, required: bool) -> list[dict[str, str]]:
    found = aws(
        "cloudformation",
        "list-stack-resources",
        "--stack-name",
        stack,
        region=region,
        missing=() if required else NO_SUCH_STACK,
    )
    if found is None:
        print(f"  (no stack {stack} in {region})")
        return []
    res = found["StackResourceSummaries"]
    items = [
        {"type": r["ResourceType"], "id": r["PhysicalResourceId"], "region": region or ""}
        for r in res
        if r["ResourceType"] in KINDS and r.get("PhysicalResourceId")
    ]
    owned = {i["id"] for i in items}
    items += [
        {"type": "AWS::Logs::LogGroup", "id": n, "region": region or ""}
        for n in service_log_groups(res, region)
        if n not in owned
    ]
    return items


def orphaned_vaults(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Vaults the stack no longer owns that are encrypted with one of its recorded keys."""
    keys: dict[str, set[str]] = {}
    for i in items:
        if i["type"] == "AWS::KMS::Key":
            keys.setdefault(i["region"], set()).add(i["id"])
    recorded = {(vault_name(i["id"]), i["region"]) for i in items if i["type"] == "AWS::Backup::BackupVault"}
    found = []
    for region, ids in keys.items():
        listed = aws("backup", "list-backup-vaults", region=region or None) or {}
        for vault in listed.get("BackupVaultList", []):
            name, key = str(vault.get("BackupVaultName", "")), str(vault.get("EncryptionKeyArn", ""))
            if key.rsplit("/", 1)[-1] in ids and (name, region) not in recorded and SAFE_ID.match(name):
                found.append({"type": "AWS::Backup::BackupVault", "id": name, "region": region})
    return found


def orphaned_secrets(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Secrets the stack no longer owns that are encrypted with one of its recorded keys: the digest secrets
    retainData kept when digest delivery was switched off (one may hold a live Slack token; scan cycle 60)."""
    keys: dict[str, set[str]] = {}
    for i in items:
        if i["type"] == "AWS::KMS::Key":
            keys.setdefault(i["region"], set()).add(i["id"])
    recorded = {(i["id"], i["region"]) for i in items if i["type"] == "AWS::SecretsManager::Secret"}
    found = []
    for region, ids in keys.items():
        listed = aws("secretsmanager", "list-secrets", region=region or None) or {}
        for secret in listed.get("SecretList", []):
            arn, key = str(secret.get("ARN", "")), str(secret.get("KmsKeyId", ""))
            if secret.get("DeletedDate") or (arn, region) in recorded or not SAFE_ID.match(arn):
                continue
            # Only the digest secrets, by the description the stack gives them: an operator's own secret on the
            # same key (an OIDC client secret, say) is left alone (scan cycle 61).
            if key.rsplit("/", 1)[-1] in ids and any(t in str(secret.get("Description", "")) for t in DIGEST_SECRETS):
                found.append({"type": "AWS::SecretsManager::Secret", "id": arn, "region": region})
    return found


def table_backups(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    """On-demand backups of the recorded tables, by Region."""
    out: dict[str, list[str]] = {}
    for i in items:
        if i["type"] in ("AWS::DynamoDB::GlobalTable", "AWS::DynamoDB::Table"):
            region = i.get("region") or ""
            listed = aws(
                "dynamodb", "list-backups", "--table-name", i["id"], "--backup-type", "USER", region=region or None
            ) or {}
            out.setdefault(region, []).extend(
                str(b["BackupArn"]) for b in listed.get("BackupSummaries", []) if b.get("BackupArn")
            )
    return out


def plan(stack: str, path: str, also: list[str]) -> None:
    items = stack_items(stack, current_region(), required=True)
    for target in also:
        name, _, region = target.partition("@")
        items += stack_items(name, region or None, required=False)
    items += orphaned_vaults(items)
    items += orphaned_secrets(items)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"stack": stack, "resources": items}, f, indent=2)
    for i in items:
        where = f" ({i['region']})" if i.get("region") else ""
        print(f"  {i['type']:32} {i['id']}{where}")


def empty_bucket(bucket: str, region: str | None) -> None:
    while True:
        # --no-paginate: one request, at most 1000 entries counting versions and delete markers together.
        page = aws(
            "s3api", "list-object-versions", "--bucket", bucket, "--max-keys", "1000", "--no-paginate", region=region
        )
        objs = [
            {"Key": o["Key"], "VersionId": o["VersionId"]}
            for o in page.get("Versions", []) + page.get("DeleteMarkers", [])
        ]
        if not objs:
            return
        for start in range(0, len(objs), 1000):  # delete-objects takes at most 1000 keys
            # Passed as a file: 1000 keys on the command line can pass the kernel's argument size limit.
            with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as batch:
                json.dump({"Objects": objs[start : start + 1000], "Quiet": True}, batch)
                batch.flush()
                result = aws(
                    "s3api", "delete-objects", "--bucket", bucket, "--delete", f"file://{batch.name}", region=region
                )
            if result.get("Errors"):
                first = result["Errors"][0]
                raise AwsError(
                    f"{bucket}: {len(result['Errors'])} not deleted, e.g. {first.get('Key')}: {first.get('Code')}"
                )


def vault_name(rid: str) -> str:
    """A recorded vault id as a vault name (CloudFormation reports the name; an ARN is accepted too)."""
    return rid.rsplit(":", 1)[-1] if rid.startswith("arn:") else rid


def vault_exists(name: str, region: str | None) -> dict[str, Any] | None:
    return aws(
        "backup",
        "describe-backup-vault",
        "--backup-vault-name",
        name,
        missing=("ResourceNotFoundException",),
        region=region,
    )


def recovery_points(name: str, region: str | None) -> list[dict[str, Any]]:
    found = aws(
        "backup",
        "list-recovery-points-by-backup-vault",
        "--backup-vault-name",
        name,
        missing=("ResourceNotFoundException",),
        region=region,
    )
    return [] if found is None else found.get("RecoveryPoints", [])


def keys_backups_need(vaults: list[tuple[str, str | None]]) -> set[str]:
    """Key IDs that the given vaults, or their recovery points, still need to be readable. A key in this
    set is never scheduled for deletion, so a kept vault stays restorable."""
    needed: set[str] = set()
    for name, region in vaults:
        vault = vault_exists(name, region)
        if vault is None:
            continue
        arns = [
            vault.get("EncryptionKeyArn"),
            *(p.get("EncryptionKeyArn") for p in recovery_points(name, region)),
        ]
        needed |= {a.rsplit("/", 1)[-1] for a in arns if a}
    return needed


def delete_vault(name: str, region: str | None) -> str:
    for point in recovery_points(name, region):
        aws(
            "backup",
            "delete-recovery-point",
            "--backup-vault-name",
            name,
            "--recovery-point-arn",
            point["RecoveryPointArn"],
            region=region,
        )
    for _ in range(DELETE_POLL_TRIES):
        if not recovery_points(name, region):
            break
        time.sleep(DELETE_POLL_SECONDS)
    else:
        raise AwsError(f"{name}: its recovery points are still being deleted; run apply again to finish")
    aws("backup", "delete-backup-vault", "--backup-vault-name", name, region=region)
    return "deleted with its recovery points"


def delete(
    kind: str,
    rid: str,
    *,
    region: str | None = None,
    delete_backups: bool = False,
    keep_keys: frozenset[str] = frozenset(),
) -> str:
    if kind == "AWS::Logs::LogGroup":
        found = aws("logs", "describe-log-groups", "--log-group-name-prefix", rid, region=region)["logGroups"]
        if not any(g["logGroupName"] == rid for g in found):
            return "already gone"
        aws("logs", "delete-log-group", "--log-group-name", rid, region=region)
    elif kind == "AWS::S3::Bucket":
        if aws("s3api", "head-bucket", "--bucket", rid, missing=("404",), region=region) is None:
            return "already gone"
        empty_bucket(rid, region)
        aws("s3api", "delete-bucket", "--bucket", rid, region=region)
    elif kind in ("AWS::DynamoDB::GlobalTable", "AWS::DynamoDB::Table"):
        if (
            aws("dynamodb", "describe-table", "--table-name", rid, missing=("ResourceNotFoundException",), region=region)
            is None
        ):
            return "already gone"
        aws("dynamodb", "update-table", "--table-name", rid, "--no-deletion-protection-enabled", region=region)
        aws("dynamodb", "wait", "table-exists", "--table-name", rid, region=region)
        aws("dynamodb", "delete-table", "--table-name", rid, region=region)
    elif kind == "AWS::Cognito::UserPool":
        pool = aws(
            "cognito-idp",
            "describe-user-pool",
            "--user-pool-id",
            rid,
            missing=("ResourceNotFoundException",),
            region=region,
        )
        if pool is None:
            return "already gone"
        domain = pool["UserPool"].get("Domain")
        if domain:
            aws("cognito-idp", "delete-user-pool-domain", "--domain", domain, "--user-pool-id", rid, region=region)
        aws(
            "cognito-idp",
            "update-user-pool",
            "--user-pool-id",
            rid,
            "--deletion-protection",
            "INACTIVE",
            "--auto-verified-attributes",
            *pool["UserPool"].get("AutoVerifiedAttributes", ["email"]),
            region=region,
        )
        aws("cognito-idp", "delete-user-pool", "--user-pool-id", rid, region=region)
    elif kind == "AWS::KMS::Key":
        if rid in keep_keys:
            return "kept: backups still need it (--delete-backups deletes both)"
        meta = aws("kms", "describe-key", "--key-id", rid, missing=("NotFoundException",), region=region)
        if meta is None or meta["KeyMetadata"]["KeyState"] == "PendingDeletion":
            return "already gone"
        aws("kms", "schedule-key-deletion", "--key-id", rid, "--pending-window-in-days", "7", region=region)
        return "deletion scheduled in 7 days"
    elif kind == "AWS::SecretsManager::Secret":
        # The digest secrets (one holds the live Slack token) are kept with the data. Scheduled with the
        # shortest recovery window, like the keys, so a mistaken purge can still be undone (scan cycle 59).
        meta = aws(
            "secretsmanager",
            "describe-secret",
            "--secret-id",
            rid,
            missing=("ResourceNotFoundException",),
            region=region,
        )
        if meta is None or meta.get("DeletedDate"):
            return "already gone"
        aws("secretsmanager", "delete-secret", "--secret-id", rid, "--recovery-window-in-days", "7", region=region)
        return "deletion scheduled in 7 days"
    elif kind == "AWS::Backup::BackupVault":
        name = vault_name(rid)
        if vault_exists(name, region) is None:
            return "already gone"
        if not delete_backups:
            return "kept: pass --delete-backups to delete it and its recovery points"
        return delete_vault(name, region)
    elif kind == "AWS::S3Vectors::VectorBucket":
        name = rid.rsplit("/", 1)[-1]
        idx = aws(
            "s3vectors", "list-indexes", "--vector-bucket-name", name, missing=("NotFoundException",), region=region
        )
        if idx is None:
            return "already gone"
        for i in idx.get("indexes", []):
            aws(
                "s3vectors",
                "delete-index",
                "--vector-bucket-name",
                name,
                "--index-name",
                i["indexName"],
                region=region,
            )
        aws("s3vectors", "delete-vector-bucket", "--vector-bucket-name", name, region=region)
    return "deleted"


def apply(path: str, delete_backups: bool = False) -> int:
    with open(path, encoding="utf-8") as f:
        recorded = json.load(f)
    bad = [
        r
        for r in recorded["resources"]
        if r.get("type") not in KINDS
        or not SAFE_ID.match(str(r.get("id", "")))
        or (r.get("region") and not SAFE_REGION.match(str(r["region"])))
    ]
    if bad:
        print(f"refusing {path}: unexpected entries {bad[:3]}", file=sys.stderr)
        return 2
    # Read before anything is deleted: the keys a vault that survives this apply still needs. The vault
    # may come after its key in the plan, so the answer cannot be worked out as the list is walked.
    vaults = [
        (vault_name(r["id"]), r.get("region") or None)
        for r in recorded["resources"]
        if r["type"] == "AWS::Backup::BackupVault"
    ]
    keep_keys = frozenset() if delete_backups else frozenset(keys_backups_need(vaults))
    failed = 0
    for region, arns in table_backups(recorded["resources"]).items():
        if not arns:
            continue
        if delete_backups:
            for arn in arns:
                aws("dynamodb", "delete-backup", "--backup-arn", arn, region=region or None)
            print(f"  {len(arns)} on-demand table backup(s){f' ({region})' if region else ''}: deleted")
            continue
        # The table's key encrypts its on-demand backups: scheduling it would make them unrestorable.
        keep_keys |= {
            r["id"]
            for r in recorded["resources"]
            if r["type"] == "AWS::KMS::Key" and (r.get("region") or "") == region
        }
        print(
            f"  {len(arns)} on-demand table backup(s){f' ({region})' if region else ''}: kept, with the keys that "
            "read them (--delete-backups deletes both)"
        )
    for r in recorded["resources"]:
        region = r.get("region") or None
        try:
            outcome = delete(
                r["type"], r["id"], region=region, delete_backups=delete_backups, keep_keys=keep_keys
            )
        except (RuntimeError, OSError) as err:
            outcome, failed = f"FAILED: {err}", failed + 1
        where = f" ({region})" if region else ""
        print(f"  {r['type']:32} {r['id']}{where}: {outcome}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "plan" and not argv[1].startswith("-") and not argv[2].startswith("-"):
        plan(argv[1], argv[2], argv[3:])
        return 0
    if (
        argv[:1] == ["apply"]
        and 2 <= len(argv) <= 3
        and not argv[1].startswith("-")
        and set(argv[2:]) <= {"--delete-backups"}
    ):
        return apply(argv[1], delete_backups="--delete-backups" in argv[2:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
