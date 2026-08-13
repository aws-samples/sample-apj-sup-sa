"""bedrock-image-url-bridge: make http(s) image URLs safe to send to
Bedrock's OpenAI-compatible (mantle) and native (runtime) APIs.
"""
from .core import resolve_image_urls

__all__ = ["resolve_image_urls"]
