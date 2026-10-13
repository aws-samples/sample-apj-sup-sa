"""AWS Lambda handler example: hosting the bridge behind a Lambda function,
using the *exact* Chat Completions request/response schema.

This is deliberately schema-faithful, not a custom API: the handler
accepts the same body you'd POST to `bedrock-mantle`'s
`/v1/chat/completions` (or to OpenAI's `/v1/chat/completions`) --
`{"model": ..., "messages": [...]}` -- and returns the same response
shape a Chat Completions call returns. The only behavior added is that
`http(s)://` image URLs in the request are resolved to inline `data:`
URIs before the request reaches Bedrock.

Why schema parity matters for migration: point an existing OpenAI Chat
Completions client (or anything speaking that wire format) at this
Lambda's Function URL or API Gateway endpoint instead of directly at
`bedrock-mantle`, and it works unmodified -- including with `https://`
image URLs that `bedrock-mantle` would otherwise reject. No custom
request/response shape to adapt to.

Deploy this however you deploy any Lambda function (SAM, CDK, Terraform,
console zip upload) -- there is nothing bridge-specific about the
deployment mechanics. Package `bridge/`, `requests`, and `Pillow` (boto3
ships with the Lambda Python runtime already) into your function's
deployment artifact, and grant the function's execution role
`bedrock:InvokeModel` for the model(s) you call.

Local smoke test (no deployment needed):
    python -c "
from examples.lambda_handler import handler
print(handler({
    'model': 'qwen.qwen3-vl-235b-a22b-instruct',
    'messages': [
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Describe this image in one sentence.'},
            {'type': 'image_url', 'image_url': {'url': 'https://placehold.co/64x64.jpg'}},
        ]}
    ],
}, None))
"
"""
from __future__ import annotations

import json
from typing import Any

from bridge import resolve_image_urls
from examples.mantle_chat_completions import call_mantle_chat_completions

_DEFAULT_REGION = "us-east-1"


def _openai_error(status_code: int, message: str, error_type: str) -> dict[str, Any]:
    """Build an OpenAI-style error envelope so error handling is also
    drop-in compatible with an OpenAI Chat Completions client."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": {"message": message, "type": error_type}}),
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point. `event` is the exact Chat Completions request
    body (`{"model": ..., "messages": [...]}`), passed either directly
    (a raw Lambda invoke) or as a JSON-encoded API Gateway proxy `body`.

    An optional top-level `region` key overrides the AWS region used to
    call `bedrock-mantle` (defaults to `us-east-1`); it is stripped
    before the request is forwarded, since it is not part of the Chat
    Completions schema.

    Returns an API-Gateway-proxy-compatible response dict whose `body`
    is the *exact* Chat Completions response JSON on success, or an
    OpenAI-style `{"error": {...}}` envelope on failure -- both are
    what an unmodified OpenAI client already expects.
    """
    payload = event
    if isinstance(event.get("body"), str):
        payload = json.loads(event["body"])

    if "messages" not in payload:
        return _openai_error(400, "'messages' is a required property", "invalid_request_error")

    region = payload.pop("region", _DEFAULT_REGION)

    try:
        resolved = resolve_image_urls(payload)
        response = call_mantle_chat_completions(resolved, region=region)
    except ValueError as exc:
        # resolve_image_urls() raises ValueError for every rejection case
        # (bad scheme, SSRF block, oversized download, invalid image) --
        # these are caller errors, not server errors.
        return _openai_error(400, str(exc), "invalid_request_error")
    except RuntimeError as exc:
        # call_mantle_chat_completions() raises RuntimeError on a non-200
        # response from bedrock-mantle.
        return _openai_error(502, str(exc), "upstream_error")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response),
    }
