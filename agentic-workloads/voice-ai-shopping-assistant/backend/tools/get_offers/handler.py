"""get_offers — AgentCore Gateway tool (UC5).

Find what's currently on special, with flexible search. Two modes:

  • BROWSE (no `queries`): top current specials, biggest savings first, optionally
    narrowed to a `category`. (The original behaviour — back-compat.)

  • SEARCH (`queries`: [str]): for each term, find the on-special products that
    match it — multi-word lexical (per-word match on name/brand + pg_trgm), and
    if that finds nothing, a semantic fallback (Bedrock Cohere Embed v3 +
    pgvector cosine). Results are grouped PER query, so the agent can answer
    "specials on pasta" and "specials for the ingredients to make carbonara"
    (pass the recipe's ingredients as the list) in ONE call — and see which
    ingredients have a deal and which don't.

The `specials` table holds no searchable text (just product_id + prices), so
search is over `products` INNER JOINed to `specials` (i.e. only on-special
products match). Each result carries `pct_below_usual` from the was/special
price — an honest CURRENT-special signal, not historical/seasonal pricing.

Event: bare args or {"arguments": {...}}.
Returns (search mode):  {"data": {"results": [{query, offers:[Offer]}], "offers": [Offer]}}
        (browse mode):  {"data": {"offers": [Offer]}}
DB via Aurora Data API; query embedded via bedrock-runtime.
"""
from __future__ import annotations

import json
import os
import re
import boto3


def _re_escape(tok: str) -> str:
    """Escape a query token for safe use inside a Postgres regex (word-boundary
    match). Tokens are user input, so neutralise regex metacharacters."""
    return re.escape(tok)

ssm = boto3.client("ssm")
rds = boto3.client("rds-data")
bedrock = boto3.client("bedrock-runtime")

_DB: dict[str, str] = {}

# Must match the model products were embedded with at seed time (seed_loader.py).
EMBED_MODEL = os.environ.get("EMBED_MODEL", "cohere.embed-english-v3")

_DIETARY = {"vegetarian", "low_sugar", "gluten_free", "low_salt", "vegan",
            "high_protein", "halal", "organic", "kosher"}
_ALLERGENS = {"milk", "gluten", "wheat", "soy", "fish", "sulphites", "egg",
              "peanut", "sesame", "shellfish"}
_MAX_TOKENS = 6
_MAX_QUERIES = 12          # cap a batch (e.g. a long recipe) for latency
_DEFAULT_PER_QUERY = 5     # matches returned per query term
_SORTS = {"relevance", "savings_desc", "price_asc", "price_desc"}

# (A) Semantic confidence gate: reject a semantic-fallback match when its cosine
# distance to the query exceeds this. Calibrated on live data — real matches
# sit ≤0.564 ("cheese"→Mozzarella String Cheese 0.564, "fizzy"→Celsius 0.517),
# junk ≥0.63 ("spaghetti"→baked beans 0.631, "eggs"→Oreo bars 0.694). Above the
# gate we return [] so the agent says "nothing on special for X" honestly.
_MAX_SEMANTIC_DIST = 0.58

# (D3) Map common spoken categories -> the catalogue's real category strings
# (verified live via SELECT DISTINCT category FROM products). When the caller's
# `category` matches a key, we filter by EXACT category (so "dairy" no longer
# pulls in ham/kombucha); otherwise we fall back to a substring ILIKE.
_CATEGORY_MAP = {
    "dairy": ["dairy"],
    "drinks": ["drinks"],
    "drink": ["drinks"],
    "soft drinks": ["drinks"],
    "beverages": ["drinks"],
    "snacks": ["biscuits & snacks"],
    "biscuits": ["biscuits & snacks"],
    "chips": ["biscuits & snacks"],
    "fruit": ["fruit & vegetables"],
    "vegetables": ["fruit & vegetables"],
    "veggies": ["fruit & vegetables"],
    "produce": ["fruit & vegetables"],
    "meat": ["meat"],
    "seafood": ["seafood"],
    "fish": ["seafood"],
    "frozen": ["frozen food"],
    "bakery": ["bakery"],
    "bread": ["bakery"],
    "pasta": ["rice, noodles & pasta"],
    "rice": ["rice, noodles & pasta"],
    "noodles": ["rice, noodles & pasta"],
    "canned": ["canned & packet food"],
    "condiments": ["condiments"],
    "sauces": ["condiments"],
    "confectionery": ["confectionery"],
    "lollies": ["confectionery"],
    "sweets": ["confectionery"],
    "chocolate": ["confectionery"],
    "breakfast": ["breakfast foods"],
    "cereal": ["breakfast foods"],
    "baking": ["baking"],
    "health": ["health foods", "health & wellbeing"],
    "toiletries": ["toiletries"],
    "household": ["household cleaning"],
    "cleaning": ["household cleaning"],
    "baby": ["baby"],
    "pet": ["pet care"],
    "pet food": ["pet care"],
    "deli": ["serviced deli", "from the deli"],
    "desserts": ["desserts"],
    "spreads": ["jams & spreads"],
    "jam": ["jams & spreads"],
    "alcohol": ["beer wine & spirits"],
    "beer": ["beer wine & spirits"],
    "wine": ["beer wine & spirits"],
}
_DEFAULT_BROWSE_LIMIT = 10


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


def _embed_query(text: str) -> list[float] | None:
    """Embed a query term (search_query) for semantic fallback; None on failure."""
    try:
        body = json.dumps({"texts": [text], "input_type": "search_query"})
        resp = bedrock.invoke_model(modelId=EMBED_MODEL, body=body,
                                    contentType="application/json", accept="*/*")
        payload = json.loads(resp["body"].read())
        embs = payload["embeddings"]
        if isinstance(embs, dict):  # {"embeddings": {"float": [[...]]}}
            embs = embs.get("float")
        return embs[0] if embs else None
    except Exception:  # noqa: BLE001 - best-effort; caller degrades to lexical-only
        return None


def _clean_list(raw, allowed: set[str]) -> list[str]:
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


def _row_to_offer(r: dict) -> dict:
    was = r.get("was_price_cents") or 0
    savings = r.get("savings_cents") or 0
    pct = round(100 * savings / was) if was else 0
    return {
        "product_id": r["product_id"],
        "name": r["name"],
        "brand": r["brand"],
        "category": r["category"],
        "aisle": r["aisle"],
        "unit": r["unit"],
        "image_url": r.get("image_url"),
        "price_cents": r["price_cents"],
        "special_price_cents": r["special_price_cents"],
        "was_price_cents": r["was_price_cents"],
        "savings_cents": savings,
        "pct_below_usual": pct,
        "special_type": r["special_type"],
    }


# INNER JOIN specials -> only on-special products. Always savings-first.
_SELECT_HEAD = """
SELECT p.product_id, p.name, p.brand, p.category, p.aisle, p.unit, p.image_url,
       p.price_cents, s.special_price_cents, s.was_price_cents, s.savings_cents,
       s.special_type
FROM specials s
JOIN products p ON p.product_id = s.product_id
"""


def _filter_clauses(filt: dict) -> tuple[list[str], list[dict]]:
    """Optional structured filters shared by lexical + semantic paths."""
    where: list[str] = []
    params: list[dict] = []
    if filt["category"]:
        cat = filt["category"].strip().lower()
        mapped = _CATEGORY_MAP.get(cat)
        if mapped:
            # (D3) Known spoken category -> EXACT catalogue category match, so
            # "dairy" excludes ham/kombucha that a substring ILIKE would catch.
            where.append("p.category = ANY(string_to_array(:category, '||')::text[])")
            params.append(_s("category", "||".join(mapped)))
        else:
            where.append("p.category ILIKE :category")
            params.append(_s("category", f"%{filt['category']}%"))
    if filt["dietary"]:
        where.append("p.dietary_tags @> string_to_array(:dietary, ',')::text[]")
        params.append(_s("dietary", ",".join(filt["dietary"])))
    if filt["exclude"]:
        where.append("NOT (p.allergens && string_to_array(:exclude, ',')::text[])")
        params.append(_s("exclude", ",".join(filt["exclude"])))
    return where, params


def _order_clause(sort: str) -> str:
    """ORDER BY for search-mode results (H). relevance handled inline by the
    lexical runner; here for the non-relevance sorts shared by both paths."""
    eff = "COALESCE(s.special_price_cents, p.price_cents)"
    if sort == "savings_desc":
        return "s.savings_cents DESC, p.name"
    if sort == "price_asc":
        return f"{eff} ASC, p.name"
    if sort == "price_desc":
        return f"{eff} DESC, p.name"
    return ""  # relevance -> caller supplies


def _run_lexical(query: str, filt: dict, limit: int, sort: str) -> list[dict]:
    # (B) Each token must hit name/brand as a WHOLE WORD (word-boundary regex),
    # not a bare substring — cuts mid-word noise. Ranking (sort=relevance): whole-
    # phrase name hit > name token hit > brand > category-only, then trigram
    # similarity, then savings. (H) Non-relevance sorts use _order_clause.
    params: list[dict] = [_l("limit", limit), _s("raw", query), _s("raw_like", f"%{query}%")]
    where: list[str] = []
    for i, tok in enumerate(query.split()[:_MAX_TOKENS]):
        # \m \M = Postgres word boundaries; ~* = case-insensitive regex.
        where.append(f"(p.name ~* :t{i} OR p.brand ~* :t{i})")
        params.append(_s(f"t{i}", r"\m" + _re_escape(tok) + r"\M"))
    fwhere, fparams = _filter_clauses(filt)
    where += fwhere
    params += fparams
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order = _order_clause(sort) or (
        "(CASE WHEN p.name ILIKE :raw_like THEN 2 "
        "WHEN p.brand ILIKE :raw_like THEN 1 ELSE 0 END) DESC, "
        "similarity(p.name, :raw) DESC, "
        "s.savings_cents DESC, p.name"
    )
    sql = f"{_SELECT_HEAD}{where_sql}\nORDER BY {order}\nLIMIT :limit"
    return json.loads(_query(sql, params).get("formattedRecords") or "[]")


def _run_semantic(qvec: list[float], filt: dict, limit: int, sort: str) -> list[dict]:
    params: list[dict] = [
        _l("limit", limit),
        _s("qvec", "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"),
    ]
    where = ["p.embedding IS NOT NULL",
             # (A) confidence gate — only matches within the distance cutoff.
             f"(p.embedding <=> :qvec::vector) <= {_MAX_SEMANTIC_DIST}"]
    fwhere, fparams = _filter_clauses(filt)
    where += fwhere
    params += fparams
    # Order by the requested sort if given, else by closeness then savings.
    order = _order_clause(sort) or "p.embedding <=> :qvec::vector ASC, s.savings_cents DESC"
    sql = (f"{_SELECT_HEAD}\nWHERE {' AND '.join(where)}"
           f"\nORDER BY {order}\nLIMIT :limit")
    return json.loads(_query(sql, params).get("formattedRecords") or "[]")


def _search_one(term: str, filt: dict, limit: int, sort: str) -> list[dict]:
    """Auto cascade for one term: lexical first, semantic only if it finds nothing."""
    rows = _run_lexical(term, filt, limit, sort)
    if not rows:
        qvec = _embed_query(term)
        if qvec is not None:
            rows = _run_semantic(qvec, filt, limit, sort)
    return rows


def handler(event, context):
    event = event or {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else event

    # Accept queries[] or a bare query string; missing/empty -> browse mode.
    queries = args.get("queries")
    if queries is None and args.get("query"):
        queries = [args["query"]]
    if isinstance(queries, str):
        queries = [queries]
    queries = [str(q).strip() for q in (queries or []) if str(q).strip()][:_MAX_QUERIES]

    filt = {
        "category": (args.get("category") or "").strip(),
        "dietary": _clean_list(args.get("dietary_tags"), _DIETARY),
        "exclude": _clean_list(args.get("exclude_allergens"), _ALLERGENS),
    }

    try:
        per_query = int(args.get("per_query_limit", _DEFAULT_PER_QUERY))
    except (TypeError, ValueError):
        per_query = _DEFAULT_PER_QUERY
    per_query = max(1, min(per_query, 20))

    sort = (args.get("sort") or "relevance").strip().lower()
    if sort not in _SORTS:
        sort = "relevance"

    try:
        # ----- BROWSE mode (no queries): top current specials -----
        if not queries:
            try:
                limit = int(args.get("limit", _DEFAULT_BROWSE_LIMIT))
            except (TypeError, ValueError):
                limit = _DEFAULT_BROWSE_LIMIT
            limit = max(1, min(limit, 50))
            fwhere, fparams = _filter_clauses(filt)
            where_sql = (" WHERE " + " AND ".join(fwhere)) if fwhere else ""
            sql = f"{_SELECT_HEAD}{where_sql}\nORDER BY s.savings_cents DESC, p.name\nLIMIT :limit"
            rows = json.loads(_query(sql, [_l("limit", limit), *fparams]).get("formattedRecords") or "[]")
            return {"data": {"offers": [_row_to_offer(r) for r in rows]}}

        # ----- SEARCH mode: per-term grouped results -----
        results = []
        flat: dict[str, dict] = {}  # product_id -> offer, for the deduped flat list
        for term in queries:
            rows = _search_one(term, filt, per_query, sort)
            offers = [_row_to_offer(r) for r in rows]
            # (C) matched=false means "we searched and nothing's on special for
            # this term" — lets the agent say so honestly rather than imply a deal.
            results.append({"query": term, "offers": offers, "matched": bool(offers)})
            for o in offers:
                flat.setdefault(o["product_id"], o)
        flattened = sorted(flat.values(), key=lambda o: o["savings_cents"], reverse=True)
        return {"data": {"results": results, "offers": flattened}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "query_failed", "message": str(e)}}
