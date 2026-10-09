"""
Local test for /invocations-bidirectional-stream.

Verifies:
- WebSocket handshake succeeds
- Container responds to WebSocket Ping frames (Pong reply)
- Text and Binary frames flow both directions
- Graceful close on client disconnect

Requires: pip install websockets

This test hits the container directly on ws://localhost:8080. To test against
a deployed SageMaker endpoint (HTTP/2 on port 8443 with SigV4), use the AWS
`aws_sdk_sagemaker_runtime_http2` SDK — see reference/websocket.md for install
notes (Python 3.12+, pre-alpha status, awscrt platform wheels).
"""

import asyncio
import json
import sys

import websockets


async def test() -> None:
    uri = "ws://127.0.0.1:8080/invocations-bidirectional-stream"

    async with websockets.connect(uri) as ws:
        print("Connected")

        # 1) Send a client Ping; expect a Pong within a reasonable window.
        pong_waiter = await ws.ping()
        await asyncio.wait_for(pong_waiter, timeout=5)
        print("Ping/Pong OK")

        # 2) Send an initial config Text frame (adjust for your protocol).
        config = json.dumps({"sample_rate": 16000, "language": "en"})
        await ws.send(config)

        # 3) Send a small Binary frame (adjust to something your model accepts).
        await ws.send(b"\x00" * 3200)  # 100ms of 16kHz 16-bit silence

        # 4) Receive at least one response frame with a short timeout.
        try:
            reply = await asyncio.wait_for(ws.recv(), timeout=5)
            print(f"Received: {reply[:200]!r}")
        except asyncio.TimeoutError:
            print("WARNING: no response within 5s — model may still be warming up")
            sys.exit(1)

        # 5) Graceful close.
        await ws.close()
        print("Closed cleanly")


if __name__ == "__main__":
    asyncio.run(test())
