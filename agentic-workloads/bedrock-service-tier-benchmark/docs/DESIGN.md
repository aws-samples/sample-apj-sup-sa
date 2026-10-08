# Design: Bedrock service-tier benchmark v2

This document records why the benchmark is built the way it is. The user-facing guide is the
[README](../README.md).

## 1. What changed since v1 (June 2026), and why

| Area | v1 | Problem | v2 |
|---|---|---|---|
| Prompt | One fixed ~30-token prompt, reused on every call | Bedrock **implicit prompt caching** is on by default. A live probe (GLM 5.3, Oct 2026) showed the 2nd identical request read 100% of its prefix from cache on all four runtime APIs. Every v1 sample after the first was therefore a warm-cache measurement. | Every cold request carries a fresh random nonce as its first line and a unique document. Each sample's cache-read tokens are recorded, and a cold sample that reports a cache read is flagged as contaminated. |
| APIs | InvokeModel stream (runtime), Chat Completions stream (Mantle) | Bedrock now serves five APIs on `bedrock-runtime` and three on `bedrock-mantle`, and AWS recommends `bedrock-runtime` for new applications. | Endpoint × API matrix (section 2). |
| Models | In-region model IDs only | New models (GLM 5.3, Kimi K3, ...) are served **only** through cross-Region inference profiles (`us.`, `global.`). v1 discovery cannot see them. | Inference scope dimension: `in_region`, `geo`, `global`. |
| Tiers | default, flex, priority | Bedrock also has `reserved` (requires a reservation). | `reserved` is supported when the account has a reservation; otherwise it is skipped. |
| Prompt size | One size | Latency depends heavily on input length (prefill). | `small` ≈1k, `medium` ≈10k, `large` ≈100k input tokens (Artificial Analysis workloads). |
| Metrics | TTFT, total latency; p20/p50/p90 | No decode-speed metric, no tail, no uncertainty; TTFT counted empty chunks; reasoning models emit only reasoning tokens in small budgets. | TTFT, time to first answer token, E2E, inter-token latency, output tokens/s, server-side first-byte latency; p50/p90/p95/p99, mean, stdev; bootstrap 95% CI on p50 and on tier deltas. |
| Fairness | Tier order fixed per round; first sample kept | Order bias; cold-connection outlier. | Tier order shuffled per round (seeded); one warm-up request per cell discarded. |
| Timeouts | 120 s | Flex queueing took 16-37 s on a trivial prompt. | Per-tier timeout (flex 600 s by default). |

## 2. Dimensions

A **cell** is one combination of:

| Dimension | Values |
|---|---|
| model | from the registry |
| endpoint | `runtime` (`bedrock-runtime.{region}.amazonaws.com`), `mantle` (`bedrock-mantle.{region}.api.aws`) |
| api | runtime: `converse_stream`, `invoke_stream`, `chat_completions`, `responses`, `messages`; mantle: `chat_completions`, `responses`, `messages`. Non-streaming `converse` / `invoke` are opt-in (they yield only E2E, no TTFT). |
| scope | `in_region`, `geo`, `global` (inference-profile prefix) |
| region | source region the request is sent to |
| prompt_size | `small`, `medium`, `large` |
| cache | `cold`, `warm_implicit`, `warm_explicit` |
| tier | `default`, `flex`, `priority`, `reserved` |

Only combinations the model actually supports are expanded (from the registry). `warm_*` cells are
expanded only when the prompt prefix meets the model's minimum cacheable tokens. The default run is a
subset (section 6); the full cross product is opt-in because it is large.

Tiers are the *treatment*; every other dimension is a *context*. Results are always compared
**within one context** (same model, endpoint, API, scope, region, size, cache), tier against
`default`.

## 3. Measurement

Timing uses `time.perf_counter()` around the streaming call. Body serialisation is inside the timed
section; credential refresh and client construction are outside it.

| Metric | Definition |
|---|---|
| TTFT | Request start → first stream event that carries non-empty generated text **or reasoning**. Empty role-only chunks are ignored (NVIDIA AIPerf rule). |
| TTFAT | Request start → first non-empty **answer** text (after any reasoning). Equals TTFT for non-reasoning models. |
| E2E | Request start → last content event (terminal `[DONE]`/stop/metadata events excluded). |
| ITL (= TPOT) | `(E2E − TTFT) / (output_tokens − 1)`, using provider-reported output tokens (AIPerf definition). |
| Output tokens/s | `(output_tokens − 1) / (E2E − TTFT)`. |
| Server first byte | `amazon-bedrock-invocationMetrics.firstByteLatency` (InvokeModel) or `metadata.metrics.latencyMs` (Converse) when provided; lets the report separate time inside Bedrock from network and front-door time. |
| Tokens | input, output, reasoning, cache-read, cache-write, as reported by the API. |
| Served tier | the tier Bedrock reports it used (header, response body, or stream metadata, per API). A sample served on a different tier than requested is excluded from that tier's statistics and counted. |

Statistics per cell: n, mean, stdev, min, max, p50, p90, p95, p99. p50 is the headline. Tier deltas
(Δp50 vs default) carry a bootstrap 95% confidence interval (10,000 resamples, seeded). A delta whose
interval contains 0 is reported as "no significant difference".

### Prompts

Prompts are generated locally and deterministically from a seed, so runs are reproducible but no two
requests share a prefix unless the cache mode asks them to:

- **Document**: synthetic, varied English text (sentences drawn from a fixed word bank with a seeded
  RNG) sized to the target token count.
- **Task**: a fixed instruction that asks for a bounded answer ("summarise in N bullet points").
- **Nonce**: `Request <uuid4>` as the first line of cold prompts so no prefix matches.
- Output length is controlled with `max_tokens` (default 512 for small/medium, 1024 for large) and the
  instruction. Reasoning models get a larger budget so they reach answer tokens; the actual output and
  reasoning tokens are reported so results stay comparable.
- Sampling: temperature 0 for non-reasoning models; the model default for reasoning models.

### Cache modes

| Mode | Prompt construction | Verification |
|---|---|---|
| `cold` | nonce + unique document + task | cache-read tokens must be 0; otherwise the sample is flagged `cache_contaminated` |
| `warm_implicit` | shared document per cell (no nonce) + unique short question at the end; one priming request discarded | cache-read tokens > 0, else sample is `warm_miss` (counted, excluded from warm stats) |
| `warm_explicit` | as `warm_implicit` plus the API's native cache checkpoint after the document | same as above |

Native checkpoints (verified live on GLM 5.3): Converse `{"cachePoint": {"type": "default"}}`;
Chat Completions / InvokeModel (OpenAI body) `prompt_cache_breakpoint` on the text part plus
`prompt_cache_options`; Responses `prompt_cache_breakpoint` on the `input_text` block;
Anthropic Messages `cache_control: {"type": "ephemeral"}`.

All tiers of a warm context share one document, and the context's requests are at most
`tiers x --interval` apart (3 min with three tiers at the default 60 s), inside the 5-minute minimum cache
TTL. With more tiers or a longer interval, check the cache-hit rate in the report: misses are excluded
and counted, never silently mixed in.

## 4. Pacing and fairness

- A **pacing domain** is (endpoint, model id, region). Requests in a domain run strictly serially with
  `--interval` seconds between starts (default 60). Domains run in parallel.
- Inside a domain, contexts run one after another (seeded order). Inside a context, the tiers are
  interleaved and shuffled every round, so no tier systematically runs first and all tiers see the same
  conditions.
- A request that overruns its slot shifts the schedule; the next slots are not fired back to back.
- Every request has a wall-clock deadline (per tier; flex 600 s by default) checked on each stream event.
- SDK retries are disabled: a throttle becomes one recorded error, not a silently slow sample.
- Discarded warm-up rounds per context (at least one for warm contexts, which must prime the cache).
- Responses API requests always send `store=false` (Bedrock otherwise retains the request and response
  for 30 days).

## 5. Discovery and the model registry

No Bedrock API reports which tiers, APIs or scopes a model supports. Discovery combines three sources:

1. **Control plane**: `ListFoundationModels`, `ListInferenceProfiles` (runtime), `GET /v1/models`
   (Mantle) give the candidate model and profile IDs.
2. **Model cards** (Models at a glance): structured HTML tables give APIs per endpoint, tiers, scopes per
   region and caching support (minimum tokens, TTL). They are parsed from raw HTML, because support
   marks are icons. Mantle uses `/v1` unless a registry entry sets `base_path` to `/openai/v1`; no other
   value is accepted, because the bearer token is sent to that URL.
3. **Live probes** are ground truth: one tiny request per (model, endpoint, API, scope, tier) confirms
   the documented support and records the served tier.

The registry is a JSON file (`models.json`, schema v2) for the CLI. The optional web deployment stores
the same data in Aurora and refreshes it daily with a Strands agent (see
[`deploy/README.md`](../deploy/README.md)).

## 6. Run profiles

| Profile | Contents | Approx. requests |
|---|---|---|
| `quick` | 1 model, runtime, `converse_stream` + `chat_completions`, small, cold, default+flex, n=5 | ~25 |
| `standard` (default) | every model, every supported API on its primary endpoint, small + medium, cold + warm_implicit, all tiers, n=30 | large; shown by `--dry-run` |
| `full` | the full cross product including large prompts and both endpoints | very large; opt-in |

`--dry-run` always prints the cell count, request count, token estimate and wall-clock estimate before
anything is sent.
