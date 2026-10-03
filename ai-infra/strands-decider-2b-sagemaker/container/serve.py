"""Starts the Strands Decider server for SageMaker AI on port 8080 (or SAGEMAKER_BIND_TO_PORT).

The request body is the decider's own System One request, unchanged (at most 32 KB, 64 questions and 64
options per question; see app.py):

    {"state": ..., "questions": {id: {"type": "noul"|"choice"|"score", ...}}}

and the response is its System One response. The app is in app.py.

With DECIDER_WORKERS=1 the app runs in this process, so a fatal GPU error stops the container and SageMaker
replaces the instance. With more, each worker process holds its own model copy and listens on a loopback port,
and this process is a small front: it sends every request to the worker with the fewest requests in flight.
SageMaker reaches a container over a few kept-alive connections, so balancing by request (not by connection)
is what lets the copies overlap. The front restarts a worker that exits (after a fatal GPU error) and routes
around it while its model loads again; a request that reaches a worker that is exiting is sent to another
one. A worker that exits before it ever loaded, or does not load within
LOAD_S, stops the container instead, as a single worker would: the next copy would fail the same way.
"""
import asyncio
import contextlib
import dataclasses
import multiprocessing as mp
import os
import signal
import sys
import time

PORT = int(os.environ.get("SAGEMAKER_BIND_TO_PORT", "8080"))
WORKERS = int(os.environ.get("DECIDER_WORKERS", "1"))
STUCK_S = 30      # a worker that served, then fails /ping this long, is replaced
KILL_S = 10       # grace after SIGTERM before SIGKILL (a hung CUDA call never lets uvicorn finish)
LOAD_S = 900      # a worker that has not answered /ping this long after it started stops the container
MAX_BODY_BYTES = int(os.environ.get("DECIDER_MAX_BODY_BYTES", str(32 * 1024)))   # the same cap as app.py


def worker_ports() -> list[int]:
    """Loopback ports for the workers: from SAGEMAKER_SAFE_PORT_RANGE when SageMaker sets it, never PORT."""
    lo, hi = 9001, 9100
    if os.environ.get("SAGEMAKER_SAFE_PORT_RANGE"):
        lo, hi = (int(x) for x in os.environ["SAGEMAKER_SAFE_PORT_RANGE"].split("-"))
    ports = [p for p in range(lo, hi + 1) if p != PORT][:WORKERS]
    if not 1 <= WORKERS <= len(ports):
        raise SystemExit(f"DECIDER_WORKERS={WORKERS}: must be between 1 and {len(ports)} (free ports {lo}-{hi})")
    return ports


def worker(port: int, host: str) -> None:
    # Until uvicorn installs its own handler, SIGTERM must still stop a load: as PID 1 (one worker, no front)
    # the process would otherwise ignore it, and SageMaker would wait out its grace period.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    import uvicorn                     # imported here, so the front never loads torch
    import app

    app.prepare()                      # load and warm up before the port opens
    uvicorn.run(app.app, host=host, port=port, timeout_keep_alive=75, log_level="warning")


async def read_capped(request) -> bytes | None:
    """The request body, or None once it is larger than MAX_BODY_BYTES (without reading the rest)."""
    size = request.headers.get("content-length", "")
    if size.isdigit() and int(size) > MAX_BODY_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


@dataclasses.dataclass
class Worker:
    """One model process behind the front, and what the front knows about it."""
    port: int
    proc: mp.Process = None
    up: bool = False
    ever_up: bool = False
    busy: int = 0
    down_since: float | None = None
    terminated_at: float | None = None
    started_at: float = 0.0

    def start(self, ctx) -> None:
        self.proc = ctx.Process(target=worker, args=(self.port, "127.0.0.1"), daemon=True)
        self.proc.start()
        self.up = self.ever_up = False
        self.busy, self.down_since, self.terminated_at = 0, None, None
        self.started_at = time.monotonic()


def front() -> None:
    import httpx
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    ctx = mp.get_context("spawn")
    workers = [Worker(p) for p in worker_ports()]
    healthy_once = False
    # Short pool and connect waits: a busy front or an unreachable worker answers well inside SageMaker's 60 s.
    client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=2.0, pool=1.0),
                               limits=httpx.Limits(max_connections=256, max_keepalive_connections=64))
    # Health probes get their own small pool, so a full request pool never makes a healthy worker look down.
    probe_client = httpx.AsyncClient(timeout=httpx.Timeout(2.0),
                                     limits=httpx.Limits(max_connections=2 * len(workers)))

    async def watch():
        """Restart workers that exit or stay unhealthy, and track which ones answer /ping."""
        while True:
            try:
                await check()
            except Exception as e:  # noqa: BLE001  the watcher must keep running
                print(f"[decider] watcher error: {type(e).__name__}", flush=True)
            await asyncio.sleep(2)

    async def probe(w) -> bool:
        try:
            return (await probe_client.get(f"http://127.0.0.1:{w.port}/ping")).status_code == 200
        except httpx.HTTPError:
            return False

    async def check():
        nonlocal healthy_once
        now = time.monotonic()
        probed = []
        for w in workers:
            if w.proc is None:                 # not started: the others wait until the first has loaded, so they
                if workers[0].ever_up:         # reuse the Triton kernels it compiled instead of all compiling them
                    w.start(ctx)
                continue
            if not w.proc.is_alive():
                if not w.ever_up:              # it never loaded (bad config, unreadable artifact, hung load): the
                    print(f"[decider] worker on :{w.port} exited with {w.proc.exitcode} before it loaded; "
                          "stopping the container", flush=True)     # next copy would fail the same way
                    for x in workers:
                        if x.proc is not None:
                            x.proc.kill()
                    os._exit(1)
                print(f"[decider] worker on :{w.port} exited with {w.proc.exitcode}; starting a new one", flush=True)
                w.start(ctx)
                continue
            if w.terminated_at is not None:            # stopping: escalate if SIGTERM did not end it
                if now - w.terminated_at > KILL_S:
                    w.proc.kill()
                continue
            probed.append(w)
        # All at once, so one slow worker does not delay what the others report.
        for w, up in zip(probed, await asyncio.gather(*(probe(w) for w in probed))):
            w.up = up
            w.ever_up = w.ever_up or w.up
            w.down_since = None if w.up else (w.down_since or now)
            if w.ever_up and w.down_since and now - w.down_since > STUCK_S:
                print(f"[decider] worker on :{w.port} unhealthy for {STUCK_S} s; replacing it", flush=True)
                w.up, w.terminated_at = False, now
                w.proc.terminate()
            elif not w.ever_up and now - w.started_at > LOAD_S:
                print(f"[decider] worker on :{w.port} did not load within {LOAD_S} s; stopping it", flush=True)
                w.terminated_at = now
                w.proc.terminate()
        healthy_once = healthy_once or all(w.up for w in workers)

    async def ping(_request):
        # Healthy once every copy has loaded; afterwards while at least one answers.
        return Response(status_code=200 if healthy_once and any(w.up for w in workers) else 503)

    async def invocations(request):
        body = await read_capped(request)
        if body is None:
            return JSONResponse({"error": f"request body is larger than {MAX_BODY_BYTES:,} bytes; "
                                          "send a shorter state or fewer options"}, status_code=413)
        headers = {"content-type": request.headers.get("content-type", "application/json")}
        tried = set()
        while True:
            ready = [w for w in workers if w.up and w.port not in tried]
            if not ready:
                return JSONResponse({"error": "no model worker is ready; retry"}, status_code=503)
            w = min(ready, key=lambda x: x.busy)
            tried.add(w.port)
            proc = w.proc                              # the count belongs to this process, not to a replacement
            w.busy += 1
            try:
                r = await client.post(f"http://127.0.0.1:{w.port}/invocations", content=body, headers=headers)
            except httpx.PoolTimeout:                  # the front is at capacity; no worker is at fault
                return JSONResponse({"error": "too many requests in flight; retry"}, status_code=503)
            except (httpx.ConnectError, httpx.ConnectTimeout):   # never delivered (exited or restarting):
                w.up = False                                       # try another worker
                continue
            except httpx.TimeoutException:             # stuck: route around it until /ping says it is healthy
                w.up = False
                return JSONResponse({"error": "the model worker did not answer within 70 s"}, status_code=504)
            except httpx.TransportError:               # it failed while handling this request: do not pass the
                w.up = False                           # same request on to the other workers
                return JSONResponse({"error": "the model worker failed during the request; retry"}, status_code=503)
            finally:
                if w.proc is proc:
                    w.busy -= 1
            if r.status_code == 503 and r.headers.get("x-decider-worker") == "restarting":
                w.up = False                           # its GPU broke and it is about to exit: try another worker
                continue
            return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        workers[0].start(ctx)                          # the watcher starts the rest once this one has loaded
        task = asyncio.get_running_loop().create_task(watch())
        yield
        task.cancel()
        for w in workers:
            if w.proc is not None:
                w.proc.kill()
        await client.aclose()
        await probe_client.aclose()

    app = Starlette(routes=[Route("/ping", ping), Route("/invocations", invocations, methods=["POST"])],
                    lifespan=lifespan)
    # SageMaker reaches the container on its network interface, so it must listen on all of them.
    uvicorn.run(app, host="0.0.0.0", port=PORT, timeout_keep_alive=75, log_level="warning")  # nosec B104


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if WORKERS < 1:
        raise SystemExit(f"DECIDER_WORKERS={WORKERS}: must be at least 1")
    if WORKERS == 1:
        worker(PORT, "0.0.0.0")  # nosec B104
    else:
        front()
