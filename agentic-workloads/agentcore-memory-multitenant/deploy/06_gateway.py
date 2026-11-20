"""Step 6: the AgentCore Gateway itself.

- authorizerType=CUSTOM_JWT against the Cognito user pool's OIDC discovery
  document, with allowedClients restricted to exactly the 4 tenant/agent app
  clients (the attacker/no-scope client is deliberately NOT in this list --
  its token is rejected by the authorizer before any interceptor or target
  runs, which is the "token from another/unauthorized client" attack test).
- One REQUEST interceptor (the Lambda in lambda_src/interceptor), with
  passRequestHeaders=true so it can read the Authorization header and derive
  tenant/agent context server-side.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deploy.common import client, load_state, log, save_state, tags_map, wait_until

GATEWAY_NAME = "agentcore-mt-demo-gateway"


def allowed_client_ids(state: dict) -> list[str]:
    return [
        info["client_id"]
        for info in state["cognito"]["clients"].values()
        if info.get("tenant_id") is not None  # excludes the attacker client
    ]


def ensure_gateway(ctl, state: dict) -> str:
    existing_id = state.get("gateway", {}).get("gateway_id")
    if existing_id:
        try:
            resp = ctl.get_gateway(gatewayIdentifier=existing_id)
            log(f"gateway exists: {existing_id} status={resp['status']}")
            return existing_id
        except Exception:
            pass

    for page in ctl.get_paginator("list_gateways").paginate():
        for g in page.get("items", []):
            if g.get("name") == GATEWAY_NAME:
                log(f"found existing gateway by name: {g['gatewayId']}")
                return g["gatewayId"]

    resp = ctl.create_gateway(
        name=GATEWAY_NAME,
        description="Multi-tenant retail memory demo gateway (MCP, Lambda target, CUSTOM_JWT auth)",
        roleArn=state["iam"]["gateway_role_arn"],
        protocolType="MCP",
        protocolConfiguration={"mcp": {"supportedVersions": ["2025-03-26"], "searchType": "SEMANTIC"}},
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": f"{state['cognito']['issuer']}/.well-known/openid-configuration",
                "allowedClients": allowed_client_ids(state),
            }
        },
        interceptorConfigurations=[
            {
                "interceptor": {"lambda": {"arn": state["lambdas"]["interceptor_arn"]}},
                "interceptionPoints": ["REQUEST"],
                "inputConfiguration": {"passRequestHeaders": True},
            }
        ],
        exceptionLevel="DEBUG",
        tags=tags_map(),
    )
    gateway_id = resp["gatewayId"]
    log(f"created gateway: {gateway_id}")
    return gateway_id


def wait_for_ready(ctl, gateway_id: str) -> dict:
    def check():
        g = ctl.get_gateway(gatewayIdentifier=gateway_id)
        log(f"  gateway status={g['status']}")
        if g["status"] == "FAILED":
            raise RuntimeError(f"Gateway creation FAILED: {g}")
        return g if g["status"] in ("READY", "ACTIVE") else None

    return wait_until(check, timeout_s=300, interval_s=10, what=f"gateway {gateway_id} READY")


def main():
    ctl = client("bedrock-agentcore-control")
    state = load_state()

    gateway_id = ensure_gateway(ctl, state)
    gateway = wait_for_ready(ctl, gateway_id)

    state["gateway"] = {
        "gateway_id": gateway_id,
        "gateway_arn": gateway.get("gatewayArn"),
        "gateway_url": gateway.get("gatewayUrl"),
    }
    save_state(state)
    log("Gateway setup complete.")
    log(f"  gateway_id={gateway_id}")
    log(f"  gateway_url={gateway.get('gatewayUrl')}")


if __name__ == "__main__":
    main()
