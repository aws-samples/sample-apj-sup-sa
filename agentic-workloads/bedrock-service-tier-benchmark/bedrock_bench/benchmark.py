"""Orchestrator: config -> registry -> preflight -> scheduler -> metrics -> report."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from .auth import AuthBroker
from .cells import Cell, expand_cells
from .config import BenchmarkConfig
from .metrics import CellSummary, Sample, summarize
from .preflight import format_report, preflight
from .registry import ModelSpec, select
from .report import RawWriter, write_all
from .scheduler import Scheduler

logger = logging.getLogger("bedrock_bench")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")


class Benchmark:
    """End-to-end benchmark runner."""

    def __init__(
        self,
        config: BenchmarkConfig,
        *,
        keys: tuple[str, ...] | None = None,
        interval_override: float | None = None,
    ):
        if not config.run_id:
            config = type(config)(**{**config.__dict__, "run_id": _make_run_id()})
        self.config = config
        self.broker = AuthBroker(profile=config.profile)
        self.specs: list[ModelSpec] = select(families=config.families, keys=keys)
        self.cells: list[Cell] = expand_cells(config, self.specs)
        self.interval_override = interval_override
        self.out_dir = Path(config.output_dir) / config.run_id

    # --- estimation --------------------------------------------------------
    def estimate(self) -> dict:
        """Rough wall-clock estimate and matrix shape."""
        interval = self.interval_override
        if interval is None:
            interval = self.config.interval_seconds
        domains: dict[str, int] = {}
        for c in self.cells:
            domains[c.domain] = domains.get(c.domain, 0) + 1
        # Per domain wall-clock = (#cells_in_domain * n - 1) * interval.
        per_domain = [(cnt * self.config.n_requests - 1) * interval for cnt in domains.values()]
        return {
            "models": len(self.specs),
            "cells": len(self.cells),
            "domains": len(domains),
            "n_per_cell": self.config.n_requests,
            "interval_s": interval,
            "est_seconds_if_fully_parallel": max(per_domain, default=0),
            "total_requests": sum(domains.values()) * self.config.n_requests,
        }

    # --- phases ------------------------------------------------------------
    async def run_preflight(self) -> list[Cell]:
        logger.info("Preflight: probing %d cells ...", len(self.cells))
        kept, results = await preflight(self.cells, self.broker, self.config)
        print(format_report(results))
        return kept

    async def run(self, *, skip_preflight: bool = False) -> list[CellSummary]:
        meta = {
            "account_id": self.broker.account_id(),
            "started": _utc_now(),
            "version": __import__("bedrock_bench").__version__,
        }
        self.out_dir.mkdir(parents=True, exist_ok=True)

        cells = self.cells if skip_preflight else await self.run_preflight()
        if not cells:
            logger.warning("No cells passed preflight; nothing to run.")
            meta["finished"] = _utc_now()
            self._write_reports([], meta)
            return []

        with RawWriter(self.out_dir / "raw.jsonl") as raw:

            def on_sample(label: str, sample: Sample, done: int, total: int) -> None:
                raw.write(label, _sample_record(sample))
                status = "ok" if sample.error is None else "ERR"
                logger.info(
                    "[%s] %d/%d %s ttft=%s total=%s tier=%s",
                    label,
                    done,
                    total,
                    status,
                    _r(sample.ttft),
                    _r(sample.total),
                    sample.served_tier,
                )

            scheduler = Scheduler(
                self.config,
                self.broker,
                on_sample=on_sample,
                interval_override=self.interval_override,
            )
            samples = await scheduler.run(cells)

        meta["finished"] = _utc_now()
        summaries = self._summarize(cells, samples)
        self._write_reports(summaries, meta)
        return summaries

    # --- helpers -----------------------------------------------------------
    def _summarize(self, cells: list[Cell], samples: dict[str, list[Sample]]) -> list[CellSummary]:
        by_label = {c.label: c for c in cells}
        out: list[CellSummary] = []
        for label, cell in by_label.items():
            out.append(
                summarize(
                    label=label,
                    family=cell.spec.family,
                    model_key=cell.spec.key,
                    display_name=cell.spec.display_name,
                    transport=cell.transport.value,
                    tier=cell.tier.value,
                    region=cell.region,
                    model_id=cell.model_id,
                    requested=self.config.n_requests,
                    samples=samples.get(label, []),
                )
            )
        return out

    def _write_reports(self, summaries: list[CellSummary], meta: dict) -> None:
        paths = write_all(self.out_dir, self.config, meta, summaries)
        logger.info("Wrote reports: %s", {k: str(v) for k, v in paths.items()})
        print(f"\nReports written to {self.out_dir}/")
        for k, v in paths.items():
            print(f"  {k}: {v}")


def _sample_record(s: Sample) -> dict:
    return {
        "request_time": s.request_time,
        "ttft": s.ttft,
        "total": s.total,
        "served_tier": s.served_tier,
        "num_tokens_output": s.num_tokens_output,
        "error": s.error,
    }


def _r(v: float | None) -> str:
    return "—" if v is None else f"{v:.3f}"


def run_sync(config: BenchmarkConfig, **kwargs) -> list[CellSummary]:
    """Convenience blocking entry point."""
    bench = Benchmark(
        config, **{k: v for k, v in kwargs.items() if k in ("keys", "interval_override")}
    )
    return asyncio.run(bench.run(skip_preflight=kwargs.get("skip_preflight", False)))
