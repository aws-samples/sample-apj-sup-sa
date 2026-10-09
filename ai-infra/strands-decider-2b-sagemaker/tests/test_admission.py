"""Admission tests for container/app.py, with a stub engine (no GPU, no model, no torch).

    pip install -r tests/requirements.txt
    python -m pytest tests

They cover the queue bookkeeping behind the fast 503: requests whose handler is cancelled, while queued or
while running, must not leave the worker refusing traffic or admitting more than it can take.
"""
import asyncio
import importlib
import json
import os
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

CONTAINER = Path(__file__).resolve().parents[1] / "container"


class _Request:
    def __init__(self, questions, cost):
        self.questions, self.cost = questions, cost

    @classmethod
    def model_validate_json(cls, raw):
        body = json.loads(raw)
        return cls(body["questions"], body.get("cost", 0.05))


class _Result:
    def model_dump(self):
        return {"answers": {}}


class _Engine:
    def evaluate(self, req):
        time.sleep(req.cost)          # the request body says how long the "engine" takes
        return _Result()


@pytest.fixture()
def app(monkeypatch):
    """A fresh app module with torch and the decider package stubbed out."""
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(synchronize=lambda: None)
    torch.dtype = object
    torch.bfloat16 = torch.float32 = object()
    monkeypatch.setitem(sys.modules, "torch", torch)
    for name in ("strands_decider", "strands_decider.infer", "strands_decider.modeling", "strands_decider.schema"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["strands_decider.schema"].SystemOneRequest = _Request
    sys.modules["strands_decider.infer"].EngineConfig = object
    sys.modules["strands_decider.infer"].SystemOneEngine = object
    sys.modules["strands_decider.modeling"].StrandsDeciderModel = object
    monkeypatch.setenv("DECIDER_DEVICE", "cpu")
    monkeypatch.setenv("DECIDER_MAX_QUEUE", "16")
    monkeypatch.setenv("DECIDER_MAX_QUEUE_WAIT", "2")
    monkeypatch.syspath_prepend(str(CONTAINER))
    for name in ("app", "serve"):
        sys.modules.pop(name, None)
    module = importlib.import_module("app")
    module.ENGINE = _Engine()
    module.RECENT.extend([0.05] * 8)
    module.ENGINE_S = 0.05
    yield module
    sys.modules.pop("app", None)
    sys.modules.pop("serve", None)


def _body(cost):
    return json.dumps({"state": "x", "questions": {"a": {}}, "cost": cost})


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://test", timeout=60)


def test_cancelled_while_queued_leaves_no_stale_entry(app):
    async def run():
        async with _client(app) as c:
            running = asyncio.create_task(c.post("/invocations", content=_body(1.0)))
            await asyncio.sleep(0.1)
            queued = [asyncio.create_task(c.post("/invocations", content=_body(0.05))) for _ in range(3)]
            await asyncio.sleep(0.1)
            assert len(app.WAITING) == 3
            queued[0].cancel()
            queued[1].cancel()
            await asyncio.gather(running, queued[2], return_exceptions=True)
            await asyncio.sleep(0.2)
            assert len(app.WAITING) == 0 and app.IN_FLIGHT == 0
            await asyncio.sleep(app.MAX_QUEUE_WAIT_S + 0.5)   # a stale entry would now look older than the limit
            assert (await c.post("/invocations", content=_body(0.05))).status_code == 200
    asyncio.run(run())


def test_cancelled_while_running_is_counted_until_the_job_ends(app):
    async def run():
        async with _client(app) as c:
            running = asyncio.create_task(c.post("/invocations", content=_body(1.0)))
            await asyncio.sleep(0.2)                         # the job is on the engine thread now
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            assert app.IN_FLIGHT == 1                       # the engine is still busy with it
            await asyncio.sleep(1.0)
            assert app.IN_FLIGHT == 0
    asyncio.run(run())


def test_burst_is_refused_at_once_beyond_the_queue_depth(app):
    async def run():
        async with _client(app) as c:
            async def one():
                t0 = time.monotonic()
                r = await c.post("/invocations", content=_body(0.5))
                return r.status_code, time.monotonic() - t0, r.headers.get("x-decider-worker")
            out = await asyncio.gather(*(one() for _ in range(40)))
        refused = [(d, h) for s, d, h in out if s == 503]
        assert sum(s == 200 for s, _, _ in out) == app.MAX_QUEUE
        assert len(refused) == 40 - app.MAX_QUEUE
        assert all(d < 0.1 and h == "busy" for d, h in refused)
        assert len(app.WAITING) == 0 and app.IN_FLIGHT == 0
    asyncio.run(run())
