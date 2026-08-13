"""Unit tests for bridge.core.resolve_image_urls.

No network or AWS calls -- all HTTP is mocked via unittest.mock.
"""
from __future__ import annotations

import base64
import io
import unittest
from unittest import mock

from PIL import Image

from bridge.core import resolve_image_urls


def _make_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (4, 4), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class _FakeResponse:
    """Minimal stand-in for requests.Response covering what core.py uses."""

    def __init__(self, content: bytes, status_code: int = 200, headers: dict | None = None):
        self._content = content
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = False
        self._closed = False

    def iter_content(self, chunk_size: int = 65536):
        data = self._content
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def close(self):
        self._closed = True


class PassthroughTests(unittest.TestCase):
    def test_s3_url_passthrough_chat_completions(self):
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "s3://bucket/key.jpg"}}],
                }
            ]
        }
        result = resolve_image_urls(payload)
        self.assertEqual(
            result["messages"][0]["content"][0]["image_url"]["url"], "s3://bucket/key.jpg"
        )

    def test_data_uri_passthrough(self):
        data_uri = "data:image/jpeg;base64,AAAA"
        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_uri}}]}]
        }
        result = resolve_image_urls(payload)
        self.assertEqual(result["messages"][0]["content"][0]["image_url"]["url"], data_uri)

    def test_no_image_blocks_unchanged(self):
        payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]}
        result = resolve_image_urls(payload)
        self.assertEqual(result, payload)

    def test_does_not_mutate_input(self):
        payload = {
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "s3://bucket/key.jpg"}}]}
            ]
        }
        original = {
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "s3://bucket/key.jpg"}}]}
            ]
        }
        resolve_image_urls(payload)
        self.assertEqual(payload, original)


class HttpsConversionTests(unittest.TestCase):
    @mock.patch("bridge.core.socket.getaddrinfo")
    @mock.patch("bridge.core.requests.get")
    def test_https_url_converted_to_data_uri_string_form(self, mock_get, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        jpeg_bytes = _make_jpeg_bytes()
        mock_get.return_value = _FakeResponse(jpeg_bytes, headers={"Content-Length": str(len(jpeg_bytes))})

        payload = {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_image", "image_url": "https://example.com/cat.jpg"}],
                }
            ]
        }
        result = resolve_image_urls(payload)
        resolved_url = result["input"][0]["content"][0]["image_url"]
        self.assertTrue(resolved_url.startswith("data:image/jpeg;base64,"))
        b64_part = resolved_url.split(",", 1)[1]
        self.assertEqual(base64.b64decode(b64_part), jpeg_bytes)

    @mock.patch("bridge.core.socket.getaddrinfo")
    @mock.patch("bridge.core.requests.get")
    def test_https_url_converted_object_form(self, mock_get, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        jpeg_bytes = _make_jpeg_bytes()
        mock_get.return_value = _FakeResponse(jpeg_bytes, headers={"Content-Length": str(len(jpeg_bytes))})

        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}}],
                }
            ]
        }
        result = resolve_image_urls(payload)
        resolved_url = result["messages"][0]["content"][0]["image_url"]["url"]
        self.assertTrue(resolved_url.startswith("data:image/jpeg;base64,"))


class SchemeAndInsecureHttpTests(unittest.TestCase):
    def test_unsupported_scheme_raises(self):
        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///etc/passwd"}}]}]
        }
        with self.assertRaisesRegex(ValueError, "Unsupported image_url scheme"):
            resolve_image_urls(payload)

    def test_http_rejected_by_default(self):
        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://example.com/cat.jpg"}}]}]
        }
        with self.assertRaisesRegex(ValueError, "insecure http"):
            resolve_image_urls(payload)

    @mock.patch("bridge.core.socket.getaddrinfo")
    @mock.patch("bridge.core.requests.get")
    def test_http_allowed_with_override(self, mock_get, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        jpeg_bytes = _make_jpeg_bytes()
        mock_get.return_value = _FakeResponse(jpeg_bytes, headers={"Content-Length": str(len(jpeg_bytes))})

        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://example.com/cat.jpg"}}]}]
        }
        result = resolve_image_urls(payload, allow_insecure_http=True)
        resolved_url = result["messages"][0]["content"][0]["image_url"]["url"]
        self.assertTrue(resolved_url.startswith("data:image/jpeg;base64,"))


class SsrfGuardTests(unittest.TestCase):
    @mock.patch("bridge.core.socket.getaddrinfo")
    def test_blocks_loopback_ip(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("127.0.0.1", 0))]
        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://evil.example/x.jpg"}}]}]
        }
        with self.assertRaisesRegex(ValueError, "blocked address"):
            resolve_image_urls(payload)

    @mock.patch("bridge.core.socket.getaddrinfo")
    def test_blocks_metadata_ip(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("169.254.169.254", 0))]
        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://evil.example/x.jpg"}}]}]
        }
        with self.assertRaisesRegex(ValueError, "blocked address"):
            resolve_image_urls(payload)

    @mock.patch("bridge.core.socket.getaddrinfo")
    def test_blocks_private_ip(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("10.0.0.5", 0))]
        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://evil.example/x.jpg"}}]}]
        }
        with self.assertRaisesRegex(ValueError, "blocked address"):
            resolve_image_urls(payload)


class SizeCapTests(unittest.TestCase):
    @mock.patch("bridge.core.socket.getaddrinfo")
    @mock.patch("bridge.core.requests.get")
    def test_content_length_over_cap_raises_before_reading_body(self, mock_get, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("93.184.216.34", 0))]

        class _NeverIterateResponse(_FakeResponse):
            def iter_content(self, chunk_size: int = 65536):
                raise AssertionError("iter_content should not be called when Content-Length already exceeds cap")

        mock_get.return_value = _NeverIterateResponse(b"x" * 10, headers={"Content-Length": str(50 * 1024 * 1024)})

        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/huge.jpg"}}]}]
        }
        with self.assertRaisesRegex(ValueError, "exceeds max_bytes"):
            resolve_image_urls(payload, max_bytes=20 * 1024 * 1024)

    @mock.patch("bridge.core.socket.getaddrinfo")
    @mock.patch("bridge.core.requests.get")
    def test_streamed_body_over_cap_without_content_length(self, mock_get, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        big_body = b"x" * (1024 * 1024)  # 1 MiB, no Content-Length header
        mock_get.return_value = _FakeResponse(big_body, headers={})

        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/huge.jpg"}}]}]
        }
        with self.assertRaisesRegex(ValueError, "exceeded max_bytes"):
            resolve_image_urls(payload, max_bytes=1024)  # tiny cap forces overflow mid-stream


class InvalidImageTests(unittest.TestCase):
    @mock.patch("bridge.core.socket.getaddrinfo")
    @mock.patch("bridge.core.requests.get")
    def test_non_image_bytes_rejected(self, mock_get, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        not_an_image = b"not an image, just plain text bytes"
        mock_get.return_value = _FakeResponse(not_an_image, headers={"Content-Length": str(len(not_an_image))})

        payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/fake.jpg"}}]}]
        }
        with self.assertRaisesRegex(ValueError, "not a valid image"):
            resolve_image_urls(payload)


class MixedShapeTests(unittest.TestCase):
    @mock.patch("bridge.core.socket.getaddrinfo")
    @mock.patch("bridge.core.requests.get")
    def test_both_chat_completions_and_responses_shapes_in_one_call(self, mock_get, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        jpeg_bytes = _make_jpeg_bytes()
        mock_get.return_value = _FakeResponse(jpeg_bytes, headers={"Content-Length": str(len(jpeg_bytes))})

        chat_payload = {
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}}]}]
        }
        responses_payload = {
            "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "https://example.com/b.jpg"}]}]
        }

        chat_result = resolve_image_urls(chat_payload)
        responses_result = resolve_image_urls(responses_payload)

        self.assertTrue(
            chat_result["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )
        self.assertTrue(
            responses_result["input"][0]["content"][0]["image_url"].startswith("data:image/jpeg;base64,")
        )


if __name__ == "__main__":
    unittest.main()
