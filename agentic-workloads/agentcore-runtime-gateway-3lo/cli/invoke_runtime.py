#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Local CLI that invokes the deployed AgentCore Runtime as a Cognito user.

Flow (3LO passthrough):
  1. Discover the deployed resource ids from the CloudFormation stack outputs
     (RuntimeArn, UserPoolId, UserPoolClientId).
  2. Sign the chosen Cognito user in with username/password (ADMIN_USER_PASSWORD_AUTH)
     and grab their **access token**.
  3. POST the prompt to the runtime data-plane /invocations endpoint with the token
     as the bearer, and stream the Server-Sent-Events response to the terminal.

boto3's invoke_agent_runtime cannot be used here: it signs the request with SigV4,
but the runtime's CUSTOM_JWT authorizer expects a Cognito JWT bearer instead.

This is an interactive shell: it signs in once, then holds a back-and-forth
conversation with the agent, reusing a single runtime session id so context
persists across turns. Type 'exit'/'quit' (or Ctrl-D) to stop.

It also launches callback_server.py in the background, bound to the same token,
so first-time Slack OAuth session-binding redirects (triggered when a gateway
tool call needs downstream consent) can be completed on
http://localhost:8080/callback during the session.

Usage:
  python invoke_runtime.py                        # interactive chat
  python invoke_runtime.py "list the slack users" # send one prompt, then chat
  python invoke_runtime.py --user user2           # chat as a different user

Config resolution order for each value: CLI flag > environment variable > auto-discovery.
  --stack / STACK_NAME            (default: SlackGatewayStack)
  --region / AWS_REGION           (default: us-east-1)
  --user / COGNITO_USERNAME       (default: user1)
  --password / TEST_USER_PASSWORD (prompted if not set)
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError

try:
    from dotenv import load_dotenv

    # Load a .env sitting next to this script so username/password/overrides
    # are picked up without exporting them into the shell.
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

DEFAULT_STACK = "SlackGatewayStack"
DEFAULT_REGION = "us-east-1"
DEFAULT_USER = "user1"

# The gateway OAuth provider redirects consent back to this port
# (DefaultReturnUrl = http://localhost:8080/callback), so the callback server
# must listen here during the conversation.
CALLBACK_PORT = 8080
CALLBACK_SCRIPT = Path(__file__).parent / "callback_server.py"


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def discover_stack_outputs(region: str, stack_name: str) -> dict[str, str]:
    """Return the CloudFormation stack outputs as a {key: value} dict."""
    cfn = boto3.client("cloudformation", region_name=region)
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
    except ClientError as exc:
        _fail(
            f"could not read stack '{stack_name}' in {region}: {exc}. "
            "Is it deployed? Check --stack / --region."
        )
    stacks = resp.get("Stacks", [])
    if not stacks:
        _fail(f"stack '{stack_name}' not found in {region}.")
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}
    return outputs


def get_client_secret(region: str, user_pool_id: str, client_id: str) -> str | None:
    """Fetch the app client secret (the client is created with a generated secret)."""
    idp = boto3.client("cognito-idp", region_name=region)
    resp = idp.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
    return resp["UserPoolClient"].get("ClientSecret")


def secret_hash(username: str, client_id: str, client_secret: str) -> str:
    """Compute the Cognito SECRET_HASH for a client that has a secret."""
    msg = (username + client_id).encode("utf-8")
    digest = hmac.new(client_secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def authenticate(
    region: str,
    user_pool_id: str,
    client_id: str,
    client_secret: str | None,
    username: str,
    password: str,
) -> str:
    """Sign the user in and return their Cognito access token."""
    idp = boto3.client("cognito-idp", region_name=region)
    auth_params = {"USERNAME": username, "PASSWORD": password}
    if client_secret:
        auth_params["SECRET_HASH"] = secret_hash(username, client_id, client_secret)

    try:
        resp = idp.admin_initiate_auth(
            UserPoolId=user_pool_id,
            ClientId=client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters=auth_params,
        )
    except ClientError as exc:
        _fail(f"authentication failed for '{username}': {exc}")

    result = resp.get("AuthenticationResult")
    if not result or "AccessToken" not in result:
        challenge = resp.get("ChallengeName")
        _fail(
            f"no access token returned (challenge: {challenge}). "
            "The user may need its password reset to a permanent value."
        )
    return result["AccessToken"]


def stream_runtime(
    region: str, runtime_arn: str, access_token: str, prompt: str, session_id: str
) -> None:
    """POST one turn to the runtime /invocations endpoint and stream the SSE reply.

    The same ``session_id`` is reused for every turn so the runtime keeps the
    conversation in the same MicroVM and preserves context across the exchange.
    """
    encoded_arn = urllib.parse.quote(runtime_arn, safe="")
    url = (
        f"https://bedrock-agentcore.{region}.amazonaws.com/"
        f"runtimes/{encoded_arn}/invocations"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        # Must be 33+ chars; reused across the whole conversation.
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }

    with requests.post(
        url,
        params={"qualifier": "DEFAULT"},
        headers=headers,
        json={"prompt": prompt},
        stream=True,
        timeout=300,
    ) as resp:
        if not resp.ok:
            print(f"[HTTP {resp.status_code}] {resp.text}\n")
            return
        _render_sse(resp)


def _render_sse(resp: requests.Response) -> None:
    """Parse the SSE stream and print typed events from the runtime agent."""
    printed_answer_prefix = False
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        data = raw[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue

        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            print(data, end="", flush=True)
            continue

        etype = event.get("type")
        if etype == "text":
            if not printed_answer_prefix:
                print("agent> ", end="", flush=True)
                printed_answer_prefix = True
            print(event.get("text", ""), end="", flush=True)
        elif etype == "tool_use":
            print(f"\n  [tool] {event.get('name')}", flush=True)
            printed_answer_prefix = False
        elif etype == "reasoning":
            print(f"\n  [reasoning] {event.get('text', '')}", flush=True)
            printed_answer_prefix = False
        elif etype == "error":
            print(f"\n[error] {event.get('message')}", flush=True)

    print()  # final newline after the streamed answer


def _free_callback_port(port: int) -> None:
    """Kill any process already bound to the callback port (best effort)."""
    try:
        pids = subprocess.run(
            ["lsof", f"-ti:{port}"], capture_output=True, text=True
        ).stdout.split()
    except FileNotFoundError:
        return  # lsof not available; nothing to do
    for pid in pids:
        subprocess.run(["kill", "-9", pid], capture_output=True)
    if pids:
        time.sleep(1)


def start_callback_server(access_token: str, region: str) -> subprocess.Popen:
    """Start callback_server.py in the background, bound to the same token.

    This lets first-time OAuth session-binding redirects (triggered when a
    gateway Slack tool call needs downstream consent) be completed on
    http://localhost:8080/callback during the session.
    """
    _free_callback_port(CALLBACK_PORT)

    # Pass the access token through the environment rather than argv so it is not
    # exposed in process listings (`ps`) to other local users.
    child_env = os.environ.copy()
    child_env["AGENTCORE_USER_TOKEN"] = access_token

    process = subprocess.Popen(
        [
            sys.executable,
            str(CALLBACK_SCRIPT),
            "--region",
            region,
            "--port",
            str(CALLBACK_PORT),
            "--keep-alive",
        ],
        env=child_env,
    )
    # Give uvicorn a moment to bind the port.
    time.sleep(2)
    print(
        f"Callback server running on http://localhost:{CALLBACK_PORT}/callback "
        f"(pid {process.pid})."
    )
    print(
        "If a response contains an authorization URL, open it in your browser to "
        "complete OAuth session binding, then retry the prompt.\n"
    )
    return process


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invoke the AgentCore Runtime as a Cognito user (3LO passthrough)."
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="the prompt to send to the agent (prompted interactively if omitted)",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("COGNITO_USERNAME", DEFAULT_USER),
        help=f"Cognito username (default: {DEFAULT_USER})",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("TEST_USER_PASSWORD"),
        help="Cognito password (default: TEST_USER_PASSWORD env, else prompt)",
    )
    parser.add_argument(
        "--stack",
        default=os.environ.get("STACK_NAME", DEFAULT_STACK),
        help=f"CloudFormation stack name (default: {DEFAULT_STACK})",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", DEFAULT_REGION),
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass(f"Password for {args.user}: ")

    outputs = discover_stack_outputs(args.region, args.stack)
    runtime_arn = outputs.get("RuntimeArn")
    user_pool_id = outputs.get("UserPoolId")
    client_id = outputs.get("UserPoolClientId")
    missing = [
        name
        for name, val in [
            ("RuntimeArn", runtime_arn),
            ("UserPoolId", user_pool_id),
            ("UserPoolClientId", client_id),
        ]
        if not val
    ]
    if missing:
        _fail(f"stack '{args.stack}' is missing outputs: {', '.join(missing)}")

    client_secret = get_client_secret(args.region, user_pool_id, client_id)

    print(f"Signing in as {args.user}...")
    access_token = authenticate(
        args.region, user_pool_id, client_id, client_secret, args.user, password
    )

    # One session id for the whole conversation so the runtime keeps context
    # across turns (64 hex chars satisfies the 33+ char requirement).
    session_id = uuid.uuid4().hex + uuid.uuid4().hex
    print(f"Signed in as {args.user}. Session: {session_id[:12]}...\n")

    # Launch the callback server bound to this token, so first-time Slack OAuth
    # session-binding redirects can be completed during the conversation.
    callback_process = start_callback_server(access_token, args.region)

    try:
        # A prompt passed on the command line is sent first, then we go interactive.
        if args.prompt:
            print(f"you> {args.prompt}")
            stream_runtime(
                args.region, runtime_arn, access_token, args.prompt, session_id
            )

        print("Interactive mode. Type 'exit' or 'quit' (or Ctrl-D) to stop.\n")
        while True:
            try:
                user_input = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if user_input.lower() in {"exit", "quit"}:
                break
            if not user_input:
                continue
            try:
                stream_runtime(
                    args.region, runtime_arn, access_token, user_input, session_id
                )
            except requests.RequestException as exc:
                print(f"[request error] {exc}\n")
    finally:
        print("Shutting down callback server...")
        callback_process.terminate()
        try:
            callback_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            callback_process.kill()


if __name__ == "__main__":
    main()
