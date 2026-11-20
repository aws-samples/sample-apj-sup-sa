"""Tenant isolation for the policy knowledge base (kb_search, S3 Vectors).

Same three layers as memory: the Gateway's JWT authorizer, the interceptor
(which strips any caller-supplied tenant / index / filter argument), and
IAM (each brand's role may query only its own `policies-<brand>` index).
Each brand's synthetic policies carry distinctive facts -- Northwind: 30
days / $6.95 return fee; Borealis: 60 days / "Trail Promise"; Cascade: 14
days / 15% restocking fee -- so a cross-brand read is easy to detect.
"""
from __future__ import annotations

import uuid

import boto3
import pytest

from config.tenants import REGION
from tests.conftest import TOOL_PREFIX
from tests.mcp_client import call_tool, mcp_session

KB_TOOL = TOOL_PREFIX + "kb_search"


def _texts(resp: dict) -> str:
    return " ".join(r["text"] for r in resp.get("results", []))


@pytest.mark.parametrize(
    "tenant_id,agent_id,question,expected_doc,own_fact",
    [
        ("northwind", "support", "How many days do I have to return an item?", "returns-policy", "30 days"),
        ("borealis", "merch", "Do your jackets run small if I layer a fleece?", "sizing-guide", "one size up"),
        ("cascade", "support", "How much is standard shipping?", "shipping-policy", "$4.99"),
    ],
)
async def test_kb_search_returns_own_brand_policy(tokens, gateway_url, tenant_id, agent_id, question, expected_doc, own_fact):
    token = tokens.for_tenant_agent(tenant_id, agent_id)
    async with mcp_session(gateway_url, token) as session:
        resp = await call_tool(session, KB_TOOL, {"query": question})
    assert resp.get("ok"), resp
    assert resp["kb_index"] == f"policies-{tenant_id}"
    assert resp["results"], resp
    assert all(r["tenant"] == tenant_id for r in resp["results"]), resp
    assert resp["results"][0]["doc_id"] == expected_doc, resp["results"][0]
    assert own_fact in _texts(resp), resp
    assert all(r["cite"].startswith(r["doc_id"] + " §") for r in resp["results"])


async def test_kb_search_spoofed_tenant_index_and_filter_are_stripped(tokens, gateway_url):
    """Northwind's JWT plus every argument an attacker might use to point the
    search at Borealis: the interceptor strips them, and only Northwind's
    index (with a server-set tenant filter) is ever queried."""
    token = tokens.for_tenant_agent("northwind", "support")
    spoof = {
        "query": "Trail Promise 60 day return window",
        "tenant_id": "borealis",
        "index": "policies-borealis",
        "index_name": "policies-borealis",
        "filter": {"tenant": "borealis"},
        "namespace": "policies-borealis",
        "top_k": 5,
    }
    async with mcp_session(gateway_url, token) as session:
        resp = await call_tool(session, KB_TOOL, spoof)
    assert resp.get("ok"), resp
    assert resp["kb_index"] == "policies-northwind"
    stripped = set(resp["_trace"]["stripped_fields"])
    assert {"tenant_id", "index", "index_name", "filter", "namespace"} <= stripped, stripped
    assert resp["results"] and all(r["tenant"] == "northwind" for r in resp["results"]), resp
    assert "Trail Promise" not in _texts(resp) and "60 days" not in _texts(resp), resp


@pytest.mark.parametrize("tenant_id,agent_id,other", [("northwind", "support", "borealis"), ("cascade", "support", "northwind")])
async def test_kb_search_iam_probe_denies_other_brand_index(tokens, gateway_url, tenant_id, agent_id, other):
    """Defense in depth, independent of the interceptor: the tools Lambda's
    already-assumed brand role is denied a direct query on another brand's
    index."""
    token = tokens.for_tenant_agent(tenant_id, agent_id)
    async with mcp_session(gateway_url, token) as session:
        resp = await call_tool(session, KB_TOOL, {"query": "returns", "iam_probe_target_tenant_id": other})
    probe = resp.get("iam_probe")
    assert probe and probe["denied"] is True, resp
    assert probe["target_index"] == f"policies-{other}"
    assert "AccessDenied" in probe["error"], probe


def test_iam_layer_blocks_cross_brand_kb_query(state, sts):
    """The IAM layer alone (no Lambda, no Gateway): Northwind's tenant role
    can query policies-northwind and is denied on policies-borealis."""
    creds = sts.assume_role(
        RoleArn=state["iam"]["tenant_access_role_arns"]["northwind"], RoleSessionName=f"kbtest-{uuid.uuid4().hex[:8]}"
    )["Credentials"]
    s3v = boto3.client(
        "s3vectors",
        region_name=REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )
    kb = state["kb"]
    vector = {"float32": [0.0] * (kb["embed_dim"] - 1) + [1.0]}
    own = s3v.query_vectors(
        vectorBucketName=kb["vector_bucket"], indexName="policies-northwind", topK=1, queryVector=vector, returnMetadata=True
    )
    assert own["vectors"] and own["vectors"][0]["metadata"]["tenant"] == "northwind"
    with pytest.raises(Exception) as excinfo:
        s3v.query_vectors(vectorBucketName=kb["vector_bucket"], indexName="policies-borealis", topK=1, queryVector=vector)
    assert "AccessDenied" in str(excinfo.value), excinfo.value
