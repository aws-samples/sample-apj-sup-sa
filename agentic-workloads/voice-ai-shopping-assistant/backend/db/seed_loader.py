"""CloudFormation custom-resource handler: create schema + seed the database.

Runs on stack Create/Update via the Data API (no VPC, no driver). Idempotent:
applies schema.sql (all statements use IF NOT EXISTS), TRUNCATEs, then bulk
re-inserts products.json + specials.json. Re-runs whenever the bundled asset
(schema or seed JSON) changes. On Delete it is a no-op (the cluster goes away
with the stack).

Array columns (allergens, dietary_tags) are passed as Postgres array-literal
strings and cast `::text[]` in SQL — safe because every token is snake_case
[a-z_], no commas/quotes to escape.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import boto3

rds = boto3.client("rds-data")
bedrock = boto3.client("bedrock-runtime")

CLUSTER_ARN = os.environ["CLUSTER_ARN"]
SECRET_ARN = os.environ["SECRET_ARN"]
DB_NAME = os.environ["DB_NAME"]

HERE = Path(__file__).parent
BATCH = 100  # parameter sets per BatchExecuteStatement

# Bedrock Cohere Embed English v3 — 1024-dim. Products are embedded with
# input_type=search_document; the search_products tool embeds the query with
# input_type=search_query (asymmetric, better retrieval). Cohere accepts up to
# 96 texts per call.
EMBED_MODEL = "cohere.embed-english-v3"
EMBED_BATCH = 96


def _product_text(p: dict) -> str:
    """The text we embed for semantic search — what a shopper would describe."""
    return ", ".join(
        x for x in (p.get("name"), p.get("brand"), p.get("category"), p.get("aisle")) if x
    )


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed a list of product texts (search_document), batched, with resume retry."""
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        chunk = texts[i : i + EMBED_BATCH]
        body = json.dumps({"texts": chunk, "input_type": "search_document"})
        resp = bedrock.invoke_model(modelId=EMBED_MODEL, body=body,
                                    contentType="application/json", accept="*/*")
        payload = json.loads(resp["body"].read())
        embs = payload["embeddings"]
        if isinstance(embs, dict):  # {"embeddings": {"float": [...]}}
            embs = embs.get("float")
        out.extend(embs)
    return out


def _exec(sql: str, parameters=None):
    """ExecuteStatement with retry while the serverless cluster resumes (0 ACU)."""
    for attempt in range(12):
        try:
            return rds.execute_statement(
                resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
                sql=sql, parameters=parameters or [],
            )
        except rds.exceptions.DatabaseResumingException:
            wait = min(30, 5 * (attempt + 1))
            print(f"cluster resuming, retry in {wait}s...")
            time.sleep(wait)
    raise RuntimeError("cluster did not resume in time")


def _batch(sql: str, parameter_sets):
    for i in range(0, len(parameter_sets), BATCH):
        chunk = parameter_sets[i : i + BATCH]
        for attempt in range(12):
            try:
                rds.batch_execute_statement(
                    resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
                    sql=sql, parameterSets=chunk,
                )
                break
            except rds.exceptions.DatabaseResumingException:
                time.sleep(min(30, 5 * (attempt + 1)))
        else:
            raise RuntimeError("cluster did not resume in time")


def _arr(values) -> str:
    """Python list -> Postgres array literal, e.g. ['milk','soy'] -> '{milk,soy}'."""
    return "{" + ",".join(values or []) + "}"


def apply_schema() -> None:
    raw = (HERE / "schema.sql").read_text()
    # Data API runs one statement per call. Strip `--` comments (whole-line and
    # trailing) so a semicolon inside/after a comment can't split a statement,
    # then split on ';'. Safe here: no `--` appears inside a string literal.
    no_comments = "\n".join(line.split("--", 1)[0] for line in raw.splitlines())
    statements = [s.strip() for s in no_comments.split(";") if s.strip()]
    for stmt in statements:
        _exec(stmt)
    print(f"applied {len(statements)} schema statements")


def seed_products() -> int:
    products = json.loads((HERE / "seed" / "products.json").read_text())

    # Embed every product up front (search_document), so the vector lands with
    # the row in one INSERT and the HNSW index is populated immediately.
    print(f"embedding {len(products)} products via {EMBED_MODEL}...")
    vectors = embed_documents([_product_text(p) for p in products])
    if len(vectors) != len(products):
        raise RuntimeError(f"embedding count {len(vectors)} != products {len(products)}")

    sql = (
        "INSERT INTO products (product_id, name, brand, category, aisle, "
        "price_cents, unit, allergens, dietary_tags, quality_tier, in_stock, image_url, "
        "embedding) "
        "VALUES (:product_id::uuid, :name, :brand, :category, :aisle, :price_cents, "
        ":unit, :allergens::text[], :dietary_tags::text[], :quality_tier, :in_stock, "
        ":image_url, :embedding::vector)"
    )
    sets = []
    for p, vec in zip(products, vectors):
        sets.append([
            {"name": "product_id", "value": {"stringValue": p["product_id"]}},
            {"name": "name", "value": {"stringValue": p["name"]}},
            {"name": "brand", "value": {"stringValue": p["brand"]}},
            {"name": "category", "value": {"stringValue": p["category"]}},
            {"name": "aisle", "value": {"stringValue": p["aisle"]}},
            {"name": "price_cents", "value": {"longValue": int(p["price_cents"])}},
            {"name": "unit", "value": {"stringValue": p["unit"]}},
            {"name": "allergens", "value": {"stringValue": _arr(p.get("allergens"))}},
            {"name": "dietary_tags", "value": {"stringValue": _arr(p.get("dietary_tags"))}},
            {"name": "quality_tier", "value": {"stringValue": p.get("quality_tier", "standard")}},
            {"name": "in_stock", "value": {"booleanValue": bool(p.get("in_stock", True))}},
            {"name": "image_url",
             "value": {"stringValue": p["image_url"]} if p.get("image_url")
             else {"isNull": True}},
            # pgvector accepts the textual form '[f1,f2,...]' cast ::vector.
            {"name": "embedding", "value": {"stringValue": "[" + ",".join(f"{x:.6f}" for x in vec) + "]"}},
        ])
    _batch(sql, sets)
    return len(sets)


def seed_specials() -> int:
    specials = json.loads((HERE / "seed" / "specials.json").read_text())
    sql = (
        "INSERT INTO specials (special_id, product_id, special_price_cents, "
        "was_price_cents, savings_cents, special_type) "
        "VALUES (:special_id::uuid, :product_id::uuid, :special_price_cents, "
        ":was_price_cents, :savings_cents, :special_type)"
    )
    sets = []
    for s in specials:
        sets.append([
            {"name": "special_id", "value": {"stringValue": s["special_id"]}},
            {"name": "product_id", "value": {"stringValue": s["product_id"]}},
            {"name": "special_price_cents", "value": {"longValue": int(s["special_price_cents"])}},
            {"name": "was_price_cents", "value": {"longValue": int(s["was_price_cents"])}},
            {"name": "savings_cents", "value": {"longValue": int(s.get("savings_cents", 0))}},
            {"name": "special_type", "value": {"stringValue": s.get("special_type", "special")}},
        ])
    _batch(sql, sets)
    return len(sets)


def handler(event, context):
    request_type = event.get("RequestType")
    print(f"RequestType={request_type}")
    if request_type == "Delete":
        return {"PhysicalResourceId": "aisle-seed"}

    apply_schema()
    _exec("TRUNCATE products CASCADE")  # CASCADE clears specials (FK) too
    n_products = seed_products()
    n_specials = seed_specials()
    print(f"seeded {n_products} products, {n_specials} specials")

    return {
        "PhysicalResourceId": "aisle-seed",
        "Data": {"products": n_products, "specials": n_specials},
    }
