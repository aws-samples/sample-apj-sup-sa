---
title: "Validate"
weight: 80
---

Now check your agent against reality. First, validate it against your own canvas:
did it produce the Outcome you designed, stop when your Definition of Done said it
should, and use the tools you expected? Whatever shape your agent took — a report, a
structured feed, a chatbot, a dashboard — judge it against what your canvas promised.

There is no single right answer to check against here: every team built something
different. So validation is about exercising *your* agent deliberately and then
reading what actually happened in the traces.

## Invoke your agent deliberately

Run a handful of invocations chosen to stress the decisions on your canvas, not just
a happy path. Pick the ones that apply to what you built:

- **A clear, in-scope case.** The obvious task your agent exists for. Confirm it
  produces your intended Outcome in the right shape.
- **A boundary / ambiguous case.** Thin or conflicting evidence. Does it stop at your
  Definition of Done, set an escalation flag, or keep looping? This is where your
  stop conditions and tool-call cap earn their keep.
- **An out-of-scope case.** Something your agent should decline or hand off. Confirm
  it does not hallucinate an answer.
- **A "heavy" case** (if relevant). One that legitimately needs many tools or a large
  data pull, so you can see cost and latency at the top of your range.

Invoke locally, through the gateway, or against the deployed runtime — the behaviour
should be consistent. Use the same invoke path you set up in Build:

```text
agentcore invoke "<your prompt here>"
```

or the boto3 fallback (`invoke_agent_runtime`, long read_timeout) from Build Phase 4.

## Observe what happened

Every deployed agent is auto-instrumented, so you can inspect all of this whatever
your agent outputs. Read it the same way you did in
[Part 1's observability section](../030-part-1-building-blocks/035-observability.md) -
**CloudWatch -> Metrics**, not the GenAI Observability page, which stays empty at an
AWS-run event because it needs Transaction Search:

- **Your cost per run** — `gen_ai.client.token.usage` in the `bedrock-agentcore`
  namespace, split by `gen_ai.token.type` into input and output. Set the statistic to
  **Sum** over one invocation and that is the run's token bill.
- **The path it chose** — `Invocations` in `AWS/Bedrock-AgentCore`, graphed by the
  tool `Name` dimension. Every tool your agent called appears by name, so you can
  confirm it used the tools you expected, and only those, without needing spans.
- **How far round the loop** — `strands.event_loop.cycle_count`, plus
  `strands.tool.call_count` and `strands.tool.duration` per tool.
- **Latency and errors** — `Latency`, `Duration`, `Errors` and `Throttles` in
  `AWS/Bedrock-AgentCore`, and `gen_ai.client.operation.duration` for the model calls.

The service metrics appear within a couple of minutes; the agent's own
`bedrock-agentcore` metrics take longer, so an empty namespace means you are waiting
rather than that something broke. `agentcore logs` shows the run itself in the
meantime.

## Compare against your canvas

Turn what you observed back into a judgement on your design:

| Canvas box | What to check, and where |
|------------|--------------------------|
| **Outcome** | Did the final output match the shape and content you designed? Read the invoke output. |
| **Definition of Done** | Did it stop for the right reason — done, capped, or escalated — not by accident? `strands.event_loop.cycle_count` against your tool-call cap. |
| **Inputs / tools** | Did it call the tools you expected, and only those? `Invocations` by tool `Name`. |
| **Cost** | Measured token cost per run against the estimate on your canvas. `gen_ai.client.token.usage`, Sum, split input/output. |
| **Success metrics** | Measured latency against your target. `Latency` and `Duration` in `AWS/Bedrock-AgentCore`. |

## Debrief (facilitator, ~10 min)

1. Did your agent match your canvas? Where did output, stopping behaviour, or tool
   use differ from what you designed?
2. Where did it surprise you — unexpected tool order, an insight you did not
   anticipate, terminating too early or too late?
3. What did the numbers say? Was your cost estimate close? Was your tool-call cap or
   confidence threshold right? Should a human gate move?
4. What would you change on your canvas now, before you built it "for real"?

This closes the loop the canvas opened: the decisions you guessed in design, now
measured against a running agent.

Next: [Cleanup](../090-cleanup/index.md)
