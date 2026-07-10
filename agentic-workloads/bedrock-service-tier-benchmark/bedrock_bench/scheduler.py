"""The rate-paced execution engine.

Requirements that shape the design:

* **1 request / model / minute** to avoid RPM/TPM/TPD limits. We enforce this
  *per pacing domain*, where a domain = (transport, model, region). Within a
  domain, requests run strictly serially with ``interval_seconds`` between
  *starts*; the default and flex tiers are **interleaved** so a model's two
  tiers share the cadence (default, flex, default, flex, ...).
* **Parallelism across models**, not within. Each domain is an independent
  asyncio task; many domains run at once (bounded by a semaphore) so the whole
  matrix finishes in ~``n * interval`` wall-clock (~30 min for n=30) regardless
  of how many models there are.

llmeter endpoints are synchronous (boto3 / openai SDK), so each ``invoke`` runs
in a worker thread via ``asyncio.to_thread``; the per-request timeout is enforced
with ``asyncio.wait_for``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .auth import AuthBroker
from .cells import Cell, build_endpoint, build_payload
from .config import BenchmarkConfig
from .metrics import Sample

logger = logging.getLogger("bedrock_bench.scheduler")

# Progress callback: (cell_label, sample, completed_in_domain, total_in_domain).
ProgressCb = Callable[[str, Sample, int, int], None]


def _now_iso() -> str:
    # Local import keeps module import cheap and avoids a hard datetime dep up top.
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class Scheduler:
    """Runs the benchmark matrix with per-domain pacing and cross-domain parallelism."""

    def __init__(
        self,
        config: BenchmarkConfig,
        broker: AuthBroker,
        *,
        on_sample: ProgressCb | None = None,
        max_concurrent_domains: int | None = None,
        interval_override: float | None = None,
    ):
        self.config = config
        self.broker = broker
        self.on_sample = on_sample
        self.interval = (
            interval_override if interval_override is not None else config.interval_seconds
        )
        # None => run *all* domains concurrently (sized at run() time). Each
        # domain is almost entirely idle — one short request per interval — so
        # the per-domain pacing, not a concurrency cap, is what protects against
        # throttling. A cap would needlessly stretch wall-clock when there are
        # many models. An explicit int still bounds thread/connection churn.
        self._max_concurrent_domains = max_concurrent_domains
        # Collected samples keyed by cell label.
        self.samples: dict[str, list[Sample]] = defaultdict(list)

    async def run(self, cells: list[Cell]) -> dict[str, list[Sample]]:
        """Execute every cell; return samples keyed by ``cell.label``.

        Cells sharing a pacing domain (same model+transport+region, differing
        tier) are grouped and run by a single domain worker that interleaves them.
        """
        domains: dict[str, list[Cell]] = defaultdict(list)
        for cell in cells:
            domains[cell.domain].append(cell)

        limit = self._max_concurrent_domains or len(domains) or 1
        sem = asyncio.Semaphore(limit)
        logger.info(
            "Scheduling %d cells across %d pacing domains (interval=%.1fs, n=%d, concurrency=%d)",
            len(cells),
            len(domains),
            self.interval,
            self.config.n_requests,
            limit,
        )
        # Domains fire their per-round requests near-simultaneously, and each
        # synchronous invoke() runs in a worker thread. We own the pool (sized to
        # cover the concurrency so threads don't queue and distort measurements)
        # and shut it down on exit, so repeated run()/run_sync() calls and the
        # in-process test suite don't leak thread pools.
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=limit + 8) as pool:
            await asyncio.gather(
                *(
                    self._run_domain(domain_cells, sem, loop, pool)
                    for domain_cells in domains.values()
                )
            )
        return dict(self.samples)

    async def _run_domain(
        self,
        domain_cells: list[Cell],
        sem: asyncio.Semaphore,
        loop: asyncio.AbstractEventLoop,
        pool: ThreadPoolExecutor,
    ) -> None:
        """Run one pacing domain: interleave its cells, 1 request per interval."""
        async with sem:
            n = self.config.n_requests
            # Interleave: round 0 -> [cellA req0, cellB req0], round 1 -> ... so
            # both tiers progress together and share the per-minute cadence.
            schedule: list[Cell] = []
            for _ in range(n):
                schedule.extend(domain_cells)

            # Pre-build endpoints + payloads once per cell (endpoint construction
            # is untimed setup; reusing the client preserves connection warmth).
            endpoints = {c.label: build_endpoint(c, self.broker) for c in domain_cells}
            payloads = {c.label: build_payload(c, self.config) for c in domain_cells}
            done_count = 0
            total = len(schedule)

            domain_start = time.perf_counter()
            for i, cell in enumerate(schedule):
                target = domain_start + i * self.interval
                delay = target - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)

                sample = await self._invoke_once(
                    endpoints[cell.label], payloads[cell.label], loop, pool
                )
                self.samples[cell.label].append(sample)
                done_count += 1
                if self.on_sample:
                    try:
                        self.on_sample(cell.label, sample, done_count, total)
                    except Exception:  # progress must never break the run
                        logger.exception("progress callback failed")

    async def _invoke_once(
        self,
        endpoint,
        payload: dict,
        loop: asyncio.AbstractEventLoop,
        pool: ThreadPoolExecutor,
    ) -> Sample:
        """One timed invocation with a hard timeout, mapped to a :class:`Sample`."""
        request_time = _now_iso()
        try:
            resp = await asyncio.wait_for(
                loop.run_in_executor(pool, endpoint.invoke, payload),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return Sample(
                ttft=None,
                total=None,
                served_tier=None,
                num_tokens_output=None,
                error=f"TimeoutError: exceeded {self.config.timeout_seconds}s",
                request_time=request_time,
            )
        except Exception as e:  # pragma: no cover - defensive; invoke catches most
            return Sample(
                ttft=None,
                total=None,
                served_tier=None,
                num_tokens_output=None,
                error=f"{type(e).__name__}: {e}",
                request_time=request_time,
            )

        return Sample(
            ttft=resp.time_to_first_token,
            total=resp.time_to_last_token,
            served_tier=getattr(resp, "served_tier", None),
            num_tokens_output=resp.num_tokens_output,
            error=resp.error,
            request_time=request_time,
        )
