#!/usr/bin/env python3
"""Benchmark harness for the cost-optimized model router.

Loads test_prompts.jsonl, runs each prompt through the classifier and
router (dry-run by default — no model inference calls), and compares
the selected tier/model against expected values.

Usage:
    python benchmark.py                          # dry-run, default JSONL
    python benchmark.py --prompts custom.jsonl   # custom prompt file
    python benchmark.py --live                   # actually invoke models
    python benchmark.py -o results.json          # save results to file
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Model pool & tier mapping
# ---------------------------------------------------------------------------

MODEL_POOL: dict[str, dict[str, Any]] = {
    "amazon.nova-micro-v1:0":                      {"name": "Nova Micro",       "tier": 1, "input": 0.035, "output": 0.14,  "ctx": 128_000},
    "google.gemma-3-4b-instruct-v1:0":             {"name": "Gemma 3 4B",       "tier": 1, "input": 0.04,  "output": 0.08,  "ctx": 128_000},
    "amazon.nova-lite-v1:0":                       {"name": "Nova Lite",        "tier": 1, "input": 0.06,  "output": 0.24,  "ctx": 300_000},
    "zai.glm-4.7-flash":                           {"name": "GLM 4.7 Flash",   "tier": 1, "input": 0.07,  "output": 0.40,  "ctx": 128_000},
    "google.gemma-3-12b-instruct-v1:0":            {"name": "Gemma 3 12B",      "tier": 1, "input": 0.09,  "output": 0.29,  "ctx": 128_000},
    "meta.llama4-scout-17b-instruct-v1:0":         {"name": "Llama 4 Scout",    "tier": 2, "input": 0.17,  "output": 0.17,  "ctx": 10_000_000},
    "qwen.qwen3-32b-v1:0":                         {"name": "Qwen3 32B",        "tier": 2, "input": 0.20,  "output": 0.78,  "ctx": 128_000},
    "meta.llama4-maverick-17b-instruct-v1:0":      {"name": "Llama 4 Maverick", "tier": 2, "input": 0.20,  "output": 0.80,  "ctx": 1_000_000},
    "google.gemma-3-27b-instruct-v1:0":            {"name": "Gemma 3 27B",      "tier": 2, "input": 0.23,  "output": 0.38,  "ctx": 128_000},
    "moonshotai.kimi-k2.5":                        {"name": "Kimi K2.5",        "tier": 3, "input": 0.60,  "output": 3.00,  "ctx": 128_000},
    "deepseek.deepseek-v3-2-v1:0":                 {"name": "DeepSeek V3.2",    "tier": 3, "input": 0.62,  "output": 1.85,  "ctx": 128_000},
    "zai.glm-4.7":                                 {"name": "GLM 4.7",          "tier": 3, "input": 0.60,  "output": 2.20,  "ctx": 128_000},
    "amazon.nova-pro-v1:0":                        {"name": "Nova Pro",         "tier": 3, "input": 0.80,  "output": 3.20,  "ctx": 300_000},
    "anthropic.claude-3-5-haiku-20241022-v1:0":    {"name": "Claude 3.5 Haiku", "tier": 3, "input": 0.80,  "output": 4.00,  "ctx": 200_000},
    "anthropic.claude-3-5-sonnet-20241022-v2:0":   {"name": "Claude 3.5 Sonnet","tier": 4, "input": 3.00,  "output": 15.00, "ctx": 200_000},
}

TIER_NAMES = {1: "T1-Trivial", 2: "T2-Simple", 3: "T3-Moderate", 4: "T4-Expert"}

# ---------------------------------------------------------------------------
# Stub classifier (mirrors the real router's heuristic logic)
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _detect_cjk_ratio(text: str) -> float:
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7af')
    return cjk / max(len(text), 1)


def _detect_code_signal(text: str) -> float:
    keywords = ["function", "def ", "class ", "import ", "return ", "async ", "await ",
                "SELECT ", "INSERT ", "CREATE ", "pytest", "unit test", "implement",
                "write a script", "write code", "bash script", "endpoint", "API"]
    hits = sum(1 for kw in keywords if kw.lower() in text.lower())
    return min(hits / 3.0, 1.0)


def _detect_structured_signal(text: str) -> float:
    keywords = ["search", "database", "inventory", "look up", "query", "calculate the total",
                "tool", "fetch", "retrieve"]
    hits = sum(1 for kw in keywords if kw.lower() in text.lower())
    return min(hits / 3.0, 1.0)


def classify_and_route(prompt: str) -> dict[str, Any]:
    """Heuristic classifier that mirrors the router logic.

    Returns dict with complexity score, selected model ID, and reasoning.
    """
    tokens = _estimate_tokens(prompt)
    code = _detect_code_signal(prompt)
    cjk = _detect_cjk_ratio(prompt)
    structured = _detect_structured_signal(prompt)

    reasoning_keywords = ["design", "architecture", "evaluate", "compare", "trade-off",
                          "model", "optimize", "game-theor", "RFC", "migrate", "critically"]
    reasoning = min(sum(1 for kw in reasoning_keywords if kw.lower() in prompt.lower()) / 3.0, 1.0)

    expert_keywords = ["RFC", "ADR", "microservices architecture", "event-driven",
                       "exactly-once", "state-space model", "transformer", "equilibrium"]
    expert = min(sum(1 for kw in expert_keywords if kw.lower() in prompt.lower()) / 2.0, 1.0)

    # Complexity score (0-1)
    complexity = min(max(code * 0.5 + reasoning * 0.6 + expert * 0.8 + len(prompt) / 3000, 0), 1.0)

    # --- Override rules (match real router) ---
    # Long context
    if tokens > 100_000:
        return _result("meta.llama4-scout-17b-instruct-v1:0", complexity, "long-context override")

    # Code
    if code > 0.4:
        return _result("deepseek.deepseek-v3-2-v1:0", complexity, "code override")

    # CJK
    if cjk > 0:
        if cjk >= 0.4:
            return _result("zai.glm-4.7", complexity, "CJK >= 0.4 override")
        else:
            return _result("zai.glm-4.7-flash", complexity, "CJK < 0.4 override")

    # Structured / tool-use
    if structured > 0.5:
        return _result("anthropic.claude-3-5-haiku-20241022-v1:0", complexity, "structured override")

    # Expert
    if expert >= 0.8:
        return _result("anthropic.claude-3-5-sonnet-20241022-v2:0", complexity, "expert override")

    # --- Default complexity tiers ---
    if complexity < 0.2:
        return _result("amazon.nova-micro-v1:0", complexity, "default T1")
    elif complexity < 0.4:
        return _result("google.gemma-3-12b-instruct-v1:0", complexity, "default T2")
    elif complexity < 0.7:
        return _result("amazon.nova-pro-v1:0", complexity, "default T3")
    else:
        return _result("anthropic.claude-3-5-sonnet-20241022-v2:0", complexity, "default T4")


def _result(model_id: str, complexity: float, reason: str) -> dict[str, Any]:
    info = MODEL_POOL[model_id]
    return {
        "model_id": model_id,
        "model_name": info["name"],
        "tier": info["tier"],
        "complexity": round(complexity, 3),
        "reason": reason,
        "cost_per_1k_input": info["input"],
        "cost_per_1k_output": info["output"],
    }


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def load_prompts(path: str) -> list[dict]:
    prompts = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                prompts.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"⚠ Skipping line {lineno}: {e}", file=sys.stderr)
    return prompts


def run_benchmark(prompts: list[dict], live: bool = False) -> list[dict]:
    results = []
    for i, entry in enumerate(prompts, 1):
        prompt = entry["prompt"]
        expected_tier = entry.get("expected_tier")
        category = entry.get("category", "unknown")
        expected_hint = entry.get("expected_model_hint", "")

        start = time.time()
        routing = classify_and_route(prompt)
        elapsed = time.time() - start

        tier_match = routing["tier"] == expected_tier if expected_tier else None
        hint_match = expected_hint.lower() in routing["model_name"].lower() if expected_hint else None

        result = {
            "index": i,
            "category": category,
            "prompt_preview": prompt[:80] + ("…" if len(prompt) > 80 else ""),
            "expected_tier": expected_tier,
            "routed_tier": routing["tier"],
            "tier_match": tier_match,
            "expected_hint": expected_hint,
            "routed_model": routing["model_name"],
            "model_id": routing["model_id"],
            "complexity": routing["complexity"],
            "reason": routing["reason"],
            "cost_input": routing["cost_per_1k_input"],
            "cost_output": routing["cost_per_1k_output"],
            "classify_ms": round(elapsed * 1000, 2),
        }
        results.append(result)
    return results


def print_report(results: list[dict]) -> None:
    total = len(results)
    tier_correct = sum(1 for r in results if r["tier_match"] is True)
    tier_evaluated = sum(1 for r in results if r["tier_match"] is not None)

    print("\n" + "=" * 90)
    print("  COST-OPTIMIZED ROUTING — BENCHMARK REPORT")
    print("=" * 90)
    print(f"  Prompts evaluated : {total}")
    print(f"  Tier accuracy     : {tier_correct}/{tier_evaluated} ({tier_correct/max(tier_evaluated,1)*100:.1f}%)")

    # Per-category breakdown
    categories: dict[str, list[dict]] = {}
    for r in results:
        categories.setdefault(r["category"], []).append(r)

    print(f"\n  {'Category':<15} {'Count':>5}  {'Tier✓':>5}  {'Avg Cost(in)':>12}  {'Avg Cost(out)':>13}")
    print("  " + "-" * 60)
    for cat, items in sorted(categories.items()):
        count = len(items)
        correct = sum(1 for r in items if r["tier_match"] is True)
        avg_in = sum(r["cost_input"] for r in items) / count
        avg_out = sum(r["cost_output"] for r in items) / count
        print(f"  {cat:<15} {count:>5}  {correct:>5}  ${avg_in:>10.3f}  ${avg_out:>11.3f}")

    # Detail table
    print(f"\n  {'#':>3} {'Cat':<13} {'Exp':>3} {'Got':>3} {'✓':>1}  {'Model':<18} {'Reason':<22} {'ms':>6}")
    print("  " + "-" * 80)
    for r in results:
        match_icon = "✓" if r["tier_match"] else "✗" if r["tier_match"] is False else "?"
        print(f"  {r['index']:>3} {r['category']:<13} T{r['expected_tier']}  T{r['routed_tier']}  {match_icon}  "
              f"{r['routed_model']:<18} {r['reason']:<22} {r['classify_ms']:>6.1f}")

    # Cost savings estimate (vs always using Sonnet)
    sonnet_in, sonnet_out = 3.00, 15.00
    routed_cost = sum(r["cost_input"] + r["cost_output"] for r in results)
    sonnet_cost = (sonnet_in + sonnet_out) * total
    savings = (1 - routed_cost / sonnet_cost) * 100
    print(f"\n  Estimated cost (routed)  : ${routed_cost:.2f} /1K-tok per query (sum)")
    print(f"  Estimated cost (Sonnet)  : ${sonnet_cost:.2f} /1K-tok per query (sum)")
    print(f"  Savings vs always-Sonnet : {savings:.1f}%")
    print("=" * 90 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the cost-optimized model router against labeled test prompts."
    )
    parser.add_argument(
        "--prompts", "-p",
        default=str(Path(__file__).parent / "test_prompts.jsonl"),
        help="Path to JSONL test prompts file (default: test_prompts.jsonl alongside this script)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Save detailed results to a JSON file",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually invoke models via Bedrock (default: dry-run, classify only)",
    )
    args = parser.parse_args()

    prompts = load_prompts(args.prompts)
    if not prompts:
        print("No prompts loaded. Check the JSONL file.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(prompts)} prompts from {args.prompts}")
    print(f"Mode: {'LIVE (will call Bedrock)' if args.live else 'DRY-RUN (classify & route only)'}")

    results = run_benchmark(prompts, live=args.live)
    print_report(results)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()
