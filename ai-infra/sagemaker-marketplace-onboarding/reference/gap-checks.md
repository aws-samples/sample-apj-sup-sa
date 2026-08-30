# Gap Analysis Checklist (Phase 3)

The full list of things to compare between an existing model project and the SageMaker Marketplace container contract. For each item, if the project is out of spec, note the file/line, cite the constraint, and propose the fix. **Do not edit the existing project's files** — the fix goes into the scaffolded sibling directory only.

## Networking

- [ ] HTTP server listens on port **8080**. Anything else (5000, 8000, 8888, etc.) must move.
- [ ] Only port 8080 is exposed to the outside. Multi-process containers bind everything else to localhost via supervisord.
- [ ] No reliance on outbound network at inference time (no S3, no HuggingFace Hub, no license servers, no pip install at startup, no VPC endpoints).

## Endpoints

- [ ] `GET /ping` exists, returns 200 when the model is loaded and functional, 503 while loading or if broken.
- [ ] `/ping` does **not** return static 200 — the spec explicitly warns against this because it prevents SageMaker from replacing dead instances.
- [ ] `/ping` responds within 2 seconds.
- [ ] `POST /invocations` exists and is the single inference entry point.
- [ ] Any existing prediction path (`/predict`, `/v1/chat/completions`, `/generate`, etc.) is either renamed to `/invocations` or gets a `/invocations` alias that forwards to it — do not break the original.
- [ ] `/invocations` does **not** branch on invocation mode. The container cannot distinguish Real-Time from Async from Batch and should not try.
- [ ] `/invocations` does **not** depend on any HTTP header outside the five SageMaker forwards: `Content-Type`, `Accept`, `X-Amzn-SageMaker-Custom-Attributes`, `X-Amzn-SageMaker-Inference-Id`, `X-Amzn-SageMaker-Session-Id`.
- [ ] `GET /execution-parameters` present if Batch Transform will be used (recommended).

## Response contract

- [ ] Success responses use HTTP 200 with a sensible `Content-Type`.
- [ ] Error responses use non-2xx status codes. No 200-with-error-body.
- [ ] Multi-file outputs are zipped in-container and returned as `application/zip` (cannot write to S3 at runtime).
- [ ] Streaming response (if enabled): uses `Transfer-Encoding: chunked`. Same `/invocations` endpoint, not a separate route.

## Model weights

- [ ] Weights are **not** baked into the Docker image.
- [ ] Weights are **not** downloaded at startup (HuggingFace Hub, S3, git-lfs — all fail under network isolation).
- [ ] Loading code reads from `/opt/ml/model/` (or `SM_MODEL_DIR` env var). No hardcoded paths like `/app/models/`.
- [ ] Total load time (S3 download + extraction + weight load) fits in the 8-minute startup window. If tight, recommend uncompressed tar and safetensors.
- [ ] Weights packaged as **uncompressed** tar (`tar -cf`, not `tar -czf`).

## Filesystem

- [ ] Container writes only to `/tmp`. Nothing else is writable.
- [ ] No `USER` directive in the Dockerfile (must run as root).

## Dockerfile

- [ ] `ENTRYPOINT` is **exec form** (`ENTRYPOINT ["python3", "app.py"]`). Shell form breaks SIGTERM.
- [ ] `CMD ["serve"]` is present.
- [ ] Base image matches the target instance's CUDA version.
- [ ] No NVIDIA drivers bundled in the image.
- [ ] No `tini` used as init.
- [ ] `EXPOSE 8080` only.
- [ ] `LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming=true` **if and only if** WebSocket bidirectional streaming is implemented.

## Signal handling

- [ ] Container exits gracefully within 30 seconds of SIGTERM. If not, exec-form ENTRYPOINT is missing.

## Advanced modes (opt-in)

**Bidirectional WebSocket** (only if selected in Phase 2):
- [ ] `/invocations-bidirectional-stream` endpoint exists on port 8080.
- [ ] Container responds to WebSocket Ping with Pong (usually automatic in modern WebSocket libraries).
- [ ] Container stateless per-connection (no session state persisted across reconnects).
- [ ] Errors return either a `{"ModelStreamError": {...}}` text frame or a WebSocket Close frame.
- [ ] Aware of the 30-minute max connection duration.

**Per-inference billing** (only if selected in Phase 2):
- [ ] Every 2XX `/invocations` response emits the `X-Amzn-Inference-Metering` header. Value is a JSON string: `{"Dimension": "...", "ConsumedUnits": N}`. Mini-batch requests set `ConsumedUnits` to the batch size. Metering is not emitted on non-2XX responses (which are not billed).
- [ ] Dimension name matches what will be configured on the Marketplace listing.
- [ ] For WebSocket: upgrade response includes `X-Amzn-SageMaker-Metadata-Stream-Supported: true` and the container implements `/invocations-bidirectional-stream-metadata`.

## Multi-process containers (only if applicable)

- [ ] Only port 8080 is exposed to SageMaker; every other process binds to localhost.
- [ ] `supervisord` runs as PID 1 with `nodaemon=true` so SIGTERM propagates to child processes.
- [ ] **Every** program in `supervisord.conf` has `stdout_logfile=/dev/stdout` and `stdout_logfile_maxbytes=0` (same for stderr). Without this, program logs never reach CloudWatch — SageMaker only captures the container's own stdout.
- [ ] `stopwaitsecs` for each program is ≤ 25s (SageMaker sends SIGKILL 30s after SIGTERM; leave buffer for supervisord itself to exit).

## Miscellaneous

- [ ] Logs go to stdout/stderr (SageMaker captures automatically to CloudWatch). Structured JSON logging recommended.
- [ ] Container does not try to write logs to a file inside `/opt/ml/model/` (read-only) or any path other than `/tmp`.
- [ ] No dependency on a database, cache, or external service running alongside the container.
