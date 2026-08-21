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

1. Open the **CloudWatch** console in your region.
2. Left nav -> **GenAI Observability**.
3. Choose **Bedrock AgentCore** -> your agent (`biodiversity-anomaly-agent`).
4. You get:
   - **Sessions**: one row per investigation, with total tokens and duration.
   - **Traces / spans**: the model turns and each tool call (which of the six
     tools ran, in what order, how long each took).
   - **Metrics**: session count, latency (p50/p90), duration, **token usage**,
     and error rate.

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
