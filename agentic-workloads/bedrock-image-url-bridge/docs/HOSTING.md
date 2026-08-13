# Hosting

`resolve_image_urls()` is a plain, stateless Python function -- no
listening port, no background process, nothing to keep alive. You call
it inline, wherever your code already builds a Bedrock request, right
before sending it. What you host is *your own calling code*, not the
bridge itself.

## Already-running service

If you have an existing API, worker, notebook, or CLI that already
calls `bedrock-mantle` or `bedrock-runtime`, add one line before the
existing call:

```python
payload = resolve_image_urls(payload)
# ... send payload as before
```

No new infrastructure. This is the expected case for most callers.

## AWS Lambda

`examples/lambda_handler.py` is a complete handler that accepts the
*exact* Chat Completions request schema (`{"model": ..., "messages":
[...]}`) and returns the *exact* Chat Completions response JSON,
unmodified -- see [MIGRATION.md](./MIGRATION.md) for why that schema
fidelity matters.

Try it locally first, no deployment needed:

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

To deploy: package `bridge/`, `examples/lambda_handler.py`,
`examples/mantle_chat_completions.py`, `requests`, and `Pillow` (boto3
ships with the Lambda Python runtime already) using whatever you
already use for Lambda -- SAM, CDK, Terraform, or a console zip upload.
There is nothing bridge-specific about the deployment mechanics. Create
the function with `examples.lambda_handler.handler` as the entry point,
and grant its execution role `bedrock:InvokeModel` for the model(s) you
call.

An optional top-level `region` key in the request overrides the AWS
region used to call `bedrock-mantle` (default `us-east-1`); it's stripped
before forwarding since it isn't part of the Chat Completions schema.

## Container / ECS / EC2

Same idea as "already-running service" -- a normal, long-running Python
process. `examples/mantle_chat_completions.py`, `examples/mantle_responses_api.py`,
and `examples/runtime_converse.py` are runnable as-is and show the exact
call shape to wrap in your own service.

## In every case

The security guards in `bridge/core.py` run in the same process as the
caller. There's no separate bridge process, network hop, or service to
secure, deploy, or monitor independently of your own application.
