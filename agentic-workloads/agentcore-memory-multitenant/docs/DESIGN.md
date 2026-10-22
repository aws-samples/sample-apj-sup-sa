# Design notes

How each part of the sample works. For what was observed (IAM condition
keys, consistency lag, caching thresholds), see [EVIDENCE.md](EVIDENCE.md).

## Memory design

One semantic `customMemoryStrategy`, namespace template
`/tenant/{tenantid}/{agentid}/{shopperid}/` (`agentid="shared"` = brand-wide;
`shopperid` by regexPattern). `actorId = tenant-<brand>-<shopper>`.
`namespaceKeys` cannot be changed on an existing Memory, so changing the
namespace shape means recreating it (`deploy/03_memory.py`).

- `remember_fact` -> `BatchCreateMemoryRecords` **plus a write-through
  `CreateEvent`**: long-term records take ~40-60s (sometimes longer) to
  appear in `ListMemoryRecords`/`RetrieveMemoryRecords`, while
  `CreateEvent`/`ListEvents` are immediately consistent. `recall` /
  `list_memories` merge both, newest first, so the second agent sees the
  fact within a second. The UI tags each record **Indexed** or **Pending**.
- `remember` -> `CreateEvent` for conversational turns (async extraction).

Agent ids `support`/`merch` are stable identifiers; the display names come
from `config/tenants.py`. Renaming an id means widening the Memory
resource's `agentid` namespaceKey.

## Policy knowledge base (RAG)

Synthetic returns / sizing / shipping policies per brand (`kb/docs/`), one
chunk per section, embedded with Titan Text Embeddings V2 (512-d) into one
S3 Vectors index per brand (`deploy/14_knowledge_base.py`). `kb_search`
queries the caller brand's index under that brand's role with a
server-set `tenant` filter, and returns passages with `cite` tags; the
agent cites them (`[returns-policy §1]`) and the UI lists the sources.
`python3 scripts/rag_eval.py` scores 12 answer-key questions through the
deployed site and counts cross-brand leaks. Idle cost: ~$0 (no Bedrock KB
or OpenSearch resource; a few KB of vectors).

## AgentCore Runtime

`deploy/15_runtime.py` builds the arm64 image (content-hash tag, immutable
ECR), creates a least-privilege execution role (pull its own ECR
repository, own logs/traces/metrics, two chat models, the one guardrail --
no Memory, KB, SSM or Cognito access) and the Runtime with inbound JWT auth
and the `Authorization` header allowlisted. One session (microVM) per
brand+agent+shopper, 5-minute idle timeout. ~10s for a cold session, ~4-5s
warm. The webui Lambda keeps the same chat loop as a fallback and every
response says which served it (`served_by`).

## Observability

`deploy/12_observability.py`: X-Ray on the 3 Lambdas; the Runtime agent
under ADOT with vended TRACES to X-Ray (GenAI span per model call),
APPLICATION_LOGS and USAGE_LOGS (per-session vCPU/memory) to log groups;
dashboard `agentcore-mt-memory-demo` with Runtime, Gateway per-tool, Lambda
and Bedrock metrics. Runtime trace delivery needs **X-Ray Transaction
Search**, an account-wide setting this step turns on (revert:
`aws xray update-trace-segment-destination --destination XRay`). ADOT's
GenAI instrumentation captures message content by default
(Runtime-supplied setting) -- fine for synthetic data, worth turning off
for real shoppers.

## Web UI

Styled with AWS design tokens (`webui/assets/colors_and_type.css`), Amazon
Ember Display/Mono, the AWS logo, and official AWS Architecture icons for
the "How it works" diagram. Built around memory:

- **Memory panel:** the shared and agent-private namespaces for this
  brand/agent/shopper (paths as derived server-side), each record Indexed or
  Pending, and the server-derived actorId.
- **Namespace map:** every brand's namespaces for this shopper; only the
  ones this agent can reach are highlighted.
- **Guided demo** (5 steps), **isolation proof** (forged args vs namespaces
  actually searched, 4 layer cards), **Sources** under policy answers,
  **What happened** trace per reply, **human-approval** dialog for
  `issue_refund`, per-request host/model/tokens/cache/cost line, and a
  Haiku/Sonnet model switch.
- A brand/agent switch never carries chat history over, so recall comes
  from Memory, not the transcript ([EVIDENCE.md](EVIDENCE.md) section 6).

## Abuse controls

The site is public and calls Bedrock. `deploy/13_abuse_controls.py`
(idempotent, tagged `project=agentcore-mt-memory-demo`): a WAF web ACL on
the distribution (an IP gets `429` after 100 `/api/*` requests per 5
minutes; `AWSManagedRulesCommonRuleSet`), reserved concurrency 5 on the
backend Lambda, and server-side caps (message <= 1,000 chars, <= 4 prior
turns, unknown model/brand -> `400`; the Runtime agent re-validates).

The backend's Lambda Function URL uses `AuthType=AWS_IAM` behind CloudFront
Origin Access Control, which needs **both** `lambda:InvokeFunctionUrl` and
`lambda:InvokeFunction` permissions for the distribution
(`deploy/10_webui_cdn.py`).

## Models and guardrail

Haiku 4.5 ~$0.001/$0.005 per 1K input/output tokens, Sonnet 4.5 3x that
(`config/tenants.py::MODEL_PRICING_PER_1K_TOKENS`). Prompt caching serves
the ~4,100-token static prefix (tool specs + preamble) from cache on both
models -- on Haiku only because the prefix crosses its 4,096-token minimum
([EVIDENCE.md](EVIDENCE.md) section 8) -- so a typical tool-using turn costs
~$0.003-0.004 on Haiku. Haiku is the default (routing-heavy retail
assistant work, lower latency, ~1/3 the price); Sonnet suits brands that
want more consultative conversations. The Bedrock Guardrail
(`deploy/11_guardrail.py`) passes normal shopping questions and blocks
off-topic and prompt-injection requests.
