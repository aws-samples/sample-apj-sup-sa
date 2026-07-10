"""Example: Using SemanticRouter with LangGraph.

This example shows how to integrate the cost-optimized semantic router with
LangGraph to build a stateful agent with custom routing logic that optimizes
cost while maintaining quality.

Install dependencies:
    pip install langgraph langchain-core boto3

Run:
    python examples/langgraph_agent.py
"""

import asyncio
import operator
import sys
from pathlib import Path
from typing import Annotated, TypedDict, Sequence

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END

from app.router import SemanticRouter


# ---------------------------------------------------------------------------
# Agent State Definition
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """The state of our agent."""
    messages: Annotated[Sequence[BaseMessage], operator.add]
    routing_history: list[dict]
    total_cost: float


# ---------------------------------------------------------------------------
# SemanticRouter Integration Node
# ---------------------------------------------------------------------------

class SemanticRouterNode:
    """LangGraph node that uses SemanticRouter for model selection."""

    def __init__(self, router: SemanticRouter):
        self.router = router

    async def __call__(self, state: AgentState) -> dict:
        """Process state and generate response using hybrid routing.

        Args:
            state: Current agent state with messages and routing history

        Returns:
            Updated state dict with new message and routing info
        """
        messages = state["messages"]

        # Get the last user message
        user_messages = [msg for msg in messages if isinstance(msg, HumanMessage)]
        if not user_messages:
            return {
                "messages": [AIMessage(content="No user message found")],
                "routing_history": state.get("routing_history", []),
                "total_cost": state.get("total_cost", 0.0),
            }

        latest_query = user_messages[-1].content

        # Build conversation history for context
        history = []
        for msg in messages[:-1]:  # Exclude the latest message
            if isinstance(msg, HumanMessage):
                history.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                history.append({"role": "assistant", "content": msg.content})

        # Route and get response
        result = await self.router.route_and_respond(latest_query, history)

        # Print routing information
        print(f"\n{'='*70}")
        print(f"Model Routed: {result.model_used} ({result.family} Tier {result.tier})")
        print(f"Routing Logic: {result.routing_explanation}")
        print(f"Cost: ${result.cost_usd:.6f} | Latency: {result.latency_s:.2f}s")
        print(f"Complexity: {result.classification_signals.complexity_score:.2f}")
        print(f"{'='*70}\n")

        # Update routing history
        routing_info = {
            "query": latest_query,
            "model": result.model_used,
            "family": result.family,
            "tier": result.tier,
            "cost": result.cost_usd,
            "complexity": result.classification_signals.complexity_score,
            "task_type": result.classification_signals.task_type,
        }

        # Return state updates
        return {
            "messages": [AIMessage(content=result.response)],
            "routing_history": [routing_info],
            "total_cost": result.cost_usd,
        }


# ---------------------------------------------------------------------------
# Build LangGraph Agent
# ---------------------------------------------------------------------------

def build_agent(router: SemanticRouter) -> StateGraph:
    """Build a LangGraph agent with SemanticRouter integration.

    Args:
        router: Configured SemanticRouter instance

    Returns:
        Compiled StateGraph ready to use
    """
    # Create the graph
    workflow = StateGraph(AgentState)

    # Add the routing node
    router_node = SemanticRouterNode(router)
    workflow.add_node("route_and_respond", router_node)

    # Set entry point
    workflow.set_entry_point("route_and_respond")

    # Add edge to END
    workflow.add_edge("route_and_respond", END)

    # Compile and return
    return workflow.compile()


# ---------------------------------------------------------------------------
# Demo Scenarios
# ---------------------------------------------------------------------------

async def run_demo():
    """Run demo with various query types."""

    print("LangGraph + SemanticRouter Demo")
    print("=" * 70)
    print("\nThis agent uses LangGraph for state management and")
    print("SemanticRouter for intelligent, cost-optimized model selection.\n")

    # Initialize router
    router = SemanticRouter(region='us-east-1')

    # Build agent
    agent = build_agent(router)

    # Test queries
    test_scenarios = [
        {
            "name": "Simple Factual Query",
            "query": "What is the capital of Japan?",
            "expected_tier": "1 (Nova/Llama)"
        },
        {
            "name": "Code Generation",
            "query": "Write a Python function to find the longest common subsequence of two strings using dynamic programming.",
            "expected_tier": "2-3 (Qwen/DeepSeek)"
        },
        {
            "name": "CJK Language",
            "query": "日本の伝統文化について教えてください。",
            "expected_tier": "2 (Qwen - CJK specialist)"
        },
        {
            "name": "Complex System Design",
            "query": "Design a fault-tolerant distributed cache system that can handle 1M requests/second with sub-10ms latency. Discuss consistency models, replication strategies, and failure handling.",
            "expected_tier": "4 (Claude - Expert reasoning)"
        },
        {
            "name": "Structured Output",
            "query": "Create a JSON schema for an e-commerce order with nested customer, items, and payment information. Include validation rules.",
            "expected_tier": "3 (Claude Haiku - Structured output)"
        },
    ]

    all_routing_history = []

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n{'─'*70}")
        print(f"Scenario {i}/{len(test_scenarios)}: {scenario['name']}")
        print(f"Expected Routing: Tier {scenario['expected_tier']}")
        print(f"{'─'*70}")
        print(f"\n Query: {scenario['query']}\n")

        # Create initial state
        initial_state = {
            "messages": [HumanMessage(content=scenario['query'])],
            "routing_history": [],
            "total_cost": 0.0,
        }

        try:
            # Invoke agent
            result = await agent.ainvoke(initial_state)

            # Print response
            ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
            if ai_messages:
                print(f" Response:\n{ai_messages[-1].content}\n")

            # Store routing history
            all_routing_history.extend(result.get("routing_history", []))

        except Exception as e:
            print(f"[ERROR] Error: {e}")
            import traceback
            traceback.print_exc()

    # Print summary
    print("\n" + "="*70)
    print(" Session Summary")
    print("="*70)

    stats = router.get_stats()
    print(f"\nTotal Requests: {stats['total_requests']}")
    print(f"Total Cost: ${stats['total_cost_usd']:.6f}")
    print(f"Average Cost per Request: ${stats['avg_cost_per_request']:.6f}")
    print(f"Total Latency: {stats['total_latency_s']:.2f}s")
    print(f"Average Latency: {stats['avg_latency_per_request']:.2f}s")

    # Routing distribution
    print("\n Routing Distribution:")
    tier_counts = {}
    for entry in all_routing_history:
        tier = entry["tier"]
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    for tier in sorted(tier_counts.keys()):
        count = tier_counts[tier]
        percentage = (count / len(all_routing_history)) * 100 if all_routing_history else 0
        print(f"  Tier {tier}: {count} requests ({percentage:.1f}%)")

    print("\n Cost Optimization Insights:")
    print("  • Simple queries automatically routed to budget models")
    print("  • Code tasks routed to specialized models (Qwen/DeepSeek)")
    print("  • Complex reasoning routed to premium models only when needed")
    print("  • Domain-specific routing (CJK → Qwen, structured → Claude)")

    print("\n Done!\n")


# ---------------------------------------------------------------------------
# Multi-Turn Conversation Demo
# ---------------------------------------------------------------------------

async def run_conversation_demo():
    """Run a multi-turn conversation with context awareness."""

    print("\n LangGraph Multi-Turn Conversation Demo")
    print("=" * 70)
    print("\nDemonstrating context-aware routing across multiple turns.\n")

    # Initialize router
    router = SemanticRouter(region='us-east-1')

    # Build agent
    agent = build_agent(router)

    # Conversation with follow-ups
    conversation = [
        "What are the main cloud computing services?",
        "Can you explain the first one in more detail?",
        "Write a Python script to connect to AWS S3 and list all buckets.",
        "Now add error handling and logging to that script.",
    ]

    # Start with initial state
    state = {
        "messages": [],
        "routing_history": [],
        "total_cost": 0.0,
    }

    print("  Starting Conversation:\n")

    for turn, query in enumerate(conversation, 1):
        print(f"\n{'─'*70}")
        print(f"Turn {turn}/{len(conversation)}")
        print(f"{'─'*70}")
        print(f"User: User: {query}\n")

        # Add user message to state
        state["messages"].append(HumanMessage(content=query))

        # Invoke agent
        result = await agent.ainvoke(state)

        # Extract AI response
        ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
        if ai_messages:
            ai_response = ai_messages[-1].content
            print(f" Assistant: {ai_response}\n")

            # Update state with full message history
            state["messages"] = result["messages"]
            state["routing_history"].extend(result.get("routing_history", []))
            state["total_cost"] += result.get("total_cost", 0.0)

    # Final stats
    print("\n" + "="*70)
    print(" Conversation Summary")
    print("="*70)

    stats = router.get_stats()
    print(f"\nTotal Turns: {len(conversation)}")
    print(f"Total Cost: ${stats['total_cost_usd']:.6f}")
    print(f"Average Cost per Turn: ${stats['avg_cost_per_request']:.6f}")

    print("\n Notice how routing adapts based on:")
    print("  • Query complexity (simple → Tier 1, code → Tier 2-3)")
    print("  • Context depth (follow-ups maintain conversation flow)")
    print("  • Task type (factual vs. code generation)")

    print("\n Done!\n")


# ---------------------------------------------------------------------------
# Interactive Mode
# ---------------------------------------------------------------------------

async def run_interactive():
    """Run interactive chat with LangGraph + SemanticRouter."""

    print("\n Interactive Mode - LangGraph Agent")
    print("=" * 70)
    print("Type your questions below. Type 'quit' or 'exit' to end.\n")

    # Initialize router
    router = SemanticRouter(region='us-east-1')

    # Build agent
    agent = build_agent(router)

    # Initial state
    state = {
        "messages": [],
        "routing_history": [],
        "total_cost": 0.0,
    }

    while True:
        try:
            # Get user input
            user_input = input("\nUser: You: ").strip()

            if user_input.lower() in ['quit', 'exit', 'q']:
                print("\n Goodbye!")
                break

            if not user_input:
                continue

            # Add to state
            state["messages"].append(HumanMessage(content=user_input))

            # Invoke agent
            result = await agent.ainvoke(state)

            # Print response
            ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
            if ai_messages:
                print(f"\n Assistant: {ai_messages[-1].content}")

            # Update state
            state["messages"] = result["messages"]
            state["routing_history"].extend(result.get("routing_history", []))
            state["total_cost"] += result.get("total_cost", 0.0)

        except KeyboardInterrupt:
            print("\n\n Goodbye!")
            break
        except Exception as e:
            print(f"\n[ERROR] Error: {e}")

    # Print final stats
    print("\n" + "="*70)
    stats = router.get_stats()
    print(f" Session Stats: {stats['total_requests']} requests, ${stats['total_cost_usd']:.6f} total cost")
    print("="*70 + "\n")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="LangGraph with SemanticRouter demo"
    )
    parser.add_argument(
        "--mode",
        choices=["demo", "conversation", "interactive"],
        default="demo",
        help="Run mode: demo (single-turn scenarios), conversation (multi-turn), or interactive (chat)",
    )

    args = parser.parse_args()

    if args.mode == "demo":
        asyncio.run(run_demo())
    elif args.mode == "conversation":
        asyncio.run(run_conversation_demo())
    else:
        asyncio.run(run_interactive())
