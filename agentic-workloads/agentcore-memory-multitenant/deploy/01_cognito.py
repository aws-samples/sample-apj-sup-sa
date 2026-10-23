"""Step 1: Cognito user pool for M2M (client_credentials) auth.

Each (tenant, agent) pair gets its own app client, granted exactly one
resource-server scope named `mem.<tenant>-<agent>`. There are no human
users in this pool -- agents authenticate as machines via OAuth2
client_credentials, and the resulting access token's `client_id` claim is
how the Gateway's REQUEST interceptor derives tenant/agent identity
server-side (never from caller-supplied arguments).

We also provision one extra "attacker" app client with NO scopes granted,
used by the isolation tests to prove that a validly-issued-but-wrong-scope
token is rejected.

Idempotent: re-running reuses existing pool/clients by name.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import (
    PROJECT,
    RESOURCE_SERVER_ID,
    RESOURCE_SERVER_NAME,
    all_tenant_agent_pairs,
    client_name,
    scope_name,
)
from deploy.common import account_id, client, load_state, log, save_state, tags_map, wait_until

POOL_NAME = "agentcore-mt-demo-pool"
DOMAIN_PREFIX = f"agentcore-mt-demo-{account_id()}"[:63].lower()


def ensure_user_pool(idp) -> str:
    state = load_state()
    pool_id = state.get("cognito", {}).get("user_pool_id")
    if pool_id:
        try:
            idp.describe_user_pool(UserPoolId=pool_id)
            log(f"user pool exists: {pool_id}")
            return pool_id
        except idp.exceptions.ResourceNotFoundException:
            pass

    for page in idp.get_paginator("list_user_pools").paginate(MaxResults=60):
        for p in page["UserPools"]:
            if p["Name"] == POOL_NAME:
                log(f"found existing user pool by name: {p['Id']}")
                return p["Id"]

    resp = idp.create_user_pool(
        PoolName=POOL_NAME,
        UserPoolTags=tags_map(),
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 12,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
            }
        },
    )
    pool_id = resp["UserPool"]["Id"]
    log(f"created user pool: {pool_id}")
    return pool_id


def ensure_domain(idp, pool_id: str) -> str:
    try:
        existing = idp.describe_user_pool_domain(Domain=DOMAIN_PREFIX)
        status = existing.get("DomainDescription", {}).get("Status")
        if status and status != "DELETING":
            log(f"domain exists: {DOMAIN_PREFIX} status={status}")
        else:
            raise idp.exceptions.ResourceNotFoundException({}, "recreate")
    except Exception:
        idp.create_user_pool_domain(Domain=DOMAIN_PREFIX, UserPoolId=pool_id)
        log(f"created domain: {DOMAIN_PREFIX}")

    def check():
        d = idp.describe_user_pool_domain(Domain=DOMAIN_PREFIX)
        status = d.get("DomainDescription", {}).get("Status")
        log(f"  domain status={status}")
        return status == "ACTIVE"

    wait_until(check, timeout_s=300, interval_s=10, what=f"domain {DOMAIN_PREFIX} ACTIVE")
    return DOMAIN_PREFIX


def ensure_resource_server(idp, pool_id: str) -> list[str]:
    scopes = [scope_name(t, a) for t, a in all_tenant_agent_pairs()]
    scopes_with_extra = scopes + ["mem.attacker-unmapped"]
    scope_defs = [{"ScopeName": s, "ScopeDescription": f"Access scope {s}"} for s in scopes_with_extra]
    try:
        idp.describe_resource_server(UserPoolId=pool_id, Identifier=RESOURCE_SERVER_ID)
        idp.update_resource_server(
            UserPoolId=pool_id,
            Identifier=RESOURCE_SERVER_ID,
            Name=RESOURCE_SERVER_NAME,
            Scopes=scope_defs,
        )
        log(f"updated resource server {RESOURCE_SERVER_ID} with {len(scope_defs)} scopes")
    except idp.exceptions.ResourceNotFoundException:
        idp.create_resource_server(
            UserPoolId=pool_id,
            Identifier=RESOURCE_SERVER_ID,
            Name=RESOURCE_SERVER_NAME,
            Scopes=scope_defs,
        )
        log(f"created resource server {RESOURCE_SERVER_ID} with {len(scope_defs)} scopes")
    return scopes


def ensure_app_client(idp, pool_id: str, name: str, allowed_scope: str | None) -> dict:
    existing_id = None
    for page in idp.get_paginator("list_user_pool_clients").paginate(UserPoolId=pool_id, MaxResults=60):
        for c in page["UserPoolClients"]:
            if c["ClientName"] == name:
                existing_id = c["ClientId"]
                break
        if existing_id:
            break

    allowed_scopes = [f"{RESOURCE_SERVER_ID}/{allowed_scope}"] if allowed_scope else []

    if existing_id:
        idp.update_user_pool_client(
            UserPoolId=pool_id,
            ClientId=existing_id,
            ClientName=name,
            AllowedOAuthFlows=["client_credentials"],
            AllowedOAuthFlowsUserPoolClient=True,
            AllowedOAuthScopes=allowed_scopes,
            SupportedIdentityProviders=["COGNITO"],
            TokenValidityUnits={"AccessToken": "minutes"},
            AccessTokenValidity=60,
        )
        desc = idp.describe_user_pool_client(UserPoolId=pool_id, ClientId=existing_id)["UserPoolClient"]
        log(f"updated app client {name}: {existing_id}")
        return {"client_id": existing_id, "client_secret": desc.get("ClientSecret")}

    resp = idp.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=name,
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthFlowsUserPoolClient=True,
        AllowedOAuthScopes=allowed_scopes,
        SupportedIdentityProviders=["COGNITO"],
        TokenValidityUnits={"AccessToken": "minutes"},
        AccessTokenValidity=60,
    )
    client_id = resp["UserPoolClient"]["ClientId"]
    client_secret = resp["UserPoolClient"]["ClientSecret"]
    log(f"created app client {name}: {client_id}")
    return {"client_id": client_id, "client_secret": client_secret}


def main():
    idp = client("cognito-idp")
    state = load_state()
    cognito_state = state.get("cognito", {})

    pool_id = ensure_user_pool(idp)
    domain = ensure_domain(idp, pool_id)
    scopes = ensure_resource_server(idp, pool_id)

    clients = cognito_state.get("clients", {})
    for tenant_id, agent_id in all_tenant_agent_pairs():
        name = client_name(tenant_id, agent_id)
        info = ensure_app_client(idp, pool_id, name, scope_name(tenant_id, agent_id))
        clients[f"{tenant_id}:{agent_id}"] = {
            "client_id": info["client_id"],
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "scope": scope_name(tenant_id, agent_id),
            # secret deliberately NOT stored in state.json; fetched live by tests/ via
            # describe_user_pool_client when needed, using the deployer's credentials.
        }

    attacker_info = ensure_app_client(idp, pool_id, "agentcore-mt-attacker-no-scope", "mem.attacker-unmapped")
    clients["attacker:none"] = {
        "client_id": attacker_info["client_id"],
        "tenant_id": None,
        "agent_id": None,
        "scope": None,
    }

    region = idp.meta.region_name
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
    token_endpoint = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"

    state["cognito"] = {
        "user_pool_id": pool_id,
        "domain": domain,
        "issuer": issuer,
        "token_endpoint": token_endpoint,
        "resource_server_id": RESOURCE_SERVER_ID,
        "scopes": scopes,
        "clients": clients,
    }
    save_state(state)
    log("Cognito setup complete.")
    log(f"  user_pool_id={pool_id}")
    log(f"  issuer={issuer}")
    log(f"  token_endpoint={token_endpoint}")
    log(f"  app clients: {len(clients)}")


if __name__ == "__main__":
    main()
