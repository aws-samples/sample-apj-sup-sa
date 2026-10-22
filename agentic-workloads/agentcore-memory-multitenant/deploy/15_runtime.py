"""Step 15: host the agent on Amazon Bedrock AgentCore Runtime.

  - ECR repo + an arm64 image built from agents/runtime_agent/ plus
    lambda_src/chat_core/chat_core.py (the same chat loop the webui backend
    Lambda keeps as its fallback). The tag is a content hash, so an
    unchanged agent is not rebuilt or re-pushed.
  - Execution role (least privilege): pull this one repo, write its own
    logs/traces/metrics, invoke the two chat models, apply the one
    guardrail. NO Memory, KB, SSM or Cognito permissions -- the agent
    reaches data only through the Gateway, with the caller's JWT.
  - The Runtime: inbound CUSTOM_JWT authorizer (Cognito issuer, the four
    brand+agent app clients only), the Authorization header allowlisted so
    the agent can forward that same JWT to the Gateway, short idle session
    timeout (billing stops when a session ends; no idle cost otherwise).
  - Points the webui backend at it (RUNTIME_ARN env var, merged in).

Idempotent: finds the repo/role/runtime by name and updates in place.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import MODEL_HAIKU, MODEL_PRICING_PER_1K_TOKENS, MODEL_SONNET, REGION, TENANTS
from deploy.common import account_id, client, load_state, log, save_state, tags_map, wait_until

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / ".build" / "runtime_agent"
REPO_NAME = "agentcore-mt-runtime-agent"
ROLE_NAME = "agentcore-mt-runtime-role"
RUNTIME_NAME = "agentcore_mt_shopping_agent"
WEBUI_FN = "agentcore-mt-webui-backend"
IDLE_SESSION_TIMEOUT_S = 300
MAX_SESSION_LIFETIME_S = 3600
AVAILABLE_MODELS = [
    {"model_id": MODEL_HAIKU, "label": "Claude Haiku 4.5 (fast, cheap)"},
    {"model_id": MODEL_SONNET, "label": "Claude Sonnet 4.5 (higher quality)"},
]


def prepare_build_dir() -> str:
    """Copies the build context and returns its content hash (the image tag)."""
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)
    sources = [
        ROOT / "agents" / "runtime_agent" / "Dockerfile",
        ROOT / "agents" / "runtime_agent" / "requirements.txt",
        ROOT / "agents" / "runtime_agent" / "app.py",
        ROOT / "lambda_src" / "chat_core" / "chat_core.py",
    ]
    digest = hashlib.sha256()
    for src in sources:
        shutil.copy(src, BUILD_DIR / src.name)
        digest.update(src.name.encode() + src.read_bytes())
    return digest.hexdigest()[:16]


def ensure_repo(ecr) -> str:
    try:
        repo = ecr.describe_repositories(repositoryNames=[REPO_NAME])["repositories"][0]
        log(f"ECR repo exists: {REPO_NAME}")
    except ecr.exceptions.RepositoryNotFoundException:
        repo = ecr.create_repository(
            repositoryName=REPO_NAME,
            imageScanningConfiguration={"scanOnPush": True},
            imageTagMutability="IMMUTABLE",
            tags=[{"Key": k, "Value": v} for k, v in tags_map().items()],
        )["repository"]
        log(f"created ECR repo: {REPO_NAME}")
    ecr.put_lifecycle_policy(
        repositoryName=REPO_NAME,
        lifecyclePolicyText=json.dumps(
            {
                "rules": [
                    {
                        "rulePriority": 1,
                        "description": "keep the 5 most recent images",
                        "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 5},
                        "action": {"type": "expire"},
                    }
                ]
            }
        ),
    )
    return repo["repositoryUri"]


def build_and_push(ecr, repo_uri: str, tag: str) -> str:
    image = f"{repo_uri}:{tag}"
    try:
        ecr.describe_images(repositoryName=REPO_NAME, imageIds=[{"imageTag": tag}])
        log(f"image already in ECR: {image}")
        return image
    except ecr.exceptions.ImageNotFoundException:
        pass
    subprocess.run(["docker", "build", "--platform", "linux/arm64", "-t", image, str(BUILD_DIR)], check=True)
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    # Password over stdin: never on a command line or in shell history.
    subprocess.run(
        ["docker", "login", "--username", user, "--password-stdin", auth["proxyEndpoint"]],
        input=password.encode(),
        check=True,
        capture_output=True,
    )
    subprocess.run(["docker", "push", image], check=True)
    log(f"pushed {image}")
    return image


def ensure_role(iam, acct: str, guardrail_id: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": acct},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{acct}:*"},
                },
            }
        ],
    }
    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=ROLE_NAME, PolicyDocument=json.dumps(trust))
        log(f"role exists: {ROLE_NAME}")
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="AgentCore Runtime execution role for the MT shopping-assistant agent",
            Tags=[{"Key": k, "Value": v} for k, v in tags_map().items()],
        )["Role"]["Arn"]
        log(f"created role: {ROLE_NAME}")
        time.sleep(10)  # IAM propagation before the Runtime validates the role
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PullThisImage",
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": f"arn:aws:ecr:{REGION}:{acct}:repository/{REPO_NAME}",
            },
            {
                "Sid": "EcrAuthTokenIsAccountWide",
                "Effect": "Allow",
                "Action": "ecr:GetAuthorizationToken",
                "Resource": "*",
            },
            {
                "Sid": "OwnLogs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
                "Resource": f"arn:aws:logs:{REGION}:{acct}:log-group:/aws/bedrock-agentcore/runtimes/*",
            },
            {
                "Sid": "DescribeLogGroups",
                "Effect": "Allow",
                "Action": "logs:DescribeLogGroups",
                "Resource": f"arn:aws:logs:{REGION}:{acct}:log-group:*",
            },
            {
                "Sid": "TracesWriteOnlyHaveNoResourceScope",
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:PutSpans",
                    "xray:PutSpansForIndexing",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": "*",
            },
            {
                "Sid": "OwnMetricsNamespace",
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                "Sid": "ChatModelsOnly",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    f"arn:aws:bedrock:{REGION}:{acct}:inference-profile/{MODEL_HAIKU}",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                    f"arn:aws:bedrock:{REGION}:{acct}:inference-profile/{MODEL_SONNET}",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
                ],
            },
            {
                "Sid": "TheOneGuardrail",
                "Effect": "Allow",
                "Action": "bedrock:ApplyGuardrail",
                "Resource": f"arn:aws:bedrock:{REGION}:{acct}:guardrail/{guardrail_id}",
            },
        ],
    }
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="runtime-agent", PolicyDocument=json.dumps(policy))
    return arn


def client_map(state: dict) -> dict:
    out = {}
    for key, info in state["cognito"]["clients"].items():
        if info.get("tenant_id") is None:
            continue  # the no-scope attacker client is not a brand
        tenant = TENANTS[info["tenant_id"]]
        out[info["client_id"]] = {
            "tenant_id": info["tenant_id"],
            "agent_id": info["agent_id"],
            "tenant_display": tenant["display_name"],
            "agent_display": tenant["agents"][info["agent_id"]],
        }
    return out


def ensure_runtime(ctl, state: dict, image: str, role_arn: str) -> dict:
    cmap = client_map(state)
    webui = state["webui"]
    params = {
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image}},
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "authorizerConfiguration": {
            "customJWTAuthorizer": {
                "discoveryUrl": f"{state['cognito']['issuer']}/.well-known/openid-configuration",
                "allowedClients": sorted(cmap),
            }
        },
        # Lets the agent see the inbound bearer token so it can forward the
        # SAME JWT to the Gateway (identity propagation, no second token).
        "requestHeaderConfiguration": {"requestHeaderAllowlist": ["Authorization"]},
        "lifecycleConfiguration": {
            "idleRuntimeSessionTimeout": IDLE_SESSION_TIMEOUT_S,
            "maxLifetime": MAX_SESSION_LIFETIME_S,
        },
        "environmentVariables": {
            "GATEWAY_URL": state["gateway"]["gateway_url"],
            "MODEL_PRICING_JSON": json.dumps(MODEL_PRICING_PER_1K_TOKENS),
            "GUARDRAIL_ID": webui.get("guardrail_id", ""),
            "GUARDRAIL_VERSION": webui.get("guardrail_version", "DRAFT"),
            "CLIENT_MAP_JSON": json.dumps(cmap),
            "AVAILABLE_MODELS_JSON": json.dumps(AVAILABLE_MODELS),
            "DEFAULT_MODEL_ID": MODEL_HAIKU,
            # ADOT (see agents/runtime_agent/Dockerfile).
            "AGENT_OBSERVABILITY_ENABLED": "true",
            "OTEL_PYTHON_DISTRO": "aws_distro",
            "OTEL_PYTHON_CONFIGURATOR": "aws_configurator",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_RESOURCE_ATTRIBUTES": f"service.name={RUNTIME_NAME}",
        },
        "description": "Multi-tenant shopping assistant; tenant from the inbound JWT only",
    }
    existing = None
    for page in ctl.get_paginator("list_agent_runtimes").paginate():
        for rt in page.get("agentRuntimes", []):
            if rt["agentRuntimeName"] == RUNTIME_NAME:
                existing = rt
    # A freshly written execution-role policy can take a few seconds to
    # propagate; the Runtime validates ECR access with it up front.
    for attempt in range(8):
        try:
            if existing:
                resp = ctl.update_agent_runtime(agentRuntimeId=existing["agentRuntimeId"], **params)
                log(f"updated runtime {RUNTIME_NAME} -> version {resp.get('agentRuntimeVersion')}")
            else:
                resp = ctl.create_agent_runtime(agentRuntimeName=RUNTIME_NAME, tags=tags_map(), **params)
                log(f"created runtime {RUNTIME_NAME}")
            break
        except ctl.exceptions.ValidationException as exc:
            if "Access denied while validating" not in str(exc) or attempt == 7:
                raise
            log("  execution role not propagated yet; retrying in 10s")
            time.sleep(10)
    runtime_id = resp["agentRuntimeId"]

    def ready():
        rt = ctl.get_agent_runtime(agentRuntimeId=runtime_id)
        log(f"  runtime status={rt['status']}")
        if rt["status"].endswith("FAILED"):
            raise RuntimeError(f"runtime {rt['status']}: {rt.get('failureReason')}")
        return rt if rt["status"] == "READY" else None

    return wait_until(ready, timeout_s=600, interval_s=10, what=f"runtime {runtime_id} READY")


def point_webui_backend(lam, runtime_arn: str) -> None:
    cfg = lam.get_function_configuration(FunctionName=WEBUI_FN)
    env = cfg.get("Environment", {}).get("Variables", {})
    if env.get("RUNTIME_ARN") == runtime_arn:
        log(f"{WEBUI_FN} already points at the runtime")
        return
    lam.update_function_configuration(FunctionName=WEBUI_FN, Environment={"Variables": {**env, "RUNTIME_ARN": runtime_arn}})
    log(f"{WEBUI_FN}: RUNTIME_ARN set (Lambda chat loop stays as the fallback)")


def main() -> None:
    state = load_state()
    acct = account_id()
    ecr = client("ecr")
    iam = client("iam")
    ctl = client("bedrock-agentcore-control")
    lam = client("lambda")

    tag = prepare_build_dir()
    repo_uri = ensure_repo(ecr)
    image = build_and_push(ecr, repo_uri, tag)
    role_arn = ensure_role(iam, acct, state["webui"]["guardrail_id"])
    rt = ensure_runtime(ctl, state, image, role_arn)
    point_webui_backend(lam, rt["agentRuntimeArn"])

    state["runtime"] = {
        "agent_runtime_arn": rt["agentRuntimeArn"],
        "agent_runtime_id": rt["agentRuntimeId"],
        "agent_runtime_version": rt.get("agentRuntimeVersion"),
        "image": image,
        "role_arn": role_arn,
        "ecr_repo": REPO_NAME,
        "log_group": f"/aws/bedrock-agentcore/runtimes/{rt['agentRuntimeId']}-DEFAULT",
    }
    save_state(state)
    log("Runtime setup complete.")
    log(f"  agent_runtime_arn={rt['agentRuntimeArn']}")


if __name__ == "__main__":
    main()
