"""Step 5: create/update the two Lambda functions, then attach the Gateway
execution role's lambda:InvokeFunction policy scoped to exactly these two
function ARNs (no wildcard).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import all_tenant_agent_pairs
from deploy.common import account_id, client, load_state, log, save_state, tags_map

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / ".build"
INTERCEPTOR_FN = "agentcore-mt-interceptor"
TOOLS_FN = "agentcore-mt-tools"


def build_client_map(state: dict) -> dict:
    clients = state["cognito"]["clients"]
    mapping = {}
    for key, info in clients.items():
        if info.get("tenant_id") and info.get("agent_id"):
            mapping[info["client_id"]] = {"tenant_id": info["tenant_id"], "agent_id": info["agent_id"]}
    return mapping


def ensure_function(lam, name: str, role_arn: str, zip_path: Path, handler: str, env: dict, timeout: int = 15) -> str:
    code_bytes = zip_path.read_bytes()
    try:
        lam.get_function(FunctionName=name)
        exists = True
    except lam.exceptions.ResourceNotFoundException:
        exists = False

    if not exists:
        resp = lam.create_function(
            FunctionName=name,
            Runtime="python3.12",
            Role=role_arn,
            Handler=handler,
            Code={"ZipFile": code_bytes},
            Timeout=timeout,
            MemorySize=256,
            Architectures=["arm64"],
            Environment={"Variables": env},
            Tags=tags_map(),
        )
        log(f"created function: {name}")
        arn = resp["FunctionArn"]
    else:
        lam.update_function_code(FunctionName=name, ZipFile=code_bytes)
        _wait_update_done(lam, name)
        resp = lam.update_function_configuration(
            FunctionName=name,
            Role=role_arn,
            Handler=handler,
            Timeout=timeout,
            MemorySize=256,
            Environment={"Variables": env},
        )
        log(f"updated function: {name}")
        arn = resp["FunctionArn"]

    _wait_update_done(lam, name)
    return arn


def _wait_update_done(lam, name: str, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        cfg = lam.get_function_configuration(FunctionName=name)
        if cfg.get("LastUpdateStatus", "Successful") != "InProgress" and cfg.get("State", "Active") != "Pending":
            return
        time.sleep(2)


def attach_gateway_invoke_policy(iam, interceptor_arn: str, tools_arn: str) -> None:
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": [interceptor_arn, tools_arn],
            }
        ],
    }
    iam.put_role_policy(
        RoleName="agentcore-mt-gateway-role",
        PolicyName="invoke-specific-lambda-targets",
        PolicyDocument=json.dumps(policy),
    )
    log("attached invoke-specific-lambda-targets policy -> agentcore-mt-gateway-role (no wildcard)")


def add_gateway_invoke_permission(lam, fn_name: str, gateway_role_arn: str) -> None:
    """Resource-based policy on the Lambda itself: only the gateway execution
    role may invoke it. (Belt-and-suspenders alongside the gateway role's own
    identity policy above.)"""
    try:
        lam.add_permission(
            FunctionName=fn_name,
            StatementId="allow-agentcore-gateway-role",
            Action="lambda:InvokeFunction",
            Principal=gateway_role_arn,
        )
        log(f"  added resource policy on {fn_name} for gateway role")
    except lam.exceptions.ResourceConflictException:
        pass  # already present


def kb_env(state: dict) -> dict:
    """kb_search env vars, once deploy/14_knowledge_base.py has created the KB
    (on a first deploy step 14 runs later and merges them in itself)."""
    kb = state.get("kb")
    if not kb:
        return {}
    return {
        "KB_VECTOR_BUCKET": kb["vector_bucket"],
        "KB_INDEX_PREFIX": kb["index_prefix"],
        "KB_EMBED_MODEL_ID": kb["embed_model_id"],
        "KB_EMBED_DIM": str(kb["embed_dim"]),
    }


def main():
    lam = client("lambda")
    iam = client("iam")
    state = load_state()
    iam_state = state["iam"]
    memory_state = state["memory"]

    client_map = build_client_map(state)

    interceptor_arn = ensure_function(
        lam,
        INTERCEPTOR_FN,
        iam_state["interceptor_role_arn"],
        BUILD / "interceptor.zip",
        "handler.handler",
        {"CLIENT_MAP_JSON": json.dumps(client_map)},
        timeout=10,
    )

    tools_arn = ensure_function(
        lam,
        TOOLS_FN,
        iam_state["tools_role_arn"],
        BUILD / "tools.zip",
        "handler.handler",
        {
            "MEMORY_ID": memory_state["memory_id"],
            "TENANT_ROLE_ARNS_JSON": json.dumps(iam_state["tenant_access_role_arns"]),
            **kb_env(state),
        },
        timeout=30,
    )

    attach_gateway_invoke_policy(iam, interceptor_arn, tools_arn)
    add_gateway_invoke_permission(lam, INTERCEPTOR_FN, iam_state["gateway_role_arn"])
    add_gateway_invoke_permission(lam, TOOLS_FN, iam_state["gateway_role_arn"])

    state["lambdas"] = {"interceptor_arn": interceptor_arn, "tools_arn": tools_arn}
    save_state(state)
    log("Lambda setup complete.")
    log(f"  interceptor_arn={interceptor_arn}")
    log(f"  tools_arn={tools_arn}")
    log(f"  client_map entries={len(client_map)}")


if __name__ == "__main__":
    main()
