"""Phase-2 calling-task tests (asyncio, fakeredis)."""
import fakeredis

import calling_task as ct

ARN = "arn:aws:ecs:us-east-1:111122223333:task/voice-cluster/taskdddd"


def new_task():
    t = ct.CallingTask(ARN, "127.0.0.1")
    t.db = fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    return t


# ---------------------------------------------------------------- N2: never recreate a deleted record
async def test_n2_draining_task_does_not_recreate_deleted_record():
    t = new_task()
    t.state = "COUNT_LOWERED"
    await t.db.hset(t.key, mapping={"arn": ARN, "state": "COUNT_LOWERED"})
    assert await t.publish() is True and await t.db.hget(t.key, "active_calls") == "0"
    await t.db.delete(t.key)  # the controller finished the removal
    assert await t.publish() is False
    assert not await t.db.exists(t.key), "publish() recreated a record the controller deleted"
    t.state = "STOPPING"
    await t.shutdown()
    assert not await t.db.exists(t.key), "shutdown() recreated a record the controller deleted"


async def test_n2_accepting_task_still_publishes():
    t = new_task()
    t.state = "ACCEPTING"
    assert await t.publish() is True
    assert await t.db.hget(t.key, "proc_state") == "STARTING"  # proc_state follows register_accepting
    assert await t.db.hget(t.key, "arn") == ARN


# ---------------------------------------------------------------- N4: task returns to ACCEPTING after abort
async def test_n4_task_resumes_after_abort(monkeypatch):
    import asyncio
    monkeypatch.setattr(ct, "DIAL_INTERVAL_SECONDS", 0.02)
    t = new_task()
    t.state = "ACCEPTING"
    await t.db.hset(t.key, mapping={"arn": ARN, "state": "DRAINING"})
    workers = [asyncio.create_task(t.watch_state()), asyncio.create_task(t.dialer())]
    await asyncio.sleep(1.2)
    assert t.state == "DRAINING" and t.dialer_stopped
    placed = t.placed_total
    await t.db.hset(t.key, "state", "ACCEPTING")  # what `controller.py abort` writes
    await asyncio.sleep(1.3)
    t.stopping.set()
    for w in workers:
        w.cancel()
    assert t.state == "ACCEPTING" and not t.dialer_stopped and t.placed_total > placed


async def test_n4_count_lowered_task_never_goes_back():
    import asyncio
    t = new_task()
    t.state = "COUNT_LOWERED"
    await t.db.hset(t.key, mapping={"arn": ARN, "state": "ACCEPTING"})
    w = asyncio.create_task(t.watch_state())
    await asyncio.sleep(0.2)
    t.stopping.set()
    w.cancel()
    assert t.state == "COUNT_LOWERED"


# ---------------------------------------------------------------- L4: ACCEPTING only after confirmed protection
class _Conn:
    def respond(self, status, text):
        return (int(status), text)


class _Req:
    def __init__(self, path):
        self.path = path
        self.headers = {"User-Agent": "ELB-HealthChecker/2.0"}


def test_l4_protection_ok_shapes():
    assert ct.protection_ok({"protection": {"ProtectionEnabled": True, "ExpirationDate": "x"}})
    assert ct.protection_ok({"protection": {}})
    assert not ct.protection_ok({"protection": {"ProtectionEnabled": False}})
    assert not ct.protection_ok({"failure": {"Reason": "DEPLOYMENT_BLOCKED"}})
    assert not ct.protection_ok({"error": {"Code": "ThrottlingException"}})
    assert not ct.protection_ok(None)


async def test_l4_unconfirmed_task_stays_starting_then_accepts_after_renewal(monkeypatch):
    import asyncio
    t = new_task()
    t.register_accepting = lambda: asyncio.sleep(0)
    assert (await t.health(_Conn(), _Req("/health")))[0] == 200
    assert t.state == "STARTING", "went ACCEPTING without confirmed protection"
    assert (await t.health(_Conn(), _Req("/ws")))[0] == 503
    # A renewal in STARTING succeeds -> the next health check makes it ACCEPTING.
    monkeypatch.setattr(ct, "RENEW_SECONDS", 100)
    monkeypatch.setattr(ct, "set_protection", lambda en: {"protection": {"ProtectionEnabled": True}})
    w = asyncio.create_task(t.protect_forever())
    await asyncio.sleep(0.1)
    t.stopping.set()
    await w
    assert t.protection_confirmed
    await t.health(_Conn(), _Req("/health"))
    assert t.state == "ACCEPTING"


async def test_l4_main_confirms_initial_protection(monkeypatch):
    seen = {}
    monkeypatch.setattr(ct, "task_metadata", lambda: (ARN, "127.0.0.1"))
    monkeypatch.setattr(ct, "set_protection", lambda en: {"protection": {"ProtectionEnabled": True}})

    async def fake_run(self):
        seen["confirmed"] = self.protection_confirmed
    monkeypatch.setattr(ct.CallingTask, "run", fake_run)
    await ct.main()
    assert seen == {"confirmed": True}


# ---------------------------------------------------------------- L5: initial protect retried with backoff
async def test_l5_initial_protect_retries_until_agent_ready(monkeypatch):
    monkeypatch.setattr(ct, "INITIAL_PROTECT_WINDOW_SECONDS", 10)
    calls = []

    def sp(en):
        calls.append(en)
        if len(calls) < 3:
            raise ConnectionRefusedError()
        return {"protection": {"ProtectionEnabled": True}}
    monkeypatch.setattr(ct, "set_protection", sp)
    assert await ct.initial_protect() is True and len(calls) == 3


async def test_l5_initial_protect_gives_up_within_window(monkeypatch):
    import time
    monkeypatch.setattr(ct, "INITIAL_PROTECT_WINDOW_SECONDS", 0.6)
    monkeypatch.setattr(ct, "set_protection", lambda en: {"failure": {"Reason": "DEPLOYMENT_BLOCKED"}})
    t0 = time.monotonic()
    assert await ct.initial_protect() is False
    assert time.monotonic() - t0 < 1.5


# ---------------------------------------------------------------- L6: short retry only for transient errors
def test_l6_retry_delay_bounds(monkeypatch):
    monkeypatch.setattr(ct, "RENEW_SECONDS", 300)
    delays = [ct.transient_retry_delay() for _ in range(200)]
    assert 10 <= min(delays) and max(delays) <= 30 and len({round(d, 3) for d in delays}) > 50
    monkeypatch.setattr(ct, "RENEW_SECONDS", 5)
    assert ct.transient_retry_delay() == 5


async def test_l6_deployment_blocked_waits_full_interval(monkeypatch):
    import asyncio
    monkeypatch.setattr(ct, "RENEW_SECONDS", 0.5)
    monkeypatch.setattr(ct, "TRANSIENT_RETRY_MIN_SECONDS", 0.02)
    monkeypatch.setattr(ct, "TRANSIENT_RETRY_MAX_SECONDS", 0.03)
    calls = []
    monkeypatch.setattr(ct, "set_protection",
                        lambda en: calls.append(en) or {"failure": {"Reason": "DEPLOYMENT_BLOCKED"}})
    t = new_task()
    t.state = "ACCEPTING"
    w = asyncio.create_task(t.protect_forever())
    await asyncio.sleep(0.3)
    t.stopping.set()
    await w
    assert len(calls) == 1, "a refusal (not transient) was retried early"


# ---------------------------------------------------------------- L7: Valkey timeouts, bounded shutdown
def test_l7_task_client_has_timeouts(monkeypatch):
    # redis-py 8.1 already defaults both timeouts to 5 s, so check the explicit, configurable setting.
    monkeypatch.setattr(ct, "VALKEY_TIMEOUT_SECONDS", 1.5, raising=False)
    t = ct.CallingTask(ARN, "127.0.0.1")
    kw = t.db.connection_pool.connection_kwargs
    assert kw["socket_timeout"] == 1.5 and kw["socket_connect_timeout"] == 1.5


def test_l7_controller_client_has_timeouts():
    # Note: equal to redis-py 8.1's own default (5 s), so this also passes on the original code; the
    # controller now sets it explicitly so a library upgrade can't silently remove it.
    import controller  # no `world` fixture here, so controller.db is the real (unconnected) client
    kw = controller.db.connection_pool.connection_kwargs
    assert kw["socket_timeout"] == 5 and kw["socket_connect_timeout"] == 5


async def test_l7_watch_state_survives_valkey_errors(monkeypatch):
    import asyncio

    import redis.asyncio as aioredis
    t = new_task()
    real_hget = t.db.hget
    fails = {"n": 2}

    async def flaky_hget(*a, **k):
        if fails["n"]:
            fails["n"] -= 1
            raise aioredis.TimeoutError("Timeout reading from socket")
        return await real_hget(*a, **k)
    t.db.hget = flaky_hget
    t.state = "ACCEPTING"
    await t.db.hset(t.key, mapping={"arn": ARN, "state": "DRAINING"})
    w = asyncio.create_task(t.watch_state())
    await asyncio.sleep(3.3)
    t.stopping.set()
    await asyncio.sleep(0)
    assert not w.done() or w.exception() is None, "watch_state died on a Valkey error"
    w.cancel()
    assert t.state == "DRAINING", "watch_state stopped following the controller after a Valkey error"


async def test_l7_shutdown_bounded_when_valkey_down():
    import asyncio
    import time

    class DownDB:
        async def hset(self, *a, **k):
            raise ConnectionError("valkey down")

        def pipeline(self, *a, **k):
            raise ConnectionError("valkey down")
    t = new_task()
    t.db = DownDB()
    t.state = "ACCEPTING"
    t0 = time.monotonic()
    await asyncio.wait_for(t.shutdown(), 3)
    assert time.monotonic() - t0 < 1


# ---------------------------------------------------------------- L8: concurrent, bounded call close
async def test_l8_stuck_closes_bounded(monkeypatch):
    import asyncio
    import time
    monkeypatch.setattr(ct, "CALL_CLOSE_TIMEOUT_SECONDS", 0.3)

    class StuckWS:
        async def close(self, code, reason):
            await asyncio.Event().wait()  # a caller that never completes the close handshake

    class BadWS:
        async def close(self, code, reason):
            raise ConnectionResetError()
    t = new_task()
    t.calls = {StuckWS() for _ in range(20)} | {BadWS()}
    t0 = time.monotonic()
    await t.shutdown()
    assert time.monotonic() - t0 < 1.0, "shutdown waited for stuck closes"


def test_l8_close_budget_leaves_room_for_stop_timeout():
    assert ct.CALL_CLOSE_TIMEOUT_SECONDS <= ct.STOP_TIMEOUT_SECONDS - 10
