"""Example: Using SemanticRouter with AWS Strands SDK.

This example shows how to integrate the cost-optimized semantic router with
AWS Strands SDK by creating a custom tool that routes queries through the
SemanticRouter for intelligent model selection.

## Tool Call Architecture:

There are two approaches for handling tools:

### Approach 1: Agent-Level Tool Selection (This Example)
- The Strands agent has multiple tools: route_query, calculate, etc.
- The agent decides which tool to use for each query
- For calculations → agent calls calculate directly
- For reasoning → agent calls route_query
- Simple but requires agent to make good tool choices

### Approach 2: Tool Passthrough (See strands_agent_with_tools.py)
- Tools are passed through to the routed Bedrock model
- The Bedrock model can use tools like calculate, search, etc.
- More complex but gives routed models tool capabilities
- Requires Bedrock tool use API integration

Install dependencies:
    pip install strands-agents boto3

Run:
    python examples/strands_agent.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from strands import Agent, tool

from app.router import SemanticRouter


# ---------------------------------------------------------------------------
# Global Router Instance
# ---------------------------------------------------------------------------

_router: SemanticRouter = None


# ---------------------------------------------------------------------------
# Custom Routing Tool
# ---------------------------------------------------------------------------

@tool
def route_query(query: str, context: list[dict] = None) -> str:
    """Route a query to the optimal model and get a response.

    Args:
        query: The user's query to route
        context: Optional conversation history

    Returns:
        Response from the selected model with routing metadata
    """
    if not _router:
        return "Error: Router not initialized"

    # Run async route_and_respond in sync context
    result = asyncio.run(_router.route_and_respond(query, context or []))

    # Print routing information
    print(f"\n{'='*70}")
    print(f" Model: {result.model_used} ({result.family} Tier {result.tier})")
    print(f" Routing: {result.routing_explanation}")
    print(f" Cost: ${result.cost_usd:.6f} |  Latency: {result.latency_s:.2f}s")
    print(f" Complexity: {result.classification_signals.complexity_score:.2f}")
    print(f"  Task: {result.classification_signals.task_type} | Lang: {result.classification_signals.language}")
    print(f"{'='*70}\n")

    # Return response with metadata
    return f"""{result.response}

---
**Routing Info:**
- Model: {result.model_used} ({result.family} Tier {result.tier})
- Cost: ${result.cost_usd:.6f}
- Latency: {result.latency_s:.2f}s
- Complexity: {result.classification_signals.complexity_score:.2f}
- Task Type: {result.classification_signals.task_type}
- Language: {result.classification_signals.language}"""


@tool
def calculate(expression: str) -> str:
    """Evaluate a mathematical expression.

    Args:
        expression: Mathematical expression to evaluate

    Returns:
        The result of the calculation
    """
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return f"Result: {result}"
    except Exception as e:
        return f"Error: {str(e)}"


# ---------------------------------------------------------------------------
# Demo Mode
# ---------------------------------------------------------------------------

def run_demo():
    """Run demo with various query types."""

    global _router

    print(" AWS Strands SDK + SemanticRouter Demo")
    print("=" * 70)
    print("\nThis demo uses Strands Agent SDK with a custom routing tool")
    print("that intelligently selects the best model for each query.\n")

    # Initialize router
    _router = SemanticRouter(region='us-east-1')

    # Create agent with routing tool
    agent = Agent(
        system_prompt="""You are a helpful AI assistant with access to an intelligent routing system.

When answering user queries, you should:
1. Use the 'route_query' tool to get responses for questions that need model inference
2. Pass the user's query to the tool
3. Present the response along with the routing information

The routing system automatically selects the most cost-effective model based on query complexity, domain, and language.""",
        tools=[route_query, calculate],
    )

    # Test scenarios
    scenarios = [
        {
            "name": "Simple Factual Query",
            "query": "What is the capital of Japan?",
            "expected": "Tier 1 (Nova) - Simple factual"
        },
        {
            "name": "Code Generation",
            "query": "Write a Python function to find the factorial of a number using recursion.",
            "expected": "Tier 2 (Qwen) - Code task"
        },
        {
            "name": "CJK Language - Chinese",
            "query": "什么是机器学习？请简单解释。",
            "expected": "Tier 2 (Qwen) - CJK specialist"
        },
        {
            "name": "CJK Language - Japanese",
            "query": "日本の首都はどこですか？",
            "expected": "Tier 2 (Qwen) - CJK specialist"
        },
        {
            "name": "Math with Calculator",
            "query": "What is 999 multiplied by 888? Use the calculator.",
            "expected": "Calculator tool usage"
        },
        {
            "name": "Complex System Design",
            "query": "Design a distributed caching system for a social media platform with 50M users. Discuss cache invalidation, consistency, and performance optimization strategies.",
            "expected": "Tier 4 (Claude) - Expert reasoning"
        },
    ]

    print("\n Running Test Scenarios...\n")

    for i, scenario in enumerate(scenarios, 1):
        print(f"\n{'─'*70}")
        print(f"Scenario {i}/{len(scenarios)}: {scenario['name']}")
        print(f"Expected: {scenario['expected']}")
        print(f"{'─'*70}")
        print(f"\n Query: {scenario['query'][:100]}{'...' if len(scenario['query']) > 100 else ''}\n")

        try:
            # Invoke agent
            result = agent(scenario['query'])

            # Print response
            response_text = str(result)
            response_preview = response_text[:500] + "..." if len(response_text) > 500 else response_text
            print(f"\n Response:\n{response_preview}\n")

        except Exception as e:
            print(f"[ERROR] Error: {e}")
            import traceback
            traceback.print_exc()

    # Print summary statistics
    print("\n" + "="*70)
    print(" Session Summary")
    print("="*70)

    stats = _router.get_stats()
    print(f"\nTotal Requests: {stats['total_requests']}")
    print(f"Total Cost: ${stats['total_cost_usd']:.6f}")
    print(f"Average Cost per Request: ${stats['avg_cost_per_request']:.6f}")
    print(f"Total Latency: {stats['total_latency_s']:.2f}s")
    print(f"Average Latency: {stats['avg_latency_per_request']:.2f}s")

    print("\n Cost Optimization Benefits:")
    print("  • Simple queries → Budget models (85-95% cost savings)")
    print("  • Code tasks → Specialized models (Qwen/DeepSeek)")
    print("  • CJK languages → Qwen for better quality")
    print("  • Complex reasoning → Premium models only when needed")
    print("  • Automatic fallbacks for reliability")

    print("\n Done!\n")


# ---------------------------------------------------------------------------
# Interactive Mode
# ---------------------------------------------------------------------------

def run_interactive():
    """Run interactive chat mode."""

    global _router

    print("\n Interactive Mode - Strands Agent with SemanticRouter")
    print("=" * 70)
    print("Type your questions. Commands: 'quit'/'exit' to end, 'stats' for session stats.\n")

    # Initialize router
    _router = SemanticRouter(region='us-east-1')

    # Create agent
    agent = Agent(
        system_prompt="""You are a helpful AI assistant with access to an intelligent routing system.

When answering queries, use the 'route_query' tool to get optimized responses.""",
        tools=[route_query, calculate],
    )

    conversation_history = []

    while True:
        try:
            user_input = input("\nUser: You: ").strip()

            if user_input.lower() in ['quit', 'exit', 'q']:
                print("\n Goodbye!")
                break

            if user_input.lower() == 'stats':
                stats = _router.get_stats()
                print(f"\n Stats: {stats['total_requests']} requests, ${stats['total_cost_usd']:.6f} cost")
                continue

            if not user_input:
                continue

            # Add to conversation history
            conversation_history.append({"role": "user", "content": user_input})

            # Invoke agent with history
            result = agent(user_input, messages=conversation_history)

            # Print response
            print(f"\n Assistant: {result}")

            # Add to conversation history
            conversation_history.append({"role": "assistant", "content": str(result)})

        except KeyboardInterrupt:
            print("\n\n Goodbye!")
            break
        except Exception as e:
            print(f"\n[ERROR] Error: {e}")
            import traceback
            traceback.print_exc()

    # Final stats
    stats = _router.get_stats()
    print(f"\n{'='*70}")
    print(f" Final Stats: {stats['total_requests']} requests, ${stats['total_cost_usd']:.6f} total cost")
    print(f"{'='*70}\n")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="AWS Strands SDK with SemanticRouter integration"
    )
    parser.add_argument(
        "--mode",
        choices=["demo", "interactive"],
        default="demo",
        help="Run mode: demo (test scenarios) or interactive (chat)",
    )

    args = parser.parse_args()

    if args.mode == "demo":
        run_demo()
    else:
        run_interactive()
