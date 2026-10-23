"""Scenario 6: a real Strands agent, per tenant/agent,
connects to the Gateway's MCP endpoint with its own JWT and has a natural
conversation. This proves memory carries across sessions (a brand-new Agent
instance = a fresh conversation, but the backend memory persists) and that
Borealis's Order Support agent can recall what the Shopping Assistant
stored for the SAME shopper via shared memory -- using natural language,
not direct tool calls.

Each new `tenant_agent(...)` context below is a brand-new strands Agent
with an empty message history -- i.e. a new "session" from the LLM's point
of view. Anything it recalls must come from the persistent backend memory,
not from conversation history. Each test uses its own fresh, unique
shopper_id (passed to the agent via the system prompt) so tests never
collide with each other's data.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.strands_tenant_agent import tenant_agent  # noqa: E402
from tests.conftest import TOOL_PREFIX  # noqa: E402
from tests.mcp_client import call_tool, mcp_session, poll_until_text_present  # noqa: E402


def _unique(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:10]}"


def _shopper_id() -> str:
    return f"shopper{uuid.uuid4().hex[:12]}"


async def _wait_for_indexed(gateway_url: str, token: str, scope: str, shopper_id: str, marker: str) -> None:
    """Poll via a direct (non-LLM) tool call until `marker` shows up in
    list_memories, so the next agent turn's recall doesn't race the AWS-side
    indexing lag documented in docs/EVIDENCE.md.

    AgentCore Memory's list/search visibility was observed to be NOT
    monotonic while converging (a read can see a new record, then a
    subsequent read from a different connection can briefly miss it again --
    classic multi-replica eventual consistency). A single "it showed up
    once" check is therefore not enough; we add a fixed settle buffer after
    first detection before letting the next turn proceed.
    """
    import asyncio

    async with mcp_session(gateway_url, token) as session:
        found, last = await poll_until_text_present(
            lambda: call_tool(session, TOOL_PREFIX + "list_memories", {"scope": scope, "shopper_id": shopper_id}),
            marker,
        )
    assert found, f"setup: marker never got indexed for scope={scope}: {last}"
    await asyncio.sleep(45)  # settle buffer for broader replica convergence


async def test_agent_e2e_memory_across_sessions_northwind(tokens, gateway_url):
    """A Northwind Shopping Assistant conversation in one 'session' stores a
    preference; a brand-new agent 'session' later recalls it in natural
    language."""
    token = tokens.for_tenant_agent("northwind", "support")
    shopper_id = _shopper_id()
    marker = _unique("pref-email-contact")

    system_prompt = (
        "You are the Shopping Assistant for Northwind Apparel Co. The current shopper's id is "
        f"'{shopper_id}' -- always pass shopper_id='{shopper_id}' when calling any memory tool. "
        "When a shopper tells you a preference or fact to remember, call the remember_fact tool "
        "with scope='private'. Be concise."
    )

    # --- Session 1: store the preference ---
    with tenant_agent(gateway_url, token, system_prompt) as agent:
        result1 = agent(
            f"Please remember this exact note for my account, word for word: '{marker}'. "
            "Use the remember_fact tool now."
        )
        print("SESSION 1 RESPONSE:", str(result1)[:300])

    await _wait_for_indexed(gateway_url, token, "private", shopper_id, marker)

    # --- Session 2: brand-new Agent instance (empty history) recalls it ---
    with tenant_agent(gateway_url, token, system_prompt) as agent:
        result2 = agent(
            f"Use your list_memories tool (scope='private', shopper_id='{shopper_id}') and tell me the "
            "EXACT text of any note you find, quoting it verbatim."
        )
        print("SESSION 2 RESPONSE:", str(result2)[:500])

    assert marker in str(result2), (
        f"expected session 2 (new conversation) to recall the marker stored in session 1 via persistent "
        f"backend memory; got: {result2}"
    )


async def test_agent_e2e_borealis_cross_agent_natural_language(tokens, gateway_url):
    """Borealis's Shopping Assistant notes a shopper's size/budget (shared
    memory, scoped to that shopper); the Order Support agent, in a totally
    separate conversation, recalls it in natural language for the SAME
    shopper."""
    shop_token = tokens.for_tenant_agent("borealis", "merch")
    orders_token = tokens.for_tenant_agent("borealis", "support")
    shopper_id = _shopper_id()
    marker = _unique("size-budget")

    shop_prompt = (
        "You are the Shopping Assistant for Borealis Outfitters, helping with product discovery, "
        f"sizing, and recommendations. The current shopper's id is '{shopper_id}' -- always pass "
        f"shopper_id='{shopper_id}' when calling any memory tool. When a shopper tells you their size "
        "or budget, call remember_fact with scope='shared' (never 'private' for these, since Order "
        "Support needs to see them too). Be concise."
    )
    orders_prompt = (
        "You are the Order Support agent for Borealis Outfitters, handling order status and returns. "
        f"The current shopper's id is '{shopper_id}' -- always pass shopper_id='{shopper_id}' when "
        "calling any memory tool. Use list_memories with scope='shared' to check what the Shopping "
        "Assistant already learned about this shopper, and report it verbatim."
    )

    with tenant_agent(gateway_url, shop_token, shop_prompt) as shop_agent:
        result1 = shop_agent(
            f"Please call remember_fact now with scope='shared' and this EXACT fact text, word for "
            f"word, with no paraphrasing or shortening: 'Size M, budget $200, tag: {marker}'. Use "
            f"that precise string as the fact argument."
        )
        print("SHOPPING ASSISTANT RESPONSE:", str(result1)[:300])

    await _wait_for_indexed(gateway_url, shop_token, "shared", shopper_id, marker)

    with tenant_agent(gateway_url, orders_token, orders_prompt) as orders_agent:
        result2 = orders_agent("What has the Shopping Assistant already learned about me? Quote the exact note text.")
        print("ORDER SUPPORT RESPONSE:", str(result2)[:500])

    assert marker in str(result2), (
        f"expected the Order Support agent (separate conversation, separate JWT identity) to recall the "
        f"Shopping Assistant's SHARED note for the same shopper via natural language; got: {result2}"
    )
