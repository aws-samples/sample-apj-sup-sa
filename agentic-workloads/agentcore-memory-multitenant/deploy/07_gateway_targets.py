"""Step 7: the Gateway target -- exposes the tools Lambda's functions as MCP
tools for the shopping-assistant story: remember, remember_fact, recall,
list_memories, forget, lookup_order, check_inventory, search_products,
issue_refund, kb_search.

Every memory tool takes an optional `shopper_id` argument identifying the
individual shopper using the assistant (NOT a tenant-isolation field --
sanitized and re-injected server-side by the interceptor, see
lambda_src/interceptor/handler.py). If omitted, the interceptor defaults it
to "anonymous".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deploy.common import client, load_state, log, save_state, wait_until

TARGET_NAME = "retail-memory-tools"

SHOPPER_ID_PROPERTY = {
    "shopper_id": {
        "type": "string",
        "description": "Identifies the individual shopper (e.g. a stable per-browser id). "
        "Partitions memory within this tenant/agent. Optional; defaults to 'anonymous'.",
    }
}

TOOL_DEFINITIONS = [
    {
        "name": "remember",
        "description": "Append a short-term conversational turn to this agent's memory. Over "
        "time this contributes to durable long-term facts (extracted automatically). Use for "
        "ongoing conversation context, not for facts you need back immediately -- use "
        "remember_fact for that.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The conversational turn to store."},
                "role": {"type": "string", "description": "Who said it: USER or ASSISTANT."},
                "session_id": {"type": "string", "description": "Conversation/session identifier."},
                "scope": {
                    "type": "string",
                    "description": "'shared' (tenant-wide, visible to all this tenant's agents) or "
                    "'private' (only this agent). Defaults to 'private'.",
                },
                **SHOPPER_ID_PROPERTY,
            },
            "required": ["text"],
        },
    },
    {
        "name": "remember_fact",
        "description": "Store one durable shopper fact immediately (synchronous, no extraction "
        "delay) -- e.g. a preferred size, budget, or style note. Use this when you need the fact "
        "to be recallable right away.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "The fact to remember, as a short statement."},
                "scope": {
                    "type": "string",
                    "description": "'shared' (tenant-wide, visible to all this tenant's agents for this "
                    "shopper) or 'private' (only this agent). Defaults to 'private'.",
                },
                **SHOPPER_ID_PROPERTY,
            },
            "required": ["fact"],
        },
    },
    {
        "name": "recall",
        "description": "Semantically search this shopper's memory for relevant facts (preferences, "
        "sizes, budget, past interactions).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "scope": {
                    "type": "string",
                    "description": "'shared', 'private', or 'both' (default 'both').",
                },
                "top_k": {"type": "integer", "description": "Max results to return (default 5)."},
                **SHOPPER_ID_PROPERTY,
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_memories",
        "description": "List all stored memory facts for this shopper, in this tenant/agent, "
        "without a search query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "'shared', 'private', or 'both' (default 'both')."},
                **SHOPPER_ID_PROPERTY,
            },
        },
    },
    {
        "name": "forget",
        "description": "Delete one memory record by its id (from a prior recall/list_memories result).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_record_id": {"type": "string", "description": "The memory record id to delete."},
                **SHOPPER_ID_PROPERTY,
            },
            "required": ["memory_record_id"],
        },
    },
    {
        "name": "lookup_order",
        "description": "Look up a shopper's order status by order id, for this tenant's catalog.",
        "inputSchema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "The order id, e.g. NW-1001."}},
            "required": ["order_id"],
        },
    },
    {
        "name": "check_inventory",
        "description": "Check stock/size availability for a SKU, for this tenant's catalog.",
        "inputSchema": {
            "type": "object",
            "properties": {"sku": {"type": "string", "description": "The product SKU, e.g. NW-JKT-001."}},
            "required": ["sku"],
        },
    },
    {
        "name": "search_products",
        "description": "Search this tenant's product catalog by keyword (e.g. 'jacket', 'parka'). "
        "Use this for product discovery before recommending a specific SKU.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Keywords to search for."}},
            "required": ["query"],
        },
    },
    {
        "name": "kb_search",
        "description": "Search this brand's store-policy knowledge base: returns and refunds, "
        "exchanges, sizing and fit, and shipping. Returns passages, each with a `cite` tag "
        "(e.g. 'returns-policy §1'). Use it for any policy question and answer only from the "
        "passages, citing them.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The policy question, in the shopper's words."},
                "top_k": {"type": "integer", "description": "Max passages to return (1-5, default 3)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "issue_refund",
        "description": "Issue a refund for an order. THIS IS A RISKY ACTION: calling it WITHOUT "
        "approved=true only returns a proposed action for shopper confirmation and changes nothing. "
        "Only call it again with approved=true AFTER the shopper has explicitly confirmed in the UI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The order id to refund."},
                "amount": {"type": "number", "description": "The refund amount in USD."},
                "approved": {"type": "boolean", "description": "Only set true after explicit shopper confirmation."},
            },
            "required": ["order_id", "amount"],
        },
    },
]


def ensure_target(ctl, gateway_id: str, tools_arn: str, state: dict) -> str:
    existing_id = state.get("gateway", {}).get("target_id")
    if existing_id:
        try:
            resp = ctl.get_gateway_target(gatewayIdentifier=gateway_id, targetId=existing_id)
            log(f"target exists: {existing_id} status={resp['status']}")
            # keep tool schema in sync on re-runs
            ctl.update_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=existing_id,
                name=TARGET_NAME,
                targetConfiguration={"mcp": {"lambda": {"lambdaArn": tools_arn, "toolSchema": {"inlinePayload": TOOL_DEFINITIONS}}}},
                credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            )
            log("  tool schema refreshed")
            return existing_id
        except Exception:
            pass

    for page in ctl.get_paginator("list_gateway_targets").paginate(gatewayIdentifier=gateway_id):
        for t in page.get("items", []):
            if t.get("name") == TARGET_NAME:
                log(f"found existing target by name: {t['targetId']}")
                return t["targetId"]

    resp = ctl.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=TARGET_NAME,
        description="Shopping-assistant memory tools + mock retail API tools (Lambda target)",
        targetConfiguration={
            "mcp": {"lambda": {"lambdaArn": tools_arn, "toolSchema": {"inlinePayload": TOOL_DEFINITIONS}}}
        },
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )
    target_id = resp["targetId"]
    log(f"created target: {target_id}")
    return target_id


def wait_for_ready(ctl, gateway_id: str, target_id: str) -> dict:
    def check():
        t = ctl.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        log(f"  target status={t['status']}")
        if t["status"] == "FAILED":
            raise RuntimeError(f"Gateway target creation FAILED: {t}")
        return t if t["status"] in ("READY", "ACTIVE") else None

    return wait_until(check, timeout_s=180, interval_s=5, what=f"target {target_id} READY")


def main():
    ctl = client("bedrock-agentcore-control")
    state = load_state()
    gateway_id = state["gateway"]["gateway_id"]
    tools_arn = state["lambdas"]["tools_arn"]

    target_id = ensure_target(ctl, gateway_id, tools_arn, state)
    target = wait_for_ready(ctl, gateway_id, target_id)

    state["gateway"]["target_id"] = target_id
    state["gateway"]["target_name"] = TARGET_NAME
    save_state(state)
    log("Gateway target setup complete.")
    log(f"  target_id={target_id}")
    log(f"  tools: {[t['name'] for t in TOOL_DEFINITIONS]}")


if __name__ == "__main__":
    main()
