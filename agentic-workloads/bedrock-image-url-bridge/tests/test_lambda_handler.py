"""Unit tests for examples.lambda_handler.handler.

No network or AWS calls -- the Bedrock call is mocked.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from examples.lambda_handler import handler


class LambdaHandlerTests(unittest.TestCase):
    def test_missing_image_url_returns_400(self):
        response = handler({"prompt": "hi"}, None)
        self.assertEqual(response["statusCode"], 400)
        self.assertIn("image_url", json.loads(response["body"])["error"])

    def test_api_gateway_proxy_body_is_json_decoded(self):
        response = handler({"body": json.dumps({"prompt": "hi"})}, None)
        self.assertEqual(response["statusCode"], 400)

    @mock.patch("examples.lambda_handler.call_mantle_chat_completions")
    def test_happy_path_returns_answer(self, mock_call):
        mock_call.return_value = {
            "choices": [{"message": {"content": "A red square."}}]
        }
        response = handler(
            {"image_url": "s3://bucket/key.jpg", "prompt": "Describe this."}, None
        )
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["answer"], "A red square.")
        mock_call.assert_called_once()

    @mock.patch("examples.lambda_handler.call_mantle_chat_completions")
    def test_invalid_scheme_returns_400_not_500(self, mock_call):
        response = handler({"image_url": "file:///etc/passwd"}, None)
        self.assertEqual(response["statusCode"], 400)
        mock_call.assert_not_called()

    @mock.patch("examples.lambda_handler.call_mantle_chat_completions")
    def test_upstream_failure_returns_502(self, mock_call):
        mock_call.side_effect = RuntimeError("bedrock-mantle chat/completions failed (500): boom")
        response = handler({"image_url": "s3://bucket/key.jpg"}, None)
        self.assertEqual(response["statusCode"], 502)


if __name__ == "__main__":
    unittest.main()
