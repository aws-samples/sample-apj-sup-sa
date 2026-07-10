"""Async tests for the rate-paced scheduler.

These use a fake in-memory endpoint (no AWS) injected by monkeypatching the
``build_endpoint``/``build_payload`` helpers the scheduler imports, so we can
assert pacing, timeout handling, and that one cell's failure does not abort its
domain.
"""

from __future__ import annotations

import time

import pytest

from bedrock_bench import scheduler as scheduler_mod
from bedrock_bench.cells import Cell
from bedrock_bench.config import BenchmarkConfig, Tier, Transport
from bedrock_bench.registry import ModelSpec

SPEC = ModelSpec(
    key="vendor.model",
    family="Vendor",
    display_name="Vendor Model",
    invoke_id="vendor.model",
    invoke_region="us-west-2",
    invoke_tiers=(Tier.DEFAULT, Tier.FLEX),
    mantle_id=None,
    mantle_region=None,
    mantle_tiers=(),
)


class _FakeResponse:
    def __init__(self, ttft, total, served, error=None):
        self.time_to_first_token = ttft
        self.time_to_last_token = total
        self.served_tier = served
        self.num_tokens_output = 5
        self.error = error


class _FakeEndpoint:
    """Records invocations; returns a canned response or raises."""

    def __init__(self, behaviour):
        self.behaviour = behaviour  # callable(payload) -> _FakeResponse | raises

    def invoke(self, payload):
        return self.behaviour(payload)


@pytest.fixture
def patch_builders(monkeypatch):
    """Patch the scheduler's endpoint/payload builders with fakes."""

    def _install(behaviour):
        monkeypatch.setattr(
            scheduler_mod, "build_endpoint", lambda cell, broker: _FakeEndpoint(behaviour)
        )
        monkeypatch.setattr(scheduler_mod, "build_payload", lambda cell, config: {"p": 1})

    return _install


def _cells(tiers):
    return [Cell(spec=SPEC, transport=Transport.INVOKE, tier=t, region="us-west-2") for t in tiers]


@pytest.mark.asyncio
async def test_collects_samples_per_cell(patch_builders):
    patch_builders(lambda p: _FakeResponse(0.4, 0.9, "flex"))
    cfg = BenchmarkConfig(regions=("us-west-2",), n_requests=3)
    sched = scheduler_mod.Scheduler(cfg, broker=None, interval_override=0.0)
    cells = _cells([Tier.DEFAULT, Tier.FLEX])
    samples = await sched.run(cells)
    assert len(samples[cells[0].label]) == 3
    assert len(samples[cells[1].label]) == 3
    assert all(s.error is None and s.ttft == 0.4 for s in samples[cells[0].label])


@pytest.mark.asyncio
async def test_pacing_interval_respected(patch_builders):
    patch_builders(lambda p: _FakeResponse(0.01, 0.01, "flex"))
    cfg = BenchmarkConfig(regions=("us-west-2",), n_requests=3)
    sched = scheduler_mod.Scheduler(cfg, broker=None, interval_override=0.2)
    cells = _cells([Tier.DEFAULT])  # single tier -> 3 requests spaced by 0.2s
    start = time.perf_counter()
    await sched.run(cells)
    elapsed = time.perf_counter() - start
    # 3 requests at 0.2s spacing -> >= ~0.4s between first and last starts.
    assert elapsed >= 0.4


@pytest.mark.asyncio
async def test_failure_in_one_request_does_not_abort_domain(patch_builders):
    calls = {"n": 0}

    def behaviour(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return _FakeResponse(0.3, 0.7, "flex")

    patch_builders(behaviour)
    cfg = BenchmarkConfig(regions=("us-west-2",), n_requests=3)
    sched = scheduler_mod.Scheduler(cfg, broker=None, interval_override=0.0)
    cells = _cells([Tier.DEFAULT])
    samples = await sched.run(cells)
    got = samples[cells[0].label]
    assert len(got) == 3  # all attempts recorded despite the first failing
    assert sum(1 for s in got if s.error) == 1
    assert sum(1 for s in got if s.error is None) == 2


@pytest.mark.asyncio
async def test_timeout_maps_to_error_sample(patch_builders):
    def slow(payload):
        time.sleep(0.5)
        return _FakeResponse(0.1, 0.1, "flex")

    patch_builders(slow)
    cfg = BenchmarkConfig(regions=("us-west-2",), n_requests=1, timeout_seconds=0.1)
    sched = scheduler_mod.Scheduler(cfg, broker=None, interval_override=0.0)
    cells = _cells([Tier.DEFAULT])
    samples = await sched.run(cells)
    s = samples[cells[0].label][0]
    assert s.error is not None and "Timeout" in s.error
    assert s.ttft is None and s.total is None
