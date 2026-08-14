# Per-model samples for Amazon Bedrock

Runnable Jupyter notebooks for calling foundation models on Amazon Bedrock, **one
folder per model family**. Open the folder for the model you are using and it tells
you everything needed for that model — which endpoint serves it, which API to call,
which parameters it accepts, and where it behaves unlike its neighbours.

> This is sample code, for non-production usage. You should work with your security
> and legal teams to meet your organizational security, regulatory and compliance
> requirements before deployment.

The notebooks are teaching material, not production-ready artefacts. They favour
readability over completeness: error handling is shown where it teaches something and
elided where it would obscure the point.

## Why per-model

Bedrock model behaviour is not uniform, and the differences are the part that costs
you time. Gemma 4 pins `temperature` to its default and rejects `top_p`. Grok takes
`max_completion_tokens` where the `/v1` families take `max_tokens`. Palmyra Vision has
no tool support at all. Most Claude models must be addressed through a cross-Region
inference profile and are rejected by their bare model ID. None of that is discoverable
from a generic example, so each family gets its own notebook rather than a shared one.

## Two endpoints, and which to use

Bedrock serves models through two inference endpoints. **Which one you use is a
per-model fact, not a preference.**

| | `bedrock-runtime` | `bedrock-mantle` |
|---|---|---|
| APIs | Converse, InvokeModel | OpenAI Responses, OpenAI Chat Completions, Anthropic Messages |
| Auth | SigV4 via the AWS SDK | Bearer token (short-term Bedrock API key) |
| Reach for it when | you want one AWS-native shape across providers, or need Converse-only features | you have existing OpenAI- or Anthropic-shaped code to move |

Some models are on both, some on only one. Every family table below names the
endpoints for that family, and `_shared/bedrock.py` exposes `endpoints_for(model_id)`
so a notebook can ask the service instead of trusting a table that ages.

## Find your model family

Open one folder. Each notebook is self-contained.

| Folder | Models | Endpoint | API | What the notebooks cover |
|---|---|---|---|---|
| [`01-openai-gpt/`](01-openai-gpt/) | gpt-5.6 sol/terra/luna, gpt-5.5, gpt-5.4 · gpt-oss 20b/120b · gpt-oss-safeguard | gpt-5.x **mantle only** · gpt-oss both | Responses | Core inference · **web search** · tools & strict JSON · prompt caching · server-side Lambda tools & fine-tuning |
| [`02-anthropic-claude/`](02-anthropic-claude/) | opus-5, sonnet-5, opus-4-8, opus-4-7, haiku-4-5, fable-5 | both | Messages | Core inference · adaptive thinking, tool loops, caching · computer use, memory, compaction |
| [`03-google-gemma/`](03-google-gemma/) | gemma-4 31b · 26b-a4b · e2b · gemma-3 4b · 12b · 27b | gemma 4 **mantle only** · gemma 3 both | gemma 4 Responses · gemma 3 Chat Completions | Gemma 4 end to end · Gemma 3 on both endpoints, and why the two generations share almost nothing |
| [`04-qwen/`](04-qwen/) | qwen3 32b/235b/next-80b, coder 30b/480b/next, vl-235b | both | Chat Completions | Core inference & tools · coding models & vision |
| [`05-deepseek/`](05-deepseek/) | v3.2, v3.1 | both (v3.1 mantle only) | Chat Completions | Core inference, reasoning effort, tools, structured output |
| [`06-zai-glm/`](06-zai-glm/) | glm-5, glm-4.7, glm-4.7-flash, glm-4.6 | both (4.6 mantle only) | Chat Completions | Core inference plus cost-aware routing across the size ladder |
| [`07-mistral/`](07-mistral/) | mistral-large-3, ministral 3b/8b/14b, magistral, devstral-2, voxtral | both | Chat Completions | Size ladder & routing · Devstral coding, Voxtral |
| [`08-moonshot-kimi/`](08-moonshot-kimi/) | kimi-k2.5, kimi-k2-thinking | both | Chat Completions | Core inference, long context, agentic patterns |
| [`09-minimax/`](09-minimax/) | minimax-m2.5, m2.1, m2 | both | Chat Completions | Core inference plus a version-migration test across three generations |
| [`10-nvidia-nemotron/`](10-nvidia-nemotron/) | nemotron-super-3-120b, nano 9b/12b/30b | both | Chat Completions | Core inference across the cost/quality curve |
| [`11-xai-grok/`](11-xai-grok/) | grok-4.3 | **mantle only** | Responses | Core inference, always-on reasoning, encrypted reasoning content |
| [`12-writer-palmyra/`](12-writer-palmyra/) | palmyra-vision-7b · palmyra-x4 · palmyra-x5 | vision both · x4/x5 **runtime only** | Chat Completions · Converse | Vision, and working around a model with no tool support · the text models, which need an inference profile |
| [`13-amazon-nova/`](13-amazon-nova/) | nova-micro · nova-lite · nova-pro | **runtime only** | Converse | Tier selection graded on a checkable task · vision on lite/pro · what a provider-deprecated model looks like |
| [`14-openai-gpt-oss/`](14-openai-gpt-oss/) | gpt-oss 20b/120b · gpt-oss-safeguard 20b/120b | both | Chat Completions · Converse | Where the reasoning trace lives on each endpoint · policy classification graded on a labelled set |
| [`15-meta-llama/`](15-meta-llama/) | llama4-scout · llama4-maverick | **runtime only** | Converse | Mixture-of-experts sizing · inference-profile-only access · vision · validating tool calls |

Endpoint availability was read from the live catalogues, not hand-maintained. Check it
yourself for any model with `endpoints_for()` from [`_shared/bedrock.py`](_shared/bedrock.py).

**Read [`00-foundations/`](00-foundations/) first if you are new to Bedrock** — auth,
endpoints, quotas and governance apply to every family:

| Notebook | Covers |
|---|---|
| [`01-endpoints-auth-and-the-three-paths`](00-foundations/01-endpoints-auth-and-the-three-paths.ipynb) | SigV4 · short-term API keys · curl · the three URL paths · model discovery · IAM |
| [`02-governance-projects-and-retention`](00-foundations/02-governance-projects-and-retention.ipynb) | Projects/Workspaces · cost attribution · data retention & ZDR · CloudWatch |
| [`03-scaling-tiers-and-latency`](00-foundations/03-scaling-tiers-and-latency.ipynb) | Quota model · retries & backoff · service tiers · TTFT measurement |
| [`04-bedrock-runtime-converse-and-profiles`](00-foundations/04-bedrock-runtime-converse-and-profiles.ipynb) | SigV4 · Converse · content blocks · inference profiles (CRIS) · reading the catalogue · streaming · InvokeModel |

Choosing between families, or already have OpenAI code?
[`99-cross-cutting/`](99-cross-cutting/) has a live capability survey, a migration
guide, and a pre-launch checklist.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
jupyter lab            # then open any notebook and run all cells
```

You need AWS credentials with Bedrock access. Nothing else — every notebook derives
its own auth from whatever credentials are already in your environment.

**On `bedrock-runtime`** the AWS SDK signs with SigV4, so your existing credentials
work directly:

```python
import boto3

runtime = boto3.client("bedrock-runtime", region_name="us-east-1")
response = runtime.converse(
    modelId="us.anthropic.claude-sonnet-5",      # note the "us." inference profile
    messages=[{"role": "user", "content": [{"text": "Hello"}]}],
    inferenceConfig={"maxTokens": 64},
)
print(response["output"]["message"]["content"][0]["text"])
```

**On `bedrock-mantle`** the OpenAI and Anthropic SDKs expect a bearer token, so mint a
short-term Bedrock API key from the same credentials:

```python
from aws_bedrock_token_generator import provide_token
from openai import OpenAI

client = OpenAI(
    api_key=provide_token(region="us-east-1"),   # expires in <= 12 hours
    base_url="https://bedrock-mantle.us-east-1.api.aws/openai/v1",
)
response = client.responses.create(
    model="google.gemma-4-31b", input="Hello", max_output_tokens=64
)
print(response.output_text)
```

Two things in that first snippet catch people out, and
[`00-foundations/`](00-foundations/) covers both: most Claude models are rejected by
their bare model ID and require the `us.` inference-profile form, and the base URL
prefix on `bedrock-mantle` differs by model family.

Least-privilege IAM: `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream` cover Converse on `bedrock-runtime`; attach
`AmazonBedrockMantleInferenceAccess` for `bedrock-mantle` inference, and
`AmazonBedrockMantleFullAccess` only if you want to create Projects. The model- and
profile-discovery cells also need `bedrock:ListFoundationModels` and
`bedrock:ListInferenceProfiles`.

## Cost and cleanup

Each notebook makes tens of small calls with tight token budgets per run. Two cost more than the rest:

- `01-openai-gpt/02-web-search-and-grounding.ipynb` — web search is billed
  separately from tokens.
- `99-cross-cutting/01-choosing-a-model-and-api.ipynb` — a deliberate survey that
  touches every family.

Notebooks that create demo Projects archive them in a final cell.

## Scope

These notebooks demonstrate the documented public APIs of Amazon Bedrock serverless
inference, on both the `bedrock-runtime` and `bedrock-mantle` endpoints.

In scope: models that **return text**, across text and image inputs. Out of scope:
image, video, speech and embedding outputs, rerankers, Bedrock Marketplace, and
models that a newer generation has superseded.

Model behaviour on Bedrock changes without notice — a parameter that is accepted
today can be rejected tomorrow, as happened to Gemma 4 on 12 August 2026. Where a
notebook states a parameter matrix it also shows the probe that produced it, so you
can re-derive today's answer instead of trusting a committed table.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for how to
report a security issue. Please do not open a public GitHub issue.

## License

MIT-0. See [LICENSE](../../LICENSE).
