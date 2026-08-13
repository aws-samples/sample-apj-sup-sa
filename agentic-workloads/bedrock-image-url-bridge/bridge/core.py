"""Utilities to make OpenAI-style image_url content blocks safe to send to
Amazon Bedrock's OpenAI-compatible bedrock-mantle endpoint (and any other
endpoint that only accepts inline data or s3:// URIs, not arbitrary https
URLs).

Amazon Bedrock Mantle rejects plain https image_url values with:

    "Only inline image data URLs and S3 URLs are supported."

It natively accepts:
  - s3://bucket/key            (Mantle fetches it itself)
  - data:<mime>;base64,<...>   (inline)

This module rewrites only http(s):// URLs into inline data: URIs, leaving
s3:// and data: URIs untouched. It is deliberately dependency-light
(requests + Pillow only) and framework-free -- it is meant to be read,
understood, and copied into your own code, not installed as a library.
"""
from __future__ import annotations

import base64
import copy
import io
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse, urljoin

import requests
from PIL import Image, UnidentifiedImageError

_METADATA_IP = ipaddress.ip_address("169.254.169.254")


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if the IP is loopback, private, link-local, or the
    cloud metadata address -- the classic SSRF target set."""
    ip = ipaddress.ip_address(ip_str)
    if ip == _METADATA_IP:
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _validate_fetch_target(url: str, *, allow_insecure_http: bool) -> None:
    """Validate scheme and resolved IP before making a request. Raises
    ValueError on any policy violation. Called once per redirect hop."""
    parsed = urlparse(url)
    if parsed.scheme == "http" and not allow_insecure_http:
        raise ValueError(
            f"Refusing to fetch insecure http:// URL: {url!r} "
            "(pass allow_insecure_http=True to override for local testing only)"
        )
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme for fetch: {parsed.scheme!r} in {url!r}")
    if not parsed.hostname:
        raise ValueError(f"URL has no hostname: {url!r}")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host {parsed.hostname!r}: {exc}") from exc

    for family, _, _, _, sockaddr in resolved:
        ip_str = sockaddr[0]
        if _is_blocked_ip(ip_str):
            raise ValueError(
                f"Refusing to fetch {url!r}: resolves to blocked address {ip_str} "
                "(private/loopback/link-local/metadata IP -- possible SSRF)"
            )


def _download_image_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    allow_insecure_http: bool,
    max_redirects: int = 2,
) -> bytes:
    """Stream-download url with SSRF guard, size cap, and a bounded,
    re-validated redirect chain. Returns raw bytes on success."""
    current_url = url
    for _hop in range(max_redirects + 1):
        _validate_fetch_target(current_url, allow_insecure_http=allow_insecure_http)
        resp = requests.get(
            current_url,
            stream=True,
            timeout=timeout_seconds,
            allow_redirects=False,
        )
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise ValueError(f"Redirect from {current_url!r} had no Location header")
            current_url = urljoin(current_url, location)
            continue

        resp.raise_for_status()

        content_length = resp.headers.get("Content-Length")
        if content_length is not None:
            try:
                content_length_int = int(content_length)
            except ValueError:
                resp.close()
                raise ValueError(
                    f"Malformed Content-Length header from {current_url!r}: {content_length!r}"
                )
            if content_length_int > max_bytes:
                resp.close()
                raise ValueError(
                    f"Refusing download from {current_url!r}: "
                    f"Content-Length {content_length_int} exceeds max_bytes={max_bytes}"
            )

        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > max_bytes:
                resp.close()
                raise ValueError(
                    f"Refusing download from {current_url!r}: "
                    f"exceeded max_bytes={max_bytes} while streaming"
                )
            chunks.append(chunk)
        resp.close()
        return b"".join(chunks)

    raise ValueError(f"Too many redirects (> {max_redirects}) fetching {url!r}")


def _bytes_to_data_uri(raw: bytes) -> str:
    """Verify raw bytes are a real image (via Pillow) and return a
    data:<mime>;base64,<...> URI. The mime type comes from the verified
    image format, never from the URL or a server-supplied header."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        fmt = img.format
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Downloaded content is not a valid image: {exc}") from exc

    if not fmt:
        raise ValueError("Could not determine image format after verification")

    mime = Image.MIME.get(fmt, f"image/{fmt.lower()}")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _resolve_url(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    allow_insecure_http: bool,
) -> str:
    """Resolve a single image_url string: pass through s3:// and data:,
    convert http(s):// to a data: URI, reject anything else."""
    if url.startswith("s3://") or url.startswith("data:"):
        return url
    if url.startswith("http://") or url.startswith("https://"):
        raw = _download_image_bytes(
            url,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
            allow_insecure_http=allow_insecure_http,
        )
        return _bytes_to_data_uri(raw)

    scheme = url.split(":", 1)[0] if ":" in url else "(none)"
    raise ValueError(f"Unsupported image_url scheme {scheme!r} in {url!r}")


def resolve_image_urls(
    payload: dict[str, Any],
    *,
    max_bytes: int = 20 * 1024 * 1024,
    timeout_seconds: float = 10.0,
    allow_insecure_http: bool = False,
) -> dict[str, Any]:
    """Rewrite plain http(s):// image_url values in a Bedrock-mantle
    request payload into inline data: URIs, leaving s3:// and data: URIs
    untouched.

    Walks both supported payload shapes:
      - Chat Completions: payload["messages"][*]["content"][*] blocks of
        {"type": "image_url", "image_url": "<url>" | {"url": "<url>"}}
      - Responses API: payload["input"][*]["content"][*] blocks of
        {"type": "input_image", "image_url": "<url>"}

    Args:
        payload: A Chat Completions or Responses API request body (dict).
        max_bytes: Maximum bytes to download per image (default 20 MiB).
            Enforced against both the Content-Length header and the actual
            number of bytes streamed, whichever is hit first.
        timeout_seconds: Combined connect+read timeout per HTTP request
            (default 10s). Applied per redirect hop.
        allow_insecure_http: If True, permit fetching plain http:// URLs
            (default False -- only https:// is fetched by default). Only
            enable this for local/offline testing against non-TLS hosts.

    Returns:
        A new dict with any http(s):// image_url values replaced by
        data:<mime>;base64,<...> URIs. The input payload is not mutated.

    Raises:
        ValueError: on an unsupported URL scheme, a blocked SSRF target,
            an oversized download, or content that is not a valid image.
    """
    result = copy.deepcopy(payload)

    def _walk_content(content: Any) -> None:
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "image_url":
                image_url = block.get("image_url")
                if isinstance(image_url, str):
                    block["image_url"] = _resolve_url(
                        image_url,
                        max_bytes=max_bytes,
                        timeout_seconds=timeout_seconds,
                        allow_insecure_http=allow_insecure_http,
                    )
                elif isinstance(image_url, dict) and "url" in image_url:
                    image_url["url"] = _resolve_url(
                        image_url["url"],
                        max_bytes=max_bytes,
                        timeout_seconds=timeout_seconds,
                        allow_insecure_http=allow_insecure_http,
                    )
            elif block_type == "input_image":
                image_url = block.get("image_url")
                if isinstance(image_url, str):
                    block["image_url"] = _resolve_url(
                        image_url,
                        max_bytes=max_bytes,
                        timeout_seconds=timeout_seconds,
                        allow_insecure_http=allow_insecure_http,
                    )

    for message in result.get("messages", []) if isinstance(result.get("messages"), list) else []:
        if isinstance(message, dict):
            _walk_content(message.get("content"))

    for item in result.get("input", []) if isinstance(result.get("input"), list) else []:
        if isinstance(item, dict):
            _walk_content(item.get("content"))

    return result
