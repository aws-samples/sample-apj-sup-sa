"""Collect outputs for a completed submission — download / sample / report."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from statistics import StatisticsError, median, quantiles
from typing import Any

import boto3
import pandas as pd

from ..submitter.submit import SubmissionReport
from .poll import StatusSnapshot

LOG = logging.getLogger(__name__)


def _percentile(values: list[float], p: float) -> float | None:
    """LLMeter-compatible percentile.

    Matches LLMeter 0.1.11's ``summary_stats_from_list`` (which backs
    ``llmeter.results.Result.stats``): uses ``statistics.median`` for p50
    and ``statistics.quantiles(data, n=k)`` with the smallest ``k`` in
    ``{4, 10, 100}`` that evenly divides ``p``. Produces numerically
    identical values to LLMeter on the same input.

    Returns ``None`` for empty input. Rounds to 4 decimals.
    """
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 4)
    try:
        if p == 50:
            return round(median(values), 4)
        for k in (4, 10, 100):
            if p % (100 / k) == 0:
                qs = quantiles(values, n=k)
                return round(qs[int(p * k / 100) - 1], 4)
    except StatisticsError:
        return None
    raise ValueError(f"Unsupported percentile {p}; must be 1-99 integer.")


@dataclass
class CollectReport:
    """What collector.download_outputs returns."""

    output_dir: Path
    files_downloaded: list[Path]
    per_shard_summary: list[dict[str, Any]]
    """One entry per shard with {shard_index, total, succeeded, failed, ...,
    input_tokens_per_second, output_tokens_per_second, wall_clock_s, ...}."""

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.per_shard_summary)

    def aggregate_throughput(self) -> dict[str, Any]:
        """Aggregate throughput across all shards.

        Two aggregation modes are useful and we report both:

        1. **Summed across shards** — treat the submission as one combined
           workload. Input/output tokens are summed; wall-clock is taken
           as the *maximum* across shards (which is what happens when
           shards run in parallel on separate containers).
        2. **Per-shard average** — the mean of per-shard throughput values.
           This is a single-endpoint-style number: directly comparable to
           any per-endpoint benchmark (e.g. LLMeter) on the same model +
           hardware.
        """
        rows = [r for r in self.per_shard_summary
                if r.get("wall_clock_s") is not None]
        if not rows:
            return {
                "mode": "empty",
                "shards_with_throughput_data": 0,
            }

        total_in = sum(r.get("total_input_tokens", 0) or 0 for r in rows)
        total_out = sum(r.get("total_output_tokens", 0) or 0 for r in rows)
        total_succeeded = sum(r.get("succeeded", 0) or 0 for r in rows)
        max_wc = max(r.get("wall_clock_s", 0.0) for r in rows)

        summed_total_tps = round((total_in + total_out) / max_wc, 2) if max_wc else None
        summed_in_tps = round(total_in / max_wc, 2) if max_wc else None
        summed_out_tps = round(total_out / max_wc, 2) if max_wc else None
        summed_rps = round(total_succeeded / max_wc, 3) if max_wc else None

        # Per-shard mean throughput (comparable to LLMeter per-endpoint nums)
        def _mean(key: str) -> float | None:
            vals = [r.get(key) for r in rows
                    if isinstance(r.get(key), (int, float))]
            return round(sum(vals) / len(vals), 2) if vals else None

        return {
            "shards_with_throughput_data": len(rows),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_succeeded_requests": total_succeeded,
            "max_wall_clock_s": max_wc,

            # "Submission as a whole" — use max wall-clock as the clock
            "summed_input_tokens_per_second": summed_in_tps,
            "summed_output_tokens_per_second": summed_out_tps,
            "summed_total_tokens_per_second": summed_total_tps,
            "summed_requests_per_second": summed_rps,

            # Per-shard means — single-endpoint-style throughput
            "mean_per_shard_input_tokens_per_second": _mean("input_tokens_per_second"),
            "mean_per_shard_output_tokens_per_second": _mean("output_tokens_per_second"),
            "mean_per_shard_total_tokens_per_second": _mean("total_tokens_per_second"),
            "mean_per_shard_requests_per_second": _mean("requests_per_second"),
        }

    def comparison_row(self, *, instance_type: str, concurrency: int) -> dict[str, Any]:
        """One-row summary for dropping alongside per-endpoint benchmark results.

        Produces a flat dict combining the per-container throughput (the
        mean-across-shards — apples-to-apples with a single-endpoint
        benchmark) and the aggregate fleet throughput (all containers
        summed). Useful for composite tables that mix Batch-run numbers
        with numbers from other benchmarks (e.g. LLMeter against a live
        endpoint on the same hardware).
        """
        agg = self.aggregate_throughput()
        return {
            "source": "batch",
            "instance_type": instance_type,
            "concurrency": concurrency,
            "n_replicas": len([r for r in self.per_shard_summary
                               if r.get("wall_clock_s") is not None]),
            "total_input_tokens": agg.get("total_input_tokens"),
            "total_output_tokens": agg.get("total_output_tokens"),
            # "per-endpoint" comparable figure: mean across shards
            "input_tokens_per_second": agg.get("mean_per_shard_input_tokens_per_second"),
            "output_tokens_per_second": agg.get("mean_per_shard_output_tokens_per_second"),
            "total_tokens_per_second": agg.get("mean_per_shard_total_tokens_per_second"),
            "requests_per_second": agg.get("mean_per_shard_requests_per_second"),
            # Aggregate fleet figure (parallel containers multiplying throughput)
            "fleet_total_tokens_per_second": agg.get("summed_total_tokens_per_second"),
        }

    def llmeter_comparable_stats(
        self,
        *,
        model_id: str | None = None,
        concurrency: int | None = None,
    ) -> dict[str, Any]:
        """Stats report matching the LLMeter ``stats.json`` schema.

        Walks the per-shard output JSONL files under ``self.output_dir``,
        treats each line as one LLMeter-style request, and computes
        per-request distributions (avg / p50 / p90 / p99) for latency,
        input tokens, and output tokens — plus summed throughput across
        the whole submission.

        Field names match LLMeter's ``stats.json`` so rows can be dropped
        in directly alongside LLMeter-driven benchmarks of the same
        model + hardware.
        """
        # Walk every per-shard JSONL and collect per-record fields.
        latencies_ms: list[float] = []
        input_tokens: list[int] = []
        output_tokens: list[int] = []
        errors = 0
        total = 0

        for shard_dir in sorted(self.output_dir.glob("shard-*")):
            for out_file in sorted(shard_dir.iterdir()):
                if out_file.name == "_summary.json":
                    continue
                if not out_file.is_file():
                    continue
                try:
                    body = out_file.read_text()
                except OSError:
                    continue
                for line in body.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    total += 1
                    if rec.get("error") is not None:
                        errors += 1
                        continue
                    lat = rec.get("latency_ms")
                    in_t = rec.get("input_tokens")
                    out_t = rec.get("output_tokens")
                    if isinstance(lat, (int, float)):
                        latencies_ms.append(float(lat))
                    if isinstance(in_t, int):
                        input_tokens.append(in_t)
                    if isinstance(out_t, int):
                        output_tokens.append(out_t)

        # Driver wall-clock: take the MAX wall_clock across shards (parallel)
        wall_clocks = [r.get("wall_clock_s") for r in self.per_shard_summary
                       if isinstance(r.get("wall_clock_s"), (int, float))]
        max_wall_clock_s = max(wall_clocks) if wall_clocks else None

        succeeded = total - errors
        fail_rate = errors / total if total else 0.0
        total_in = sum(input_tokens)
        total_out = sum(output_tokens)

        def _avg(xs: list[float]) -> float | None:
            return round(sum(xs) / len(xs), 4) if xs else None

        def _sec(ms: float | None) -> float | None:
            return round(ms / 1000.0, 4) if ms is not None else None

        # Per-request time_to_last_token in SECONDS (benchmark uses seconds)
        ttlt_avg_ms = _avg(latencies_ms)
        ttlt_p50_ms = _percentile(latencies_ms, 50)
        ttlt_p90_ms = _percentile(latencies_ms, 90)
        ttlt_p99_ms = _percentile(latencies_ms, 99)

        # Output TPS matching benchmark's definition: total_output_tokens / total_test_time
        output_tps = (
            round(total_out / max_wall_clock_s, 4)
            if max_wall_clock_s and max_wall_clock_s > 0 else None
        )
        reqs_per_min = (
            round(succeeded / max_wall_clock_s * 60, 4)
            if max_wall_clock_s and max_wall_clock_s > 0 else None
        )
        in_tpm = (
            round(total_in / max_wall_clock_s * 60, 4)
            if max_wall_clock_s and max_wall_clock_s > 0 else None
        )
        out_tpm = (
            round(total_out / max_wall_clock_s * 60, 4)
            if max_wall_clock_s and max_wall_clock_s > 0 else None
        )

        return {
            "source": "batch",
            "total_requests": total,
            "clients": concurrency,
            "n_requests": None,
            "total_test_time": max_wall_clock_s,
            "model_id": model_id,
            "failed_requests": errors,
            "failed_requests_rate": round(fail_rate, 4),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            # time_to_last_token family — in seconds, to match LLMeter
            "time_to_last_token-average": _sec(ttlt_avg_ms),
            "time_to_last_token-p50": _sec(ttlt_p50_ms),
            "time_to_last_token-p90": _sec(ttlt_p90_ms),
            "time_to_last_token-p99": _sec(ttlt_p99_ms),
            # Output token distribution
            "num_tokens_output-average": _avg([float(t) for t in output_tokens]),
            "num_tokens_output-p50": _percentile([float(t) for t in output_tokens], 50),
            "num_tokens_output-p90": _percentile([float(t) for t in output_tokens], 90),
            "num_tokens_output-p99": _percentile([float(t) for t in output_tokens], 99),
            # Input token distribution
            "num_tokens_input-average": _avg([float(t) for t in input_tokens]),
            "num_tokens_input-p50": _percentile([float(t) for t in input_tokens], 50),
            "num_tokens_input-p90": _percentile([float(t) for t in input_tokens], 90),
            "num_tokens_input-p99": _percentile([float(t) for t in input_tokens], 99),
            # Throughput
            "requests_per_minute": reqs_per_min,
            "average_input_tokens_per_minute": in_tpm,
            "average_output_tokens_per_minute": out_tpm,
            "output_tps": output_tps,
        }

    def real_world_wall_clock_stats(
        self, status_snapshot: StatusSnapshot,
    ) -> dict[str, Any]:
        """End-to-end wall-clock stats from Batch's own timestamps.

        This answers "how many tokens per second did I actually get, from
        the moment I clicked Submit to the moment the last job finished?"
        — in contrast to :meth:`llmeter_comparable_stats` which only
        counts the inference loop.

        Uses ``createdAt`` (min across shards) as the submission start
        and ``stoppedAt`` (max across shards) as the completion end.
        Both are set by Batch server-side; they survive Jupyter crashes
        because the measurement is done at report-generation time from
        ``DescribeJobs`` data, not from a live timer.

        Parameters
        ----------
        status_snapshot
            The final :class:`StatusSnapshot` from
            :func:`wait_for_completion` (or a fresh :func:`poll` call).

        Returns
        -------
        dict with ``submission_start_epoch_ms``, ``submission_end_epoch_ms``,
        ``total_real_wall_clock_s``, and derived real-world throughput
        (tokens / real-world-wall-clock) across the whole submission.
        """
        # Batch emits timestamps in epoch milliseconds.
        created_ats = [j.created_at for j in status_snapshot.jobs
                       if j.created_at is not None]
        stopped_ats = [j.stopped_at for j in status_snapshot.jobs
                       if j.stopped_at is not None]
        started_ats = [j.started_at for j in status_snapshot.jobs
                       if j.started_at is not None]

        if not created_ats or not stopped_ats:
            return {
                "mode": "incomplete",
                "shards_with_created_at": len(created_ats),
                "shards_with_stopped_at": len(stopped_ats),
                "shards_total": len(status_snapshot.jobs),
            }

        submission_start_ms = min(created_ats)
        submission_end_ms = max(stopped_ats)
        first_started_ms = min(started_ats) if started_ats else None
        wall_clock_s = (submission_end_ms - submission_start_ms) / 1000.0

        # Sum tokens across shards (parsed from _summary.json during download)
        total_in = sum(r.get("total_input_tokens", 0) or 0
                       for r in self.per_shard_summary)
        total_out = sum(r.get("total_output_tokens", 0) or 0
                        for r in self.per_shard_summary)
        succeeded = sum(r.get("succeeded", 0) or 0 for r in self.per_shard_summary)

        # Queue/startup overhead: time before any job started running.
        queue_overhead_s = (
            (first_started_ms - submission_start_ms) / 1000.0
            if first_started_ms is not None else None
        )

        # Per-shard billable instance-seconds (startedAt → stoppedAt).
        # This is what AWS Batch actually charges the EC2 instance for.
        billable_seconds = 0.0
        for j in status_snapshot.jobs:
            if j.started_at is not None and j.stopped_at is not None:
                billable_seconds += (j.stopped_at - j.started_at) / 1000.0

        def _rate(numer: float, denom: float) -> float | None:
            return round(numer / denom, 4) if denom > 0 else None

        return {
            "submission_start_epoch_ms": submission_start_ms,
            "submission_end_epoch_ms": submission_end_ms,
            "first_job_started_epoch_ms": first_started_ms,
            "total_real_wall_clock_s": round(wall_clock_s, 3),
            "queue_overhead_s": round(queue_overhead_s, 3) if queue_overhead_s is not None else None,
            "total_billable_instance_seconds": round(billable_seconds, 3),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "total_succeeded_requests": succeeded,
            # Real-world throughput: what you actually get end-to-end
            "real_world_input_tokens_per_second": _rate(total_in, wall_clock_s),
            "real_world_output_tokens_per_second": _rate(total_out, wall_clock_s),
            "real_world_total_tokens_per_second": _rate(
                total_in + total_out, wall_clock_s,
            ),
            "real_world_requests_per_second": _rate(succeeded, wall_clock_s),
        }

    def project_economics(
        self,
        status_snapshot: StatusSnapshot,
        cost_estimate: Any,
    ) -> dict[str, Any]:
        """Three-headline summary of a submission: cost, tokens, duration.

        This is the answer to "what did this project actually cost and
        how fast did it really go?" — combining the accurate
        per-instance cost (from :func:`estimate_cost`) with the full
        end-to-end duration (from ``createdAt``/``stoppedAt``) and
        the actual token counts (including output from failed-mid-way
        requests where vLLM still reported token usage).

        Parameters
        ----------
        status_snapshot
            Final :class:`StatusSnapshot` (for createdAt/stoppedAt).
        cost_estimate
            :class:`CostEstimate` from
            :func:`llm_batch_deploy.waiter.cost.estimate_cost`.

        Returns
        -------
        dict with:
        * ``total_cost_usd`` — actual AWS bill for the EC2 instances.
        * ``total_tokens`` — input + output across all jobs (incl. failed).
        * ``duration_s`` — wall clock from first SubmitJob to last job done.
        * ``usd_per_1m_tokens`` — economic efficiency.
        * ``real_world_tokens_per_second`` — end-to-end throughput,
          including queue wait, boot, vLLM warmup. This is what you'd
          quote for planning.
        * ``n_instances``, ``n_jobs``, ``n_unresolved_jobs`` — operational
          scale context.
        """
        # Duration: min createdAt → max stoppedAt, server-side timestamps.
        created_ats = [j.created_at for j in status_snapshot.jobs
                       if j.created_at is not None]
        stopped_ats = [j.stopped_at for j in status_snapshot.jobs
                       if j.stopped_at is not None]
        duration_s: float | None = None
        if created_ats and stopped_ats:
            duration_s = round(
                (max(stopped_ats) - min(created_ats)) / 1000.0, 3
            )

        # Tokens: use the collect report's per-shard summary (which already
        # aggregates ALL records that made it to the output JSONLs,
        # including ones that errored mid-request but had token counts).
        total_in = sum(r.get("total_input_tokens", 0) or 0
                       for r in self.per_shard_summary)
        total_out = sum(r.get("total_output_tokens", 0) or 0
                        for r in self.per_shard_summary)
        total_tokens = total_in + total_out

        # Cost: from cost_estimate (real AWS bill across instances)
        total_cost_usd = float(getattr(cost_estimate, "total_usd", 0.0))

        def _rate(numer: float, denom: float | None) -> float | None:
            if denom is None or denom <= 0:
                return None
            return round(numer / denom, 4)

        def _per_1m(cost: float, tokens: int) -> float | None:
            if tokens <= 0:
                return None
            return round(cost / tokens * 1_000_000, 4)

        return {
            # Headline three
            "total_cost_usd": round(total_cost_usd, 6),
            "total_tokens": total_tokens,
            "duration_s": duration_s,
            # Convenience splits
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            # Derived
            "usd_per_1m_tokens": _per_1m(total_cost_usd, total_tokens),
            "usd_per_1m_input_tokens": _per_1m(total_cost_usd, total_in),
            "usd_per_1m_output_tokens": _per_1m(total_cost_usd, total_out),
            "real_world_tokens_per_second": _rate(total_tokens, duration_s),
            "real_world_input_tokens_per_second": _rate(total_in, duration_s),
            "real_world_output_tokens_per_second": _rate(total_out, duration_s),
            # Operational context
            "n_instances": getattr(cost_estimate, "instance_count", 0),
            "n_jobs": len(status_snapshot.jobs),
            "n_unresolved_jobs": len(
                getattr(cost_estimate, "unresolved_job_ids", []) or []
            ),
            "region": getattr(cost_estimate, "region", None),
        }


def list_outputs(
    report: SubmissionReport,
    *,
    s3_client: Any | None = None,
    region: str = "us-west-2",
) -> dict[int, list[str]]:
    """For each shard, list the S3 URIs of output objects produced."""
    s3 = s3_client or boto3.client("s3", region_name=region)
    by_shard: dict[int, list[str]] = {}
    for shard in report.shards:
        # Output prefix URI is s3://bucket/outputs/<sid>/shard-NNNN/
        bucket, key_prefix = shard.output_prefix_s3_uri.replace("s3://", "").split("/", 1)
        paginator = s3.get_paginator("list_objects_v2")
        uris = []
        for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
            for obj in page.get("Contents", []):
                uris.append(f"s3://{bucket}/{obj['Key']}")
        by_shard[shard.shard_index] = uris
    return by_shard


def download_outputs(
    report: SubmissionReport,
    *,
    output_dir: Path,
    s3_client: Any | None = None,
    region: str = "us-west-2",
    include_summary_only: bool = False,
) -> CollectReport:
    """Download each shard's outputs under ``output_dir/shard-NNNN/``.

    Parameters
    ----------
    output_dir
        Local directory root. Created if missing.
    include_summary_only
        If True, only fetch ``_summary.json`` per shard (cheap). If False,
        download every output file.
    """
    s3 = s3_client or boto3.client("s3", region_name=region)
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    per_shard: list[dict[str, Any]] = []

    outputs_by_shard = list_outputs(report, s3_client=s3, region=region)

    for shard in report.shards:
        shard_dir = output_dir / f"shard-{shard.shard_index:04d}"
        shard_dir.mkdir(exist_ok=True)
        shard_uris = outputs_by_shard.get(shard.shard_index, [])

        summary_stats: dict[str, Any] = {"shard_index": shard.shard_index,
                                         "files_found": len(shard_uris)}
        for uri in shard_uris:
            filename = uri.rsplit("/", 1)[-1]
            if include_summary_only and filename != "_summary.json":
                continue
            local = shard_dir / filename
            bucket, key = uri.replace("s3://", "").split("/", 1)
            try:
                s3.download_file(Bucket=bucket, Key=key, Filename=str(local))
                downloaded.append(local)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Download failed for %s: %s", uri, exc)
                continue

            # If this is _summary.json, parse its stats into the per-shard row.
            if filename == "_summary.json":
                try:
                    summary_body = json.loads(local.read_text())
                    summary_stats.update(summary_body.get("stats", {}))
                    summary_stats["records_processed"] = \
                        summary_body.get("records_processed")
                except Exception:  # noqa: BLE001
                    pass

        per_shard.append(summary_stats)
        LOG.info("shard %d: %d files", shard.shard_index, len(shard_uris))

    return CollectReport(
        output_dir=output_dir,
        files_downloaded=downloaded,
        per_shard_summary=per_shard,
    )


def sample_outputs(
    report: SubmissionReport,
    *,
    n: int = 3,
    s3_client: Any | None = None,
    region: str = "us-west-2",
) -> list[dict[str, Any]]:
    """Grab the first ``n`` output records across all shards (cheap peek).

    Useful in the notebook for sanity-checking before downloading everything.
    """
    s3 = s3_client or boto3.client("s3", region_name=region)
    collected: list[dict[str, Any]] = []
    outputs_by_shard = list_outputs(report, s3_client=s3, region=region)

    for shard_idx in sorted(outputs_by_shard):
        if len(collected) >= n:
            break
        for uri in outputs_by_shard[shard_idx]:
            if len(collected) >= n:
                break
            # Skip summaries; we want actual records.
            if uri.endswith("_summary.json"):
                continue
            bucket, key = uri.replace("s3://", "").split("/", 1)
            try:
                resp = s3.get_object(Bucket=bucket, Key=key)
                body = resp["Body"].read().decode("utf-8")
                # Files are JSONL — take first N lines of the file, total
                # capped at ``n`` across all shards.
                for line in body.splitlines():
                    if len(collected) >= n:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        rec["_source_uri"] = uri
                        collected.append(rec)
                    except json.JSONDecodeError:
                        continue
            except Exception as exc:  # noqa: BLE001
                LOG.warning("sample: couldn't fetch %s: %s", uri, exc)
                continue
    return collected
