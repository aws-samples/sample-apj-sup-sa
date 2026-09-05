---
title: "Observability: See the Price Mechanisms"
weight: 35
---

You just watched the demo agent investigate an anomaly. Before you design your own,
see what that run cost, how long it took, and which tools it chose, using the
OpenTelemetry metrics the AgentCore Runtime and the Gateway publish to CloudWatch.
Like the Part 1 demo, this is facilitator-driven.

:::alert{type="warning"}
**Read this before you open the console.** At an AWS-run event, **CloudWatch ->
GenAI Observability -> Bedrock AgentCore** stays empty - every panel reads *"No
data - Enable Transaction Search"* - because Transaction Search is not turned on by
default: it indexes 100% of spans into the reserved `aws/spans` log group, and a
temporary workshop account leaves it off to avoid the added log storage and
ingestion cost. 

That does **not** mean you have no telemetry - CloudWatch
**metrics** already give you aggregated token usage, per-tool call counts, latency
and errors, and the rest of this page uses those instead. If you want the per-run
span views, you can enable it once in your own account - **CloudWatch -> Settings ->
Transaction Search -> Enable**.
:::

## Make some telemetry

The Part 1 invocations already produced metrics. Run a couple more if you want a
comparison between a single-station and an all-stations investigation:

```bash
agentcore invoke "Asian Elephant gone from STN-01 since March 2026. Investigate."
agentcore invoke "Sunda Pangolin declining across all stations Jan-Jul 2026. Investigate."
```

Give the metrics a few minutes before deciding something is broken. They travel out
through the runtime's log group as embedded-metric-format records first, so on a
live event they took a good deal longer than the invocation itself to show up. If
the namespace reads *"There are no metrics in this namespace,"* that is what you are
waiting on.

## Where the numbers are

Open **CloudWatch -> Metrics -> Classic metrics** in your event's region, and search
**All metrics** for the `bedrock-agentcore` namespace - written by the OpenTelemetry
instrumentation inside the runtime, so it is there whatever the agent is built with.

| Metric | What it tells you |
|--------|-------------------|
| `gen_ai.client.token.usage` | **The price mechanism.** Split by a `gen_ai.token.type` dimension of `input` or `output`, and by `gen_ai.request.model`. Select both types, set the statistic to **Sum**, and you have the run's token bill. |
| `gen_ai.client.operation.duration` | How long each model call took. |
| `strands.tool.call_count` | How many times the agent called a tool. |
| `strands.tool.duration` | Per-tool latency - which tool made the user wait. |
| `strands.tool.success_count` | Tool calls that returned cleanly. |
| `strands.event_loop.cycle_count` | **How many times round the loop.** This is the pipeline-versus-loop distinction from the previous section, as a number. |

A single tapir investigation in a test run of this workshop came to roughly **8,600 input tokens and 1,300 output tokens** (about **9,900 total**) for the final `chat` turn, with an end-to-end model duration around **22 seconds**. Input dominates, because every tool result is fed back into the next turn.

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

## Go further: read a single run's span

Everything above is aggregated - sums and counts over a namespace. If you enable
Transaction Search in your own account (workshop accounts do not permit this - see
the warning at the top), you get the per-run detail instead: each investigation
produces a `chat` span carrying the token counts, latency, model id and the full
message/tool trace for that one run, no summing across runs required.

1. **Enable OpenTelemetry span ingestion (one time).** **CloudWatch -> Settings ->
   X-Ray traces -> Transaction Search -> View settings**, and ensure **Ingest
   OpenTelemetry Spans** is on. Give it a minute - spans that ran before ingestion
   was enabled will not be backfilled, so run the agent again once it is on.
2. **Run the agent** so there is a fresh span to look at - the same
   `agentcore invoke` commands as above work.
3. **Open the span.** **CloudWatch -> GenAI Observability -> Bedrock AgentCore ->
   All Spans**, then click a `chat` span.
4. **Read the token bill off the span.** The attributes that matter:

   | Span attribute | What it tells you |
   |-----------------|-------------------|
   | `gen_ai.usage.input_tokens` (also `gen_ai.usage.prompt_tokens`) | The input side of the bill for that one run. |
   | `gen_ai.usage.output_tokens` (also `gen_ai.usage.completion_tokens`) | The tokens the model generated. |
   | `gen_ai.usage.total_tokens` | Input + output for the run. |
   | `gen_ai.request.model` | Which model you were billed for. |
   | `gen_ai.server.request.duration` | End-to-end model latency for the run, in milliseconds. |
   | `gen_ai.server.time_to_first_token` | How long until the first token came back, in milliseconds. |
   | `session.id` | One session per investigation - use it to tell runs apart. |

   The span's **events** (`gen_ai.system.message`, `gen_ai.user.message`,
   `gen_ai.assistant.message`, `gen_ai.tool.message`, `gen_ai.choice`) are the full
   message and tool trace: the system prompt, your prompt, each `toolUse` the agent
   chose, each `toolResult` it got back, and the final report. That is where you
   read the path the agent chose and judge response quality.
5. **Use the default dashboards** under **CloudWatch -> GenAI Observability ->
   Bedrock AgentCore**: **All Capabilities** for total token and cost per agent,
   **All Sessions** for tokens/errors/latency per session, **All Traces** and
   **All Spans** for everything underneath a given run.

## Discuss

- Did the pangolin (all-stations) investigation cost more than the tapir
  (one-station) one? Why? (Hint: multi-station detection payloads are larger, and
  larger tool results mean more tokens fed back into the model.)
- If you have spans enabled, open each run's `chat` span and read its tool trace
  (the `toolUse` events). Did the agent take the same path for both? Should it have?
  Otherwise, compare `strands.tool.call_count` and `strands.tool.duration` across
  the two runs instead.
- Which tool returned the biggest payload, and how did that move the cost?
- If you halved the 8-call ceiling, what would you save and what would you risk?

Keep these numbers in mind. When you design your canvas next, you will put a COST
ESTIMATE on it, then compare against your own agent's real usage after you deploy
in Part 2.
