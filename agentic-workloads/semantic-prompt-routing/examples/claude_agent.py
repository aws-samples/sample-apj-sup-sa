"""Example: Using SemanticRouter with Claude Agent SDK.

This example shows how to integrate the cost-optimized semantic router with
the official Claude Agent SDK by creating custom tools that route queries
through the SemanticRouter for intelligent model selection.

Install dependencies:
    pip install claude-agent-sdk boto3

Run:
    python examples/claude_agent.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_agent_sdk import query, ClaudeSDKClient, ClaudeAgentOptions, tool, create_sdk_mcp_server

from app.router import SemanticRouter


# ---------------------------------------------------------------------------
# Custom Routing Tool
# ---------------------------------------------------------------------------

# Global router instance (initialized in main)
_router: SemanticRouter = None


@tool("route_query", "Route a query to the optimal model and get a response", {"query": str, "context": list})
async def route_query_tool(args):
    """Tool that routes queries through SemanticRouter for cost-optimized model selection.

    Args:
        query: The user's query
        context: Optional conversation history

    Returns:
        Response from the selected model with routing metadata
    """
    query_text = args.get("query", "")
    context = args.get("context", [])

    if not _router:
        return {
            "content": [{
                "type": "text",
                "text": "Error: Router not initialized"
            }]
        }

    # Route and get response
    result = await _router.route_and_respond(query_text, context)

    # Print routing information
    print(f"\n{'='*70}")
    print(f" Model: {result.model_used} ({result.family} Tier {result.tier})")
    print(f" Routing: {result.routing_explanation}")
    print(f" Cost: ${result.cost_usd:.6f} | Latency: {result.latency_s:.2f}s")
    print(f" Complexity: {result.classification_signals.complexity_score:.2f}")
    print(f" Task: {result.classification_signals.task_type} | Lang: {result.classification_signals.language}")
    print(f"{'='*70}\n")

    # Return response with metadata
    return {
        "content": [{
            "type": "text",
            "text": f"{result.response}\n\n---\n**Routing Info:**\n- Model: {result.model_used} ({result.family} Tier {result.tier})\n- Cost: ${result.cost_usd:.6f}\n- Complexity: {result.classification_signals.complexity_score:.2f}\n- Task: {result.classification_signals.task_type}"
        }]
    }


# ---------------------------------------------------------------------------
# Additional Helper Tools
# ---------------------------------------------------------------------------

@tool("calculate", "Perform mathematical calculations", {"expression": str})
async def calculate_tool(args):
    """Simple calculator tool."""
    expression = args.get("expression", "")
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return {
            "content": [{
                "type": "text",
                "text": f"Result: {result}"
            }]
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": f"Error: {str(e)}"
            }]
        }


# ---------------------------------------------------------------------------
# Demo Mode
# ---------------------------------------------------------------------------

async def run_demo():
    """Run demo with various query types."""

    global _router

    print(" Claude Agent SDK + SemanticRouter Demo")
    print("=" * 70)
    print("\nThis demo uses the Claude Agent SDK with a custom routing tool")
    print("that intelligently selects the best model for each query.\n")

    # Initialize router
    _router = SemanticRouter(region='us-east-1')

    # Create MCP server with custom tools
    mcp_server = create_sdk_mcp_server(
        name="hybrid-router",
        version="1.0.0",
        tools=[route_query_tool, calculate_tool]
    )

    # Configure agent options
    options = ClaudeAgentOptions(
        system_prompt="""You are a helpful AI assistant with access to an intelligent routing system.

When answering user queries, you should:
1. Use the 'route_query' tool to get responses for questions
2. Pass the user's query and any relevant conversation context
3. Present the response along with routing information

The routing system will automatically select the most cost-effective model based on query complexity, domain, and language.""",
        mcp_servers={"router": mcp_server},
        allowed_tools=["mcp__router__route_query", "mcp__router__calculate"],
        max_turns=10,
    )

    # Test scenarios
    scenarios = [
        {
            "name": "Simple Factual Query",
            "query": "What is the capital of France?",
            "expected": "Tier 1 (Nova) - Simple factual"
        },
        {
            "name": "Code Generation",
            "query": "Write a Python function to reverse a string without using built-in reverse functions.",
            "expected": "Tier 2 (Qwen) - Code task"
        },
        {
            "name": "CJK Language - Chinese",
            "query": "什么是人工智能？请简单解释。",
            "expected": "Tier 2 (Qwen) - CJK specialist"
        },
        {
            "name": "Math with Calculator",
            "query": "What is 12345 multiplied by 6789? Use the calculator.",
            "expected": "Calculator tool usage"
        },
        {
            "name": "Complex System Design",
            "query": "Design a microservices architecture for an e-commerce platform with 5M+ daily users. Include service boundaries, data flow, and fault tolerance strategies.",
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
            # Use ClaudeSDKClient for proper conversation flow
            async with ClaudeSDKClient(options=options) as client:
                # Send the query
                await client.query(scenario['query'])

                # Receive and collect the response
                response_text = ""
                async for message in client.receive_response():
                    if hasattr(message, 'content'):
                        for block in message.content:
                            if hasattr(block, 'text'):
                                response_text += block.text

                # Print response
                response_preview = response_text[:400] + "..." if len(response_text) > 400 else response_text
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

async def run_interactive():
    """Run interactive chat mode."""

    global _router

    print("\n Interactive Mode - Claude Agent with SemanticRouter")
    print("=" * 70)
    print("Type your questions. Commands: 'quit'/'exit' to end, 'stats' for session stats.\n")

    # Initialize router
    _router = SemanticRouter(region='us-east-1')

    # Create MCP server
    mcp_server = create_sdk_mcp_server(
        name="hybrid-router",
        version="1.0.0",
        tools=[route_query_tool, calculate_tool]
    )

    # Configure options
    options = ClaudeAgentOptions(
        system_prompt="""You are a helpful AI assistant with access to an intelligent routing system.

When answering queries, use the 'route_query' tool to get optimized responses.""",
        mcp_servers={"router": mcp_server},
        allowed_tools=["mcp__router__route_query", "mcp__router__calculate"],
        max_turns=20,
    )

    # Use ClaudeSDKClient for persistent conversation
    async with ClaudeSDKClient(options=options) as client:
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

                # Send query
                await client.query(user_input)

                # Receive response
                print("\n Assistant: ", end="", flush=True)
                async for msg in client.receive_response():
                    if hasattr(msg, 'content'):
                        for block in msg.content:
                            if hasattr(block, 'text'):
                                print(block.text, end="", flush=True)
                print()  # New line after response

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
        description="Claude Agent SDK with SemanticRouter integration"
    )
    parser.add_argument(
        "--mode",
        choices=["demo", "interactive"],
        default="demo",
        help="Run mode: demo (test scenarios) or interactive (chat)",
    )

    args = parser.parse_args()

    if args.mode == "demo":
        asyncio.run(run_demo())
    else:
        asyncio.run(run_interactive())
