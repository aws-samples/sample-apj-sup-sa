# bedrock-image-url-bridge

**Problem:** Amazon Bedrock's OpenAI-compatible `bedrock-mantle` endpoint (Chat
Completions / Responses APIs) rejects a plain `https://` `image_url` value.
Verified live against a real account:

```json
{"error":{"code":"validation_error","message":"Only inline image data URLs and S3 URLs are supported.","type":"invalid_request_error"}}
```

It *does* accept `s3://bucket/key` (fetched server-side) and `data:<mime>;base64,...`
URIs natively. The native `bedrock-runtime` Converse API is stricter still: it has
no `image_url` concept at all -- only raw bytes.

**Solution:** `resolve_image_urls()` walks a request payload and rewrites only
the `http(s)://` image URLs into inline `data:` URIs. `s3://` and `data:` URIs
pass through untouched. One function, no server, no cache -- copy it into your
own code.

## Quick start

```python
from bridge import resolve_image_urls

payload = {
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image."},
                {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}},
            ],
        }
    ]
}

payload = resolve_image_urls(payload)  # http(s) url -> inline data: URI
# ... send payload to bedrock-mantle as before
```

## Security guards (and why)

- **HTTPS-only by default** -- plain `http://` is rejected unless you pass
  `allow_insecure_http=True` (local testing only). Avoids sending requests
  in the clear.
- **SSRF guard** -- the hostname is resolved and the IP checked *before*
  connecting. Loopback, private, link-local, and the cloud metadata address
  (`169.254.169.254`) are all blocked. Without this, an attacker-supplied
  URL could make your server fetch its own internal endpoints or cloud
  credentials.
- **Size cap** (`max_bytes`, default 20 MiB) -- enforced against both the
  `Content-Length` header and the actual streamed byte count, whichever
  hits first. Stops a malicious or misconfigured host from exhausting
  memory with an unbounded response.
- **Bounded, re-validated redirects** (max 2 hops) -- each redirect target
  is re-checked against the same scheme/SSRF guard, so a redirect can't be
  used to smuggle a request past the guard.
- **Real format verification** -- downloaded bytes are verified with
  Pillow's `Image.verify()` and the mime type is derived from the verified
  format, never from the URL extension or a server-supplied
  `Content-Type` header (both can lie).

## Examples

Three example endpoints, all using the same `resolve_image_urls()` bridge:

| Example | Endpoint | Image URL handling before the bridge |
|---|---|---|
| `examples/mantle_chat_completions.py` | `bedrock-mantle` `/v1/chat/completions` | rejects https, accepts s3/data |
| `examples/mantle_responses_api.py` | `bedrock-mantle` `/v1/responses` | rejects https, accepts s3/data |
| `examples/runtime_converse.py` | `bedrock-runtime` Converse | no URL support at all -- bytes only |

**Live-verified status** (region us-east-1, profile `agentcore-deploy`):
- `mantle_chat_completions.py` -- verified end to end with a real vision
  model (`qwen.qwen3-vl-235b-a22b-instruct`).
- `runtime_converse.py` -- verified end to end with
  `us.anthropic.claude-haiku-4-5-20251001-v1:0`.
- `mantle_responses_api.py` -- the bridge output is schema-correct and
  the payload passes Mantle's request validation, but as of this
  writing no model in this account that supports the `/v1/responses`
  API is also vision-capable: `openai.gpt-oss-120b` supports
  `/v1/responses` per AWS's [API compatibility table](https://docs.aws.amazon.com/bedrock/latest/userguide/models-api-compatibility.html)
  but is text-only, so it accepts an `input_image` block at the schema
  level and then fails at inference time with a generic
  `server_error`. `qwen3-vl` (the vision model used above) does not
  support `/v1/responses` at all. This is a model-availability gap on
  AWS's side, not a bug in the bridge -- re-check the compatibility
  table as new models ship, and swap `--model` once a vision + Responses
  API combination is available.

### Run a demo

```bash
export AWS_PROFILE=agentcore-deploy
export AWS_REGION=us-east-1

python -m examples.mantle_chat_completions \
  --image-url https://placehold.co/64x64.jpg \
  --prompt "Describe this image in one sentence." \
  --model qwen.qwen3-vl-235b-a22b-instruct

python -m examples.mantle_responses_api \
  --image-url https://placehold.co/64x64.jpg \
  --prompt "Describe this image in one sentence."

python -m examples.runtime_converse \
  --image-url https://placehold.co/64x64.jpg \
  --prompt "Describe this image in one sentence." \
  --model anthropic.claude-haiku-4-5
```

## Run tests

```bash
pip install -e ".[dev]"

# unit tests -- no network, no AWS calls
pytest tests/test_bridge_unit.py -v

# live tests -- real calls against bedrock-mantle and bedrock-runtime
AWS_PROFILE=agentcore-deploy AWS_REGION=us-east-1 BRIDGE_LIVE_TEST=1 \
  pytest tests/test_bridge_live.py -v
```

## Scaling notes

This sample is intentionally synchronous, per-request, and uncached --
that's what makes the interception pattern easy to read. It's directly
usable as-is in a Lambda handler or any single-request context.

For high-volume or highly concurrent image fetching, add your own layer on
top: connection pooling (a shared `requests.Session`), a concurrency limit
(semaphore or thread pool bound), and a cache keyed on URL if the same
image is fetched repeatedly. None of that is included here deliberately --
it adds real complexity and this sample's only job is to show the
interception pattern clearly.
