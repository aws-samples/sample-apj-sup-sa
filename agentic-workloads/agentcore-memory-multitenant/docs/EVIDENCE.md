# Observed behaviour

What was **empirically observed** while building and testing this sample
(largely by `deploy/08_probe_iam_conditions.py` and the test suite), not
what the docs imply. Re-run the probe in your own account to check the
condition-key matrix; it writes its per-call results to the gitignored
`iam_probe_raw.json`.

## 1. Why one role per tenant instead of session-tag ABAC

The textbook design is a single shared IAM role assumed with a session tag
`tenant=<id>`, enforced with
`Condition:{StringLike:{"bedrock-agentcore:namespace":"/tenant/${aws:PrincipalTag/tenant}/*"}}`.

That needs `sts:TagSession`, and AWS Organizations SCPs often deny it. In
the account this sample was built in:

- A fully open trust policy (no `Condition` at all) granting
  `sts:AssumeRole` + `sts:TagSession` to the deploying principal:
  - plain `sts:AssumeRole` (no tags) -> **succeeded**
  - `sts:AssumeRole` with `Tags=[{"Key":"tenant","Value":"northwind"}]` ->
    **AccessDenied on sts:TagSession specifically**, even though nothing in
    the role's own trust/permission policy restricted it.

A deny that no IAM policy change can override points to a Service Control
Policy (SCPs apply before identity and resource policies are evaluated).

**Design:** **one static IAM role per tenant** (`agentcore-mt-tenant-access-<tenant_id>`), each with
its own permission policy using the tenant's *literal* id instead of
`${aws:PrincipalTag/tenant}`. The tools Lambda selects which role to assume
from a fixed `tenant_id -> role_arn` map built at deploy time from
`config/tenants.py` -- it can never construct an arbitrary role ARN from
caller input, so the defense-in-depth property (IAM blocks cross-tenant
namespace access even if application code has a bug) is preserved. See
`deploy/02_iam.py`.

## 2. Condition-key enforcement matrix (per action)

| Action | Request field checked | Condition key that works | Notes |
|---|---|---|---|
| `BatchCreateMemoryRecords` | `records[].namespaces[]` | `bedrock-agentcore:namespace` | ALLOWED for own-tenant namespace, DENIED for another tenant's namespace, using *only* a `namespace` condition. |
| `ListMemoryRecords` | `namespace` | `bedrock-agentcore:namespace` | Same pattern as above. |
| `RetrieveMemoryRecords` | `namespace` | `bedrock-agentcore:namespace` | Same pattern as above. |
| `CreateEvent` | `actorId` | `bedrock-agentcore:actorId` | ALLOWED for own-tenant actorId prefix, DENIED for another tenant's actorId. |
| `ListEvents` | `actorId` | `bedrock-agentcore:actorId` | Same pattern as above. |
| `GetMemoryRecord` | *(none -- only `memoryRecordId`)* | **none works** | A policy that conditions this action on `namespace` denies **even the caller's own record** -- i.e. `namespace` is simply not populated as a condition key for a by-ID lookup. There is no IAM-level way to scope this action by tenant in this service version. |
| `DeleteMemoryRecord` | *(none -- only `memoryRecordId`)* | **none works** | Same situation as `GetMemoryRecord` (not independently re-tested with its own call, but it takes the identical by-ID shape and the same IAM mechanism; granted unconditioned for the same reason). |
| `ListActors` | *(none -- only `memoryId`)* | **none works** | A policy conditioning this on `actorId` denies the call entirely (own-tenant check included) -- confirms `actorId` is not populated for this action either. **Not granted to tenant roles at all** and not exposed as a tool. |

### Critical bug found and fixed

A natural first policy combines `namespace` **and** `namespacePath`
**and** `actorId` in one `Condition` block. `StringLike` ANDs every key
listed inside it. Since none of our calls use the `namespacePath` request
field (we always pass `namespace`), that key is **never present** in the
request context -- and IAM's default behavior for a missing condition key
under `StringLike` is "does not match" (false), not "ignore". Including
`namespacePath` alongside `namespace` in the same statement therefore made
the **whole condition always false**, denying every call including
legitimate same-tenant ones. This is the first bug this sample hit, and
it took the empirical probe to find it.

**Fix:** split into separate `Statement` blocks per action group, each using
only the condition key that action actually supports:
- `namespace`-conditioned statement: `BatchCreateMemoryRecords`,
  `ListMemoryRecords`, `RetrieveMemoryRecords`.
- `actorId`-conditioned statement: `CreateEvent`, `ListEvents`.
- Unconditioned (but resource-scoped to this one Memory ARN only) statement:
  `GetMemoryRecord`, `DeleteMemoryRecord` -- compensated by an
  application-layer ownership check in `tool_forget` (see Limitations).

See the final policy in `deploy/03_memory.py::attach_tenant_access_policy`.

## 3. Bottom line

- IAM **does** provide a real, working, namespace/actorId-scoped backstop
  for the five actions our tools actually rely on for writes and reads
  (`BatchCreateMemoryRecords`, `ListMemoryRecords`, `RetrieveMemoryRecords`,
  `CreateEvent`, `ListEvents`). Verified by direct assumed-role calls against
  the Memory data plane, bypassing the Lambda and Gateway entirely
  (`tests/test_isolation.py`).
- IAM **cannot** scope by-ID record operations (`GetMemoryRecord`,
  `DeleteMemoryRecord`) or `ListActors` by tenant in this service version.
  Those are either not exposed as tools (`ListActors`) or protected only at
  the application layer (`forget`'s ownership check). This is a genuine
  limitation of the current AgentCore Memory IAM surface, not a shortcut we
  chose casually -- documented here and in the README.

## 4. AgentCore Memory's list/search consistency lag is occasionally longer than 60s

Across repeated full-suite runs, one run saw the lag exceed the test's
original 90s poll window (the write was confirmed via `GetMemoryRecord`
immediately, but `ListMemoryRecords` still didn't surface it after 90s of
polling). This is **inherent variance in a preview/newer AWS service's
indexing pipeline**, not a bug in this repo's code. The poll timeout was
increased to 150s as a result (`tests/mcp_client.py::poll_until_text_present`).
If the suite ever fails at exactly one `list_memories`/`recall` presence
assertion with no other changes, re-running it is a reasonable first
response -- that is the known failure mode, and it is distinct from an
actual isolation bug (which would fail consistently, not intermittently,
and would show data in the *wrong* tenant's namespace rather than *no*
data in the *right* one).

## 5. LLM non-determinism can make exact-verbatim e2e assertions flaky

One final full-suite run saw `test_agent_e2e_borealis_cross_agent_natural_language`
fail: the Order Support agent correctly retrieved the Shopping Assistant's
SHARED memory entry ("Size M, budget $200.") -- proving cross-agent shared
memory works exactly as designed -- but the retrieved text did not include
the random verbatim tag the test asks the model to echo back. The Shopping
Assistant's own reply claimed it had saved the tag, but the `remember_fact`
tool call it actually made evidently paraphrased rather than literally
including the random string. This is **LLM content non-determinism**, not
a security or system bug: the underlying mechanics (tool routed to the
correct shared namespace, Order Support's `list_memories` call retrieved
it) worked. Tests that ask a model to reproduce an exact random string
verbatim through two separate tool-use turns are inherently a little
flaky for this reason, on top of the AWS consistency-lag flakiness in
section 4. Across all other full-suite runs, this same test passed
cleanly.


## 6. Public UI abuse controls, verified live

- **WAF rate rule fires.** Bursting `GET /api/config` (cheap, no Bedrock)
  from one IP through the CloudFront URL after the web ACL propagated:
  `25 x 200, 105 x 429` (this IP had already sent requests in the same
  5-minute window), then `1 x 200, 9 x 429` 40s later; the static site
  (`GET /`) still returned `200` while `/api/*` was throttled, and `/api/*`
  came back to `200` about 4 minutes later. Note: right after
  `update_distribution` the distribution reported `Deployed` but the first
  ~600 requests over ~8 minutes were neither counted nor sampled by WAF --
  allow ~10 minutes of edge propagation before testing.
- **Caps hold through the real path** (WAF -> CloudFront -> OAC -> Lambda):
  a 1,001-char message and a 6-turn history both return `400`
  (`tests/test_webui_public.py`).
- **A real demo-faking bug, caught by the browser smoke, not by tests.**
  Sending chat history as context made the guided demo's step 3 (Northwind)
  answer "I have on file: size M, $250" -- the Borealis transcript had
  followed the shopper across the brand switch, so the model "knew" it from
  the transcript, not from Memory (and step 2's cross-agent recall proved
  nothing either). Fixed in `webui/app.js::startNewConversation`: any
  brand/agent switch resets the history sent as context. Re-run: Order
  Support recalls M/$250 from the shared namespace, Northwind knows nothing.

## 7. Knowledge base isolation (S3 Vectors), verified live

- One index per brand (`policies-<brand>`); each tenant role's only KB
  permission is `s3vectors:QueryVectors`/`GetVectors` on its own index ARN.
  Direct assumed-role test (`tests/test_kb_isolation.py`): Northwind's role
  queries `policies-northwind` fine and gets `AccessDeniedException` on
  `policies-borealis`. The same probe through the tools Lambda (its
  already-assumed Northwind role) is denied too.
- `kb_search` with Northwind's JWT plus `tenant_id`/`index`/`index_name`/
  `filter`/`namespace` all pointing at Borealis: the interceptor strips all
  five (`_trace.stripped_fields`), only `policies-northwind` is queried with
  a server-set `tenant=northwind` filter, and no Borealis fact ("60 days",
  "Trail Promise") comes back.
- RAG eval (`scripts/rag_eval.py`, 12 questions through the live site):
  91.7 -> 95.8 -> 100.0 over three iterations. The two misses were real
  agent-routing gaps, fixed in the prompt: fit advice ("I'm between sizes")
  and shipping costs were answered without calling `kb_search`. One was a
  scorer bug: the model writes ranges with an en dash ("1–2 days").
  Cross-brand leaks: 0 in every run, including a probe asking Cascade for
  the Borealis Trail Promise window.

## 8. Prompt caching never hit on Haiku 4.5 -- the prefix was too short

Measured with identical back-to-back Converse calls (tool specs + static
preamble + `cachePoint`): the prefix was **3,267 tokens**. Sonnet 4.5
(minimum 1,024) cached it (`cacheWrite=3267`, then `cacheRead=3267`);
**Haiku 4.5 -- the default model -- reported 0/0 every time**, because its
minimum cacheable prefix is 4,096 tokens. Earlier "cache hit" evidence was
from Sonnet only. Adding worked tool-routing examples to the preamble (real
guidance that also fixed two RAG eval misses) took it to **4,124 tokens**:
Haiku now writes 4,124 then reads 4,124, a live policy turn reads ~8.2K
cached tokens, and its cost fell from ~$0.0089 to ~$0.0035. The UI's
per-request cost was also ignoring cache tokens entirely; it now prices
reads at 0.1x and writes at 1.25x the input price.

## 9. AgentCore Runtime: one JWT end to end, verified live

- The webui backend calls the Runtime's HTTPS invocation endpoint with the
  brand+agent JWT as `Authorization: Bearer` -- no SigV4, so the backend
  role has no `InvokeAgentRuntime` permission at all. The Runtime's
  CUSTOM_JWT authorizer (Cognito discovery URL, the 4 brand+agent clients
  in `allowedClients`) rejects no token, an unsigned forged JWT, and a
  validly signed token from the no-scope client (`tests/test_runtime.py`).
- `requestHeaderConfiguration.requestHeaderAllowlist=["Authorization"]`
  passes the verified token through to the container; the agent forwards
  that same token to the Gateway. With Northwind's JWT and a payload
  claiming `tenant_id=borealis`, the agent served Northwind and every tool
  call's interceptor trace showed the same `client_id` the Runtime verified.
- The Runtime does not pass a container's 4xx through: the agent's `400`
  for a bad payload reaches the caller as **`424 Failed Dependency`**
  ("Received error (400) from runtime").
- Latency through CloudFront: ~10s for a new session (microVM cold start
  ~5s), ~4-5s warm. One session per brand+agent+shopper, 5-minute idle
  timeout.
- Observability: the Runtime publishes `AWS/Bedrock-AgentCore` metrics
  (Invocations, Sessions, Latency, Errors, Throttles) on its own. Its vended
  `TRACES` delivery to X-Ray is **rejected unless X-Ray Transaction Search is
  on** ("X-Ray Delivery Destination is supported with CloudWatch Logs as a
  Trace Segment Destination"), an account-wide setting; deploy/12 turns it
  on for the whole account/region.
  Vended traces alone carried no agent spans until the container ran under
  ADOT (`opentelemetry-instrument`); then `aws/spans` showed
  `AgentCore.Runtime.Invoke`, one GenAI `chat <model>` span per Converse
  call (model, input/output tokens, finish reason) and the Gateway `POST`s.
