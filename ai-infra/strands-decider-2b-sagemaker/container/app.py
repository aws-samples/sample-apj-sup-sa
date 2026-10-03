"""The FastAPI app each worker process serves: GET /ping and POST /invocations.

`serve.py` starts it.

The request and response formats are described in serve.py. The engine is the published
``strands-decider`` package; this file adds the SageMaker contract, request limits, precision
selection and kernel warm-up.

Model files come from the S3 artifact SageMaker mounts read-only at /opt/ml/model:
    /opt/ml/model/decider/   the Hub repo StrandsAgents/strands-decider-2B-hobson-v19
    /opt/ml/model/hf/        a Hugging Face cache holding Qwen/Qwen3.5-2B-Base at a pinned revision
The container runs with network isolation, so the image sets HF_HUB_OFFLINE=1.

Settings (environment variables):
    DECIDER_WORKERS  model processes sharing the GPU (default 1). The engine answers one request
                     at a time, so extra processes let requests overlap on the GPU.
    DECIDER_DTYPE    auto (bf16 where the GPU has it, else fp32), bfloat16 or float32
    DECIDER_FLA      auto, on or off: the Triton kernels for the Gated DeltaNet layers
    DECIDER_DEVICE   cuda, or cpu for a local smoke test
    DECIDER_REQUEST_DEADLINE  seconds a request may wait for the model before it gets 503 (default 50)
    DECIDER_MAX_QUESTIONS     questions allowed in one request (default 64), so one request cannot run for long
    DECIDER_MAX_OPTIONS       options allowed in one question (default 64)
    DECIDER_MAX_BODY_BYTES    request body size limit (default 32 KiB), enforced by serve.py and here
    DECIDER_HANG_SECONDS      one request running this long marks the worker stuck: /ping turns 503 (default 50);
                              keep it at or above DECIDER_REQUEST_DEADLINE
    DECIDER_MODEL_DIR         the decider's model files (default /opt/ml/model/decider)

A worker whose GPU context breaks (a sticky CUDA error) exits. With several workers serve.py starts a new one;
with one, the container stops and SageMaker replaces the instance.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

MODEL_DIR = os.environ.get("DECIDER_MODEL_DIR", "/opt/ml/model/decider")
DEVICE = os.environ.get("DECIDER_DEVICE", "cuda")
# Limits that keep one request short, since a worker runs one at a time: the body (serve.MAX_BODY_BYTES, an upper
# bound on the tokens it can hold; benchmark requests are under 2 KB) and the options in one question.
MAX_OPTIONS = int(os.environ.get("DECIDER_MAX_OPTIONS", "64"))
# One request this long means the engine is stuck: /ping turns 503. Kept under SageMaker's 60 s invocation limit
# (a request at the limits above takes a few seconds), so the front stops routing to it before callers time out.
HANG_S = float(os.environ.get("DECIDER_HANG_SECONDS", "50"))
# SageMaker gives a real-time invocation 60 s. A request that has waited this long for the model
# (it is busy with earlier ones) is refused with 503 rather than run for a caller that has gone.
DEADLINE_S = float(os.environ.get("DECIDER_REQUEST_DEADLINE", "50"))
MAX_QUESTIONS = int(os.environ.get("DECIDER_MAX_QUESTIONS", "64"))
# flash-linear-attention (fla) gives the Gated DeltaNet layers fast Triton kernels on GPU.
# Transformers picks it at import time whenever the package imports, so it has to be hidden
# before the model code loads wherever it must not run: on CPU (Triton needs a GPU), or when
# DECIDER_FLA=off. Transformers then uses its reference PyTorch path, which gives the same answers.
_FLA = os.environ.get("DECIDER_FLA", "auto")
USE_FLA = _FLA == "on" or (_FLA == "auto" and DEVICE == "cuda")
if not USE_FLA:
    sys.modules["fla"] = None  # type: ignore[assignment]  # makes `import fla` raise ImportError

import torch  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from strands_decider.infer import EngineConfig, SystemOneEngine  # noqa: E402
from strands_decider.modeling import StrandsDeciderModel  # noqa: E402
from strands_decider.schema import SystemOneRequest  # noqa: E402

from serve import MAX_BODY_BYTES, read_capped  # noqa: E402  (the same cap and capped body read as the front)

ENGINE: SystemOneEngine | None = None    # set by prepare() before this worker opens its socket
BUSY_SINCE: float | None = None          # when the engine thread started its current request
UNHEALTHY: str | None = None             # the GPU context is broken; this worker is about to exit
# The engine is synchronous and not thread-safe: one thread runs it, taking requests in arrival order.
RUNNER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="engine")
# Errors after which every CUDA call in this process fails. A transient failure such as an allocation that
# did not fit, or a Triton launch that was refused, is not among them: the synchronize() probe below decides.
_STICKY = ("illegal memory access", "device-side assert", "unspecified launch failure", "misaligned address",
           "illegal instruction", "uncorrectable ECC")


def _gpu_broken(e: Exception) -> bool:
    """True when the CUDA context is unusable: a known sticky error, or the device no longer synchronises."""
    if any(m in str(e) for m in _STICKY):   # any CUDA error, AcceleratorError included, can be transient
        return True
    if DEVICE != "cuda":
        return False
    try:
        torch.cuda.synchronize()       # an earlier asynchronous fault surfaces here
    except Exception:  # noqa: BLE001
        return True
    return False


def pick_dtype() -> torch.dtype:
    """bf16 where the GPU supports it natively, else fp32.

    The checkpoint is bf16. A T4 (compute capability 7.5) has no native bf16, and fp16 is not
    a safe stand-in: the Gated DeltaNet layers can overflow fp16's range. fp32 is exact, and the
    2B torso still fits a 16 GB T4 (about 7 GiB of weights).
    """
    choice = os.environ.get("DECIDER_DTYPE", "auto")
    allowed = {"bfloat16": torch.bfloat16, "float32": torch.float32}
    if choice != "auto" and choice not in allowed:
        raise SystemExit(f"DECIDER_DTYPE={choice!r} is not one of auto, bfloat16, float32")
    if choice != "auto":
        return allowed[choice]
    if DEVICE == "cpu":
        return torch.float32
    # including_emulation=False: recent torch reports bf16 as "supported" on a T4 through
    # slow emulation, which is not what we want.
    return torch.bfloat16 if torch.cuda.is_bf16_supported(including_emulation=False) else torch.float32


def load_engine() -> SystemOneEngine:
    roots = {Path(MODEL_DIR), Path(os.environ.get("HF_HOME", "/opt/ml/model/hf"))}   # the model files and the HF cache
    unreadable = [str(f) for r in roots for f in r.rglob("*") if f.is_file() and not os.access(f, os.R_OK)]
    if unreadable:
        raise SystemExit(f"the server runs as uid {os.getuid()} and cannot read {len(unreadable)} model files, "
                         f"for example {unreadable[0]}")
    dtype = pick_dtype()
    # The engine moves the bf16 model to the device; casting afterwards does the fp32 conversion on the GPU,
    # so the host never holds a 7 GiB fp32 copy and half as many bytes cross PCIe.
    engine = SystemOneEngine(StrandsDeciderModel.load(MODEL_DIR),
                             EngineConfig(device=DEVICE, use_prefix_cache=True,
                                          model_name="strands-decider-2B-hobson-v19"))
    if dtype != next(engine.model.torso.parameters()).dtype:
        with torch.inference_mode():
            engine.model.torso.to(dtype)
    return engine


def warm_up(engine: SystemOneEngine) -> None:
    """Compile the Triton kernels before the first real request.

    Without this the first request pays the compile: about 100 s on a T4 and 35 s on an L4,
    past SageMaker's 60 s invocation limit. Short and long states cover the kernel variants, and both the
    several-question path (shared prefix cache) and the one-question path (no cache) are exercised.
    """
    three = {"a": {"type": "noul", "instructions": "Is this urgent?"},
             "b": {"type": "choice", "instructions": "Which team?", "criteria": {"x": "", "y": "", "z": ""}},
             "c": {"type": "score", "instructions": "How bad is it?", "criteria": ["low", "mid", "high"]}}
    one = {"best": {"type": "choice", "instructions": "Which option fits best?",
                    "criteria": {k: f"option {k}" for k in "ABCDEFGH"}}}
    for questions in (three, one):
        for state in ("Short message.", "A longer support message about a billing problem. " * 60):
            engine.evaluate(SystemOneRequest.model_validate({"state": state, "questions": questions}))


def prepare() -> None:
    """Load and warm up the model. serve.py calls this before the worker opens its socket."""
    global ENGINE
    if ENGINE is not None:
        return
    t0 = time.time()
    engine = load_engine()
    warm_up(engine)
    ENGINE = engine
    gpu = (f"{torch.cuda.get_device_name(0)}, {torch.cuda.memory_allocated() / 2**30:.1f} GiB in this worker"
           if DEVICE == "cuda" else "cpu")
    print(f"[decider] worker {os.getpid()} ready in {time.time() - t0:.0f} s on {gpu}, "
          f"{next(engine.model.torso.parameters()).dtype}, fla kernels {'on' if USE_FLA else 'off'}", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    prepare()                          # a no-op when serve.py has already loaded the model
    yield


app = FastAPI(title="strands-decider on SageMaker", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/ping")
async def ping() -> Response:          # async: never waits behind requests queued for the engine
    stuck = BUSY_SINCE is not None and time.monotonic() - BUSY_SINCE > HANG_S
    return Response(status_code=200 if UNHEALTHY is None and not stuck else 503)


def _arrived(request: Request) -> float:
    """When the front first received this request, so a retry on another worker keeps its original deadline.

    The front passes its time.monotonic(), a clock every process on the host shares; a missing or implausible value
    means the request came straight to this worker.
    """
    now = time.monotonic()
    try:
        t = float(request.headers.get("x-decider-arrived", ""))
    except ValueError:
        return now
    return t if now - 120 <= t <= now else now


@app.post("/invocations")
async def invocations(request: Request) -> JSONResponse:
    arrived = _arrived(request)
    raw = await read_capped(request)
    if raw is None:
        return JSONResponse({"error": f"request body is larger than {MAX_BODY_BYTES:,} bytes; "
                                      "send a shorter state or fewer options"}, status_code=413)
    try:
        req = SystemOneRequest.model_validate_json(raw)
    except ValidationError as e:
        # e.json() leaves out the raw input, which may not be serialisable (or may be large).
        detail = json.loads(e.json(include_url=False, include_input=False))
        return JSONResponse({"error": "invalid request", "detail": detail}, status_code=400)
    if len(req.questions) > MAX_QUESTIONS:
        return JSONResponse({"error": f"at most {MAX_QUESTIONS} questions per request"}, status_code=400)
    if any(len(getattr(q, "criteria", None) or ()) > MAX_OPTIONS for q in req.questions.values()):
        return JSONResponse({"error": f"at most {MAX_OPTIONS} options per question"}, status_code=400)
    return await asyncio.get_running_loop().run_in_executor(RUNNER, _evaluate, req, arrived)


def _evaluate(req: SystemOneRequest, arrived: float) -> Response:
    global BUSY_SINCE
    BUSY_SINCE = time.monotonic()
    try:
        return _run(req, arrived)
    finally:
        BUSY_SINCE = None


def _run(req: SystemOneRequest, arrived: float) -> Response:
    global UNHEALTHY
    if UNHEALTHY is not None:
        # The header tells the front (serve.py) to send the request to another worker instead.
        return JSONResponse({"error": "worker is restarting; retry"}, status_code=503,
                            headers={"x-decider-worker": "restarting"})
    if time.monotonic() - arrived > DEADLINE_S:   # the caller's 60 s are nearly gone: do not start
        return JSONResponse({"error": f"busy: not started within {DEADLINE_S:.0f} s; retry later"}, status_code=503)
    try:
        t0 = time.perf_counter()
        try:
            result = ENGINE.evaluate(req)
        except ValidationError:                   # the engine built an invalid answer: our fault, not the caller's
            raise
        except ValueError as e:                   # e.g. a malformed option permutation: caller error
            return JSONResponse({"error": str(e)}, status_code=400)
        payload = result.model_dump()
        payload["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        # Strict JSON: a NaN would mean a numerical fault on our side, so it becomes a 500, not a broken body.
        return Response(json.dumps(payload, allow_nan=False), media_type="application/json")
    except Exception as e:  # noqa: BLE001       a server-side fault: log it, report 500
        # The exception type and where it happened, not its message: messages can quote request text.
        print(f"[decider] {type(e).__name__}\n" + "".join(traceback.format_tb(e.__traceback__)), flush=True)
        if _gpu_broken(e):
            UNHEALTHY = type(e).__name__              # the type only, like the log line above
            print(f"[decider] worker {os.getpid()} exiting: {UNHEALTHY}", flush=True)
            # Exit once this 500 is on its way, so a fresh process (or instance) takes over.
            threading.Timer(1.0, os._exit, (1,)).start()
        return JSONResponse({"error": f"internal error: {type(e).__name__}"}, status_code=500)
