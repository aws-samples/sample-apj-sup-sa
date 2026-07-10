# Amazon Bedrock Service-Tier Latency Benchmark

Measure and compare **first-token and end-to-end latency** across Amazon Bedrock's
three on-demand **service tiers** — `default` (Standard), `flex`, and `priority` —
for any text model, over two invocation paths.

Amazon Bedrock lets you pick a [service tier](https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html)
per request to trade off cost and latency: `flex` is cheaper for latency-tolerant
work, `priority` pays a premium for preferential processing, and `default` is the
balanced baseline. This sample answers the practical question **"how much latency
does each tier actually cost or save for the model I use?"** by running a careful,
rate-limited A/B/C benchmark and producing a self-contained HTML report.

Measurement is delegated to [AWS Labs **LLMeter**](https://github.com/awslabs/llmeter);
this sample adds per-request service-tier selection, a request-paced scheduler that
stays within account limits, automatic model discovery, and tier-comparison reporting.

> [!IMPORTANT]
> This sample sends real inference requests to Amazon Bedrock and **incurs cost**.
> See [Cost](#cost) before running. A full run is ~6,000 short requests.

---

## Table of contents

- [How it works](#how-it-works)
- [What it measures](#what-it-measures)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Usage](#usage)
- [Sample output](#sample-output)
- [Cost](#cost)
- [Cleanup](#cleanup)
- [Security](#security)
- [Project structure](#project-structure)
- [How the benchmark stays within limits](#how-the-benchmark-stays-within-limits)
- [Limitations](#limitations)
- [References](#references)

---

## How it works

The benchmark runs as a four-stage pipeline:

1. **Discover** — probe every Bedrock text model on each transport to learn which
   service tiers it actually serves, and write a generated registry
   (`bedrock_bench/models.json`). Models that serve only `default` are excluded —
   there is nothing to compare.
2. **Expand** — turn the registry into a matrix of *cells*, one per
   (model × transport × tier).
3. **Run** — the scheduler invokes the cells with strict pacing — **1 request per
   model per minute**, tiers interleaved, models in parallel (see
   [below](#how-the-benchmark-stays-within-limits)) — over both the InvokeModel and
   Mantle transports.
4. **Report** — reduce each cell's samples to latency percentiles and render the
   flex-vs-default and priority-vs-default comparison as HTML, Markdown, JSON, and CSV.

### Transports

| Transport | API | Authentication |
|---|---|---|
| **InvokeModel** | `bedrock-runtime` `InvokeModelWithResponseStream` (streaming) | AWS SigV4 (IAM) |
| **Mantle** | [`bedrock-mantle`](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-mantle.html) OpenAI-compatible Chat Completions (streaming) | Bedrock bearer token |

The same model may be reachable on one or both transports, with different model IDs
and different per-tier support — all captured automatically by discovery.

## What it measures

For every cell, over `n` samples (default 30):

- **TTFT** — Time To First Token: how long until the first streamed token arrives
  (the user-perceived responsiveness).
- **Total latency** — Time to the last token (full response).

Reported as **p20 / p50 / p90**, plus the **Δp50** of `flex` and `priority`
against `default`. Throughput is intentionally **not** measured, to keep request
volume low and stay clear of tokens-per-minute limits.

## Prerequisites

- **Python 3.10+**
- **An AWS account** with [Amazon Bedrock model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
  enabled for the models you want to benchmark, in `us-east-1` and/or `us-west-2`.
- **AWS credentials** available to the standard
  [boto3 credential chain](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html)
  (environment variables, a named profile, or an instance/container role).
- For the **Mantle** transport: permission to mint and use Bedrock bearer tokens
  (see the IAM policy below).

### IAM permissions (least privilege)

The inference actions are scoped to model / inference-profile ARNs; only the
discovery and identity APIs (which do not act on a specific resource) use `"*"`.
Replace `REGION` / `ACCOUNT_ID`, or narrow the ARNs to the exact models you
benchmark.

```jsonc
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeModelTransport",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModelWithResponseStream", "bedrock:InvokeModel"],
      // Scope to the models / inference profiles you actually benchmark:
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:*:ACCOUNT_ID:inference-profile/*"
      ]
    },
    {
      "Sid": "MantleTransport",
      "Effect": "Allow",
      "Action": ["bedrock-mantle:CallWithBearerToken", "bedrock-mantle:CreateInference"],
      "Resource": "*"  // Mantle actions are not resource-scoped
    },
    {
      // Discovery + run metadata: these list/identity APIs require "*"
      // (they do not operate on a specific resource).
      "Sid": "DiscoveryAndMetadata",
      "Effect": "Allow",
      "Action": [
        "bedrock:ListFoundationModels",
        "bedrock:ListInferenceProfiles",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

Cells you lack permission for are dropped at preflight rather than failing the
whole run.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .                     # add ".[dev]" for the test/lint tooling
```

Select credentials with a profile or the standard chain:

```bash
export BEDROCK_BENCH_PROFILE=my-aws-profile   # optional; else $AWS_PROFILE / default chain
```

## Usage

The package installs two console scripts (`bedrock-bench`, `bedrock-bench-discover`).
Equivalent `python -m bedrock_bench …` forms are shown in comments.

```bash
# 1. Print the benchmark matrix and a time estimate — no AWS calls, no cost:
bedrock-bench --dry-run

# 2. Probe every cell once to confirm access/tiers (a few cheap requests):
bedrock-bench --preflight-only

# 3. A quick, low-cost real run end to end (2 models, fast cadence):
bedrock-bench --keys zai.glm-5,deepseek.v3.2 -n 3 --interval 3

# 4. A full run: all discovered models, default+flex+priority, n=30, 1 req/min/model:
bedrock-bench -n 30

# Regenerate the model registry after Bedrock's catalog changes:
bedrock-bench-discover            # python -m bedrock_bench.discovery
```

Common options (`bedrock-bench --help` for all):

| Option | Purpose |
|---|---|
| `--keys k1,k2` | Benchmark only specific model keys (see `--dry-run` for the list) |
| `--families GLM,Qwen` | Restrict to model families |
| `--transports invoke` | One transport only (`invoke` or `mantle`) |
| `--tiers flex` | Restrict tiers (`default` is auto-added as the baseline) |
| `--regions us-east-1` | Region preference order |
| `-n / --n-requests` | Samples per cell (≥ 30 recommended for stable percentiles) |
| `--interval` | Seconds between requests per model (default 60) |
| `--output-dir` | Where reports are written (default `results/`) |

## Sample output

Each run writes a timestamped folder under `results/<run_id>/`:

| File | Contents |
|---|---|
| `report.html` | **Self-contained** report: glossary, an at-a-glance table, and per-model cards with colour-coded `flex`/`priority` deltas vs `default`. Open it in any browser. |
| `report.md` | The same comparison in Markdown. |
| `summary.json` | Per-cell percentile summaries + run metadata. |
| `summary.csv` | One row per cell, for spreadsheets/plotting. |
| `raw.jsonl` | One line per individual request (written live during the run). |

A redacted example report is included at
[`docs/sample-report/report.html`](docs/sample-report/) so you can see the format
without running a benchmark.

Every report shows the **p20 / p50 / p90** percentiles and the Δp50 deltas. The tier
Bedrock *actually served* (which can differ from what was requested) is captured per
request and shown in every report.

## Cost

This sample calls Amazon Bedrock with real inference requests, **billed per token**
at each tier's rate. There is no AWS Free Tier for these calls.

- The prompt is short and `max_tokens` defaults to **200**, so each request is small.
- A **full run** is roughly `models × transports × tiers × n` requests — on the order
  of **~6,000 short requests** with the default registry and `n=30`.
- Use `--dry-run` to see the exact request count first, and `--keys` / `--families` /
  `-n` to scope a smaller, cheaper run.

You are responsible for the cost of the Bedrock usage this tool generates. See
[Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/).

## Cleanup

This sample creates **no persistent AWS resources** — it only makes on-demand
inference calls, so there is nothing to tear down in your account. To remove local
artifacts:

```bash
rm -rf results/        # generated reports and raw samples
deactivate && rm -rf .venv
```

## Security

- **No long-lived secrets in code.** Credentials resolve through the standard boto3
  chain; no access keys are embedded or required as arguments.
- **Mantle bearer tokens are short-lived and in-memory.** They are minted from the
  resolved credentials, cached with a short TTL, refreshed in memory, and never
  written to disk or logged.
- **Reports contain no secrets.** Tokens, headers, and request bodies never appear in
  any output file or log. By default, reports include the AWS **account ID** and
  **profile name** as run metadata — run with **`--public`** to mask the account ID
  and drop the profile, producing a report that is safe to share externally.
- **Generated HTML is escaped.** All dynamic values (model IDs, served-tier labels,
  error strings) are HTML-escaped before rendering (no XSS from model output).

To report a security issue, follow the disclosure process of the repository this
sample is published under.

## Project structure

```
bedrock_bench/
├── __main__.py      # CLI entry point (bedrock-bench)
├── config.py        # enums (Tier/Transport/PayloadStyle) + BenchmarkConfig
├── discovery.py     # probes models for tier support → models.json (bedrock-bench-discover)
├── registry.py      # loads the generated model registry
├── models.json      # generated registry of benchmarkable models
├── cells.py         # expands config × registry into the measurement matrix
├── auth.py          # boto3 clients + Mantle bearer-token broker
├── endpoints.py     # LLMeter endpoint adapters that add serviceTier selection
├── payloads.py      # per-transport request bodies
├── scheduler.py     # rate-paced async execution engine
├── metrics.py       # percentile statistics
├── report.py        # JSON / CSV / Markdown reports
├── html_report.py   # self-contained HTML report
└── benchmark.py     # orchestrator (discovery → preflight → run → report)
tests/               # unit tests (pytest); all AWS/network calls are mocked
```

## How the benchmark stays within limits

Latency comparisons are only fair if requests aren't being throttled or queued, so
the scheduler is deliberate about pacing:

- A **pacing domain** = (transport, model, region). Within a domain, requests run
  **serially** with `--interval` seconds between starts (default 60 → **1 request per
  model per minute**), and the tiers are **interleaved** (default → flex → priority →
  …) so a model's tiers share one cadence and see comparable conditions.
- Domains run **in parallel**, so total wall-clock ≈ `tiers × n × interval` per model
  regardless of how many models — they all run concurrently. A full default run takes
  ~90 minutes.
- Per-request retries are disabled so a transient throttle surfaces as one recorded
  error rather than a silently slow sample.

## Limitations

- **Statistical strength scales with `n`.** At the default `n=30`, p50/p90 are
  reliable; treat tail latency as directional and use a larger `n` for firm
  tail-latency claims.
- **Latency depends on conditions** — region, time of day, prompt size, and overall
  Bedrock load. Treat results as a snapshot, and compare tiers *within the same run*
  rather than across runs.
- Some models stream differently; a few report total latency without a separable
  first-token signal, which shows as a reduced TTFT sample count for those cells.
- `service_tier` support and model availability change over time; re-run discovery to
  refresh the registry.

## References

- [Amazon Bedrock service tiers](https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html)
- [Amazon Bedrock Mantle (OpenAI-compatible endpoint)](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-mantle.html)
- [AWS Labs LLMeter](https://github.com/awslabs/llmeter)
- [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)

## Development

```bash
pip install -e ".[dev]"
ruff check bedrock_bench/ tests/      # lint
ruff format bedrock_bench/ tests/     # format
mypy bedrock_bench/                   # type check
pytest -q                             # tests (no AWS calls; all mocked)
```

### Security scanning

```bash
bandit -c pyproject.toml -r bedrock_bench/   # Python SAST
pip-audit                                    # dependency CVE scan
checkov -d . --compact                       # IaC / secrets scan
```

This is a pure-Python sample with no infrastructure-as-code, so Checkov reports no
scannable resources and its secrets scan is clean; `bandit` (Python SAST) and
`pip-audit` (dependency CVEs) provide the meaningful coverage and both pass clean.

See [`CHANGELOG.md`](CHANGELOG.md) for release history.
