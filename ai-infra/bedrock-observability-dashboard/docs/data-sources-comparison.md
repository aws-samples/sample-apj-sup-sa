# Bedrock observability — three data sources compared

Bedrock exposes runtime telemetry through three independent surfaces. Each answers a different question, costs differently, and requires different setup. This dashboard uses the first two by default and treats the third as an optional upgrade.

## Comparison table

| Dimension | CloudWatch Metrics (`AWS/Bedrock`) | Model Invocation Logs | CloudTrail (management events) |
|---|---|---|---|
| **What it is** | Aggregated numeric time series | Full per-call JSON record (request + response) | Per-API-call audit record (no body) |
| **Default state** | ON automatically | OFF — must opt in per region | Management events ON by default; you add a CloudWatch Logs destination to query them |
| **Granularity** | 1-minute aggregates | Per-call | Per-call |
| **Latency to availability** | ~1 min | ~30 s – 2 min | ~5–15 min |
| **Scope** | Per region | Per region (configured separately) | Per trail (can be multi-region/org-wide) |
| **Dimensions / fields available** | `ModelId` only | Caller, modelId, operation, full prompt, full response, token counts, cache tokens, stopReason, guardrail trace, `requestMetadata.*` | `userIdentity` (full IAM), `eventName`, `sourceIPAddress`, `requestParameters` (no body), `additionalEventData.inferenceRegion` |
| **Token counts** | Sum/Avg only | Per-call exact | Not present |
| **Cost / pricing math** | Derive from token metrics × $/KTok via CW math | Per-call, per-tenant from log records | Need separate token source |
| **Prompt / completion text** | No | Yes — full text (configurable per modality) | No |
| **Cache read/write tokens** | Yes — as metrics | Yes — per-call | No |
| **Stop reason** | No | Yes — `end_turn`, `max_tokens`, `tool_use`, `content_filtered`, `guardrail_intervened` | No |
| **Guardrail interventions** | No | Yes — full trace | Event name only |
| **IAM principal of caller** | No | Role session ARN only | Yes — full `userIdentity` (user, federated identity, source role) |
| **Source IP / user agent** | No | No | Yes |
| **Cross-region inference: routed-to region** | No (only profile id surfaces) | No | Yes — `additionalEventData.inferenceRegion` |
| **Failed-before-invoke errors** (e.g. AccessDenied, malformed) | As `InvocationClientErrors` count, no detail | Sometimes — not all pre-invoke failures log | Always |
| **Throttle visibility** | Count metric | With `errorCode` | Full event |
| **Per-tenant / per-team attribution** | No | Yes — via `requestMetadata.{team,app,env}` (caller-supplied) | Via IAM role naming convention only |
| **Streaming TTFT (`TimeToFirstToken`)** | As metric | Not in record | No |
| **Quota usage estimate** | `EstimatedTPMQuotaUsage` (approximate per AWS) | No | No |
| **Query language** | CW Metric Math, search expressions | CloudWatch Logs Insights (or Athena via S3) | CloudWatch Logs Insights, CloudTrail Lake SQL, or Athena |
| **Storage destination** | Internal CW timeseries store | CloudWatch Logs and/or S3 | CloudWatch Logs and/or S3 (trail) or Lake event data store |
| **Cost — base** | First 10 metrics free, then $0.30/metric × dimensions × regions | $0 for delivery; pay for destination | Runtime calls are **management events** — captured free, no per-event charge. (Bedrock **data events**, billed ~$0.10/100K, apply only to async/bidirectional/Agent/KB/Flow/Guardrail ops — not these runtime calls.) |
| **Cost — destination** | n/a | CW Logs ingest $0.50/GB or S3 storage ~$0.023/GB | CW Logs ingest $0.50/GB or S3 ~$0.023/GB |
| **Cost @ 10K RPM (~14M calls/day)** | ~$25–$50/mo total | ~$60/mo (CW Logs, text-only) or ~$3/mo (S3 gzip) | CW Logs ingest of the delivered management events only (~$0.50/GB), comparable to invocation-log ingest — no per-event surcharge for runtime calls |
| **PII risk** | None — no payloads | HIGH — full prompt text in log | Medium — `requestParameters` may include `requestMetadata` tags in plaintext |
| **CloudFormation supported** | Yes | Log group yes; the `put-...-logging-configuration` call is **not** a CFN resource (manual step) | Yes — `AWS::CloudTrail::Trail` with `AdvancedEventSelectors` |
| **Modality toggles** | n/a | text / image / embedding / video — each independent | n/a |
| **Used in this dashboard** | Sections 1–5, always on (Tier 1: Overview, Performance, Errors, Tokens, Quota) | Section 6 (Tier 2, via `ModelInvocationLogGroup`) — 5 Logs Insights queries (top tenants, tokens by model+op, stop reasons, largest prompts, guardrail interventions) | Section 7 (Tier 3, via `CloudTrailLogGroup`) — 3 queries (top callers by IAM, error reasons by caller, cross-region routing); gated by the `HasCloudTrail` condition |
| **Best at answering** | "How fast / how many / how many tokens?" | "What did each tenant ask, and what came back?" | "Who called what, from where, with what permissions, routed where?" |

## Quick decision guide

| If your top question is… | Enable | Why |
|---|---|---|
| Latency, cost, throttle rate trend | Just CW Metrics (default) | Free, instant, no setup |
| Per-team chargeback, prompt forensics, cache effectiveness per tenant | + Invocation logs (text-only, CW destination) | Cheap (~$5–$60/mo), no CT cost |
| Compliance audit, IAM principal attribution, cross-region routing visibility | + CloudTrail management events (CW Logs destination) | The only source for IAM identity & routed region; runtime calls are management events, so cost is just log ingest |
| All three at scale (large prod) | All three, but ship invocation + CT logs to **S3** instead of CW Logs | 10× cheaper for queries you can run via Athena |

## Layered cost picture (text-only, ~3 KB/event)

The CloudTrail column below is **CW Logs ingest of management events only** — runtime `InvokeModel`/`Converse` calls carry no per-event data-event charge. The per-event data-event price (~$0.10/100K) applies only if you separately enable Bedrock **data** events for async/bidirectional/Agent/KB/Flow/Guardrail operations, which this dashboard does not use.

| Daily Bedrock calls | CW Metrics | + Invocation logs (CW dest) | + CloudTrail management events (CW dest) |
|---|---|---|---|
| 10K | <$5 | +$0.50 | +$0.50 |
| 100K | <$15 | +$5 | +$5 |
| 1M | <$25 | +$45 | +$45 |
| 10M | <$50 | +$450 (or ~$3 to S3) | +$450 (or ~$3 to S3) |
| 100M | ~$50 | +$4,500 (or ~$30 to S3) | +$4,500 (or ~$30 to S3) |

CloudTrail management-event records are leaner than full invocation logs (no prompt/response body), so in practice the CloudTrail ingest cost trends a bit below the invocation-log figure at the same call count; treat the column as an upper bound. Verify against current AWS pricing before quoting.

**Pattern**: metrics scale flat with model count, not call count. Logs and CloudTrail scale linearly with call count, so they're what you optimize first at scale — usually by switching destination from CW Logs to S3.

## What this project uses today

- **CW Metrics** (Tier 1) — always on, drives sections 1–5.
- **Invocation logs** (Tier 2) — text-only, delivered to CloudWatch log group `<YOUR_LOG_GROUP>`. Drives the 5 Logs Insights queries in section 6. Enabled by passing `ModelInvocationLogGroup`.
- **CloudTrail management events** (Tier 3) — enabled by passing `CloudTrailLogGroup` (a trail delivering management events to a CloudWatch Logs group; no data-event selector needed). Drives section 7's 3 widgets/queries — identity, error reasons, cross-region routing — all gated by the `HasCloudTrail` CloudFormation condition. In the deployment of record this group is not yet wired, so those widgets are skipped at deploy time.

To wire up CloudTrail later, see `setup.md` and redeploy with `--parameter-overrides CloudTrailLogGroup=<your-cloudtrail-log-group>`.

## Pricing caveat

All numbers above are us-east-1 list prices as of 2026-05. Verify before quoting:
- CloudTrail: https://aws.amazon.com/cloudtrail/pricing/
- CloudWatch (metrics + logs): https://aws.amazon.com/cloudwatch/pricing/
