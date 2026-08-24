---
title: "Observability: See the Price Mechanisms"
weight: 35
---

You just watched the demo agent investigate an anomaly. Before you design your own,
see what that run cost, how long it took, and which tools it chose, using the
OpenTelemetry metrics the AgentCore Runtime and the Gateway publish to CloudWatch.
Like the Part 1 demo, this is facilitator-driven.

:::alert{type="warning"}
**Read this before you open the console.** At an AWS-run event the
**CloudWatch -> GenAI Observability -> Bedrock AgentCore** page is **empty** - every
panel on it reads *"No data - Enable Transaction Search"*. That page is built
entirely on indexed transaction spans, and enabling Transaction Search needs
`xray:UpdateTraceSegmentDestination` plus a CloudWatch Logs resource policy on the
reserved `aws/spans` log group, which a temporary workshop account refuses even with
facilitator credentials.

That does **not** mean you have no telemetry. Token usage, per-tool call counts,
latency and errors are all published as ordinary CloudWatch **metrics** and are
there right now. The rest of this page uses those instead. In your own account you
can turn the span views on once - **CloudWatch -> Settings -> Transaction Search ->
Enable** - and get the trace waterfall as well.
:::

## Make some telemetry

The Part 1 invocations already produced metrics. Run a couple more if you want a
comparison between a single-station and an all-stations investigation:

```bash
agentcore invoke "Asian Elephant gone from STN-01 since March 2026. Investigate."
agentcore invoke "Sunda Pangolin declining across all stations Jan-Jul 2026. Investigate."
```

The two groups of metrics do not arrive together, so give them time before deciding
something is broken. Measured on a live event: the service metrics under
`AWS/Bedrock-AgentCore` were already there a couple of minutes after the first
invocation, while the agent's own `bedrock-agentcore` metrics - the token counts -
took a good deal longer, because they travel out through the runtime's log group as
embedded-metric-format records first. If the namespace reads *"There are no metrics
in this namespace"*, that is what you are waiting on. Start with `AWS/Bedrock-AgentCore`
below and come back to the token numbers in `bedrock-agentcore`.

## Where the numbers are

Open **CloudWatch -> Metrics -> All metrics** in your event's region. Two custom
namespaces matter.

### 1. `bedrock-agentcore` - what the agent itself emitted

Written by the OpenTelemetry instrumentation inside the runtime.

| Metric | What it tells you |
|--------|-------------------|
| `gen_ai.client.token.usage` | **The price mechanism.** Split by a `gen_ai.token.type` dimension of `input` or `output`, and by `gen_ai.request.model`. Select both types, set the statistic to **Sum**, and you have the run's token bill. |
| `gen_ai.client.operation.duration` | How long each model call took. |
| `strands.tool.call_count` | How many times the agent called a tool. |
| `strands.tool.duration` | Per-tool latency - which tool made the user wait. |
| `strands.tool.success_count` | Tool calls that returned cleanly. |
| `strands.event_loop.cycle_count` | **How many times round the loop.** This is the pipeline-versus-loop distinction from the previous section, as a number. |

A single tapir investigation in a test run of this workshop came to roughly
**18,800 input tokens and 1,900 output tokens** across all its model turns. Input
dominates, because every tool result is fed back into the next turn.

### 2. `AWS/Bedrock-AgentCore` - what the service saw

Written by AgentCore itself, so it is there whatever the agent is built with.

| Metric | What it tells you |
|--------|-------------------|
| `Invocations` | Dimensioned by tool `Name`, e.g. `wildlife-investigation-tools___check_land_use`. **Graph this by Name and you can see the path the agent chose** - which tools it used, and how often - without needing spans. |
| `Sessions`, `ActiveSessionCount` | One session per investigation. |
| `Latency`, `Duration`, `TargetExecutionTime` | End-to-end and per-target timing. |
| `Errors`, `UserErrors`, `SystemErrors`, `Throttles` | Where a run went wrong. |
| `InboundAuthorizationSuccess` | The Cognito JWT the Gateway checked on the way in. |

## Turn tokens into cost

Token usage is the price mechanism for an agentic loop:

```
cost = (input_tokens/1000)*input_price_per_1k + (output_tokens/1000)*output_price_per_1k
```

Take the input and output sums from `gen_ai.client.token.usage` and the per-1K
prices for your model from :link[Amazon Bedrock pricing]{href="https://aws.amazon.com/bedrock/pricing/" external=true}.
One investigation is cents. The number that matters is cents × how often you intend
to run it - which is exactly what your canvas has to answer.

## Also useful from the terminal

```bash
agentcore logs           # stream the runtime logs
agentcore traces list    # recent traces
```

The runtime's log group is
`/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`. The metrics above arrive
there first as embedded-metric-format records, so if a metric looks wrong the raw
event is in the log.

## Discuss

- Did the pangolin (all-stations) investigation cost more than the tapir
  (one-station) one? Why? (Hint: multi-station detection payloads are larger, and
  larger tool results mean more tokens fed back into the model.)
- Graph `Invocations` by tool `Name` for both runs. Did the agent take the same
  path? Should it have?
- Which tool returned the biggest payload, and how did that move the cost?
- If you halved the 8-call ceiling, what would you save and what would you risk?

Keep these numbers in mind. When you design your canvas next, you will put a COST
ESTIMATE on it, then compare against your own agent's real usage after you deploy
in Part 2.
