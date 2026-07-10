"""
Lambda function: generates a pre-signed WebSocket URL for AgentCore voice agent.
Uses the official bedrock_agentcore SDK which handles SigV4 signing correctly.
Max expiry is 300 seconds (SDK limit), so tokens are refreshed every ~4 minutes.

Returns:
  { "signed_url": "wss://...", "expires_at": <epoch>, "session_id": "..." }
"""

import json
import os
import time
import uuid

REGION = os.environ.get("AWS_REGION", "us-west-2")
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
EXPIRES_IN = 300  # SDK max is 300 seconds


def lambda_handler(event, context):
    """Lambda entry point — returns signed WS URL using the AgentCore SDK."""
    if not AGENT_RUNTIME_ARN:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "AGENT_RUNTIME_ARN environment variable is not configured"}),
        }

    try:
        from bedrock_agentcore.runtime.agent_core_runtime_client import AgentCoreRuntimeClient

        client = AgentCoreRuntimeClient(region=REGION)
        session_id = str(uuid.uuid4())

        presigned_url = client.generate_presigned_url(
            runtime_arn=AGENT_RUNTIME_ARN,
            session_id=session_id,
            expires=EXPIRES_IN,
        )

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({
                "signed_url": presigned_url,
                "expires_at": int(time.time()) + EXPIRES_IN,
                "session_id": session_id,
                "expires_in": EXPIRES_IN,
            }),
        }

    except ImportError:
        # Fallback: manual SigV4 presigning if SDK not available
        return fallback_presign(event, context)
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


def fallback_presign(event, context):
    """Manual SigV4 presigning fallback."""
    import boto3, hmac, hashlib, urllib.parse
    from datetime import datetime, timezone
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    session = boto3.Session()
    creds = session.get_credentials().get_frozen_credentials()

    AGENT_ID = AGENT_RUNTIME_ARN.split("/")[-1]
    HOST = f"bedrock-agentcore.{REGION}.amazonaws.com"
    WS_PATH = f"/runtimes/{AGENT_ID}/ws"
    session_id = str(uuid.uuid4())

    https_url = f"https://{HOST}{WS_PATH}?X-Amzn-Bedrock-AgentCore-Runtime-Session-Id={session_id}"
    request = AWSRequest(method="GET", url=https_url)
    SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(request)

    # Convert to pre-signed query string
    presigned = request.prepare()
    signed_url = presigned.url.replace("https://", "wss://")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({
            "signed_url": signed_url,
            "expires_at": int(time.time()) + EXPIRES_IN,
            "session_id": session_id,
            "expires_in": EXPIRES_IN,
        }),
    }
