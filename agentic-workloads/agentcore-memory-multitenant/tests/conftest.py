"""Shared pytest fixtures: AWS clients, deploy state, and cached JWTs per
(tenant, agent) app client. No secrets are written anywhere -- Cognito
client secrets are fetched live via describe_user_pool_client and kept only
in memory for the pytest process lifetime.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.tenants import REGION  # noqa: E402
from tests.mcp_client import mint_token  # noqa: E402

TOOL_PREFIX = "retail-memory-tools___"


@pytest.fixture(scope="session")
def state() -> dict:
    path = ROOT / "deploy" / "state.json"
    if not path.exists():
        pytest.skip("needs a deployed stack (deploy/state.json); run deploy/deploy_all.py first")
    return json.loads(path.read_text())


@pytest.fixture(scope="session")
def idp():
    return boto3.client("cognito-idp", region_name=REGION)


@pytest.fixture(scope="session")
def iam():
    return boto3.client("iam", region_name=REGION)


@pytest.fixture(scope="session")
def sts():
    return boto3.client("sts", region_name=REGION)


@pytest.fixture(scope="session")
def bac():
    return boto3.client("bedrock-agentcore", region_name=REGION)


@pytest.fixture(scope="session")
def gateway_url(state) -> str:
    return state["gateway"]["gateway_url"]


class TokenFactory:
    def __init__(self, state: dict, idp) -> None:
        self.state = state
        self.idp = idp
        self._cache: dict[str, str] = {}

    def _client_secret(self, client_id: str) -> str:
        return self.idp.describe_user_pool_client(
            UserPoolId=self.state["cognito"]["user_pool_id"], ClientId=client_id
        )["UserPoolClient"]["ClientSecret"]

    def for_tenant_agent(self, tenant_id: str, agent_id: str) -> str:
        key = f"{tenant_id}:{agent_id}"
        if key not in self._cache:
            info = self.state["cognito"]["clients"][key]
            secret = self._client_secret(info["client_id"])
            scope = f"agentcore-mt-demo/{info['scope']}"
            self._cache[key] = mint_token(self.state["cognito"]["token_endpoint"], info["client_id"], secret, scope)
        return self._cache[key]

    def for_client_key(self, key: str, scope_override: str | None = None) -> str:
        if key not in self._cache:
            info = self.state["cognito"]["clients"][key]
            secret = self._client_secret(info["client_id"])
            scope = scope_override or (f"agentcore-mt-demo/{info['scope']}" if info["scope"] else None)
            self._cache[key] = mint_token(self.state["cognito"]["token_endpoint"], info["client_id"], secret, scope)
        return self._cache[key]


@pytest.fixture(scope="session")
def tokens(state, idp) -> TokenFactory:
    return TokenFactory(state, idp)


TENANT_AGENTS = [
    ("northwind", "support"),
    ("borealis", "support"),
    ("borealis", "merch"),
    ("cascade", "support"),
]
