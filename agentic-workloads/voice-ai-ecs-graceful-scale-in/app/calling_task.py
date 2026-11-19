"""Calling task: holds WebSocket "calls", protects itself from scale-in, and drains on request.

Each task protects itself through the Amazon ECS agent endpoint as soon as it starts, renews that
protection while it is ACCEPTING, and stops renewing once the controller marks it DRAINING. The
outbound dialer here is simulated: each placed call gets an unguessable call ID, and a simulated carrier
connects that call's media stream back to this task (`/ws?call=<id>`) after RING_SECONDS. A draining
task stops dialing but still accepts every /ws connection that reaches it: a call it accepts before
deregistration ends at the call cap, before the deregistration delay does. Only a starting or stopping
task refuses calls.
"""
import asyncio
import http
import http.client
import json
import os
import random
import secrets
import signal
import time
import urllib.parse

import redis.asyncio as redis
import websockets
from websockets.asyncio.server import serve

VALKEY_HOST = os.environ["VALKEY_HOST"]
PROTECT_MINUTES = int(os.environ.get("PROTECT_MINUTES", "15"))
RENEW_SECONDS = int(os.environ.get("RENEW_SECONDS", "300"))
CALL_CAP_SECONDS = int(os.environ.get("CALL_CAP_SECONDS", "600"))
RING_SECONDS = int(os.environ.get("RING_SECONDS", "20"))
# A placed call counts as pending for its ring time plus this grace, so the controller's drain step waits
# for a media stream that connects a little late.
PLACED_CALL_GRACE_SECONDS = float(os.environ.get("PLACED_CALL_GRACE_SECONDS", "10"))
DIAL_INTERVAL_SECONDS = float(os.environ.get("DIAL_INTERVAL_SECONDS", "2"))
ENABLE_TEST_HOOKS = os.environ.get("ENABLE_TEST_HOOKS") == "1"
# How long main() keeps retrying the first protect call before starting unconfirmed (see initial_protect).
INITIAL_PROTECT_WINDOW_SECONDS = float(os.environ.get("INITIAL_PROTECT_WINDOW_SECONDS", "60"))
# Errors from the agent endpoint that are worth retrying: connection and timeout errors, a broken HTTP
# exchange, or a non-JSON body.
AGENT_ERRORS = (OSError, ValueError, http.client.HTTPException)
# Jitter only (not security sensitive); SystemRandom avoids sharing state with other users of random.
_rng = random.SystemRandom()
# After a transient renewal error, retry after a random 10-30 s instead of a full RENEW_SECONDS: with
# 15-minute protection renewed every 5 minutes, a missed renewal leaves only two more attempts.
TRANSIENT_RETRY_MIN_SECONDS = float(os.environ.get("TRANSIENT_RETRY_MIN_SECONDS", "10"))
TRANSIENT_RETRY_MAX_SECONDS = float(os.environ.get("TRANSIENT_RETRY_MAX_SECONDS", "30"))


def transient_retry_delay():
    """Jittered short retry, never longer than the normal renewal interval."""
    return min(RENEW_SECONDS, _rng.uniform(TRANSIENT_RETRY_MIN_SECONDS, TRANSIENT_RETRY_MAX_SECONDS))


PORT = 8080
# Valkey calls fail after this long instead of hanging (connect and per-command read).
VALKEY_TIMEOUT_SECONDS = float(os.environ.get("VALKEY_TIMEOUT_SECONDS", "5"))
# The last state write on SIGTERM gets at most this long, so a hung Valkey can't hold up the exit.
STATE_FLUSH_TIMEOUT_SECONDS = float(os.environ.get("STATE_FLUSH_TIMEOUT_SECONDS", "2"))
# Closing calls on SIGTERM gets at most this long overall, leaving at least 10 s of the container's
# stopTimeout (STOP_TIMEOUT_SECONDS, set from the template) for the state flush and exit.
STOP_TIMEOUT_SECONDS = float(os.environ.get("STOP_TIMEOUT_SECONDS", "120"))
CALL_CLOSE_TIMEOUT_SECONDS = max(1.0, min(30.0, STOP_TIMEOUT_SECONDS - 10))


def log(event, **fields):
    print(json.dumps({"ts": round(time.time(), 3), "event": event, **fields}), flush=True)


def local_request(method, url, body=None, timeout=10):
    """Call an Amazon ECS endpoint inside the task (metadata or agent). Both are plain HTTP on a
    link-local address; any other scheme is refused rather than followed."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "http" or not u.hostname:
        raise ValueError(f"refusing non-http endpoint: {url}")
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, u.path or "/", body=data, headers={"Content-Type": "application/json"} if data else {})
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


def task_metadata():
    base = os.environ["ECS_CONTAINER_METADATA_URI_V4"]
    task = local_request("GET", f"{base}/task", timeout=5)
    container = local_request("GET", base, timeout=5)
    # Listen only on the task's own address (awsvpc network mode), not on every interface.
    return task["TaskARN"], container["Networks"][0]["IPv4Addresses"][0]


def set_protection(enabled):
    """PUT to the agent endpoint; the agent retries calls to the Amazon ECS API."""
    body = {"ProtectionEnabled": enabled}
    if enabled:
        body["ExpiresInMinutes"] = PROTECT_MINUTES
    return local_request("PUT", f"{os.environ['ECS_AGENT_URI']}/task-protection/v1/state", body)


def protection_ok(resp):
    """True if the agent confirmed protection: a "protection" object that isn't explicitly disabled.
    Failures ({"failure": ...}, for example DEPLOYMENT_BLOCKED) and errors ({"error": ...}) are not."""
    return isinstance(resp, dict) and isinstance(resp.get("protection"), dict) \
        and resp["protection"].get("ProtectionEnabled", True) is not False


class CallingTask:
    def __init__(self, arn, listen_ip):
        self.arn = arn
        self.listen_ip = listen_ip
        self.id = arn.rsplit("/", 1)[-1]
        self.key = f"task:{self.id}"
        self.db = redis.Redis(host=VALKEY_HOST, port=6379, ssl=True, decode_responses=True,
                              socket_timeout=VALKEY_TIMEOUT_SECONDS,
                              socket_connect_timeout=VALKEY_TIMEOUT_SECONDS)
        self.state = "STARTING"
        # The task's own lifecycle (STARTING, ACCEPTING, STOPPING). It is published as "proc_state"
        # because "state" belongs to the controller once the task is ACCEPTING.
        self.proc_state = "STARTING"
        self.active_calls = 0
        self.pending = {}  # call ID -> expiry, for placed calls whose media stream hasn't connected yet
        self.carrier = set()  # simulated carrier coroutines (one per placed call)
        self.placed_total = 0
        self.dialer_stopped = False
        # True while a protection PUT to the agent is outstanding. A PUT that started while ACCEPTING
        # can land after the controller's release, so drain-ready waits for it (renewal_stopped).
        self.renewal_in_flight = False
        # True once the agent has confirmed protection at least once. A task only becomes ACCEPTING
        # (routable work, eligible for the controller) after that, so it is never unprotected there.
        self.protection_confirmed = False
        self.calls = set()
        self.stopping = asyncio.Event()

    async def publish(self):
        """Report counters and the task's own lifecycle. The task never writes "state" here: after
        ACCEPTING, only the controller changes it, so a report can't overwrite a DRAINING decision.

        Once the controller has taken over (the task is past ACCEPTING), the controller may delete the
        record when the removal is done. The report then only updates a record that still exists, so it
        never recreates an orphaned hash outside the registry."""
        fields = {
            "arn": self.arn, "proc_state": self.proc_state, "active_calls": self.active_calls,
            "pending_calls": len(self.pending), "placed_total": self.placed_total,
            "dialer_stopped": int(self.dialer_stopped), "renewal_stopped": int(self.renewal_stopped()),
            "renewal_in_flight": int(self.renewal_in_flight), "updated_at": time.time()}
        if self.state in ("STARTING", "ACCEPTING"):
            await self.db.hset(self.key, mapping=fields)
            return True
        return await self.update_if_exists(fields)

    async def update_if_exists(self, fields):
        """HSET only if the key exists. WATCH makes the check and the write atomic: if the controller
        deletes the key in between, the transaction is discarded."""
        async with self.db.pipeline(transaction=True) as p:
            try:
                await p.watch(self.key)
                if not await p.exists(self.key):
                    return False
                p.multi()
                p.hset(self.key, mapping=fields)
                await p.execute()
                return True
            except redis.WatchError:
                return False

    def renewal_stopped(self):
        """No renewal will be sent again and none is outstanding. protect_forever checks the state
        and sets renewal_in_flight in the same event-loop step, so once this is True it stays True."""
        return self.state not in ("STARTING", "ACCEPTING") and not self.renewal_in_flight

    async def protect_forever(self):
        """Renew protection while ACCEPTING. A DRAINING task must not re-protect itself."""
        while not self.stopping.is_set():
            wait = RENEW_SECONDS
            if self.state in ("STARTING", "ACCEPTING"):
                self.renewal_in_flight = True
                try:
                    resp = await asyncio.to_thread(set_protection, True)
                    if protection_ok(resp):
                        self.protection_confirmed = True
                        log("protection_set", expires=resp["protection"].get("ExpirationDate"))
                    else:
                        # For example DEPLOYMENT_BLOCKED: the service has more protected tasks than
                        # its desired count, and Amazon ECS refuses every renewal until that clears.
                        log("protection_failed", response=resp)
                        if isinstance(resp, dict) and "error" in resp:  # ThrottlingException, RequestCanceled
                            wait = transient_retry_delay()
                except Exception as e:  # keep serving calls; alarm on this log line
                    log("protection_failed", error=repr(e))
                    wait = transient_retry_delay()
                finally:
                    self.renewal_in_flight = False
            try:
                await asyncio.wait_for(self.stopping.wait(), wait)
            except asyncio.TimeoutError:
                pass

    async def watch_state(self):
        """Follow the controller's instructions recorded in the state store."""
        while not self.stopping.is_set():
            try:
                stored = await self.db.hget(self.key, "state")
            except (redis.RedisError, OSError) as e:  # timed out or down: keep the last known state
                log("valkey_error", op="read_state", error=repr(e))
                await asyncio.sleep(1)
                continue
            if self.stopping.is_set():
                break  # shutdown() started while we were reading; keep its STOPPING view
            if stored in ("DRAINING", "UNPROTECTED", "COUNT_LOWERED") and stored != self.state:
                log("state_changed", old=self.state, new=stored)
                self.state = stored
            elif stored == "ACCEPTING" and self.state in ("DRAINING", "UNPROTECTED"):
                # `controller.py abort` re-protected this task and handed it back: serve, dial and
                # renew again. A task whose count was lowered (COUNT_LOWERED) never goes back.
                log("state_changed", old=self.state, new=stored, reason="removal_aborted")
                self.state = "ACCEPTING"
                self.dialer_stopped = False
            if self.state != "ACCEPTING" and self.state != "STARTING" and not self.dialer_stopped:
                self.dialer_stopped = True
                log("dialer_stopped", placed_total=self.placed_total, pending=len(self.pending))
            now = time.time()
            self.pending = {c: exp for c, exp in self.pending.items() if exp > now}
            try:
                await self.publish()
            except (redis.RedisError, OSError) as e:
                log("valkey_error", op="publish", error=repr(e))
            await asyncio.sleep(1)

    async def dialer(self):
        """Simulated outbound dialer: places a call every DIAL_INTERVAL_SECONDS while ACCEPTING."""
        while not self.stopping.is_set():
            if self.state == "ACCEPTING":
                self.place_call()
            await asyncio.sleep(DIAL_INTERVAL_SECONDS)

    def place_call(self):
        """Record a placed call under an unguessable ID: a real carrier carries it in the media stream URL
        (`/ws?call=<id>`). The call counts as pending until its stream connects or its ring window ends."""
        call_id = secrets.token_urlsafe(16)
        self.pending[call_id] = time.time() + RING_SECONDS + PLACED_CALL_GRACE_SECONDS
        self.placed_total += 1
        answer = asyncio.ensure_future(self.simulated_answer(call_id))
        self.carrier.add(answer)
        answer.add_done_callback(self.carrier.discard)
        return call_id

    async def simulated_answer(self, call_id):
        """Simulated carrier: the callee answers after RING_SECONDS and the carrier connects the call's
        media stream to this task, then hangs up at once. This exercises the same /ws admission path as a
        real stream, including while the task is draining."""
        try:
            await asyncio.wait_for(self.stopping.wait(), RING_SECONDS)
            return  # the task is stopping
        except asyncio.TimeoutError:
            pass
        if call_id not in self.pending:
            return
        try:
            async with websockets.connect(f"ws://{self.listen_ip}:{PORT}/ws?call={call_id}",
                                          open_timeout=5, close_timeout=2) as ws:
                await asyncio.wait_for(ws.recv(), 5)
        except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as e:
            log("placed_call_connect_failed", call=call_id[:6], error=repr(e))

    def admit(self, request):
        """Admission for a new /ws connection. Returns None to accept, or the refusal reason.
        A starting task refuses: it isn't confirmed protected yet. A stopping task refuses: it is closing
        its calls, and the load balancer has already deregistered it. Every other state accepts, including
        a draining task. The dispatcher stops assigning a draining task work and its dialer stops, but a
        connection the load balancer still routes to it before deregistration is safe to serve: it ends at
        the call cap, before the deregistration delay does. Refusing it would fail the call unless something
        upstream retries it on another task."""
        if self.state == "STARTING":
            return "starting"
        if self.state == "STOPPING":
            return "stopping"
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.path).query)
        call_id = (query.get("call") or [None])[0]
        # A placed call stops counting as pending once its media stream connects.
        if call_id and self.pending.pop(call_id, None) is not None:
            log("placed_call_connected", call=call_id[:6], state=self.state)
        return None

    async def health(self, connection, request):
        if request.path.startswith("/health"):
            if ENABLE_TEST_HOOKS and await self.db.get(f"fail_health:{self.id}"):
                return connection.respond(http.HTTPStatus.SERVICE_UNAVAILABLE, "unhealthy\n")
            if (self.state == "STARTING" and self.protection_confirmed
                    and "ELB-HealthChecker" in request.headers.get("User-Agent", "")):
                # The first load balancer health check this task answers is the earliest point
                # it can be routed traffic, so this is when it starts ACCEPTING, but only once its
                # protection is confirmed. Until then it stays STARTING: /health is 200, /ws is 503, and
                # the controller treats it as starting. Register in the background so a slow state
                # store never delays the health check response.
                self.state = "ACCEPTING"
                asyncio.ensure_future(self.register_accepting())
            return connection.respond(http.HTTPStatus.OK, "ok\n")
        if not request.path.startswith("/ws"):
            return connection.respond(http.HTTPStatus.NOT_FOUND, "not found\n")
        refused = self.admit(request)
        if refused:
            # Starting or stopping. Health checks above stay 200 in every state, so Amazon ECS doesn't
            # replace a draining task that is finishing its calls.
            log("call_refused", reason=refused, state=self.state)
            return connection.respond(http.HTTPStatus.SERVICE_UNAVAILABLE, "not accepting calls\n")
        return None

    async def register_accepting(self):
        # The only time the task writes "state": ACCEPTING is the hand-off to the controller.
        self.proc_state = "ACCEPTING"
        await self.db.hset(self.key, mapping={"arn": self.arn, "state": "ACCEPTING", "proc_state": "ACCEPTING"})
        await self.db.sadd("tasks", self.id)
        log("accepting")

    async def call(self, ws):
        """One WebSocket per call. The server enforces the call cap."""
        self.active_calls += 1
        self.calls.add(ws)
        started = time.time()
        log("call_started", active_calls=self.active_calls, state=self.state)
        try:
            await ws.send(json.dumps({"task": self.id, "state": self.state}))
            while time.time() - started < CALL_CAP_SECONDS:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=1)
                except asyncio.TimeoutError:
                    await ws.send("tick")
            await ws.close(1000, "call cap reached")
        except websockets.ConnectionClosed:
            pass  # the caller hung up; this is how most calls end
        except Exception as e:
            log("call_error", error=repr(e))
        finally:
            self.active_calls -= 1
            self.calls.discard(ws)
            log("call_ended", seconds=round(time.time() - started, 1), active_calls=self.active_calls)

    async def close_calls(self):
        """Close every call at once, bounded by CALL_CLOSE_TIMEOUT_SECONDS overall. Each close can take
        up to websockets' close_timeout (10 s) when a caller doesn't answer, so closing one after
        another could outlast stopTimeout with a dozen stuck calls."""
        if not self.calls:
            return
        closes = [asyncio.ensure_future(ws.close(1001, "task stopping")) for ws in list(self.calls)]
        done, pending = await asyncio.wait(closes, timeout=CALL_CLOSE_TIMEOUT_SECONDS)
        for f in pending:
            f.cancel()
        failed = sum(1 for f in done if f.exception() is not None)
        log("calls_closed", closed=len(done) - failed, failed=failed, timed_out=len(pending))

    async def shutdown(self):
        log("sigterm", active_calls=self.active_calls, state=self.state)
        self.state = "STOPPING"
        self.proc_state = "STOPPING"
        self.stopping.set()
        await self.close_calls()
        # publish() writes proc_state=STOPPING. "state" is left as the controller wrote it, so an
        # interrupted removal (for example COUNT_LOWERED) is still visible to a restarted controller.
        # Bounded: if Valkey hangs or is down, give up after STATE_FLUSH_TIMEOUT_SECONDS and exit anyway.
        try:
            await asyncio.wait_for(self.publish(), STATE_FLUSH_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, redis.RedisError, OSError) as e:
            log("state_flush_failed", error=repr(e))
        log("exit")

    async def run(self):
        loop = asyncio.get_running_loop()
        done = asyncio.Event()

        def on_sigterm():
            asyncio.ensure_future(self.shutdown()).add_done_callback(lambda _: done.set())

        loop.add_signal_handler(signal.SIGTERM, on_sigterm)
        # Listen first, so health checks are answered while the state store connection warms up.
        async with serve(self.call, self.listen_ip, PORT, process_request=self.health):
            log("listening", port=PORT, task=self.id)
            # No "state" is written here. A task with no state is still starting, and ACCEPTING is
            # the first state it records, so a slow first write can't overwrite a later one.
            await self.db.hset(self.key, "arn", self.arn)
            await self.db.sadd("tasks", self.id)
            workers = [asyncio.create_task(c) for c in (self.protect_forever(), self.watch_state(), self.dialer())]
            await done.wait()
        for w in workers:
            w.cancel()
        for a in list(self.carrier):
            a.cancel()


async def initial_protect():
    """Protect before serving. The agent endpoint may not be ready at container start, so retry with
    exponential backoff and jitter (0.5 s doubling to 8 s) for up to INITIAL_PROTECT_WINDOW_SECONDS.
    Returns True once protection is confirmed. On False the task still starts, but stays STARTING
    (not routable work) and protect_forever keeps trying."""
    deadline = time.monotonic() + INITIAL_PROTECT_WINDOW_SECONDS
    delay, attempt = 0.5, 0
    while True:
        attempt += 1
        try:
            resp = await asyncio.to_thread(set_protection, True)
        except AGENT_ERRORS as e:  # for example ConnectionRefusedError while the agent starts
            resp = {"error": repr(e)}
        if protection_ok(resp):
            log("protection_initial", response=resp, confirmed=True, attempts=attempt)
            return True
        log("protection_initial_failed", response=resp, attempt=attempt)
        if time.monotonic() + delay > deadline:
            log("protection_initial_gave_up", attempts=attempt, window_seconds=INITIAL_PROTECT_WINDOW_SECONDS)
            return False
        await asyncio.sleep(delay * _rng.uniform(0.5, 1.0))
        delay = min(delay * 2, 8)


async def main():
    arn, listen_ip = await asyncio.to_thread(task_metadata)
    task = CallingTask(arn, listen_ip)
    # Protect before the task can be reached, so it is never eligible for scale-in while starting.
    task.protection_confirmed = await initial_protect()
    await task.run()


if __name__ == "__main__":
    asyncio.run(main())
