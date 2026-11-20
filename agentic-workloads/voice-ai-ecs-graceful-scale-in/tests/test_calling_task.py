"""Calling-task tests (asyncio). Assertions state the intended behaviour; failures are findings.
The agent endpoint is faked by patching set_protection/local_request; Valkey is fakeredis."""
import asyncio
import contextlib
import json
import signal
import socket
import threading
import time

import fakeredis
import pytest
import websockets

import calling_task as ct

ARN = "arn:aws:ecs:us-east-1:111122223333:task/voice-cluster/taskdddd"


def new_task(db=None):
    t = ct.CallingTask(ARN, "127.0.0.1")
    t.db = db or fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    return t


class FakeConn:
    def respond(self, status, text):
        return (int(status), text)


class FakeReq:
    def __init__(self, path, ua="ELB-HealthChecker/2.0"):
        self.path = path
        self.headers = {"User-Agent": ua}


class HangingDB:
    """A Valkey that accepts the connection but never answers."""
    async def _hang(self, *a, **k):
        await asyncio.Event().wait()
    hset = sadd = get = hget = _hang

    # (Updated for N2.) publish() past ACCEPTING uses a WATCH/MULTI pipeline; it hangs the same way.
    def pipeline(self, *a, **k):
        db = self

        class Pipe:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False
            watch = exists = execute = db._hang

            def multi(self):
                pass

            def hset(self, *a, **k):
                pass
        return Pipe()


class BrokenDB:
    async def _boom(self, *a, **k):
        raise ConnectionError("valkey down")
    hset = sadd = get = hget = _boom


@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(ct, "log", lambda e, **f: out.append((e, f)))
    return out


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@contextlib.asynccontextmanager
async def live(task, monkeypatch):
    port = free_port()
    monkeypatch.setattr(ct, "PORT", port)
    # (Updated for L4.) ACCEPTING now needs a confirmed protection, which main() does before run().
    # These tests start run() directly, so model main()'s confirmed initial protect.
    task.protection_confirmed = True
    runner = asyncio.create_task(task.run())
    for _ in range(100):
        try:
            _, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            break
        except OSError:
            await asyncio.sleep(0.02)
    try:
        yield port, runner
    finally:
        task.stopping.set()
        runner.cancel()
        with contextlib.suppress(BaseException):
            await runner
        asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)


async def http_get(port, path, ua="ELB-HealthChecker/2.0", timeout=2):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\nUser-Agent: {ua}\r\n\r\n".encode())
    await w.drain()
    line = await asyncio.wait_for(r.readline(), timeout)
    w.close()
    return line.decode().strip()


# ---------------------------------------------------------------- (j) protection lifecycle
def test_j1_protection_request_shape_and_defaults(monkeypatch):
    monkeypatch.setenv("ECS_AGENT_URI", "http://169.254.170.2/api/abc")
    cap = []
    monkeypatch.setattr(ct, "local_request", lambda m, u, b=None, timeout=10: cap.append((m, u, b)) or {})
    ct.set_protection(True)
    ct.set_protection(False)
    assert cap[0] == ("PUT", "http://169.254.170.2/api/abc/task-protection/v1/state",
                      {"ProtectionEnabled": True, "ExpiresInMinutes": 15})
    assert cap[1][2] == {"ProtectionEnabled": False}
    assert (ct.RENEW_SECONDS, ct.PROTECT_MINUTES, ct.CALL_CAP_SECONDS) == (300, 15, 600)


def test_j1b_local_request_refuses_non_http():
    with pytest.raises(ValueError):
        ct.local_request("GET", "https://169.254.170.2/v4")


async def test_j2_protects_before_serving(monkeypatch):
    order = []
    monkeypatch.setattr(ct, "task_metadata", lambda: (ARN, "127.0.0.1"))
    monkeypatch.setattr(ct, "set_protection", lambda en: order.append(("protect", en)) or {"protection": {}})

    async def fake_run(self):
        order.append("serve")
    monkeypatch.setattr(ct.CallingTask, "run", fake_run)
    await ct.main()
    assert order == [("protect", True), "serve"]


async def test_j3_not_accepting_while_unprotected(monkeypatch):
    """Initial protection and the first renewal both fail; the task should not report ACCEPTING."""
    monkeypatch.setattr(ct, "set_protection", lambda en: {"failure": {"Reason": "DEPLOYMENT_BLOCKED"}})
    t = new_task()
    t.register_accepting = lambda: asyncio.sleep(0)
    await t.health(FakeConn(), FakeReq("/health"))
    assert t.state != "ACCEPTING", "task went ACCEPTING without ever confirming protection"


async def test_j4_renews_while_accepting_stops_on_draining(monkeypatch):
    monkeypatch.setattr(ct, "RENEW_SECONDS", 0.05)
    calls = []
    monkeypatch.setattr(ct, "set_protection", lambda en: calls.append(en) or {"protection": {"ExpirationDate": 1}})
    t = new_task()
    t.state = "ACCEPTING"
    w = asyncio.create_task(t.protect_forever())
    await asyncio.sleep(0.3)
    t.state = "DRAINING"
    await asyncio.sleep(0.1)
    n2 = len(calls)
    await asyncio.sleep(0.3)
    t.stopping.set()
    await w
    assert n2 >= 3 and len(calls) == n2 and all(calls)


async def test_j5_dialer_and_flag_stop_on_draining(monkeypatch):
    monkeypatch.setattr(ct, "DIAL_INTERVAL_SECONDS", 0.02)
    t = new_task()
    t.state = "ACCEPTING"
    tasks = [asyncio.create_task(t.dialer()), asyncio.create_task(t.watch_state())]
    await asyncio.sleep(0.2)
    await t.db.hset(t.key, "state", "DRAINING")
    await asyncio.sleep(1.3)
    placed = t.placed_total
    await asyncio.sleep(0.2)
    t.stopping.set()
    for x in tasks:
        x.cancel()
    assert t.state == "DRAINING" and t.placed_total == placed > 0
    assert await t.db.hget(t.key, "dialer_stopped") == "1"
    assert await t.db.hget(t.key, "state") == "DRAINING", "task overwrote the controller's state"


# ---------------------------------------------------------------- (h)/(i) agent endpoint errors
AGENT_ERRORS = {
    "DEPLOYMENT_BLOCKED": lambda: {"failure": {"Arn": ARN, "Reason": "DEPLOYMENT_BLOCKED", "Detail": None}},
    "TASK_NOT_VALID": lambda: {"failure": {"Arn": ARN, "Reason": "TASK_NOT_VALID", "Detail": None}},
    "ThrottlingException": lambda: {"requestID": "r", "error": {"Arn": ARN, "Code": "ThrottlingException",
                                                                "Message": "Rate exceeded"}},
    "RequestCanceled": lambda: {"error": {"Arn": ARN, "Code": "RequestCanceled", "Message": "Timed out"}},
    "connection refused": lambda: (_ for _ in ()).throw(ConnectionRefusedError()),
    "non-JSON body": lambda: json.loads("<html>"),
}


@pytest.mark.parametrize("kind", list(AGENT_ERRORS))
async def test_hi_renewal_errors_logged_and_retried(monkeypatch, logs, kind):
    monkeypatch.setattr(ct, "RENEW_SECONDS", 0.05)
    attempts = []

    def sp(en):
        attempts.append(en)
        return AGENT_ERRORS[kind]()
    monkeypatch.setattr(ct, "set_protection", sp)
    t = new_task()
    t.state = "ACCEPTING"
    w = asyncio.create_task(t.protect_forever())
    await asyncio.sleep(0.6)  # room for three 0.05 s intervals plus retry jitter on a loaded machine
    assert not w.done(), "renewal loop crashed"
    t.stopping.set()
    await w
    assert len(attempts) >= 3
    assert sum(1 for e, _ in logs if e == "protection_failed") == len(attempts)


async def test_i2_transient_error_retried_before_next_interval_advisory(monkeypatch):
    """Advisory: with 15-min protection renewed every 5 min, a throttled renewal waits a full interval."""
    monkeypatch.setattr(ct, "RENEW_SECONDS", 0.5)
    # (Updated for L6.) The short retry is 10-30 s in production; scale it down like RENEW_SECONDS.
    monkeypatch.setattr(ct, "TRANSIENT_RETRY_MIN_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(ct, "TRANSIENT_RETRY_MAX_SECONDS", 0.1, raising=False)
    stamps = []

    def sp(en):
        stamps.append(time.monotonic())
        return AGENT_ERRORS["ThrottlingException"]()
    monkeypatch.setattr(ct, "set_protection", sp)
    t = new_task()
    t.state = "ACCEPTING"
    w = asyncio.create_task(t.protect_forever())
    await asyncio.sleep(0.3)
    t.stopping.set()
    await w
    assert len(stamps) >= 2, "no short backoff retry after ThrottlingException; next try is RENEW_SECONDS later"


async def test_i3_initial_protection_exception_kills_task(monkeypatch):
    # (Updated for L5.) main() now retries for INITIAL_PROTECT_WINDOW_SECONDS (60 s by default); shorten it.
    monkeypatch.setattr(ct, "INITIAL_PROTECT_WINDOW_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(ct, "task_metadata", lambda: (ARN, "127.0.0.1"))
    monkeypatch.setattr(ct, "set_protection", lambda en: (_ for _ in ()).throw(ConnectionRefusedError()))

    async def fake_run(self):
        pass
    monkeypatch.setattr(ct.CallingTask, "run", fake_run)
    try:
        await ct.main()
    except ConnectionRefusedError:
        pytest.fail("agent endpoint not ready at start -> unhandled exception, container exits")


# ---------------------------------------------------------------- (c) task-side view of the renewal race
async def test_c4_drain_ready_not_reported_while_renewal_in_flight(monkeypatch):
    monkeypatch.setattr(ct, "RENEW_SECONDS", 100)
    in_flight = threading.Event()

    def slow(en):
        in_flight.set()
        time.sleep(1.8)
        in_flight.clear()
        return {"protection": {"ProtectionEnabled": True}}
    monkeypatch.setattr(ct, "set_protection", slow)
    t = new_task()
    t.state = "ACCEPTING"
    workers = [asyncio.create_task(t.protect_forever())]
    await asyncio.sleep(0.1)
    await t.db.hset(t.key, "state", "DRAINING")  # controller decision
    workers.append(asyncio.create_task(t.watch_state()))
    await asyncio.sleep(1.2)
    ready = (await t.db.hget(t.key, "dialer_stopped") == "1" and await t.db.hget(t.key, "pending_calls") == "0"
             # (Updated for H1.) Drain-ready now also needs renewal_stopped=1, which the fixed controller
             # requires. A missing field counts as ready because the original controller ignores it.
             and await t.db.hget(t.key, "renewal_stopped") in ("1", None))
    still = in_flight.is_set()
    t.stopping.set()
    await asyncio.sleep(0.8)
    for x in workers:
        x.cancel()
    assert not (ready and still), ("task published drain-ready (dialer_stopped=1, pending=0) while a "
                                   "protection renewal was still in flight; controller cannot tell")


# ---------------------------------------------------------------- (n) health / SIGTERM
async def test_n1_health_is_process_health(monkeypatch):
    t = new_task(BrokenDB())
    for state in ("STARTING", "ACCEPTING", "DRAINING", "COUNT_LOWERED"):
        t.state = state
        t.register_accepting = lambda: asyncio.sleep(0)
        assert (await t.health(FakeConn(), FakeReq("/health")))[0] == 200


async def test_n2_health_answered_before_valkey_connects(monkeypatch):
    monkeypatch.setattr(ct, "set_protection", lambda en: {"protection": {}})
    t = new_task(HangingDB())
    async with live(t, monkeypatch) as (port, _):
        assert (await http_get(port, "/health")).startswith("HTTP/1.1 200")
        assert t.state == "ACCEPTING"
        assert (await http_get(port, "/nope")).startswith("HTTP/1.1 404")
        assert signal.SIGTERM in asyncio.get_running_loop()._signal_handlers


async def test_n3_shutdown_flushes_state():
    # Updated for H2: shutdown() reports its own lifecycle in proc_state and must leave the
    # controller-owned "state" alone (it used to overwrite it with STOPPING, hiding COUNT_LOWERED).
    t = new_task()
    t.state = "COUNT_LOWERED"
    await t.db.hset(t.key, "state", "COUNT_LOWERED")
    await t.shutdown()
    assert await t.db.hget(t.key, "proc_state") == "STOPPING"
    assert await t.db.hget(t.key, "state") == "COUNT_LOWERED", "task overwrote the controller's state"
    assert t.stopping.is_set()


async def test_n4_shutdown_bounded_when_valkey_hangs():
    t = new_task(HangingDB())
    try:
        await asyncio.wait_for(t.shutdown(), 3)
    except asyncio.TimeoutError:
        pytest.fail("shutdown blocks forever on a hung Valkey write (no socket timeout); exit relies on "
                    "SIGKILL at stopTimeout")


async def test_n5_shutdown_closes_calls_concurrently():
    class SlowWS:
        async def close(self, code, reason):
            await asyncio.sleep(0.3)  # stands in for websockets close_timeout (10 s default)
    t = new_task()
    t.calls = {SlowWS() for _ in range(5)}
    t0 = time.monotonic()
    await t.shutdown()
    took = time.monotonic() - t0
    assert took < 0.6, f"calls closed sequentially: {took:.2f}s for 5 calls (x10 s close_timeout in prod)"


# ---------------------------------------------------------------- (o) call cap + inbound while draining
async def test_o1_call_cap_enforced(monkeypatch):
    monkeypatch.setattr(ct, "CALL_CAP_SECONDS", 2)
    monkeypatch.setattr(ct, "set_protection", lambda en: {"protection": {}})
    t = new_task()
    async with live(t, monkeypatch) as (port, _):
        # (Updated for M5.) A task refuses /ws until it is ACCEPTING; the ALB only routes to it after
        # its first health check, so do that first, as the load balancer would.
        assert (await http_get(port, "/health")).startswith("HTTP/1.1 200") and t.state == "ACCEPTING"
        t0 = time.monotonic()
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            first = json.loads(await ws.recv())
            with contextlib.suppress(websockets.ConnectionClosed):
                async for _ in ws:
                    pass
        took = time.monotonic() - t0
        assert first["task"] == "taskdddd"
        assert ws.close_code == 1000 and ws.close_reason == "call cap reached"
        assert 1.9 <= took <= 3.5, took


async def test_o2_inbound_call_accepted_while_draining(monkeypatch):
    """A draining task stops dialing but serves a call the load balancer still routes to it before
    deregistration: the call ends at the call cap, before the deregistration delay does."""
    monkeypatch.setattr(ct, "set_protection", lambda en: {"protection": {}})
    t = new_task()
    async with live(t, monkeypatch) as (port, _):
        t.state = "DRAINING"
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert msg.get("state") == "DRAINING", "a DRAINING task refused an inbound call"
