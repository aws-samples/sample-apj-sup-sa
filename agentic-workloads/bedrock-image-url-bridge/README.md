# bedrock-image-url-bridge

**Problem:** Amazon Bedrock's OpenAI-compatible `bedrock-mantle` endpoint
(Chat Completions / Responses APIs) rejects a plain `https://` `image_url`:

```json
{"error":{"code":"validation_error","message":"Only inline image data URLs and S3 URLs are supported.","type":"invalid_request_error"}}
```

It accepts `s3://bucket/key` and `data:<mime>;base64,...` natively. The
native `bedrock-runtime` Converse API is stricter still: no URL concept
at all, only raw bytes.

**Solution:** `resolve_image_urls()` rewrites only `http(s)://` image
URLs into inline `data:` URIs. `s3://` and `data:` pass through
untouched. One function, no server -- copy it into your own code.

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

Every example -- including the Lambda handler -- mimics the *exact*
request/response schema of the API it fronts, so pointing an existing
client at it is a base-URL swap, not a rewrite. See
[docs/MIGRATION.md](./docs/MIGRATION.md).

## Examples

| Example | Endpoint / hosting shape |
|---|---|
| `examples/mantle_chat_completions.py` | `bedrock-mantle` `/v1/chat/completions`, CLI script |
| `examples/mantle_responses_api.py` | `bedrock-mantle` `/v1/responses`, CLI script |
| `examples/runtime_converse.py` | native `bedrock-runtime` Converse, CLI script |
| `examples/lambda_handler.py` | AWS Lambda handler, schema-faithful to Chat Completions |

All live-verified against a real AWS account (region `us-east-1`) --
see [docs/MIGRATION.md](./docs/MIGRATION.md) for per-example status and a
live-discovered model-availability gap on the Responses API path.

### Run a demo

```bash
export AWS_PROFILE=your-aws-profile
export AWS_REGION=us-east-1

python -m examples.mantle_chat_completions \
  --image-url https://placehold.co/64x64.jpg \
  --prompt "Describe this image in one sentence." \
  --model qwen.qwen3-vl-235b-a22b-instruct
```

Try the Lambda handler locally (no deployment needed) and see how to
deploy it: [docs/HOSTING.md](./docs/HOSTING.md).

## Run tests

```bash
pip install -e ".[dev]"

# unit tests -- no network, no AWS calls
pytest tests/test_bridge_unit.py tests/test_lambda_handler.py -v

# live tests -- real calls against bedrock-mantle and bedrock-runtime
AWS_PROFILE=your-aws-profile AWS_REGION=us-east-1 BRIDGE_LIVE_TEST=1 \
  pytest tests/test_bridge_live.py -v
```

## Docs

- **[docs/SECURITY.md](./docs/SECURITY.md)** -- the security guards
  (SSRF, size cap, redirects, format verification) and why each exists.
- **[docs/CACHING.md](./docs/CACHING.md)** -- the optional `session=`
  and `cache=` kwargs: connection pooling and URL-keyed result caching,
  both opt-in and no-ops when unused, both live-verified.
- **[docs/HOSTING.md](./docs/HOSTING.md)** -- where this runs: inline in
  an existing service, AWS Lambda, or a container/EC2 process.
- **[docs/SCALING.md](./docs/SCALING.md)** -- concurrency patterns and a
  real cost breakdown (S3/Lambda/model-inference pricing) at volume.
- **[docs/MIGRATION.md](./docs/MIGRATION.md)** -- per-example
  schema-fidelity notes and the Responses API model-availability gap.
- **[docs/ALTERNATIVES.md](./docs/ALTERNATIVES.md)** -- when to reach
  for an AI gateway like LiteLLM instead of this sample.
