"""
Voice token service.
Calls a Lambda function to get a SigV4 pre-signed WebSocket URL for the AgentCore voice agent.
Caches the token and auto-refreshes when it's within 5 minutes of expiry.
The browser then connects directly to AgentCore using the signed URL.
"""

import json
import logging
import os
import time

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import httpx

logger = logging.getLogger(__name__)

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
LAMBDA_FUNCTION_NAME = os.getenv("VOICE_TOKEN_LAMBDA", "tasty-bites-voice-token")

# Cached token: {signed_url, expires_at}
_token_cache: dict | None = None
_REFRESH_BUFFER = 30  # Refresh 30 sec before expiry (tokens only last 300s)


def _invoke_lambda() -> dict:
    """Call the voice token Lambda via boto3."""
    client = boto3.client("lambda", region_name=AWS_REGION)
    resp = client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps({}),
    )
    payload = json.loads(resp["Payload"].read())
    body = json.loads(payload.get("body", "{}"))
    return body


def get_voice_token() -> dict:
    """
    Get a valid signed WebSocket URL for the AgentCore voice agent.
    Returns cached token if still valid, otherwise fetches a fresh one from Lambda.
    """
    global _token_cache

    now = int(time.time())

    # Return cached if still valid (with buffer)
    if _token_cache and _token_cache.get("expires_at", 0) > now + _REFRESH_BUFFER:
        logger.debug("Returning cached voice token")
        return _token_cache

    # Fetch fresh token from Lambda
    logger.info("Fetching fresh voice token from Lambda")
    token_data = _invoke_lambda()

    if not token_data.get("signed_url"):
        raise ValueError("Lambda did not return a signed_url")

    _token_cache = token_data
    logger.info(f"Voice token cached, expires at: {token_data.get('expires_at')}")
    return token_data
