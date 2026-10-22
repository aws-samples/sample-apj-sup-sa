"""Step 9: the web UI backend -- SSM secrets, IAM role, Lambda function,
and a Lambda Function URL (fronted by CloudFront in deploy/10_webui_cdn.py).

Secrets handling: each Cognito app client's secret is stored as an SSM
SecureString parameter, never as a plain Lambda environment variable and
never committed to git.

The web UI is public (no sign-in). Abuse controls -- WAF on
CloudFront, reserved concurrency on this function -- live in
deploy/13_abuse_controls.py; per-request caps are in the handler itself.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import MODEL_HAIKU, MODEL_NOVA_LITE, MODEL_PRICING_PER_1K_TOKENS, MODEL_SONNET, REGION, TENANTS
from deploy.common import account_id, client, load_state, log, save_state, tags_map

ROLE_NAME = "agentcore-mt-webui-backend-role"
FUNCTION_NAME = "agentcore-mt-webui-backend"
SSM_PREFIX = "/agentcore-mt-demo"

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / ".build"


def ensure_client_secret_params(ssm, idp, state: dict) -> dict:
    """Returns {('tenant:agent'): ssm_param_name}."""
    pool_id = state["cognito"]["user_pool_id"]
    out = {}
    for key, info in state["cognito"]["clients"].items():
        if info.get("tenant_id") is None:
            continue  # skip the attacker/no-scope client
        secret = idp.describe_user_pool_client(UserPoolId=pool_id, ClientId=info["client_id"])["UserPoolClient"][
            "ClientSecret"
        ]
        param_name = f"{SSM_PREFIX}/cognito-secret/{key.replace(':', '-')}"
        ssm.put_parameter(
            Name=param_name,
            Value=secret,
            Type="SecureString",
            Overwrite=True,
            Description=f"Cognito app client secret for {key}",
        )
        try:
            ssm.add_tags_to_resource(
                ResourceType="Parameter", ResourceId=param_name, Tags=[{"Key": k, "Value": v} for k, v in tags_map().items()]
            )
        except Exception:  # noqa: BLE001, S110
            pass
        out[key] = param_name
    log(f"stored {len(out)} Cognito client secrets in SSM under {SSM_PREFIX}/cognito-secret/")
    return out


def ensure_role(iam, acct: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
    }
    try:
        resp = iam.get_role(RoleName=ROLE_NAME)
        iam.update_assume_role_policy(RoleName=ROLE_NAME, PolicyDocument=json.dumps(trust))
        arn = resp["Role"]["Arn"]
        log(f"role exists: {ROLE_NAME}")
    except iam.exceptions.NoSuchEntityException:
        resp = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Web UI backend Lambda role (MT memory demo)",
            Tags=[{"Key": k, "Value": v} for k, v in tags_map().items()],
        )
        arn = resp["Role"]["Arn"]
        log(f"created role: {ROLE_NAME}")
        time.sleep(5)

    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="logs",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                        "Resource": f"arn:aws:logs:{REGION}:{acct}:log-group:/aws/lambda/{FUNCTION_NAME}:*",
                    }
                ],
            }
        ),
    )
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="ssm-read-demo-params",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "ssm:GetParameter",
                        "Resource": f"arn:aws:ssm:{REGION}:{acct}:parameter{SSM_PREFIX}/cognito-secret/*",
                    }
                ],
            }
        ),
    )
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="invoke-approved-models",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                        "Resource": [
                            f"arn:aws:bedrock:{REGION}:{acct}:inference-profile/{MODEL_HAIKU}",
                            f"arn:aws:bedrock:{REGION}:{acct}:inference-profile/{MODEL_NOVA_LITE}",
                            "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                            "arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0",
                            # global.* Sonnet cross-region profile spans all commercial regions;
                            # scope to the global inference-profile resource + its foundation models.
                            f"arn:aws:bedrock:{REGION}:{acct}:inference-profile/{MODEL_SONNET}",
                            "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                        ],
                    },
                    {
                        "Effect": "Allow",
                        "Action": "bedrock:ApplyGuardrail",
                        "Resource": f"arn:aws:bedrock:{REGION}:{acct}:guardrail/*",
                    },
                ],
            }
        ),
    )
    return arn


def ensure_function(lam, role_arn: str, env: dict) -> str:
    zip_path = BUILD / "webui_backend.zip"
    code_bytes = zip_path.read_bytes()
    try:
        lam.get_function(FunctionName=FUNCTION_NAME)
        lam.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=code_bytes)
        _wait_update(lam)
        resp = lam.update_function_configuration(
            FunctionName=FUNCTION_NAME,
            Role=role_arn,
            Handler="handler.handler",
            Timeout=60,
            MemorySize=512,
            Environment={"Variables": env},
        )
        log(f"updated function: {FUNCTION_NAME}")
    except lam.exceptions.ResourceNotFoundException:
        resp = lam.create_function(
            FunctionName=FUNCTION_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="handler.handler",
            Code={"ZipFile": code_bytes},
            Timeout=60,
            MemorySize=512,
            Architectures=["arm64"],
            Environment={"Variables": env},
            Tags=tags_map(),
        )
        log(f"created function: {FUNCTION_NAME}")
    _wait_update(lam)
    return resp["FunctionArn"]


def _wait_update(lam, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        cfg = lam.get_function_configuration(FunctionName=FUNCTION_NAME)
        if cfg.get("LastUpdateStatus", "Successful") != "InProgress" and cfg.get("State", "Active") != "Pending":
            return
        time.sleep(2)


def ensure_function_url(lam) -> str:
    try:
        resp = lam.get_function_url_config(FunctionName=FUNCTION_NAME)
        log("function URL already configured")
    except lam.exceptions.ResourceNotFoundException:
        # AuthType=AWS_IAM, not NONE: public Function URLs are often blocked
        # by organization guardrails, and this one never needs to be public.
        # CloudFront's Origin Access Control (OAC) for Lambda origins
        # (deploy/10_webui_cdn.py) SigV4-signs every request on CloudFront's
        # behalf so only this one CloudFront distribution can invoke it.
        resp = lam.create_function_url_config(
            FunctionName=FUNCTION_NAME,
            AuthType="AWS_IAM",
            Cors={
                "AllowOrigins": ["*"],
                "AllowMethods": ["*"],
                "AllowHeaders": ["*"],
            },
        )
        log("created function URL (AuthType=AWS_IAM; invokable only via CloudFront OAC)")
    return resp["FunctionUrl"]


def build_tenant_agents_list(state: dict, secret_params: dict) -> list:
    out = []
    for tenant_id, cfg in TENANTS.items():
        for agent_id, agent_display in cfg["agents"].items():
            key = f"{tenant_id}:{agent_id}"
            info = state["cognito"]["clients"][key]
            out.append(
                {
                    "tenant_id": tenant_id,
                    "agent_id": agent_id,
                    "tenant_display": cfg["display_name"],
                    "agent_display": agent_display,
                    "client_id": info["client_id"],
                    "scope": info["scope"],
                    "token_endpoint": state["cognito"]["token_endpoint"],
                    "secret_param": secret_params[key],
                }
            )
    return out


def main() -> None:
    state = load_state()
    ssm = client("ssm")
    idp = client("cognito-idp")
    iam = client("iam")
    lam = client("lambda")
    acct = account_id()

    secret_params = ensure_client_secret_params(ssm, idp, state)
    role_arn = ensure_role(iam, acct)

    env = {
        "GATEWAY_URL": state["gateway"]["gateway_url"],
        "TENANT_AGENTS_JSON": json.dumps(build_tenant_agents_list(state, secret_params)),
        "MODEL_PRICING_JSON": json.dumps(MODEL_PRICING_PER_1K_TOKENS),
        "DEFAULT_MODEL_ID": MODEL_HAIKU,
        "AVAILABLE_MODELS_JSON": json.dumps(
            [
                {"model_id": MODEL_HAIKU, "label": "Claude Haiku 4.5 (fast, cheap)"},
                {"model_id": MODEL_SONNET, "label": "Claude Sonnet 4.5 (higher quality)"},
            ]
        ),
        "GUARDRAIL_ID": state.get("webui", {}).get("guardrail_id", ""),
        "GUARDRAIL_VERSION": state.get("webui", {}).get("guardrail_version", "DRAFT"),
        # Set once deploy/15_runtime.py has created the Runtime; empty = every
        # chat turn runs in this Lambda.
        "RUNTIME_ARN": state.get("runtime", {}).get("agent_runtime_arn", ""),
    }
    function_arn = ensure_function(lam, role_arn, env)
    function_url = ensure_function_url(lam)

    state["webui"] = state.get("webui", {})
    state["webui"]["backend_function_arn"] = function_arn
    state["webui"]["backend_function_url"] = function_url
    save_state(state)
    log("Web UI backend setup complete.")
    log(f"  backend_function_url={function_url}")


if __name__ == "__main__":
    main()
