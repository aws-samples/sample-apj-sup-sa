# agentcore-memory-multitenant

**Problem:** a multi-tenant SaaS agent needs memory that is shared where it
should be (two agents at the same brand serving the same shopper) and
isolated everywhere else (other agents, other shoppers, other brands). If
the tenant boundary lives in the prompt or in tool arguments, one
prompt injection or one bug in the tool code crosses it.

**Solution:** a multi-brand retail shopping assistant on **Amazon Bedrock
AgentCore** where the tenant comes only from a verified JWT, and isolation
is enforced at three independent layers: the JWT authorizers, a Gateway
REQUEST interceptor that strips and re-derives tenant context, and one
least-privilege IAM role per tenant whose conditions scope **AgentCore
Memory** namespaces and the brand's own knowledge-base index. The agent
runs on **AgentCore Runtime** and reaches Memory, a per-brand policy
knowledge base (Amazon S3 Vectors) and a mock retail API only through
**AgentCore Gateway**. All data is synthetic.

## What you will see

A web UI with a 5-step guided demo:

1. Tell the Borealis **Shopping Assistant** your size and budget.
2. Borealis **Order Support** (a different agent, no chat history carried
   over) already knows them, from the brand's shared memory.
3. **Northwind**, with the same shopper id, knows nothing.
4. A forged cross-brand read is blocked live, layer by layer.
5. Back at Borealis, the memory panel shows the shared fact, its namespace
   and actorId.

Policy questions ("What's your return window?") are answered from the
brand's own knowledge base, with citations. Every reply has a **What
happened** trace: the client_id the Runtime verified, the same client_id
at the Gateway, what the interceptor stripped, the namespaces or index
touched, and the IAM role assumed.

| Tenant | Brand | Agents |
|---|---|---|
| `northwind` | Northwind Apparel Co. | `support` -> **Shopping Assistant** |
| `borealis` | Borealis Outfitters | `merch` -> **Shopping Assistant**, `support` -> **Order Support** |
| `cascade` | Cascade Mart | `support` -> **Shopping Assistant** |

Borealis's two agents share one tenant-wide, **per-shopper** memory
namespace, and each also has an agent-private one, which shows cross-agent
sharing *and* agent-level isolation inside one tenant.

## Architecture

```
  Browser -- holds only a shopper_id it generated; never a JWT
     |  HTTPS
     v
  AWS WAF (per-IP rate limit on /api/*, managed rules) + Amazon CloudFront
     |-- static site: S3 (OAC)          webui/
     |-- /api/*: webui backend Lambda (Function URL, AWS_IAM via CloudFront OAC)
            - validates + caps the request, mints the brand+agent JWT
              (Cognito client_credentials; client secrets in SSM)
            |  Bearer <brand+agent JWT>        (no SigV4, no IAM permission)
            v
  AgentCore Runtime -- the agent (agents/runtime_agent, arm64 container, ADOT)
     - CUSTOM_JWT authorizer: Cognito issuer, the 4 brand+agent clients only
     - Authorization header allowlisted -> the agent reads the brand/agent
       from the verified JWT and forwards THE SAME JWT to the Gateway
     - Bedrock Converse tool loop (Haiku 4.5 / Sonnet 4.5, Guardrail,
       prompt cache) -- lambda_src/chat_core/chat_core.py, which the Lambda
       above also runs as its fallback if the Runtime is unavailable
            |  Bearer <same JWT>  (MCP tools/list, tools/call)
            v
  AgentCore Gateway
     - layer 1: CUSTOM_JWT authorizer (signature, issuer, allowedClients)
     - layer 2: REQUEST interceptor Lambda: client_id -> tenant/agent from a
       fixed table; STRIPS caller-supplied tenant/namespace/actor/index/filter
       args; INJECTS server_tenant_id/agent_id/shopper_id/actor_id
            v
  tools Lambda (lambda_src/tools): memory tools, kb_search, mock retail API,
  issue_refund (human approval) -- trusts only the injected server_* fields
     - layer 3: assumes agentcore-mt-tenant-access-<tenant> (fixed map)
            v                                        v
  ONE AgentCore Memory                     Amazon S3 Vectors
  /tenant/{brand}/{shared|agent}/{shopper}/   policies-<brand> (one index per brand)
  IAM: namespace / actorId conditions         IAM: own index ARN only
```

### The three isolation layers

1. **AuthN -- Cognito JWT, checked twice.** Every brand+agent pair is a
   Cognito app client with exactly one OAuth scope. The Runtime's and the
   Gateway's CUSTOM_JWT authorizers each verify signature, issuer and
   `allowedClients` before any code runs; forged, unsigned, unknown-client
   and missing tokens are rejected.
2. **Context derivation -- REQUEST interceptor.** It maps the verified JWT's
   `client_id` to a tenant/agent from a fixed table, strips every
   caller-supplied tenant/namespace/actor/index/filter argument and injects
   the server-derived ones. The tools Lambda trusts only those. The Runtime
   agent likewise takes the brand from the JWT, never the payload.
3. **IAM -- one static role per tenant.** The tools Lambda assumes the
   tenant's role (chosen by lookup, not input). Memory access is limited by
   `bedrock-agentcore:namespace` / `actorId` conditions on the literal
   tenant prefix; KB access to that tenant's own vector index ARN. A bug in
   the Lambda that computed the wrong namespace or index is still denied.

Per-tenant roles are used instead of session-tag ABAC because
`sts:TagSession` is commonly denied by AWS Organizations SCPs. Which IAM
condition keys AgentCore Memory actually enforces, per action, is in
[docs/EVIDENCE.md](docs/EVIDENCE.md). Memory design, the knowledge base,
the Runtime, observability, the web UI and the abuse controls are covered
in [docs/DESIGN.md](docs/DESIGN.md).

## Prerequisites

- An AWS account and credentials for a principal that can create IAM
  roles, Lambda functions, Cognito, CloudFront, WAF, S3, S3 Vectors, ECR
  and AgentCore resources.
- Amazon Bedrock model access to Claude Haiku 4.5, Claude Sonnet 4.5 and
  Titan Text Embeddings V2.
- Python 3.12, Docker with arm64 build support (for the Runtime image),
  `zip`.

## Deploy

```bash
cd agentic-workloads/agentcore-memory-multitenant
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export AWS_REGION=us-east-1        # any us-* region with AgentCore; see below
python3 deploy/deploy_all.py
```

Each step finds existing resources by name and updates them, so re-running
is safe. The account comes from `sts get-caller-identity`, the region from
`AWS_REGION` / `AWS_DEFAULT_REGION` (default `us-east-1`), and every resource
id the steps create is written to `deploy/state.json`, which is gitignored.
The CloudFront URL is printed at the end and stored as
`webui.cloudfront_url` in that file. A first run takes ~30-40 minutes;
CloudFront propagation dominates.

The chat models in `config/tenants.py` are US cross-Region inference
profiles. To deploy outside the US, switch them to your geography's
profiles.

The deploying principal is also trusted by the per-tenant roles, so the IAM
tests can assume them directly. Set `DEPLOYER_PRINCIPAL_ARN` to trust a
different principal.

> [!NOTE]
> `deploy/12_observability.py` turns on X-Ray Transaction Search, an
> account-wide setting the Runtime's trace delivery requires. Revert with
> `aws xray update-trace-segment-destination --destination XRay`.

## Test

```bash
python -m pytest tests/ -q        # ~3 min, real AWS calls against the deployed stack
python3 scripts/rag_eval.py       # RAG eval through the deployed site
python3 scripts/eval_gate.py      # release gate: isolation suite + RAG eval vs baseline
```

- `test_isolation.py` -- per-tenant read/write, cross-agent sharing,
  agent-private isolation, spoofed-arg/forged-JWT/unauthorized-client/no-token
  attacks, direct assumed-role IAM denials, write-through fast path.
- `test_kb_isolation.py` -- own-brand retrieval, spoofed index/filter
  stripped, IAM denies cross-brand KB queries (via the Lambda and directly).
- `test_runtime.py` -- Runtime inbound JWT auth, payload cannot choose the
  tenant, same client_id at the Gateway, UI served by the Runtime.
- `test_webui_public.py` -- no sign-in needed, request caps, WAF/concurrency
  wiring, cache-aware cost, Runtime fallback.
- `test_agent_e2e.py` (Strands agents), `test_human_approval.py`,
  `test_eval_gate.py`.

Without a deployed stack (no `deploy/state.json`), `pytest tests/` runs the
offline unit tests (eval gate logic, webui backend helpers and Runtime
fallback) and skips the rest.

Memory list/search is eventually consistent (up to ~60s, occasionally more),
so presence assertions poll for up to 150s; LLM-driven tests can be
non-deterministic ([docs/EVIDENCE.md](docs/EVIDENCE.md) sections 4-5).

## Teardown

```bash
python3 deploy/destroy_all.py            # DRY RUN -- prints the plan, deletes nothing
python3 deploy/destroy_all.py --yes      # tears everything down, clears deploy/state.json
```

Transaction Search (account-wide) is left on; revert it as shown above.

## Cost

Pay-per-use throughout (Gateway, Memory, Lambda, Runtime, S3 Vectors,
Bedrock). **Idle: ~$0.25/day**, almost all of it the WAF web ACL (~$5/month
+ $1/month per rule). The Runtime costs nothing without sessions (5-minute
idle timeout), the KB is a few KB of vectors, the ECR images ~140 MB. At
scale (`python3 scripts/cost_forecast.py`): ~$580/month on Haiku for 50,000
shoppers x 3 chats with 40% policy questions (~$0.0039/chat); Sonnet ~2.7x.

## Repository layout

```
config/tenants.py            tenant/agent/namespace/scope naming, region, models -- single source of truth
deploy/                      idempotent boto3 deploy steps, see deploy_all.py for the order
  01..07                     Cognito, IAM, Memory, Lambda build, Lambdas, Gateway, Gateway target
  08_probe_iam_conditions.py optional empirical IAM condition-key probe (docs/EVIDENCE.md)
  09..11                     webui backend, CloudFront + S3 site, Guardrail
  12_observability.py        X-Ray, Transaction Search, Runtime deliveries, dashboard (run last)
  13_abuse_controls.py       WAF + reserved concurrency
  14_knowledge_base.py       S3 Vectors per-brand policy KB + IAM
  15_runtime.py              ECR image, Runtime role, AgentCore Runtime
  destroy_all.py             full teardown, DRY RUN by default (needs --yes)
agents/runtime_agent/        the Runtime agent: app.py, Dockerfile
agents/strands_tenant_agent.py  Strands agent on the Gateway (used by tests/test_agent_e2e.py)
lambda_src/chat_core/        chat loop shared by the Runtime agent and the Lambda fallback
lambda_src/interceptor/      REQUEST interceptor: JWT -> tenant, strips spoofed args
lambda_src/tools/            memory tools, kb_search, mock retail API, per-tenant AssumeRole
lambda_src/webui_backend/    /api/config, /api/chat (-> Runtime), /api/memory, /api/attack
kb/                          synthetic policy docs + RAG answer key
webui/                       static frontend + assets/
scripts/                     rag_eval.py, eval_gate.py (+ eval_baseline.json), cost_forecast.py, run_validation.sh
tests/                       test suite (mostly against the deployed stack)
docs/                        DESIGN.md, EVIDENCE.md
```

## Limitations

- **IAM cannot scope `GetMemoryRecord`/`DeleteMemoryRecord` by tenant** (no
  namespace in the request); `forget` relies on an application-layer
  ownership check. `ListActors` is not tenant-scopable and not exposed.
- **Agent-private fast-path events** are filtered in the tools Lambda: IAM's
  `actorId` condition scopes by tenant, not by agent.
- **No end-user auth in the UI** -- anyone with the link can pick any brand
  and shopper id. Spend is bounded by the abuse controls; tenant identity
  still never comes from the browser. A real SaaS would sign shoppers in
  and derive the brand from that session.
- **The webui Lambda holds the Cognito client secrets** (SSM) to mint the
  brand+agent JWTs, and stays in the path in front of the Runtime.
- **The interceptor and the Runtime agent decode, not re-verify, the JWT**
  -- by design: neither can run unless an authorizer already verified it.
- **No VPC / private endpoints**; default networking throughout.
- **One deployment per account and region**: resource names are fixed
  (`agentcore-mt-*`), with the account id appended where names must be
  globally unique.
