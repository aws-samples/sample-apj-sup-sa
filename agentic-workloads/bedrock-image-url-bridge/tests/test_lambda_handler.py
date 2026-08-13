"""Unit tests for examples.lambda_handler.handler.

Verifies the handler is schema-faithful to Chat Completions: request in,
response out, unmodified shape -- with the only added behavior being
http(s) image_url resolution. No network or AWS calls (Bedrock is mocked).
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from examples.lambda_handler import handler


class LambdaHandlerTests(unittest.TestCase):
    def test_missing_messages_returns_openai_style_400(self):
        response = handler({"model": "some-model"}, None)
        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["type"], "invalid_request_error")
        self.assertIn("messages", body["error"]["message"])

    def test_api_gateway_proxy_body_is_json_decoded(self):
        response = handler({"body": json.dumps({"model": "m"})}, None)
        self.assertEqual(response["statusCode"], 400)

    @mock.patch("examples.lambda_handler.call_mantle_chat_completions")
    def test_happy_path_returns_exact_chat_completions_response(self, mock_call):
        chat_completions_response = {
            "id": "chatcmpl-abc",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "A red square."}}],
        }
        mock_call.return_value = chat_completions_response

        request_body = {
            "model": "qwen.qwen3-vl-235b-a22b-instruct",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this."},
                        {"type": "image_url", "image_url": {"url": "s3://bucket/key.jpg"}},
                    ],
                }
            ],
        }
        response = handler(request_body, None)

        self.assertEqual(response["statusCode"], 200)
        # Response body is the exact, unmodified Chat Completions JSON --
        # not a custom wrapper -- which is the whole point of the
        # schema-faithful design.
        self.assertEqual(json.loads(response["body"]), chat_completions_response)
        mock_call.assert_called_once()

    @mock.patch("examples.lambda_handler.call_mantle_chat_completions")
    def test_region_key_is_stripped_before_forwarding(self, mock_call):
        mock_call.return_value = {"choices": []}
        request_body = {
            "model": "m",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "region": "eu-west-1",
        }
        handler(request_body, None)

        forwarded_payload, kwargs = mock_call.call_args
        self.assertNotIn("region", forwarded_payload[0])
        self.assertEqual(kwargs["region"], "eu-west-1")

    @mock.patch("examples.lambda_handler.call_mantle_chat_completions")
    def test_invalid_scheme_returns_openai_style_400_not_500(self, mock_call):
        request_body = {
            "model": "m",
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///etc/passwd"}}]}
            ],
        }
        response = handler(request_body, None)
        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(json.loads(response["body"])["error"]["type"], "invalid_request_error")
        mock_call.assert_not_called()

    @mock.patch("examples.lambda_handler.call_mantle_chat_completions")
    def test_upstream_failure_returns_502(self, mock_call):
        mock_call.side_effect = RuntimeError("bedrock-mantle chat/completions failed (500): boom")
        request_body = {
            "model": "m",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        }
        response = handler(request_body, None)
        self.assertEqual(response["statusCode"], 502)
        self.assertEqual(json.loads(response["body"])["error"]["type"], "upstream_error")


if __name__ == "__main__":
    unittest.main()
