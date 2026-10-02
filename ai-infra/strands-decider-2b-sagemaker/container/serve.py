"""Starts the Strands Decider server for SageMaker AI on port 8080 (or SAGEMAKER_BIND_TO_PORT).

The request body is the decider's own System One request, unchanged:

    {"state": ..., "questions": {id: {"type": "noul"|"choice"|"score", ...}}}

and the response is its System One response. The app is in app.py. This launcher starts DECIDER_WORKERS
processes, each with its own model copy and its own listening socket on the same port (SO_REUSEPORT), so the
kernel spreads connections across them; a shared socket would let one process accept a burst of connections
while the others sit idle. A worker opens its socket only once its model is loaded and warmed up, and one that
exits (after a fatal GPU error) is started again. With one worker the server runs in this process, so a fatal
error stops the container and SageMaker replaces the instance.
"""
import multiprocessing as mp
import os
import signal
import socket
import sys
import time

PORT = int(os.environ.get("SAGEMAKER_BIND_TO_PORT", "8080"))
WORKERS = int(os.environ.get("DECIDER_WORKERS", "1"))


def worker() -> None:
    import uvicorn                     # imported here, so the supervisor never loads torch
    import app

    app.prepare()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    # SageMaker reaches the container on its network interface, so it must listen on all of them.
    sock.bind(("0.0.0.0", PORT))  # nosec B104
    sock.listen(1024)
    config = uvicorn.Config(app.app, timeout_keep_alive=75, log_level="warning")
    uvicorn.Server(config).run(sockets=[sock])


def supervise() -> None:
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=worker, daemon=True) for _ in range(WORKERS)]
    for p in procs:
        p.start()

    def stop(*_):
        for p in procs:
            p.terminate()
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        time.sleep(2)
        for i, p in enumerate(procs):
            if not p.is_alive():
                print(f"[decider] worker {p.pid} exited with {p.exitcode}; starting a new one", flush=True)
                procs[i] = ctx.Process(target=worker, daemon=True)
                procs[i].start()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    worker() if WORKERS == 1 else supervise()
