"""Mantle Chat Completions example.

Sends a Chat Completions request to the bedrock-mantle endpoint with a
plain https image URL, routing it through resolve_image_urls() first
since Mantle rejects raw https image_url values.

Usage:
    python -m examples.mantle_chat_completions \\
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

DEFAULT_MODEL = "qwen.qwen3-vl-235b-a22b-instruct"


def call_mantle_chat_completions(payload: dict, region: str = "us-east-1") -> dict:
    """POST a Chat Completions payload to bedrock-mantle, SigV4-signed
    using the credentials from AWS_PROFILE (or the default credential
    chain) and AWS_REGION/region.

    Raises RuntimeError with the response body on any non-200 response.
    """
    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"), region_name=region)
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError("No AWS credentials found (check AWS_PROFILE / credential chain)")
    frozen = creds.get_frozen_credentials()

    host = f"bedrock-mantle.{region}.api.aws"
    url = f"https://{host}/v1/chat/completions"
    data = json.dumps(payload)

    req = AWSRequest(method="POST", url=url, data=data, headers={"Content-Type": "application/json"})
    SigV4Auth(frozen, "bedrock", region).add_auth(req)
    prepped = req.prepare()

    resp = requests.post(url, headers=dict(prepped.headers), data=data, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"bedrock-mantle chat/completions failed ({resp.status_code}): {resp.text}")
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-url", required=True, help="https:// or s3:// image URL")
    parser.add_argument("--prompt", default="Describe this image in one sentence.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    payload = {
        "model": args.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": args.prompt},
                    {"type": "image_url", "image_url": {"url": args.image_url}},
                ],
            }
        ],
    }

    resolved = resolve_image_urls(payload)
    response = call_mantle_chat_completions(resolved, region=args.region)
    print(response["choices"][0]["message"]["content"])


if __name__ == "__main__":
    main()
