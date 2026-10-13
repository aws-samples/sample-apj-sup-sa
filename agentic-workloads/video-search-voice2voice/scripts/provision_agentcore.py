#!/usr/bin/env python
"""Provision the AgentCore harness that gives the voice agent outside knowledge.

Replaces the third-party search API with AWS-native infrastructure:

    voice agent (find_moment / look_closer / ask_the_world)
                      │  ask_the_world
                      ▼
        AgentCore Harness  ── managed agent loop, own VM, session state
                      │      model: Claude Haiku 4.5 on Bedrock
                      ▼
        AgentCore Gateway  ── MCP surface, AWS_IAM inbound auth
                      │
                      ▼
        Web Search connector ("web-search") — Amazon's own web index;
        queries are served inside AWS and never reach a third party.

What this creates (all idempotent, safe to re-run):
  * IAM role for the gateway  — InvokeGateway + InvokeWebSearch
  * IAM role for the harness  — InvokeGateway + Bedrock model access + memory
  * AgentCore Gateway (MCP, AWS_IAM) + web-search connector target
  * AgentCore Harness wired to that gateway

Usage:
    uv run python scripts/provision_agentcore.py             # create / reuse
    uv run python scripts/provision_agentcore.py --verify    # live test search
    uv run python scripts/provision_agentcore.py --teardown  # delete everything
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from talk2vid import config  # noqa: E402

GATEWAY_NAME = "talk2vid-web"
GATEWAY_TARGET_NAME = "web-search"
HARNESS_NAME = "talk2vid_world"  # must match [a-zA-Z][a-zA-Z0-9_]{0,39}
GATEWAY_ROLE = "Talk2VidGatewayRole"
HARNESS_ROLE = "Talk2VidHarnessRole"
WEB_SEARCH_TOOL_ARN = "arn:aws:bedrock-agentcore:us-east-1:aws:tool/web-search.v1"

HARNESS_SYSTEM_PROMPT = """You are the research arm of a voice assistant that answers questions \
about videos. You are called when the answer is not in the video and must come from the outside \
world.

Use web search to find the answer, then reply with the answer itself — two or three sentences of \
plain prose that will be read aloud. No markdown, no bullet points, no URLs, no preamble like \
"Based on my search". Lead with the fact. If sources disagree or you cannot find it, say so in one \
sentence. Include a date or figure when it is what makes the answer useful."""


# --------------------------------------------------------------------------- #
# IAM
# --------------------------------------------------------------------------- #
def trust_policy(account: str, region: str) -> dict:
    """Let AgentCore assume the role, scoped to this account and region."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"},
                },
            }
        ],
    }


def gateway_policy(account: str, region: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeGateway",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeGateway",
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account}:gateway/*",
            },
            {
                # Resource is owned by AWS (account "aws"); authorization for web
                # search is enforced per invocation against this exact ARN.
                "Sid": "InvokeWebSearch",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeWebSearch",
                "Resource": WEB_SEARCH_TOOL_ARN,
            },
        ],
    }


def harness_policy(account: str, region: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeGateway",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeGateway",
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account}:gateway/*",
            },
            {
                "Sid": "ModelAccess",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    f"arn:aws:bedrock:{region}:{account}:inference-profile/*",
                    "arn:aws:bedrock:*::foundation-model/*",
                ],
            },
            {
                # The harness keeps session state in managed AgentCore Memory.
                "Sid": "SessionMemory",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:GetMemoryRecord",
                    "bedrock-agentcore:ListSessions",
                    "bedrock-agentcore:ListActors",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account}:memory/*",
            },
            {
                "Sid": "Observability",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/*",
            },
        ],
    }


def ensure_role(iam, name: str, trust: dict, policy: dict, description: str) -> str:
    try:
        arn = iam.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description=description,
        )["Role"]["Arn"]
        print(f"  created role {name}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        print(f"  reusing role {name}")
    iam.put_role_policy(
        RoleName=name, PolicyName=f"{name}Policy", PolicyDocument=json.dumps(policy)
    )
    return arn


# --------------------------------------------------------------------------- #
# AgentCore
# --------------------------------------------------------------------------- #
def find_by_name(items: list[dict], name: str, key: str) -> dict | None:
    return next((i for i in items if i.get(key) == name), None)


def wait_ready(fetch, label: str, timeout: float = 240.0) -> dict:
    """Poll until the resource leaves its transitional state."""
    deadline = time.time() + timeout
    while True:
        item = fetch()
        status = (item.get("status") or "").upper()
        if status in ("READY", "AVAILABLE", "ACTIVE", ""):
            return item
        if "FAIL" in status or status in ("DELETING", "DELETED"):
            raise RuntimeError(f"{label} entered {status}: {item.get('statusReasons') or item}")
        if time.time() > deadline:
            raise TimeoutError(f"{label} still {status} after {timeout:.0f}s")
        time.sleep(4)


def retrying(call, *, attempts: int = 8, delay: float = 5.0, label: str = ""):
    """IAM roles are eventually consistent; a fresh role often is not usable yet."""
    for attempt in range(attempts):
        try:
            return call()
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            message = str(exc)
            transient = code in ("ValidationException", "AccessDeniedException") and (
                "role" in message.lower() or "assume" in message.lower()
            )
            if not transient or attempt == attempts - 1:
                raise
            print(f"  waiting for IAM propagation ({label}, attempt {attempt + 1})")
            time.sleep(delay)


def ensure_gateway(cc, role_arn: str) -> dict:
    existing = find_by_name(cc.list_gateways().get("items", []), GATEWAY_NAME, "name")
    if existing:
        gateway = cc.get_gateway(gatewayIdentifier=existing["gatewayId"])
        print(f"  reusing gateway {GATEWAY_NAME} ({gateway['status']})")
    else:
        gateway = retrying(
            lambda: cc.create_gateway(
                name=GATEWAY_NAME,
                description="Talk2Vid outside-world knowledge (AgentCore Web Search)",
                roleArn=role_arn,
                protocolType="MCP",
                authorizerType="AWS_IAM",
            ),
            label="create_gateway",
        )
        print(f"  created gateway {GATEWAY_NAME}")
    gateway_id = gateway["gatewayId"]
    gateway = wait_ready(lambda: cc.get_gateway(gatewayIdentifier=gateway_id), "gateway")

    targets = cc.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
    target = find_by_name(targets, GATEWAY_TARGET_NAME, "name")
    if target:
        print(f"  reusing web-search target ({target.get('status')})")
    else:
        cc.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=GATEWAY_TARGET_NAME,
            description="Amazon's own web index, served inside AWS",
            targetConfiguration={
                "mcp": {
                    "connector": {
                        "source": {"connectorId": "web-search"},
                        "configurations": [{"name": "WebSearch", "parameterValues": {}}],
                    }
                }
            },
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        print("  created web-search target")
    def target_status() -> dict:
        items = cc.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        return find_by_name(items, GATEWAY_TARGET_NAME, "name") or {}

    wait_ready(target_status, "gateway target")
    return gateway


def ensure_harness(cc, role_arn: str, gateway_arn: str) -> dict:
    tools = [
        {
            "type": "agentcore_gateway",
            "name": "web",
            "config": {
                "agentCoreGateway": {"gatewayArn": gateway_arn, "outboundAuth": {"awsIam": {}}}
            },
        }
    ]
    spec = dict(
        model={
            "bedrockModelConfig": {
                "modelId": config.LLM_MODEL_ID,
                "maxTokens": 800,
                "temperature": 0.2,
                "apiFormat": "converse_stream",
            }
        },
        systemPrompt=[{"text": HARNESS_SYSTEM_PROMPT}],
        tools=tools,
        # Only the gateway's tools: the default shell and file_operations tools
        # would add ~900 input tokens per request for no benefit here.
        allowedTools=["@web"],
        maxIterations=4,
        maxTokens=800,
        timeoutSeconds=45,
    )

    # Note the harness APIs wrap their payload in a "harness" key and name the
    # ARN field "arn", unlike the gateway APIs.
    existing = find_by_name(
        cc.list_harnesses().get("harnesses", []), HARNESS_NAME, "harnessName"
    )
    if existing:
        harness_id = existing["harnessId"]
        # An update is rejected while the harness is still CREATING/UPDATING.
        wait_ready(lambda: cc.get_harness(harnessId=harness_id)["harness"], "harness")
        cc.update_harness(harnessId=harness_id, executionRoleArn=role_arn, **spec)
        print(f"  updated harness {HARNESS_NAME}")
    else:
        created = retrying(
            lambda: cc.create_harness(
                harnessName=HARNESS_NAME, executionRoleArn=role_arn, **spec
            ),
            label="create_harness",
        )
        harness_id = created["harness"]["harnessId"]
        print(f"  created harness {HARNESS_NAME}")
    return wait_ready(lambda: cc.get_harness(harnessId=harness_id)["harness"], "harness")


# --------------------------------------------------------------------------- #
# Verify / teardown
# --------------------------------------------------------------------------- #
def verify(harness_arn: str) -> bool:
    from talk2vid import agentcore

    question = "What is Amazon Bedrock AgentCore, in two sentences?"
    print(f"  test question: {question}")
    t0 = time.time()
    result = agentcore.ask_world_sync(question, session_id="provision-verify-000000000000")
    print(f"  took {time.time() - t0:.1f}s")
    print(f"  tools used: {result.get('tools_used')}")
    print(f"  answer: {result.get('answer', '')[:400]}")
    return bool(result.get("answer"))


def teardown(cc, iam) -> None:
    harness = find_by_name(
        cc.list_harnesses().get("harnesses", []), HARNESS_NAME, "harnessName"
    )
    if harness:
        cc.delete_harness(harnessId=harness["harnessId"])
        print(f"  deleted harness {HARNESS_NAME}")
    gateway = find_by_name(cc.list_gateways().get("items", []), GATEWAY_NAME, "name")
    if gateway:
        gid = gateway["gatewayId"]
        for target in cc.list_gateway_targets(gatewayIdentifier=gid).get("items", []):
            cc.delete_gateway_target(gatewayIdentifier=gid, targetId=target["targetId"])
            print(f"  deleted target {target['name']}")
        time.sleep(5)
        cc.delete_gateway(gatewayIdentifier=gid)
        print(f"  deleted gateway {GATEWAY_NAME}")
    for role in (HARNESS_ROLE, GATEWAY_ROLE):
        try:
            iam.delete_role_policy(RoleName=role, PolicyName=f"{role}Policy")
            iam.delete_role(RoleName=role)
            print(f"  deleted role {role}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchEntity":
                raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="run a live test search and exit")
    ap.add_argument("--teardown", action="store_true", help="delete the harness, gateway and roles")
    args = ap.parse_args()

    region = config.AWS_REGION
    if region != "us-east-1":
        print(f"note: AgentCore Web Search is us-east-1 only (region is {region})")
    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    cc = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam")

    if args.teardown:
        print("tearing down AgentCore resources")
        teardown(cc, iam)
        return 0

    if args.verify:
        if not config.AGENTCORE_HARNESS_ARN:
            print("TALK2VID_HARNESS_ARN is not set in .env", file=sys.stderr)
            return 1
        print("verifying harness")
        return 0 if verify(config.AGENTCORE_HARNESS_ARN) else 1

    print(f"account={account} region={region}")
    print("iam roles:")
    gateway_role = ensure_role(
        iam, GATEWAY_ROLE, trust_policy(account, region), gateway_policy(account, region),
        "Talk2Vid AgentCore gateway: outbound auth to the Web Search connector",
    )
    harness_role = ensure_role(
        iam, HARNESS_ROLE, trust_policy(account, region), harness_policy(account, region),
        "Talk2Vid AgentCore harness execution role",
    )

    print("gateway:")
    gateway = ensure_gateway(cc, gateway_role)
    print("harness:")
    harness = ensure_harness(cc, harness_role, gateway["gatewayArn"])

    print("\nlive check:")
    ok = verify(harness["arn"])

    print("\nAdd this to .env:")
    print(f"TALK2VID_HARNESS_ARN={harness['arn']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
