"""AWS Lambda handler example: hosting the bridge behind a Lambda function.

This is the "how do I actually host this" answer for the sample: the
bridge itself (`bridge/core.py`) is not a service -- it's a function you
call from wherever your application already builds a Bedrock request.
This file shows one common hosting shape: a Lambda function that accepts
{"image_url", "prompt", "model"} (e.g. from API Gateway or a direct
Lambda invoke) and returns the model's answer.

Deploy this however you deploy any Lambda function (SAM, CDK, Terraform,
console zip upload) -- there is nothing bridge-specific about the
deployment mechanics. Package `bridge/`, `requests`, `Pillow`, and
`boto3` (or use the boto3 already bundled in the Lambda Python runtime)
into your function's deployment artifact, and grant the function's
execution role `bedrock:InvokeModel` for the model(s) you call.

Local smoke test (no deployment needed):
    python -c "
from examples.lambda_handler import handler
print(handler({
    'image_url': 'https://placehold.co/64x64.jpg',
    'prompt': 'Describe this image in one sentence.',
}, None))
"
"""
from __future__ import annotations

import json
from typing import Any

from bridge import resolve_image_urls
from examples.mantle_chat_completions import DEFAULT_MODEL, call_mantle_chat_completions


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point.

    Expects `event` to contain (either directly, or under a JSON-encoded
    "body" key as API Gateway proxy integrations send it):
      - image_url (required): https://, s3://, or data: image URL
      - prompt (optional): defaults to a generic description prompt
      - model (optional): defaults to DEFAULT_MODEL
      - region (optional): defaults to AWS_REGION / us-east-1

    Returns an API-Gateway-proxy-compatible response dict. Direct Lambda
    invokes can ignore statusCode/headers and read body themselves.
    """
    body = event
    if isinstance(event.get("body"), str):
        body = json.loads(event["body"])

    image_url = body.get("image_url")
    if not image_url:
        return {"statusCode": 400, "body": json.dumps({"error": "image_url is required"})}

    prompt = body.get("prompt", "Describe this image in one sentence.")
    model = body.get("model", DEFAULT_MODEL)
    region = body.get("region", "us-east-1")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    }

    try:
        resolved = resolve_image_urls(payload)
        response = call_mantle_chat_completions(resolved, region=region)
    except ValueError as exc:
        # resolve_image_urls() raises ValueError for every rejection case
        # (bad scheme, SSRF block, oversized download, invalid image) --
        # these are caller errors, not server errors.
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
    except RuntimeError as exc:
        # call_mantle_chat_completions() raises RuntimeError on a non-200
        # response from bedrock-mantle.
        return {"statusCode": 502, "body": json.dumps({"error": str(exc)})}

    answer = response["choices"][0]["message"]["content"]
    return {"statusCode": 200, "body": json.dumps({"answer": answer})}
