"""A draining task stops dialing but still accepts every call that reaches it, including the media
stream of a call it placed itself. Only a starting or stopping task refuses calls.

With a real carrier, a call the task dialed connects its media stream after the task starts draining
(the controller waits for exactly those calls). These tests use the real admission path on a live
WebSocket server bound to 127.0.0.1."""
import asyncio
import json
import time

import fakeredis
import pytest
import websockets

import calling_task as ct
from test_calling_task import ARN, live

DRAINING_STATES = ("DRAINING", "UNPROTECTED", "COUNT_LOWERED")


def new_task():
    t = ct.CallingTask(ARN, "127.0.0.1")
    t.db = fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    return t


@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(ct, "log", lambda e, **f: out.append((e, f)))
    return out


@pytest.fixture
def quiet(monkeypatch):
    """Protection always succeeds; the background dialer places no calls of its own."""
    monkeypatch.setattr(ct, "set_protection", lambda en: {"protection": {"ProtectionEnabled": True}})
    monkeypatch.setattr(ct, "DIAL_INTERVAL_SECONDS", 3600)


async def connect(port, query=""):
    """Open /ws (optionally with ?call=...). Returns ("accepted", first message) or ("refused", status)."""
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws{query}", open_timeout=3) as ws:
            return "accepted", json.loads(await asyncio.wait_for(ws.recv(), 3))
    except websockets.InvalidStatus as e:
        return "refused", e.response.status_code


class _Req:
    def __init__(self, path):
        self.path = path
        self.headers = {}


# ---------------------------------------------------------------- draining tasks accept calls
@pytest.mark.parametrize("state", DRAINING_STATES)
async def test_draining_accepts_inbound_calls(monkeypatch, logs, quiet, state):
    t = new_task()
    async with live(t, monkeypatch) as (port, _):
        t.state = state
        status, first = await connect(port)
        assert status == "accepted" and first["state"] == state
        assert (await connect(port, "?call=not-a-call-we-placed"))[0] == "accepted"
        t.pending["expiredcall0"] = time.time() - 1
        assert (await connect(port, "?call=expiredcall0"))[0] == "accepted"
    assert not [e for e, _ in logs if e == "call_refused"]


def test_admit_reasons_are_exact():
    t = new_task()
    t.state = "STARTING"
    assert t.admit(_Req("/ws")) == "starting"
    t.state = "STOPPING"
    assert t.admit(_Req("/ws")) == "stopping"
    for state in ("ACCEPTING",) + DRAINING_STATES:
        t.state = state
        assert t.admit(_Req("/ws")) is None
        assert t.admit(_Req("/ws?call=nope")) is None
    t.state = "DRAINING"
    t.pending["placedcall01"] = time.time() + 30
    assert t.admit(_Req("/ws?call=placedcall01")) is None
    assert "placedcall01" not in t.pending, "a placed call stops counting as pending once its stream connects"


async def test_starting_and_stopping_tasks_refuse_calls(monkeypatch, logs, quiet):
    t = new_task()
    async with live(t, monkeypatch) as (port, _):
        t.state = "STARTING"
        assert await connect(port) == ("refused", 503)
        t.state = "STOPPING"
        assert await connect(port) == ("refused", 503)
    assert [f["reason"] for e, f in logs if e == "call_refused"] == ["starting", "stopping"]


# ---------------------------------------------------------------- the task's own placed call
@pytest.mark.parametrize("state", DRAINING_STATES)
async def test_draining_admits_its_placed_call(monkeypatch, logs, quiet, state):
    t = new_task()
    monkeypatch.setattr(ct.CallingTask, "simulated_answer", lambda self, call_id: asyncio.sleep(0))
    async with live(t, monkeypatch) as (port, _):
        t.state = "ACCEPTING"
        call_id = t.place_call()  # dialed while ACCEPTING
        t.state = state           # then the controller drains the task
        status, first = await connect(port, f"?call={call_id}")
        assert status == "accepted" and first["task"] == "taskdddd"
        assert call_id not in t.pending
    assert [f["call"] for e, f in logs if e == "placed_call_connected"] == [call_id[:6]]
    assert not [e for e, _ in logs if e == "call_refused"]


async def test_call_ids_are_unguessable():
    t = new_task()
    t.state = "ACCEPTING"
    ids = {t.place_call() for _ in range(200)}
    for a in list(t.carrier):
        a.cancel()
    assert len(ids) == 200 and min(len(i) for i in ids) >= 20


async def test_accepting_task_admits_ordinary_inbound_calls(monkeypatch, logs, quiet):
    t = new_task()
    async with live(t, monkeypatch) as (port, _):
        t.state = "ACCEPTING"
        status, first = await connect(port)
        assert status == "accepted" and first["state"] == "ACCEPTING"
        status, _ = await connect(port, "?call=whatever")  # unknown IDs don't matter while ACCEPTING
        assert status == "accepted"
    assert not [e for e, _ in logs if e == "call_refused"]


# ---------------------------------------------------------------- pending_calls and drain readiness
async def test_pending_counts_placed_call_until_stream_connects(monkeypatch, quiet):
    t = new_task()
    monkeypatch.setattr(ct.CallingTask, "simulated_answer", lambda self, call_id: asyncio.sleep(0))
    async with live(t, monkeypatch) as (port, _):
        t.state = "ACCEPTING"
        call_id = t.place_call()
        t.state = "DRAINING"
        await asyncio.sleep(1.3)  # watch_state publishes every second
        assert await t.db.hget(t.key, "pending_calls") == "1"
        assert (await connect(port, f"?call={call_id}"))[0] == "accepted"
        await asyncio.sleep(1.3)
        assert await t.db.hget(t.key, "pending_calls") == "0"


async def test_pending_drops_placed_call_when_ring_window_expires(monkeypatch):
    monkeypatch.setattr(ct, "RING_SECONDS", 0)
    monkeypatch.setattr(ct, "PLACED_CALL_GRACE_SECONDS", 1.0)
    monkeypatch.setattr(ct.CallingTask, "simulated_answer", lambda self, call_id: asyncio.sleep(0))
    t = new_task()
    await t.db.hset(t.key, "arn", ARN)  # run() creates the record; a draining task only updates it (N2)
    t.state = "ACCEPTING"
    call_id = t.place_call()  # never answered
    t.state = "DRAINING"
    w = asyncio.create_task(t.watch_state())
    await asyncio.sleep(0.2)
    assert await t.db.hget(t.key, "pending_calls") == "1"
    await asyncio.sleep(1.9)
    t.stopping.set()
    w.cancel()
    assert await t.db.hget(t.key, "pending_calls") == "0" and call_id not in t.pending


async def test_drain_ready_not_reported_while_placed_call_pending(monkeypatch, logs):
    """End to end on the task, using only the task's own dialer: calls are dialed while ACCEPTING, the
    controller then drains the task, and each call's media stream connects after RING_SECONDS. Until
    then the published record must not satisfy the controller's drain_ready rule (dialer_stopped=1,
    pending_calls=0, renewal_stopped=1), and the draining task must admit those streams."""
    monkeypatch.setattr(ct, "set_protection", lambda en: {"protection": {"ProtectionEnabled": True}})
    monkeypatch.setattr(ct, "RING_SECONDS", 2)
    monkeypatch.setattr(ct, "DIAL_INTERVAL_SECONDS", 0.5)

    async def ready():
        r = await t.db.hgetall(t.key)
        return r.get("dialer_stopped") == "1" and r.get("pending_calls") == "0" and r.get("renewal_stopped") == "1"
    t = new_task()
    async with live(t, monkeypatch) as (port, _):
        t.state = "ACCEPTING"
        await asyncio.sleep(0.7)  # the dialer places at least one call
        assert t.placed_total >= 1
        await t.db.hset(t.key, "state", "DRAINING")  # the controller's decision
        await asyncio.sleep(1.3)
        assert t.state == "DRAINING" and not await ready(), "drain-ready while a placed call was pending"
        await asyncio.sleep(3.0)  # every placed call's stream has connected by now
        assert await ready(), "the placed calls' streams connected but pending_calls didn't drop"
    admitted = [f for e, f in logs if e == "call_started" and f.get("state") in DRAINING_STATES]
    assert admitted, "the draining task refused the media streams of calls it placed itself"
    assert len(admitted) == t.placed_total, f"{len(admitted)} of {t.placed_total} placed calls got through"
