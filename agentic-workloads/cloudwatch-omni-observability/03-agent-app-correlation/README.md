# 03 — Agent + application correlation

Agents call APIs, and APIs depend on other services. When an agent "gets worse", the cause may be the model, the prompt, or a slow dependency three hops away. This sample connects sample 01's agent to sample 02's shop so that **one trace spans agent → orders → payments**. You then watch a downstream latency spike turn into tool failures and lower quality scores.

```
support-agent (ADOT, direct OTLP)                 shop (OTel SDK → collector)
 invoke_agent
   chat (Bedrock)
   execute_tool lookup_order ── httpx GET /orders/ORD-1001 ──► orders ── GET /payments/ORD-1001 ──► payments
   chat (Bedrock)                (traceparent header)                                          └ card_processor.authorize (slow!)
```

The two halves export through different paths: the agent goes straight to the X-Ray OTLP endpoint, and the shop goes through the collector. They join into one trace because `opentelemetry-instrumentation-httpx` injects W3C `traceparent` on the agent's tool calls, and FastAPI's instrumentation continues it.

## Run it

> **Organization domain?** Run `source common/assume-member-role.sh` from the repo root in this shell first, so everything below acts in the member account that owns the space (see the main README).

Prerequisites: sample 02 is running (`02-microservices-apm/up.sh`), and sample 01's venv is active. Turn on online evaluation from sample 01 beforehand if you want the quality-score comparison.

```bash
cd 03-agent-app-correlation
./run_scenario.sh 15
```

| Phase | Payments | What the agent experiences |
|---|---|---|
| `baseline` | healthy | `lookup_order` returns in about 100 ms, and answers are grounded |
| `incident` | +4 s per call (`chaos.sh latency 4000`) | Tool calls hit the 3 s `TOOL_TIMEOUT_SECONDS`, the `execute_tool` spans fail, and the agent apologises instead of answering |
| `recovery` | healthy | Back to baseline |

Every agent span carries `app.scenario.phase`, so the phases can be compared in SQL.

## What to look at

**1. One trace, four services.** Open any `support-agent` trace from the incident phase. Below the failed `execute_tool lookup_order` span you'll find the `orders` and `payments` spans and the slow `card_processor.authorize`.

```bash
python ../common/omni_query.py queries/services_in_agent_traces.sql
python ../common/omni_query.py ../02-microservices-apm/queries/trace_waterfall.sql --var trace_id=<traceId>
```

![Graph view of one incident-phase trace: invoke_agent, execute_tool lookup_order failing with httpx.ReadTimeout, and the GET call continuing into the orders service](../docs/screenshots/03-agent-trace-into-shop.png)

*Graph view of an incident-phase trace. The agent's `lookup_order` tool times out (`httpx.ReadTimeout`), and its `GET` continues into the `orders` server span (`GET /orders/{order_id}`, 4.66 s). Expand that node to see `payments` and `card_processor.authorize`.*

**2. Agent latency and failures against downstream latency**, per phase, in one statement:

```bash
python ../common/omni_query.py queries/agent_vs_payments_latency.sql
```

```
phase     turns  agent_p50_ms  avg_payments_ms  turns_with_tool_failure
baseline  ...    low           a few ms         a few (ORD-9999)
incident  ...    higher        ~4000            more
recovery  ...    low           a few ms         a few (ORD-9999)
```

Baseline and recovery still show a few tool failures: `prompts.jsonl` asks about `ORD-9999` on purpose, and that lookup always fails with a 404. Compare the phases rather than expecting zero. A verified run with 5 sessions per phase gave 2 / 3 / 2 failing turns and 2 / 4255 / 2 ms average payments latency.

**3. Quality impact** (online evaluation enabled):

```bash
python ../common/omni_query.py queries/quality_by_phase.sql
```

`Builtin.Helpfulness` should dip in `incident`. The agent wasn't wrong; it just couldn't reach its data. This is the case where an eval-only tool blames the prompt and an APM-only tool shows no agent problem. Omni puts both on the same trace.

**4. Ask the Omni agent:** *"Why did support-agent's helpfulness drop in the last hour?"* It should connect the score dip to `lookup_order` failures and walk the context graph down to `payments` latency.

## Cleanup

Nothing new is created here. Run `../02-microservices-apm/chaos.sh off` if you stopped the scenario midway.
