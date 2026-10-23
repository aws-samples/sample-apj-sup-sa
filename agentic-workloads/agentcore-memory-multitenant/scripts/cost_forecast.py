#!/usr/bin/env python3
"""Monthly cost forecast for the shopping-assistant backend at a given
customer scale. Uses the SAME pricing table as the live backend
(config/tenants.py::MODEL_PRICING_PER_1K_TOKENS) and token counts measured
from real /api/chat calls (scripts/rag_eval.py records them per answer), not
invented figures.

Usage:
    python3 scripts/cost_forecast.py --shoppers 50000 --chats-per-shopper 3 --kb-share 0.4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.tenants import MODEL_HAIKU, MODEL_PRICING_PER_1K_TOKENS, MODEL_SONNET

# Measured on Haiku 4.5 from the 12-question RAG eval (Oct 2026), every
# question a tool-using turn (two Converse calls). The ~4,100-token static
# prefix (tool specs + system preamble) is served from the prompt cache, so
# it shows up as cache reads rather than input tokens.
AVG_UNCACHED_INPUT_TOKENS_PER_TURN = 1416
AVG_CACHE_READ_TOKENS_PER_TURN = 7904
AVG_OUTPUT_TOKENS_PER_TURN = 171
CACHED_PREFIX_TOKENS = 4124
# The cache is shared by every brand/agent/shopper (identical prefix) and
# its TTL is 5 minutes, so writes depend on wall-clock time, not traffic.
# Upper bound: one write per model per 5 minutes, all month.
CACHE_WRITES_PER_MONTH = 30 * 24 * 12
CACHE_WRITE_PRICE_MULTIPLIER = 1.25  # same as lambda_src/webui_backend/handler.py
CACHE_READ_PRICE_MULTIPLIER = 0.1

# Knowledge base (kb_search): Titan Text Embeddings V2 for the query, one
# S3 Vectors QueryVectors call. Approximate us-east-1 list prices; S3 Vectors
# also bills data processed per query, negligible for ~10-vector indexes.
KB_QUERY_EMBED_TOKENS = 20
TITAN_EMBED_V2_PER_1K_TOKENS = 0.00002
S3_VECTORS_QUERY_PER_MILLION = 2.50

# AgentCore usage-based pricing (see README "Cost notes" for the source caveat).
GATEWAY_PER_1K_CALLS = 0.005
MEMORY_EVENTS_PER_1K = 0.25


def forecast(shoppers: int, chats_per_shopper: float, kb_share: float, model_id: str) -> dict:
    pricing = MODEL_PRICING_PER_1K_TOKENS[model_id]
    total_chats = shoppers * chats_per_shopper
    kb_queries = total_chats * kb_share

    input_cost = total_chats * AVG_UNCACHED_INPUT_TOKENS_PER_TURN / 1000 * pricing["input"]
    cache_read_cost = (
        total_chats * AVG_CACHE_READ_TOKENS_PER_TURN / 1000 * pricing["input"] * CACHE_READ_PRICE_MULTIPLIER
    )
    cache_write_cost = (
        CACHE_WRITES_PER_MONTH * CACHED_PREFIX_TOKENS / 1000 * pricing["input"] * CACHE_WRITE_PRICE_MULTIPLIER
    )
    output_cost = total_chats * AVG_OUTPUT_TOKENS_PER_TURN / 1000 * pricing["output"]
    bedrock_cost = input_cost + cache_read_cost + cache_write_cost + output_cost

    kb_cost = kb_queries * (
        KB_QUERY_EMBED_TOKENS / 1000 * TITAN_EMBED_V2_PER_1K_TOKENS + S3_VECTORS_QUERY_PER_MILLION / 1_000_000
    )
    gateway_calls = total_chats * 1.5 + kb_queries  # ~1-2 memory/retail tool calls per turn, plus kb_search
    gateway_cost = gateway_calls / 1000 * GATEWAY_PER_1K_CALLS
    memory_events = total_chats * 2  # remember_fact write-through + the long-term write
    memory_cost = memory_events / 1000 * MEMORY_EVENTS_PER_1K

    total = bedrock_cost + kb_cost + gateway_cost + memory_cost
    return {
        "model_id": model_id,
        "total_chats": total_chats,
        "kb_queries": kb_queries,
        "bedrock_cost_usd": round(bedrock_cost, 2),
        "  of_which_cache_writes_usd": round(cache_write_cost, 2),
        "kb_cost_usd": round(kb_cost, 2),
        "gateway_cost_usd": round(gateway_cost, 2),
        "memory_cost_usd": round(memory_cost, 2),
        "total_monthly_usd": round(total, 2),
        "cost_per_chat_usd": round(total / total_chats, 6) if total_chats else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shoppers", type=int, default=50_000)
    parser.add_argument("--chats-per-shopper", type=float, default=3.0)
    parser.add_argument("--kb-share", type=float, default=0.4, help="fraction of chat turns that call kb_search")
    args = parser.parse_args()

    print(
        f"Forecast: {args.shoppers:,} shoppers x {args.chats_per_shopper} chats/month each, "
        f"{args.kb_share:.0%} of turns call kb_search"
    )
    print("(token counts measured on live /api/chat calls; see script docstring)\n")
    for model_id, label in [(MODEL_HAIKU, "Haiku 4.5"), (MODEL_SONNET, "Sonnet 4.5")]:
        result = forecast(args.shoppers, args.chats_per_shopper, args.kb_share, model_id)
        print(f"--- {label} ---")
        for k, v in result.items():
            print(f"  {k}: {v}")
        print()


if __name__ == "__main__":
    main()
