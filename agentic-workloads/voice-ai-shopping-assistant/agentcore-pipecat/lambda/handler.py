"""Lambda /start handler.

Replaces the Fargate ALB "start a bot" endpoint. On invocation it calls
AgentCore Runtime (InvokeAgentRuntime) with the Daily room URL so the bot
joins the room, then returns that room URL to the caller. Designed to be
fronted by a Lambda Function URL.

Environment variables:
  AGENT_RUNTIME_ARN  - ARN of the deployed AgentCore agent (set by launch.sh)
  DAILY_ROOM_URL     - Daily room the bot should join
  ALLOWED_ORIGIN     - CORS origin to allow (default "*")
  DEMO_USER_ID       - default AgentCore Memory actor id (long-term preferences)
"""

import json
import os
import time
import uuid

import boto3

_bedrock = boto3.client("bedrock-agentcore")

AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN")
DAILY_ROOM_URL = os.environ.get("DAILY_ROOM_URL")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
DEMO_USER_ID = os.environ.get("DEMO_USER_ID", "demo-user")

_CORS = {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps(body),
    }


def handler(event, context):
    # Handle CORS preflight from Function URL (payload format 2.0).
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
    )
    if method == "OPTIONS":
        return _response(200, {"ok": True})

    if not AGENT_RUNTIME_ARN:
        return _response(500, {"error": "AGENT_RUNTIME_ARN not configured"})
    if not DAILY_ROOM_URL:
        return _response(500, {"error": "DAILY_ROOM_URL not configured"})

    # Single-user demo. AgentCore Memory keys long-term prefs by user_id (the
    # actor), so the agent still recognises the returning shopper. We use a FRESH
    # session_id per connect so each demo starts with an empty cart and a clean
    # conversation context (a stable session_id would carry the previous run's
    # cart over and replay its turns — confusing in a live demo).
    payload = {
        "room_url": DAILY_ROOM_URL,
        "user_id": DEMO_USER_ID,
        "session_id": f"{DEMO_USER_ID}-{uuid.uuid4().hex[:12]}",
    }
    raw_body = event.get("body")
    if raw_body:
        try:
            incoming = json.loads(raw_body)
            if isinstance(incoming, dict):
                payload.update(incoming)
        except (ValueError, TypeError):
            pass

    resp = _bedrock.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        contentType="application/json",
        payload=json.dumps(payload),
        runtimeSessionId=str(uuid.uuid4()),
    )

    # Wait for the bot to report the room the client should join. The avatar path
    # (TavusTransport) creates its room dynamically and reports it in a "ready"
    # event with a room_url; the audio-only path reports the room it joined. Fall
    # back to the payload room if the bot doesn't report one in time.
    returned_room_url = payload.get("room_url")
    try:
        stream = resp.get("response")
        if stream is not None and "text/event-stream" in resp.get("contentType", ""):
            deadline = time.time() + 25  # stay under API Gateway's ~29s integration cap
            for line in stream.iter_lines(chunk_size=1):
                if time.time() > deadline:
                    break
                if not line:
                    continue
                line = line.decode("utf-8")
                if not line.startswith("data: "):
                    continue
                try:
                    evt = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if not isinstance(evt, dict):
                    continue
                if evt.get("room_url"):
                    returned_room_url = evt["room_url"]
                    break
                # A terminal "ready"/"completed"/"error" without a room_url means
                # use the fallback (e.g. audio-only with no surfaced url, or failure).
                if evt.get("status") in ("ready", "completed", "error"):
                    break
    except Exception:
        # Returning the fallback room URL is still useful if we couldn't read the stream.
        pass

    return _response(200, {"room_url": returned_room_url, "status": "ok"})
