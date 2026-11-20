"""Shared helpers for the deploy scripts: boto3 clients, idempotent state
file read/write, and small utilities. No secrets are ever written to
state.json -- Cognito client secrets are fetched live from Cognito at test
time, never persisted to disk.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "deploy" / "state.json"

sys.path.insert(0, str(ROOT))
from config.tenants import PROJECT, REGION, TAGS  # noqa: E402

_session = boto3.Session(region_name=REGION)
_CALLER = _session.client("sts").get_caller_identity()
ACCOUNT_ID = _CALLER["Account"]


# CloudFront-scoped resources (WAF web ACLs) are always managed from us-east-1.
CLOUDFRONT_REGION = "us-east-1"


def client(service: str, region_name: str | None = None):
    return _session.client(service, region_name=region_name or REGION)


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def tags_list() -> list[dict]:
    return [{"key": k, "value": v} for k, v in TAGS.items()]


def tags_map() -> dict:
    return dict(TAGS)


def log(msg: str) -> None:
    print(f"[deploy] {msg}", flush=True)


def wait_until(predicate, timeout_s: int = 180, interval_s: int = 5, what: str = "resource"):
    """Poll `predicate()` until it returns a truthy value or timeout."""
    deadline = time.time() + timeout_s
    last_exc = None
    while time.time() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(interval_s)
    extra = f" last_error={last_exc}" if last_exc else ""
    raise TimeoutError(f"Timed out waiting for {what}.{extra}")


def account_id() -> str:
    return ACCOUNT_ID


def caller_principal_arn() -> str:
    """IAM principal ARN of whoever runs the deploy, usable in a trust policy.

    An assumed-role session ARN (arn:aws:sts::<acct>:assumed-role/<role>/<session>)
    is mapped back to the role's IAM ARN (looked up, so roles with a path such
    as IAM Identity Center roles work); IAM users are returned as-is. Set
    DEPLOYER_PRINCIPAL_ARN to trust a different principal instead.
    """
    override = os.environ.get("DEPLOYER_PRINCIPAL_ARN")
    if override:
        return override
    arn = _CALLER["Arn"]
    if ":assumed-role/" in arn:
        role_name = arn.split(":assumed-role/", 1)[1].split("/", 1)[0]
        return client("iam").get_role(RoleName=role_name)["Role"]["Arn"]
    return arn
