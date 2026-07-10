"""Example: SemanticRouter with tool passthrough support.

This example shows how to pass tools through to the routed model,
enabling the underlying Bedrock models to use tools like calculator.

Install dependencies:
    pip install strands-agents boto3

Run:
    python examples/strands_agent_with_tools.py
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
# Helper Tools
# ---------------------------------------------------------------------------

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


@tool
def search_web(query: str) -> str:
    """Search the web for information (mock).

    Args:
        query: Search query

    Returns:
        Search results
    """
    # Mock implementation
    return f"Search results for '{query}': [Mock data - AWS Bedrock provides managed AI services]"


# ---------------------------------------------------------------------------
# Enhanced Routing Tool with Tool Passthrough
# ---------------------------------------------------------------------------

@tool
def route_query_with_tools(query: str, context: list[dict] = None, available_tools: list[str] = None) -> str:
    """Route a query to optimal model WITH access to tools.

    This version passes tool specifications to the underlying Bedrock model,
    allowing it to use tools like calculator, search, etc.

    Args:
        query: The user's query
        context: Optional conversation history
        available_tools: List of tool names the model can use

    Returns:
        Response from the selected model
    """
    if not _router:
        return "Error: Router not initialized"

    # Convert tool names to Bedrock tool specs
    tool_specs = []
    if available_tools:
        # Map tool names to their specifications
        tool_map = {
            "calculate": {
                "toolSpec": {
                    "name": "calculate",
                    "description": "Evaluate a mathematical expression",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "expression": {
                                    "type": "string",
                                    "description": "Mathematical expression to evaluate"
                                }
                            },
                            "required": ["expression"]
                        }
                    }
                }
            },
            "search_web": {
                "toolSpec": {
                    "name": "search_web",
                    "description": "Search the web for information",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Search query"
                                }
                            },
                            "required": ["query"]
                        }
                    }
                }
            }
        }

        for tool_name in available_tools:
            if tool_name in tool_map:
                tool_specs.append(tool_map[tool_name])

    # Route with tools - we need to extend SemanticRouter to support this
    # For now, we'll make a direct Bedrock call with tools
    import boto3
    import json

    # Use the router to select the model
    from app.routing.classifier import classify_query
    signals = asyncio.run(classify_query(query, context or []))
    litellm_model_name, routing_reason = _router.selector.select_model(signals)

    # Find the actual Bedrock model ID
    selected_model_id = "amazon.nova-micro-v1:0"
    selected_family = "Nova"
    selected_tier = 1

    for cfg in _router.selector._available_models:
        if f"{cfg.family.lower()}-tier{cfg.tier}" == litellm_model_name:
            selected_model_id = cfg.model_id
            selected_family = cfg.family
            selected_tier = cfg.tier
            break

    # Print routing information
    print(f"\n{'='*70}")
    print(f" Model: {selected_model_id} ({selected_family} Tier {selected_tier})")
    print(f" Routing: {routing_reason}")
    print(f" Complexity: {signals.complexity_score:.2f}")
    print(f" Task: {signals.task_type} | Lang: {signals.language}")
    if tool_specs:
        print(f" Tools Available: {', '.join(available_tools)}")
    print(f"{'='*70}\n")

    # Make Bedrock call with tools
    client = boto3.client("bedrock-runtime", region_name="us-east-1")

    messages = [{"role": "user", "content": [{"text": query}]}]

    request = {
        "modelId": selected_model_id,
        "messages": messages,
        "inferenceConfig": {
            "maxTokens": 2048,
            "temperature": 0.7,
        }
    }

    # Add tools if provided
    if tool_specs:
        request["toolConfig"] = {"tools": tool_specs}

    try:
        response = client.converse(**request)

        # Handle tool use in response
        output = response["output"]["message"]["content"]

        # Check if model wants to use tools
        tool_uses = [block for block in output if "toolUse" in block]

        if tool_uses:
            print(f" Model requested {len(tool_uses)} tool call(s)")

            # Execute tools
            tool_results = []
            for tool_use in tool_uses:
                tool_name = tool_use["toolUse"]["name"]
                tool_input = tool_use["toolUse"]["input"]
                tool_use_id = tool_use["toolUse"]["toolUseId"]

                print(f"  -> Executing: {tool_name}({tool_input})")

                # Execute the actual Python tool
                if tool_name == "calculate":
                    result = calculate(tool_input.get("expression", ""))
                elif tool_name == "search_web":
                    result = search_web(tool_input.get("query", ""))
                else:
                    result = f"Unknown tool: {tool_name}"

                tool_results.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": result}]
                    }
                })

            # Send tool results back to model
            messages.append({"role": "assistant", "content": output})
            messages.append({"role": "user", "content": tool_results})

            request["messages"] = messages
            response = client.converse(**request)
            output = response["output"]["message"]["content"]

        # Extract text response
        text_blocks = [block.get("text", "") for block in output if "text" in block]
        response_text = " ".join(text_blocks)

        # Calculate cost
        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        # Find model config for pricing
        model_cfg = None
        for cfg in _router.selector._available_models:
            if cfg.model_id == selected_model_id:
                model_cfg = cfg
                break

        if model_cfg:
            cost = (
                input_tokens * model_cfg.input_price / 1_000_000 +
                output_tokens * model_cfg.output_price / 1_000_000
            )
            print(f" Cost: ${cost:.6f} | Tokens: {input_tokens}in/{output_tokens}out\n")

        return response_text

    except Exception as e:
        return f"Error calling model: {str(e)}"


# ---------------------------------------------------------------------------
# Demo Mode
# ---------------------------------------------------------------------------

def run_demo():
    """Run demo showing tool passthrough."""

    global _router

    print(" Strands Agent + SemanticRouter with Tool Passthrough")
    print("=" * 70)
    print("\nThis demo shows how tools can be passed through to the routed model,")
    print("allowing the underlying Bedrock model to use tools like calculator.\n")

    # Initialize router
    _router = SemanticRouter(region='us-east-1')

    # Create agent with enhanced routing tool
    agent = Agent(
        system_prompt="""You are a helpful AI assistant with access to tools.

When answering queries:
1. If you need to calculate something, mention you'll use the calculator
2. If you need general reasoning, use route_query_with_tools
3. Pass the list of tools you might need to route_query_with_tools

Available tools: calculate, search_web""",
        tools=[route_query_with_tools, calculate, search_web],
    )

    # Test scenarios
    scenarios = [
        {
            "name": "Simple Math with Tool",
            "query": "What is 999 multiplied by 888? Please calculate it precisely.",
            "expected": "Calculator tool should be used"
        },
        {
            "name": "Reasoning Query",
            "query": "Explain the concept of recursion in programming.",
            "expected": "No tools needed, just routing"
        },
        {
            "name": "Math in Context",
            "query": "I have 15 boxes with 24 items each. How many total items do I have? Calculate it.",
            "expected": "Calculator tool through router"
        },
    ]

    print("\n Running Test Scenarios...\n")

    for i, scenario in enumerate(scenarios, 1):
        print(f"\n{'─'*70}")
        print(f"Scenario {i}/{len(scenarios)}: {scenario['name']}")
        print(f"Expected: {scenario['expected']}")
        print(f"{'─'*70}")
        print(f"\n Query: {scenario['query']}\n")

        try:
            # Invoke agent
            result = agent(scenario['query'])

            # Print response
            response_text = str(result)
            response_preview = response_text[:400] + "..." if len(response_text) > 400 else response_text
            print(f"\n Response:\n{response_preview}\n")

        except Exception as e:
            print(f"[ERROR] Error: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "="*70)
    print(" Summary")
    print("="*70)
    print("\n Tool Passthrough Working:")
    print("  • Tools are passed to the routed Bedrock model")
    print("  • The model can request tool execution")
    print("  • Results are fed back for final response")
    print("  • Cost-optimized routing maintained")

    print("\n Key Insight:")
    print("  The routed model has access to tools and can use them!")
    print("  This combines intelligent routing with tool-using capabilities.\n")


if __name__ == "__main__":
    run_demo()
