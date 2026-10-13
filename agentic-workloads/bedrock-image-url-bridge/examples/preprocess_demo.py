"""Preprocessing demo: token-savings comparison, no AWS credentials needed.

Demonstrates bridge.preprocess's patch-mode (OpenAI-style) and tile-mode
(Anthropic-style) resizing against a real image, printing a before/after
token estimate comparison so the savings are visible without making any
Bedrock call. Optionally, if AWS_PROFILE is set, also runs a live
bedrock-mantle Chat Completions call using the preprocessed image via
resolve_image_urls()'s preprocess= hook (best-effort -- the script's core
value, the token comparison, does not depend on this succeeding).

Usage:
    python -m examples.preprocess_demo --image-path ./photo.jpg --mode tile
    python -m examples.preprocess_demo --image-path ./photo.jpg --mode patch --detail high
    python -m examples.preprocess_demo --image-url https://example.com/photo.jpg --mode tile --max-tokens 2000
"""
from __future__ import annotations

import argparse
import base64
import io
import os

from PIL import Image

from bridge import resolve_image_urls
from bridge.preprocess import (
    calculate_patch_count,
    is_photographic,
    preprocess_patch_mode,
    preprocess_tile_mode,
)

DEFAULT_MODEL = "qwen.qwen3-vl-235b-a22b-instruct"

_PATCH_TOKEN_MULTIPLIER = 1.62
_ANTHROPIC_TOKENS_PER_PIXEL_DIVISOR = 750


def _load_image_bytes(args: argparse.Namespace) -> bytes:
    """Return raw image bytes from --image-path (local file) or
    --image-url (fetched through the SSRF-guarded resolve_image_urls
    download path, without preprocessing, just to get raw bytes)."""
    if args.image_path:
        with open(args.image_path, "rb") as f:
            return f.read()

    # Fetch via the same guarded path resolve_image_urls uses, but we
    # only want the raw bytes here (not a data: URI), so build a
    # throwaway payload and pull the decoded bytes back out.
    payload = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": args.image_url}}]}
        ]
    }
    resolved = resolve_image_urls(payload)
    data_uri = resolved["messages"][0]["content"][0]["image_url"]["url"]
    b64_part = data_uri.split(",", 1)[1]
    return base64.b64decode(b64_part)


def _naive_raw_token_estimate(width: int, height: int, mode: str) -> int:
    """Estimate tokens for the RAW, unprocessed image using the same
    formula the target mode would apply -- i.e. what it would cost
    Bedrock today, with no client-side preprocessing at all."""
    if mode == "patch":
        patches = calculate_patch_count(width, height)
        return round(patches * _PATCH_TOKEN_MULTIPLIER)
    return round((width * height) / _ANTHROPIC_TOKENS_PER_PIXEL_DIVISOR)


def _print_comparison(
    mode: str,
    original_width: int,
    original_height: int,
    raw_tokens: int,
    resized_width: int,
    resized_height: int,
    final_tokens: int,
) -> None:
    print("Token usage summary:")
    print(f"  Mode: {mode}")
    print(f"  Before: {original_width}x{original_height} (~{raw_tokens} tokens, unprocessed)")
    print(f"  After:  {resized_width}x{resized_height} (~{final_tokens} tokens, preprocessed)")
    if raw_tokens > 0:
        reduction_pct = 100 * (1 - final_tokens / raw_tokens)
        print(f"  Reduction: {reduction_pct:.1f}% ({raw_tokens} -> {final_tokens} tokens)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image-path", help="Path to a local image file")
    group.add_argument("--image-url", help="https:// or s3:// image URL")
    parser.add_argument("--mode", choices=["patch", "tile"], default="tile")
    parser.add_argument(
        "--detail",
        choices=["low", "high", "original"],
        default="high",
        help="Patch mode detail level (ignored for tile mode)",
    )
    parser.add_argument("--max-tokens", type=int, default=None, help="Optional token budget for the preprocessed image")
    parser.add_argument("--prompt", default="Describe this image in one sentence.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    raw_bytes = _load_image_bytes(args)

    with Image.open(io.BytesIO(raw_bytes)) as img:
        original_width, original_height = img.width, img.height

    raw_tokens = _naive_raw_token_estimate(original_width, original_height, args.mode)

    if args.mode == "patch":
        result = preprocess_patch_mode(raw_bytes, detail=args.detail)
    else:
        result = preprocess_tile_mode(raw_bytes, max_token_budget=args.max_tokens)

    resized = result.metadata.resized_dimensions
    photo_kind = "jpeg/photo" if is_photographic(raw_bytes) else "diagram/screenshot"
    print(f"  (photographic detection: {photo_kind} -> {result.mime_type})")
    _print_comparison(
        args.mode,
        original_width,
        original_height,
        raw_tokens,
        resized.width,
        resized.height,
        result.metadata.estimated_tokens,
    )

    # Optional, best-effort: if AWS credentials are configured, also
    # demonstrate feeding the preprocessed bytes through
    # resolve_image_urls()'s preprocess= hook and making a live
    # bedrock-mantle call. This is not required for the script to prove
    # its core value (the token comparison above).
    if os.environ.get("AWS_PROFILE"):
        print()
        print("AWS_PROFILE detected -- attempting a live bedrock-mantle call...")
        try:
            from examples.mantle_chat_completions import call_mantle_chat_completions

            if args.image_path:
                # Local file: build a data: URI directly since there's no
                # http(s) URL for resolve_image_urls to fetch.
                data_uri = f"data:{result.mime_type};base64,{result.base64}"
            else:
                def _preprocess_hook(b: bytes) -> bytes:
                    if args.mode == "patch":
                        return preprocess_patch_mode(b, detail=args.detail).to_bytes()
                    return preprocess_tile_mode(b, max_token_budget=args.max_tokens).to_bytes()

                image_payload = {
                    "messages": [
                        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": args.image_url}}]}
                    ]
                }
                resolved = resolve_image_urls(image_payload, preprocess=_preprocess_hook)
                data_uri = resolved["messages"][0]["content"][0]["image_url"]["url"]

            chat_payload = {
                "model": args.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": args.prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
            }
            response = call_mantle_chat_completions(chat_payload, region=args.region)
            print("Model response:")
            print(f"  {response['choices'][0]['message']['content']}")
        except Exception as exc:  # best-effort: never fail the demo on this
            print(f"  Live call skipped/failed (non-fatal): {exc}")
    else:
        print()
        print("(Set AWS_PROFILE to also try a live bedrock-mantle call with the preprocessed image.)")


if __name__ == "__main__":
    main()
