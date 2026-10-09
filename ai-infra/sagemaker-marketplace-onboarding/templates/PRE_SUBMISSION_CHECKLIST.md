# Pre-Submission Checklist

Walk through every item below before pushing to ECR and creating the model package. These are the conditions the SageMaker Marketplace validation job checks against.

## Endpoints

- [ ] `GET /ping` responds within 2 seconds once the model is loaded.
- [ ] `/ping` **actually verifies the inference code path** (calls `smoke_check()`) — does not return static 200 based on object presence.
- [ ] `/ping` returns **503** (not 200) while the model is loading or broken.
- [ ] `POST /invocations` handles the test payload and returns a valid response with a sensible `Content-Type`.
- [ ] `/invocations` returns non-2xx on error.
- [ ] `GET /execution-parameters` returns valid values (optional but recommended).
- [ ] Streaming (if enabled): `Transfer-Encoding: chunked` and chunks arrive progressively.

## Startup and shutdown

- [ ] `/ping` returns 200 within **8 minutes** of `docker run`.
- [ ] Container shuts down gracefully within 30 seconds of SIGTERM.
- [ ] `ENTRYPOINT` is exec form (`["python3", "app.py"]`).
- [ ] `CMD` is `["serve"]`.

## Runtime environment

- [ ] Container runs as root.
- [ ] Container works under `docker run --network none ...`.
- [ ] Weights loaded from `/opt/ml/model/` (or `SM_MODEL_DIR`).
- [ ] No NVIDIA drivers, no tini bundled.
- [ ] Only port 8080 exposed.

## Model artifacts

- [ ] Weights NOT baked into the Docker image.
- [ ] `model.tar` is uncompressed.
- [ ] Weights uploaded to a seller S3 bucket.

## Security

- [ ] Trivy scan passes (no CRITICAL/HIGH).
- [ ] ECR vulnerability scan passes.

## Contract

- [ ] `/invocations` does not branch on invocation mode.
- [ ] No dependency on custom HTTP headers.
- [ ] Only `/tmp` is written to at runtime.

## Bidirectional WebSocket (only if implemented)

- [ ] `/invocations-bidirectional-stream` responds to WebSocket upgrade on port 8080.
- [ ] `LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming=true` is set in the Dockerfile.
- [ ] Container answers WebSocket Pings with Pongs.
- [ ] Stateless per-connection; long sessions reconnect client-side.
- [ ] Errors surface as `ModelStreamError` text frames or Close frames.

## Per-inference billing (only if selected)

- [ ] Metering emitted on every 2XX `/invocations` response via `X-Amzn-Inference-Metering: {"Dimension":"...","ConsumedUnits":N}`. Mini-batch: `ConsumedUnits` = number of inferences processed. No metering on non-2XX.
- [ ] Dimension name matches the Marketplace listing configuration.
- [ ] For WebSocket: `X-Amzn-SageMaker-Metadata-Stream-Supported: true` set on upgrade, `/invocations-bidirectional-stream-metadata` implemented.
