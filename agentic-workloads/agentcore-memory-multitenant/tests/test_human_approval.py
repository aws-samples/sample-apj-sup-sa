"""Human-approval-gated risky action: `issue_refund`
must NEVER actually process anything on its first call -- it only returns
a proposed action. Only a SECOND call with approved=True (which only the
backend sends, after the UI's explicit confirm click) actually "processes"
it. These tests call the tool directly over MCP (bypassing the LLM/UI
layers) to prove the server-side gate itself, not just that the demo UI
happens to behave -- a malicious or buggy client cannot skip the approval
step by omitting it, since omission IS the safe (unapproved) path.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.conftest import TOOL_PREFIX  # noqa: E402
from tests.mcp_client import call_tool, mcp_session  # noqa: E402


async def test_issue_refund_without_approval_does_not_process(tokens, gateway_url):
    token = tokens.for_tenant_agent("northwind", "support")
    async with mcp_session(gateway_url, token) as session:
        resp = await call_tool(
            session, TOOL_PREFIX + "issue_refund", {"order_id": "NW-1001", "amount": 49.99}
        )
    assert resp["ok"] is False, resp
    assert resp["requires_approval"] is True, resp
    assert resp["proposed_action"]["order_id"] == "NW-1001"
    assert "refund_id" not in resp, f"a refund_id appearing here would mean it processed without approval: {resp}"


async def test_issue_refund_with_approval_processes(tokens, gateway_url):
    token = tokens.for_tenant_agent("northwind", "support")
    async with mcp_session(gateway_url, token) as session:
        resp = await call_tool(
            session,
            TOOL_PREFIX + "issue_refund",
            {"order_id": "NW-1001", "amount": 49.99, "approved": True},
        )
    assert resp["ok"] is True, resp
    assert resp["requires_approval"] is False, resp
    assert resp["refund_id"].startswith("RFND-NW-1001-"), resp


async def test_issue_refund_unknown_order_rejected_either_way(tokens, gateway_url):
    token = tokens.for_tenant_agent("northwind", "support")
    fake_order = f"NW-{uuid.uuid4().hex[:6]}"
    async with mcp_session(gateway_url, token) as session:
        resp = await call_tool(
            session, TOOL_PREFIX + "issue_refund", {"order_id": fake_order, "amount": 10, "approved": True}
        )
    assert resp["ok"] is False, resp
    assert "not found" in resp.get("error", ""), resp
