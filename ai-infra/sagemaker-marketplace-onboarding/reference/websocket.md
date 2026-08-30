# Bidirectional WebSocket Streaming — Reference

Opt-in. Only implement `/invocations-bidirectional-stream` if your model needs `InvokeEndpointWithBidirectionalStream` (STT, TTS, real-time voice, etc.). Standard streaming response (`InvokeEndpointWithResponseStream`) does **not** use this endpoint.

## Protocol translation

The client uses HTTP/2 on port 8443 with SigV4 auth. SageMaker translates HTTP/2 to WebSocket internally. Your container **only** implements WebSocket on port 8080. Do not implement HTTP/2.

```
Client (HTTP/2 SDK)  →  SageMaker (translates)  →  Your container (WebSocket, port 8080)
```

## Docker label — REQUIRED

Without this label, SageMaker will not route WebSocket traffic to your container.

```dockerfile
LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming=true
```

Do **not** add this label if you are not implementing the WebSocket endpoint.

## Handshake

SageMaker upgrades to WebSocket:

```
GET ws://localhost:8080/invocations-bidirectional-stream HTTP/1.1
Connection: Upgrade
Upgrade: websocket
Sec-WebSocket-Version: 13
Sec-WebSocket-Key: <base64-nonce>
```

Your container responds `101 Switching Protocols` with a computed `Sec-WebSocket-Accept`. FastAPI/Starlette handles this automatically.

Path can be overridden via `X-Amzn-SageMaker-Model-Invocation-Path` header at invocation time.

## Frame mapping — client → container

| Client PayloadPart | Container receives |
|---|---|
| DataType = UTF8 | Text Data Frame |
| DataType = BINARY | Binary Data Frame |

## Frame mapping — container → client

| Container sends | Client receives |
|---|---|
| Text Data Frame | PayloadPart with DataType=UTF8 |
| Binary Data Frame | PayloadPart with DataType=BINARY |
| Data Frame with FIN=0 | PayloadPart with CompletionState=PARTIAL |
| Continuation Frame with FIN=1 | PayloadPart with CompletionState=COMPLETE |

## Ping/Pong (required for connection health)

- SageMaker sends WebSocket Ping every 60 seconds.
- Container **must** respond with a Pong frame (RFC 6455 §5.5.3). Most WebSocket libraries do this automatically.
- 5 consecutive missed Pongs → connection closed by SageMaker.
- Container may send its own Pings; SageMaker responds with Pong.

## Error handling

Two options:

**Text frame with structured error:**
```json
{"ModelStreamError": {"ErrorCode": "<code>", "Message": "<message>"}}
```

**WebSocket Close frame:** SageMaker wraps the close code + reason into `ModelStreamError` for the client.

## Max connection duration — 30 minutes

Hard limit. Client must reconnect after. For longer sessions (e.g., call-center calls), implement reconnection at the application/orchestration layer — **not** in the container. Design the container **stateless per-connection**; do not persist session state across reconnects on the server.

## Recommended STT protocol

- Client → Container: Binary frames (PCM audio chunks) + one Text config frame `{"sample_rate": 16000, "language": "en"}`
- Container → Client: Text frames `{"text": "partial", "is_final": false}` and final `{"text": "final.", "is_final": true, "confidence": 0.95}`

## Recommended TTS protocol

- Client → Container: Text frames `{"text": "sentence", "voice": "id", "language": "en"}`
- Container → Client: Binary frames (PCM audio, streaming as generated) + end signal `{"status": "complete", "duration_ms": 1240}`

## TTS pronunciation dictionary (TTS models only)

Applies only to text-to-speech models — skip for LLMs, STT, image, embedding, or any other modality. Because the container has zero outbound network, pronunciation dictionaries cannot be loaded from S3 at runtime. Recommended pattern: customer sends pronunciation overrides per-request:

```json
{"text": "Welcome to HDFC Bank", "pronunciations": {"HDFC": "H D F C"}, "voice": "id"}
```

Container reads and applies. No shared state between customers.

## Per-inference billing on WebSocket

Metering does **not** go on the main data stream. It goes on a **companion metadata WebSocket** at `/invocations-bidirectional-stream-metadata`.

1. Container advertises support in the main endpoint's upgrade response header:
   ```
   X-Amzn-SageMaker-Metadata-Stream-Supported: true
   ```
2. SageMaker opens a second WebSocket to the metadata endpoint with the same `X-Amzn-SageMaker-Request-Id`.
3. Container emits JSON frames on the metadata channel:
   ```json
   {"Metering": {"Dimension": "audio_seconds", "ConsumedUnits": 150, "ClientToken": "<uuid>"}}
   ```

`Dimension` must match the value configured in your Marketplace listing. `ClientToken` is a per-frame idempotency key.

## Testing locally

`wscat` for quick manual testing:

```bash
npm install -g wscat
wscat --connect ws://127.0.0.1:8080/invocations-bidirectional-stream
```

For automated testing, use `templates/test/test_websocket.py`.

## Client SDK — testing against a live SageMaker endpoint

The local tests above hit the container directly on `ws://localhost:8080/…`. To exercise the full path from a SageMaker endpoint (HTTP/2 on port 8443 with SigV4 auth), you need the AWS SDK for bidirectional streaming. `boto3` does not implement it — it's a separate smithy-generated package.

**Canonical package:** [`aws_sdk_sagemaker_runtime_http2`](https://pypi.org/project/aws_sdk_sagemaker_runtime_http2/) on PyPI. Underscores and hyphens both resolve.

```bash
pip install aws_sdk_sagemaker_runtime_http2
```

**Prerequisites that catch people out:**

- **Python 3.12+ required.** Users on 3.10 or 3.11 see `ERROR: Could not find a version that satisfies the requirement` — reads like "package missing" but is really "no compatible release for your Python." This is the single most common install failure.
- **Pre-alpha status.** Currently 0.x releases. The description on PyPI warns "changes may result in breaking changes prior to the release of version 1.0.0" — pin the version and expect churn.
- **`awscrt` C dependency.** Pulled in transitively via `smithy-http[awscrt]`. Needs prebuilt wheels for the target platform. Fails on alpine musl and some older ARM boards; a source build via `pip install --no-binary=awscrt awscrt` may work but requires build tooling.
- **Transitive deps:** `smithy-aws-core[eventstream,json]`, `smithy-core`, `smithy-http[awscrt]`. Corporate PyPI mirrors that don't proxy new AWS SDK packages may need to be primed with all four.

**Minimal usage sketch:**

```python
from aws_sdk_sagemaker_runtime_http2.client import SageMakerRuntimeHTTP2Client
from aws_sdk_sagemaker_runtime_http2.config import Config
from aws_sdk_sagemaker_runtime_http2.models import (
    InvokeEndpointWithBidirectionalStreamInput,
    RequestPayloadPart,
    RequestStreamEventPayloadPart,
)
from smithy_aws_core.identity import EnvironmentCredentialsResolver

config = Config(
    endpoint_uri=f"https://runtime.sagemaker.{region}.amazonaws.com:8443",
    region=region,
    aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
)
client = SageMakerRuntimeHTTP2Client(config=config)

stream = await client.invoke_endpoint_with_bidirectional_stream(
    InvokeEndpointWithBidirectionalStreamInput(
        endpoint_name="<your-endpoint>",
        model_invocation_path="",       # optional path override
        model_query_string="",          # optional query params
    )
)

# Send:
await stream.input_stream.send(RequestStreamEventPayloadPart(
    value=RequestPayloadPart(bytes_=b"...", data_type="BINARY"),   # or "UTF8"
))

# Receive:
output = await stream.await_output()
async for event in output[1]:
    ...
```

Note the client is **async-only** (`await` everywhere) and uses `smithy_aws_core.identity.EnvironmentCredentialsResolver` — not boto3's credential chain — so `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars are the standard path. AWS CLI config and instance metadata are also picked up.

**Voice/AI pipelines only:** if the model is being consumed by a voice-AI pipeline, the `pipecat` framework wraps this SDK in an extras install (`uv add "pipecat-ai[sagemaker]"`) — it pins compatible smithy versions and provides a `SageMakerBidiClient` wrapper. Not a general-purpose recommendation; skip for non-voice use cases and use the raw SDK above.
