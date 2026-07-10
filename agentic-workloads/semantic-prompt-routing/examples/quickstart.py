#!/usr/bin/env python3
"""Quick-start script for the semantic router.

Demonstrates basic usage with minimal setup.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def basic_example():
    """Basic usage example."""
    print("=" * 70)
    print("SEMANTIC ROUTER - BASIC EXAMPLE")
    print("=" * 70)

    from app.router import SemanticRouter

    # Initialize router
    print("Initializing semantic router...")
    router = SemanticRouter(region='us-east-1')
    print("Router initialized\n")

    # Test queries
    queries = [
        "What is the capital of France?",
        "Write a Python function to calculate Fibonacci numbers using dynamic programming.",
        "什么是人工智能？",  # Chinese: What is AI?
    ]

    for i, query in enumerate(queries, 1):
        print(f"\n{'─' * 70}")
        print(f"Query {i}: {query}")
        print(f"{'─' * 70}")

        try:
            result = await router.route_and_respond(query, conversation_history=[])

            print(f"\nResponse received:")
            print(f"  Model: {result.family} (Tier {result.tier})")
            print(f"  Cost: ${result.cost_usd:.6f}")
            print(f"  Latency: {result.latency_s:.2f}s")
            print(f"  Tokens: {result.input_tokens} in / {result.output_tokens} out")
            print(f"\n  Classification:")
            print(f"    - Complexity: {result.classification_signals.complexity_score:.2f}")
            print(f"    - Task Type: {result.classification_signals.task_type}")
            print(f"    - Language: {result.classification_signals.language}")
            print(f"\n  Routing Explanation:")
            print(f"    {result.routing_explanation}")
            print(f"\n  Response Preview:")
            print(f"    {result.response[:200]}...")

        except Exception as e:
            print(f"\n[ERROR] {e}")

    # Show stats
    print(f"\n{'=' * 70}")
    print("STATISTICS")
    print("=" * 70)
    stats = router.get_stats()
    print(f"Total Requests: {stats['total_requests']}")
    print(f"Total Cost: ${stats['total_cost_usd']:.6f}")
    print(f"Avg Cost/Request: ${stats['avg_cost_per_request']:.6f}")
    print(f"Avg Latency/Request: {stats['avg_latency_per_request']:.2f}s")


async def cost_comparison():
    """Compare costs across different model tiers."""
    print("\n\n")
    print("=" * 70)
    print("COST COMPARISON: Semantic Routing")
    print("=" * 70)

    from app.router import SemanticRouter

    test_queries = [
        ("What is 2+2?", "Simple query - should route to Tier 1"),
        ("Write Python code for binary search", "Code query - should route to Tier 2"),
        ("Explain quantum computing in simple terms.", "Moderate query - should route to Tier 2"),
    ]

    router = SemanticRouter(region='us-east-1')
    results = []

    for query, description in test_queries:
        print(f"\n\nTesting: {description}")
        print(f"Query: {query}")
        print("-" * 70)

        try:
            result = await router.route_and_respond(query, conversation_history=[])
            result_data = {
                "description": description,
                "model": f"{result.family} T{result.tier}",
                "cost": result.cost_usd,
                "latency": result.latency_s,
            }
            results.append(result_data)
            print(f"  Model: {result_data['model']}")
            print(f"  Cost: ${result_data['cost']:.6f}")
            print(f"  Latency: {result_data['latency']:.2f}s")
        except Exception as e:
            print(f"  Error: {e}")
            results.append(None)

    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Description':<40} {'Model':<20} {'Cost':<12} {'Latency'}")
    print("-" * 70)
    for data in results:
        if data:
            print(
                f"{data['description']:<40} {data['model']:<20} "
                f"${data['cost']:<11.6f} {data['latency']:.2f}s"
            )
        else:
            print(f"{'Error':<40} {'N/A':<20}")


async def family_filtering_example():
    """Demonstrate family filtering."""
    print("\n\n")
    print("=" * 70)
    print("FAMILY FILTERING EXAMPLE")
    print("=" * 70)

    from app.router import SemanticRouter

    # Scenario: You only want to use Nova and Claude models
    print("\nEnabling only Nova and Claude families...")

    router = SemanticRouter(
        region='us-east-1',
        enabled_families={"Nova", "Claude"},
    )

    query = "Write a haiku about cloud computing."

    result = await router.route_and_respond(query, conversation_history=[])

    print(f"\nQuery: {query}")
    print(f"Selected Model: {result.family} (Tier {result.tier})")
    print(f"Explanation: {result.routing_explanation}")
    print(f"\nNote: Only Nova and Claude were considered, even though")
    print(f"      other families might have been cheaper or faster.")


async def main():
    """Run all examples."""
    import argparse

    parser = argparse.ArgumentParser(description="Semantic Router Quick Start")
    parser.add_argument(
        "--example",
        choices=["basic", "cost", "family", "all"],
        default="basic",
        help="Which example to run",
    )
    args = parser.parse_args()

    if args.example == "basic":
        await basic_example()
    elif args.example == "cost":
        await cost_comparison()
    elif args.example == "family":
        await family_filtering_example()
    elif args.example == "all":
        await basic_example()
        await cost_comparison()
        await family_filtering_example()

    print("\n\n" + "=" * 70)
    print("Next Steps:")
    print("=" * 70)
    print("1. Try the Streamlit demo: streamlit run app/demo.py")
    print("2. Run classification tests: python examples/test_router.py --mode classify")
    print("3. Read the README.md for more information")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback
        traceback.print_exc()
