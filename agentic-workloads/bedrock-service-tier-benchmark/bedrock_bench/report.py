"""Persist raw samples and write summaries (JSON, CSV, Markdown).

Outputs under ``<output_dir>/<run_id>/``:

* ``raw.jsonl``    — one line per measured request, written live.
* ``summary.json`` — run metadata, per-cell summaries and tier comparisons.
  This is the interchange format the HTML report and the web app's loader read.
* ``summary.csv``  — one row per cell.
* ``report.md``    — the tier comparison per context.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from .metrics import METRICS, CellSummary, bootstrap_delta

SUMMARY_SCHEMA_VERSION = 2
_HEADLINE = ("ttft", "ttfat", "e2e", "itl", "output_tps")
_CONTEXT_DIMS = (
    "model",
    "endpoint",
    "api",
    "scope",
    "region",
    "prompt_size",
    "cache",
)


def atomic_write(path: Path, data: str) -> None:
    """Write via temp file + ``os.replace`` so a crash never leaves a truncated report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


class RawWriter:
    """Thread-safe, line-buffered JSONL writer for live sample capture."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", buffering=1, encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str)
        with self._lock:
            self._fh.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            self._fh.close()

    def __enter__(self) -> RawWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def context_key(dims: dict[str, Any]) -> str:
    return "|".join(str(dims.get(k)) for k in _CONTEXT_DIMS)


def compare(
    summaries: list[CellSummary],
    samples: dict[str, list[dict[str, Any]]],
    *,
    seed: int,
) -> list[dict[str, Any]]:
    """Tier-vs-default deltas for every context that has a default cell."""
    groups: dict[str, dict[str, CellSummary]] = defaultdict(dict)
    labels: dict[tuple[str, str], str] = {}
    for s in summaries:
        ck = context_key(s.cell)
        groups[ck][s.cell["tier"]] = s
        labels[(ck, s.cell["tier"])] = s.cell["label"]
    out: list[dict[str, Any]] = []
    for ck, by_tier in groups.items():
        if "default" not in by_tier:
            continue
        base = [r for r in samples.get(labels[(ck, "default")], []) if r.get("included")]
        for tier, s in by_tier.items():
            if tier == "default":
                continue
            other = [r for r in samples.get(labels[(ck, tier)], []) if r.get("included")]
            deltas = {
                m: bootstrap_delta(
                    [r.get(m) for r in base], [r.get(m) for r in other], metric=m, seed=seed
                ).__dict__
                for m in _HEADLINE
            }
            out.append(
                {
                    "context": {k: by_tier["default"].cell.get(k) for k in _CONTEXT_DIMS},
                    "display_name": s.cell.get("display_name"),
                    "family": s.cell.get("family"),
                    "tier": tier,
                    "deltas": deltas,
                }
            )
    return out


def write_summary_json(
    path: Path,
    meta: dict[str, Any],
    config: dict[str, Any],
    summaries: list[CellSummary],
    comparisons: list[dict[str, Any]],
) -> None:
    payload = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "meta": meta,
        "config": config,
        "cells": [s.to_dict() for s in summaries],
        "comparisons": comparisons,
    }
    atomic_write(path, json.dumps(payload, indent=2, default=str))


def write_summary_csv(path: Path, summaries: list[CellSummary]) -> None:
    dims = ("family", "display_name", "model_id", *_CONTEXT_DIMS, "tier")
    stat_cols = [f"{m}_{p}" for m in METRICS for p in ("n", "p50", "p90", "p95", "p99", "mean")]
    cols = [
        *dims,
        "requested",
        "succeeded",
        "failed",
        "excluded",
        "cache_hit_rate",
        "mean_input_tokens",
        "mean_output_tokens",
        *stat_cols,
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for s in summaries:
        row: list[Any] = [s.cell.get(d) for d in dims]
        row += [
            s.requested,
            s.succeeded,
            s.failed,
            ";".join(f"{k}={v}" for k, v in s.excluded.items()),
            s.cache_hit_rate,
            s.tokens.get("input_tokens"),
            s.tokens.get("output_tokens"),
        ]
        for m in METRICS:
            st = s.stats.get(m)
            row += [getattr(st, a, None) for a in ("n", "p50", "p90", "p95", "p99", "mean")]
        w.writerow(row)
    atomic_write(path, buf.getvalue())


def _ms(v: float | None) -> str:
    return "—" if v is None else f"{v * 1000:.0f}"


def _delta_txt(d: dict[str, Any], scale: float = 1000.0, unit: str = "ms") -> str:
    if d.get("delta") is None:
        return "—"
    sig = "" if d.get("significant") else " (n.s.)"
    pct = f" {d['pct']:+.0f}%" if d.get("pct") is not None else ""
    return f"{d['delta'] * scale:+.0f} {unit}{pct}{sig}"


def write_markdown(
    path: Path,
    meta: dict[str, Any],
    summaries: list[CellSummary],
    comparisons: list[dict[str, Any]],
) -> None:
    lines = [
        "# Amazon Bedrock service-tier benchmark",
        "",
        f"Run `{meta.get('run_id')}` · {meta.get('started')} → {meta.get('finished')} · "
        f"version {meta.get('version')}",
        "",
        "Δ = tier p50 − default p50 in the same context. (n.s.) = the 95% bootstrap "
        "confidence interval includes 0. Lower is better for TTFT/TTFAT/E2E/ITL.",
        "",
        "| Model | Endpoint | API | Scope | Region | Size | Cache | Tier | ΔTTFT | ΔTTFAT | ΔE2E | ΔITL |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in sorted(comparisons, key=lambda c: (c["family"] or "", c["display_name"] or "")):
        ctx, d = c["context"], c["deltas"]
        lines.append(
            f"| {c['display_name']} | {ctx['endpoint']} | {ctx['api']} | {ctx['scope']} | "
            f"{ctx['region']} | {ctx['prompt_size']} | {ctx['cache']} | {c['tier']} | "
            f"{_delta_txt(d['ttft'])} | {_delta_txt(d['ttfat'])} | {_delta_txt(d['e2e'])} | "
            f"{_delta_txt(d['itl'])} |"
        )
    lines += [
        "",
        "## Cells",
        "",
        "| Cell | n | TTFT p50 | TTFT p90 | E2E p50 | E2E p90 | ITL p50 | Cache hit | Excluded | Errors |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        st = s.stats
        hit = "—" if s.cache_hit_rate is None else f"{s.cache_hit_rate:.0%}"
        lines.append(
            f"| {s.cell['label'].replace('|', ' / ')} | {st['e2e'].n} | {_ms(st['ttft'].p50)} | {_ms(st['ttft'].p90)} | "
            f"{_ms(st['e2e'].p50)} | {_ms(st['e2e'].p90)} | {_ms(st['itl'].p50)} | {hit} | "
            f"{dict(s.excluded) or ''} | {dict(s.errors) or ''} |"
        )
    atomic_write(path, "\n".join(lines) + "\n")


_ARN_RE = re.compile(r"arn:aws[\w-]*:[^\s\"']*")
_ACCOUNT_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")


def redact_text(text: str | None) -> str | None:
    """Mask ARNs and 12-digit account ids inside free text (e.g. error messages)."""
    if not text:
        return text
    return _ACCOUNT_RE.sub("<account>", _ARN_RE.sub("<arn>", text))


def redact_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Mask account-identifying metadata for externally shared reports."""
    out = dict(meta)
    if isinstance(out.get("preflight"), list):
        out["preflight"] = [{**p, "error": redact_text(p.get("error"))} for p in out["preflight"]]
    acct = str(out.get("account_id") or "")
    out["account_id"] = ("*" * 8 + acct[-4:]) if len(acct) >= 4 else "redacted"
    out.pop("profile", None)
    return out
