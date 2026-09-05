# Cost estimation

All figures are rough, for planning only. Verify against your account pricing and
the real token usage shown in AgentCore Observability. Region: us-east-1.

## Per investigation (one anomaly)

The dominant cost of an agentic loop is **model tokens**, because every tool
result is fed back into the model's context.

| Item | Typical | Notes |
|------|---------|-------|
| Tool calls | 4-6 | 6 tools, sometimes one re-check; capped at 8 |
| Model turns | ~5-8 | one per tool round-trip plus the final report |
| Input tokens | ~15k-40k | grows with tool payload sizes (multi-station = more) |
| Output tokens | ~2k-5k | reasoning + the structured report |
| **Model cost** | **~$0.10-0.30** | the bulk of the cost |
| Lambda | << $0.01 | 6 short invocations |
| S3 | negligible | a few GETs + small PUTs |
| Gateway / Runtime | usage-based | see AgentCore pricing |

**Rule of thumb:** budget roughly **$0.10-0.30 per investigation**, model-dominated.
The pangolin (multi-station) case sits at the higher end because its detection
payloads are larger.

## Workshop event (per participant, 90 min)

| Item | Estimate |
|------|----------|
| ~10-20 agent invocations during build/validate | ~$1-4 |
| Tool backend (Lambda + S3), 90 min | < $0.10 |
| AgentCore Runtime + Gateway, 90 min | usage-based, low |
| KMS key for the Code Editor token | < $0.05 | 
| **Total per participant** | **~$1-5** |

The KMS line is a flat $1/month per key, prorated, and it keeps running for the
7-day pending-deletion window after the stack is deleted — so it is a few cents
per participant, not a rounding error you can ignore entirely at 300 accounts.

For a 30-person event, plan roughly **$50-150** of Bedrock + AgentCore usage,
excluding any Workshop Studio account overhead.

## Cost levers (tie back to the canvas)

- **Lower the tool-call ceiling** (8 -> 4): fewer round-trips, fewer tokens, but
  risk of under-investigating the hard cases.
- **Trim tool payloads**: return summaries instead of full record lists so less
  gets fed back into context. Biggest single lever on token cost.
- **Right-size the model**: a smaller model for simple confirmations, escalating to
  Sonnet only when needed (a hybrid pipeline+loop).
- **Batch overnight** (the canvas latency choice): no idle real-time capacity.

## Measure, don't guess

After deploying, compare the **COST ESTIMATE** box on your canvas to the actual
token totals: **CloudWatch -> Metrics -> `bedrock-agentcore` ->
`gen_ai.client.token.usage`**, split by the `gen_ai.token.type` dimension into input
and output, statistic **Sum**. The gap between estimate and measurement is the lesson.

(Not the GenAI Observability console page - that one is built on indexed transaction
spans and reads "No data" until CloudWatch Transaction Search is enabled, which a
temporary workshop account cannot do.)
