# Amazon Bedrock Mantle samples — OpenAI and Anthropic compatible APIs

Runnable Jupyter notebooks for **`bedrock-mantle`**, the Amazon Bedrock endpoint that
speaks the **OpenAI Responses API**, **OpenAI Chat Completions API**, and the
**Anthropic Messages API**. One folder per model family: Claude, GPT-5.x, gpt-oss,
Gemma 4, Qwen3, DeepSeek, GLM, Mistral, Kimi, MiniMax, Nemotron, Grok, Palmyra.

## Find your model family

Open one folder. Each notebook is self-contained.

| Folder | Models | API to use | What the notebooks cover |
|---|---|---|---|
| [`01-openai-gpt/`](01-openai-gpt/) | gpt-5.6 sol/terra/luna, gpt-5.5, gpt-5.4, gpt-oss 20b/120b, gpt-oss-safeguard | Responses | Core inference · **web search** · tools & strict JSON · prompt caching · server-side Lambda tools & fine-tuning |
| [`02-anthropic-claude/`](02-anthropic-claude/) | opus-5, sonnet-5, opus-4-8, opus-4-7, haiku-4-5, fable-5 | Messages | Core inference · adaptive thinking, tool loops, caching · computer use, memory, compaction |
| [`03-google-gemma/`](03-google-gemma/) | gemma-4 31b · 26b-a4b · e2b | Responses | Everything for this family in one notebook, simple call → production client |
| [`04-qwen/`](04-qwen/) | qwen3 32b/235b/next-80b, coder 30b/480b/next, vl-235b | Chat Completions | Core inference & tools · coding models & vision |
| [`05-deepseek/`](05-deepseek/) | v3.2, v3.1 | Chat Completions | Core inference, reasoning effort, tools, structured output |
| [`06-zai-glm/`](06-zai-glm/) | glm-5, glm-4.7, glm-4.7-flash, glm-4.6 | Chat Completions | Core inference plus cost-aware routing across the size ladder |
| [`07-mistral/`](07-mistral/) | mistral-large-3, ministral 3b/8b/14b, magistral, devstral-2, voxtral | Chat Completions | Size ladder & routing · Devstral coding, Voxtral |
| [`08-moonshot-kimi/`](08-moonshot-kimi/) | kimi-k2.5, kimi-k2-thinking | Chat Completions | Core inference, long context, agentic patterns |
| [`09-minimax/`](09-minimax/) | minimax-m2.5, m2.1, m2 | Chat Completions | Core inference plus a version-migration test across three generations |
| [`10-nvidia-nemotron/`](10-nvidia-nemotron/) | nemotron-super-3-120b, nano 9b/12b/30b | Chat Completions | Core inference across the cost/quality curve |
| [`11-xai-grok/`](11-xai-grok/) | grok-4.3 | Responses | Core inference, always-on reasoning, encrypted reasoning content |
| [`12-writer-palmyra/`](12-writer-palmyra/) | palmyra-vision-7b | Chat Completions | Vision, and how to work around a model with no tool support |

**Read [`00-foundations/`](00-foundations/) first if you are new to this endpoint** —
auth, the three URL paths, quotas, and governance apply to every family:

| Notebook | Covers |
|---|---|
| [`01-endpoints-auth-and-the-three-paths`](00-foundations/01-endpoints-auth-and-the-three-paths.ipynb) | SigV4 · short-term API keys · curl · the three URL paths · model discovery · IAM |
| [`02-governance-projects-and-retention`](00-foundations/02-governance-projects-and-retention.ipynb) | Projects/Workspaces · cost attribution · data retention & ZDR · CloudWatch |
| [`03-scaling-tiers-and-latency`](00-foundations/03-scaling-tiers-and-latency.ipynb) | Quota model · retries & backoff · service tiers · TTFT measurement |

Choosing between families, or already have OpenAI code?
[`99-cross-cutting/`](99-cross-cutting/) has a live capability survey, a migration
guide, and a pre-launch checklist.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
jupyter lab            # then open any notebook and run all cells
```

You need AWS credentials with Bedrock Mantle access. The notebooks mint short-term
Bedrock API keys from whatever credentials are already in your environment — nothing
to store, nothing to rotate:

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

Least-privilege IAM: attach `AmazonBedrockMantleInferenceAccess` for inference; add
`AmazonBedrockMantleFullAccess` only if you want to create Projects.

## The one thing to get right: the URL path

**Model IDs map to three different URL paths.** Guessing wrong returns a 404, a 400,
or — for at least one model — a request that never answers.

```python
def api_prefix(model_id):
    if model_id.startswith("anthropic."):
        return "/anthropic/v1"                       # Claude only
    if model_id.startswith(("google.gemma-4", "openai.gpt-5", "xai.")):
        return "/openai/v1"                          # Responses-API families
    return "/v1"                                     # gpt-oss + everything else
```

Note the split *inside* the OpenAI family: `gpt-5.*` uses `/openai/v1`, while
`gpt-oss*` uses the bare `/v1`. Control-plane paths (`models`, `files`,
`fine_tuning`, `organization/projects`, `data_retention`) are **always** under
`/v1/…`.

## Feature availability

| Feature | Where |
|---|---|
| **Web search** with citations, AWS-hosted | `openai.gpt-5.*` only · Responses API · us-east-1/2, us-west-2 |
| Readable reasoning traces | Responses API: gemma-4, gpt-5.x, gpt-oss, grok |
| Adaptive thinking | Claude opus-5 / sonnet-5 / opus-4-8 (**not** haiku-4-5) |
| Explicit prompt-cache breakpoints | `openai.gpt-5.6-*` |
| Prompt caching, 1-hour TTL | Claude |
| Server-side tools (Lambda MCP, AgentCore Gateway) | Responses-API models |
| Built-in `notes` / `tasks` tools | `openai.gpt-oss-*` only |
| Computer use, memory, compaction | Claude, via beta headers |
| Server-side conversation state | Responses API with `store=True` |
| `count_tokens` | Claude, mantle only |
| Fine-tuning (RFT) | `gpt-oss-20b`, `qwen3-32b` — **us-west-2 only** |
| Batch inference, cross-Region inference, Provisioned Throughput | **`bedrock-runtime`**, not mantle |

## Region footprint

| Region | Models | Notable gaps |
|---|---|---|
| `us-east-1` | 55 | none — the only Region with the full Claude set |
| `us-east-2` | 49 | no Anthropic models |
| `us-west-2` | 47 | Claude haiku-4-5 only; no gpt-5.5 or gpt-5.6-sol |
| `eu-central-1` | 33 | no Anthropic, no gpt-5.x, no xAI, no DeepSeek |

`gemma-4` is the only family present in all four.

## Gotchas that cost the most debugging time

Every row is demonstrated live in the notebooks rather than asserted.

| Gotcha | Detail |
|---|---|
| No boto3 client | There is **no** `boto3.client("bedrock-mantle")`. Use the AWS SDK to *sign*, or use the OpenAI/Anthropic SDKs with a bearer token |
| SigV4 signing name | `bedrock`, not `bedrock-mantle` |
| `max_output_tokens` minimum | **16** on the Responses API; Chat Completions accepts 1 |
| Reasoning consumes the budget | Grok spends ~400 output tokens thinking before any text. A small budget returns HTTP 200 with `status: "incomplete"` and an **empty string**. Budget ≥1000 and check `status` |
| Transient 5xx | mantle returns 500/503 under load and the SDKs do **not** retry by default — set `max_retries` |
| A 5xx can arrive as a **4xx** | A server fault is sometimes reported as `HTTP 400` with the body `Internal server error`. A status-only retry policy treats that as permanent and gives up on a blip that succeeds on the next attempt. Retry a 4xx **only** when the body says the server failed — never all 400s |
| Sampling params are per model | On **Responses**, a model accepts `temperature` **only at its own documented default** — Gemma 4 and gpt-5.6 want `1.0`, Grok wants `0.7`; every other value is a 400. `top_p` is rejected by Gemma 4 and gpt-5.6, accepted by Grok. Frontier Claude rejects `temperature` outright. Chat Completions is permissive. **Safest: send neither** |
| `service_tier` | `gpt-5.x` accepts `default` only; `reserved` is rejected everywhere as a parameter |
| `reasoning.effort` | `none`/`low`/`medium`/`high` — **`minimal` is rejected** |
| `store` defaults to `true` | 30-day retention unless you opt out per request |
| `store=False` breaks chaining | `previous_response_id` then returns 404 |
| HTTP 200 ≠ correct | Truncation, trailing characters after valid JSON, and silently-ignored constraints all return 200 |
| Gemma 4 "strict" JSON | Appends trailing characters in roughly half of runs — never call bare `json.loads()` |
| qwen3-coder tool arguments | **Truncated mid-object** (reproduced 10/10). Repair before use *and* before echoing, or the next request 400s |
| gpt-oss `tool_choice` | Supports **`auto` only**. The named form returns 200 and ignores the constraint |
| Claude `content[0]` | Reasoning models put a `thinking` block first — filter by block type, never index blindly |
| Retention gating | A model can report `unavailable` under your retention mode (`claude-fable-5` does) |
| `writer.palmyra-vision-7b` | Rejects **tools** and rejects a **`system` role** |
| Wrong path can **stall** | Grok on `/v1/responses` never answers. Always set a client-side timeout |
| CloudWatch namespace | `AWS/BedrockMantle`, not `AWS/Bedrock` — and there is **no 5xx metric** |
| Web search default | `external_web_access` defaults to `true`, but `AmazonBedrockFullAccess` lacks the permission → 403 |
| Quota increases | Via AWS Support case, **not** the Service Quotas console |

## Security notes for anyone reusing this code

- **Never `exec()` or `eval()` model output.** Model output is untrusted input
  ([OWASP LLM05][llm05]). A notebook kernel holds your live AWS credentials, so
  executing generated text there is arbitrary code execution against your own
  account. The coding-model notebooks verify generated code **statically** with
  `ast` (`inspect_code()` in [`_shared/mantle.py`](_shared/mantle.py)). To actually
  run generated code, use an isolated sandbox with no credentials and no network
  egress.
- **Validate every response before using it.** HTTP 200 does not mean complete or
  well-formed. Check `status` / `finish_reason`, then validate the shape.
- **Bound everything**: output tokens, client timeouts, retry attempts, agent
  iterations, concurrency ([OWASP LLM10][llm10]).
- **Keep secrets out of argv.** Pass API keys through the environment, never on a
  command line — process arguments are world-readable via `ps`.
- **Least privilege**, and short-term credentials over static keys.

[llm05]: https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/
[llm10]: https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption/

## Repository layout

```
_shared/mantle.py    path resolution · token minting · retrying HTTPS POST
                     SSE streaming · TTFT timing · lenient JSON parsing
                     static inspection of model-generated code
00-foundations/      read first — auth, paths, governance, quotas
01-…12-              one folder per model family
99-cross-cutting/    choosing a model · migrating from OpenAI · hardening
requirements.txt     openai, anthropic, aws-bedrock-token-generator, boto3
```

## Cost and cleanup

Each notebook makes tens of small calls with tight token budgets — cents, not
dollars, per run. Two cost more than the rest:

- `01-openai-gpt/02-web-search-and-grounding.ipynb` — web search is billed
  separately from tokens.
- `99-cross-cutting/01-choosing-a-model-and-api.ipynb` — a deliberate survey that
  touches every family.

Notebooks that create demo Projects archive them in a final cell. No other billable
resource is created.

## Scope

These notebooks demonstrate the documented public API of `bedrock-mantle`. Two areas
are deliberately **not** covered, so you know not to look for them:

- **Guardrails** — out of scope for this collection.
- **Server-side Lambda and AgentCore Gateway tools end to end** — the request shapes
  are shown and validated against deliberately-invalid ARNs, but no Lambda is
  deployed, and fine-tuning stops short of starting a real training job. Each
  notebook says so where it applies.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for how to
report a security issue. Please do not open a public GitHub issue.

## License

MIT-0. See [LICENSE](../../LICENSE).
