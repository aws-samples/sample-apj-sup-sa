"""Orchestrator: registry → cells → (preflight) → runner → summaries → reports."""

from __future__ import annotations

import logging
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .apis.base import Measurement, Request
from .auth import AuthBroker
from .cells import Cell, expand_cells
from .config import BenchmarkConfig, Tier
from .metrics import CellSummary, summarize
from .registry import ModelSpec, select
from .report import (
    RawWriter,
    compare,
    redact_meta,
    redact_text,
    write_markdown,
    write_summary_csv,
    write_summary_json,
)
from .runner import Runner, build_adapter, served_matches

logger = logging.getLogger("bedrock_bench")

_PREFLIGHT_DOC = "Preflight check."
_PREFLIGHT_Q = "Reply with the single word: ok"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def estimate(config: BenchmarkConfig, cells: list[Cell]) -> dict[str, Any]:
    """Matrix shape, request count, token volume and wall-clock estimate (no AWS calls)."""

    def rounds(c: Cell) -> int:
        # Mirrors Runner: warm contexts always get at least one priming round.
        return config.n_requests + max(config.warmup_requests, 1 if c.cache.is_warm else 0)

    domains: dict[str, int] = {}
    for c in cells:
        domains[c.domain] = domains.get(c.domain, 0) + rounds(c)
    per_domain = [n * config.interval_seconds for n in domains.values()]
    total = sum(rounds(c) for c in cells)
    in_tokens = sum(c.size.input_tokens * rounds(c) for c in cells)
    out_tokens = sum(config.output_cap(c.size) * rounds(c) for c in cells)
    return {
        "models": len({c.spec.key for c in cells}),
        "contexts": len({c.context_key for c in cells}),
        "cells": len(cells),
        "domains": len(domains),
        "requests": total,
        "approx_input_tokens": in_tokens,
        "max_output_tokens": out_tokens,
        "est_wall_clock_s": max(per_domain, default=0.0),
    }


class Benchmark:
    """End-to-end benchmark run."""

    def __init__(
        self,
        config: BenchmarkConfig,
        *,
        keys: tuple[str, ...] | None = None,
        registry_path: Path | None = None,
        reasoning_effort: str | None = "none",
        broker: AuthBroker | None = None,
    ):
        if not config.run_id:
            run_id = datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")
            config = replace(config, run_id=run_id)
        self.config = config
        self.reasoning_effort = reasoning_effort
        self.specs: list[ModelSpec] = [
            s for s in select(config.families, keys, registry_path) if s.multi_tier
        ]
        self.cells: list[Cell] = expand_cells(config, self.specs)
        self._broker = broker
        self.out_dir = Path(config.output_dir) / config.run_id

    @property
    def broker(self) -> AuthBroker:
        if self._broker is None:
            longest = max(self.config.timeout_for(t) for t in Tier)
            self._broker = AuthBroker(profile=self.config.profile, read_timeout=longest + 30)
        return self._broker

    def estimate(self) -> dict[str, Any]:
        return estimate(self.config, self.cells)

    # ------------------------------------------------------------------ preflight
    def preflight(self) -> tuple[list[Cell], list[dict[str, Any]]]:
        """One tiny request per cell; drop cells that fail (wrong id, no access, tier refused)."""
        kept: list[Cell] = []
        report: list[dict[str, Any]] = []
        for cell in self.cells:
            try:
                m = build_adapter(cell, self.broker).send(
                    Request(
                        model_id=cell.model_id,
                        region=cell.region,
                        document=_PREFLIGHT_DOC,
                        question=_PREFLIGHT_Q,
                        max_tokens=32,
                        tier=None if cell.tier is Tier.DEFAULT else cell.tier.value,
                        temperature=None,
                        timeout=self.config.timeout_for(cell.tier),
                        reasoning_effort=self.reasoning_effort if cell.spec.reasoning else None,
                    )
                )
            except Exception as e:  # noqa: BLE001 - a failing cell is dropped, not fatal
                m = Measurement(error=f"{type(e).__name__}: {str(e)[:300]}", error_kind="client")
            ok = m.error is None
            if ok:
                match = served_matches(cell.tier, m.served_tier)
                if match is False or (match is None and cell.tier is not Tier.DEFAULT):
                    ok = False
                    m.error_kind = "tier_mismatch" if match is False else "tier_unreported"
                    m.error = f"requested {cell.tier.value}, served {m.served_tier!r}"
            report.append(
                {
                    "cell": cell.label,
                    "ok": ok,
                    "served_tier": m.served_tier,
                    "error_kind": m.error_kind,
                    "error": m.error,
                }
            )
            if ok:
                kept.append(cell)
        # A context is useful only if default and at least one other tier survived.
        by_ctx: dict[str, set[Tier]] = {}
        for c in kept:
            by_ctx.setdefault(c.context_key, set()).add(c.tier)
        kept = [c for c in kept if _comparable(by_ctx[c.context_key])]
        return kept, report

    # ------------------------------------------------------------------ run
    def run(self, *, skip_preflight: bool = False) -> list[CellSummary]:
        cfg = self.config
        self.out_dir.mkdir(parents=True, exist_ok=True)
        meta: dict[str, Any] = {
            "run_id": cfg.run_id,
            "started": _utc_now(),
            "version": __version__,
            "account_id": self.broker.account_id(),
            "profile": cfg.profile,
            "reasoning_effort": self.reasoning_effort,
        }
        cells = self.cells
        if not skip_preflight:
            cells, pre = self.preflight()
            meta["preflight"] = pre
        raw_path = self.out_dir / "raw.jsonl"
        with RawWriter(raw_path) as raw:

            def on_sample(cell: Cell, rec: dict[str, Any], done: int, total: int) -> None:
                if cfg.redact:
                    rec = {**rec, "error": redact_text(rec.get("error"))}
                raw.write(rec)
                logger.info(
                    "[%s] %d/%d %s ttft=%s e2e=%s tier=%s",
                    cell.label,
                    done,
                    total,
                    "ok" if rec.get("included") else (rec.get("exclude_reason") or "ERR"),
                    _r(rec.get("ttft")),
                    _r(rec.get("e2e")),
                    rec.get("served_tier"),
                )

            runner = Runner(
                cfg, self.broker, on_sample=on_sample, reasoning_effort=self.reasoning_effort
            )
            try:
                samples = runner.run(cells)
            finally:
                self.broker.close()
        meta["finished"] = _utc_now()
        summaries = [
            summarize({**c.dims(), "label": c.label}, samples.get(c.label, [])) for c in cells
        ]
        self.write_reports(meta, summaries, samples)
        return summaries

    def write_reports(
        self,
        meta: dict[str, Any],
        summaries: list[CellSummary],
        samples: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Path]:
        from .html_report import write_html

        if self.config.redact:
            meta = redact_meta(meta)
        comparisons = compare(summaries, samples, seed=self.config.seed)
        config_out = {k: _plain(v) for k, v in asdict(self.config).items() if k != "profile"}
        if self.config.redact:
            # The output path can contain the local user name.
            config_out["output_dir"] = "redacted"
        paths = {
            "summary_json": self.out_dir / "summary.json",
            "summary_csv": self.out_dir / "summary.csv",
            "report_md": self.out_dir / "report.md",
            "report_html": self.out_dir / "report.html",
            "raw_jsonl": self.out_dir / "raw.jsonl",
        }
        write_summary_json(paths["summary_json"], meta, config_out, summaries, comparisons)
        write_summary_csv(paths["summary_csv"], summaries)
        write_markdown(paths["report_md"], meta, summaries, comparisons)
        write_html(paths["summary_json"], paths["report_html"])
        return paths


def _comparable(tiers: set[Tier]) -> bool:
    return Tier.DEFAULT in tiers and len(tiers) > 1


def _plain(v: Any) -> Any:
    if isinstance(v, tuple):
        return [_plain(x) for x in v]
    return getattr(v, "value", v)


def _r(v: Any) -> str:
    return "—" if v is None else f"{v:.3f}"
