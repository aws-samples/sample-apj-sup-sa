# Pre-Submission Checklist

Copy this file into the project as `PRE_SUBMISSION_CHECKLIST.md`. Walk through every item with the model provider before declaring the container marketplace-ready. Do not skip items — most of these are the exact conditions the SageMaker validation transform job will fail on.

## Endpoints

- [ ] `GET /ping` responds within 2 seconds once the model is loaded.
- [ ] `/ping` actually **exercises the inference path** (a lightweight smoke prediction) — not a static 200 based on object presence. The spec explicitly warns that static 200 during model failure keeps SageMaker routing to the dead instance.
- [ ] `/ping` returns **503** (not 200) while the model is still loading or if inference is broken.
- [ ] `POST /invocations` handles the test payload from `test/test_input.json` and returns a valid response with a sensible `Content-Type`.
- [ ] `/invocations` returns non-2xx on error (do not return 200 with an error body — errors must surface to the caller).
- [ ] `GET /execution-parameters` returns valid `MaxConcurrentTransforms`, `BatchStrategy`, `MaxPayloadInMB` (optional but recommended for Batch Transform).
- [ ] If streaming response was enabled: `/invocations` returns `Transfer-Encoding: chunked` and chunks arrive progressively (verified with `test/test_streaming.py`).

## Startup and shutdown

- [ ] Container starts and `/ping` returns 200 within **8 minutes** of `docker run` — including S3 download, extraction, and weight loading.
- [ ] Container shuts down gracefully within 30 seconds of SIGTERM (`docker stop` completes without SIGKILL).
- [ ] `ENTRYPOINT` is in **exec form** (`["python3", "app.py"]`), not shell form.
- [ ] `CMD` is `["serve"]` (SageMaker invokes `docker run <image> serve`).

## Runtime environment

- [ ] Container runs as **root** (no `USER` directive in the Dockerfile).
- [ ] Container works under `docker run --network none ...`. No runtime dependency on external services (S3, HuggingFace Hub, license servers, pip install at startup).
- [ ] Model weights are loaded from `/opt/ml/model/` (or `SM_MODEL_DIR` env var), not from a baked-in path.
- [ ] No NVIDIA drivers bundled in the image.
- [ ] No `tini` used as init.
- [ ] Only port 8080 is exposed. Any internal processes bind to localhost.

## Model artifacts

- [ ] Model weights are NOT baked into the Docker image.
- [ ] `model.tar` is uncompressed (`tar -cf`, not `tar -czf`).
- [ ] Weights are in `.safetensors` (or another fast-load format) where possible.
- [ ] Weights uploaded to a seller-owned S3 bucket.

## Security

- [ ] ECR image passes `trivy image --severity CRITICAL,HIGH` (no CRITICAL or HIGH findings).
- [ ] ECR automatic vulnerability scan passes (`aws ecr describe-image-scan-findings`).

## Docker label

- [ ] If (and only if) implementing the WebSocket bidirectional-streaming endpoint, the Dockerfile has `LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming=true`. Otherwise this label is absent.

## Bidirectional WebSocket (only if implemented)

- [ ] `/invocations-bidirectional-stream` endpoint responds to a WebSocket upgrade on port 8080.
- [ ] Container responds to WebSocket Ping frames with Pong within the required window.
- [ ] Container is stateless per-connection — sessions >30 min reconnect at the application layer.
- [ ] Errors are surfaced as `{"ModelStreamError": {...}}` Text frames or WebSocket Close frames.

## Per-inference billing (only if selected)

- [ ] Every 2XX `/invocations` response emits the `X-Amzn-Inference-Metering` header with a JSON value: `{"Dimension": "...", "ConsumedUnits": N}`. Mini-batch requests set `ConsumedUnits` to the number of inferences processed. No metering on non-2XX responses.
- [ ] The `dimension` name in the emission matches what is configured on the Marketplace listing.
- [ ] For bidirectional streaming: the WebSocket upgrade response includes `X-Amzn-SageMaker-Metadata-Stream-Supported: true` and `/invocations-bidirectional-stream-metadata` is implemented.

## Contract compliance (spot-check)

- [ ] Code does **not** branch on invocation mode. The same `/invocations` handler works for Real-Time, Streaming response, and Batch Transform without needing to know which one is calling.
- [ ] Code does **not** depend on any HTTP header outside the five SageMaker forwards (`Content-Type`, `Accept`, `X-Amzn-SageMaker-Custom-Attributes`, `X-Amzn-SageMaker-Inference-Id`, `X-Amzn-SageMaker-Session-Id`).
- [ ] Container only writes to `/tmp` — never to `/opt/ml/model/` or arbitrary paths.
