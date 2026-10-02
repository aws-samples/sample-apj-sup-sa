"""SageMaker serving for Strands Decider 2B: GET /ping and POST /invocations on port 8080.

The request body is the decider's own System One request, unchanged:

    {"state": ..., "questions": {id: {"type": "noul"|"choice"|"score", ...}}}

and the response is its System One response (answers with probabilities and confidence,
plus usage and server latency). The engine is the published ``strands-decider`` package;
this file only adds the SageMaker contract, precision selection, kernel warm-up and worker
processes.

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
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager

MODEL_DIR = os.environ.get("DECIDER_MODEL_DIR", "/opt/ml/model/decider")
DEVICE = os.environ.get("DECIDER_DEVICE", "cuda")
WORKERS = int(os.environ.get("DECIDER_WORKERS", "1"))
MAX_BODY_BYTES = 6 * 1024 * 1024        # SageMaker's own request limit
# SageMaker gives a real-time invocation 60 s. A request that has waited this long for the model
# (it is busy with earlier ones) is refused with 503 rather than run for a caller that has gone.
DEADLINE_S = float(os.environ.get("DECIDER_REQUEST_DEADLINE", "50"))
# flash-linear-attention (fla) gives the Gated DeltaNet layers fast Triton kernels on GPU.
# Transformers picks it at import time whenever the package imports, so it has to be hidden
# before the model code loads wherever it must not run: on CPU (Triton needs a GPU), or when
# DECIDER_FLA=off. Transformers then uses its reference PyTorch path, which gives the same answers.
_FLA = os.environ.get("DECIDER_FLA", "auto")
USE_FLA = _FLA == "on" or (_FLA == "auto" and DEVICE == "cuda")
if not USE_FLA:
    sys.modules["fla"] = None  # type: ignore[assignment]  # makes `import fla` raise ImportError

import torch  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402
from strands_decider.infer import EngineConfig, SystemOneEngine  # noqa: E402
from strands_decider.modeling import StrandsDeciderModel  # noqa: E402
from strands_decider.schema import SystemOneRequest  # noqa: E402

ENGINE: SystemOneEngine | None = None
LOCK = threading.Lock()                 # the engine is synchronous and not thread-safe


def pick_dtype() -> torch.dtype:
    """bf16 where the GPU supports it natively, else fp32.

    The checkpoint is bf16. A T4 (compute capability 7.5) has no native bf16, and fp16 is not
    a safe stand-in: the Gated DeltaNet layers can overflow fp16's range. fp32 is exact, and the
    2B torso still fits a 16 GB T4 (about 7 GiB of weights).
    """
    choice = os.environ.get("DECIDER_DTYPE", "auto")
    if choice != "auto":
        return {"bfloat16": torch.bfloat16, "float32": torch.float32}[choice]
    if DEVICE == "cpu":
        return torch.float32
    # including_emulation=False: recent torch reports bf16 as "supported" on a T4 through
    # slow emulation, which is not what we want.
    return torch.bfloat16 if torch.cuda.is_bf16_supported(including_emulation=False) else torch.float32


def load_engine() -> SystemOneEngine:
    dtype = pick_dtype()
    model = StrandsDeciderModel.load(MODEL_DIR)
    if dtype != torch.bfloat16:
        with torch.inference_mode():
            model.torso.to(dtype)
    return SystemOneEngine(model, EngineConfig(device=DEVICE, use_prefix_cache=True,
                                               model_name="strands-decider-2B-hobson-v19"))


def warm_up(engine: SystemOneEngine) -> None:
    """Compile the Triton kernels before the first real request.

    Without this the first request pays the compile: about 100 s on a T4 and 35 s on an L4,
    past SageMaker's 60 s invocation limit. Short and long states cover the kernel variants.
    """
    q = {"a": {"type": "noul", "instructions": "Is this urgent?"},
         "b": {"type": "choice", "instructions": "Which team?", "criteria": {"x": "", "y": "", "z": ""}},
         "c": {"type": "score", "instructions": "How bad is it?", "criteria": ["low", "mid", "high"]}}
    for state in ("Short message.", "A longer support message about a billing problem. " * 60):
        engine.evaluate(SystemOneRequest.model_validate({"state": state, "questions": q}))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global ENGINE
    t0 = time.time()
    engine = load_engine()
    warm_up(engine)
    ENGINE = engine
    gpu = (f"{torch.cuda.get_device_name(0)}, {torch.cuda.memory_allocated() / 2**30:.1f} GiB in this worker"
           if DEVICE == "cuda" else "cpu")
    print(f"[decider] worker {os.getpid()} ready in {time.time() - t0:.0f} s on {gpu}, "
          f"{next(engine.model.torso.parameters()).dtype}, fla kernels {'on' if USE_FLA else 'off'}", flush=True)
    yield


app = FastAPI(title="strands-decider on SageMaker", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/ping")
def ping() -> Response:
    return Response(status_code=200 if ENGINE is not None else 503)


@app.post("/invocations")
async def invocations(request: Request) -> JSONResponse:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse({"error": "request body is larger than 6 MB"}, status_code=413)
    try:
        req = SystemOneRequest.model_validate_json(raw)
    except ValidationError as e:
        # e.json() leaves out the raw input, which may not be serialisable (or may be large).
        detail = json.loads(e.json(include_url=False, include_input=False))
        return JSONResponse({"error": "invalid request", "detail": detail}, status_code=400)
    return await run_in_threadpool(_evaluate, req, time.monotonic())


def _evaluate(req: SystemOneRequest, arrived: float) -> JSONResponse:
    if ENGINE is None:
        return JSONResponse({"error": "model is still loading"}, status_code=503)
    if not LOCK.acquire(timeout=max(0.0, DEADLINE_S - (time.monotonic() - arrived))):
        return JSONResponse({"error": f"busy: not started within {DEADLINE_S:.0f} s; retry later"}, status_code=503)
    try:
        t0 = time.perf_counter()
        payload = ENGINE.evaluate(req).model_dump()
        payload["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return JSONResponse(payload)
    except ValueError as e:                       # e.g. a malformed option permutation: caller error
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001       a server-side fault: log it, report 500
        traceback.print_exc()
        return JSONResponse({"error": f"internal error: {type(e).__name__}"}, status_code=500)
    finally:
        LOCK.release()


if __name__ == "__main__":
    # SageMaker reaches the container on its network interface, so it must listen on all of them.
    # Each worker process loads its own model copy in `lifespan`; the parent process loads none.
    uvicorn.run("serve:app", host="0.0.0.0",  # nosec B104
                port=int(os.environ.get("SAGEMAKER_BIND_TO_PORT", "8080")),
                workers=WORKERS, timeout_keep_alive=75, log_level="warning")
