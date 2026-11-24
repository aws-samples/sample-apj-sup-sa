# Observability — Reference (operating a published listing)

`reference/logging.md` covers the container-side logging contract every seller needs for the build
(stdout/stderr → CloudWatch Logs, structured JSON, what not to log). This file covers **metrics** —
mostly informational for the build phase, but directly relevant once a listing is live and buyers ask
"how do I monitor this."

Source: AWS ML blog ["Deepgram deepens Amazon SageMaker AI observability with Enhanced Metrics"](https://aws.amazon.com/blogs/machine-learning/deepgram-deepens-amazon-sagemaker-ai-observability-with-enhanced-metrics/)
(2026-08-27) and Deepgram's own SageMaker observability docs
([Observability for Amazon SageMaker](https://developers.deepgram.com/docs/observability-sagemaker),
[Deepgram Enhanced Metrics](https://developers.deepgram.com/docs/enhanced-metrics-sagemaker),
[Prometheus & OpenTelemetry Metrics](https://developers.deepgram.com/docs/prometheus-otel-sagemaker)),
which document a reference pattern for exactly this seller use case — surfacing container-internal
metrics without any outbound network call.

## Three layers of metrics on a SageMaker endpoint

| Layer | Who publishes it | Where it lives | Needs container code? |
|---|---|---|---|
| SageMaker invocation metrics | AWS, automatically | CloudWatch `AWS/SageMaker` namespace | No |
| SageMaker instance metrics | AWS, automatically | CloudWatch `/aws/sagemaker/Endpoints` namespace | No |
| Detailed observability (GPU/host/container Prometheus) | AWS-managed OTel Collector on the host | CloudWatch OTel metric store (PromQL) | Only for the container's own `/metrics`; GPU+host work with zero container changes |
| Container-emitted business metrics (EMF) | Your container, via stdout | CloudWatch custom namespace (classic metrics API) | Yes — this is the pattern to build |

## 1. SageMaker invocation metrics (`AWS/SageMaker` namespace) — free, no code

Published automatically per endpoint variant. The ones that matter most for the modes this skill
scaffolds:

| Metric | Resolution | Notes |
|---|---|---|
| `Invocations` | 1 min | Total request count. Each streaming session or Real-Time/Batch call counts as one. |
| `InvocationsPerInstance` | 1 min | Throughput normalized by instance count. Less useful for long-lived streaming sessions than `ConcurrentRequestsPerModel`. |
| `ModelLatency` | per-request | Time inside your container. **Not emitted for bidirectional-streaming sessions** — use `FirstChunkLatency` there instead. Microseconds. |
| `OverheadLatency` | per-request | SageMaker routing/serialization time, outside your container. High values point at platform overhead, not your code. |
| `ConcurrentRequestsPerModel` | **10s** (high-resolution) | In-flight requests per instance, **including queued**. The primary load signal for anything holding a connection open (bidirectional WebSocket, long streaming responses) — each connection occupies capacity for its full duration, so this reflects real load far better than completed-invocation counts. |
| `FirstChunkLatency` | per-session | Time from stream start to first chunk sent. Primary latency metric for streaming/WebSocket. Microseconds. |
| `MidStreamErrors` | per-session | Failures after a streaming response has already started — otherwise invisible outside container logs. |
| `Invocation4XXErrors` / `Invocation5XXErrors` / `InvocationModelErrors` | 1 min | Client vs. server-side failure counts. 5XX spikes often mean container crash / OOM / GPU issue. |

All latency metrics are in **microseconds** — a 2-second alarm threshold is `2000000`, not `2000`.

## 2. SageMaker instance metrics (`/aws/sagemaker/Endpoints` namespace) — free, no code

`CPUUtilization`, `GPUUtilization`, `GPUMemoryUtilization`, `MemoryUtilization`, `DiskUtilization` —
resource-level, per endpoint variant. For GPU-bound models (the common case for anything this skill
scaffolds — LLM/STT/TTS/vision), `GPUUtilization` and `GPUMemoryUtilization` are the ones to alarm on:
sustained near-max indicates the instance is at capacity and needs scaling.

Enable **enhanced (per-instance) metrics** via `MetricsConfig` on the endpoint config to break these
down per-instance rather than aggregated across the fleet — useful once you're running >1 instance
behind a variant.

## 3. Detailed observability — per-GPU + host Prometheus via managed OTel (on by default, no code for GPU/host)

SageMaker runs an AWS-managed OpenTelemetry Collector on every instance backing the endpoint. It
exports three sources to a CloudWatch OTel metric store queried with **PromQL** (not the classic
`get-metric-statistics` API):

- **GPU (DCGM exporter):** `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED`, etc. — **per-GPU**, not summed.
  Critical on multi-GPU instances (e.g. `ml.g6.12xlarge`) where an aggregate/average would hide one
  saturated device.
- **Host (node exporter):** standard `node_*` Prometheus metrics for the instance.
- **Container:** the collector scrapes your container's own `/metrics` endpoint on port 8080 if you
  serve one. This is the one piece that needs container code — see below.

Key facts:

- **On by default for new endpoints**, publishing every 60s. Set explicitly (and choose 10–300s
  frequency) via `MetricsConfig={"EnableDetailedObservability": True, "MetricPublishFrequencyInSeconds": N}`
  on `create_endpoint_config` / `update_endpoint_config`. Requires botocore/boto3 ≥1.43.49 or a recent
  CLI — older SDKs reject the parameter.
- **Runs on the host, outside your container** — GPU and host metrics work under Marketplace's network
  isolation requirement with zero impact, since the collector isn't part of the model container's
  network namespace restrictions.
- **Not in `list-metrics`** — these live in the OTel store only; query with PromQL via the CloudWatch
  console's PromQL editor or the Prometheus-compatible HTTP API at
  `https://monitoring.<region>.amazonaws.com/api/v1/query` (SigV4-signed, service name `monitoring`;
  works with Grafana or any Prometheus-native tool).
- **Label quoting gotcha:** OTel resource labels use dotted names (`aws.sagemaker.endpoint.name`) —
  PromQL requires quoting them (`{"aws.sagemaker.endpoint.name"="..."}`). Using underscores instead of
  dots is valid PromQL syntax that silently matches nothing — it returns an empty result, not an error,
  and is easy to misread as "metric not published."

### Optional: serve your own `/metrics` for the container layer

If the container exposes a Prometheus-format `/metrics` endpoint on port 8080, the collector scrapes it
automatically alongside GPU/host metrics — no extra opt-in needed beyond detailed observability being
on. Useful for exposing engine-internal signals (queue depth, active-request counts, model-specific
health) that SageMaker's own metrics can't see. This is genuinely optional — most sellers get useful
signal from GPU+host alone.

## 4. Container-emitted business metrics via CloudWatch EMF — the pattern this skill should recommend

**Supersedes the "use CloudWatch Metric Filters" advice in `reference/logging.md`.** Metric Filters
promote log values into metrics after the fact and are fragile (pattern-matching against log text).
**CloudWatch Embedded Metric Format (EMF)** is the better mechanism for exactly the "I need custom
metrics but have zero outbound network" problem this skill's containers face:

- Write a specially-structured JSON line to stdout (the same stdout SageMaker already captures to
  CloudWatch Logs). CloudWatch Logs recognizes the EMF schema and extracts real CloudWatch metrics
  automatically — no `cloudwatch:PutMetricData` call, no IAM permission, no agent or sidecar.
- Works under Marketplace's zero-outbound-network constraint because it never makes a network call —
  it's a stdout write that CloudWatch's own log pipeline post-processes.
- Appears in the **classic** metrics API (`aws cloudwatch list-metrics`, `get-metric-statistics`,
  alarms, dashboards) — unlike detailed observability's PromQL-only store.
- Spec: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html

### Recommended use: mirror your metering emission into EMF

If per-inference billing was selected in Phase 2, `metering.py` already computes `ConsumedUnits` per
request for the `X-Amzn-Inference-Metering` header. Emit the **same value** as an EMF record on stdout
at the same call site so the seller has a self-serve way to reconcile the AWS Marketplace bill against
actual traffic, independent of buyer-side CloudWatch access:

```python
import json, time

def emit_emf_metric(dimension: str, consumed_units: int, extra_dims: dict | None = None):
    """Write a CloudWatch EMF record to stdout. SageMaker ships stdout to the
    endpoint's CloudWatch log group; CloudWatch Logs extracts the metric —
    no cloudwatch:PutMetricData call, no outbound network."""
    dims = {"Dimension": dimension, **(extra_dims or {})}
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "YourModel/Inference",
                "Dimensions": [list(dims.keys())],
                "Metrics": [{"Name": "ConsumedUnits", "Unit": "Count"}],
            }],
        },
        **dims,
        "ConsumedUnits": consumed_units,
    }
    print(json.dumps(record))
```

Keep dimension cardinality low (category/model/transport-style buckets, not per-request IDs) — each
unique metric+dimension combination is billed under standard CloudWatch metric pricing, and high
cardinality also risks leaking request-identifying information into a metrics namespace, which is the
wrong place for it (see "what NOT to log" in `reference/logging.md` — the same rule applies to EMF
dimensions).

### Bidirectional WebSocket metering

For WebSocket, emit the EMF record at the same point the metering companion channel
(`/invocations-bidirectional-stream-metadata`) sends its `{"Metering": {...}}` frame — same
`ConsumedUnits` value, two destinations (buyer's billing pipeline via the metadata WebSocket, seller's
own CloudWatch via EMF on stdout).

## What to add to this skill's phases

- **Phase 5 (container walkthrough):** when per-inference billing is selected, offer to add the
  `emit_emf_metric()` call alongside the existing `X-Amzn-Inference-Metering` header / metadata-channel
  emission in `metering.py`.
- **Phase 8 (local testing):** EMF lines are just JSON on stdout — verify locally by grepping container
  logs for the `_aws` key; no CloudWatch account access needed to confirm the container emits correctly
  shaped records before pushing to ECR.
- **Post-Phase-11 (operating a live listing):** point sellers who ask about capacity planning at
  `ConcurrentRequestsPerModel` (streaming load) and `GPUUtilization`/detailed observability (resource
  headroom) rather than `InvocationsPerInstance`, which undercounts long-lived streaming connections.

## Related

- `reference/logging.md` — stdout/CloudWatch Logs contract (this file is the metrics counterpart)
- `reference/billing.md` — the `X-Amzn-Inference-Metering` header contract that EMF metering should mirror
- `reference/websocket.md` — the metadata-channel metering protocol for bidirectional streaming
