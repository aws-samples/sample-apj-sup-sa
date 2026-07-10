"""Preflight: probe every cell once before committing to the ~1h run.

A single live invocation per cell catches the failure modes that would otherwise
waste 30+ minutes producing an all-errors column:

* flex requested on a model that doesn't support it -> ``ValidationException``
* wrong Mantle model id -> 404 ``not_found_error``
* missing Mantle IAM permission -> 401/403 ``access_denied``
* region mismatch / model not enabled

Cells that succeed are kept; cells that fail are dropped from the run (and the
reason is reported). The probe also serves as a connection warm-up.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .auth import AuthBroker
from .cells import Cell, build_endpoint, build_payload
from .config import BenchmarkConfig

logger = logging.getLogger("bedrock_bench.preflight")


@dataclass
class ProbeResult:
    cell: Cell
    ok: bool
    served_tier: str | None
    ttft: float | None
    error: str | None

    @property
    def tier_mismatch(self) -> bool:
        """True if we requested a non-default tier but Bedrock served something else."""
        if not self.ok or self.cell.tier.is_default:
            return False
        return (self.served_tier or "").lower() != self.cell.tier.value


async def _probe(cell: Cell, broker: AuthBroker, config: BenchmarkConfig) -> ProbeResult:
    endpoint = build_endpoint(cell, broker)
    payload = build_payload(cell, config)
    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(endpoint.invoke, payload),
            timeout=config.timeout_seconds,
        )
    except Exception as e:
        return ProbeResult(cell, False, None, None, f"{type(e).__name__}: {e}")

    if resp.error:
        return ProbeResult(cell, False, getattr(resp, "served_tier", None), None, resp.error)
    return ProbeResult(
        cell,
        True,
        getattr(resp, "served_tier", None),
        resp.time_to_first_token,
        None,
    )


async def preflight(
    cells: list[Cell],
    broker: AuthBroker,
    config: BenchmarkConfig,
    *,
    max_concurrent: int = 24,
) -> tuple[list[Cell], list[ProbeResult]]:
    """Probe all cells concurrently; return ``(kept_cells, all_results)``.

    Probes can all fire at once: it's a single request per cell, well under any
    per-minute limit for distinct models. (If many cells share one model id, the
    semaphore still bounds total concurrency.)
    """
    sem = asyncio.Semaphore(max_concurrent)

    async def guarded(cell: Cell) -> ProbeResult:
        async with sem:
            return await _probe(cell, broker, config)

    results = await asyncio.gather(*(guarded(c) for c in cells))
    kept = [r.cell for r in results if r.ok]
    return kept, list(results)


def format_report(results: list[ProbeResult]) -> str:
    """Human-readable go/no-go matrix."""
    lines = ["Preflight probe results:", ""]
    width = max((len(r.cell.label) for r in results), default=10)
    for r in sorted(results, key=lambda x: x.cell.label):
        if r.ok:
            tier = r.served_tier or "—"
            flag = f"  ⚠ served≠{r.cell.tier.value}" if r.tier_mismatch else ""
            status = f"OK   served={tier:<8} ttft={r.ttft and round(r.ttft, 2)}{flag}"
        else:
            status = f"DROP {(r.error or 'unknown error')[:90]}"
        lines.append(f"  {r.cell.label:<{width}}  {status}")
    ok = sum(1 for r in results if r.ok)
    lines += ["", f"{ok}/{len(results)} cells passed preflight."]
    return "\n".join(lines)
