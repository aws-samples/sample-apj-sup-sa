"""Mantle Responses API example.

Same interception pattern as mantle_chat_completions.py, but against the
OpenAI Responses API shape (payload["input"][*]["content"][*] blocks of
type "input_image").

Usage:
    python -m examples.mantle_responses_api \\
        --image-url https://placehold.co/64x64.jpg \\
        --prompt "Describe this image in one sentence." \\
        --model qwen.qwen3-vl-235b-a22b-instruct
"""
from __future__ import annotations

import argparse
import json
import os

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from bridge import resolve_image_urls

# openai.gpt-oss-120b supports the /v1/responses API with vision input.
# qwen3-vl (used for chat/completions) does NOT support /v1/responses --
# verified live: "does not support the '/v1/responses' API".
DEFAULT_MODEL = "openai.gpt-oss-120b"


def call_mantle_responses(payload: dict, region: str = "us-east-1") -> dict:
    """POST a Responses API payload to bedrock-mantle, SigV4-signed using
    the credentials from AWS_PROFILE (or the default credential chain)
    and AWS_REGION/region.

    Raises RuntimeError with the response body on any non-200 response.
    """
    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"), region_name=region)
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError("No AWS credentials found (check AWS_PROFILE / credential chain)")
    frozen = creds.get_frozen_credentials()

    host = f"bedrock-mantle.{region}.api.aws"
    url = f"https://{host}/v1/responses"
    data = json.dumps(payload)

    req = AWSRequest(method="POST", url=url, data=data, headers={"Content-Type": "application/json"})
    SigV4Auth(frozen, "bedrock", region).add_auth(req)
    prepped = req.prepare()

    resp = requests.post(url, headers=dict(prepped.headers), data=data, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"bedrock-mantle responses failed ({resp.status_code}): {resp.text}")
    return resp.json()


def _extract_text(response: dict) -> str:
    """Pull the assistant's text out of a Responses API result, skipping
    reasoning blocks."""
    for item in response.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    return block.get("text", "")
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-url", required=True, help="https:// or s3:// image URL")
    parser.add_argument("--prompt", default="Describe this image in one sentence.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    payload = {
        "model": args.model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": args.prompt},
                    # NOTE: "detail" is required here -- omitting it makes
                    # bedrock-mantle's Rust-side deserializer reject the
                    # whole input_image variant with a generic
                    # "did not match any expected variant" error.
                    {"type": "input_image", "image_url": args.image_url, "detail": "auto"},
                ],
            }
        ],
    }

    resolved = resolve_image_urls(payload)
    response = call_mantle_responses(resolved, region=args.region)
    print(_extract_text(response))


if __name__ == "__main__":
    main()
