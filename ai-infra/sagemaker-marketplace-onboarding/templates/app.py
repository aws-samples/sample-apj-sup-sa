"""
SageMaker Marketplace-compatible inference server.

Contract:
- Runs on port 8080.
- GET /ping         — Health check. 200 when model loaded, 503 otherwise. Must respond within 2s.
- POST /invocations — Single entry point for Real-Time, Streaming response, and Batch Transform.
                      SageMaker gives ZERO indication of which mode the request came from.
- GET /execution-parameters — Optional. Advertises optimal batch config to SageMaker.

Do NOT bake model weights into the Docker image. Weights are mounted read-only at
/opt/ml/model/ (via env var SM_MODEL_DIR). See model_loader.py.
"""

import json
import logging
import os
import signal
import sys
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from inference import predict, predict_stream, smoke_check
from model_loader import load_model

# --- Opt-in modules — uncomment only if selected in the skill's Phase 2 ---
# from websocket_handler import router as ws_router
# from metering import inference_metering_header

# --- Structured logging (goes to CloudWatch via SageMaker stdout capture) ---
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(
    logging.Formatter(
        json.dumps(
            {
                "time": "%(asctime)s",
                "level": "%(levelname)s",
                "msg": "%(message)s",
            }
        )
    )
)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)

app = FastAPI()

# --- Wire up the bidirectional streaming router if enabled ---
# app.include_router(ws_router)

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
_model = None


@app.on_event("startup")
async def _startup() -> None:
    """Load the model. Blocks until ready. /ping returns 503 until this exits.

    Total container-startup budget until /ping = 200 is 8 minutes. That budget
    covers S3 download of model.tar + extraction + this function. Log startup
    duration so you can spot regressions before you burn the budget.

    Also log the batch-mode env vars if present — useful when debugging why
    /execution-parameters returned unexpected values.
    """
    global _model
    load_start = time.perf_counter()
    logger.info(f"Loading model from {MODEL_DIR}")
    if os.environ.get("SAGEMAKER_BATCH") == "true":
        logger.info(f"Batch mode: max_payload_mb="
                    f"{os.environ.get('SAGEMAKER_MAX_PAYLOAD_IN_MB')}, "
                    f"strategy={os.environ.get('SAGEMAKER_BATCH_STRATEGY')}, "
                    f"max_concurrent={os.environ.get('SAGEMAKER_MAX_CONCURRENT_TRANSFORMS')}")
    _model = load_model(MODEL_DIR)
    load_ms = int((time.perf_counter() - load_start) * 1000)
    logger.info(f"Model ready (load_ms={load_ms})")


@app.get("/ping")
async def ping() -> Response:
    """Health check. Return 200 only when a lightweight inference path works.

    The spec is explicit (§3.2): "Do NOT just return a static 200. Verify that
    the model artifact is loaded, GPU memory is allocated, and the inference
    code path is functional (lightweight test prediction)." If /ping keeps
    returning 200 while the model is broken, SageMaker will keep routing
    traffic to the dead instance instead of replacing it — a silent outage.

    Budget: 2 seconds hard limit. Make smoke_check() cheap (single-token
    generation, single-frame audio, single 8x8 pixel image, etc.), and cache
    a "last healthy at" timestamp if the check is close to the budget.
    """
    if _model is None:
        return Response(status_code=503)
    try:
        # Runs the actual inference code path. Fails 503 on any exception —
        # do not swallow. SageMaker uses non-200 as the signal to replace
        # the instance.
        smoke_check(_model)
    except Exception as exc:
        logger.warning(f"/ping smoke check failed: {exc}")
        return Response(status_code=503)
    return Response(status_code=200)


@app.post("/invocations")
async def invocations(request: Request):
    """The one and only inference endpoint.

    Serves Real-Time, Streaming response, AND Batch Transform. Do not branch on
    invocation mode — the container cannot tell them apart, and it should not try.

    SageMaker only forwards these headers (all others are stripped):
      Content-Type, Accept, X-Amzn-SageMaker-Custom-Attributes,
      X-Amzn-SageMaker-Inference-Id, X-Amzn-SageMaker-Session-Id
    """
    content_type = request.headers.get("content-type", "application/json")
    accept = request.headers.get("accept", "application/json")
    inference_id = request.headers.get("x-amzn-sagemaker-inference-id", "")
    body = await request.body()

    start = time.perf_counter()
    logger.info(f"Invocation received (id={inference_id}, bytes={len(body)}, "
                f"content-type={content_type})")

    # -----------------------------------------------------------------------
    # Streaming response path.
    # Triggered by the client using InvokeEndpointWithResponseStream. The
    # container still receives a plain POST — the only signal that streaming
    # is desired comes from the Accept header the provider chooses to use.
    # A safer pattern: always support a streaming Accept type if your model
    # can chunk output. Uncomment if you enabled streaming in Phase 0.
    # -----------------------------------------------------------------------
    # if accept == "application/x-ndjson":
    #     async def chunks():
    #         async for chunk in predict_stream(_model, body, content_type):
    #             yield chunk
    #     return StreamingResponse(chunks(), media_type=accept)

    try:
        result, response_content_type = predict(_model, body, content_type, accept)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.exception(f"Inference failed (id={inference_id}, latency_ms={latency_ms})")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "inference_id": inference_id},
        )

    latency_ms = int((time.perf_counter() - start) * 1000)
    logger.info(f"Invocation completed (id={inference_id}, latency_ms={latency_ms}, "
                f"bytes_out={len(result) if isinstance(result, (bytes, bytearray)) else 0})")

    headers = {}
    # --- Per-inference billing (uncomment if selected in Phase 2) ---
    # `consumed_units` is whatever your predict() computed as the billable
    # quantity for this specific invocation. For mini-batch requests that
    # process multiple inferences in one call, set it to the number of
    # inferences processed.
    #
    # Metering is ONLY honored on 2XX responses. If you return a non-2XX
    # error, the buyer is not charged. Emit metering on every successful
    # invocation, or you will not be paid for those calls.
    #
    # consumed_units = ...   # e.g. len(tokens_generated), audio_seconds, etc.
    # headers["X-Amzn-Inference-Metering"] = inference_metering_header(consumed_units)

    return Response(content=result, media_type=response_content_type, headers=headers)


@app.get("/execution-parameters")
async def execution_parameters() -> JSONResponse:
    """Optional batch optimization hint. SageMaker calls this BEFORE /invocations
    during Batch Transform to self-tune throughput.

    Values below are conservative defaults — tune for your instance and model.
    """
    return JSONResponse(
        content={
            "MaxConcurrentTransforms": int(
                os.environ.get("SAGEMAKER_MAX_CONCURRENT_TRANSFORMS", "4")
            ),
            "BatchStrategy": os.environ.get("SAGEMAKER_BATCH_STRATEGY", "SINGLE_RECORD"),
            "MaxPayloadInMB": int(os.environ.get("SAGEMAKER_MAX_PAYLOAD_IN_MB", "6")),
        }
    )


def _graceful_shutdown(signum, frame) -> None:
    """SageMaker sends SIGTERM on shutdown. You have 30 seconds before SIGKILL."""
    logger.info(f"Received signal {signum}; shutting down")
    # Close any open resources here: release GPU memory, flush buffers, etc.
    sys.exit(0)


if __name__ == "__main__":
    # SageMaker invokes: docker run <image> serve
    # sys.argv[1] will be "serve"
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
