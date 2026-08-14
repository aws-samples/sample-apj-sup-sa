# Scaling

This sample is intentionally synchronous, per-request, and (by default)
uncached -- that's what keeps the core interception pattern in
`bridge/core.py` easy to read in one sitting. It's directly usable as-is
in a Lambda handler or any single-request context. This doc covers what
changes as volume grows, and what it costs.

## Concurrency

`resolve_image_urls()` does not manage concurrency itself -- it's a
plain function, safe to call from multiple threads or async tasks
because it doesn't share mutable state across calls (the module-level
`requests.Session` used for pooling is thread-safe by design). Bounding
*how many* calls run at once is the caller's decision, made where the
caller already controls concurrency:

```python
from concurrent.futures import ThreadPoolExecutor
from bridge.core import resolve_image_urls

def resolve_one(payload):
    return resolve_image_urls(payload)

with ThreadPoolExecutor(max_workers=8) as pool:
    resolved_payloads = list(pool.map(resolve_one, payloads))
```

From an `async` caller, run it in a thread pool executor rather than
forking the function into sync/async variants -- the OpenAI SDKs
themselves split sync/async at the client layer, not the wire protocol,
so this composes the same way:

```python
import asyncio

resolved = await asyncio.get_running_loop().run_in_executor(
    None, resolve_image_urls, payload
)
```

## Connection pooling and caching

See [CACHING.md](./CACHING.md) for the `session=` and `cache=` kwargs --
both opt-in, both no-ops when not passed, both live-verified against a
real AWS account (session reuse confirmed across calls; a cache hit
measured at effectively 0ms vs ~70ms for a live download+verify).

## Cost estimate

Three cost components scale with image-URL volume; none scale with
adding this bridge specifically -- it adds no billed AWS resource of its
own, only the compute already running your calling code.

**Image download** (per unique URL, until cached or re-fetched):
negligible for image hosts you control; if fetching from S3, GET
requests are $0.0004 per 1,000 (Standard tier, `us-east-1`, per
[AWS S3 pricing](https://aws.amazon.com/s3/pricing/)) -- 1M image fetches
≈ $0.40 in request fees alone, before any data transfer.

**Compute, if hosted on Lambda**: $0.20 per 1M requests +
$0.0000166667 per GB-second (x86; ~20% less on arm64), per
[AWS Lambda pricing](https://aws.amazon.com/lambda/pricing/). A
512MB function spending ~200ms per invocation resolving one image costs
roughly $0.20 (requests) + $1.70 (compute) per 1M invocations ≈ $1.90/1M
-- caching cuts the compute side further for repeated URLs, since a
cache hit skips the download+verify work entirely.

**Model inference (the dominant cost)**: vision tokens, not the bridge,
drive the real bill. Anthropic Claude models bill roughly
`(width × height) / 750` input tokens per image (capped around 1,568px
on the long edge on most Claude models); a 1024×768 image is ~1,050
tokens. At Claude Haiku 4.5's Bedrock on-demand price of $1.00/1M input
tokens, that's roughly $0.001 per image purely for vision input tokens,
before the text prompt and output. OpenAI-style tile-based models charge
a fixed base (e.g. 85 tokens) plus a per-tile charge; check
[Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) for the
specific model you're calling, since Bedrock Mantle and Bedrock Runtime
have model-specific per-token rates that vary widely by provider and
model size.

**Bottom line**: at meaningful volume, image download/compute costs are
a rounding error next to model inference cost. The `cache=` option
matters most when the *same* image URL recurs across many requests
(shared assets, templated prompts) -- it eliminates repeat download+
verification cost, but does not reduce vision token cost on the model
side, since each request still sends the same image bytes to the model.
