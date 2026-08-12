# Amazon Bedrock Mantle samples — OpenAI and Anthropic compatible APIs

Runnable Jupyter notebooks for **`bedrock-mantle`**, the Amazon Bedrock endpoint that
speaks the **OpenAI Responses API**, **OpenAI Chat Completions API**, and the
**Anthropic Messages API**. One folder per model family: Claude, GPT-5.x, gpt-oss,
Gemma 4, Qwen3, DeepSeek, GLM, Mistral, Kimi, MiniMax, Nemotron, Grok, Palmyra.

The runnable notebooks are meant as samples, not as production-ready runnable artifacts.

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
Bedrock API keys from whatever credentials are already in your environment as shown in the following example:

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

## Cost and cleanup

Each notebook makes tens of small calls with tight token budgets per run. Two cost more than the rest:

- `01-openai-gpt/02-web-search-and-grounding.ipynb` — web search is billed
  separately from tokens.
- `99-cross-cutting/01-choosing-a-model-and-api.ipynb` — a deliberate survey that
  touches every family.

Notebooks that create demo Projects archive them in a final cell.

## Scope

These notebooks demonstrate the documented public API of `bedrock-mantle`.

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for how to
report a security issue. Please do not open a public GitHub issue.

## License

MIT-0. See [LICENSE](../../LICENSE).
