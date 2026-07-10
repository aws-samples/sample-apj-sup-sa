# Examples Guide

This directory contains examples demonstrating the cost-optimized semantic router, from basic usage to advanced agent framework integrations.

## Table of Contents

- [Quick Start Examples](#quick-start-examples)
- [Agent Framework Integrations](#agent-framework-integrations)
- [How Routing Works](#how-routing-works)
- [Tool Calling Patterns](#tool-calling-patterns)
- [Testing & Validation](#testing--validation)
- [Configuration Guide](#configuration-guide)
- [Troubleshooting](#troubleshooting)

---

## Quick Start Examples

### Basic Usage (`quickstart.py`)

Interactive tutorials demonstrating core router functionality.

```bash
# Basic usage with 3 test queries
python examples/quickstart.py --example basic

# Cost comparison across routing strategies
python examples/quickstart.py --example cost

# Model family filtering demo
python examples/quickstart.py --example family

# Run all examples
python examples/quickstart.py --example all
```

**What you'll learn:**
- How to initialize and use SemanticRouter
- Query classification and model selection
- Cost tracking and statistics
- Different routing strategies

---

## Agent Framework Integrations

### Overview

The semantic router integrates with popular agent frameworks through custom tools. These examples demonstrate:

- **Automatic routing** based on query complexity, domain, and language
- **Cost optimization** using cheaper models when appropriate
- **Multi-turn conversations** with context awareness
- **Intelligent fallbacks** with automatic error handling

### 1. AWS Strands SDK (`strands_agent.py`)

Integration with AWS Strands Agent SDK.

**Features:**
- Custom `route_query` tool for intelligent routing
- Calculator and search tools
- Demo and interactive modes
- Full routing visibility with cost tracking

**Install:**
```bash
pip install strands-agents boto3
```

**Run:**
```bash
# Demo mode with 6 predefined scenarios
python examples/strands_agent.py --mode demo

# Interactive chat mode
python examples/strands_agent.py --mode interactive
```

**Key Integration:**
- Agent uses `@tool` decorator to expose routing functionality
- `route_query` tool wraps SemanticRouter for model selection
- Agent decides whether to use routing vs. direct tools (e.g., calculator)

### 2. Claude Agent SDK (`claude_agent.py`)

Integration with the official Claude Agent SDK.

**Features:**
- MCP server with custom routing tools
- Tool passthrough to routed models
- Agent-orchestrated routing
- Pre-approved tools for seamless execution

**Install:**
```bash
pip install claude-agent-sdk boto3 litellm
```

**Run:**
```bash
# Demo mode with test scenarios
python examples/claude_agent.py --mode demo

# Interactive chat mode
python examples/claude_agent.py --mode interactive
```

**Key Integration:**
- Uses `create_sdk_mcp_server()` to expose tools
- `ClaudeSDKClient` for persistent conversations
- Agent calls routing tool when needed

### 3. LangGraph (`langgraph_agent.py`)

Integration with LangGraph for stateful, graph-based agents.

**Features:**
- Stateful conversations with message history
- Custom `SemanticRouterNode` implementation
- Routing history tracking across turns
- Cost accumulation in agent state

**Install:**
```bash
pip install langgraph langchain-core boto3
```

**Run:**
```bash
# Demo mode - single-turn scenarios
python examples/langgraph_agent.py --mode demo

# Multi-turn conversation demo
python examples/langgraph_agent.py --mode conversation

# Interactive chat mode
python examples/langgraph_agent.py --mode interactive
```

**Key Integration:**
- `SemanticRouterNode` implements LangGraph node interface
- `AgentState` TypedDict tracks messages, routing history, and costs
- Compiled graph handles state transitions

### 4. Tool Passthrough (`strands_agent_with_tools.py`)

Advanced example showing how to pass tools through to routed models.

**Features:**
- Tools available to underlying Bedrock models
- Multi-turn tool use conversations
- Model-driven tool selection
- Bedrock tool use API integration

**Run:**
```bash
python examples/strands_agent_with_tools.py
```

**What's different:**
- Tools are passed to the routed Bedrock model
- The model decides when to use tools
- Supports complex multi-tool workflows

---

## How Routing Works

### 1. Query Classification

Each query is analyzed for:

| Signal | Description | Example Values |
|--------|-------------|----------------|
| **Complexity** | Overall difficulty (0.0-1.0) | 0.1 (simple), 0.8 (complex) |
| **Task Type** | Nature of the task | code, math, reasoning, translation |
| **Language** | Query language (ISO-639-1) | en, zh, ja, ko |
| **Context Depth** | History importance (0-3) | 0 (standalone), 3 (highly contextual) |
| **Domain Specificity** | Specialization needed (0.0-1.0) | 0.1 (general), 0.9 (specialized) |
| **Structured Output** | Needs formatted response | true (JSON/tables), false |
| **Token Count** | Approximate input size | 10 (short), 50000 (long) |

### 2. Model Selection

Based on classification signals:

| Signal/Condition | Routing Decision | Example Models | Cost Range |
|-----------------|------------------|----------------|------------|
| Complexity < 0.3 | **Tier 1** (Budget) | Nova Micro, Llama 3.3 70B | $0.035-0.09/1M tokens |
| Complexity 0.3-0.5 | **Tier 2** (Standard) | Qwen 32B, Llama 4 Scout | $0.17-0.20/1M tokens |
| Complexity 0.5-0.7 | **Tier 3** (Advanced) | DeepSeek V3, Claude Haiku | $0.62-0.80/1M tokens |
| Complexity > 0.7 | **Tier 4** (Expert) | Claude Sonnet 4.6 | $3.00/1M tokens |
| **Code tasks** | Qwen or DeepSeek | Specialized for coding | $0.20-0.62/1M tokens |
| **CJK languages** | Qwen | Chinese/Japanese/Korean | $0.20/1M tokens |
| **Structured output** | Claude Haiku | Best for JSON/schema | $0.80/1M tokens |
| **Long context** (>100k) | Llama 4 | 10M token context | $0.17-0.20/1M tokens |

### 3. Execution Flow

```
User Query
    ↓
┌─────────────────┐
│  Classifier     │ ← Analyzes query complexity, domain, language
└────────┬────────┘
         ↓
┌─────────────────┐
│  Selector       │ ← Selects optimal model based on signals
└────────┬────────┘
         ↓
┌─────────────────┐
│  Bedrock API    │ ← Direct AWS Bedrock calls
└────────┬────────┘
         ↓
     Response + Metadata (cost, latency, routing reason)
```

**Resilience features:**
- Direct AWS Bedrock API integration
- Automatic fallback to heuristic classification if LLM classification fails
- Error handling with detailed logging

### 4. Cost Optimization Examples

```python
# Simple factual query
"What is the capital of France?"
→ Nova Micro (Tier 1) - $0.000001 per query
→ 98% cost savings vs. Claude Sonnet

# Code generation
"Write a Python function for quicksort"
→ Qwen 32B (Tier 2) - $0.000015 per query
→ Specialized for code, 85% cheaper than Claude

# Complex reasoning
"Design a distributed system for 100M users"
→ Claude Sonnet 4.6 (Tier 4) - $0.000450 per query
→ Premium model only when quality is critical

# CJK language
"什么是人工智能？"
→ Qwen 32B (Tier 2) - $0.000012 per query
→ CJK specialist, better quality + lower cost
```

---

## Tool Calling Patterns

### Agent-Level Tool Selection (Simple)

The agent decides which tool to use **before** routing.

```python
from strands import Agent, tool

@tool
def route_query(query: str) -> str:
    """Route to optimal model."""
    result = asyncio.run(router.route_and_respond(query, []))
    return result.response

@tool
def calculate(expression: str) -> str:
    """Perform calculation."""
    return str(eval(expression))

agent = Agent(
    tools=[route_query, calculate],
    system_prompt="Use calculate for math, route_query for reasoning"
)
```

**Pros:** Simple, clear separation, easy to debug  
**Cons:** Agent must make smart tool choices, routed model can't use tools  
**Example:** `strands_agent.py`, `claude_agent.py`

### Tool Passthrough (Advanced)

Tools are passed **through** to the routed Bedrock model.

```python
@tool
def route_query_with_tools(query: str, available_tools: list[str]) -> str:
    """Route with tool access."""
    # 1. Select model
    model_id = select_model(query)
    
    # 2. Convert tools to Bedrock specs
    tool_specs = convert_to_bedrock_specs(available_tools)
    
    # 3. Call Bedrock with tools
    response = bedrock.converse(
        modelId=model_id,
        messages=[...],
        toolConfig={"tools": tool_specs}
    )
    
    # 4. Handle tool use if model requests it
    if has_tool_use(response):
        tool_results = execute_tools(response)
        final_response = bedrock.converse(...)  # Second call with results
        return extract_text(final_response)
    
    return extract_text(response)
```

**Pros:** Models can use tools, better for complex workflows  
**Cons:** More complex, higher cost (multi-turn), more tokens  
**Example:** `strands_agent_with_tools.py`

---

## Testing & Validation

### Test Router Classification

Test routing decisions without making actual LLM calls (fast).

```bash
# Test classification and routing logic only
python examples/test_router.py --mode classify

# Test with Ollama local classifier
python examples/test_router.py --mode classify
```

**Output:** Shows routing decisions for various query types without cost.

### Test with Live Models

Test with actual Bedrock model calls.

```bash
# Full integration test with LLM responses
python examples/test_router.py --mode full

# Compare different routing strategies
python examples/test_router.py --mode strategies

# Run all tests
python examples/test_router.py --mode all
```

### Test Agent Integrations

```bash
# Test Strands agent integration
python examples/strands_agent.py --mode demo

# Test Claude Agent SDK integration
python examples/claude_agent.py --mode demo

# Test LangGraph integration
python examples/langgraph_agent.py --mode demo
```

---

## Configuration Guide

### Basic Router Configuration

```python
from app.router import SemanticRouter

router = SemanticRouter(
    region='us-east-1',
    enabled_families={"Nova", "Claude", "Qwen", "DeepSeek", "Llama4"},
)
```

### AWS Credentials

The router uses boto3 for AWS Bedrock access.

```bash
# Option 1: AWS CLI
aws configure

# Option 2: Environment variables
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_DEFAULT_REGION=us-east-1

# Option 3: IAM role (on EC2/ECS/Lambda)
# Credentials are automatically available
```

### Model Family Filtering

Restrict to specific model families:

```python
# Only use Nova and Claude models
router = SemanticRouter(
    region='us-east-1',
    enabled_families={"Nova", "Claude"}
)

# Only use open-source models
router = SemanticRouter(
    region='us-east-1',
    enabled_families={"Llama4", "Qwen", "DeepSeek"}
)
```

---

## Troubleshooting

### Import Errors

```bash
# Error: "No module named 'app'"
# Solution: Run from project root
cd /path/to/semantic-prompt-routing
python examples/strands_agent.py

# Or add to PYTHONPATH
export PYTHONPATH=/path/to/semantic-prompt-routing:$PYTHONPATH
```

### Bedrock Access Errors

**Error:** `AccessDeniedException` or model not found

**Solution:**
1. Enable model access in AWS Bedrock console
2. Verify IAM permissions include `bedrock:InvokeModel`
3. Check model availability in your region (use `us-east-1` for all models)
4. Verify model ID matches available models

```bash
# List available Bedrock models
aws bedrock list-foundation-models --region us-east-1
```

### Ollama Classifier Issues

If using Ollama for local classification:

```bash
# Check Ollama is running
curl http://localhost:11434/api/tags

# Pull required model
ollama pull llama3.2
```

---

## Performance Tips

1. **Reuse router instances** - Initialization is expensive (~500ms)
2. **Use async/await** - All routing operations are async
3. **Restrict model families** - Reduces initialization time
4. **Monitor costs** - Use `router.get_stats()` to track spending
5. **Batch similar queries** - Amortize classifier overhead
6. **Use Ollama for classification** - Faster local classification without API calls

---

## Next Steps

- **Architecture:** Read the [main README](../README.md) for system architecture
- **Router Implementation:** Review [app/router.py](../app/router.py)
- **Classification Logic:** Check [app/routing/classifier.py](../app/routing/classifier.py)
- **Dashboard:** Explore the Streamlit dashboard: `streamlit run app/dashboard.py`
- **Contributing:** Found a bug or want to add an example? Open an issue or PR!

---

## File Index

| File | Description | Use Case |
|------|-------------|----------|
| `quickstart.py` | Basic router tutorials | Learning the basics |
| `test_router.py` | Router testing & validation | Testing, development |
| `strands_agent.py` | Strands SDK integration | Production agents (Strands) |
| `claude_agent.py` | Claude SDK integration | Production agents (Claude) |
| `langgraph_agent.py` | LangGraph integration | Stateful graph agents |
| `strands_agent_with_tools.py` | Advanced tool passthrough | Complex tool workflows |

---

## License

MIT License - see [LICENSE](../LICENSE) file for details
