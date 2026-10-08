# Amazon Bedrock Service-Tier Benchmark

Measure how much latency each Amazon Bedrock
[service tier](https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html)
costs or saves for the model you use, in the exact way you call it.

Bedrock lets you choose a tier per request: `flex` is discounted for latency-tolerant work,
`priority` costs more for preferential processing, `default` (Standard) is the baseline, and
`reserved` is contracted capacity. The answer to "how much slower is flex?" depends on more than
the model. This benchmark compares tiers **within one context** across these dimensions:

| Dimension | Values |
|---|---|
| Endpoint | `runtime` (`bedrock-runtime`, AWS's recommended endpoint), `mantle` (`bedrock-mantle`) |
| API | ConverseStream, Converse, InvokeModel(WithResponseStream), Chat Completions, Responses, Anthropic Messages |
| Inference scope | in-Region model id, geographic profile (`us.`…), global profile (`global.`) |
| Region | the source region you call from |
| Prompt size | `small` ≈1.5k, `medium` ≈10k, `large` ≈100k input tokens |
| Prompt cache | `cold`, `warm_implicit`, `warm_explicit` |
| Tier | `default`, `flex`, `priority`, `reserved` |

Run it from a laptop or CI and open the self-contained, filterable `report.html`. The
`summary.json` it writes is a stable, versioned format, so results can also be loaded into a
dashboard or database of your own.

> [!IMPORTANT]
> This sample sends real inference requests to Amazon Bedrock and **incurs cost**. Run
> `bedrock-bench --dry-run` first: it prints the request count and token volume without calling AWS.
> See [Cost](#cost).

## Table of contents

- [What it measures](#what-it-measures)
- [How the measurement stays accurate](#how-the-measurement-stays-accurate)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Usage](#usage)
- [Outputs](#outputs)
- [Model discovery](#model-discovery)
- [Cost](#cost)
- [Security](#security)
- [Cleanup](#cleanup)
- [Project structure](#project-structure)
- [Limitations](#limitations)
- [Development](#development)
- [References](#references)

## What it measures

Definitions follow [NVIDIA AIPerf](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html)
and [Artificial Analysis](https://artificialanalysis.ai/methodology/performance-benchmarking).
The full methodology is in [docs/DESIGN.md](docs/DESIGN.md).

| Metric | Meaning |
|---|---|
| **TTFT** | Time to first token: request start to the first non-empty streamed text or reasoning. |
| **TTFAT** | Time to first *answer* token (after any reasoning). Equals TTFT for non-reasoning models. |
| **E2E** | End-to-end latency: request start to the last streamed token. |
| **ITL** | Inter-token latency: `(E2E − TTFT) / (output tokens − 1)`. |
| **Output tok/s** | Decode speed after the first token. |
| **Server first byte** | Bedrock's own first-byte latency, where the API reports it, to separate time inside Bedrock from network time. |
| **Burst** | Share of samples whose stream arrived all at once after queueing (decode under 5% of E2E). Flex often behaves this way, so for flex compare **E2E**, not ITL. |

Each cell reports n, mean, stdev and p50 / p90 / p95 / p99. Each non-default tier is compared with
`default` in the same context as **Δp50**, with a bootstrap 95% confidence interval; a difference whose
interval includes zero is marked *not significant*.

## How the measurement stays accurate

- **No accidental cache hits.** Bedrock caches prompt prefixes automatically. Every cold request starts
  with a random `Request <uuid>` line and a freshly generated document, and every sample's cache-read
  tokens are checked: a cold sample that read from cache is excluded and counted as
  `cache_contaminated`.
- **Warm cache is verified, not assumed.** Warm cells reuse one document per cell, send one discarded
  priming request, and keep only samples that report cache-read tokens (`warm_miss` otherwise).
  `warm_explicit` adds the API's native cache checkpoint (Converse `cachePoint`, `prompt_cache_breakpoint`
  for OpenAI-compatible APIs, `cache_control` for Messages).
- **The tier you got is the tier you measured.** The served tier is read from each response; samples
  served on another tier are excluded as `tier_mismatch`.
- **Fair ordering and pacing.** Requests to one model id, endpoint and region run one at a time,
  `--interval` seconds apart; the tier order is shuffled every round; one warm-up request per cell is
  discarded; SDK retries are disabled so throttles are recorded, not hidden.
- **Reasoning models answer.** Reasoning effort defaults to `none` (configurable) so output budgets go
  to answer tokens; reasoning tokens are reported.
- **Responses API data is not retained.** Responses requests always send `store=false`.

## Prerequisites

- **Python 3.10+**
- **An AWS account** with access to the Amazon Bedrock models you want to benchmark.
- **AWS credentials** for the standard
  [boto3 credential chain](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html).

### IAM permissions (least privilege)

Narrow the resources to the models and profiles you benchmark.

```jsonc
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockRuntimeInference",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:*:ACCOUNT_ID:inference-profile/*",
        "arn:aws:bedrock:*:ACCOUNT_ID:project/default" // OpenAI-compatible APIs on bedrock-runtime
      ]
    },
    {
      "Sid": "BedrockApiKeys",            // short-lived bearer tokens for the OpenAI/Messages APIs
      "Effect": "Allow",
      "Action": ["bedrock:CallWithBearerToken"],
      "Resource": "*"
    },
    {
      "Sid": "MantleInference",
      "Effect": "Allow",
      "Action": ["bedrock-mantle:CallWithBearerToken", "bedrock-mantle:CreateInference"],
      "Resource": "*"
    },
    {
      "Sid": "Identity",
      "Effect": "Allow",
      "Action": ["sts:GetCallerIdentity"],
      "Resource": "*"
    }
  ]
}
```

Cells you cannot access are dropped at preflight instead of failing the run.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install --require-hashes -r requirements.lock   # exact, hash-pinned dependencies
pip install --no-deps -e .
export BEDROCK_BENCH_PROFILE=my-aws-profile   # optional; otherwise the default chain
```

`requirements.lock` (runtime) and `requirements-dev.lock` (adds tests and linters) pin every
dependency, including transitive ones, with hashes. Regenerate them with
`uv pip compile pyproject.toml --generate-hashes -o requirements.lock` (add `--extra dev` for the dev
file) and check them with `pip-audit -r requirements-dev.lock --require-hashes --disable-pip`.

## Usage

```bash
# Models in the registry (bedrock_bench/models.json):
bedrock-bench --list-models

# Print the matrix, request count, token volume and time estimate. No AWS calls:
bedrock-bench --dry-run --keys zai.glm-5.3 --scopes geo

# Probe every cell once (a few tiny requests) and print go / no-go:
bedrock-bench --preflight-only --keys zai.glm-5.3 --scopes geo

# Quick run (default preset): 2 APIs, small cold prompts, default vs flex, n=5:
bedrock-bench --keys zai.glm-5.3 --scopes geo

# Standard preset: 5 APIs on both endpoints, small+medium, cold+warm_implicit, 3 tiers, n=30:
bedrock-bench --preset standard --keys zai.glm-5.3,moonshotai.kimi-k3

# Exactly one context, e.g. GLM 5.3, Converse, global profile, large cold prompt:
bedrock-bench --keys zai.glm-5.3 --apis converse_stream --scopes global \
  --prompt-sizes large --cache-modes cold --tiers default,flex,priority -n 30

# Re-render the HTML report from an existing summary:
bedrock-bench --render results/<run_id>/summary.json
```

| Option | Purpose |
|---|---|
| `--preset quick\|standard\|full` | Base matrix (explicit flags override it). |
| `--keys`, `--families` | Choose models. |
| `--endpoints`, `--apis`, `--scopes`, `--regions` | Choose how requests are sent. |
| `--prompt-sizes`, `--cache-modes`, `--tiers` | Choose workload and treatments (`default` is always added). |
| `-n`, `--interval`, `--warmup` | Samples per cell, seconds between request starts, discarded warm-ups. |
| `--reasoning-effort` | `none` (default), `low`, … or `model-default`. |
| `--timeout`, `--flex-timeout` | Per-request ceilings (flex queues; default 600 s). |
| `--public` | Mask the account id and drop the profile name in reports. |

## Outputs

Each run writes `results/<run_id>/`:

| File | Contents |
|---|---|
| `report.html` | Self-contained interactive report: filter by every dimension; tier deltas colour-coded with confidence intervals; per-cell percentiles, cache-hit rate, burst share, exclusions and errors. |
| `report.md` | The same comparison in Markdown. |
| `summary.json` | Machine-readable summaries and comparisons (schema version 2). |
| `summary.csv` | One row per cell. |
| `raw.jsonl` | One line per measured request, written live. |

## Model discovery

No Bedrock API reports which tiers, APIs or scopes a model supports. `bedrock-bench-discover` reads the
[endpoint-availability page](https://docs.aws.amazon.com/bedrock/latest/userguide/models-endpoint-availability.html)
and every model card, then (with `--probe`) confirms each documented tier with one tiny live request.

```bash
bedrock-bench-discover --dry-run                       # documentation only, prints the list
bedrock-bench-discover --models "GLM 5.3,Kimi K3" --probe   # verify and write models.json
```

Only text models with more than one on-demand tier are kept.

## Cost

Requests are billed per token at each tier's rate; there is no free tier for these calls.

- `--dry-run` prints the request count, approximate input tokens and maximum output tokens.
- Every cold request uses a new prompt, so Bedrock writes it to the prompt cache; for models that charge
  more for cache writes than for input, cold cells cost more than plain input pricing suggests.
- `large` prompts are ~100k tokens each; keep them to targeted runs.

See [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/).

## Security

- **No long-lived secrets.** Credentials come from the boto3 chain. Bedrock bearer tokens are minted
  in memory from those credentials, cached for under 12 hours, and never written to disk or logged.
- **Only Bedrock documentation is fetched** by discovery (HTTPS, `docs.aws.amazon.com/bedrock/`).
- **Reports contain no secrets.** Use `--public` to mask the account id before sharing.
- **The HTML report is static and escaped.** Data is embedded as JSON and rendered with `textContent`;
  a Content-Security-Policy blocks all network access.
- **Responses API requests send `store=false`**, so Bedrock does not retain them.

To report a security issue, follow the disclosure process of the repository this sample is published under.

## Cleanup

The CLI creates no AWS resources. Remove local artifacts with:

```bash
rm -rf results/ && deactivate && rm -rf .venv
```

## Project structure

```
bedrock_bench/
├── __main__.py        # CLI (bedrock-bench)
├── config.py          # dimensions (enums) + BenchmarkConfig
├── registry.py        # models.json schema v2 (offerings per endpoint / API / scope)
├── models.json        # generated registry
├── catalog.py         # parses Bedrock docs (endpoint availability, model cards)
├── discovery.py       # docs + live probes -> models.json (bedrock-bench-discover)
├── cells.py           # expands config x registry into cells
├── prompts.py         # cold / warm prompt generation
├── apis/              # one adapter per API (Converse, InvokeModel, OpenAI-compatible, Messages)
├── runner.py          # paced execution, sample verification
├── metrics.py         # percentiles, bootstrap CIs, burst detection
├── report.py          # JSON / CSV / Markdown
├── html_report.py     # interactive self-contained HTML
└── benchmark.py       # orchestrator (estimate -> preflight -> run -> report)
docs/DESIGN.md         # methodology and rationale
tests/                 # unit tests (no AWS calls)
```

## Limitations

- **Results are a snapshot.** Latency depends on time of day, region and overall load. Compare tiers
  within one run; use n ≥ 30 for stable medians and treat p99 as directional.
- **Flex queues.** Flex requests may wait tens of seconds and then stream all at once; the report's
  Burst column shows when that happened.
- **Documentation can lag.** Discovery's `--probe` step is the ground truth for tier support.
- **Reserved tier** needs a capacity reservation and is only benchmarked when requested.

## Development

```bash
pip install --require-hashes -r requirements-dev.lock && pip install --no-deps -e .
ruff check bedrock_bench tests && ruff format --check bedrock_bench tests
mypy bedrock_bench
pytest -q
bandit -c pyproject.toml -r bedrock_bench
pip-audit
```

See [CHANGELOG.md](CHANGELOG.md) for release history.

## References

- [Amazon Bedrock service tiers](https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html)
- [Endpoints supported by Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/endpoints.html)
- [Responses API on Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-responses-api.html)
- [Prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
- [NVIDIA AIPerf metrics](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html)
- [Artificial Analysis benchmarking methodology](https://artificialanalysis.ai/methodology/performance-benchmarking)
- [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)
