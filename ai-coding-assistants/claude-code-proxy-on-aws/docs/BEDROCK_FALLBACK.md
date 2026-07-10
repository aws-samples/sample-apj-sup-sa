# Bedrock to Anthropic 1P Fallback

This document describes the Bedrock-to-Anthropic-1P fallback mechanism added on the
`feat/1p-fallback` branch: what it does, how it is wired, how it behaves at runtime,
what it deliberately does not cover, and how it was verified.

It is code-aligned. If this document and the code disagree, trust the code:

- `gateway/domains/runtime/services.py`
- `gateway/domains/runtime/bedrock_client.py`
- `gateway/domains/runtime/anthropic_client.py`
- `gateway/domains/runtime/circuit_breaker.py`
- `gateway/domains/runtime/converter/request_converter.py`

## Goal

When Bedrock is unavailable, the gateway transparently routes the same request to the
Anthropic first-party (1P) Messages API so that clients keep working during a Bedrock
outage. When Bedrock recovers, traffic automatically returns to Bedrock, so the
organization keeps consuming pre-purchased Bedrock capacity and only spends on 1P during
an actual outage window.

The Bedrock Converse / ConverseStream path itself is unchanged. Fallback is an additional
leg layered on top of the existing runtime, not a rewrite of the working Bedrock path.

## What triggers fallback

Fallback is driven entirely by how a Bedrock failure is classified. `bedrock_client.py`
maps boto exceptions to three categories:

| Category | Examples | Exception | Falls back? | Trips breaker? |
|----------|----------|-----------|-------------|----------------|
| Provider outage | `ServiceUnavailableException`, `InternalServerException`, `ModelTimeoutException`, and any other `ClientError` not otherwise classified | `BedrockError` | Yes | Yes |
| Throttle | `ThrottlingException`, `TooManyRequestsException` | `BedrockThrottlingError` | Yes | Yes |
| Connection / network | DNS, connect timeout, read timeout, endpoint unreachable (any `BotoCoreError`) | `BedrockError` | Yes | Yes |
| Client bug | `ValidationException`, `AccessDeniedException`, `ResourceNotFoundException`, signature/auth errors, `ParamValidationError`, etc. | `BedrockClientBugError` | No | No |

The key design point: a request that Bedrock rejected because the payload, auth, or policy
was wrong (`BedrockClientBugError`) is **not** retried against 1P, because the same payload
would fail upstream too. Only failures that indicate Bedrock-side unavailability are
eligible for fallback.

Network and connection failures (`BotoCoreError`) are explicitly treated as
fallback-eligible. These never reach Bedrock, so the service may still be healthy
elsewhere — they must surface as `BedrockError`, not as an unhandled botocore exception.

## Per-model eligibility

A model is fallback-eligible only if its catalog row has a non-empty `anthropic_model_id`.
This is the 1:1 mapping from the Bedrock model to its 1P equivalent (for example
`global.anthropic.claude-opus-4-8` → `claude-opus-4-8`). Models without
`anthropic_model_id` (for example non-Anthropic models such as GLM or MiniMax, which have
no 1P equivalent) skip fallback even when Bedrock fails and surface the original error.

The `anthropic_model_id` column was added to `model_catalog` in
`migrations/versions/003_add_anthropic_model_id_to_model_catalog.py` and is exposed through
the admin model catalog schemas.

## Circuit breaker

`circuit_breaker.py` implements an in-memory circuit breaker keyed per (Bedrock region,
model) so that once a given model in a region is known to be down, subsequent requests for
that same region+model skip Bedrock entirely and go straight to 1P, instead of paying the
failure latency on every request. Keying by model as well as region keeps one model's outage
or throttle (for example hitting its TPM/RPM quota) from diverting unrelated models in the
same region; a true region-wide outage still trips every model independently as each one
fails.

State machine, per (region, model) key:

```
CLOSED  -- record_failure --> OPEN
OPEN    -- timer expired   --> HALF   (next call probes Bedrock)
HALF    -- record_success  --> CLOSED
HALF    -- record_failure  --> OPEN   (timer reset)
```

- `allow_bedrock(key)` returns whether the next request for that (region, model) key should
  hit Bedrock. When the open window has elapsed it transitions `OPEN -> HALF` and lets the
  caller act as the probe.
- `record_failure` is called only for provider-outage and throttle failures, never for
  client-bug failures (the Bedrock service itself is healthy in that case).
- The open window is `BEDROCK_BREAKER_OPEN_SECONDS` (default `300`).

State is per ECS task and in memory. With multiple tasks behind the ALB, each task
discovers a Bedrock outage independently on its first failing request, and each recovers
independently after its own open window elapses. There is no shared/distributed breaker
state.

## Request flow

### Non-streaming (`POST /v1/messages`, `stream:false`)

`GatewayService.process_message`:

1. Run the policy chain and resolve the model.
2. If the breaker is open for the model's (region, model) key and the model is
   fallback-eligible, skip Bedrock and call 1P (`_call_anthropic_fallback`, reason
   `circuit_open`).
3. Otherwise convert the request and call `bedrock_client.converse`.
   - On `BedrockError` / `BedrockThrottlingError`: record a breaker failure, and if the
     model is fallback-eligible, call 1P (reason = the Bedrock error type). If not
     eligible, re-raise.
   - On success: record a breaker success (closing the breaker) and return the converted
     Bedrock response.

The 1P leg sends the original Anthropic-native request body (with `model` set to
`anthropic_model_id` and `stream` forced to `false`) and returns the response. 1P usage is
intentionally not recorded (see Cost behavior).

### Streaming (`POST /v1/messages`, `stream:true`)

`GatewayService.process_message_stream` mirrors the non-streaming path for the window
before the stream has started:

1. Run the policy chain and resolve the model.
2. If the breaker is open and the model is fallback-eligible, skip Bedrock and stream from
   1P (`_stream_anthropic_fallback`, reason `circuit_open`).
3. Otherwise call `bedrock_client.converse_stream`.
   - If the `ConverseStream` call fails **before the first chunk** with
     `BedrockError` / `BedrockThrottlingError`: record a breaker failure, and if the model
     is fallback-eligible, stream from 1P. If not eligible, re-raise.
   - On success: record a breaker success and stream the Bedrock response.

The 1P streaming leg (`anthropic_client.messages_stream`) issues a `stream:true` request to
1P and yields the raw Anthropic-native SSE bytes to the client. Because 1P already emits
Anthropic-native SSE, the bytes pass through unchanged — no re-encoding. 1P usage is
intentionally not recorded (see Cost behavior), so no usage is parsed off the stream.

The streaming 1P client checks the upstream HTTP status **before yielding the first chunk**.
A non-2xx response raises (`AnthropicThrottlingError` on 429, otherwise `AnthropicError`)
while no SSE has been sent yet, which keeps the fallback decision inside the
stream-not-yet-started window.

## What fallback does NOT cover (mid-stream)

Streaming fallback only applies while the stream has not started. There are two failure
moments and only the first is recoverable:

```
request arrives
  |
  +-- breaker OPEN?              --> 1P fallback (stream not started)
  |
  +-- converse_stream() call
  |       fails here             --> 1P fallback (stream not started)
  |
  +-- SSE chunks already sent
          Bedrock disconnects    --> NO fallback (client must retry)
```

Once the gateway has sent `message_start` and content deltas to the client, the HTTP 200
SSE response is already in flight. Switching providers mid-stream would replay the response
from the beginning and corrupt the client's view, and the SSE protocol cannot splice a new
message into an in-flight one. Mid-stream disconnects are therefore the client's retry
responsibility, which matches how the reference implementation
(`aws-kr-startup-samples/gen-ai/claude-code-proxy`) bounds streaming fallback with its
`streaming_started` flag.

In practice this is a narrow gap: most real Bedrock failures are either connection failures
(caught before the stream starts) or HTTP 5xx at request time (also before the stream
starts). A clean disconnect partway through a successful stream is comparatively rare, and
once the breaker is open all subsequent requests skip Bedrock immediately.

## User-visible latency

- First failure during a hard network block: the boto connect attempt must time out before
  fallback. `bedrock_client` sets `connect_timeout=20` and `read_timeout=120`, with
  `max_attempts=1`. Because the Bedrock Runtime interface VPC endpoint exposes one ENI (one
  private IP) per Availability Zone, a blackholed connection retries each IP in turn, so the
  observed first-failure latency is roughly `connect_timeout x IP count`. This is specific
  to a network blackhole; a normal Bedrock outage returns HTTP 5xx quickly and falls back
  without this delay.
- After the breaker opens: subsequent requests skip Bedrock and go straight to 1P. Measured
  end-to-end latency for a streaming request in this state was ~1.5s.
- Recovery: after `BEDROCK_BREAKER_OPEN_SECONDS` the next request probes Bedrock; on success
  the breaker closes and traffic returns to Bedrock automatically.

## Cost behavior

While the breaker is closed and Bedrock is healthy, all traffic uses Bedrock (pre-purchased
capacity). 1P is billed by Anthropic only for requests served during an open/half-open
window — i.e. during an actual outage. Once Bedrock recovers and the breaker closes, 1P
spend stops.

**Usage metering is Bedrock-only.** Requests served by Bedrock are recorded in
`usage_events` with cost computed from `model_pricing`, and they decrement budgets. Requests
served by the 1P fallback are intentionally **not** recorded: no `usage_events` row, no cost
estimate, and no budget decrement. Rationale: 1P spend already appears on the Anthropic
console, so the gateway only meters Bedrock spend and avoids double counting (and avoids
charging 1P tokens at Bedrock prices). The practical consequence is that during a Bedrock
outage, requests routed to 1P are not subject to the gateway's budget limits. The fact that a
fallback occurred is still visible in the logs (`falling back to anthropic 1p ...`).

## Networking

The Bedrock path uses the existing Bedrock Runtime interface VPC endpoint
(`com.amazonaws.<region>.bedrock-runtime`) defined in `infra/cdk_constructs/network.py`,
whose endpoint policy already allows both `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream`. This endpoint is part of the original
infrastructure and was not changed by the fallback work.

The 1P path calls `api.anthropic.com` over the internet (outbound via the existing NAT
path), so it does not depend on the Bedrock VPC endpoint. The Anthropic API key is stored in
Secrets Manager (`AnthropicApiKeySecret`), read on first fallback and cached in memory; see
the README section "Populate Anthropic API Key (For 1P Fallback)".

## Adaptive thinking fix (related)

While verifying advanced request shapes, a separate bug was found and fixed in
`request_converter.py`: `ADAPTIVE_THINKING_MODEL_FAMILIES` did not include
`claude-opus-4-8`. As a result, adaptive/extended thinking requests for Opus 4.8 were
converted to the legacy fixed-budget (`thinking.type: enabled`) shape, which Bedrock rejects
for that model ("use thinking.type.adaptive"). Adding `claude-opus-4-8` to the set routes
those requests through the adaptive normalization path. This is independent of fallback but
was on the same branch.

## Configuration

| Setting | Env var | Default | Purpose |
|---------|---------|---------|---------|
| 1P key secret ARN | `ANTHROPIC_API_KEY_SECRET_ARN` | (empty) | When empty, the 1P client is not constructed and fallback is disabled. |
| 1P base URL | `ANTHROPIC_API_BASE_URL` | `https://api.anthropic.com` | 1P API endpoint. |
| 1P API version | `ANTHROPIC_API_VERSION` | `2023-06-01` | `anthropic-version` header. |
| 1P request timeout | `ANTHROPIC_REQUEST_TIMEOUT_SECONDS` | `60` | httpx timeout for the 1P call. |
| Breaker open window | `BEDROCK_BREAKER_OPEN_SECONDS` | `300` | How long the breaker stays open before probing Bedrock again. |

## Verification

### Unit tests

- `tests/unit/gateway/test_bedrock_client.py` — error classification, including connection
  failures (`EndpointConnectionError`, `ConnectTimeoutError`, `ReadTimeoutError`) surfacing
  as fallback-eligible `BedrockError`.
- `tests/unit/gateway/test_circuit_breaker.py` — state machine transitions.
- `tests/unit/gateway/test_anthropic_client.py` — 1P request shape, throttling/error
  mapping, and streaming (`messages_stream`) including the before-first-chunk status check.
- `tests/unit/gateway/test_runtime_fallback.py` — non-streaming and streaming fallback,
  no-fallback on client-bug and on missing `anthropic_model_id`, breaker trip/skip/recover.
- `tests/unit/gateway/test_request_converter.py` — adaptive thinking for Opus 4.8.

### End-to-end (deployed dev environment)

Verified against the deployed gateway by temporarily replacing the Bedrock Runtime VPC
endpoint security group with an empty one to simulate a Bedrock outage, then restoring it:

- Non-streaming and streaming both fall back to 1P during the block (confirmed via response
  `model` field — `claude-opus-4-8` for 1P vs `global.anthropic.claude-opus-4-8` for
  Bedrock — and gateway logs showing `falling back to anthropic 1p ...`).
- With the breaker open, a streaming request returned a 1P-native SSE stream in ~1.5s.
- After restoring the endpoint and waiting out the breaker window, both paths returned to
  Bedrock automatically.
