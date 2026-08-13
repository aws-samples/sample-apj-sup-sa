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

## How this is hosted / how you use it

**The bridge is not a service you deploy.** `resolve_image_urls()` is a plain
Python function with no state and no listening port. You call it inline,
wherever your code already builds a Bedrock request, right before sending
that request. There is nothing to run continuously and nothing to keep
alive -- copy `bridge/core.py` into your project (or `pip install -e .`
this directory as a local editable dependency) and import it.

What you host is *your own calling code* -- and that can be anything that
runs Python:

- **Already-running service** (existing API, worker, notebook, CLI) --
  just call `resolve_image_urls(payload)` before your existing Bedrock
  call. No new infrastructure at all. This is the expected case for most
  readers.
- **AWS Lambda** -- see `examples/lambda_handler.py`: a handler that
  accepts the *exact* Chat Completions request schema
  (`{"model": ..., "messages": [...]}`) and returns the *exact* Chat
  Completions response JSON, unmodified. Point an existing OpenAI Chat
  Completions client at this Lambda's Function URL / API Gateway
  endpoint instead of at `bedrock-mantle` directly, and it works with no
  request/response adaptation -- the only behavior added is resolving
  `http(s)://` image URLs before the request reaches Bedrock. Deploy it
  with whatever you already use for Lambda (SAM, CDK, Terraform, console
  zip) -- package `bridge/`, `requests`, and `Pillow` into the deployment
  artifact (`boto3` ships with the Python runtime already), and grant the
  function's execution role `bedrock:InvokeModel` for the model(s) you
  call.
- **Container / ECS / EC2** -- same idea, just a normal Python process;
  `examples/mantle_chat_completions.py` and the other example scripts are
  runnable as-is and show the exact call shape to wrap in your own
  service.

In every case the guards in `bridge/core.py` run in the same process as
the caller -- there's no separate bridge process to secure, scale, or
monitor independently of your own application.

### Why schema fidelity matters

Every example in this repo -- including the Lambda handler -- takes and
returns the *exact* request/response shape of the API it's fronting
(Chat Completions in, Chat Completions out; Responses API in, Responses
API out). The bridge never introduces a custom envelope. That's what
makes migration a drop-in change: swap the base URL your client points
at, keep the client code and its request/response parsing exactly as
is, and image URLs that would otherwise 400 against `bedrock-mantle`
just work.

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

Four example entry points, all using the same `resolve_image_urls()` bridge:

| Example | Endpoint / hosting shape | Image URL handling before the bridge |
|---|---|---|
| `examples/mantle_chat_completions.py` | `bedrock-mantle` `/v1/chat/completions`, runnable CLI script | rejects https, accepts s3/data |
| `examples/mantle_responses_api.py` | `bedrock-mantle` `/v1/responses`, runnable CLI script | rejects https, accepts s3/data |
| `examples/runtime_converse.py` | `bedrock-runtime` Converse, runnable CLI script | no URL support at all -- bytes only |
| `examples/lambda_handler.py` | AWS Lambda handler (same Chat Completions call, wrapped for `event`/`context`) | rejects https, accepts s3/data |

**Live-verified status** (region us-east-1):
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
export AWS_PROFILE=your-aws-profile
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

### Try the Lambda handler locally (no deployment needed)

```bash
export AWS_PROFILE=your-aws-profile
export AWS_REGION=us-east-1

python -c "
from examples.lambda_handler import handler
print(handler({
    'model': 'qwen.qwen3-vl-235b-a22b-instruct',
    'messages': [
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Describe this image in one sentence.'},
            {'type': 'image_url', 'image_url': {'url': 'https://placehold.co/64x64.jpg'}},
        ]}
    ],
}, None))
"
```

The input is a standard Chat Completions request body; the output is a
standard Chat Completions response body wrapped in an API-Gateway-proxy
envelope (`statusCode`/`headers`/`body`). Errors follow the OpenAI error
shape (`{"error": {"message", "type"}}`) so client-side error handling
needs no changes either.

To actually deploy it, zip up `bridge/`, `examples/lambda_handler.py`,
`examples/mantle_chat_completions.py`, `requests`, and `Pillow` (boto3 is
already in the Lambda Python runtime) and create a Lambda function with
`examples.lambda_handler.handler` as the entry point. Grant its execution
role `bedrock:InvokeModel`.

## Run tests

```bash
pip install -e ".[dev]"

# unit tests -- no network, no AWS calls
pytest tests/test_bridge_unit.py tests/test_lambda_handler.py -v

# live tests -- real calls against bedrock-mantle and bedrock-runtime
AWS_PROFILE=your-aws-profile AWS_REGION=us-east-1 BRIDGE_LIVE_TEST=1 \
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

## Alternative: use an AI gateway instead of a hand-rolled bridge

If you're calling more than one model/provider and want this handled for
you instead of maintaining `resolve_image_urls()` yourself, put an AI
gateway in front of Bedrock and let it own image URL normalization:

- **[LiteLLM](https://docs.litellm.ai/docs/proxy/architecture)** already
  implements this exact pattern generically: it detects an `image_url` in
  the request, checks whether the target provider accepts URLs natively,
  and if not, downloads the image (capped at `MAX_IMAGE_URL_DOWNLOAD_SIZE_MB`,
  50 MB by default), converts it to base64, and caches up to 10 converted
  images in memory to cut latency on repeated calls. LiteLLM also has a
  dedicated [Bedrock Mantle provider](https://docs.litellm.ai/docs/providers/bedrock_mantle),
  so routing through it gets you the URL-handling fix plus routing,
  fallbacks, retries, budgets, and spend tracking across every provider you
  use, at the cost of running (and securing) an extra proxy service.
- Any OpenAI-compatible gateway with similar "fetch-and-inline" middleware
  (e.g. a custom API Gateway + Lambda layer) can apply the same idea --
  the security guards in `bridge/core.py` (SSRF guard, size cap, format
  verification) are exactly what such middleware needs regardless of
  where it runs.

Use this sample as-is when you want a single dependency-light function
with no extra infrastructure. Reach for a gateway like LiteLLM when you're
already managing multiple providers/models and want URL handling, routing,
and cost tracking solved together instead of piecemeal.
