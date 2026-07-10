# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-06-19

### Added
- `--public` flag (and `BenchmarkConfig.redact`) to mask the AWS account id and drop
  the profile name from generated reports, so they are safe to share externally.
- PEP 561 `py.typed` marker, so downstream consumers receive the package's type hints.

### Changed
- Reports now use the **p20 / p50 / p90** percentiles only; the p99 percentile has
  been removed everywhere (metrics, JSON, CSV, and the HTML/Markdown views), since at
  practical sample sizes it is a single worst-case observation rather than a stable
  statistic.
- Reports' IAM example in the README is now least-privilege by construction: the
  InvokeModel actions are scoped to model/inference-profile ARNs, with `"*"` kept only
  on the list/identity APIs that require it.
- Region validation is shared between the benchmark and discovery entry points and now
  accepts GovCloud regions (e.g. `us-gov-west-1`).
- Dependencies are single-sourced in `pyproject.toml`; `requirements.txt` now installs
  the package (`-e .`) instead of duplicating the dependency list. The package version
  is single-sourced from `bedrock_bench.__version__`.
- HTML report meets WCAG 2.2 AA basics: `scope`-associated table headers, `<main>`/
  `<nav>` landmarks, and a skip link.

### Fixed
- Raise the `boto3` floor to `>=1.40.0`: the InvokeModel `serviceTier` parameter
  (flex/priority) was added in botocore 1.40.0, and an earlier resolved version
  rejects it with a `ParamValidationError`, breaking the flex/priority benchmark.
- Bump the dev `pytest` pin to allow `>=9.0.3`, which fixes CVE-2025-71176.
- The scheduler now owns and shuts down its `ThreadPoolExecutor` instead of replacing
  the event loop's default executor on every run (no thread-pool leak across runs).
- Remove the unused `warmup` config flag whose docstring described behavior that was
  never implemented.
- Correct the package docstring to describe the three-tier (default/flex/priority)
  comparison.

### Security
- Verified clean under `bandit` (Python SAST), `pip-audit` (dependency CVEs), and
  `checkov` (no IaC/secrets findings). Suppressed two `bandit` B106 false positives
  on JMESPath token-count query strings, with justification.

## [0.2.0] - 2026-06-17

### Added
- **Priority service tier**: benchmarks now compare both `flex` and `priority`
  against `default` (three-way), with per-tier `Δp50` deltas in every report.
- **Model discovery** (`bedrock_bench.discovery`, `bedrock-bench-discover`): probes
  every Bedrock text model on both transports for which tiers it actually serves
  and regenerates the registry (`models.json`). Models are included only if they
  serve flex and/or priority on at least one transport.
- Packaging (`pyproject.toml`) with `[project.scripts]` entry points
  (`bedrock-bench`, `bedrock-bench-discover`), pinned dependency ranges, and
  ruff/mypy/pytest configuration.
- This changelog and a unit test suite (pytest).
- `--keys` CLI filter to benchmark specific models by logical key.
- Atomic report writes (temp-file + replace) so a crash never leaves a truncated
  `summary.json`/`report.html`.
- AWS region syntactic validation before the value is used to build an endpoint host.

### Changed
- Reports now feature `p20`/`p50`/`p90` and `Δp50`; `p99` is retained in
  `summary.json`/`summary.csv` but removed from the Markdown/HTML *views*
  (at n=30 it is a single worst sample).
- Credentials profile now defaults to the standard boto3 chain (or
  `$BEDROCK_BENCH_PROFILE`) instead of a hardcoded named profile.
- Model registry is now generated from live discovery rather than hand-curated,
  and is loaded lazily so importing the package performs no file I/O.

### Removed
- `--newest-only` flag (superseded by discovery + `--keys`).

## [0.1.0] - 2026-06-17

### Added
- Initial benchmark of Bedrock `flex` vs `default` tiers over InvokeModel and
  Mantle transports, using AWS Labs llmeter for measurement.
- Rate-paced scheduler (1 request/model/minute, parallel across models),
  percentile metrics (TTFT + total latency), and JSON/CSV/Markdown/HTML reports.
