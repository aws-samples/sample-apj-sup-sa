# Architecture & Panel Reference

## What this dashboard covers

Single-pane CloudWatch dashboard for Bedrock **InvokeModel / Converse / ConverseStream / InvokeModelWithResponseStream** workloads. Combines:

| Source | Used for | Default-on? |
|---|---|---|
| `AWS/Bedrock` CloudWatch metrics | Invocations, latency, tokens, throttles, log-delivery health | Yes |
| Bedrock Model Invocation Logs (CloudWatch Logs) | Per-tenant attribution, guardrail interventions, prompt forensics | **No — must be enabled** |
| CloudTrail management events (`eventSource = bedrock.amazonaws.com`) | Caller identity, throttle/error reasons, cross-region routing | Yes — runtime calls are **management events** (default-on, no per-event charge); you pay only to deliver them to a log destination |

Out of scope (not covered by this dashboard):
- Bedrock Agents (`AWS/Bedrock/Agents` — separate dashboard)
- Bedrock Guardrails standalone metrics (`AWS/Bedrock/Guardrails` — separate dashboard)
- Knowledge Bases ingestion logs
- Provisioned Throughput utilization (no native metric)

## Data sources

### `AWS/Bedrock` namespace

| Metric | Unit | Notes |
|---|---|---|
| `Invocations` | Count | Successful Converse / InvokeModel calls. Errors and throttles do NOT count. |
| `InvocationClientErrors` | Count | 4xx |
| `InvocationServerErrors` | Count | 5xx |
| `InvocationThrottles` | Count | Mutually exclusive with Invocations + Errors |
| `LegacyModelInvocations` | Count | Track migration off deprecated models |
| `InvocationLatency` | Milliseconds | Full request → last token. Use percentiles. |
| `TimeToFirstToken` | Milliseconds | **Streaming-only** (`ConverseStream`, `InvokeModelWithResponseStream`) |
| `InputTokenCount` | Count | |
| `OutputTokenCount` | Count | |
| `OutputImageCount` | Count | Image-generation models only. Extra dims: `ImageSize`, `BucketedStepSize` |
| `CacheReadInputTokenCount` | Count | Reduced-rate, doesn't count toward TPM. |
| `CacheWriteInputTokenCount` | Count | Counts toward TPM. |
| `EstimatedTPMQuotaUsage` | Count | **Approximate** per AWS — not the value used for throttling decisions. |
| `ModelInvocationLogs{CloudWatch,S3}Delivery{Success,Failure}` | Count | Log delivery health. Single dimension "Across all model IDs". |
| `ModelInvocationLargeDataS3Delivery{Success,Failure}` | Count | Large-payload delivery health. |

**Dimensions:** only `ModelId`. For cross-region inference, the dimension carries the inference-profile id, not the underlying model. The routed region is only visible in CloudTrail (`additionalEventData.inferenceRegion`).

### Model Invocation Logs

Schema fields used by panels: `requestId`, `operation`, `modelId`, `requestMetadata.<your-keys>`, `input.inputTokenCount`, `output.outputTokenCount`, `output.outputBodyJson.amazon-bedrock-guardrailAction`, `output.outputBodyJson.stopReason`.

To populate per-tenant breakdowns, callers must pass `requestMetadata` on Converse requests:
```python
client.converse(
    modelId=...,
    messages=...,
    requestMetadata={"team": "platform", "app": "chatbot", "env": "prod"},
)
```
Limits: 16 keys, 256 chars per key/value. Don't put PII here.

### CloudTrail

Standard Bedrock runtime calls — `InvokeModel`, `InvokeModelWithResponseStream`, `Converse`, `ConverseStream` — are recorded as CloudTrail **management events** (`eventSource = bedrock.amazonaws.com`, on by default, no per-event data-event charge). You pay only to deliver them to a CloudWatch Logs group; you do **not** need a data-event selector for this dashboard. `requestId` joins to invocation logs. `errorCode` exposes throttle/error reasons not visible in CW metrics. (Data events — billed per 100K — apply only to async / bidirectional / Agent / KB / Flow / Guardrail operations, which are out of scope here.)

## Panel reference (sections in dashboard.yaml)

1. **Overview** — invocations / errors / total tokens (single-value tiles, last hour)
2. **Performance** — by ModelId: invocations, p50/p90/p99 latency, TimeToFirstToken (streaming), all-models search expression
3. **Errors & Throttles** — stacked errors, throttle rate %, legacy-model usage (log-delivery health moved to Section 6, Tier 2)
4. **Tokens** — by ModelId: input vs output tokens, per-model cache read vs write tokens (no blended hit-ratio overlay — a single ratio across mixed models was misleading)
5. **Quota Headroom** — `EstimatedTPMQuotaUsage` per ModelId, derived RPM
6. **Per-tenant attribution** (Tier 2, requires `ModelInvocationLogGroup`) — Logs Insights reading `requestMetadata.<TenantMetadataKey>` (default `team`): top tenants by tokens, model+operation breakdown, guardrail interventions; plus log-delivery health
7. **CloudTrail identity / routing / errors** (Tier 3, requires `CloudTrailLogGroup`) — top callers by IAM identity, error reasons by caller, cross-region routed region; gated by the `HasCloudTrail` condition

> Total true input tokens = `InputTokenCount + CacheReadInputTokenCount + CacheWriteInputTokenCount`.

## Known caveats

- `EstimatedTPMQuotaUsage` is directional only — AWS warns it differs from the reservation logic that drives throttling. Don't drive auto-scaling off it.
- Cross-region inference routed region is invisible in CW metrics. Only CloudTrail carries `additionalEventData.inferenceRegion`.
- Provisioned Throughput utilization has no native metric — derive from token throughput.
- `requestMetadata` is opt-in per call; widgets that depend on it stay empty until callers tag.
- Image / embedding / video modalities are off by default in our current logging config (only text is enabled in `us-east-1`). To analyze those, enable them via `PutModelInvocationLoggingConfiguration`.
- `stopReason` and the guardrail action are read from `output.outputBodyJson`, whose shape is **model-specific** (the queries are Anthropic-shaped). Those two queries may not populate for Nova / Llama / Titan, whose response bodies nest these fields differently.

## Sources

- https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/model-invocation-logging.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/cost-mgmt-request-metadata.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/logging-using-cloudtrail.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html
- AWS blog "Improve visibility into Amazon Bedrock usage and performance with Amazon CloudWatch" (2024-06-25)
- AWS blog "Track, allocate, and manage your generative AI cost and usage with Amazon Bedrock" (2024-11-01)
- aws-samples/sample-amazon-cloudwatch-generative-ai-observability
- aws-samples/sample-quota-dashboard-for-amazon-bedrock
