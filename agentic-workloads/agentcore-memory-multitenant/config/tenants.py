"""
Single source of truth for the multi-tenant demo's tenant/agent topology.

Story: a multi-brand RETAIL SHOPPING ASSISTANT. Three synthetic retail
brands (tenants) share one AgentCore Gateway + one AgentCore Memory
resource. Tenant "borealis" has two agents -- a shopping assistant
(product discovery/sizing/recommendations) and order support
(post-purchase: status/returns) -- that share one tenant-wide,
per-shopper memory namespace and each also get an agent-private,
per-shopper namespace.

Agent ids are the stable `support`/`merch` identifiers (renaming them would require widening the Memory resource's `agentid`
namespaceKey `allowedValues`, which this repo treats as a deliberately
rare, disruptive change -- see deploy/03_memory.py). The *display*
names and system prompts carry the shopping-assistant story:
  - northwind/cascade `support`  -> displayed as "Shopping Assistant"
    (one agent covers discovery AND post-purchase for single-agent tenants)
  - borealis `merch`             -> displayed as "Shopping Assistant"
    (product discovery, sizing, recommendations)
  - borealis `support`           -> displayed as "Order Support"
    (order status, returns -- and sees what the Shopping Assistant learned)

Per-shopper scoping: a third namespaceKey `shopperid` partitions memory
within a tenant+agent by the individual shopper using the assistant.
actorId is `tenant-<tenant_id>-<shopper_id>` (per-shopper, not per-agent --
a shopper's identity is the same person whichever Borealis agent they are
talking to). The IAM actorId/namespace StringLike conditions
(`tenant-<tenant_id>-*` / `/tenant/<tenant_id>/*`) still match this shape
unchanged.

Every tenant id / agent id here must be lowercase ASCII letters only
(Memory namespaceKeys validation is lowercase-only), and must stay in sync
with the `allowedValues` set on the Memory resource's namespaceKeys
(see deploy/03_memory.py) and the Cognito resource-server scopes
(see deploy/01_cognito.py).
"""

from __future__ import annotations

import os
import re

PROJECT = "agentcore-mt-memory-demo"
# Deploy region, taken from the standard AWS environment variables. The model
# ids below are US cross-Region inference profiles: deploy to a us-* region,
# or switch them to the profiles for your geography.
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

# Chat models offered in the UI (Converse API).
MODEL_HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
MODEL_SONNET = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
MODEL_NOVA_LITE = "us.amazon.nova-lite-v1:0"

# Per-1,000-token USD list prices used for the UI's cost-per-request display.
# Source: Bedrock on-demand pricing as of Oct 2026 (us-east-1); kept here as
# plain constants rather than a live API call so the UI doesn't depend on an
# extra network hop for every message.
MODEL_PRICING_PER_1K_TOKENS = {
    MODEL_HAIKU: {"input": 0.001, "output": 0.005},
    MODEL_SONNET: {"input": 0.003, "output": 0.015},
    MODEL_NOVA_LITE: {"input": 0.00006, "output": 0.00024},
}

SHOPPER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DEFAULT_SHOPPER_ID = "anonymous"


def sanitize_shopper_id(raw: str | None) -> str:
    """Used by the interceptor Lambda to turn a caller-supplied shopper_id
    into something safe to embed in a Memory namespace path. shopper_id is
    NOT a tenant-isolation boundary (it only partitions data within the
    already tenant/agent-locked namespace), but it still must not be able
    to path-traverse within that namespace (e.g. '../other-agent/'), so it
    is validated against a strict allowlist pattern and defaulted
    otherwise."""
    if raw and SHOPPER_ID_PATTERN.match(raw):
        return raw
    return DEFAULT_SHOPPER_ID


# tenant_id -> { display_name, agents: { agent_id: display_name } }
TENANTS: dict[str, dict] = {
    "northwind": {
        "display_name": "Northwind Apparel Co.",
        "agents": {
            "support": "Shopping Assistant",
        },
    },
    "borealis": {
        "display_name": "Borealis Outfitters",
        "agents": {
            "support": "Order Support",
            "merch": "Shopping Assistant",
        },
    },
    "cascade": {
        "display_name": "Cascade Mart",
        "agents": {
            "support": "Shopping Assistant",
        },
    },
}

# Every (tenant, agent) pair that gets its own Cognito app client / JWT identity.
def all_tenant_agent_pairs() -> list[tuple[str, str]]:
    pairs = []
    for tenant_id, cfg in TENANTS.items():
        for agent_id in cfg["agents"]:
            pairs.append((tenant_id, agent_id))
    return pairs


def all_tenant_ids() -> list[str]:
    return list(TENANTS.keys())


def all_agent_ids() -> list[str]:
    """Distinct agent ids across all tenants, used for the 'agentid' namespaceKey
    allowedValues list (plus the literal 'shared' value)."""
    seen = set()
    for cfg in TENANTS.values():
        for agent_id in cfg["agents"]:
            seen.add(agent_id)
    return sorted(seen)


def scope_name(tenant_id: str, agent_id: str) -> str:
    """Cognito resource-server scope name for one (tenant, agent) pair."""
    return f"mem.{tenant_id}-{agent_id}"


def client_name(tenant_id: str, agent_id: str) -> str:
    return f"agentcore-mt-{tenant_id}-{agent_id}"


def actor_id(tenant_id: str, shopper_id: str) -> str:
    """actorId used for CreateEvent / ListEvents -- tenant-prefixed so IAM
    can scope it by tenant, and per-SHOPPER (not per-agent): a
    shopper is the same person whichever agent within the tenant they are
    talking to, so short-term conversational memory is keyed by shopper."""
    return f"tenant-{tenant_id}-{shopper_id}"


def shared_namespace(tenant_id: str, shopper_id: str) -> str:
    """Tenant-wide memory shared across all of this tenant's agents, scoped
    to one shopper."""
    return f"/tenant/{tenant_id}/shared/{shopper_id}/"


def private_namespace(tenant_id: str, agent_id: str, shopper_id: str) -> str:
    """One agent's private memory, scoped to one shopper."""
    return f"/tenant/{tenant_id}/{agent_id}/{shopper_id}/"


RESOURCE_SERVER_ID = "agentcore-mt-demo"
RESOURCE_SERVER_NAME = "AgentCore MT Memory Demo"

TAGS = {"project": PROJECT}
