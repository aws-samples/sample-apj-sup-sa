"""Integration test for the deployed Strands analytics agent on AgentCore Runtime.

Validates the full critical path:
  Cognito auth → JWT-native AgentCore invoke (Bearer HTTPS) → SSE stream →
  spoken text extracted → no markdown

The runtime uses a CustomJWTAuthorizer (hard cutover from IAM/SigV4), so this MUST
invoke over plain HTTPS with `Authorization: Bearer <token>` — the SigV4 SDK
(`boto3 invoke_agent_runtime`) is rejected with "Authorization method mismatch". This
mirrors how app/voice/analytics_processor.py and the SPA text path actually call it.

Requires live AWS credentials and a deployed stack. Set all vars in app/voice/.env.
The conftest.py skip guard ensures this is a no-op when credentials are absent.
"""

import json
import os
import sys
import urllib.request
from urllib.parse import quote

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from auth import get_gateway_token

MARKDOWN_ARTIFACTS = ["**", "##", "| ", "```", "* ", "- ["]
VOICE_PROMPT = "What unicorns are available this weekend?"


def _parse_spoken_text(raw_sse: str) -> str:
    """Extract and concatenate all spoken-text chunks from a raw SSE response body."""
    texts = []
    for line in raw_sse.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            d = json.loads(line[6:])
            text = (
                d.get("event", {})
                .get("contentBlockDelta", {})
                .get("delta", {})
                .get("text")
            )
            if text:
                texts.append(text)
        except Exception:
            pass
    return "".join(texts)


@pytest.mark.integration
def test_invoke_returns_spoken_text():
    """AgentCore returns a non-empty spoken response with no markdown artifacts."""
    # Step 1: Cognito auth — must return an AccessToken (JWT)
    token = get_gateway_token()
    assert token.startswith("eyJ"), (
        "get_gateway_token() did not return a JWT. "
        "Check DEMO_USERNAME / DEMO_PASSWORD / COGNITO_CLIENT_ID."
    )

    # Step 2: Invoke AgentCore JWT-native (Bearer HTTPS, NOT the SigV4 SDK).
    region = os.environ["AWS_REGION"]
    arn = os.environ["AWS_AGENT_ARN"]
    qualifier = os.getenv("AWS_AGENT_QUALIFIER", "agentic_analytics_endpoint")
    invoke_url = (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(arn, safe='')}/invocations?qualifier={quote(qualifier, safe='')}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "itest-" + "p" * 33,
    }
    payload = {"prompt": VOICE_PROMPT, "mode": "voice"}
    req = urllib.request.Request(
        invoke_url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 (trusted AWS URL)
        status = r.status
        raw = r.read().decode("utf-8")

    # Step 3: HTTP-level assertions
    assert status == 200, f"Expected HTTP 200, got {status}: {raw[:300]}"

    # Step 4: Parse spoken text from SSE stream
    spoken = _parse_spoken_text(raw)

    assert len(spoken) > 20, (
        f"Spoken text too short ({len(spoken)} chars) — agent may have returned an error. "
        f"Raw response (first 500 chars):\n{raw[:500]}"
    )

    # Step 5: Voice SOP compliance — no markdown artifacts
    found = [a for a in MARKDOWN_ARTIFACTS if a in spoken]
    assert not found, (
        f"Markdown artifacts found in spoken text: {found}\n"
        f"Spoken text:\n{spoken[:500]}"
    )
