"""The paced execution engine.

* **Pacing domain** = (endpoint, model id, region). Requests in a domain run
  strictly serially, ``interval_seconds`` apart (start to start). Domains run in
  parallel threads.
* Each **round** sends one request per cell in the domain, in a seeded random
  order, so no tier systematically goes first.
* The first ``warmup_requests`` rounds (at least 1 for warm-cache cells, which
  must prime the cache) are sent and discarded.
* Every measured sample is checked: a cold sample that read from cache is
  excluded as ``cache_contaminated``; a warm sample with no cache read is
  excluded as ``warm_miss``; a sample served on another tier than requested is
  excluded as ``tier_mismatch``. Exclusions are counted and reported.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Any

from .apis import anthropic_messages, bedrock_native, openai_compat
from .apis.base import Measurement, Request
from .auth import AuthBroker
from .cells import Cell
from .config import Api, BenchmarkConfig, CacheMode, Endpoint, Tier
from .prompts import PromptFactory

logger = logging.getLogger("bedrock_bench.runner")

#: (cell, record, done, total) -> None
SampleCallback = Callable[[Cell, dict[str, Any], int, int], None]

_STANDARD_NAMES = {"default", "standard", "on_demand", "on-demand"}


def build_adapter(cell: Cell, broker: AuthBroker) -> Any:
    """The API adapter that sends requests for ``cell``."""
    region = cell.region
    api = cell.api
    if api is Api.MESSAGES:
        return anthropic_messages.Messages(
            cell.endpoint.value, region, broker.token_provider(region)
        )
    if api in (Api.CHAT_COMPLETIONS, Api.RESPONSES):
        if cell.endpoint is Endpoint.RUNTIME:
            base = openai_compat.runtime_base_url(region)
        else:
            base = openai_compat.mantle_base_url(
                region, cell.offering.base_path or openai_compat.MANTLE_DEFAULT_PATH
            )
        if api is Api.CHAT_COMPLETIONS:
            return openai_compat.ChatCompletions(base, broker.token_provider(region))
        return openai_compat.Responses(base, broker.token_provider(region))
    client = broker.bedrock_runtime(region)
    style = cell.spec.body_style
    return {
        Api.CONVERSE_STREAM: lambda: bedrock_native.ConverseStream(client),
        Api.CONVERSE: lambda: bedrock_native.Converse(client),
        Api.INVOKE_STREAM: lambda: bedrock_native.InvokeStream(client, style),
        Api.INVOKE: lambda: bedrock_native.Invoke(client, style),
    }[api]()


def served_matches(requested: Tier, served: str | None) -> bool | None:
    """Whether the served tier matches the request (``None`` = not reported)."""
    if not served:
        return None
    s = served.lower()
    if requested is Tier.DEFAULT:
        return s in _STANDARD_NAMES
    return s == requested.value


def classify_sample(cell: Cell, m: Measurement) -> tuple[bool, str | None]:
    """``(included, exclude_reason)`` for a successful measurement."""
    if served_matches(cell.tier, m.served_tier) is False:
        return False, "tier_mismatch"
    read = m.usage.cache_read_tokens
    if cell.cache is CacheMode.COLD and read:
        return False, "cache_contaminated"
    if cell.cache.is_warm and not read:
        return False, "warm_miss"
    if cell.api.streaming and m.ttft is None:
        return False, "no_tokens"
    return True, None


class Runner:
    """Runs a list of cells with per-domain pacing and cross-domain parallelism."""

    def __init__(
        self,
        config: BenchmarkConfig,
        broker: AuthBroker,
        *,
        on_sample: SampleCallback | None = None,
        reasoning_effort: str | None = None,
        adapter_factory: Callable[[Cell, AuthBroker], Any] = build_adapter,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.broker = broker
        self.on_sample = on_sample
        self.reasoning_effort = reasoning_effort
        self._factory = adapter_factory
        self._sleep = sleep
        self.records: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._lock = threading.Lock()

    def run(self, cells: list[Cell]) -> dict[str, list[dict[str, Any]]]:
        domains: dict[str, list[Cell]] = defaultdict(list)
        for c in cells:
            domains[c.domain].append(c)
        logger.info("Running %d cells in %d pacing domains", len(cells), len(domains))
        with ThreadPoolExecutor(max_workers=max(len(domains), 1)) as pool:
            futures = [pool.submit(self._run_domain, d) for d in domains.values()]
            for f in futures:
                f.result()
        return dict(self.records)

    # ------------------------------------------------------------------ internals
    def _run_domain(self, cells: list[Cell]) -> None:
        cfg = self.config
        rng = random.Random(f"{cfg.seed}:{cells[0].domain}")  # nosec B311 - shuffles request order, not security-sensitive
        adapters = {c.label: self._factory(c, self.broker) for c in cells}
        factories = {
            c.label: PromptFactory(c.size, c.cache, cfg.seed, c.context_key) for c in cells
        }
        warmup = max(cfg.warmup_requests, 1 if any(c.cache.is_warm for c in cells) else 0)
        rounds = warmup + cfg.n_requests
        total = cfg.n_requests * len(cells)
        done = 0
        # One worker thread enforces the wall-clock timeout per request.
        with ThreadPoolExecutor(max_workers=1) as one:
            start = time.monotonic()
            slot = 0
            for rnd in range(rounds):
                order = list(cells)
                rng.shuffle(order)
                for cell in order:
                    target = start + slot * cfg.interval_seconds
                    slot += 1
                    delay = target - time.monotonic()
                    if delay > 0:
                        self._sleep(delay)
                    m = self._send(one, adapters[cell.label], factories[cell.label], cell)
                    if rnd < warmup:
                        continue
                    record = self._record(cell, m)
                    with self._lock:
                        self.records[cell.label].append(record)
                        done += 1
                    if self.on_sample:
                        try:
                            self.on_sample(cell, record, done, total)
                        except Exception:  # noqa: BLE001 - progress must never break a run
                            logger.exception("progress callback failed")

    def _send(self, pool: ThreadPoolExecutor, adapter: Any, prompts: PromptFactory, cell: Cell):
        p = prompts.next()
        timeout = self.config.timeout_for(cell.tier)
        req = Request(
            model_id=cell.model_id,
            region=cell.region,
            document=p.document,
            question=p.question,
            max_tokens=self.config.output_cap(cell.size),
            tier=None if cell.tier.is_default else cell.tier.value,
            explicit_cache=cell.cache is CacheMode.WARM_EXPLICIT,
            temperature=None if cell.spec.reasoning else 0.0,
            timeout=timeout,
            reasoning_effort=self.reasoning_effort if cell.spec.reasoning else None,
        )
        fut = pool.submit(adapter.send, req)
        try:
            return fut.result(timeout=timeout + 5)
        except FutureTimeout:
            m = Measurement(error=f"TimeoutError: exceeded {timeout:.0f}s", error_kind="timeout")
            return m

    def _record(self, cell: Cell, m: Measurement) -> dict[str, Any]:
        rec = m.to_dict()
        if m.error is None:
            included, reason = classify_sample(cell, m)
        else:
            included, reason = False, None
        rec.update(
            cell=cell.label,
            included=included,
            exclude_reason=reason,
            request_time=datetime.now(timezone.utc).isoformat(),
        )
        return rec
