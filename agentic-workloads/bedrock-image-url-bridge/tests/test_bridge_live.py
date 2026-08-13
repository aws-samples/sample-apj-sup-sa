"""Live integration tests -- real calls against bedrock-mantle and
bedrock-runtime. Skipped by default; set BRIDGE_LIVE_TEST=1 to enable.

Requires AWS_PROFILE and AWS_REGION env vars pointing at an account with
Bedrock Mantle + Runtime access (verified live against a real account,
region us-east-1).
"""
from __future__ import annotations

import os

import pytest

from bridge import resolve_image_urls
from examples.mantle_chat_completions import call_mantle_chat_completions
from examples.mantle_responses_api import _extract_text, call_mantle_responses

LIVE = os.environ.get("BRIDGE_LIVE_TEST") == "1"
REGION = os.environ.get("AWS_REGION", "us-east-1")
TEST_IMAGE_URL = "https://placehold.co/64x64.jpg"
TEST_MODEL = "qwen.qwen3-vl-235b-a22b-instruct"

skip_reason = "Set BRIDGE_LIVE_TEST=1 (with AWS_PROFILE/AWS_REGION) to run live Bedrock calls"


@pytest.mark.skipif(not LIVE, reason=skip_reason)
def test_mantle_chat_completions_live():
    payload = {
        "model": TEST_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in one short sentence."},
                    {"type": "image_url", "image_url": {"url": TEST_IMAGE_URL}},
                ],
            }
        ],
    }
    resolved = resolve_image_urls(payload)
    response = call_mantle_chat_completions(resolved, region=REGION)
    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert len(content) > 0


@pytest.mark.skipif(not LIVE, reason=skip_reason)
def test_mantle_responses_api_live():
    payload = {
        "model": "openai.gpt-oss-120b",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Say hello in one short sentence."},
                ],
            }
        ],
    }
    # text-only sanity check on the Responses API path (image_url on
    # Responses API is exercised via resolve_image_urls unit tests; this
    # confirms the live call plumbing itself works end to end)
    response = call_mantle_responses(payload, region=REGION)
    text = _extract_text(response)
    assert isinstance(text, str)
    assert len(text) > 0


@pytest.mark.skipif(not LIVE, reason=skip_reason)
def test_runtime_converse_live():
    import boto3

    from examples.runtime_converse import _data_uri_to_bytes_and_format

    carrier_payload = {
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": TEST_IMAGE_URL}}]}]
    }
    resolved = resolve_image_urls(carrier_payload)
    data_uri = resolved["messages"][0]["content"][0]["image_url"]["url"]
    image_bytes, image_format = _data_uri_to_bytes_and_format(data_uri)

    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"), region_name=REGION)
    client = session.client("bedrock-runtime", region_name=REGION)
    response = client.converse(
        modelId="anthropic.claude-haiku-4-5",
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                    {"text": "Describe this image in one short sentence."},
                ],
            }
        ],
    )
    text = response["output"]["message"]["content"][0]["text"]
    assert isinstance(text, str)
    assert len(text) > 0
