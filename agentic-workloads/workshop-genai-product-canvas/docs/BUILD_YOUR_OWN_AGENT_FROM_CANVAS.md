# Replicate this workflow: canvas -> local validation -> AgentCore Runtime

This guide lets any builder take a completed **GenAI Product Canvas** and, in their
own coding agent (Kiro, Claude Code, etc.), turn it into a working agent on top of
the Touch Grass baseline: build it, **validate the output locally**, then deploy it
to **Amazon Bedrock AgentCore Runtime** using their AWS Workshop Studio account.

## Prerequisites

Install and verify these before you start.

| Tool | Why | Install / check |
|------|-----|-----------------|
| **Python 3.10+** | Runs the agent and the Strands framework | `python3 --version` |
| **AWS CLI v2** | Credentials + calling AWS | `aws --version` |
| **AWS credentials for your Workshop Studio account** | Bedrock access + deploy | `aws sts get-caller-identity` returns the WS account |
| **Bedrock model access enabled** | The agent's reasoning model | The baseline model (BEDROCK_MODEL_ID in agent.py) is already enabled for this workshop account — reuse it rather than picking a new model, since availability varies by region |
| **git** | Clone the baseline repo | `git --version` |
| **Node.js 20+** | Runs the AgentCore CLI | `node --version` |
| **`@aws/agentcore` CLI** | Package + deploy to AgentCore Runtime | `npm install -g @aws/agentcore` (verify: `agentcore --version`) |

**CLI note:** `@aws/agentcore` is the AgentCore CLI this guide uses throughout. Your
agent code, the `bedrock-agentcore` runtime SDK, and `strands-agents` are independent
of the CLI. If you would rather not use the CLI at all, you can invoke a deployed
endpoint directly with boto3 (set a long client `read_timeout`) — see Phase 4.

## The prompt

Copy everything in the block below into your coding agent, with your completed GenAI
Product Canvas attached or pasted where indicated.

```
I'm building an AI agent on top of the "Touch Grass" biodiversity workshop baseline
(agent/agent.py, tools_local.py, gateway_client.py, system_prompt.txt,
tool_definition.json, data/*.json). I've attached my completed GenAI Product Canvas
(a photo or a filled-in copy of the canvas template).

Goal: turn my canvas into a working agent, let me validate it LOCALLY, then deploy it
to Amazon Bedrock AgentCore Runtime in my AWS Workshop Studio account.

Follow these phases and check with me between each:

PHASE 0 — Read first, don't guess
- My completed canvas is what I attached to this message (a photo, or a filled-in
  copy of the template). Read it as my answers. For structure and worked examples,
  read the ./canvas folder in the repo (the blank template, the reference answer,
  and the canvas-to-config mapping).
- Read the baseline agent.py, system_prompt.txt, and the data/*.json so you match the
  repo's patterns.
- For any canvas box I left blank, propose a sensible default and list them for my
  approval before writing code. Do not invent product scope.
- Some boxes inform scope, not prompt text. Pricing, Costs, and business-model boxes
  (and deployment-only decisions like "async batch, overnight") shape how the agent is
  built and run — do NOT encode them as rules inside the system prompt. The
  canvas-to-config mapping in ./canvas shows which boxes become prompt lines and which
  do not.

PHASE 1 — Translate canvas -> config (agent first, reuse existing resources)
- Create a NEW sibling agent (e.g. agent/<my>_agent.py) and a NEW system prompt file.
  Do NOT overwrite the baseline agent.py or system_prompt.txt.
- Default to the resources this repo and workshop account already provide; do not
  create new AWS resources unless my canvas clearly requires them. The agent itself
  (this phase) needs no new resources. Any optional UX/extra stack described below is
  built and deployed later (Phase 3), not silently as part of translation.
- Read tool_definition.json first: it is the canonical tool contract that the
  pre-provisioned AgentCore Gateway already exposes over the ./data sources (the six
  wildlife-investigation tools, with their schemas). Treat those tools as already
  available. The agent reaches them two ways, both already wired in the baseline:
  local tools in tools_local.py when TOOL_MODE=local (reads ./data directly), or the
  remote Gateway tools via gateway_client.py when TOOL_MODE=gateway. Only build a new
  tool on top of the ./data sources if my canvas needs a capability the existing
  contract does not cover — and if it does, explain the gap and the new tool contract
  before making it.
- Encode my Definition of Done as explicit, enforceable prompt rules: stop conditions,
  a hard cap on tool calls, and what to do if unresolved (write the artifact anyway /
  set an escalation flag). Include a human-gate section.
- Shape the final product to my canvas — specifically the Inputs, Outputs, Definition
  of Done, and UX sections. The baseline emits a single Markdown report, but that is
  just one shape; match whatever my canvas defines. For example:
    * Unstructured artifact (report, brief, summary): have the agent output it as its
      final text; if it should be a saved file, write it to agent/reports/ (add that
      dir to .gitignore) and strip any model preamble so only the artifact is saved
      (keep from the first "# " heading onward).
    * Structured artifact (JSON, table, graph/chart data): use a terminal tool with a
      strict schema so the output is machine-checkable and can feed a downstream UI.
    * Conversational / interactive UX (e.g. a chatbot, or a dashboard that renders a
      graph from the agent's output): keep the AgentCore agent as the reasoning core
      and add a thin presentation layer on top of the invoke response. Do NOT bake UI
      concerns into the system prompt; the agent returns data, the UX renders it.
- Match the Inputs section too: if my canvas takes inputs beyond a single prompt
  (uploads, a form, a data feed), define how they arrive in the payload the handler
  receives.
- If the canvas requires functions or UX beyond the AgentCore setup in this repo, add
  them as separate components around the agent — never overwrite the baseline. Assume
  I may be new to AWS: do NOT ask me to choose services. Build the default serverless,
  least-privilege stack below, tell me what you are creating and why in one short
  paragraph, then proceed. Only deviate if my canvas clearly cannot be met by this
  stack, and if so explain the change first.

  Default stack (serverless, least-privilege, easy to deploy):
    * Frontend / UX: a static single-page app (React + Vite) hosted on S3 and served
      through CloudFront. For a chatbot, poll the API for the agent response and
      render it client-side; for a graph/dashboard, render the structured output the
      agent returns.
    * API / integration layer: an API Gateway HTTP API in front of one Lambda that
      calls invoke_agent_runtime. The browser talks only to the API, never to AWS
      directly, so no credentials reach the client. Give the Lambda a long timeout and
      a boto3 read_timeout of ~600s for multi-tool runs (see Phase 4).
    * State / storage: DynamoDB for session or conversation state; S3 for generated
      artifacts (reports, images, graph data), since the runtime filesystem is
      ephemeral. Do not add a relational database unless the canvas truly needs one.
    * AI/ML: Bedrock for the model (already used by the agent). Add nothing else unless
      the canvas requires it.
    * Infrastructure as code: define these new resources with AWS SAM in a new sibling
      directory (e.g. infra/ux/), not by editing the baseline infra. SAM keeps a
      first-time deploy to a single `sam deploy --guided`.
    * IAM: scope each role to least privilege — the API Lambda needs only
      bedrock-agentcore invoke on my runtime plus its own DynamoDB/S3 access; nothing
      account-wide.
    * Monitoring: rely on CloudWatch plus the built-in AgentCore GenAI Observability
      (Phase 4); add X-Ray only if I ask to trace the multi-service UX.

CRITICAL entrypoint rule (this breaks the deploy if wrong):
- The file must call app.run() when executed with NO CLI args (that's how AgentCore
  Runtime starts the server). Running a full task at import/no-args will fail the
  runtime with "initialization time exceeded". Use the baseline pattern:
  args present -> run one task locally; no args -> app.run() (guarded by a try/except
  import of bedrock_agentcore so local runs don't need the runtime package).

PHASE 2 — Validate locally (no deploy yet)
- Create a venv, install agent/requirements.txt. Confirm imports work.
- Set up env vars: copy agent/.env.example to agent/.env and source it (or export at
  minimum TOOL_MODE=local and the baseline BEDROCK_MODEL_ID). The local run needs
  these set before it will work.
- Confirm AWS creds work: aws sts get-caller-identity. Confirm Bedrock model access is
  enabled for my model + region. If creds are missing, PAUSE and let me authenticate.
- Validate against local tools first: run the agent with TOOL_MODE=local against the
  local JSON data, passing a prompt arg. Show me the generated artifact (and the saved
  file, if my canvas produces one). Fix issues before moving on. Only validate the
  remote path (TOOL_MODE=gateway with the gateway.env values) if my canvas actually
  needs the remote Gateway tools.
- If my canvas defines an Evaluation approach, run my sample cases here and report the
  outputs against the expected outcomes so I can judge quality. Do NOT build a test
  framework or add automated tests unless I ask.

PHASE 3 — Deploy to AgentCore Runtime
- Tell me this creates real AWS resources (a runtime endpoint, an IAM execution role,
  and deploy artifacts) before running anything.
- Use the @aws/agentcore CLI (install with `npm install -g @aws/agentcore` if absent;
  verify `agentcore --version`).
- The CLI is project-based: it needs an agentcore project. Run `agentcore --help` and
  the relevant subcommand `--help` (create, add, deploy, invoke, status) and follow
  the CLI's own current syntax rather than assuming flags. In outline: create/
  initialise a project (framework Strands, model provider Bedrock), wire in my
  entrypoint agent, then `agentcore deploy`.
- Deploy with the SAME model the baseline already uses (BEDROCK_MODEL_ID in agent.py /
  .env.example) rather than picking a new one — model availability varies by region
  (many Bedrock models are not offered in-region in ap-southeast-1), and the baseline
  model is already enabled for this workshop account. Only change the model if I ask,
  and if so confirm the model is available in my region first.
- Set my runtime env on the project as the CLI directs. If my agent uses local tools,
  it needs TOOL_MODE=local and BEDROCK_MODEL_ID. If it uses remote Gateway tools, run
  infra/deploy_tools.sh then infra/create_gateway.py first and supply the gateway.env
  values.
- Wait for `agentcore status` to show the endpoint READY before invoking.

PHASE 4 — Invoke, observe, and verify
- Invoke the deployed agent with `agentcore invoke` (the CLI takes the prompt as a
  plain argument, e.g. `agentcore invoke --runtime <name> "..."`; check `agentcore
  invoke --help`). If a run calls several tools it can take minutes — this is expected.
- No-CLI fallback: invoke the endpoint directly with boto3
  (`bedrock-agentcore` client, `invoke_agent_runtime`, agentRuntimeArn=<arn>). Set a
  long client read_timeout (e.g. 600s) or it hits the 60s default and times out
  mid-run.
- Build in observability so I can see cost, latency, and every tool call:
    * AgentCore auto-instruments the runtime for traces/metrics at deploy time (OTEL),
      so no code change is needed for the deployed agent.
    * Traces only appear once CloudWatch Transaction Search is enabled for the
      account/region (one-time). If it is not on yet, enable it — the repo ships
      `observability/enable_observability.sh` for this; run it, or do the equivalent
      Transaction Search setup, before expecting traces.
    * After a few invokes, point me to CloudWatch -> GenAI Observability -> Bedrock
      AgentCore -> my runtime name. Show me where to read sessions (token usage +
      duration), traces/spans (each model turn and tool call in order, with per-tool
      latency), and metrics (session count, p50/p90 latency, tokens, error rate).
    * Tie it back to my canvas: compare real token cost against the Cost box, and real
      latency against the Success-metrics box.
- Verify the run against my Definition of Done: confirm the output matches my canvas's
  Outcome shape and that any stop/escalation rules fired as intended.
- Note that on the runtime the artifact is returned in the invoke response, not saved
  to disk (ephemeral filesystem). If my canvas needs durable output, write it to S3
  from the handler (or use the CLI's session/EFS/S3 storage mounts).
- Remind me how to tear down (see teardown note below).

Constraints throughout:
- Never overwrite the baseline files. Keep my agent as separate, clearly named files.
- Prefer least-privilege AWS actions; the local validation only needs Bedrock invoke
  (bedrock:InvokeModel).
- Explain any deviation from my canvas before making it.
```

## Teardown

- **CLI-independent (recommended, always works):** delete the runtime directly with
  the AWS API, by id:
  ```bash
  aws bedrock-agentcore-control list-agent-runtimes --region <region>
  aws bedrock-agentcore-control delete-agent-runtime \
    --agent-runtime-id <runtime-id> --region <region>
  ```
  This is destructive and removes the live endpoint — confirm before running. The
  auto-created IAM execution role and deploy resources are separate; in Workshop
  Studio accounts they are cleaned up automatically, otherwise remove them manually
  for a full teardown.

- **`@aws/agentcore` CLI:** teardown is project-based and tied to the agentcore
  project you deployed from. Run `agentcore --help` to find the current remove/destroy
  subcommand and its flags rather than assuming a command name. If you deployed from a
  different machine or no longer have the project, use the CLI-independent path above.
