"""Step 14: per-brand policy knowledge base (returns, sizing, shipping) on
Amazon S3 Vectors, embedded with Titan Text Embeddings V2.

Cheapest option that still gives IAM-level isolation:
  - one S3 vector bucket, ONE INDEX PER BRAND (`policies-<tenant>`), built
    from the synthetic markdown docs in kb/docs/<tenant>/;
  - each brand's static tenant-access role may query ONLY its own index ARN
    (s3vectors:QueryVectors/GetVectors), so the tools Lambda -- which runs
    kb_search under the caller brand's assumed role, picked from the
    interceptor-derived tenant -- cannot read another brand's index even if
    its code asked for one;
  - every vector also carries `tenant` metadata, and kb_search sets the
    `tenant` filter from the server-derived tenant (never from tool args).
No Bedrock Knowledge Base resource and no OpenSearch: no idle cost beyond
S3 Vectors storage of a few KB.

Idempotent: creates the bucket/indexes if missing, re-embeds and upserts
every chunk (deterministic keys `<doc_id>#<n>`), and deletes keys that no
longer correspond to a chunk.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import REGION, TENANTS
from deploy.common import account_id, client, load_state, log, save_state, tags_map

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "kb" / "docs"
BUCKET_PREFIX = "agentcore-mt-kb"
INDEX_PREFIX = "policies-"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBED_DIM = 512
TOOLS_FN = "agentcore-mt-tools"
TOOLS_ROLE = "agentcore-mt-tools-role"
TENANT_ROLE_PREFIX = "agentcore-mt-tenant-access-"


def load_chunks(tenant_id: str) -> list[dict]:
    """One chunk per `## ` section of each doc. The chunk text repeats the
    doc title and section so it stands alone when retrieved."""
    chunks = []
    for path in sorted((DOCS_DIR / tenant_id).glob("*.md")):
        doc_id = path.stem
        text = path.read_text()
        title = re.search(r"^# (.+)$", text, re.M).group(1).strip()
        sections = re.split(r"^## ", text, flags=re.M)[1:]
        for n, sec in enumerate(sections, start=1):
            heading, _, body = sec.partition("\n")
            body = " ".join(body.split())
            chunks.append(
                {
                    "key": f"{doc_id}#{n}",
                    "doc_id": doc_id,
                    "title": title,
                    "section": heading.strip(),
                    "text": body,
                    "embed_text": f"{title} -- {heading.strip()}: {body}",
                }
            )
    return chunks


def embed(br, text: str) -> list[float]:
    resp = br.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=json.dumps({"inputText": text, "dimensions": EMBED_DIM, "normalize": True}),
    )
    return json.loads(resp["body"].read())["embedding"]


def ensure_bucket(s3v, bucket: str) -> None:
    try:
        s3v.get_vector_bucket(vectorBucketName=bucket)
        log(f"vector bucket exists: {bucket}")
    except s3v.exceptions.NotFoundException:
        s3v.create_vector_bucket(vectorBucketName=bucket, tags=tags_map())
        log(f"created vector bucket: {bucket}")


def ensure_index(s3v, bucket: str, index: str) -> None:
    try:
        s3v.get_index(vectorBucketName=bucket, indexName=index)
        log(f"  index exists: {index}")
    except s3v.exceptions.NotFoundException:
        s3v.create_index(
            vectorBucketName=bucket,
            indexName=index,
            dataType="float32",
            dimension=EMBED_DIM,
            distanceMetric="cosine",
            # Only `tenant` and `doc_id` are filterable; the passage itself
            # is returned as metadata but never filtered on.
            metadataConfiguration={"nonFilterableMetadataKeys": ["text", "title", "section"]},
            tags=tags_map(),
        )
        log(f"  created index: {index}")


def sync_index(s3v, br, bucket: str, tenant_id: str) -> int:
    index = INDEX_PREFIX + tenant_id
    ensure_index(s3v, bucket, index)
    chunks = load_chunks(tenant_id)
    vectors = [
        {
            "key": c["key"],
            "data": {"float32": embed(br, c["embed_text"])},
            "metadata": {
                "tenant": tenant_id,
                "doc_id": c["doc_id"],
                "title": c["title"],
                "section": c["section"],
                "text": c["text"],
            },
        }
        for c in chunks
    ]
    s3v.put_vectors(vectorBucketName=bucket, indexName=index, vectors=vectors)

    wanted = {c["key"] for c in chunks}
    existing: set[str] = set()
    kwargs: dict = {"vectorBucketName": bucket, "indexName": index, "maxResults": 500}
    while True:
        page = s3v.list_vectors(**kwargs)
        existing.update(v["key"] for v in page.get("vectors", []))
        if not page.get("nextToken"):
            break
        kwargs["nextToken"] = page["nextToken"]
    stale = sorted(existing - wanted)
    if stale:
        s3v.delete_vectors(vectorBucketName=bucket, indexName=index, keys=stale)
    log(f"  {index}: upserted {len(vectors)} chunks, deleted {len(stale)} stale")
    return len(vectors)


def index_arn(acct: str, bucket: str, tenant_id: str) -> str:
    return f"arn:aws:s3vectors:{REGION}:{acct}:bucket/{bucket}/index/{INDEX_PREFIX}{tenant_id}"


def attach_iam(iam, acct: str, bucket: str) -> None:
    for tenant_id in TENANTS:
        iam.put_role_policy(
            RoleName=TENANT_ROLE_PREFIX + tenant_id,
            PolicyName="tenant-scoped-kb",
            PolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            # QueryVectors with a filter / returnMetadata also
                            # needs GetVectors on the same index.
                            "Effect": "Allow",
                            "Action": ["s3vectors:QueryVectors", "s3vectors:GetVectors"],
                            "Resource": index_arn(acct, bucket, tenant_id),
                        }
                    ],
                }
            ),
        )
        log(f"  {TENANT_ROLE_PREFIX}{tenant_id}: may query only {INDEX_PREFIX}{tenant_id}")
    iam.put_role_policy(
        RoleName=TOOLS_ROLE,
        PolicyName="kb-embed",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "bedrock:InvokeModel",
                        "Resource": f"arn:aws:bedrock:{REGION}::foundation-model/{EMBED_MODEL_ID}",
                    }
                ],
            }
        ),
    )
    log(f"  {TOOLS_ROLE}: may invoke only {EMBED_MODEL_ID}")


def kb_env(bucket: str) -> dict:
    """Tools Lambda env vars for kb_search (also used by deploy/05_lambdas.py,
    so a re-run of step 5 keeps them)."""
    return {
        "KB_VECTOR_BUCKET": bucket,
        "KB_INDEX_PREFIX": INDEX_PREFIX,
        "KB_EMBED_MODEL_ID": EMBED_MODEL_ID,
        "KB_EMBED_DIM": str(EMBED_DIM),
    }


def merge_tools_env(lam, env: dict) -> None:
    try:
        cfg = lam.get_function_configuration(FunctionName=TOOLS_FN)
    except lam.exceptions.ResourceNotFoundException:
        log(f"{TOOLS_FN} not deployed yet; deploy/05_lambdas.py will set the KB env vars")
        return
    current = cfg.get("Environment", {}).get("Variables", {})
    if all(current.get(k) == v for k, v in env.items()):
        log(f"{TOOLS_FN} already has the KB env vars")
        return
    lam.update_function_configuration(FunctionName=TOOLS_FN, Environment={"Variables": {**current, **env}})
    log(f"merged KB env vars into {TOOLS_FN}")


def main() -> None:
    state = load_state()
    acct = account_id()
    bucket = f"{BUCKET_PREFIX}-{acct}"
    s3v = client("s3vectors")
    br = client("bedrock-runtime")
    iam = client("iam")
    lam = client("lambda")

    ensure_bucket(s3v, bucket)
    counts = {tenant_id: sync_index(s3v, br, bucket, tenant_id) for tenant_id in TENANTS}
    attach_iam(iam, acct, bucket)
    merge_tools_env(lam, kb_env(bucket))

    state["kb"] = {
        "vector_bucket": bucket,
        "index_prefix": INDEX_PREFIX,
        "embed_model_id": EMBED_MODEL_ID,
        "embed_dim": EMBED_DIM,
        "chunks_per_tenant": counts,
    }
    save_state(state)
    log("Knowledge base setup complete.")


if __name__ == "__main__":
    main()
