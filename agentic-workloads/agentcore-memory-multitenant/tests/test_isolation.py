"""Automated validation of the multi-tenant isolation demo (shopping
assistant story).

Run with: pytest -v  (see scripts/run_validation.sh for the one-command
wrapper with a pass/fail summary and a non-zero exit code on failure).

Covers:
  1. Each tenant writes/reads its own memory through the Gateway with a real JWT.
  2. Tenant B agent1 writes shared memory, agent2 reads it (cross-agent sharing).
  3. Tenant B agent-private memory is NOT visible to the other B agent.
  4. Tenant A cannot read tenant B/C memory, including spoofed-arg, forged-JWT,
     unauthorized-client, and no-token attacks (fail closed).
  5. The IAM layer ALONE blocks cross-tenant access (direct assumed-role calls,
     bypassing the Lambda/Gateway entirely).
Scenario 6 (full agent conversation over MCP) is in test_agent_e2e.py.

Every test uses a freshly generated, globally-unique `shopper_id` (a uuid
fragment), so tests never see data left over from previous runs and no
session-wide cleanup fixture is needed.

NOTE on timing: AgentCore Memory's ListMemoryRecords/RetrieveMemoryRecords
have an observed eventual-consistency lag after a BatchCreateMemoryRecords
write (anywhere from <1s to ~60s, occasionally longer --
GetMemoryRecord-by-id is immediately consistent, list/search is not; see
docs/EVIDENCE.md). Presence assertions below poll for up to ~150s instead of
checking once.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from config.tenants import REGION
from tests.conftest import TOOL_PREFIX, TENANT_AGENTS
from tests.mcp_client import call_tool, mcp_session, poll_until_text_present


def _unique(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:10]}"


def _shopper_id() -> str:
    """A fresh, globally-unique shopper id for one test -- guarantees no
    cross-test data pollution without needing a cleanup fixture."""
    return f"shopper{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# 1. Each tenant can write and read its own memory through the Gateway.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tenant_id,agent_id", TENANT_AGENTS)
async def test_tenant_write_and_read_own_memory(tokens, gateway_url, tenant_id, agent_id):
    token = tokens.for_tenant_agent(tenant_id, agent_id)
    shopper_id = _shopper_id()
    marker = _unique(f"own-memory-{tenant_id}-{agent_id}")

    async with mcp_session(gateway_url, token) as session:
        write_resp = await call_tool(
            session, TOOL_PREFIX + "remember_fact", {"fact": marker, "scope": "private", "shopper_id": shopper_id}
        )
        assert write_resp["ok"] is True, write_resp
        assert write_resp["namespace"] == f"/tenant/{tenant_id}/{agent_id}/{shopper_id}/"

        # list_memories (exact listing), not recall (semantic ranking), for a
        # deterministic presence check.
        found, last = await poll_until_text_present(
            lambda: call_tool(session, TOOL_PREFIX + "list_memories", {"scope": "private", "shopper_id": shopper_id}),
            marker,
        )
        assert found, f"expected to find own marker via list_memories within timeout, last={last}"


async def test_recall_semantic_search_basic_sanity(tokens, gateway_url):
    """recall uses semantic (vector) similarity, not literal text match, so we
    only assert that it runs without error and returns a well-formed ranked
    result set -- not that any specific marker ranks #1 (see list_memories-
    based tests above for deterministic presence/absence checks)."""
    token = tokens.for_tenant_agent("northwind", "support")
    shopper_id = _shopper_id()
    async with mcp_session(gateway_url, token) as session:
        await call_tool(
            session,
            TOOL_PREFIX + "remember_fact",
            {"fact": "Shopper asked about return policy for jackets.", "scope": "private", "shopper_id": shopper_id},
        )
        resp = await call_tool(
            session,
            TOOL_PREFIX + "recall",
            {"query": "return policy jackets", "scope": "private", "top_k": 5, "shopper_id": shopper_id},
        )
        assert resp["ok"] is True, resp
        assert isinstance(resp["records"], list)
        # Fast-path (pending_indexing) records are exact-substring matches,
        # not semantically ranked -- they intentionally carry no "score".
        # Only records the long-term semantic index has already scored
        # should have one.
        for r in resp["records"]:
            if r.get("status", "indexed") == "indexed":
                assert "score" in r, r


# ---------------------------------------------------------------------------
# 2. Tenant B: agent1 (merch/Shopping Assistant) writes shared memory,
#    agent2 (support/Order Support) reads it, for the SAME shopper.
# ---------------------------------------------------------------------------
async def test_borealis_cross_agent_shared_memory(tokens, gateway_url):
    shop_token = tokens.for_tenant_agent("borealis", "merch")
    orders_token = tokens.for_tenant_agent("borealis", "support")
    shopper_id = _shopper_id()
    marker = _unique("shared-cross-agent")

    async with mcp_session(gateway_url, shop_token) as shop_session:
        write_resp = await call_tool(
            shop_session,
            TOOL_PREFIX + "remember_fact",
            {"fact": f"Shopping assistant note: {marker}", "scope": "shared", "shopper_id": shopper_id},
        )
        assert write_resp["ok"] is True, write_resp
        assert write_resp["namespace"] == f"/tenant/borealis/shared/{shopper_id}/"

    async with mcp_session(gateway_url, orders_token) as orders_session:
        found, last = await poll_until_text_present(
            lambda: call_tool(
                orders_session, TOOL_PREFIX + "list_memories", {"scope": "shared", "shopper_id": shopper_id}
            ),
            marker,
        )
        assert found, f"order-support agent should see the shopping assistant's SHARED note, last={last}"


# ---------------------------------------------------------------------------
# 2a2. Freshness ordering: when a shopper's facts conflict (updated size,
#      updated budget), recall/list_memories must surface the NEWEST one
#      first -- fixes a real demo bug where a stale, already-indexed fact
#      could outrank a just-written one with no ordering signal at all.
# ---------------------------------------------------------------------------
async def test_list_memories_returns_newest_fact_first(tokens, gateway_url):
    token = tokens.for_tenant_agent("northwind", "support")
    shopper_id = _shopper_id()
    older_marker = _unique("freshness-older")
    newer_marker = _unique("freshness-newer")

    async with mcp_session(gateway_url, token) as session:
        write1 = await call_tool(
            session, TOOL_PREFIX + "remember_fact",
            {"fact": f"Old preference: {older_marker}", "scope": "private", "shopper_id": shopper_id},
        )
        assert write1["ok"] is True, write1
        import asyncio

        await asyncio.sleep(2)  # ensure a distinct, later timestamp for the second write
        write2 = await call_tool(
            session, TOOL_PREFIX + "remember_fact",
            {"fact": f"New preference: {newer_marker}", "scope": "private", "shopper_id": shopper_id},
        )
        assert write2["ok"] is True, write2

        await asyncio.sleep(2)
        list_resp = await call_tool(session, TOOL_PREFIX + "list_memories", {"scope": "private", "shopper_id": shopper_id})
        texts = [r["content"]["text"] for r in list_resp["records"]]
        older_idx = next((i for i, t in enumerate(texts) if older_marker in t), None)
        newer_idx = next((i for i, t in enumerate(texts) if newer_marker in t), None)
        assert older_idx is not None and newer_idx is not None, f"both facts should be present: {texts}"
        assert newer_idx < older_idx, (
            f"the more recently written fact must sort BEFORE the older one (newest-first), "
            f"got order: {texts}"
        )


# ---------------------------------------------------------------------------
# 2a3. The attack demo's 4th trace layer: a direct IAM-layer probe (same
#      already-assumed tenant role, different tenant's namespace) must be
#      denied independently of the interceptor's own stripping behavior.
# ---------------------------------------------------------------------------
async def test_list_memories_iam_probe_denies_cross_tenant(tokens, gateway_url):
    token = tokens.for_tenant_agent("northwind", "support")
    shopper_id = _shopper_id()

    async with mcp_session(gateway_url, token) as session:
        resp = await call_tool(
            session, TOOL_PREFIX + "list_memories",
            {"scope": "shared", "shopper_id": shopper_id, "iam_probe_target_tenant_id": "borealis"},
        )
        assert resp["ok"] is True, resp
        probe = resp.get("iam_probe")
        assert probe is not None, f"expected an iam_probe field in the response, got: {resp}"
        assert probe["denied"] is True, f"IAM should deny a direct cross-tenant probe: {probe}"
        assert "AccessDenied" in probe.get("error", ""), probe


# ---------------------------------------------------------------------------
# 2b. The write-through fast path (fixes a real demo-breaking ~40-60s lag):
#     cross-agent shared recall must work within a few seconds, not up to a
#     minute. Also re-proves private-scope isolation and cross-tenant
#     isolation specifically for the fast-path (CreateEvent/ListEvents)
#     mechanism, since it uses a DIFFERENT isolation mechanism (actorId +
#     app-layer scope/agent_id filtering) than the long-term namespace path.
# ---------------------------------------------------------------------------
async def test_fast_path_cross_agent_shared_recall_within_5s(tokens, gateway_url):
    shop_token = tokens.for_tenant_agent("borealis", "merch")
    orders_token = tokens.for_tenant_agent("borealis", "support")
    shopper_id = _shopper_id()
    marker = _unique("fastpath-shared")

    async with mcp_session(gateway_url, shop_token) as shop_session:
        write_resp = await call_tool(
            shop_session,
            TOOL_PREFIX + "remember_fact",
            {"fact": f"Fast-path shared note: {marker}", "scope": "shared", "shopper_id": shopper_id},
        )
        assert write_resp["ok"] is True, write_resp

    # No polling loop here on purpose: this asserts the fix actually makes
    # the headline demo moment fast, not just eventually consistent.
    async with mcp_session(gateway_url, orders_token) as orders_session:
        import asyncio

        await asyncio.sleep(3)  # well under the ~40-60s long-term-index lag
        list_resp = await call_tool(
            orders_session, TOOL_PREFIX + "list_memories", {"scope": "shared", "shopper_id": shopper_id}
        )
        texts = [r["content"]["text"] for r in list_resp["records"]]
        assert any(marker in t for t in texts), (
            f"fast-path write-through should make this visible within ~3s, got: {texts}"
        )
        matching = [r for r in list_resp["records"] if marker in r["content"]["text"]]
        assert matching[0].get("status") == "pending_indexing", (
            f"expected the fast-path copy to be labeled pending_indexing before the long-term index "
            f"catches up, got: {matching[0]}"
        )

        recall_resp = await call_tool(
            orders_session, TOOL_PREFIX + "recall", {"query": marker, "scope": "shared", "shopper_id": shopper_id}
        )
        recall_texts = [r["content"]["text"] for r in recall_resp["records"]]
        assert any(marker in t for t in recall_texts), f"recall should also surface the fast-path note, got: {recall_texts}"


async def test_fast_path_recall_matches_paraphrased_query_within_5s(tokens, gateway_url):
    """Regression test: recall's fast-path ranking must find a just-written
    fact even when the query's WORDING differs from the fact's wording (not
    just an exact-substring match of the whole query). Without this, a
    shopper asking "what do you know about my size and budget" would not
    find a fact phrased "Wears size L, budget up to $300" until the
    long-term index caught up (~40-80s), defeating the whole point of the
    fast path for the natural-language recall path (not just list_memories)."""
    shop_token = tokens.for_tenant_agent("borealis", "merch")
    orders_token = tokens.for_tenant_agent("borealis", "support")
    shopper_id = _shopper_id()
    marker = _unique("fastpath-paraphrase")
    fact_text = f"Wears size L, budget up to $300. tag:{marker}"
    paraphrased_query = "size and budget"

    async with mcp_session(gateway_url, shop_token) as shop_session:
        write_resp = await call_tool(
            shop_session,
            TOOL_PREFIX + "remember_fact",
            {"fact": fact_text, "scope": "shared", "shopper_id": shopper_id},
        )
        assert write_resp["ok"] is True, write_resp

    async with mcp_session(gateway_url, orders_token) as orders_session:
        import asyncio

        await asyncio.sleep(3)  # well under the ~40-80s long-term-index lag
        recall_resp = await call_tool(
            orders_session,
            TOOL_PREFIX + "recall",
            {"query": paraphrased_query, "scope": "shared", "shopper_id": shopper_id, "top_k": 5},
        )
        recall_texts = [r["content"]["text"] for r in recall_resp["records"]]
        assert any(marker in t for t in recall_texts), (
            f"recall with a paraphrased query ('{paraphrased_query}') should still rank-and-surface a "
            f"just-written fast-path fact via word overlap, not require an exact substring match of the "
            f"whole query against the fact text; got: {recall_texts}"
        )


async def test_fast_path_private_scope_not_visible_to_other_agent(tokens, gateway_url):
    """The fast-path mechanism uses a DIFFERENT isolation primitive
    (actorId is per-shopper, shared by both Borealis agents) than the
    long-term namespace path, so this needs its own dedicated proof: a
    private-scope fast-path event written by one agent must not leak to
    the other agent at the same brand for the same shopper."""
    shop_token = tokens.for_tenant_agent("borealis", "merch")
    orders_token = tokens.for_tenant_agent("borealis", "support")
    shopper_id = _shopper_id()
    marker = _unique("fastpath-private")

    async with mcp_session(gateway_url, shop_token) as shop_session:
        write_resp = await call_tool(
            shop_session,
            TOOL_PREFIX + "remember_fact",
            {"fact": f"Fast-path private note: {marker}", "scope": "private", "shopper_id": shopper_id},
        )
        assert write_resp["ok"] is True, write_resp

        # shop agent itself DOES see its own private fast-path note quickly.
        import asyncio

        await asyncio.sleep(2)
        own_list = await call_tool(
            shop_session, TOOL_PREFIX + "list_memories", {"scope": "private", "shopper_id": shopper_id}
        )
        own_texts = [r["content"]["text"] for r in own_list["records"]]
        assert any(marker in t for t in own_texts), f"shop agent should see its own fast-path private note: {own_texts}"

    async with mcp_session(gateway_url, orders_token) as orders_session:
        other_list = await call_tool(
            orders_session, TOOL_PREFIX + "list_memories", {"scope": "private", "shopper_id": shopper_id}
        )
        other_texts = [r["content"]["text"] for r in other_list["records"]]
        assert not any(marker in t for t in other_texts), (
            f"order-support must NOT see the shopping assistant's fast-path PRIVATE note: {other_texts}"
        )

        other_both = await call_tool(
            orders_session, TOOL_PREFIX + "list_memories", {"scope": "both", "shopper_id": shopper_id}
        )
        other_both_texts = [r["content"]["text"] for r in other_both["records"]]
        assert not any(marker in t for t in other_both_texts), (
            f"order-support's 'both' listing must NOT leak the fast-path PRIVATE note either: {other_both_texts}"
        )


async def test_fast_path_not_visible_cross_tenant(tokens, gateway_url):
    """The fast-path event is actorId-scoped (tenant-prefixed), so it must
    be just as tenant-isolated as the long-term namespace path -- proven
    directly against the Gateway, immediately (no poll needed, since the
    whole point of the fast path is immediate consistency)."""
    borealis_token = tokens.for_tenant_agent("borealis", "merch")
    northwind_token = tokens.for_tenant_agent("northwind", "support")
    shopper_id = _shopper_id()
    marker = _unique("fastpath-crosstenant")

    async with mcp_session(gateway_url, borealis_token) as borealis_session:
        write_resp = await call_tool(
            borealis_session,
            TOOL_PREFIX + "remember_fact",
            {"fact": f"Fast-path tenant secret: {marker}", "scope": "shared", "shopper_id": shopper_id},
        )
        assert write_resp["ok"] is True, write_resp

    async with mcp_session(gateway_url, northwind_token) as northwind_session:
        leak_check = await call_tool(
            northwind_session, TOOL_PREFIX + "list_memories", {"scope": "both", "shopper_id": shopper_id}
        )
        texts = [r["content"]["text"] for r in leak_check["records"]]
        assert not any(marker in t for t in texts), f"northwind must not see borealis's fast-path secret: {texts}"


# ---------------------------------------------------------------------------
# 3. Tenant B agent-private memory is NOT visible to the other B agent (same shopper).
# ---------------------------------------------------------------------------
async def test_borealis_agent_private_not_shared(tokens, gateway_url):
    shop_token = tokens.for_tenant_agent("borealis", "merch")
    orders_token = tokens.for_tenant_agent("borealis", "support")
    shopper_id = _shopper_id()
    marker = _unique("private-shop-only")

    async with mcp_session(gateway_url, shop_token) as shop_session:
        write_resp = await call_tool(
            shop_session,
            TOOL_PREFIX + "remember_fact",
            {"fact": f"Shopping-assistant-private note: {marker}", "scope": "private", "shopper_id": shopper_id},
        )
        assert write_resp["ok"] is True, write_resp
        assert write_resp["namespace"] == f"/tenant/borealis/merch/{shopper_id}/"

        # shop agent itself CAN see its own private note (poll until it's
        # indexed -- confirms the write really happened before we assert
        # order-support can't see it, rather than racing a not-yet-visible write).
        found_own, last_own = await poll_until_text_present(
            lambda: call_tool(shop_session, TOOL_PREFIX + "list_memories", {"scope": "private", "shopper_id": shopper_id}),
            marker,
        )
        assert found_own, f"shop agent should see its own private note once indexed, last={last_own}"

    async with mcp_session(gateway_url, orders_token) as orders_session:
        list_private = await call_tool(
            orders_session, TOOL_PREFIX + "list_memories", {"scope": "private", "shopper_id": shopper_id}
        )
        texts_private = [r["content"]["text"] for r in list_private["records"]]
        assert not any(marker in t for t in texts_private), (
            f"order-support's private listing leaked the shopping assistant's private note: {texts_private}"
        )

        list_both = await call_tool(
            orders_session, TOOL_PREFIX + "list_memories", {"scope": "both", "shopper_id": shopper_id}
        )
        texts_both = [r["content"]["text"] for r in list_both["records"]]
        assert not any(marker in t for t in texts_both), (
            f"order-support's 'both' listing leaked the shopping assistant's private note: {texts_both}"
        )


# ---------------------------------------------------------------------------
# 4. Tenant A cannot read tenant B/C memory -- including attacks.
# ---------------------------------------------------------------------------
async def test_cross_tenant_listing_returns_nothing(tokens, gateway_url):
    """Seed a borealis-only shared fact (confirmed indexed via polling on
    borealis's own side), then confirm northwind's listing never contains it
    (structural isolation: northwind's token can only ever list northwind's
    own two namespaces)."""
    borealis_token = tokens.for_tenant_agent("borealis", "merch")
    northwind_token = tokens.for_tenant_agent("northwind", "support")
    shopper_id = _shopper_id()
    marker = _unique("borealis-secret")

    async with mcp_session(gateway_url, borealis_token) as borealis_session:
        seed = await call_tool(
            borealis_session,
            TOOL_PREFIX + "remember_fact",
            {"fact": f"Borealis secret: {marker}", "scope": "shared", "shopper_id": shopper_id},
        )
        assert seed["ok"] is True
        found, _ = await poll_until_text_present(
            lambda: call_tool(
                borealis_session, TOOL_PREFIX + "list_memories", {"scope": "shared", "shopper_id": shopper_id}
            ),
            marker,
        )
        assert found, "sanity: borealis should see its own freshly-written shared secret"

    async with mcp_session(gateway_url, northwind_token) as northwind_session:
        # Even using the SAME shopper_id string (an attacker who somehow
        # learned the shopper_id would still be fenced off by the tenant
        # boundary, which shopper_id cannot cross).
        leak_check = await call_tool(
            northwind_session, TOOL_PREFIX + "list_memories", {"scope": "both", "shopper_id": shopper_id}
        )
        assert leak_check["ok"] is True
        texts = [r["content"]["text"] for r in leak_check["records"]]
        assert not any(marker in t for t in texts), f"northwind listing leaked borealis's secret: {texts}"
        assert leak_check["namespaces_searched"] == [
            f"/tenant/northwind/shared/{shopper_id}/",
            f"/tenant/northwind/support/{shopper_id}/",
        ]


@pytest.mark.parametrize(
    "spoof_args",
    [
        {"tenant_id": "borealis"},
        {"tenantid": "borealis"},
        {"namespace": "/tenant/borealis/shared/anonymous/"},
        {"namespace_path": "/tenant/borealis/*"},
        {"actor_id": "tenant-borealis-support"},
        {"agent_id": "merch", "tenant_id": "borealis"},
        {"scope": "../borealis/shared/"},
        {"scope": "*"},
        {"scope": "/tenant/borealis/shared/"},
        {"shopper_id": "../../borealis/shared/anonymous"},  # shopper_id path-traversal attempt
    ],
    ids=lambda d: ",".join(f"{k}={v}" for k, v in d.items()),
)
async def test_attack_spoofed_tenant_fields_in_tool_args(tokens, gateway_url, spoof_args):
    """A northwind-authenticated caller tries every plausible way to smuggle a
    different tenant/namespace/actor into the tool arguments (plus a
    shopper_id path-traversal attempt). All must fail closed: the write
    still lands in northwind's own namespace (interceptor strips/overrides
    the caller-supplied tenant fields and sanitizes shopper_id), never in
    borealis's."""
    token = tokens.for_tenant_agent("northwind", "support")
    marker = _unique("spoof-attempt")
    args = {"fact": f"spoof test: {marker}", **spoof_args}

    async with mcp_session(gateway_url, token) as session:
        resp = await call_tool(session, TOOL_PREFIX + "remember_fact", args)
        # Either it succeeds (landing safely in northwind's own namespace) or
        # it's rejected outright -- but it must NEVER land in borealis's namespace,
        # and an invalid shopper_id must have been sanitized (no literal
        # ".." in the resulting namespace path).
        if resp.get("ok"):
            assert resp["namespace"].startswith("/tenant/northwind/"), (
                f"spoofed args {spoof_args} caused a write outside northwind's namespace: {resp}"
            )
            assert ".." not in resp["namespace"], f"shopper_id path-traversal was not sanitized: {resp}"

    # Confirm borealis never received it (no need to poll/wait: this checks
    # absence due to isolation, not an indexing race).
    borealis_token = tokens.for_tenant_agent("borealis", "support")
    async with mcp_session(gateway_url, borealis_token) as borealis_session:
        leak_check = await call_tool(
            borealis_session, TOOL_PREFIX + "list_memories", {"scope": "both", "shopper_id": "anonymous"}
        )
        texts = [r["content"]["text"] for r in leak_check["records"]]
        assert not any(marker in t for t in texts), f"spoofed args {spoof_args} leaked into borealis: {texts}"


def _raw_mcp_post(gateway_url: str, token: str | None, body: dict) -> httpx.Response:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=20) as client:
        return client.post(gateway_url, headers=headers, json=body)


LIST_TOOLS_BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def test_attack_no_token_rejected(gateway_url):
    resp = _raw_mcp_post(gateway_url, None, LIST_TOOLS_BODY)
    assert resp.status_code in (401, 403), f"expected auth rejection with no token, got {resp.status_code}: {resp.text[:300]}"


def test_attack_unsigned_forged_jwt_rejected(gateway_url):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"client_id": "forged", "exp": int(time.time()) + 3600, "token_use": "access"}).encode()
        )
        .decode()
        .rstrip("=")
    )
    forged = f"{header}.{payload}."
    resp = _raw_mcp_post(gateway_url, forged, LIST_TOOLS_BODY)
    assert resp.status_code in (401, 403), f"expected rejection of unsigned/forged JWT, got {resp.status_code}: {resp.text[:300]}"


def test_attack_garbage_token_rejected(gateway_url):
    resp = _raw_mcp_post(gateway_url, "not-a-jwt-at-all", LIST_TOOLS_BODY)
    assert resp.status_code in (401, 403), f"expected rejection of garbage token, got {resp.status_code}: {resp.text[:300]}"


def test_attack_unauthorized_client_token_rejected(tokens, gateway_url):
    """The attacker app client exists in the same Cognito pool and CAN mint a
    syntactically/cryptographically valid token (correctly signed by the same
    issuer) -- but its client_id is deliberately NOT in the Gateway's
    allowedClients list, so the CUSTOM_JWT authorizer must reject it before
    any interceptor or tool runs."""
    token = tokens.for_client_key("attacker:none", scope_override="agentcore-mt-demo/mem.attacker-unmapped")
    resp = _raw_mcp_post(gateway_url, token, LIST_TOOLS_BODY)
    assert resp.status_code in (401, 403), (
        f"expected rejection of unauthorized/unrecognized client token, got {resp.status_code}: {resp.text[:300]}"
    )


# ---------------------------------------------------------------------------
# 5. The IAM layer ALONE blocks cross-tenant access (bypassing Lambda/Gateway).
# ---------------------------------------------------------------------------
def _assume(sts, role_arn: str):
    resp = sts.assume_role(RoleArn=role_arn, RoleSessionName=f"test-{uuid.uuid4().hex[:8]}")
    c = resp["Credentials"]
    import boto3

    return boto3.client(
        "bedrock-agentcore",
        region_name=REGION,
        aws_access_key_id=c["AccessKeyId"],
        aws_secret_access_key=c["SecretAccessKey"],
        aws_session_token=c["SessionToken"],
    )


def test_iam_layer_blocks_cross_tenant_batch_create(state, sts):
    """Tenant A's (northwind) assumed-role credentials, used directly against
    the Memory data plane (no Lambda, no Gateway), must be denied when
    targeting tenant B's (borealis) namespace -- and allowed for its own."""
    role_arn = state["iam"]["tenant_access_role_arns"]["northwind"]
    memory_id = state["memory"]["memory_id"]
    client = _assume(sts, role_arn)

    client.batch_create_memory_records(
        memoryId=memory_id,
        records=[
            {
                "requestIdentifier": str(uuid.uuid4()),
                "namespaces": ["/tenant/northwind/support/anonymous/"],
                "content": {"text": "iam-test own namespace"},
                "timestamp": datetime.now(timezone.utc),
            }
        ],
    )

    with pytest.raises(Exception) as excinfo:
        client.batch_create_memory_records(
            memoryId=memory_id,
            records=[
                {
                    "requestIdentifier": str(uuid.uuid4()),
                    "namespaces": ["/tenant/borealis/support/anonymous/"],
                    "content": {"text": "iam-test cross-tenant attempt"},
                    "timestamp": datetime.now(timezone.utc),
                }
            ],
        )
    assert "AccessDenied" in str(excinfo.value), excinfo.value


def test_iam_layer_blocks_cross_tenant_retrieve(state, sts):
    role_arn = state["iam"]["tenant_access_role_arns"]["northwind"]
    memory_id = state["memory"]["memory_id"]
    client = _assume(sts, role_arn)

    client.retrieve_memory_records(
        memoryId=memory_id,
        namespace="/tenant/northwind/support/anonymous/",
        searchCriteria={"searchQuery": "iam-test", "topK": 3},
    )
    with pytest.raises(Exception) as excinfo:
        client.retrieve_memory_records(
            memoryId=memory_id,
            namespace="/tenant/borealis/support/anonymous/",
            searchCriteria={"searchQuery": "iam-test", "topK": 3},
        )
    assert "AccessDenied" in str(excinfo.value), excinfo.value


def test_iam_layer_blocks_cross_tenant_create_event(state, sts):
    role_arn = state["iam"]["tenant_access_role_arns"]["northwind"]
    memory_id = state["memory"]["memory_id"]
    client = _assume(sts, role_arn)

    client.create_event(
        memoryId=memory_id,
        actorId="tenant-northwind-anonymous",
        sessionId="iam-test-session",
        eventTimestamp=datetime.now(timezone.utc),
        payload=[{"conversational": {"content": {"text": "iam-test own actor"}, "role": "USER"}}],
    )
    with pytest.raises(Exception) as excinfo:
        client.create_event(
            memoryId=memory_id,
            actorId="tenant-borealis-anonymous",
            sessionId="iam-test-session",
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"content": {"text": "iam-test cross-tenant actor"}, "role": "USER"}}],
        )
    assert "AccessDenied" in str(excinfo.value), excinfo.value


def test_iam_layer_blocks_cross_tenant_list_memory_records(state, sts):
    """Covers the one data-plane action not yet exercised by the tests
    above: ListMemoryRecords. Direct assumed-role call, bypassing the
    Lambda/Gateway entirely."""
    role_arn = state["iam"]["tenant_access_role_arns"]["northwind"]
    memory_id = state["memory"]["memory_id"]
    client = _assume(sts, role_arn)

    client.list_memory_records(memoryId=memory_id, namespace="/tenant/northwind/support/anonymous/", maxResults=10)
    with pytest.raises(Exception) as excinfo:
        client.list_memory_records(memoryId=memory_id, namespace="/tenant/borealis/support/anonymous/", maxResults=10)
    assert "AccessDenied" in str(excinfo.value), excinfo.value
