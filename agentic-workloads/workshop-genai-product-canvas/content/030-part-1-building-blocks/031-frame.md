---
title: "Frame: Pipeline vs Loop, and the AgentCore Building Blocks"
weight: 31
---

## Two shapes of AI products

| | Pipeline (deterministic) | Loop (agentic) |
|---|---|---|
| Call graph | Fixed at design time | Discovered at runtime |
| Who decides what runs next | The product | The model |
| Latency | Predictable | Unbounded |
| Cost | Low (smallest model per step) | Higher (reasoning model, multi-turn) |
| Evaluation | Output quality per call | Task completion |
| When to use | You know the shape of the answer | You know the goal, not the path |

The test is: can you draw the call graph before you run the code? If yes, it is a
pipeline. If no, it is an agent.

For our scenario the investigation path differs per anomaly: a tapir drop is a
local land-use story, an elephant disappearance is a construction story, a
pangolin decline is a landscape-wide poaching story. You know the goal (explain
the anomaly, recommend action) but not the path. That is a loop.

## The five design decisions

Every AI feature must answer these. You capture them, along with the product's
shape and its tools, on the **GenAI Product Canvas** you will fill in for the
biodiversity use case in Part 2.

| # | Decision | Key question |
|---|----------|--------------|
| 1 | What's the outcome? | What artifact does the agent produce? |
| 2 | When is it done? | What signals loop termination? |
| 3 | Where's the human? | What is full-auto vs gated? |
| 4 | How fast is it? | Real-time, async, or scheduled? |
| 5 | Who's the user? | Who consumes the output, and how? |

## The AgentCore building blocks

Amazon Bedrock AgentCore is a set of services for running agents in production.
This workshop uses four of them:

| Building block | What it does |
|----------------|--------------|
| AgentCore Runtime | Serverless, secure hosting for your agent. Framework-agnostic (Strands, LangGraph, etc.). |
| AgentCore Gateway | Turns Lambdas / APIs / MCP servers into a single secure MCP tool endpoint, with inbound auth. |
| AgentCore Observability | OTEL traces plus CloudWatch GenAI dashboards: token usage, latency, tool spans. |
| AgentCore Identity | OAuth-based inbound/outbound auth for gateways and runtimes. |

### Where the model fits

The agent's reasoning runs on a Bedrock foundation model (Claude Sonnet).
AgentCore does not replace Bedrock; it wraps your agent (model plus prompt plus
tools) in production infrastructure. The model decides what to do; AgentCore
handles where it runs, how it reaches tools, and how you observe it.

### The one-picture mental model

```
        your prompt
            |
   [ AgentCore Runtime ]        <- serverless host for agent.py
            |
   Bedrock model (Claude)       <- the reasoning loop
            |  tool calls (MCP)
   [ AgentCore Gateway ]         <- one secure endpoint = 6 tools
            |
     Tool Lambda -> S3 datasets  <- your remote tools
            |
   [ Observability ]             <- OTEL spans: tokens, latency, tool order
```