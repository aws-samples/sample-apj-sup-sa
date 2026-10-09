"""
Bidirectional WebSocket endpoint for SageMaker Marketplace.

This module is opt-in. Only include it if your model needs
InvokeEndpointWithBidirectionalStream (STT, TTS, real-time voice, etc.).

Contract:
- Endpoint path: /invocations-bidirectional-stream
- Protocol: WebSocket on port 8080. SageMaker terminates HTTP/2 externally
  and translates to WebSocket internally — your container only sees WebSocket.
- Must respond to WebSocket Ping frames (SageMaker sends every 60s;
  5 consecutive missed Pongs → connection closed).
- Max connection duration: 30 MINUTES. Client reconnects after.
- Errors: send Text frame with {"ModelStreamError": {"ErrorCode": "...",
  "Message": "..."}} OR send a Close frame with a status code + reason.

Required Docker label (in Dockerfile):
    LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming=true

Without this label, SageMaker will NOT route WebSocket traffic to the container.

Per-inference billing (opt-in, further):
    See metering.py. Metering for bidirectional streaming goes on a COMPANION
    metadata WebSocket at /invocations-bidirectional-stream-metadata, NOT on
    the main data stream.

Fragmentation (advanced):
    The SageMaker protocol maps WebSocket Data Frame FIN=0 → PayloadPart
    CompletionState=PARTIAL, and Continuation Frame FIN=1 → COMPLETE. If you
    need to emit PARTIAL/COMPLETE semantics (e.g. streaming a single logical
    message across many audio chunks), you cannot use FastAPI's default
    `websocket.send_text()` / `send_bytes()` — those always emit unfragmented
    frames (FIN=1). To emit fragmented frames, either:
      1) Drop down to raw ASGI send with `type="websocket.send"` and a
         custom FIN bit — non-trivial, framework-version dependent, OR
      2) Use the `websockets` library directly (bypass FastAPI's WebSocket
         wrapper) via a custom ASGI route. It exposes `fragmented=` on send.
    For most use cases, sending each chunk as a separate complete Text or
    Binary frame is fine — SageMaker treats each as its own PayloadPart with
    CompletionState=COMPLETE. Fragmentation is only necessary when the
    receiving client depends on the PARTIAL/COMPLETE grouping semantics.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from inference import handle_stream_session   # provider writes this
try:
    from metering import make_metering_frame  # only present if per-inference opted in
    METERING_ENABLED = True
except ImportError:
    METERING_ENABLED = False

logger = logging.getLogger()

router = APIRouter()


@router.websocket("/invocations-bidirectional-stream")
async def bidirectional_stream(websocket: WebSocket) -> None:
    # If per-inference billing is enabled, signal that we support a companion
    # metadata channel BEFORE calling accept().
    if METERING_ENABLED:
        # FastAPI does not expose upgrade-response headers directly; the
        # recommended way is to set them on accept() via `subprotocol` or
        # response headers on a raw ASGI send. Uvicorn 0.30+ supports
        # setting headers on websocket.accept via the `headers` keyword.
        await websocket.accept(
            headers=[(b"x-amzn-sagemaker-metadata-stream-supported", b"true")]
        )
    else:
        await websocket.accept()

    inference_id = websocket.headers.get("x-amzn-sagemaker-inference-id", "")
    session_id = websocket.headers.get("x-amzn-sagemaker-session-id", "")
    logger.info(f"WS session opened (inference_id={inference_id}, session_id={session_id})")

    try:
        # Hand off to the provider's per-connection handler. The handler is
        # a coroutine that reads from the websocket and writes back frames.
        # Keep it STATELESS between connections — sessions >30min must
        # reconnect at the application layer, not persist server-side.
        await handle_stream_session(websocket)

    except WebSocketDisconnect:
        logger.info(f"WS client disconnected (inference_id={inference_id})")

    except Exception as exc:
        logger.exception(f"WS session error (inference_id={inference_id})")
        # Send a ModelStreamError text frame so the client sees a structured error
        try:
            await websocket.send_text(json.dumps({
                "ModelStreamError": {
                    "ErrorCode": type(exc).__name__,
                    "Message": str(exc),
                }
            }))
            await websocket.close(code=1011, reason="internal error")
        except Exception:
            pass  # already closed


@router.websocket("/invocations-bidirectional-stream-metadata")
async def bidirectional_metadata(websocket: WebSocket) -> None:
    """
    Companion metadata channel for per-inference billing on bidirectional
    streaming connections.

    SageMaker opens this second WebSocket in parallel with the main data
    connection, matching by X-Amzn-SageMaker-Request-Id header. The container
    sends billing frames on this channel while the main channel carries only
    inference data.

    Only wire this up if per-inference billing is enabled. Otherwise leave
    the route unregistered — SageMaker will not open the metadata channel
    unless the main endpoint's upgrade response advertises
    X-Amzn-SageMaker-Metadata-Stream-Supported: true.
    """
    if not METERING_ENABLED:
        await websocket.close(code=1002, reason="metering not enabled")
        return

    await websocket.accept()
    request_id = websocket.headers.get("x-amzn-sagemaker-request-id", "")
    logger.info(f"WS metadata channel opened (request_id={request_id})")

    try:
        # The provider signals billable units by calling report_metering()
        # from inside handle_stream_session on the main channel. The bridge
        # between the two is application-defined; a common pattern is a
        # per-request asyncio.Queue keyed by request_id. Example scaffold:
        #
        #   from metering_bridge import get_queue
        #   queue = get_queue(request_id)
        #   while True:
        #       units, dimension, client_token = await queue.get()
        #       frame = make_metering_frame(units, dimension, client_token)
        #       await websocket.send_text(json.dumps(frame))
        #
        # TODO — replace with your metering-bridge implementation.
        while True:
            msg = await websocket.receive_text()  # keep alive
            logger.debug(f"metadata channel received: {msg[:120]}")

    except WebSocketDisconnect:
        logger.info(f"WS metadata channel closed (request_id={request_id})")
