"""Per-tier verification gates for a benchmark run.

A throughput number is only worth quoting if you can show the run that produced
it was healthy. These helpers are model- and GPU-agnostic checks that turn a
plausible-looking tier into a *verified* one, and they exist because each of
them caught a real measurement error in this repo's own results:

* :func:`check_completeness` — LLMeter reports ``failed_requests=0`` even when
  clients time out and are silently dropped, because a dropped client returns
  an empty list rather than an error. One run lost 9% of its responses (36,394
  of 40,000) while reporting a clean sweep. Gate on responses actually on disk.

* :func:`cross_check_throughput` — compare LLMeter's own stats against
  wall-clock and against a recount from disk. Divergence means the reported
  window and the real one disagree; the usual cause is a straggler tail
  (dense work finishes early, a handful of slow requests stretch the window,
  and naive tok/min understates the achieved rate).

* :func:`scrape_vllm_metrics` — vLLM's ``usage.cached_tokens`` is not populated
  even with ``--enable-prefix-caching``, so the only way to see cache behaviour
  is the Prometheus endpoint. It also exposes the preemption counter, which is
  the single most important health signal at high concurrency: a tier with
  hundreds of preemptions is the engine evicting running sequences and
  recomputing their prefill later, and its throughput is neither stable nor
  reproducible no matter how good the headline number looks.

Nothing here is specific to one model or GPU family; the thresholds are
deliberately conservative defaults you can tighten per run.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# Counters worth pulling from vLLM's /metrics. Summed across data-parallel
# replicas, since each DP engine exports its own series.
_METRIC_KEYS = (
    "prefix_cache_queries_total",
    "prefix_cache_hits_total",
    "num_preemptions_total",
    "prompt_tokens_total",
    "generation_tokens_total",
)

# Defaults chosen from observed failures: a healthy tier in this harness lands
# at 100% completeness and <2% timing divergence, so 98%/5% flags real trouble
# without tripping on noise.
DEFAULT_MIN_COMPLETENESS = 0.98
DEFAULT_MAX_DIVERGENCE = 0.05


@dataclass
class TierVerdict:
    """Outcome of verifying one concurrency tier."""

    concurrency: int
    valid: bool
    reasons: list[str] = field(default_factory=list)
    completeness: float | None = None
    divergence: float | None = None
    preemptions: float | None = None
    prefix_cache_hit_rate: float | None = None
    distinct_prompts: int | None = None

    def as_dict(self) -> dict:
        return {
            "concurrency": self.concurrency,
            "valid": self.valid,
            "reasons": list(self.reasons),
            "completeness": self.completeness,
            "divergence": self.divergence,
            "preemptions": self.preemptions,
            "prefix_cache_hit_rate": self.prefix_cache_hit_rate,
            "distinct_prompts": self.distinct_prompts,
        }


# -----------------------------------------------------------------------------
# Response-set inspection
# -----------------------------------------------------------------------------
def count_responses(output_dir: Path) -> tuple[int, int]:
    """Count response records on disk and how many carry distinct prompts.

    Returns ``(n_responses, n_distinct_prompts)``. A distinct-prompt count far
    below the response count means payloads were replayed — see
    :class:`~vllm_ec2_bench.endpoint.UniquePayloadEndpoint`.

    Missing or unreadable files count as zero rather than raising, so a
    verification pass never masks the underlying run error.
    """
    n_responses = 0
    prompts: set[str] = set()
    for path in sorted(Path(output_dir).rglob("responses*.jsonl")):
        try:
            with path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    n_responses += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # LLMeter records the payload it sent alongside the
                    # response; the user message is the per-request input.
                    payload = rec.get("payload") or {}
                    for msg in payload.get("messages", []) or []:
                        if msg.get("role") == "user":
                            prompts.add(str(msg.get("content"))[:512])
        except OSError:
            continue
    return n_responses, len(prompts)


def check_completeness(
    n_responses: int,
    n_expected: int,
    *,
    minimum: float = DEFAULT_MIN_COMPLETENESS,
) -> tuple[bool, float]:
    """Return ``(ok, ratio)`` for responses actually captured vs expected.

    ``n_expected`` is the tier's request budget (usually ``c * K``). Guards
    against a zero budget so a misconfigured tier fails loudly instead of
    dividing by zero.
    """
    if n_expected <= 0:
        return False, 0.0
    ratio = n_responses / n_expected
    return ratio >= minimum, ratio


# -----------------------------------------------------------------------------
# Throughput cross-check
# -----------------------------------------------------------------------------
def cross_check_throughput(
    *,
    stats_tokens_per_min: float,
    total_tokens: float,
    wall_clock_s: float,
    maximum_divergence: float = DEFAULT_MAX_DIVERGENCE,
) -> tuple[bool, float, float]:
    """Compare LLMeter's reported rate against a wall-clock recomputation.

    Returns ``(ok, divergence, wall_clock_tokens_per_min)`` where divergence is
    the absolute relative gap between the two rates. Both figures are legitimate
    measurements of different windows — LLMeter times its own request window,
    wall-clock includes tier setup and the straggler tail — so a gap is not
    automatically an error. It *is* a signal that you must decide which window
    you mean before quoting a number.
    """
    if wall_clock_s <= 0 or stats_tokens_per_min <= 0:
        return False, float("inf"), 0.0
    wall_rate = total_tokens / wall_clock_s * 60.0
    divergence = abs(wall_rate - stats_tokens_per_min) / stats_tokens_per_min
    return divergence <= maximum_divergence, divergence, wall_rate


# -----------------------------------------------------------------------------
# vLLM /metrics
# -----------------------------------------------------------------------------
def scrape_vllm_metrics(base_url: str, api_key: str, *, timeout_s: float = 15.0) -> dict:
    """Sum the interesting counters from vLLM's Prometheus ``/metrics``.

    ``base_url`` is the OpenAI-style base (``http://<ip>:8000/v1``); the
    ``/v1`` suffix is stripped. Values are summed across data-parallel
    replicas. Network problems are reported as an ``"error"`` key rather than
    raised, so a scrape failure degrades a verdict instead of aborting a run
    that has already cost GPU time.

    Derived keys are added when computable: ``prefix_cache_hit_rate``.
    """
    out: dict = {}
    url = base_url.replace("/v1", "").rstrip("/") + "/metrics"
    if not url.startswith(("http://", "https://")):  # pragma: no cover
        return {"error": f"unexpected scheme in metrics URL: {url!r}"}
    try:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # nosec B310
            text = resp.read().decode()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"error": str(exc)[:200]}

    for line in text.splitlines():
        if line.startswith("#"):
            continue
        for key in _METRIC_KEYS:
            if key in line:
                try:
                    out[key] = out.get(key, 0.0) + float(line.rsplit(" ", 1)[1])
                except (ValueError, IndexError):
                    # A malformed or truncated sample line is not worth failing
                    # a whole verification pass over; the counters we can parse
                    # are still useful and a missing key surfaces downstream.
                    continue

    queries = out.get("prefix_cache_queries_total")
    hits = out.get("prefix_cache_hits_total")
    if queries:
        out["prefix_cache_hit_rate"] = hits / queries if hits is not None else 0.0
    return out


# -----------------------------------------------------------------------------
# One-call tier verdict
# -----------------------------------------------------------------------------
def verify_tier(
    *,
    concurrency: int,
    output_dir: Path | str,
    n_expected: int,
    stats_tokens_per_min: float,
    total_tokens: float,
    wall_clock_s: float,
    metrics_before: dict | None = None,
    metrics_after: dict | None = None,
    min_completeness: float = DEFAULT_MIN_COMPLETENESS,
    max_divergence: float = DEFAULT_MAX_DIVERGENCE,
    max_preemptions: int = 0,
) -> TierVerdict:
    """Run every gate over one tier and return a single verdict.

    ``metrics_before``/``metrics_after`` are :func:`scrape_vllm_metrics` results
    bracketing the tier; the deltas are what matter, since the counters are
    cumulative for the life of the server.

    ``max_preemptions`` defaults to 0 — the strictest setting, and the right one
    when you intend to quote the result. Raise it only if you are deliberately
    characterising the engine past its stable concurrency ceiling.
    """
    verdict = TierVerdict(concurrency=concurrency, valid=True)

    n_responses, distinct = count_responses(Path(output_dir))
    verdict.distinct_prompts = distinct
    ok, ratio = check_completeness(n_responses, n_expected, minimum=min_completeness)
    verdict.completeness = ratio
    if not ok:
        verdict.valid = False
        verdict.reasons.append(
            f"completeness {ratio:.1%} < {min_completeness:.0%} "
            f"({n_responses}/{n_expected} responses on disk)"
        )

    ok, divergence, _ = cross_check_throughput(
        stats_tokens_per_min=stats_tokens_per_min,
        total_tokens=total_tokens,
        wall_clock_s=wall_clock_s,
        maximum_divergence=max_divergence,
    )
    verdict.divergence = divergence
    if not ok:
        verdict.valid = False
        verdict.reasons.append(
            f"timing divergence {divergence:.1%} > {max_divergence:.0%} "
            "(stats window and wall-clock disagree; check for a straggler tail)"
        )

    if metrics_before is not None and metrics_after is not None:
        before_p = metrics_before.get("num_preemptions_total")
        after_p = metrics_after.get("num_preemptions_total")
        if before_p is not None and after_p is not None:
            preemptions = after_p - before_p
            verdict.preemptions = preemptions
            if preemptions > max_preemptions:
                verdict.valid = False
                verdict.reasons.append(
                    f"{preemptions:.0f} preemptions > {max_preemptions} — the "
                    "engine evicted running sequences; this tier is past its "
                    "stable concurrency ceiling and is not reproducible"
                )
        q_before = metrics_before.get("prefix_cache_queries_total", 0.0) or 0.0
        q_after = metrics_after.get("prefix_cache_queries_total", 0.0) or 0.0
        h_before = metrics_before.get("prefix_cache_hits_total", 0.0) or 0.0
        h_after = metrics_after.get("prefix_cache_hits_total", 0.0) or 0.0
        if q_after > q_before:
            verdict.prefix_cache_hit_rate = (h_after - h_before) / (q_after - q_before)

    return verdict


__all__ = [
    "TierVerdict",
    "count_responses",
    "check_completeness",
    "cross_check_throughput",
    "scrape_vllm_metrics",
    "verify_tier",
    "DEFAULT_MIN_COMPLETENESS",
    "DEFAULT_MAX_DIVERGENCE",
]
