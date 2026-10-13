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
#: Some providers cache their fixed chat-template prefix (MiniMax M2 reports 16 cached
#: tokens on every request, however unique the user text). A cold sample is contaminated
#: only when it read more than this from cache, i.e. part of the document itself.
TEMPLATE_CACHE_TOKENS = 64
TEMPLATE_CACHE_SHARE = 0.05


def build_adapter(cell: Cell, broker: AuthBroker) -> Any:
    """The API adapter that sends requests for ``cell``."""
    region = cell.region
    api = cell.api
    if api is Api.MESSAGES:
        return anthropic_messages.Messages(
            cell.endpoint.value,
            region,
            broker.token_provider(region),
            client=broker.http_client(cell.endpoint.value, region),
        )
    if api in (Api.CHAT_COMPLETIONS, Api.RESPONSES):
        if cell.endpoint is Endpoint.RUNTIME:
            base = openai_compat.runtime_base_url(region)
        else:
            base = openai_compat.mantle_base_url(
                region, cell.offering.base_path or openai_compat.MANTLE_DEFAULT_PATH
            )
        http = broker.http_client(cell.endpoint.value, region)
        if api is Api.CHAT_COMPLETIONS:
            return openai_compat.ChatCompletions(base, broker.token_provider(region), http)
        return openai_compat.Responses(base, broker.token_provider(region), http)
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
    match = served_matches(cell.tier, m.served_tier)
    if match is False:
        return False, "tier_mismatch"
    if match is None and not cell.tier.is_default:
        # Without a served-tier report we cannot tell flex/priority from Standard.
        return False, "tier_unreported"
    read = m.usage.cache_read_tokens
    if cell.cache is CacheMode.COLD and read:
        allowed = max(TEMPLATE_CACHE_TOKENS, TEMPLATE_CACHE_SHARE * (m.usage.input_tokens or 0))
        if read > allowed:
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
            futures = {pool.submit(self._run_domain, d): k for k, d in domains.items()}
            for f, key in futures.items():
                try:
                    f.result()
                except Exception:  # noqa: BLE001 - one broken domain must not lose the others
                    logger.exception(
                        "pacing domain %s failed; its remaining cells are missing", key
                    )
        return dict(self.records)

    # ------------------------------------------------------------------ internals
    def _run_domain(self, cells: list[Cell]) -> None:
        """Run one pacing domain.

        Contexts (same model, endpoint, API, scope, region, size, cache) run one
        after another; inside a context the tiers are interleaved and shuffled each
        round. A context's requests are therefore at most ``tiers x interval`` apart,
        which keeps warm-cache contexts inside the prompt-cache TTL, while tiers
        are still compared under the same conditions.
        """
        cfg = self.config
        rng = random.Random(f"{cfg.seed}:{cells[0].domain}")  # nosec B311 - shuffles request order, not security-sensitive
        contexts: dict[str, list[Cell]] = defaultdict(list)
        for c in cells:
            contexts[c.context_key].append(c)
        order = list(contexts)
        rng.shuffle(order)
        total = cfg.n_requests * len(cells)
        done = 0
        start = time.monotonic()
        slot = 0
        for ck in order:
            group = contexts[ck]
            try:
                adapters = {c.label: self._factory(c, self.broker) for c in group}
            except Exception as e:  # noqa: BLE001 - e.g. expired credentials while minting a token
                logger.error("cannot build adapters for %s: %s", ck, type(e).__name__)
                err = Measurement(error=f"{type(e).__name__}: {str(e)[:300]}", error_kind="client")
                with self._lock:
                    for c in group:
                        self.records[c.label].extend(
                            self._record(c, err) for _ in range(cfg.n_requests)
                        )
                done += cfg.n_requests * len(group)
                continue
            # One prompt factory per context, so every tier shares the warm prefix.
            prompts = PromptFactory(group[0].size, group[0].cache, cfg.seed, ck)
            warm = group[0].cache.is_warm
            warmup = max(cfg.warmup_requests, 1 if warm else 0)
            for rnd in range(warmup + cfg.n_requests):
                round_cells = list(group)
                rng.shuffle(round_cells)
                for cell in round_cells:
                    delay = start + slot * cfg.interval_seconds - time.monotonic()
                    slot += 1
                    if delay > 0:
                        self._sleep(delay)
                    else:
                        # An overrunning request shifts the schedule instead of
                        # firing the following slots back to back.
                        start -= delay
                    m = self._send(adapters[cell.label], prompts, cell)
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

    def _send(self, adapter: Any, prompts: PromptFactory, cell: Cell) -> Measurement:
        """Send one request inline; the adapter enforces the wall-clock deadline."""
        p = prompts.next()
        req = Request(
            model_id=cell.model_id,
            region=cell.region,
            document=p.document,
            question=p.question,
            max_tokens=self.config.output_cap(cell.size),
            tier=None if cell.tier.is_default else cell.tier.value,
            explicit_cache=cell.cache is CacheMode.WARM_EXPLICIT,
            temperature=None if cell.spec.reasoning else 0.0,
            timeout=self.config.timeout_for(cell.tier),
            reasoning_effort=self.reasoning_effort if cell.spec.reasoning else None,
        )
        try:
            return adapter.send(req)
        except Exception as e:  # noqa: BLE001 - adapters record errors; this is a backstop
            return Measurement(error=f"{type(e).__name__}: {str(e)[:300]}", error_kind="client")

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
