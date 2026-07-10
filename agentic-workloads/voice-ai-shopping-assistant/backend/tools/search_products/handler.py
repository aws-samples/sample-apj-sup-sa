"""search_products — AgentCore Gateway tool.

Searches the store inventory and answers the questions a shopper actually asks
when choosing a product: not just "find milk", but "cheap gluten-free pasta with
no milk that's on special". The free-text `query` is tokenized into words
(multi-word, order-independent), and a set of OPTIONAL structured filters —
which the LLM fills from natural language — narrow and rank the results:

  - dietary_tags        product must carry ALL of them   (e.g. vegan, halal)
  - exclude_allergens   product must carry NONE of them  (e.g. milk, peanut)
  - min/max_price_cents bound the EFFECTIVE price (special price if on special)
  - quality_tier        value | standard | premium
  - in_stock_only       hide out-of-stock
  - on_special_only     only items currently on special
  - sort                relevance (default) | price_asc | price_desc | savings_desc
  - mode                auto (default) | lexical | semantic

Each product that is currently on special carries an extra `special` object
(additive to the frozen Product shape). Returns {"data": {"products": [Product]}}
on success, {"error": {...}} on failure.

Lexical vs semantic (the `mode` arg):
  * lexical  — per-word AND on name/brand + pg_trgm relevance. Precise for
    known items ("tim tam", "full cream milk").
  * semantic — embeds the query with Bedrock Cohere Embed v3 (search_query) and
    ranks by pgvector cosine distance to each product's embedding. Answers
    conceptual/synonym queries whose words don't appear in any product name
    ("something fizzy to drink", "taco night", "healthy breakfast").
  * auto (default) — run lexical first; if it returns nothing, fall back to
    semantic. So precise queries stay fast (no Bedrock call) while vague ones
    still resolve, without the caller having to choose. The structured filters
    above apply identically to both paths.

Design notes:
  * Every value reaches SQL as a bound rds-data parameter — including the query
    tokens (:t0,:t1,…), the array filters via string_to_array(:csv, ','), and
    the query embedding as a ::vector literal — so there is no string
    interpolation of user input (injection-safe). Only fixed SQL fragments are
    concatenated.
  * Relevance ranks name matches over brand-only over category-only matches
    (a bare category match for "pasta" otherwise drags in the whole
    "rice, noodles & pasta" aisle), then by trigram similarity(name, query),
    then on-special first, then name. Uses the deployed pg_trgm + GIN indexes.
  * Event: AgentCore Gateway passes the tool arguments directly as the event
    (e.g. {"query": "milk"}). We also accept the wrapped {"arguments": {...}}
    form so direct/test invokes work either way.
  * DB access via the Aurora Data API (rds-data) — no VPC, no pooling. Cluster
    coordinates read from SSM (/aisle/db/*), cached across warm invocations.
"""
from __future__ import annotations

import json
import os
import boto3

ssm = boto3.client("ssm")
rds = boto3.client("rds-data")
bedrock = boto3.client("bedrock-runtime")

_DB: dict[str, str] = {}

# Must match the model products were embedded with at seed time (seed_loader.py).
EMBED_MODEL = os.environ.get("EMBED_MODEL", "cohere.embed-english-v3")

# Controlled vocabularies (mirror the live data + the Gateway tool schema enums).
_DIETARY = {"vegetarian", "low_sugar", "gluten_free", "low_salt", "vegan",
            "high_protein", "halal", "organic", "kosher"}
_ALLERGENS = {"milk", "gluten", "wheat", "soy", "fish", "sulphites", "egg",
              "peanut", "sesame", "shellfish"}
_TIERS = {"value", "standard", "premium"}
_SORTS = {"relevance", "price_asc", "price_desc", "savings_desc"}
_MODES = {"auto", "lexical", "semantic"}
_MAX_TOKENS = 6


def _embed_query(text: str) -> list[float] | None:
    """Embed the shopper's query (search_query) for semantic search; None on failure."""
    try:
        body = json.dumps({"texts": [text], "input_type": "search_query"})
        resp = bedrock.invoke_model(modelId=EMBED_MODEL, body=body,
                                    contentType="application/json", accept="*/*")
        payload = json.loads(resp["body"].read())
        embs = payload["embeddings"]
        if isinstance(embs, dict):  # {"embeddings": {"float": [[...]]}}
            embs = embs.get("float")
        return embs[0] if embs else None
    except Exception:  # noqa: BLE001 - semantic is best-effort; caller falls back
        return None


def _db() -> dict[str, str]:
    if not _DB:
        names = ["/aisle/db/cluster_arn", "/aisle/db/secret_arn", "/aisle/db/name"]
        by_name = {p["Name"]: p["Value"] for p in ssm.get_parameters(Names=names)["Parameters"]}
        _DB.update(
            cluster_arn=by_name["/aisle/db/cluster_arn"],
            secret_arn=by_name["/aisle/db/secret_arn"],
            db_name=by_name["/aisle/db/name"],
        )
    return _DB


def _query(sql: str, params: list[dict]):
    db = _db()
    return rds.execute_statement(
        resourceArn=db["cluster_arn"], secretArn=db["secret_arn"], database=db["db_name"],
        sql=sql, parameters=params, formatRecordsAs="JSON",
    )


def _s(name: str, val: str) -> dict:
    return {"name": name, "value": {"stringValue": val}}


def _l(name: str, val: int) -> dict:
    return {"name": name, "value": {"longValue": int(val)}}


def _b(name: str, val: bool) -> dict:
    return {"name": name, "value": {"booleanValue": bool(val)}}


def _row_to_product(r: dict) -> dict:
    product = {
        "product_id": r["product_id"],
        "name": r["name"],
        "brand": r["brand"],
        "category": r["category"],
        "aisle": r["aisle"],
        "price_cents": r["price_cents"],
        "unit": r["unit"],
        "allergens": r.get("allergens") or [],
        "dietary_tags": r.get("dietary_tags") or [],
        "quality_tier": r["quality_tier"],
        "in_stock": r["in_stock"],
        "image_url": r.get("image_url"),
    }
    if r.get("special_price_cents") is not None:
        product["special"] = {
            "special_price_cents": r["special_price_cents"],
            "was_price_cents": r["was_price_cents"],
            "savings_cents": r["savings_cents"],
            "special_type": r["special_type"],
        }
    return product


def _clean_list(raw, allowed: set[str]) -> list[str]:
    """Normalize an arg that may be a list or comma string; keep only known values."""
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for v in raw:
        v = str(v).strip().lower()
        if v in allowed and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _maybe_int(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# Effective price = special price when on special, else shelf price. Used for
# every price filter/sort so "under $5" and "cheapest" respect specials.
_EFFECTIVE = "COALESCE(s.special_price_cents, p.price_cents)"

_SELECT_HEAD = """
SELECT p.product_id, p.name, p.brand, p.category, p.aisle, p.price_cents,
       p.unit, p.allergens, p.dietary_tags, p.quality_tier, p.in_stock, p.image_url,
       s.special_price_cents, s.was_price_cents, s.savings_cents, s.special_type
FROM products p
LEFT JOIN specials s ON s.product_id = p.product_id
"""


def _filter_clauses(f: dict) -> tuple[list[str], list[dict]]:
    """Build the WHERE clauses + bound params shared by lexical and semantic paths
    (the structured filters: category, dietary, allergens, price, tier, stock,
    special). Excludes the text-match clause, which differs per path."""
    where: list[str] = []
    params: list[dict] = []
    if f["category"]:
        where.append("p.category ILIKE :category")
        params.append(_s("category", f"%{f['category']}%"))
    if f["dietary"]:
        where.append("p.dietary_tags @> string_to_array(:dietary, ',')::text[]")
        params.append(_s("dietary", ",".join(f["dietary"])))
    if f["exclude"]:
        where.append("NOT (p.allergens && string_to_array(:exclude, ',')::text[])")
        params.append(_s("exclude", ",".join(f["exclude"])))
    if f["min_price"] is not None:
        where.append(f"{_EFFECTIVE} >= :min_price")
        params.append(_l("min_price", f["min_price"]))
    if f["max_price"] is not None:
        where.append(f"{_EFFECTIVE} <= :max_price")
        params.append(_l("max_price", f["max_price"]))
    if f["tier"] in _TIERS:
        where.append("p.quality_tier = :tier")
        params.append(_s("tier", f["tier"]))
    if f["in_stock_only"]:
        where.append("p.in_stock = :in_stock")
        params.append(_b("in_stock", True))
    if f["on_special_only"]:
        where.append("s.special_id IS NOT NULL")
    return where, params


def _order_by(sort: str, semantic: bool) -> str:
    """ORDER BY for the chosen sort. For sort=relevance, semantic path orders by
    vector cosine distance; lexical path by name/brand tier + trigram."""
    if sort == "price_asc":
        return f"{_EFFECTIVE} ASC, p.name"
    if sort == "price_desc":
        return f"{_EFFECTIVE} DESC, p.name"
    if sort == "savings_desc":
        return "COALESCE(s.savings_cents, 0) DESC, p.name"
    # relevance
    if semantic:
        return "p.embedding <=> :qvec ASC"  # cosine distance, nearest first
    return (
        "(CASE WHEN p.name ILIKE :raw_like THEN 2 "
        "WHEN p.brand ILIKE :raw_like THEN 1 ELSE 0 END) DESC, "
        "similarity(p.name, :raw) DESC, "
        "(s.special_id IS NOT NULL) DESC, p.name"
    )


def _run_lexical(query: str, filt: dict, sort: str, limit: int) -> list[dict]:
    params: list[dict] = [_s("raw", query), _l("limit", limit)]
    where: list[str] = []
    # Multi-word: every token must hit name OR brand (order-independent).
    for i, tok in enumerate(query.split()[:_MAX_TOKENS]):
        where.append(f"(p.name ILIKE :t{i} OR p.brand ILIKE :t{i})")
        params.append(_s(f"t{i}", f"%{tok}%"))
    fwhere, fparams = _filter_clauses(filt)
    where += fwhere
    params += fparams
    if sort == "relevance":
        params.append(_s("raw_like", f"%{query}%"))
    sql = f"{_SELECT_HEAD}\nWHERE {' AND '.join(where)}\nORDER BY {_order_by(sort, semantic=False)}\nLIMIT :limit"
    resp = _query(sql, params)
    return json.loads(resp.get("formattedRecords") or "[]")


def _run_semantic(qvec: list[float], filt: dict, sort: str, limit: int) -> list[dict]:
    params: list[dict] = [
        _l("limit", limit),
        _s("qvec", "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"),
    ]
    where = ["p.embedding IS NOT NULL"]
    fwhere, fparams = _filter_clauses(filt)
    where += fwhere
    params += fparams
    # :qvec is bound as text and cast ::vector in the ORDER BY distance expr.
    # (No need to SELECT the distance — it's only used for ranking.)
    order = _order_by(sort, semantic=True).replace(":qvec", ":qvec::vector")
    sql = f"{_SELECT_HEAD}\nWHERE {' AND '.join(where)}\nORDER BY {order}\nLIMIT :limit"
    resp = _query(sql, params)
    return json.loads(resp.get("formattedRecords") or "[]")


def handler(event, context):
    # Gateway sends args directly as the event; tolerate a wrapped form too.
    event = event or {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else event

    query = (args.get("query") or "").strip()
    if not query:
        return {"error": {"code": "invalid_argument", "message": "query is required"}}

    limit = _maybe_int(args.get("limit"))
    limit = 10 if limit is None else max(1, min(limit, 50))

    sort = (args.get("sort") or "relevance").strip().lower()
    if sort not in _SORTS:
        sort = "relevance"
    mode = (args.get("mode") or "auto").strip().lower()
    if mode not in _MODES:
        mode = "auto"

    filt = {
        "category": (args.get("category") or "").strip(),
        "dietary": _clean_list(args.get("dietary_tags"), _DIETARY),
        "exclude": _clean_list(args.get("exclude_allergens"), _ALLERGENS),
        "min_price": _maybe_int(args.get("min_price_cents")),
        "max_price": _maybe_int(args.get("max_price_cents")),
        "tier": (args.get("quality_tier") or "").strip().lower(),
        "in_stock_only": bool(args.get("in_stock_only")),
        "on_special_only": bool(args.get("on_special_only")),
    }

    try:
        rows: list[dict] = []
        used = mode

        if mode in ("auto", "lexical"):
            rows = _run_lexical(query, filt, sort, limit)
            used = "lexical"

        # auto: fall back to semantic only when lexical found nothing.
        # semantic: go straight to vector search.
        if (mode == "semantic") or (mode == "auto" and not rows):
            qvec = _embed_query(query)
            if qvec is not None:
                rows = _run_semantic(qvec, filt, sort, limit)
                used = "semantic"
            elif mode == "semantic":
                # explicit semantic but embedding failed → degrade to lexical
                rows = _run_lexical(query, filt, sort, limit)
                used = "lexical_fallback"

        products = [_row_to_product(r) for r in rows]
        return {"data": {"products": products, "search_mode": used}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "query_failed", "message": str(e)}}
