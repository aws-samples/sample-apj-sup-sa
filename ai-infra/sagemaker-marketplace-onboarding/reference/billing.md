# Billing — Hourly vs Per-Inference Reference

The container-side implications of the two Marketplace billing models. This skill covers only the container hooks — the actual Marketplace listing configuration (pricing values, dimensions, EULA) happens in the Marketplace Management Portal and is outside this skill's scope.

## The customer pays two things

Regardless of which billing model you choose:

1. **Instance usage price** — set by AWS, always per hour per instance.
2. **Model usage price** — set by you, either hourly or per-inference.

## Hourly pricing

- Customer pays $/hour for every instance running your model.
- No container-side code required. `/invocations` returns its result as usual.
- Different prices per instance type are supported.
- Example: $2/hr × 3 instances × 2 hours = $12.

## Per-inference (usage-based) pricing

- Customer pays for units consumed per invocation. **The container must report metering on every invocation.** Fail to report = fail to be paid for that invocation.
- Metering emission depends on the endpoint:

### Standard `/invocations` (Real-Time, Streaming response, Batch Transform)

Emit metering as an HTTP response header. The value is a JSON string.

```
X-Amzn-Inference-Metering: {"Dimension": "inference.count", "ConsumedUnits": 3}
```

**Mini-batch semantics.** If a single `/invocations` call processes multiple inferences (e.g. batching several inputs), set `ConsumedUnits` to the number of inferences processed. For a single-inference call, use `1`.

**Only 2XX responses are billed.** AWS Marketplace charges the buyer only for responses with HTTP status codes in the 2XX range. Metering on 4XX/5XX responses is ignored. Conversely, do not return 200 with a silent error body — you would be paid for a failed inference. Make error paths return proper non-2XX codes.

Reference: https://docs.aws.amazon.com/marketplace/latest/userguide/machine-learning-pricing.html

### Bidirectional WebSocket (`/invocations-bidirectional-stream`)

Metering goes on a **companion metadata WebSocket** — never on the main data stream. This is a GA
capability (Deepgram is the launch partner) that exists specifically because a bidi session's total
usage isn't known until the stream ends, so the header-based mechanism below can't apply. See
`reference/websocket.md` for the full protocol: opt-in headers, message schema, size/rate limits
(512 bytes, 1 msg/sec), and failure-mode behavior (`X-Amzn-SageMaker-Metadata-Stream-Required`).

Source: ["Introducing usage-based pricing for Amazon SageMaker bidirectional
streaming"](https://aws.amazon.com/blogs/machine-learning/introducing-usage-based-pricing-for-amazon-sagemaker-bidirectional-streaming/)

## Choosing a dimension

The dimension name is a naming decision that must match between the container and the Marketplace listing configuration. Common patterns:

| Dimension | When to use |
|---|---|
| `inference.count` | One unit per invocation. Simplest. |
| `audio_seconds` | STT/TTS/audio models. Bill by audio duration processed. |
| `characters_synthesized` | TTS. Bill by characters spoken. |
| `characters_processed` | Text embedding/classification. Bill by input length. |
| `tokens` | LLM generation. Bill by tokens produced (or consumed). |
| `pixels` | Image models. Bill by pixels processed. |

Whatever you pick becomes part of the customer contract — you cannot change it later without creating a new listing.

## Critical pricing rules

- **You cannot switch models.** Hourly ↔ per-inference is a new listing.
- **Once a price is set** and the product reaches limited state, you cannot change it on your own. Changes go through AWS support.
- **90-day freeze** after any price update. During the freeze:
  - You cannot add or remove supported instance types.
  - Existing subscribers stay on the old price for 90 days.
  - New subscribers get the new price immediately.
- **Workaround for the freeze:** create a new product and unpublish the old one.
- **Do not choose "Free"** if you plan to charge later. Use "$0/hour" instead — a Free listing cannot be converted to paid.

## Currency

- Public pricing: **USD only**.
- Private offers: USD or INR (useful for India-based listings).

## Private offers

Enterprise-specific pricing with a custom EULA and duration. Contract-based (fixed payment, unlimited access for the duration). Cannot mix pricing types — a hourly public listing cannot have a per-inference private offer.

Required inputs for a private offer:
- ProductId
- Targeted Buyer AWS Account(s)
- Offer acceptance deadline
- Offer duration (in days)
- Custom EULA file (optional)
- Prices per instance type

Configuration happens in the Marketplace Management Portal — outside this skill's scope.

## Container-side implication summary

| Setup | What the container must do |
|---|---|
| Hourly pricing, no WebSocket | Nothing special. Return inference results. |
| Hourly pricing, WebSocket | Nothing special beyond the WebSocket contract. |
| Per-inference, `/invocations` only | Emit `X-Amzn-Inference-Metering: {"Dimension": "...", "ConsumedUnits": N}` on every 2XX response. |
| Per-inference, WebSocket | Advertise metadata support in upgrade response, emit metering frames on companion channel. |
