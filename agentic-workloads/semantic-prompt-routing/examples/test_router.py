"""Test script for the semantic router.

Tests routing decisions and validates functionality across different query types.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.router import SemanticRouter

# Test queries covering different scenarios
TEST_QUERIES = [
    # Simple queries (should route to Tier 1)
    ("What is the capital of France?", "simple factual"),

    # Code queries (should prefer DeepSeek/Qwen)
    ("Write a Python function to sort a list of dictionaries by a key.", "code - medium"),
    (
        "Implement a binary search tree in Rust with insert, delete, and find operations. "
        "Include full error handling and unit tests.",
        "code - complex"
    ),

    # CJK queries (should prefer GLM/Qwen)
    ("什么是机器学习?", "CJK - Chinese"),
    ("日本の首都はどこですか？", "CJK - Japanese"),

    # Long context (should prefer Llama 4 Scout/Maverick)
    (
        "Analyze this 500-page document and extract all mentions of regulatory compliance issues. "
        "Cross-reference with the following 200 legal precedents...",
        "long context"
    ),

    # Structured output (should prefer Claude Haiku)
    (
        "Create a JSON schema for a user profile with nested address, payment methods, and "
        "preferences. Include validation rules.",
        "structured output"
    ),

    # High complexity (should route to Tier 3-4)
    (
        "Design a distributed system architecture for a real-time multiplayer game with "
        "100M+ concurrent users. Consider latency, consistency, fault tolerance, and cost. "
        "Compare WebSocket vs WebRTC approaches.",
        "expert reasoning"
    ),
]


async def test_router():
    """Test the semantic router with real LLM calls."""
    print("\n" + "=" * 80)
    print("TESTING SEMANTIC ROUTER")
    print("=" * 80)

    router = SemanticRouter(region='us-east-1')

    for query, category in TEST_QUERIES:
        print(f"\n[{category}]")
        print(f"Query: {query[:100]}...")

        try:
            result = await router.route_and_respond(query, conversation_history=[])
            print(f"→ Model: {result.model_used}")
            print(f"  Family: {result.family}, Tier: {result.tier}")
            print(f"  Cost: ${result.cost_usd:.6f}")
            print(f"  Reason: {result.routing_explanation}")
            print(f"  Classification: complexity={result.classification_signals.complexity_score:.2f}, "
                  f"task={result.classification_signals.task_type}, "
                  f"lang={result.classification_signals.language}")
            print(f"  Response: {result.response[:150]}...")
        except Exception as e:
            print(f"✗ Error: {e}")

    # Print stats
    print("\n" + "-" * 80)
    stats = router.get_stats()
    print("Router Statistics:")
    print(f"  Total Requests: {stats['total_requests']}")
    print(f"  Total Cost: ${stats['total_cost_usd']:.6f}")
    print(f"  Avg Cost/Request: ${stats['avg_cost_per_request']:.6f}")
    print(f"  Avg Latency/Request: {stats['avg_latency_per_request']:.2f}s")


async def test_classification_only():
    """Test just the classification logic (fast, no LLM calls for response)."""
    print("\n" + "=" * 80)
    print("CLASSIFICATION & ROUTING DECISIONS (No LLM Response Calls)")
    print("=" * 80)

    # Import with proper paths
    import sys
    from pathlib import Path

    # Add parent dir to path to import from app
    parent_dir = str(Path(__file__).parent.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    from app.routing.classifier import classify_query, classify_query_ollama
    from app.router import SemanticSelector
    from app.model_config import build_model_configs

    # Initialize MODEL_CONFIGS first
    build_model_configs('us-east-1')

    selector = SemanticSelector()

    for query, category in TEST_QUERIES:
        print(f"\n[{category}]")
        print(f"Query: {query[:100]}...")

        # Classify
        signals = await classify_query_ollama(query)
        print(f"\nClassification:")
        print(f"  Complexity: {signals.complexity_score:.2f}")
        print(f"  Task Type: {signals.task_type}")
        print(f"  Language: {signals.language}")
        print(f"  Tokens: {signals.token_count}")
        print(f"  CJK: {signals.is_cjk}, Code: {signals.is_code}")
        print(f"  Structured: {signals.has_structured_output}")
        print(f"  Reasoning: {signals.reasoning}")

        # Select model
        model_name, reason = selector.select_model(signals)
        print(f"\nRouting Decision:")
        print(f"  Model: {model_name}")
        print(f"  Reason: {reason}")


async def test_semantic_routing():
    """Test semantic routing with different query types."""
    print("\n" + "=" * 80)
    print("SEMANTIC ROUTING TEST")
    print("=" * 80)

    test_query = "Explain quantum computing in simple terms."

    router = SemanticRouter(region='us-east-1')

    try:
        result = await router.route_and_respond(test_query, conversation_history=[])
        print(f"  Model: {result.family} Tier {result.tier}")
        print(f"  Cost: ${result.cost_usd:.6f}")
        print(f"  Latency: {result.latency_s:.2f}s")
        print(f"  Routing: {result.routing_explanation}")
    except Exception as e:
        print(f"  Error: {e}")


async def main():
    """Run tests."""
    import argparse

    parser = argparse.ArgumentParser(description="Test semantic router")
    parser.add_argument(
        "--mode",
        choices=["full", "classify", "semantic", "all"],
        default="classify",
        help="Test mode: full (with LLM calls), classify (routing only), semantic (test semantic routing), or all",
    )
    args = parser.parse_args()

    if args.mode == "full":
        await test_router()
    elif args.mode == "classify":
        await test_classification_only()
    elif args.mode == "semantic":
        await test_semantic_routing()
    elif args.mode == "all":
        await test_classification_only()
        print("\n" + "=" * 80 + "\n")
        await test_semantic_routing()
        print("\n" + "=" * 80 + "\n")
        await test_router()


if __name__ == "__main__":
    asyncio.run(main())
