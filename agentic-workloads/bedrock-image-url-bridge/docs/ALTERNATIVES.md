# Alternative: use an AI gateway instead of a hand-rolled bridge

If you're calling more than one model/provider and want URL handling
solved for you instead of maintaining `resolve_image_urls()` yourself,
put an AI gateway in front of Bedrock and let it own image URL
normalization.

**[LiteLLM](https://docs.litellm.ai/docs/proxy/architecture)** already
implements this exact pattern generically: it detects an `image_url` in
the request, checks whether the target provider accepts URLs natively,
and if not, downloads the image (capped at
`MAX_IMAGE_URL_DOWNLOAD_SIZE_MB`, 50 MB by default), converts it to
base64, and caches up to 10 converted images in memory to cut latency on
repeated calls. LiteLLM also has a dedicated
[Bedrock Mantle provider](https://docs.litellm.ai/docs/providers/bedrock_mantle),
so routing through it gets you the URL-handling fix plus routing,
fallbacks, retries, budgets, and spend tracking across every provider
you use -- at the cost of running (and securing) an extra proxy service.

Any OpenAI-compatible gateway with similar "fetch-and-inline" middleware
(a custom API Gateway + Lambda layer, for example) can apply the same
idea -- the security guards in [`bridge/core.py`](../bridge/core.py)
(SSRF guard, size cap, format verification; see
[SECURITY.md](./SECURITY.md)) are exactly what such middleware needs
regardless of where it runs.

## When to use which

- **This sample, as-is**: you want a single dependency-light function
  with no extra infrastructure, calling one Bedrock endpoint (or a
  handful) directly.
- **A gateway like LiteLLM**: you're already managing multiple
  providers/models and want URL handling, routing, and cost tracking
  solved together instead of piecemeal, and are fine operating an
  additional proxy service.
