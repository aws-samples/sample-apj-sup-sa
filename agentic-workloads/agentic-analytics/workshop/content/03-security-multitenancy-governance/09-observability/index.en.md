---
title: "Step 9: Observability"
weight: 60
---

## Learning Objectives

By the end of this step, you will:
- Understand how AgentCore automatically instruments your agent with OpenTelemetry
- Trace an agent invocation end-to-end in CloudWatch (Session → Trace → Span)
- Debug a specific interaction by drilling into tool calls, model reasoning, and policy decisions
- Compare a successful trace with a guardrail-blocked trace

## Why Observability?

Your agent has implemented many techniques and components by now — but as the platform team, you need to answer: "How do we know the agent is giving correct answers? What if a tenant reports a wrong revenue number?"

:link[AgentCore Observability]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html"} automatically captures every agent interaction as :link[OpenTelemetry]{href="https://opentelemetry.io/"} traces. Everything flows to :link[CloudWatch GenAI Observability]{href="https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/GenAI-observability.html"}.

## How It Works — The OTEL Pipeline

You may have noticed two libraries in `requirements.txt` that you haven't used directly:

```
strands-agents[otel]         # Makes Strands emit OpenTelemetry traces
aws-opentelemetry-distro     # AWS Distro for OpenTelemetry — routes traces to CloudWatch
```

Here's what happens behind the scenes once your agent is deployed to the Runtime:

```
Your Agent Code (Strands + BedrockAgentCoreApp)
    │
    │  strands-agents[otel] emits OTEL spans
    │  (model calls, tool calls, agent loop)
    │
    ▼
AgentCore Runtime wraps with opentelemetry-instrument
    │  (automatic — no Dockerfile changes needed)
    │
    ▼
aws-opentelemetry-distro (ADOT SDK)
    │  Routes spans/metrics to CloudWatch
    │
    ▼
CloudWatch Transaction Search
    │  (stores spans in aws/spans log group)
    │
    ▼
GenAI Observability Dashboard
    (Sessions → Traces → Spans)
```

::alert[**No code changes needed for Runtime tracing.** The two libraries in `requirements.txt` + AgentCore Runtime's auto-instrumentation handle everything. Gateway and Memory resources need explicit log/trace delivery configuration — and that's already part of your stack's baseline (it came live with the first `make deploy` in Step 2).]{type="info"}

## Lab Procedures

### Step 9.1: Observability is Already Configured

You didn't have to do anything extra for observability — the log/trace delivery resources for the Gateway and Memory are part of the top-up template's **baseline**, so they were created by your very first `make deploy` in Step 2. CloudWatch Transaction Search was enabled by the base CloudFormation stack during provisioning.

What's already configured:
- **Log delivery** — Gateway and Memory APPLICATION_LOGS flow to CloudWatch Logs
- **Tracing delivery** — Gateway and Memory spans flow to X-Ray → CloudWatch Transaction Search

::alert[**What about Runtime?** Runtime tracing is automatic — AgentCore creates the log group and configures tracing when the Runtime is deployed. The Gateway/Memory delivery resources in the template cover the rest.]{type="info"}

### Step 9.2: Generate Some Traces

Make a few agent invocations so there's data to observe. In the chat UI, log in as:

| Field | Value |
|-------|-------|
| Username | `orion.moonshadow@example-mythicalunicorns.com` |
| Password | `Unicorn123!` |

Then ask these questions (clear chat between each to start fresh sessions):

1. "Show me top 5 customers by revenue"
2. "Is it a good time to invest in gold?"

The second query should be blocked by guardrails — we'll see that in the traces.

### Step 9.3: Open the GenAI Dashboard

1. Open the **AWS Console**, go to **CloudWatch** service, click **GenAI Observability** on left pane, and click **Bedrock AgentCore** menu.
2. Find your agent (`unicorn_rental_agent`) → click it → select **DEFAULT** endpoint
3. Set the time range to **1 hour** in the upper right

::alert[If you don't see your agent, ensure you're in the correct region (us-east-1), that you've invoked the agent at least once, and that Transaction Search is enabled (CloudWatch → Settings → X-Ray traces tab → Transaction Search should show "Enabled").]{type="info"}

### Step 9.4: Explore the Session → Trace → Span Hierarchy

AgentCore organizes telemetry in three levels:

| Level | What It Represents | Example |
|-------|-------------------|---------|
| **Session** | An entire conversation (all turns) | User's chat session |
| **Trace** | One request-response cycle | "Show me top 5 customers" → response |
| **Span** | Individual operation within a trace | Model call, tool call, guardrail check |

1. Select the **Traces** tab → click the newest trace
2. In the **Spans Timeline**, you can see:
   - **Model invocations** — how long the LLM took to reason and which model was used
   - **Tool calls** — which tool was selected, the input arguments, and the response
   - **Latency breakdown** — where time was spent (model vs tool vs Gateway)

### Step 9.5: Debug the Guardrail Trace

Find the trace for "Is it a good time to invest in gold?" — this one should be the newest one, the same one from step 9.4:

1. The span will show the guardrail evaluation
2. You'll see the guardrail **intervened** and blocked the response
3. Compare this with a successful trace (from question "Show me top 5 customers by revenue")

Please note the Session ID of the trace from question "Show me top 5 customers by revenue" to be used in step 10.

### Step 9.6: View Gateway Logs

The Gateway logs show policy decisions and tool routing:

```bash
aws logs describe-log-groups \
  --log-group-name-prefix /aws/vendedlogs/bedrock-agentcore/gateway \
  --query 'logGroups[*].logGroupName'
```

Open the log group in the CloudWatch console. These logs show every policy decision (ALLOW/DENY), which target Lambda was called, and the latency of each tool invocation.

::alert[Gateway logs depend on the log-delivery resources in the template baseline — they were created by your first `make deploy` in Step 2.]

## Verification

- Observability came live with the Step-2 baseline `make deploy` (no extra step)
- You can see your agent's sessions and traces in the GenAI dashboard
- You can drill into a span and see model inputs/outputs and tool call details
- The guardrail trace shows intervention (no tool calls for blocked queries)
- Gateway logs show policy decisions for recent tool calls

## Troubleshooting

**Agent not appearing in GenAI dashboard**
- Ensure you're in the correct region (us-east-1).
- Send a query through the demo UI chat
- Set the time range to a wider window (e.g., 3 hours).
- Verify Transaction Search is enabled: CloudWatch → Settings → X-Ray traces tab. If not enabled, the CloudFormation stack may have had an issue — you can enable it manually via the console.

**No Gateway logs found**
- The Gateway log-delivery resources are in the template baseline; confirm your Step-2 `make deploy` reached `CREATE_COMPLETE`.
- Gateway logging may take a few minutes to appear after invocations.

**Traces appear but no spans (flat trace)**
- Verify `strands-agents[otel]` and `aws-opentelemetry-distro` are in `requirements.txt`.
- Run `make build` to rebuild the agent image so it picks up the OTEL libraries, then retry.

## Summary

You explored the observability stack: CloudWatch traces for debugging, spans for understanding agent behavior, and Gateway logs for policy decisions. The key insight: Runtime tracing is automatic (via `strands-agents[otel]` + `aws-opentelemetry-distro`), but Gateway and Memory need explicit log/trace delivery configuration.

You can now trace any tenant interaction end-to-end and debug issues in production.

Next, you'll measure agent quality with evaluations → [Step 10: Evaluation](../10-evaluation/)

## Reference Materials

- :link[AgentCore Observability]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html"}
- :link[Add Observability to AgentCore Resources]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html"}
- :link[CloudWatch GenAI Observability]{href="https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/GenAI-observability.html"}
- :link[AgentCore Observability Telemetry]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-telemetry.html"}
- :link[Build Trustworthy AI Agents with AgentCore Observability]{href="https://aws.amazon.com/blogs/machine-learning/build-trustworthy-ai-agents-with-amazon-bedrock-agentcore-observability/"}
