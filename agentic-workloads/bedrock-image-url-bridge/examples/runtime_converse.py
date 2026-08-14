"""Bedrock Runtime Converse API example.

Contrast case: unlike bedrock-mantle, the native bedrock-runtime Converse
API has no image_url concept at all -- image blocks only accept raw
bytes (or an s3Location for some models). It never accepts an arbitrary
https URL. This example reuses the same resolve_image_urls() bridge to
turn a Chat-Completions-shaped payload's http(s) image_url into a
data: URI, then extracts the base64 bytes for a native Converse call.

This shows the bridge is genuinely endpoint-agnostic: the same
"make this URL into safe inline bytes" utility feeds both an
OpenAI-compatible endpoint (which wants a data: URI) and a native
endpoint (which wants raw bytes).

Usage:
    python -m examples.runtime_converse \\
        --image-url https://placehold.co/64x64.jpg \\
        --prompt "Describe this image in one sentence." \\
        --model anthropic.claude-haiku-4-5
"""
from __future__ import annotations

import argparse
import base64
import os

import boto3

from bridge import resolve_image_urls

# Cross-region inference profile ID -- required for on-demand Converse
# calls to this model in most accounts/regions.
DEFAULT_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

_MIME_TO_FORMAT = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _data_uri_to_bytes_and_format(data_uri: str) -> tuple[bytes, str]:
    """Split a data:<mime>;base64,<...> URI into (raw_bytes, converse_format)."""
    header, encoded = data_uri.split(",", 1)
    mime = header.split(":", 1)[1].split(";", 1)[0]
    fmt = _MIME_TO_FORMAT.get(mime)
    if fmt is None:
        raise ValueError(f"Unsupported image mime type for Converse: {mime!r}")
    return base64.b64decode(encoded), fmt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-url", required=True, help="https:// image URL")
    parser.add_argument("--prompt", default="Describe this image in one sentence.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    # Reuse the same bridge shape (Chat Completions) purely as a
    # convenient carrier for the URL -> data: URI conversion.
    carrier_payload = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": args.image_url}}],
            }
        ]
    }
    resolved = resolve_image_urls(carrier_payload)
    data_uri = resolved["messages"][0]["content"][0]["image_url"]["url"]
    image_bytes, image_format = _data_uri_to_bytes_and_format(data_uri)

    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"), region_name=args.region)
    client = session.client("bedrock-runtime", region_name=args.region)

    response = client.converse(
        modelId=args.model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                    {"text": args.prompt},
                ],
            }
        ],
    )
    print(response["output"]["message"]["content"][0]["text"])


if __name__ == "__main__":
    main()
