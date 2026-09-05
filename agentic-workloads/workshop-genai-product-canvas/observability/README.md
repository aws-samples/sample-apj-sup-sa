# Observability: see the price mechanisms

Once the agent runs on AgentCore Runtime, you want to answer three questions:

1. **What did each investigation cost?** (input/output tokens per session)
2. **How long did it take, and where?** (latency, per-tool spans)
3. **Did it behave?** (tool call sequence, errors, session count)

AgentCore Observability answers all three through **OpenTelemetry (OTEL)** traces
surfaced in **Amazon CloudWatch GenAI Observability**.

## How it is wired in this repo

- `agent/requirements.txt` includes `aws-opentelemetry-distro` (the AWS Distro for
  OpenTelemetry / ADOT). When you deploy with the AgentCore CLI, the runtime
  **auto-instruments** your agent, so traces flow without extra code. Keeping the
  distro in requirements means self-managed hosts and local runs emit the same
  telemetry format.
- Strands emits OTEL-compatible GenAI spans out of the box (model calls, tool
  calls, token counts), so no per-tool instrumentation is required.

## One-time account setup: enable CloudWatch Transaction Search

Traces only show up after you turn on CloudWatch Transaction Search once per
account/region. Run:

```bash
bash observability/enable_observability.sh
```

Or in the console: **CloudWatch -> Application Signals / Transaction Search ->
Enable**, and grant X-Ray trace ingestion. This is a prerequisite for the GenAI
Observability dashboards.

## Where to look after invoking the agent

### With Transaction Search on (your own account)

1. Open the **CloudWatch** console in your region.
2. Left nav -> **GenAI Observability**.
3. Choose **Bedrock AgentCore** -> your agent (`biodiversity-anomaly-agent`).
4. You get:
   - **Sessions**: one row per investigation, with total tokens and duration.
   - **Traces / spans**: the model turns and each tool call (which of the six
     tools ran, in what order, how long each took).
   - **Metrics**: session count, latency (p50/p90), duration, **token usage**,
     and error rate.

### Without it (a temporary workshop account)

Every panel on that page reads *"No data - Enable Transaction Search"*, because the
page is built entirely on indexed spans. The telemetry still exists as ordinary
CloudWatch metrics, so use **CloudWatch -> Metrics** instead:

| Namespace | Metric | Answers |
|-----------|--------|---------|
| `bedrock-agentcore` | `gen_ai.client.token.usage`, split by `gen_ai.token.type` into input/output | What the run cost |
| `bedrock-agentcore` | `gen_ai.client.operation.duration` | How long the model calls took |
| `bedrock-agentcore` | `strands.tool.call_count`, `strands.tool.duration`, `strands.tool.success_count` | Per-tool behaviour |
| `bedrock-agentcore` | `strands.event_loop.cycle_count` | How many times round the loop |
| `AWS/Bedrock-AgentCore` | `Invocations`, dimensioned by tool `Name` | Which tools it chose, by name |
| `AWS/Bedrock-AgentCore` | `Sessions`, `Latency`, `Duration`, `Errors`, `Throttles` | Sessions, latency, failures |

The `AWS/Bedrock-AgentCore` metrics appear a couple of minutes after the first
invocation; the `bedrock-agentcore` ones take longer, because they leave the runtime
as embedded-metric-format records in its log group first.

## Turning telemetry into a cost number

Token usage is the price mechanism for an agentic loop. From a session's
input/output token totals:

```
cost ≈ (input_tokens  / 1000) * input_price_per_1k
     + (output_tokens / 1000) * output_price_per_1k
```

Compare that to the "COST ESTIMATE" box on your canvas. Teams almost always
guess the number of tool calls but under-estimate tokens, because each tool
result (detection records, news snippets) is fed back into the model's context.
That feedback is exactly why an agentic loop costs more than a fixed pipeline —
and seeing it in the dashboard is the point of this phase.

## Discussion prompts

- Which tool returned the largest payload, and how did that move token cost?
- Did the pangolin (multi-station) investigation cost more than the tapir one? Why?
- If you capped the loop at 4 tool calls instead of 8, what would you lose?
