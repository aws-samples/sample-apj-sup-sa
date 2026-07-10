"""Persist raw samples and render summaries (JSON / CSV / Markdown).

Outputs (under ``<output_dir>/<run_id>/``):

* ``raw.jsonl``      — one line per invocation, written live during the run.
* ``summary.json``   — per-cell percentile summaries + run metadata.
* ``summary.csv``    — flat table, one row per cell.
* ``report.md``      — the headline comparison of flex and priority against
                       default, grouped by family + transport, for TTFT and
                       total latency.

All outputs report the p20/p50/p90 percentiles plus the Δp50 deltas of flex and
priority versus default.
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
from dataclasses import replace
from pathlib import Path

from .config import BenchmarkConfig
from .metrics import CellSummary, MetricStats

#: Percentiles reported everywhere (CSV columns, and the comparison views).
_PCTS = ("p20", "p50", "p90")

#: The non-default tiers, each compared against default. Order = display order.
_COMPARE_TIERS = ("flex", "priority")


def _atomic_write(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` atomically (temp file + ``os.replace``).

    A crash mid-write then leaves either the old file or nothing — never a
    truncated/corrupt report.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(data)
    os.replace(tmp, path)


class RawWriter:
    """Thread-safe, line-buffered JSONL writer for live sample capture.

    Usable as a context manager::

        with RawWriter(path) as raw:
            raw.write(label, record)
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: io.TextIOWrapper = path.open("a", buffering=1)  # line-buffered
        self._lock = threading.Lock()

    def write(self, cell_label: str, record: dict) -> None:
        """Append one ``{cell, **record}`` line as JSON (thread-safe)."""
        row = {"cell": cell_label, **record}
        line = json.dumps(row, default=str)
        with self._lock:
            self._fh.write(line + "\n")

    def close(self) -> None:
        """Close the underlying file handle."""
        with self._lock:
            self._fh.close()

    def __enter__(self) -> RawWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _fmt(v: float | None, scale: float = 1000.0, nd: int = 0) -> str:
    """Format seconds as milliseconds for display; '—' for missing."""
    if v is None:
        return "—"
    return f"{v * scale:.{nd}f}"


def write_summary_json(
    path: Path, config: BenchmarkConfig, meta: dict, summaries: list[CellSummary]
) -> None:
    payload = {
        "run_id": config.run_id,
        "meta": meta,
        "config": {
            "profile": config.profile,
            "regions": list(config.regions),
            "n_requests": config.n_requests,
            "interval_seconds": config.interval_seconds,
            "max_tokens": config.max_tokens,
            "prompt": config.prompt,
        },
        "cells": [s.to_dict() for s in summaries],
    }
    _atomic_write(path, json.dumps(payload, indent=2, default=str))


def write_summary_csv(path: Path, summaries: list[CellSummary]) -> None:
    cols = [
        "family",
        "display_name",
        "transport",
        "tier",
        "region",
        "model_id",
        "requested",
        "succeeded",
        "failed",
        "ttft_p20",
        "ttft_p50",
        "ttft_p90",
        "ttft_mean",
        "total_p20",
        "total_p50",
        "total_p90",
        "total_mean",
        "served_tiers",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for s in summaries:
        t, tot = s.ttft, s.total_latency
        w.writerow(
            [
                s.family,
                s.display_name,
                s.transport,
                s.tier,
                s.region,
                s.model_id,
                s.requested,
                s.succeeded,
                s.failed,
                *(getattr(t, p, None) if t else None for p in _PCTS),
                t.mean if t else None,
                *(getattr(tot, p, None) if tot else None for p in _PCTS),
                tot.mean if tot else None,
                ";".join(f"{k}={v}" for k, v in s.served_tiers.items()),
            ]
        )
    _atomic_write(path, buf.getvalue())


def _pair(summaries: list[CellSummary]) -> dict[tuple[str, str, str], dict[str, CellSummary]]:
    """Index summaries by (family, model_key, transport) -> {tier: summary}."""
    out: dict[tuple[str, str, str], dict[str, CellSummary]] = {}
    for s in summaries:
        out.setdefault((s.family, s.model_key, s.transport), {})[s.tier] = s
    return out


def _delta(default: float | None, flex: float | None) -> str:
    """flex vs default as a signed percentage (negative = flex faster)."""
    if default is None or flex is None or default == 0:
        return "—"
    pct = (flex - default) / default * 100.0
    return f"{pct:+.0f}%"


def _metric_for(summary: CellSummary | None, metric: str) -> MetricStats | None:
    if summary is None:
        return None
    return summary.ttft if metric == "ttft" else summary.total_latency


def _metric_row(
    name: str,
    default: CellSummary | None,
    by_tier: dict[str, CellSummary],
    metric: str,
) -> list[str]:
    """One table row: default p20/p50/p90, then each compare-tier's percentiles + Δp50."""

    def cell(stats: MetricStats | None, attr: str) -> str:
        return _fmt(getattr(stats, attr, None)) if stats else "NA"

    d = _metric_for(default, metric)
    row = [name]
    for p in _PCTS:
        row.append(cell(d, p))
    for tier in _COMPARE_TIERS:
        t = _metric_for(by_tier.get(tier), metric)
        for p in _PCTS:
            row.append(cell(t, p))
        dv = getattr(d, "p50", None) if d else None
        tv = getattr(t, "p50", None) if t else None
        row.append(_delta(dv, tv))
    return row


def write_markdown(
    path: Path, config: BenchmarkConfig, meta: dict, summaries: list[CellSummary]
) -> None:
    lines: list[str] = []
    lines.append("# Bedrock Service-Tier Latency Benchmark — flex &amp; priority vs default")
    lines.append("")
    lines.append(f"- **Run:** `{config.run_id}`")
    lines.append(f"- **Account:** {meta.get('account_id', '?')}  ")
    lines.append(
        f"- **Profile:** `{config.profile}`  •  **Regions (pref):** {', '.join(config.regions)}"
    )
    lines.append(
        f"- **Samples/cell (n):** {config.n_requests}  •  **Interval:** {config.interval_seconds:.0f}s  •  **max_tokens:** {config.max_tokens}"
    )
    lines.append(
        f"- **Started:** {meta.get('started', '?')}  •  **Finished:** {meta.get('finished', '?')}"
    )
    lines.append("")
    lines.append(
        "All latencies in **milliseconds**. Each row shows **default p20/p50/p90**, then "
        "**flex** and **priority** p20/p50/p90, each with **Δp50** vs default "
        "(negative ⇒ that tier is faster). `NA` = the model does not serve that tier on "
        "this transport."
    )
    lines.append("")

    paired = _pair(summaries)
    order = {s.model_key: i for i, s in enumerate(summaries)}
    keys = sorted(paired.keys(), key=lambda k: (k[0], order.get(k[1], 999), k[2]))

    last_family = None
    for family, model_key, transport in keys:
        if family != last_family:
            lines.append(f"## {family}")
            lines.append("")
            last_family = family
        by_tier = paired[(family, model_key, transport)]
        default = by_tier.get("default")
        any_s = default or next(iter(by_tier.values()), None)
        if any_s is None:
            continue
        lines.append(
            f"### {any_s.display_name} — `{transport}` ({any_s.region}, `{any_s.model_id}`)"
        )
        # Provenance line for every tier actually run.
        prov = []
        for label in ("default", *_COMPARE_TIERS):
            s = by_tier.get(label)
            if not s:
                continue
            served = ", ".join(f"{k}:{v}" for k, v in s.served_tiers.items()) or "—"
            entry = f"{label}: {s.succeeded}/{s.requested} ok, served[{served}]"
            if s.failed:
                errs = ", ".join(f"{k}×{v}" for k, v in s.errors.items())
                entry += f", {s.failed} fail ({errs})"
            prov.append(entry)
        lines.append("  •  ".join(prov))
        lines.append("")
        # Header: default block + (flex block + Δ) + (priority block + Δ).
        head = ["metric", "d·p20", "d·p50", "d·p90"]
        for tier in _COMPARE_TIERS:
            ab = tier[0]  # f / p
            head += [f"{ab}·p20", f"{ab}·p50", f"{ab}·p90", f"Δp50({tier})"]
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + "---|" * len(head))
        lines.append("| " + " | ".join(_metric_row("TTFT", default, by_tier, "ttft")) + " |")
        lines.append(
            "| " + " | ".join(_metric_row("Total", default, by_tier, "total_latency")) + " |"
        )
        lines.append("")

    _atomic_write(path, "\n".join(lines))


def _redacted(config: BenchmarkConfig, meta: dict) -> tuple[BenchmarkConfig, dict]:
    """Return ``(config, meta)`` with account-identifying fields masked.

    Used when ``config.redact`` is set so reports are safe to share externally:
    the AWS account id is masked to its last 4 digits and the profile is dropped.
    """
    if not config.redact:
        return config, meta
    masked = dict(meta)
    acct = str(meta.get("account_id", ""))
    masked["account_id"] = ("•" * 8 + acct[-4:]) if len(acct) >= 4 else "redacted"
    return replace(config, profile=None), masked


def write_all(
    out_dir: Path, config: BenchmarkConfig, meta: dict, summaries: list[CellSummary]
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    config, meta = _redacted(config, meta)
    paths = {
        "summary_json": out_dir / "summary.json",
        "summary_csv": out_dir / "summary.csv",
        "report_md": out_dir / "report.md",
        "report_html": out_dir / "report.html",
    }
    write_summary_json(paths["summary_json"], config, meta, summaries)
    write_summary_csv(paths["summary_csv"], summaries)
    write_markdown(paths["report_md"], config, meta, summaries)
    # Friendly HTML report (built from the structured summary we just wrote).
    from .html_report import write_html

    write_html(paths["summary_json"], paths["report_html"])
    return paths
