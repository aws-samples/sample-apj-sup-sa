# Threat model: Agentic Insights Radar on AWS

This threat model follows the four questions of threat modelling. It covers the code in this
repository as deployed by `deployment/deploy.sh` with default settings. File paths are relative to
the repository root.

## 1. What are we working on?

### System

Agentic Insights Radar (AIR) is a single-tenant web application that one organisation deploys into its own AWS
account. AI agents ("modules") read public web content and the organisation's product documents,
write evidence and insights into a knowledge graph, and propose entries for curated radars. Users
read the results in a web app, ask a chat assistant, and use an MCP server from their own machine.
The architecture is described in the [README](../../README.md#architecture) and the
[implementation guide](../implementation-guide.md#architecture-details).

### Actors

| Actor | Trust | Access |
|---|---|---|
| Anonymous internet client | Untrusted | CloudFront URL, the HTTP API's `execute-api` address, the two agent function URLs (IAM signature required), the Cognito hosted UI and user pool endpoints |
| Viewer | Authenticated, low privilege | Read everything in the workspace, chat |
| Editor | Authenticated | Viewer plus radars, suggestions, feedback, "working on", draft requests |
| Admin | Authenticated, high privilege | Everything in the app: users, products, documents, modules, prompts, tool grants, MCP servers, budgets, kill switch |
| Module run | Machine identity, constrained | Agent tools its module grants, for one run, through the module runtime role and a run token |
| Chat runtime | Machine identity, acting for a user | User-facing read routes with the user's own token; the chat-usage route with its role |
| External content | Untrusted, possibly hostile | Web pages, search results, browser sessions, documents, MCP server responses that reach agents |
| Model output | Untrusted | Text and tool calls produced by Amazon Bedrock models |
| AWS account operator | Trusted | Deploys, configures and can read all data through AWS APIs |

### Assets

| Asset | Why it matters | Where it lives |
|---|---|---|
| User credentials and tokens | Account takeover | Cognito; ID and access tokens in the browser's `sessionStorage`; MCP refresh token in a 0600 file on the user's machine |
| Uploaded internal documents and product profiles | Confidential business information | S3 assets bucket (`docs/`), ops table, knowledge base index |
| Knowledge graph, insights, drafts, radars | Business intelligence; integrity drives decisions | Graph and ops tables, S3 assets bucket, S3 Vectors |
| Run-token HMAC key | Forging agent authority | AWS KMS (never leaves KMS) |
| Data encryption key | Access to all data at rest | AWS KMS |
| Module definitions (prompts, grants, MCP servers) | Control what agents do and where data can go | Ops table |
| Budgets and the kill switch | Spend on Amazon Bedrock and AgentCore | Ops table |
| Audit log | Accountability | Ops table (400-day TTL) |

### Data flows and trust boundaries

| ID | Boundary | Flow | Authentication |
|---|---|---|---|
| TB1 | Internet to CloudFront and AWS WAF | Browser loads the app and calls `/api/*` | None at the edge; WAF rules and rate limit |
| TB2 | Browser to Cognito | Sign-in with password and TOTP; authorization code with PKCE | Cognito hosted UI (classic); the app clients allow no SRP or password sign-in |
| TB3 | CloudFront (or the direct `execute-api` address) to the HTTP API | `/api/*` requests | Cognito JWT authorizer (issuer, audience) on every route except `GET /api/config`; role check in the Lambda |
| TB4 | Module runtime to the agent tool gate | `POST /agent/tools/{tool}` on the `AgentTools` function URL | SigV4 (function URL `AWS_IAM` auth; no resource policy, so only principals in this account allowed `lambda:InvokeFunctionUrl`) as the module runtime role, plus the KMS-signed `X-MR-Run-Token`, plus the module's current grants |
| TB5 | Chat runtime to the backend | `/api/*` reads on the HTTP API with the user's token; `POST /agent/chat/usage` on the `ChatUsage` function URL | User JWT; SigV4 as the chat runtime role for usage |
| TB6 | Browser to the chat runtime | Chat turns | AgentCore inbound JWT authorizer (user pool discovery URL, app client audiences) |
| TB7 | Agents, the crawler and the source watcher to the internet | `http_fetch`, documentation crawl, source watcher checks, web search, AgentCore Browser, admin-attached MCP servers | Outbound; content is untrusted |
| TB8 | Agents to Amazon Bedrock | Model calls; model output returns | IAM; output is untrusted |
| TB9 | MCP server on a user's machine to the API | `/api/*` with the user's token | Cognito PKCE sign-in, loopback redirect |
| TB10 | Admins to configuration | Prompts, grants, MCP servers, budgets, users | Admin role in the app; AWS IAM for operators |

## 2. What can go wrong?

STRIDE: **S**poofing, **T**ampering, **R**epudiation, **I**nformation disclosure, **D**enial of
service, **E**levation of privilege.

| ID | Boundary | STRIDE | Threat |
|---|---|---|---|
| T1 | TB2, TB3 | S | Credential stuffing, phishing or a stolen password signs in as a user |
| T2 | TB1, TB3 | S, I | A token is stolen from the browser (for example through XSS) or replayed |
| T3 | TB3 | E | A user raises their own role by tampering with group claims or calling admin routes |
| T4 | TB3 | E | An editor changes a radar they do not own, or a viewer writes |
| T5 | TB4 | S, E | A forged, replayed or over-scoped run token lets a run call tools it was not granted |
| T6 | TB7 | T, E | **Indirect prompt injection**: a crawled page, search result, document or MCP response instructs the agent to ignore its task, call tools, or write false or harmful content |
| T7 | TB7 | I | **SSRF**: an agent, a source address, a documentation URL or an MCP server URL makes the platform reach loopback, private, link-local or metadata addresses, including through redirects and DNS rebinding |
| T8 | TB7, TB8 | I | **Data exfiltration by an agent**: a manipulated agent places internal document content in a fetch URL, a search query, an MCP call, or a graph node |
| T9 | TB4 | T | An agent overwrites other modules' outputs, writes radar entries, or plants attributes that make the API read other objects |
| T10 | TB1, TB3, TB8 | D | **Denial of wallet**: runaway runs, chat loops or floods of requests run up Bedrock and AgentCore costs |
| T11 | TB1, TB3 | D | Request floods exhaust API or Lambda capacity |
| T12 | TB3 | T, I | **Stored XSS**: HTML or Markdown written by an agent or a user runs script in another user's browser |
| T13 | TB3 | T | **CSV formula injection**: an exported radar CSV runs formulas when opened in a spreadsheet |
| T14 | all | T | **Supply chain**: a compromised or vulnerable dependency enters the build |
| T15 | TB3 | I | Uploads reach unintended keys, wrong content types, or oversized objects; presigned URLs are abused |
| T16 | all | I | Tokens or run tokens leak into logs |
| T17 | TB3, TB4 | R | A user or a module denies having changed data |
| T18 | TB6 | E | The chat assistant is used as a confused deputy to read or write beyond the user's rights |
| T19 | TB10 | E, I | An admin attaches a hostile MCP server or writes a prompt that sends data out |
| T19b | TB10 | E, I | An admin adds or attaches a skill whose text tells runs to send data out or ignore the rules |
| T20 | TB9 | S, I | The MCP server's saved tokens are stolen from the user's machine, or the sign-in callback is hijacked |
| T21 | TB3 | I | The direct `execute-api` address bypasses the WAF |
| T22 | all | I | Data at rest or in transit is read by an unauthorised party |

## 3. What are we going to do about it?

| Threat | Mitigations | Code |
|---|---|---|
| T1 | Self sign-up off (admins invite); required TOTP MFA; 12-character password with all character classes; temporary passwords valid 3 days; email-only recovery; user-existence errors suppressed; WAF rate limit and IP reputation list on the CloudFront app (the Cognito hosted UI is on Cognito's own domain, outside this WAF). Optional: Cognito Plus threat protection | `infra/insights_radar_infra/auth.py`, `edge.py` |
| T2 | Public PKCE clients, no client secret; 1-hour ID and access tokens, 12-hour refresh, token revocation; tokens in `sessionStorage`; strict CSP (`script-src 'self'`, no inline scripts, `frame-ancestors 'none'`), HSTS, `X-Frame-Options: DENY` | `infra/insights_radar_infra/auth.py`, `frontend.py`, `frontend/src/auth/session.tsx` |
| T3 | Role derived only from the verified `cognito:groups` claim; one static role-to-action table; only admins can change groups, and the API refuses to remove the last admin; Cognito's `email` attribute is immutable | `backend/src/insights_radar/identity/principal.py`, `policy.py`, `users.py` |
| T4 | `Policy.require` on every route; radar edits also check the radar's owner and editor list; admins override | `backend/src/insights_radar/api/routes.py`, `backend/src/insights_radar/radars/service.py` |
| T5 | Run token `{module, run_id, tools, exp, product_id}` signed with KMS `GenerateMac` on an HMAC_256 key and verified with `VerifyMac`; expiry of at most 8 hours; the API also requires SigV4 from the module runtime role, so a token alone is useless; each tool call is checked against the token **and** the module's current grants; the token is held in runtime code and never placed in the model's context | `backend/src/insights_radar/identity/run_token.py`, `backend/src/insights_radar/api/agent_tools.py`, `agents/src/insights_radar_agents/tool_api.py` |
| T6 | Every untrusted tool result (web, search, browser, code, MCP, graph text) is wrapped in an `untrusted-tool-output` fence by `UntrustedOutputGuard`; images in tool output are dropped, because text drawn in pixels cannot be neutralised; `Defang` folds homoglyphs, strips zero-width and bidi characters and neutralises fence and context markers so content cannot close the fence; untrusted-content rules come last in every system prompt; admin instructions and user briefs are inserted as data, and the draft prompt limits a brief to tone, audience and outline. The Bedrock Guardrail's prompt-attack filter screens users' chat messages and each run's start message only: Guardrails do not inspect tool results, so it is no control against content an agent fetched. The authoritative control is server-side (T5, T9): whatever the model is persuaded to try, writes are limited to the run's grants | `agents/src/insights_radar_agents/guard.py`, `backend/src/insights_radar/core/defang.py`, `agents/src/insights_radar_agents/prompts.py`, `agents/src/insights_radar_agents/prompt_templates/_untrusted.md`, `infra/insights_radar_infra/agents.py` |
| T7 | `UrlGuard` rejects non-https, credentials, control characters, `localhost`, `.internal`, metadata host names, and any non-globally-routable IP including decimal, hex, octal and short IPv4 forms and IPv4 embedded in IPv6 (mapped, NAT64, 6to4, Teredo); checked when a URL is saved and again at fetch time and on every redirect. `SafeResolver` resolves once and refuses if any address is internal; `PinnedBackend` connects only to those vetted addresses (no DNS rebinding). Admin-attached MCP servers use the same checks through `PinnedAsyncBackend`, with redirects and proxy settings off. Every address the *model* sends the AgentCore Browser to passes `UrlGuard` and the length budgets, and the browser may not type into a page or run script; what `UrlGuard` does **not** see is where the page sends the browser next -- a `click` on a link, `back`/`forward`, or a redirect the page starts (HTTP 30x, meta refresh, script) reaches any address, `http://` included, and is not logged as a navigation. The browser runs in an AWS-managed sandbox outside this account's network, so it has no private network to reach, and what it reads comes back fenced as untrusted output (T6); the remaining risk is an outbound request to an attacker's public address. Fetches through `http_fetch` are bounded (5 redirects, 20 s per network read, 2 MiB, text types), and a configured source's composed address passes the same length budget as one the model types. No VPC, so there is no private network to reach | `backend/src/insights_radar/core/urls.py`, `core/fetch.py`, `agents/src/insights_radar_agents/connectors.py` |
| T8 | `product_knowledge` cannot be combined with `web_fetch`, `web_search`, `browser`, `code_interpreter` or `mcp`: `ModuleDefinition.validate` refuses the combination when a module is saved, and `AgentTools` refuses it again when a run starts and on every tool call, so a definition stored before the rule existed cannot run. Among the built-ins, Gaps, Content ideas and Content drafts read product knowledge and hold no outbound grant; Scout and Signals hold the outbound grants and never read product knowledge; Trends and the Radar curator hold neither. Anything written by a module that holds, or ever held, `product_knowledge` is marked `private_writer`, and a module with an outbound grant never reads those nodes, so private text cannot reach an outbound run through the graph either. Beyond that: fences and prompt rules (T6), including a rule that queries and addresses hold only public terms; browser addresses are limited to public https with short query strings, and the browser may not type into pages or run script; runtime-side tools (web search, browser, code interpreter, MCP, `http_fetch`) are capped per run (60, 120, 30, 200, 120) and re-checked against the module's current grants every minute, revoking every runtime-side grant when the backend refuses the check or three checks fail in a row; a tool that is neither backend-gated nor registered runtime-side is refused; an address that appeared only in fetched content is defanged before it is written to an output; output nodes are sanitised and visible to the workspace only; `http_fetch` is GET-only and SSRF-guarded | `backend/src/insights_radar/modules/templates.py`, `modules/model.py`, `agents/src/insights_radar_agents/tool_policy.py`, `agents/src/insights_radar_agents/output_guard.py` |
| T9 | A module may only create outputs of its own output kind and edit outputs it created; every write is stamped with the module and run; evidence needs a real https source URL; edges are checked against a closed registry; radar entries can only be proposed; draft object keys are derived from the node id, never from node attributes | `backend/src/insights_radar/api/agent_tools.py`, `backend/src/insights_radar/graph/registry.py` |
| T10 | Per-module daily budgets in weighted tokens (warn 70%, finish at 90%, refuse at 100%, hard cap 100 M); a run stops when the budget left at its start is spent or after 200 model calls; modules ship off; minimum schedule 15 minutes; concurrency 1-10; chat shared and per-user budgets, a 12-model-call turn cap and an 8,000-character message cap; chat usage is reported only by the chat runtime role over IAM, with an implausibility cap; one AI kill switch stops every model call: new runs and chat turns at once, a run in flight at its next backend tool call or, for a run that uses only runtime-side tools, at its next model call (the runtime re-reads `budget_status` at most once a minute); per-run caps on web search, browser, code interpreter, MCP and `http_fetch` calls (60, 120, 30, 200, 120), which budgets do not meter; search and product-question routes throttled at 10 requests/s; an optional AWS Budget; alarms on failed runs, API errors and open AgentCore sessions | `backend/src/insights_radar/core/tokens.py`, `modules/service.py`, `chat/budget.py`, `agents/src/insights_radar_agents/module_runtime.py`, `agents/src/insights_radar_agents/chat_runtime.py`, `backend/src/insights_radar/api/app.py` |
| T11 | WAF rate limit (2,000 requests per IP), stage throttling (50 requests/s, burst 100); module tool calls and chat usage do not use the HTTP API at all but their own IAM-authenticated function URLs, which Lambda refuses to callers from other accounts before running anything, so `/api/*` floods cannot starve module runs; 6 MB request body cap; dead-letter queues on asynchronous paths; finalize retried on every error so runs are always recorded | `infra/insights_radar_infra/edge.py`, `backend.py`, `backend/src/insights_radar/api/http.py` |
| T12 | `HtmlSanitizer` allows only `p, br, strong, em, a, code` and `href` with `http`, `https`, `mailto`, relative or fragment targets, after canonicalising the URL as a browser would; the web app renders sanitised HTML as React nodes without `dangerouslySetInnerHTML` and re-checks every link; the Markdown renderer never interprets raw HTML and does not load images; CSP blocks inline scripts | `backend/src/insights_radar/core/html.py`, `frontend/src/components/SafeHtml.tsx`, `frontend/src/components/Markdown.tsx`, `frontend/src/lib/html.ts` |
| T13 | CSV export prefixes cells starting with `=`, `+`, `-`, `@`, tab or carriage return with an apostrophe, and import reverses it | `backend/src/insights_radar/radars/codecs.py` |
| T14 | Every direct dependency pinned exactly, with lock files (`uv.lock`, `package-lock.json`); a 7-day `exclude-newer` cooldown on every uv project; binary-only wheels for the deployed bundles; `npm ci`; the AWS CDK CLI installed from `infra/package-lock.json` with `--ignore-scripts`; no Docker build. On every push and pull request CI runs `pip-audit` over every `uv.lock` (resolved versions), `npm audit` on the web app, gitleaks on the new commits, bandit, checkov and shellcheck; ASH (with Grype) runs before each release | `*/pyproject.toml`, `deployment/build.py`, `frontend/package-lock.json`, `infra/package-lock.json`, `.github/workflows/ci.yml` |
| T15 | Presigned POST with a fixed key, a fixed content type and a 1-byte to 50 MB `content-length-range`; allowed extensions only; the upload is confirmed by checking the object's size and type; S3 Block Public Access and TLS-only bucket policies | `backend/src/insights_radar/product/service.py`, `product/model.py`, `infra/insights_radar_infra/data.py` |
| T16 | API access logs omit headers and query strings; WAF logs redact the `authorization`, `cookie` and `x-mr-run-token` headers; Step Functions logs exclude execution data (the run input carries the run token); the run token never enters the model context. The Step Functions execution history still holds the run input, token included, for 90 days to anyone allowed `states:GetExecutionHistory`; the token is useless without the module runtime role and expires within 8 hours | `infra/insights_radar_infra/backend.py`, `edge.py` |
| T17 | Audit row for every mutation (actor, action, target, time), kept 400 days; agent writes carry module and run provenance; KMS MAC operations are AWS CloudTrail management events (90 days in Event history unless the account has a trail). The audit rows live in the ops table, which the API functions can write, and the stack creates no CloudTrail trail or S3 and DynamoDB data events: see residual risks | `backend/src/insights_radar/audit/log.py` |
| T18 | Chat calls the API with the user's own token, so it reads exactly what the user can; it has no write tools; restored history drops tool blocks; tool output is fenced | `agents/src/insights_radar_agents/chat_runtime.py` |
| T19 | Only admins can attach MCP servers or change prompts and grants; every change is audited; URLs pass the SSRF guard; MCP tools are prefixed `mcp_<server>_` so they cannot shadow built-in or trusted tools; MCP output is fenced | `backend/src/insights_radar/modules/model.py`, `agents/src/insights_radar_agents/connectors.py` |
| T19b | Skills are administrator text read into a run: only administrators edit the library or attach skills (`module.manage`); the prompt carries each attached skill's name and description defanged like the instructions, the body is returned by `skill_load` only for a skill attached to the run's module, defanged so it cannot open or close a fence, and passed unfenced as trusted administrator text (`agents/src/insights_radar_agents/guard.py` `TRUSTED_TOOLS`); the shared untrusted-content rules come after the template and cannot be edited; every save and delete is audited with a hash | `backend/src/insights_radar/modules/skills.py`, `modules/prompts.py`, `api/agent_tools.py` |
| T20 | Tokens saved with 0600 permissions in a 0700 directory; a token file readable by others is made owner-only before use, one owned by another user is refused; the sign-in domain must be the stack's own Cognito domain form (or the one `AIR_COGNITO_DOMAIN` pins) and is printed before the browser opens; loopback redirect with `state` check; `logout` revokes the refresh token at Cognito; tool results fenced; every write tool is marked destructive: `give_feedback` (no confirmation step) and `run_module` (needs `confirm=true`, one run per module every 10 minutes), plus, only when `AIR_MCP_RADAR_WRITES=true`, `import_radar_entries` (a dry run without `confirm=true`), `tag_radar_version` and `restore_radar_version` (both need `confirm=true`) | `mcp/src/auth.ts`, `cli.ts`, `server.ts`, `radarTools.ts`, `fence.ts` |
| T21 | The same JWT authorizer protects the `execute-api` address and the stage is throttled; the agent routes are not on the HTTP API (T11). Keeping the address out of the stack outputs is not a control: it appears in CloudTrail, in the chat runtime's environment and to anyone who can read the stack. Residual risk: someone who learns the address can spend the `/api/*` stage throttle with unauthenticated requests (they get 401, but count), slowing real users and chat. A secret CloudFront origin header was considered and not added: HTTP APIs count a request against the stage throttle before any authorizer or Lambda check, so the header would not protect the throttle. HTTP APIs cannot take a WAF web ACL. Customers who need more can add a custom domain to the API, set `disableExecuteApiEndpoint`, and point CloudFront and the chat runtime at that domain | `infra/insights_radar_infra/backend.py`, `stack.py` |
| T22 | KMS customer managed key for DynamoDB, the assets bucket, S3 Vectors, the knowledge base data source, SQS, SNS, CloudWatch Logs and Lambda environment variables; TLS-only bucket and topic policies; HTTPS-only viewer and origin policies on CloudFront | `infra/insights_radar_infra/data.py`, `search.py`, `monitoring.py`, `frontend.py` |

Cross-cutting: one IAM role per function and runtime with resource-scoped grants (no
`AWSLambdaBasicExecutionRole` on solution functions); cdk-nag `AwsSolutionsChecks` run on every
synthesis and fail on any resource wildcard whose pattern is not listed in
`infra/insights_radar_infra/nag.py`. The one pattern that cannot be narrowed is `Resource::*`:
cdk-nag reports it without the action, so a statement such as `dynamodb:Scan` on `*` would pass.
The actions allowed to use `*` are listed in `nag.STAR_RESOURCE_ACTIONS` (X-Ray, CloudWatch Logs
delivery, `cloudwatch:PutMetricData`, and the two CloudFront invalidation actions CDK's own
BucketDeployment role asks for) and a template test fails on any other. Every rule that is not
IAM5 is suppressed on the single resource it is about, not on the stack, so a second
unauthenticated route (APIG4) or a new queue without a dead-letter queue (SQS3) is still a
finding.

## 4. Did we do a good job?

### Static analysis and scans (as of 20 Nov 2026, release candidate)

All scans ran on a snapshot of git-tracked files only (see `.ash.yaml`). `.github/workflows/ci.yml`
runs bandit, pip-audit, gitleaks and checkov on every push.

| Tool | Scope | Result |
|---|---|---|
| ASH (Automated Security Helper) | Whole repository, severity threshold MEDIUM | 0 actionable findings (bandit, cdk-nag, cfn-nag, checkov, detect-secrets, npm audit, semgrep, syft) |
| Grype | Resolved dependency trees (lock files) | 0 vulnerabilities |
| cdk-nag (AwsSolutions) | Both synthesised stacks | 0 non-compliant; every suppression names its wildcard and reason |
| Checkov, cfn_nag | Synthesised CloudFormation templates | 0 failed checks |
| Semgrep | Python, TypeScript | 0 findings after the suppressions listed in `.ash.yaml` |
| Secret scanners (detect-secrets, gitleaks) | Whole repository | 0 findings; test values are visibly fake |
| Holmes (CSR rubric and baseline) | Repository content | No rubric findings; Bandit low-severity notes only |
| Independent `/code-review` and `/security-review` passes | Every project | Findings fixed in review rounds |

Every suppression and its reason is listed in [security-matrix.md](security-matrix.md).

Examples of issues the reviews found and fixed: admin-attached MCP servers re-resolved DNS and
followed redirects after the initial check (now pinned); MCP tool names could collide with trusted
tool names (now prefixed); chat turns had no cap (now 12 model calls and the budgets); a draft read
used an S3 key from agent-writable attributes (now derived from the node id); agent tools trusted
the token's grant list after an admin revoked a grant (now checked against current grants); the
SSRF guard missed IPv4 inside NAT64, 6to4 and Teredo addresses and the shared address range (now
blocked); alarm notifications could not be delivered to the encrypted topic (key policy fixed).

### Tests (as of 20 Nov 2026)

| Project | Tests | Coverage |
|---|---|---|
| `backend` | 8,169 | 100% line and branch |
| `agents` | 347 | 100% line and branch |
| `mcp` | 296 | 100% lines |
| `infra` | 558 (templates, the deployment scripts against stand-in AWS commands, and the documents' suppression tables) | 100% line and branch |
| `frontend` | 1,254 | 100% lines |

Security-relevant behaviour (SSRF encodings and ranges, fence and marker neutralisation,
sanitiser bypasses, run-token forgery and expiry, grant revocation, role parsing, budget
thresholds, formula injection) has dedicated tests. The infrastructure tests assert properties of
the synthesised templates: only `GET /api/config` is public, the agent routes are IAM function URLs
that only their own runtime role may call, TOTP is required and no app client allows SRP, buckets
and tables are encrypted, no policy grants every action, the model scope is exactly the catalog,
the CSP forbids inline script and remote images, and every resource waits for the AgentCore Memory.
The script tests run `deploy.sh` and `destroy.sh` against stand-in `aws` and `cdk` commands: a
destroy must name its stack, an update keeps its options, and retained data cannot be switched off
without `--allow-data-loss`. The e2e suite (`e2e/`) runs against a live test deployment, including
the `/agent` boundary and a prompt-injection regression on a hostile fixture page.

### Residual risks

| Risk | Why it remains | What you can do |
|---|---|---|
| Direct `execute-api` endpoint outside the WAF | HTTP APIs cannot attach AWS WAF, and the chat runtime reads through it with the user's token | The JWT authorizer and the stage throttle still apply; module runs do not depend on it. To force all user traffic through CloudFront, add a custom domain and restrict the default endpoint, or front the API with a REST API and WAF |
| Access lasts briefly after a user is disabled or demoted | The API re-checks each user's status and groups with Cognito at most every 30 seconds (`identity/users.py` `LiveRoles`), and chat and the MCP server go through the API; while Cognito throttles, the last answer is kept for up to 5 more minutes, as viewer only, and a disabled answer is never relaxed. Signing a user out revokes refresh tokens only: their ID token stays valid for up to an hour, so a stolen token works until it expires unless the account is disabled | Act quickly on a lost device: disable the user and remove them from the admin group; expect up to 30 seconds of residual access (about 5.5 minutes, read-only, while Cognito throttles) |
| Audit rows in an application table | The audit log is TTL rows in the ops table, which the API functions can write; the stack creates no CloudTrail trail or data events | Use an organisation CloudTrail trail with S3 and DynamoDB data events for the assets bucket and both tables if you need tamper-evident records of direct data access |
| Content agents wrote reaches users' assistants | MCP results carry text copied from web pages into IDE and desktop assistants that may hold shell and file tools; the fence is an instruction the client may ignore | Use MCP clients that ask before running tools, and do not auto-approve tools in sessions with this server (see `mcp/README.md`) |
| Per-user chat history is isolated in code, not by IAM | The chat runtime reads and writes AgentCore Memory with `actor_id = <the signed-in user's sub>`, but its role holds `ListActors`, `ListSessions`, `ListEvents`, `GetEvent`, `CreateEvent` and `DeleteEvent` on the whole memory (`DeleteEvent` so a guardrail-redacted turn can replace its stored message): AgentCore Memory has no condition key for the actor, so IAM cannot narrow it. A change that took the actor from the request payload rather than the verified token would expose other users' conversations, and could delete them | Review changes to `agents/src/insights_radar_agents/chat_app.py` where `actor_id` is set (a named test covers it), and keep the memory retention short (`AIR_CHAT_MEMORY_DAYS`) |
| Workload identities of other agents in the account | The CDK L2 grants each runtime role workload access tokens for any workload identity in the account's default directory; this sample uses none | Deploy into an account that runs no other AgentCore agents with OAuth credential providers, or add a deny for other workload identities to the runtime roles |
| Indirect prompt injection | No fence or filter reliably stops a model from following injected text; Guardrails do not inspect tool results | Grant modules only the tools they need (the validator already refuses `product_knowledge` together with any outbound grant); review insights before acting, and read the run summary, which is where a run reports an injection attempt |
| Exfiltration through a module that holds an outbound grant | A module with `web_fetch`, `web_search`, `browser`, `code_interpreter` or `mcp` cannot read uploaded documents or `private_writer` nodes (T8), but its system prompt still carries the module's instructions and, when a product is attached, the product profile; injected text can try to carry those out in a search query or an address | Keep product profiles free of confidential detail; give outbound grants only to modules that need them. The per-run caps, the address budgets (2,048 characters total, 512 query, 512 path, 100 host) and the public-terms prompt rule slow a leak down; they do not prevent one |
| Email addresses in stack outputs | `FirstAdmin` and `DeployOptions.alarmEmail` hold the addresses the operator gave, so any principal with `cloudformation:DescribeStacks` can read them, and `deploy.sh` copies them into `build/outputs.json` | They are outputs because `deploy.sh` reads them back on every update to keep the first administrator and the alarm address. Use a role or group address rather than a person's, and treat `build/` as containing deployment metadata |
| CSP `connect-src` allows whole regional AWS hosts | The web app talks to `bedrock-agentcore.<region>.amazonaws.com` and `cognito-idp.<region>.amazonaws.com`, which are shared hosts: script injected into the page could post to an attacker's own AgentCore runtime on the same host (with the attacker's own JWT authorizer), so the CSP does not contain exfiltration if the output sanitiser (T12) ever regresses | The sanitiser and the React rendering are the control, and both are covered by tests. To contain it, proxy chat through `/api` so `connect-src` can be `'self'` only; that needs a backend streaming route |
| Anyone who receives a forwarded alarm email can unsubscribe | Amazon SNS email subscriptions carry an unauthenticated one-click unsubscribe link. One click by a recipient of a forwarded alarm removes the only subscriber, and nothing alarms on a topic with no confirmed subscriber (`deploy.sh` checks only at deploy time) | Subscribe a distribution list you control rather than forwarding alarms, or resubscribe with `AuthenticateOnUnsubscribe=true` (`aws sns subscribe ... --attributes AuthenticateOnUnsubscribe=true`) so unsubscribing needs AWS credentials. Check `aws sns list-subscriptions-by-topic` after any alarm silence |
| The site bucket keeps every build | `BucketDeployment` runs with `prune=false` so a browser holding the previous `index.html` can still load its hashed scripts, and `publish-site.sh` does not delete either: old JavaScript stays fetchable by URL for good. A lifecycle expiry is not safe here, because the deployment's sync does not re-write unchanged objects, so an expiry would delete the live build's assets of a deployment that has not changed in a while | Remove old builds by hand after an upgrade has settled: list the bucket (`aws s3 ls s3://<SiteBucket>/assets/`), keep the hashed files `build/site/index.html` references, and delete the rest. The bucket is versioned, so a wrong delete is recoverable |
| Accuracy of model output | Models can be wrong, out of date or misread sources | Treat output as a draft; check cited evidence; use feedback to flag bad insights |
| Licensing and copyright of crawled content | The sample stores excerpts of third-party pages and derived summaries | Point it only at sources whose terms allow this use; respect `robots.txt` (the crawler does) and site terms; review drafts before publishing |
| MCP servers attached by admins | Their responses reach the model and their requests leave your account | Attach only servers you operate or trust, over https; review them like any third-party integration |
| Account and admin compromise | An AWS operator or an app admin can read all data and change prompts | Use least-privilege IAM, MFA and CloudTrail for operators; keep the admin group small; review the audit log |
| Cross-Region model processing | Every model in the catalog is a `global.` inference profile, which can process requests in other Regions, and no deployment parameter changes that | If data must stay in one geography, change `backend/src/insights_radar/modules/catalog.py` to Regional or geographic inference profiles before you deploy |
| Skills are trusted text | A skill's body reaches the run unfenced, as instructions, so a hostile or careless skill steers every module it is attached to (T19b) | Keep the admin group small; review a skill before attaching it, and read the `skill.save` and `skill.delete` audit rows |
