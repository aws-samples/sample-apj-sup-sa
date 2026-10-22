"""AgentCore Gateway Lambda target: shopping-assistant memory tools + mock
retail API tools.

Tenant/agent/shopper identity for every call comes ONLY from
`server_tenant_id` / `server_agent_id` / `server_shopper_id` /
`server_actor_id`, which the REQUEST interceptor
(lambda_src/interceptor/handler.py) injects into the tool-call arguments
after deriving tenant/agent from the verified JWT and sanitizing the
caller-supplied shopper_id. Any caller-supplied tenant/namespace/actor
argument has already been stripped upstream; this Lambda additionally
ignores (never reads) any such field as a second layer of defense, and
only ever uses the two namespaces that belong to the calling tenant+agent,
scoped to the current shopper:

    shared_ns  = /tenant/{tenant_id}/shared/{shopper_id}/
    private_ns = /tenant/{tenant_id}/{agent_id}/{shopper_id}/

shopper_id partitions memory WITHIN a tenant+agent (e.g. "what did this
particular shopper tell us") -- it is not a tenant-isolation boundary, so
no IAM condition targets it specifically; the namespace/actorId IAM
conditions still apply to the tenant-prefixed portion of the path exactly
as before.

Before any Bedrock AgentCore Memory data-plane call, this Lambda looks up
the caller tenant's STATIC per-tenant IAM role (TENANT_ROLE_ARNS, keyed by
the interceptor-derived tenant_id only) and assumes it. That role's own
permission policy (deploy/03_memory.py) is scoped by
bedrock-agentcore:namespace / actorId conditions using that tenant's
literal id. (One shared role plus an sts:TagSession session tag per call
would also work, but AWS Organizations SCPs commonly deny sts:TagSession,
so this sample uses static per-tenant roles; see deploy/02_iam.py and
docs/EVIDENCE.md.) So even a bug in the dispatch logic below that computed
the wrong namespace string would still be blocked by IAM for any namespace
outside `/tenant/<tenant_id>/*` -- this is the defense-in-depth layer, and
deploy/08_probe_iam_conditions.py empirically verifies which condition
keys are actually enforced per action.

WRITE-THROUGH FAST PATH: ListMemoryRecords/RetrieveMemoryRecords (backed by
BatchCreateMemoryRecords writes) were observed to have an
eventual-consistency lag of up to ~40-60s (see docs/EVIDENCE.md). CreateEvent/ListEvents were empirically confirmed
IMMEDIATELY consistent (tested directly: a record written via CreateEvent
was visible via ListEvents at t+0.1s). So `remember_fact` now ALSO writes
a CreateEvent for the shopper's actorId (fixed sessionId FACT_SESSION_ID,
metadata scope=shared|private + agent_id), and `recall`/`list_memories`
merge the long-term memory-record results with these fast-path events
(ListEvents), filtered by scope exactly the same way namespaces are
filtered, deduped by exact fact text. This makes the headline cross-agent
shared-memory demo moment work within ~1s instead of up to a minute.
Isolation for the agent-private fast-path events relies on this
application-layer scope/agent_id filter (IAM's actorId condition on
CreateEvent/ListEvents only scopes by TENANT, not by which of a tenant's
agents wrote a given event, since actorId is per-shopper not per-agent --
documented explicitly, not assumed).
"""
from __future__ import annotations

import json
import re
import os
import time
import uuid
from datetime import datetime, timezone

import boto3

import retail_catalog

REGION = os.environ.get("AWS_REGION", "us-east-1")
MEMORY_ID = os.environ["MEMORY_ID"]
# tenant_id -> IAM role ARN (static, per-tenant). See deploy/02_iam.py for why
# this is a map of per-tenant roles rather than one shared role assumed with a
# session tag (sts:TagSession is commonly denied by SCPs). The Lambda can only ever assume a role
# looked up by the interceptor-derived tenant_id -- it cannot construct an
# arbitrary role ARN from caller input.
TENANT_ROLE_ARNS: dict = json.loads(os.environ["TENANT_ROLE_ARNS_JSON"])
TARGET_NAME_PREFIX_DELIM = "___"
# Fixed, synthetic sessionId used ONLY for remember_fact's write-through
# fast-path events -- separate from whatever sessionId a real `remember`
# conversational turn uses, so the two don't mix.
FACT_SESSION_ID = "factstore"

# Policy knowledge base (deploy/14_knowledge_base.py): one S3 Vectors index
# per brand, `<KB_INDEX_PREFIX><tenant_id>`. Each tenant role may query only
# its own index.
KB_VECTOR_BUCKET = os.environ.get("KB_VECTOR_BUCKET", "")
KB_INDEX_PREFIX = os.environ.get("KB_INDEX_PREFIX", "policies-")
KB_EMBED_MODEL_ID = os.environ.get("KB_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
KB_EMBED_DIM = int(os.environ.get("KB_EMBED_DIM", "512"))
KB_MAX_TOP_K = 5

_sts = boto3.client("sts", region_name=REGION)
_bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)
_assumed_cache: dict[tuple[str, str], tuple[float, object]] = {}


def _tenant_scoped_client(tenant_id: str, service: str = "bedrock-agentcore"):
    """Assume this tenant's static IAM role and return a data-plane client
    (bedrock-agentcore for Memory, s3vectors for the KB) using those
    temporary, IAM-condition-scoped credentials. Cached per tenant+service
    for the life of the Lambda execution environment (AssumeRole
    credentials last 1h; refreshed at 50 minutes)."""
    cached = _assumed_cache.get((tenant_id, service))
    if cached and cached[0] > time.time():
        return cached[1]

    role_arn = TENANT_ROLE_ARNS.get(tenant_id)
    if not role_arn:
        raise ValueError(f"no tenant-access role configured for tenant_id={tenant_id!r}")

    resp = _sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"{tenant_id}-{uuid.uuid4().hex[:8]}"[:64],
        DurationSeconds=3600,
    )
    creds = resp["Credentials"]
    data_client = boto3.client(
        service,
        region_name=REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )
    _assumed_cache[(tenant_id, service)] = (time.time() + 50 * 60, data_client)
    return data_client


def _shared_ns(tenant_id: str, shopper_id: str) -> str:
    return f"/tenant/{tenant_id}/shared/{shopper_id}/"


def _private_ns(tenant_id: str, agent_id: str, shopper_id: str) -> str:
    return f"/tenant/{tenant_id}/{agent_id}/{shopper_id}/"


def _resolve_tool_name(context) -> str:
    try:
        custom = context.client_context.custom or {}
    except AttributeError:
        custom = {}
    raw = custom.get("bedrockAgentCoreToolName", "") if custom else ""
    if TARGET_NAME_PREFIX_DELIM in raw:
        return raw.split(TARGET_NAME_PREFIX_DELIM, 1)[1]
    return raw


def _write_through_event(data_client, actor_id: str, fact: str, scope: str, agent_id: str) -> None:
    """Fast-path mirror of a remember_fact write: an immediately-consistent
    CreateEvent the shopper's actorId, tagged with enough metadata for
    `_fast_path_events` to filter it correctly later."""
    data_client.create_event(
        memoryId=MEMORY_ID,
        actorId=actor_id,
        sessionId=FACT_SESSION_ID,
        eventTimestamp=datetime.now(timezone.utc),
        payload=[{"conversational": {"content": {"text": fact}, "role": "USER"}}],
        metadata={"scope": {"stringValue": scope}, "agent_id": {"stringValue": agent_id}},
    )


def _fast_path_events(data_client, actor_id: str, agent_id: str, scope: str) -> list[dict]:
    """Returns write-through facts visible to THIS agent for THIS shopper:
    scope='shared' events (any agent wrote them) plus scope='private' events
    this SAME agent wrote. Never another agent's private events -- that's
    the isolation guarantee for agent-private memory within one tenant.
    ListEvents is actorId+sessionId scoped (immediately consistent, verified
    empirically), so we fetch everything for this actor+FACT_SESSION_ID and
    filter here rather than relying on the ListEvents filter API."""
    try:
        resp = data_client.list_events(
            memoryId=MEMORY_ID, actorId=actor_id, sessionId=FACT_SESSION_ID, includePayloads=True, maxResults=100
        )
    except Exception:  # noqa: BLE001
        return []  # no fact-session events yet for this actor; not an error

    out = []
    for ev in resp.get("events", []):
        md = ev.get("metadata", {})
        ev_scope = md.get("scope", {}).get("stringValue") if isinstance(md.get("scope"), dict) else None
        ev_agent = md.get("agent_id", {}).get("stringValue") if isinstance(md.get("agent_id"), dict) else None
        if ev_scope == "shared" and scope in ("shared", "both"):
            pass
        elif ev_scope == "private" and ev_agent == agent_id and scope in ("private", "both"):
            pass
        else:
            continue
        text = ""
        for p in ev.get("payload", []):
            text = p.get("conversational", {}).get("content", {}).get("text", "") or text
        if text:
            out.append({"content": {"text": text}, "_namespace": f"event:{ev_scope}", "status": "pending_indexing"})
            out[-1]["timestamp"] = ev.get("eventTimestamp")
    return out


def _merge_dedupe(long_term_records: list[dict], fast_path: list[dict]) -> list[dict]:
    """Long-term records (status='indexed') take precedence over fast-path
    events with the exact same text once a fact has actually been indexed."""
    seen_text = set()
    merged = []
    for r in long_term_records:
        r.setdefault("status", "indexed")
        text = r.get("content", {}).get("text", "")
        seen_text.add(text)
        merged.append(r)
    for r in fast_path:
        text = r.get("content", {}).get("text", "")
        if text not in seen_text:
            seen_text.add(text)
            merged.append(r)
    # Newest-first: facts can conflict (a shopper changes their mind), and
    # the agent is told to trust the most recent one -- so ordering matters,
    # not just presence. Records use createdAt; fast-path events use
    # timestamp (set above from eventTimestamp). Items with no timestamp at
    # all sort last rather than crashing the comparison.
    def _sort_key(r: dict):
        ts = r.get("createdAt") or r.get("timestamp")
        return str(ts) if ts else ""

    merged.sort(key=_sort_key, reverse=True)
    return merged


def tool_remember(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:
    """Append a short-term conversational event. Over time the semantic
    memory strategy extracts durable facts from this into the chosen
    namespace (shared or private -- both owned by this tenant/agent, scoped
    to this shopper)."""
    data_client = _tenant_scoped_client(tenant_id)
    scope = event.get("scope") if event.get("scope") in ("shared", "private") else "private"
    session_id = event.get("session_id", "default-session")
    text = event.get("text", "")
    role = event.get("role") if event.get("role") in ("USER", "ASSISTANT") else "USER"
    ns_agent_key = "shared" if scope == "shared" else agent_id

    resp = data_client.create_event(
        memoryId=MEMORY_ID,
        actorId=actor_id,
        sessionId=session_id,
        eventTimestamp=datetime.now(timezone.utc),
        payload=[{"conversational": {"content": {"text": text}, "role": role}}],
        extractionConfig={
            "namespaceVariables": {"tenantid": tenant_id, "agentid": ns_agent_key, "shopperid": shopper_id}
        },
    )
    return {"ok": True, "event_id": resp.get("event", {}).get("eventId"), "scope": scope}


def tool_remember_fact(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:
    """Deterministic, synchronous write directly into a tenant namespace --
    used so tests/demos don't depend on async extraction timing. ALSO
    writes an immediately-consistent fast-path event (see module docstring)
    so recall/list_memories can surface the fact right away, before the
    long-term index catches up."""
    data_client = _tenant_scoped_client(tenant_id)
    scope = event.get("scope") if event.get("scope") in ("shared", "private") else "private"
    fact = event.get("fact", "")
    ns = _shared_ns(tenant_id, shopper_id) if scope == "shared" else _private_ns(tenant_id, agent_id, shopper_id)

    resp = data_client.batch_create_memory_records(
        memoryId=MEMORY_ID,
        records=[
            {
                "requestIdentifier": str(uuid.uuid4()),
                "namespaces": [ns],
                "content": {"text": fact},
                "timestamp": datetime.now(timezone.utc),
                "metadata": {"written_by_actor": {"stringValue": actor_id}},
            }
        ],
    )
    successful = resp.get("successfulRecords", [])
    failed = resp.get("failedRecords", [])

    try:
        _write_through_event(data_client, actor_id, fact, scope, agent_id)
    except Exception:  # noqa: BLE001
        pass  # best-effort fast path; the long-term write above already succeeded

    return {"ok": not failed, "namespace": ns, "successful": successful, "failed": failed}


def tool_recall(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:
    data_client = _tenant_scoped_client(tenant_id)
    query = event.get("query", "")
    scope = event.get("scope") if event.get("scope") in ("shared", "private", "both") else "both"
    top_k = int(event.get("top_k", 5))

    namespaces = []
    if scope in ("shared", "both"):
        namespaces.append(_shared_ns(tenant_id, shopper_id))
    if scope in ("private", "both"):
        namespaces.append(_private_ns(tenant_id, agent_id, shopper_id))

    records = []
    for ns in namespaces:
        resp = data_client.retrieve_memory_records(
            memoryId=MEMORY_ID,
            namespace=ns,
            searchCriteria={"searchQuery": query, "topK": top_k},
        )
        for r in resp.get("memoryRecordSummaries", resp.get("records", [])):
            r["_namespace"] = ns
            records.append(r)

    fast_path = _fast_path_events(data_client, actor_id, agent_id, scope)
    # recall is semantic search; a fast-path event has no embedding score yet.
    # Fast-path events are already scoped to this shopper (+ shared/own agent),
    # so the set is small: rank by word overlap with the query and keep top_k,
    # rather than dropping facts whose wording differs from the query.
    query_words = {w for w in re.findall(r"[a-z0-9$]+", query.lower()) if len(w) > 2}
    fast_path.sort(
        key=lambda r: len(query_words & set(re.findall(r"[a-z0-9$]+", r["content"]["text"].lower()))),
        reverse=True,
    )
    records = _merge_dedupe(records, fast_path[:top_k])

    return {"ok": True, "query": query, "namespaces_searched": namespaces, "records": records}


def tool_list_memories(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:
    data_client = _tenant_scoped_client(tenant_id)
    scope = event.get("scope") if event.get("scope") in ("shared", "private", "both") else "both"

    namespaces = []
    if scope in ("shared", "both"):
        namespaces.append(_shared_ns(tenant_id, shopper_id))
    if scope in ("private", "both"):
        namespaces.append(_private_ns(tenant_id, agent_id, shopper_id))

    records = []
    for ns in namespaces:
        resp = data_client.list_memory_records(memoryId=MEMORY_ID, namespace=ns, maxResults=100)
        for r in resp.get("memoryRecordSummaries", resp.get("memoryRecords", [])):
            r["_namespace"] = ns
            records.append(r)

    fast_path = _fast_path_events(data_client, actor_id, agent_id, scope)
    records = _merge_dedupe(records, fast_path)

    # Optional IAM-layer probe, used ONLY by the webui backend's attack
    # demo (never by the LLM/agent -- this field is not in the Gateway's
    # published tool schema for list_memories, it's an internal diagnostic
    # passed straight through the interceptor since it isn't a spoofable
    # tenant/namespace/actor field). Reuses the SAME already-assumed
    # CURRENT-tenant role (data_client) to attempt a direct read against
    # the TARGET tenant's own shared namespace -- proving IAM would deny
    # this even if the interceptor's strip-and-inject logic had a bug,
    # independent of that layer. The webui backend never gets its own
    # Memory IAM permissions; this probe runs entirely inside the tools
    # Lambda, which is the only thing that ever assumes a tenant role.
    probe_target = event.get("iam_probe_target_tenant_id")
    if probe_target and probe_target != tenant_id:
        probe_ns = _shared_ns(probe_target, shopper_id)
        try:
            data_client.list_memory_records(memoryId=MEMORY_ID, namespace=probe_ns, maxResults=1)
            iam_probe = {
                "target_tenant_id": probe_target,
                "namespace_probed": probe_ns,
                "denied": False,
                "note": "UNEXPECTED: IAM did not deny this direct call -- this would be a real bug.",
            }
        except Exception as exc:  # noqa: BLE001
            iam_probe = {
                "target_tenant_id": probe_target,
                "namespace_probed": probe_ns,
                "denied": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {"ok": True, "namespaces_searched": namespaces, "records": records, "iam_probe": iam_probe}

    return {"ok": True, "namespaces_searched": namespaces, "records": records}


def tool_forget(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:  # noqa: ARG001
    data_client = _tenant_scoped_client(tenant_id)
    record_id = event.get("memory_record_id", "")

    # App-layer ownership check in addition to the IAM layer: refuse to even
    # attempt a delete for a record outside this tenant's namespace prefix.
    # (See docs/EVIDENCE.md: GetMemoryRecord/DeleteMemoryRecord cannot be IAM
    # namespace-scoped in this service version, so this check is the only
    # technical control for these two actions.)
    try:
        rec = data_client.get_memory_record(memoryId=MEMORY_ID, memoryRecordId=record_id)
        namespaces = rec.get("memoryRecord", {}).get("namespaces", [])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"lookup failed: {exc}"}

    allowed_prefix = f"/tenant/{tenant_id}/"
    if not all(ns.startswith(allowed_prefix) for ns in namespaces):
        return {"ok": False, "error": "record does not belong to caller's tenant"}

    data_client.delete_memory_record(memoryId=MEMORY_ID, memoryRecordId=record_id)
    return {"ok": True, "deleted": record_id}


def _kb_index(tenant_id: str) -> str:
    return f"{KB_INDEX_PREFIX}{tenant_id}"


def _embed(text: str) -> list[float]:
    resp = _bedrock_runtime.invoke_model(
        modelId=KB_EMBED_MODEL_ID,
        body=json.dumps({"inputText": text, "dimensions": KB_EMBED_DIM, "normalize": True}),
    )
    return json.loads(resp["body"].read())["embedding"]


def tool_kb_search(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:  # noqa: ARG001
    """Search this brand's policy knowledge base (returns, sizing, shipping).

    Isolation: the index name AND the `tenant` metadata filter both come
    from the interceptor-derived tenant_id; any caller-supplied index /
    filter / tenant argument was stripped by the interceptor and is never
    read here. The query runs under the caller brand's assumed role, whose
    IAM policy allows QueryVectors on that brand's index only."""
    if not KB_VECTOR_BUCKET:
        return {"ok": False, "error": "knowledge base not configured (KB_VECTOR_BUCKET unset)"}
    query = str(event.get("query", ""))[:500]
    try:
        top_k = max(1, min(int(event.get("top_k", 3)), KB_MAX_TOP_K))
    except (TypeError, ValueError):
        top_k = 3
    index = _kb_index(tenant_id)
    s3v = _tenant_scoped_client(tenant_id, "s3vectors")
    resp = s3v.query_vectors(
        vectorBucketName=KB_VECTOR_BUCKET,
        indexName=index,
        topK=top_k,
        queryVector={"float32": _embed(query)},
        filter={"tenant": tenant_id},
        returnMetadata=True,
        returnDistance=True,
    )
    results = []
    for v in resp.get("vectors", []):
        md = v.get("metadata", {})
        section_no = v["key"].split("#", 1)[1] if "#" in v["key"] else "1"
        results.append(
            {
                "cite": f"{md.get('doc_id')} §{section_no}",
                "doc_id": md.get("doc_id"),
                "title": md.get("title"),
                "section": md.get("section"),
                "text": md.get("text"),
                "tenant": md.get("tenant"),
                "score": round(1 - float(v.get("distance", 1.0)), 4),
            }
        )
    out = {
        "ok": True,
        "query": query,
        "kb_index": index,
        "namespaces_searched": [f"s3vectors://{KB_VECTOR_BUCKET}/{index} (filter tenant={tenant_id})"],
        "results": results,
    }

    # Same IAM-layer probe as list_memories, used only by tests: query the
    # TARGET brand's index with this brand's already-assumed role.
    probe_target = event.get("iam_probe_target_tenant_id")
    if probe_target and probe_target != tenant_id:
        try:
            s3v.query_vectors(
                vectorBucketName=KB_VECTOR_BUCKET,
                indexName=_kb_index(probe_target),
                topK=1,
                queryVector={"float32": [0.0] * (KB_EMBED_DIM - 1) + [1.0]},
            )
            out["iam_probe"] = {"target_index": _kb_index(probe_target), "denied": False}
        except Exception as exc:  # noqa: BLE001
            out["iam_probe"] = {
                "target_index": _kb_index(probe_target),
                "denied": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return out


def tool_lookup_order(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:  # noqa: ARG001
    return retail_catalog.lookup_order(tenant_id, event.get("order_id", ""))


def tool_check_inventory(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:  # noqa: ARG001
    return retail_catalog.check_inventory(tenant_id, event.get("sku", ""))


def tool_search_products(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:  # noqa: ARG001
    return retail_catalog.search_products(tenant_id, event.get("query", ""))


def tool_issue_refund(event: dict, tenant_id: str, agent_id: str, shopper_id: str, actor_id: str) -> dict:
    """Human-approval-gated risky action. Without `approved=True` this
    NEVER processes anything -- it only returns a proposed action for the
    caller (the webui backend) to surface as an explicit confirmation to
    the shopper. Only a second call with `approved=True` (which the UI
    only sends after the shopper clicks Confirm) actually "processes" the
    refund (mocked -- synthetic data only, no real payment system)."""
    order_id = event.get("order_id", "")
    amount = event.get("amount", 0)
    approved = bool(event.get("approved"))

    order = retail_catalog.lookup_order(tenant_id, order_id)
    if not order.get("found"):
        return {"ok": False, "error": f"order {order_id!r} not found for this tenant", "requires_approval": False}

    if not approved:
        return {
            "ok": False,
            "requires_approval": True,
            "proposed_action": {"tool": "issue_refund", "order_id": order_id, "amount": amount, "tenant_id": tenant_id},
            "message": f"This would issue a ${amount} refund for order {order_id}. Shopper confirmation required "
            "before it is processed -- this has NOT happened yet.",
        }

    return {
        "ok": True,
        "requires_approval": False,
        "refund_id": f"RFND-{order_id}-{uuid.uuid4().hex[:6]}",
        "order_id": order_id,
        "amount": amount,
        "message": f"Refund of ${amount} for order {order_id} processed (synthetic demo data).",
    }


DISPATCH = {
    "remember": tool_remember,
    "remember_fact": tool_remember_fact,
    "recall": tool_recall,
    "list_memories": tool_list_memories,
    "forget": tool_forget,
    "lookup_order": tool_lookup_order,
    "check_inventory": tool_check_inventory,
    "search_products": tool_search_products,
    "issue_refund": tool_issue_refund,
    "kb_search": tool_kb_search,
}


def _json_safe(obj):
    """Recursively convert datetimes (and other non-JSON-native types the
    bedrock-agentcore SDK responses contain) into JSON-serializable values."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def handler(event, context):  # noqa: ANN001
    tool_name = _resolve_tool_name(context)

    tenant_id = event.get("server_tenant_id")
    agent_id = event.get("server_agent_id")
    shopper_id = event.get("server_shopper_id")
    actor_id = event.get("server_actor_id")
    if not (tenant_id and agent_id and shopper_id and actor_id):
        return {
            "ok": False,
            "error": "missing server-derived tenant context; the gateway interceptor must inject "
            "server_tenant_id/server_agent_id/server_shopper_id/server_actor_id",
        }

    fn = DISPATCH.get(tool_name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool: {tool_name!r} (raw event keys: {list(event.keys())})"}

    try:
        result = fn(event, tenant_id, agent_id, shopper_id, actor_id)
    except Exception as exc:  # noqa: BLE001
        # Surface AWS error codes (e.g. AccessDenied from the IAM policy)
        # verbatim -- tests assert on these.
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # "What happened" trace, for the UI's transparency panel (not a
    # security control -- purely informational, surfaces exactly what the
    # interceptor derived/stripped and which IAM role this call ran under).
    namespace_hint = result.get("namespace") or result.get("namespaces_searched")
    result["_trace"] = {
        "jwt_client_id": event.get("_trace_client_id"),
        "stripped_fields": event.get("_trace_stripped_fields", []),
        "server_tenant_id": tenant_id,
        "server_agent_id": agent_id,
        "server_shopper_id": shopper_id,
        "server_actor_id": actor_id,
        "tool": tool_name,
        "tenant_role_arn": TENANT_ROLE_ARNS.get(tenant_id),
        "namespaces_touched": namespace_hint,
    }
    return _json_safe(result)
