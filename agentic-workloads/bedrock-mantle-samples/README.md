# Amazon Bedrock Mantle samples

Runnable Jupyter notebooks for the **`bedrock-mantle`** endpoint — Amazon Bedrock's
OpenAI- and Anthropic-compatible API surface — with one folder per model family.

Every notebook is **self-contained**: if you land here from a search engine looking
for one model family, that family's notebook is enough on its own. The
`00-foundations/` notebooks go deeper on shared mechanics, and each family notebook
links to them where relevant.

Every code cell in this collection has been **executed end to end against the live
endpoint**. The committed outputs are real API responses, not illustrations.

---

## Start here

| Notebook | Why |
|---|---|
| [`00-foundations/01-endpoints-auth-and-the-three-paths.ipynb`](00-foundations/01-endpoints-auth-and-the-three-paths.ipynb) | Auth (SigV4 + short-term API keys), the three URL paths, model discovery |
| [`00-foundations/02-governance-projects-and-retention.ipynb`](00-foundations/02-governance-projects-and-retention.ipynb) | Projects/Workspaces, cost attribution, data retention & ZDR, CloudWatch |
| [`00-foundations/03-scaling-tiers-and-latency.ipynb`](00-foundations/03-scaling-tiers-and-latency.ipynb) | Quotas, retries, service tiers, TTFT measurement |

Then jump to your model family.

---

## The single most important thing

**Model IDs map to three different URL paths.** Guessing wrong gives you a 404, a
400, or — occasionally — a request that simply stalls.

| Path | Families |
|---|---|
| `/openai/v1/…` | `google.gemma-4*`, `openai.gpt-5*`, `xai.*` |
| `/v1/…` | `openai.gpt-oss*` and every Chat-Completions-only family |
| `/anthropic/v1/…` | `anthropic.*` only |

Control-plane paths (`models`, `files`, `fine_tuning`, `organization/projects`,
`data_retention`) are **always** under `/v1/…`, never `/openai/v1/…`.

```python
def api_prefix(model_id):
    if model_id.startswith("anthropic."):
        return "/anthropic/v1"
    if model_id.startswith(("google.gemma-4", "openai.gpt-5", "xai.")):
        return "/openai/v1"
    return "/v1"
```

Note the split *inside* the OpenAI family: `gpt-5.*` uses `/openai/v1`, `gpt-oss*`
uses the bare `/v1`.

---

## Model families

| Folder | Models | Primary API |
|---|---|---|
| [`01-openai-gpt/`](01-openai-gpt/) | gpt-5.6 sol/terra/luna, gpt-5.5, gpt-5.4, gpt-oss 20b/120b, gpt-oss-safeguard | Responses |
| [`02-anthropic-claude/`](02-anthropic-claude/) | opus-5, sonnet-5, opus-4-8, opus-4-7, haiku-4-5, fable-5 | Messages |
| [`03-google-gemma/`](03-google-gemma/) | gemma-4 31b / 26b-a4b / e2b | Responses |
| [`04-qwen/`](04-qwen/) | qwen3 32b / 235b / next-80b, coder 30b/480b/next, vl-235b | Chat Completions |
| [`05-deepseek/`](05-deepseek/) | v3.2, v3.1 | Chat Completions |
| [`06-zai-glm/`](06-zai-glm/) | glm-5, glm-4.7, glm-4.7-flash, glm-4.6 | Chat Completions |
| [`07-mistral/`](07-mistral/) | mistral-large-3, ministral 3b/8b/14b, magistral, devstral-2, voxtral | Chat Completions |
| [`08-moonshot-kimi/`](08-moonshot-kimi/) | kimi-k2.5, kimi-k2-thinking | Chat Completions |
| [`09-minimax/`](09-minimax/) | minimax-m2.5, m2.1, m2 | Chat Completions |
| [`10-nvidia-nemotron/`](10-nvidia-nemotron/) | nemotron-super-3-120b, nano 9b/12b/30b | Chat Completions |
| [`11-xai-grok/`](11-xai-grok/) | grok-4.3 | Responses |
| [`12-writer-palmyra/`](12-writer-palmyra/) | palmyra-vision-7b | Chat Completions |
| [`99-cross-cutting/`](99-cross-cutting/) | choosing a model, migrating, hardening | all |

**The Responses API covers only three families.** Chat Completions is the universal
surface for open-weight models, and Claude is Messages-only on this endpoint.

---

## Feature availability at a glance

| Feature | Where |
|---|---|
| **Web Search** (AWS-hosted, with citations) | `openai.gpt-5.*` only, Responses API, us-east-1/2 + us-west-2 |
| Reasoning traces you can *read* | Responses API only (gemma-4, gpt-5.x, gpt-oss, grok) |
| Adaptive thinking | Claude opus-5 / sonnet-5 / opus-4-8 (**not** haiku-4-5) |
| Explicit prompt-cache breakpoints | `openai.gpt-5.6-*` |
| Prompt caching with 1-hour TTL | Claude |
| Server-side tools (Lambda MCP / AgentCore Gateway) | Responses API models |
| Built-in `notes` / `tasks` tools | `openai.gpt-oss-*` only |
| Computer use, memory, compaction | Claude (beta headers) |
| Server-side conversation state | Responses API + `store=True` |
| `count_tokens` | Claude, mantle only |
| Fine-tuning (RFT) | `gpt-oss-20b`, `qwen3-32b` — **us-west-2 only** |
| Batch inference | **`bedrock-runtime`**, not mantle |
| Cross-Region inference, Provisioned Throughput | `bedrock-runtime` only |

---

## Region footprint

| Region | Models | Notable gaps |
|---|---|---|
| `us-east-1` | 55 | — (the only Region with the full Claude set) |
| `us-east-2` | 49 | **no Anthropic models** |
| `us-west-2` | 47 | Claude haiku-4-5 only; no gpt-5.5 / gpt-5.6-sol |
| `eu-central-1` | 33 | no Anthropic, no gpt-5.x, no xAI, no DeepSeek |

`gemma-4` is the only family present in all four.

---

## Gotchas that cost the most time

These are all demonstrated live in the notebooks, not asserted.

| Gotcha | Detail |
|---|---|
| No boto3 client | There is **no** `boto3.client("bedrock-mantle")`. Use the SDK to *sign*, or use the OpenAI/Anthropic SDKs |
| Signing name | `bedrock` (not `bedrock-mantle`) for SigV4 |
| `max_output_tokens` | Minimum **16** on the Responses API; Chat Completions accepts 1 |
| No universal sampling config | Gemma 4 takes `temperature`, rejects `top_p`. Grok is the **exact inverse**. Frontier Claude rejects `temperature` |
| `service_tier` | `gpt-5.x` accepts `default` **only**; `reserved` is rejected everywhere as a parameter |
| `reasoning.effort` | `none`/`low`/`medium`/`high` — **`minimal` is rejected** |
| `store` defaults to `true` | 30-day retention unless you opt out per request |
| `store=False` breaks chaining | `previous_response_id` then returns 404 |
| 200 ≠ correct | Truncation, trailing characters after valid JSON, and silently-ignored constraints all return 200 |
| Gemma 4 "strict" JSON | Appends trailing characters in roughly half of runs — never bare `json.loads()` |
| qwen3-coder tool arguments | **Truncated mid-object** (reproduced 10/10). Repair before use *and* before echoing, or the next request 400s |
| gpt-oss `tool_choice` | Supports **`auto` only**. Named form returns 200 and ignores the constraint |
| Claude `content[0]` | Reasoning models put a `thinking` block first — filter by block type |
| Retention gating | A model can be `unavailable` under your retention mode (`claude-fable-5`) |
| `writer.palmyra-vision-7b` | Rejects **tools** and rejects a **`system` role** |
| Wrong path may **stall** | Grok on `/v1/responses` never answers. Always set a client timeout |
| CloudWatch namespace | `AWS/BedrockMantle`, not `AWS/Bedrock` — and there is **no 5xx metric** |
| Web Search default | `external_web_access` defaults to `true`, but `AmazonBedrockFullAccess` lacks the permission → 403 on authz |
| Quota increases | Via AWS Support case, **not** the Service Quotas console |

---

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

AWS credentials with Bedrock Mantle access. The managed policy
`AmazonBedrockMantleInferenceAccess` covers inference; add
`AmazonBedrockMantleFullAccess` if you want to create Projects.

```bash
aws iam attach-role-policy --role-name YourRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonBedrockMantleInferenceAccess
```

The notebooks mint **short-term** Bedrock API keys from your ambient IAM
credentials. Nothing to store, nothing to rotate:

```python
from aws_bedrock_token_generator import provide_token
from openai import OpenAI

client = OpenAI(
    api_key=provide_token(region="us-east-1"),
    base_url="https://bedrock-mantle.us-east-1.api.aws/openai/v1",
)
```

`_shared/mantle.py` holds the helpers the notebooks import: path resolution, token
minting, retrying POST, SSE streaming, TTFT timing, and lenient JSON parsing.

---

## Cost

Each notebook makes tens of small calls with tight token budgets — cents, not
dollars, per run. Two exceptions worth knowing:

- `01-openai-gpt/02-web-search-and-grounding.ipynb` — Web Search is billed
  separately from tokens.
- `99-cross-cutting/01-choosing-a-model-and-api.ipynb` — a deliberate survey that
  touches every family.

Notebooks create demo Projects and archive them at the end.

---

## Licence

See the repository root.
