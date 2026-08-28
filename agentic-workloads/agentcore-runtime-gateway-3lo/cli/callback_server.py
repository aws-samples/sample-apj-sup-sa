# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Localhost callback server for AgentCore OAuth (Slack 3LO) session binding.

Runs an HTTP server on http://localhost:8080/callback. When a gateway tool call
needs downstream consent, the runtime returns an authorization URL. After the
signed-in user opens it and grants consent to Slack, the browser is redirected
back here with a ``session_id`` query parameter (the session URI). This server
calls the AgentCore Identity ``CompleteResourceTokenAuth`` API, presenting the
user's identity together with the session URI. AgentCore validates that the user
who started the flow is the same one who completed consent (URL session binding)
before exchanging the authorization code for a Slack user token.

The gateway's OAuth credential provider is configured with
``DefaultReturnUrl = http://localhost:8080/callback``, so this server must be
listening on port 8080 during the conversation.

Usage:
    # Gateway user tool-invocation flow (pass the user's Cognito JWT via env var
    # so the token is never exposed in the process argument list / `ps` output)
    AGENTCORE_USER_TOKEN="<cognito_access_token>" python callback_server.py --region us-east-1

    # Admin flow (pass the workload user id)
    python callback_server.py --user-id "<user_id>" --region us-east-1
"""

from __future__ import annotations

import argparse
import os
import threading

import boto3
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI()

# Populated from CLI args at startup.
user_identifier: dict = {}
agentcore_client = None
keep_alive = False


@app.get("/callback", response_class=HTMLResponse)
async def callback(request: Request):
    """Handle the OAuth redirect and complete session binding."""
    session_uri = request.query_params.get("session_id")

    if not session_uri:
        return HTMLResponse(
            content="<h1>Error</h1><p>Missing session_id parameter.</p>",
            status_code=400,
        )

    try:
        agentcore_client.complete_resource_token_auth(
            userIdentifier=user_identifier,
            sessionUri=session_uri,
        )
    except Exception as exc:  # noqa: BLE001 - surface details to the console
        print(f"Session binding failed: {exc}")
        return HTMLResponse(
            content=(
                "<h1>Session Binding Failed</h1>"
                "<p>An error occurred during session binding. "
                "Check the console for details.</p>"
            ),
            status_code=500,
        )

    print("Session binding complete.")

    # By default this is a single-use callback and exits after one binding.
    # When run under invoke_runtime.py (--keep-alive) it stays up to serve
    # multiple consents across an interactive session.
    if not keep_alive:
        threading.Timer(0.5, lambda: os._exit(0)).start()

    return HTMLResponse(
        content=(
            "<h1>Session Binding Complete</h1>"
            "<p>Authorization code flow completed successfully. "
            "You can close this tab.</p>"
        ),
        status_code=200,
    )


def main() -> None:
    global user_identifier, agentcore_client, keep_alive

    parser = argparse.ArgumentParser(
        description="Localhost callback server for AgentCore OAuth session binding"
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--user-id", help="User ID for the admin flow")
    group.add_argument(
        "--user-token",
        help=(
            "User JWT (Cognito access token) for the gateway user tool-invocation "
            "flow. INSECURE: visible in `ps`/process listings — prefer the "
            "AGENTCORE_USER_TOKEN environment variable instead."
        ),
    )
    parser.add_argument(
        "--region", default=None, help="AWS region (defaults to the session region)"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port to listen on (default: 8080)"
    )
    parser.add_argument(
        "--keep-alive",
        action="store_true",
        help="Keep serving after the first callback (used by invoke_runtime.py)",
    )
    args = parser.parse_args()

    keep_alive = args.keep_alive

    # The access token is read from AGENTCORE_USER_TOKEN by preference so it is
    # never placed on the command line (where it would show up in `ps` and be
    # readable by other local users). The --user-token flag is kept only as an
    # explicit, discouraged fallback.
    env_token = os.environ.get("AGENTCORE_USER_TOKEN")

    if args.user_id:
        user_identifier = {"userId": args.user_id}
        print("Mode: admin (userId)")
    elif env_token:
        user_identifier = {"userToken": env_token}
        print("Mode: gateway user (userToken from AGENTCORE_USER_TOKEN)")
    elif args.user_token:
        user_identifier = {"userToken": args.user_token}
        print("Mode: gateway user (userToken from --user-token)")
    else:
        parser.error(
            "provide --user-id, or set AGENTCORE_USER_TOKEN (preferred) / pass "
            "--user-token for the gateway user flow"
        )

    agentcore_client = boto3.client("bedrock-agentcore", region_name=args.region)

    print(f"Callback URL: http://localhost:{args.port}/callback")
    print("Waiting for OAuth redirect...")

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
