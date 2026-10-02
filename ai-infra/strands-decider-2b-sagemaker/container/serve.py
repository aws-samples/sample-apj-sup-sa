"""Starts the Strands Decider server for SageMaker AI on port 8080 (or SAGEMAKER_BIND_TO_PORT).

The request body is the decider's own System One request, unchanged:

    {"state": ..., "questions": {id: {"type": "noul"|"choice"|"score", ...}}}

and the response is its System One response. The app is in app.py.

With DECIDER_WORKERS=1 the app runs in this process, so a fatal GPU error stops the container and SageMaker
replaces the instance. With more, each worker process holds its own model copy and listens on a loopback port,
and this process is a small front: it sends every request to the worker with the fewest requests in flight.
SageMaker reaches a container over a few kept-alive connections, so balancing by request (not by connection)
is what lets the copies overlap. The front restarts a worker that exits (after a fatal GPU error) and routes
around it while its model loads again.
"""
import asyncio
import contextlib
import multiprocessing as mp
import os
import sys

PORT = int(os.environ.get("SAGEMAKER_BIND_TO_PORT", "8080"))
WORKERS = int(os.environ.get("DECIDER_WORKERS", "1"))
WORKER_PORTS = [9001 + i for i in range(WORKERS)]


def worker(port: int, host: str) -> None:
    import uvicorn                     # imported here, so the front never loads torch
    import app

    app.prepare()                      # load and warm up before the port opens
    uvicorn.run(app.app, host=host, port=port, timeout_keep_alive=75, log_level="warning")


def front() -> None:
    import httpx
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    ctx = mp.get_context("spawn")
    procs = {p: ctx.Process(target=worker, args=(p, "127.0.0.1"), daemon=True) for p in WORKER_PORTS}
    up = {p: False for p in WORKER_PORTS}
    busy = {p: 0 for p in WORKER_PORTS}
    started = {"all": False}
    client = httpx.AsyncClient(timeout=httpx.Timeout(70.0),
                               limits=httpx.Limits(max_connections=256, max_keepalive_connections=64))

    async def watch():
        """Restart workers that exit, and track which ones answer /ping."""
        while True:
            for p, proc in list(procs.items()):
                if not proc.is_alive():
                    print(f"[decider] worker on :{p} exited with {proc.exitcode}; starting a new one", flush=True)
                    up[p] = False
                    procs[p] = ctx.Process(target=worker, args=(p, "127.0.0.1"), daemon=True)
                    procs[p].start()
                    continue
                try:
                    up[p] = (await client.get(f"http://127.0.0.1:{p}/ping", timeout=2)).status_code == 200
                except httpx.HTTPError:
                    up[p] = False
            started["all"] = started["all"] or all(up.values())
            await asyncio.sleep(2)

    async def ping(_request):
        # Healthy once every copy has loaded; afterwards while at least one answers.
        ok = started["all"] and any(up.values())
        return Response(status_code=200 if ok else 503)

    async def invocations(request):
        body = await request.body()
        ready = [p for p in WORKER_PORTS if up[p]]
        if not ready:
            return JSONResponse({"error": "no model worker is ready; retry"}, status_code=503)
        p = min(ready, key=busy.get)
        busy[p] += 1
        try:
            r = await client.post(f"http://127.0.0.1:{p}/invocations", content=body,
                                  headers={"content-type": request.headers.get("content-type", "application/json")})
        except httpx.TransportError:
            up[p] = False
            return JSONResponse({"error": "the model worker restarted; retry"}, status_code=503)
        finally:
            busy[p] -= 1
        return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        for proc in procs.values():
            proc.start()
        task = asyncio.get_running_loop().create_task(watch())
        yield
        task.cancel()
        for proc in procs.values():
            proc.terminate()
        await client.aclose()

    app = Starlette(routes=[Route("/ping", ping), Route("/invocations", invocations, methods=["POST"])],
                    lifespan=lifespan)
    # SageMaker reaches the container on its network interface, so it must listen on all of them.
    uvicorn.run(app, host="0.0.0.0", port=PORT, timeout_keep_alive=75, log_level="warning")  # nosec B104


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if WORKERS == 1:
        worker(PORT, "0.0.0.0")  # nosec B104
    else:
        front()
