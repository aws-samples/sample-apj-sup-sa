# Timing Constraints (from the SageMaker Marketplace Model Listing Guide)

Every hard timing limit the spec enforces, in one place. Violate any of these and the container will misbehave in production even if local testing looks fine.

| Constraint | Value | Consequence if exceeded |
|---|---|---|
| Socket connection acceptance | ≤ 250 ms | Request rejected before your code sees it |
| /ping timeout per request | 2 seconds | Health check fails; SageMaker considers instance unhealthy |
| Container startup window (from `docker run` until /ping returns 200) | 8 minutes | CreateEndpoint fails; instance replaced |
| /invocations sync max processing | 60 seconds | 504 timeout returned to client |
| InvokeEndpointWithResponseStream | 8 minutes | Connection closed mid-response |
| InvokeEndpointAsync | 60 minutes | Async job fails |
| Batch Transform (per record) | No hard timeout, but 100 MB max payload | Record fails |
| SIGKILL delay after SIGTERM | 30 seconds | Process killed hard; connections dropped |
| WebSocket Ping interval (SageMaker → container) | Every 60 seconds | (streaming only, out of scope for this skill) |

## Cold-start budget breakdown

The 8-minute window covers everything from `docker run` until `/ping` returns 200:

| Phase | Typical time | Optimization |
|---|---|---|
| S3 download of model.tar | 30–120 s (size dependent) | Uncompressed tar |
| Extraction to /opt/ml/model/ | 10–30 s | Uncompressed = near-instant |
| Weight loading to GPU | 60–180 s | Use safetensors (~2× faster than .pt/.bin) |
| **Total** | **~3–6 min** | **must be < 8 min** |

If your model is above 20 GB, budget carefully. If load consistently takes over 6 minutes, split the model or use safetensors.
