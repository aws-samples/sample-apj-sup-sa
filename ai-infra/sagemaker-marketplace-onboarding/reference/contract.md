# SageMaker Marketplace Container Contract — One-Page Reference

## Endpoints (all on port 8080)

| Method | Path | Purpose |
|---|---|---|
| GET  | `/ping` | Health check. 200 = ready. 503 = loading/broken. |
| POST | `/invocations` | Inference. Same code serves Real-Time, Streaming response, Batch Transform. |
| GET  | `/execution-parameters` | Optional. Batch Transform tuning hint. |

## Headers SageMaker forwards to `/invocations`

Only these five. Any other header is stripped.

- `Content-Type` — buyer's payload MIME type
- `Accept` — buyer's desired response MIME type
- `X-Amzn-SageMaker-Custom-Attributes` — opaque, max 1024 chars
- `X-Amzn-SageMaker-Inference-Id` — request tracking
- `X-Amzn-SageMaker-Session-Id` — for streaming sessions

## Timing constraints (hard limits)

| Constraint | Value | On failure |
|---|---|---|
| Socket connection accept | ≤ 250 ms | Request rejected |
| /ping response | ≤ 2 s | Health check fails |
| Container startup (until /ping = 200) | ≤ 8 min | CreateEndpoint fails |
| /invocations sync | ≤ 60 s | 504 timeout |
| InvokeEndpointWithResponseStream | ≤ 8 min | Connection closed |
| SIGKILL after SIGTERM | 30 s | Process killed |

## Payload limits

| Mode | Max |
|---|---|
| Real-Time /invocations | 25 MB |
| Streaming response | 25 MB request |
| Async Inference | 1 GB (via S3) |
| Batch Transform (per record) | 100 MB |

## Filesystem

- `/opt/ml/model/` — read-only mount. Weights land here at deploy time. Read via `SM_MODEL_DIR` env var.
- `/tmp` — writable scratch. Only writable path.
- No S3, no internet, no VPC endpoints, no outbound network at runtime.

## Dockerfile must-haves

**One Dockerfile / one image per listing.** Multi-stage builds are OK; only the final image is pushed. There is no separate "loader" image.

**How SageMaker launches it:** `docker run <image> serve`. That maps to `ENTRYPOINT + "serve"` at runtime.

- `ENTRYPOINT ["python3", "app.py"]` — **exec form** (JSON array). Shell form (`ENTRYPOINT python3 app.py`) makes Docker wrap in `/bin/sh -c`, which does not forward SIGTERM to Python — SageMaker's graceful shutdown breaks.
- `CMD ["serve"]` — provides the "serve" argument. Local `docker run <image>` (no args) also works because CMD supplies the default.
- Multi-process alternative: `ENTRYPOINT ["/usr/bin/supervisord"]` + `CMD ["-c", "/etc/supervisord.conf"]` — the supervisord config runs `python3 app.py serve` inside a program block.
- Run as root. No `USER` directive.
- Do not bundle NVIDIA drivers, tini, or model weights.
- Only port 8080 exposed.
- `LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming=true` **only** if implementing WebSocket.
