# Migration: pointing an existing client at this bridge

Every example in this repo, including the Lambda handler, takes and
returns the *exact* request/response schema of the API it's fronting.
The bridge never introduces a custom envelope. That's the whole point:
migration is swapping a base URL, not rewriting a client.

## Chat Completions (`mantle_chat_completions.py`, `lambda_handler.py`)

Input: the exact OpenAI Chat Completions request body --
`{"model": ..., "messages": [{"role", "content": [...]}]}`.

Output: the exact Chat Completions response JSON, unmodified --
`{"choices": [{"message": {"content": ...}}], ...}`.

An existing OpenAI-compatible client pointed at this Lambda's Function
URL (or API Gateway endpoint) instead of directly at `bedrock-mantle`
works with zero client-side changes, including for `https://` image URLs
that `bedrock-mantle` would otherwise reject with:

```
"Only inline image data URLs and S3 URLs are supported."
```

## Responses API (`mantle_responses_api.py`)

Input: the exact OpenAI Responses API request body --
`{"model": ..., "input": [{"role", "content": [...]}]}`.

Output: the exact Responses API response JSON, unmodified.

Note the live-verified model-availability gap: as of this writing, no
model in a typical Bedrock account supports both the `/v1/responses` API
*and* vision at inference time -- `openai.gpt-oss-120b` supports
`/v1/responses` but is text-only (it accepts an `input_image` block at
the schema level, then fails at inference with a generic `server_error`);
vision models like `qwen3-vl` don't support `/v1/responses` at all. This
is a Bedrock model-availability constraint, not a bug in the bridge --
recheck AWS's
[API compatibility table](https://docs.aws.amazon.com/bedrock/latest/userguide/models-api-compatibility.html)
as new models ship.

## Converse (`runtime_converse.py`)

`bedrock-runtime` Converse has no OpenAI-compatible schema to migrate
from -- it's Bedrock's own native API, with no `image_url` concept at
all, only raw bytes. This example exists as a contrast case: it reuses
the same `resolve_image_urls()` bridge purely as a URL-to-bytes utility
(feeding a Chat-Completions-shaped payload into the bridge, then
extracting the resulting bytes for the native Converse call), to show
the bridge is genuinely endpoint-agnostic rather than Mantle-specific.

## Error shape parity

The Lambda handler returns errors in the OpenAI error envelope --
`{"error": {"message": ..., "type": ...}}` -- so client-side error
handling needs no changes either, not just the happy path.
