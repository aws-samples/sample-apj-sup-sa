---
title: "Step 2: Deploy Agent Infrastructure"
weight: 20
---

## Learning Objectives

By the end of this step, you will:
- Deploy the AgentCore layer — MCP Gateway, Runtime, and Memory — as **one CloudFormation stack** you edit and redeploy
- Connect your agent to the Gateway and ship it to AgentCore Runtime **without the `agentcore` CLI**
- Understand why Gateway + Runtime is the production foundation for agentic systems

## How you build in this workshop

In Step 1 you built a basic agent that ran on your Code Editor and connected directly to the database. That was an exercise. Now you'll build the real agent: one deployed to :link[AgentCore Runtime]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html"} that discovers tools through an :link[MCP Gateway]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html"}.

Everything from here on is built by editing **one** CloudFormation template — `agentcore-topup-stack.yaml` — and redeploying it with `make`. Each step you **uncomment one clearly-marked section** (or flip one value) and run `make deploy`. No SDK scripts, no `agentcore` CLI.

::alert[**Why CloudFormation?** Production agent platforms are defined as code so they can be reviewed, versioned, and reproduced. By the end you'll have the entire agent layer — Gateway, Runtime, Memory, toolsets, policies, guardrail — described in one auditable template, deployed exactly the way the demo environment deploys it.]{type="info"}

The base infrastructure (Aurora, Cognito, Glue, the Bedrock Knowledge Base, and this Code Editor) was **already provisioned** for you by the event. The top-up stack reads those values automatically with `Fn::ImportValue` — you never copy an ARN by hand.

::alert[**SaaS pattern:** In a pool model, all tenants share the same Gateway and Runtime. You deploy the infrastructure once, then add toolsets incrementally. Each toolset becomes available to all tenants.]{type="info"}

## Lab Procedures

### Step 2.1: Open the top-up template

Open :code[/workshop/agentic-analytics/app/agentcore_strands/agentcore-topup-stack.yaml]{showCopyAction=true}. Read the banner comment at the top — it explains the uncomment-a-fence workflow you'll use in every step.

As shipped, the template's **baseline** already contains everything needed for a working (tool-less) agent:

| Baseline resource | Role |
|-------------------|------|
| **Gateway** + **Gateway Interceptor** | The MCP endpoint all toolsets register to, plus the Lambda that propagates the user's JWT to each tool (for tenant isolation later) |
| **AgentCore Memory** | Short-term conversation memory |
| **Container build chain** (ECR + CodeBuild + a build-trigger) | Builds your agent's Docker image and pushes it — this is what **replaces `agentcore configure` / `agentcore deploy`** |
| **Chart Code Interpreter** | Sandbox the agent uses to render chart images |
| **Runtime** + **Endpoint** | Hosts your agent, JWT-authorized |
| **Observability** | Log + trace delivery for the Gateway and Memory |

The toolsets, Cedar policies, and guardrail are present but **commented out** — you'll uncomment them one step at a time. Right now the Gateway has **no toolsets**: the agent can chat, but has no analytics tools yet.

Before deploying, understand how authentication flows through the architecture:

```
User (browser)
  → Cognito login (Authorization Code flow)
    → JWT with custom:role (role-based access control) + custom:account_id (tenant isolation)
      → React UI passes the JWT to AgentCore Runtime as a Bearer token
        → Runtime's JWT authorizer validates it, then the agent forwards it to the Gateway
          → Gateway validates the JWT against the allowed Cognito client
            → Gateway Interceptor propagates the JWT to Lambda targets
              → Lambda extracts JWT claims for RLS
```

The architecture uses a **single token flow** — the user's JWT from Cognito login is passed all the way through:

| Layer | What the JWT does |
|-------|------------------|
| **Runtime authorizer** | Validates the user's Cognito access token (`CustomJWTAuthorizer`) before the agent runs |
| **Gateway authorizer** | Validates the token was issued by the allowed Cognito client |
| **Cedar policies in AgentCore Policy** | Reads `custom:role` to control which tools each user can see and use |
| **Gateway Interceptor** | Propagates the JWT to Lambda targets |
| **Lambda (RLS)** | Reads `custom:account_id` and `custom:role` to filter data per tenant and role |

::alert[**Single token, multiple layers.** The same JWT carries both identity (role, tenant) and authorization (permission to call the Runtime and Gateway). No separate machine-to-machine credentials are needed — the user's token authenticates the call AND provides identity for every downstream security layer. In the template, this is the Runtime's `AuthorizerConfiguration: CustomJWTAuthorizer` and the matching block on the Gateway.]{type="info"}

### Step 2.2: Configure the Agent (TODOs 2.3.1–2.3.2)

Open :code[/workshop/agentic-analytics/app/agentcore_strands/unicorn_rental_agent.py]{showCopyAction=true}. This is the actual agent you'll use for the rest of the workshop. The container build chain in the template packages **this file** into the image it deploys.

::alert[**Observability is built in.** Open `requirements.txt` — notice `strands-agents[otel]` and `aws-opentelemetry-distro`. These enable automatic OpenTelemetry tracing when deployed to AgentCore Runtime. You don't write any instrumentation code — the Runtime wraps your agent with `opentelemetry-instrument` automatically. You'll explore the traces in Step 9.]{type="info"}

#### TODO 2.3.1: Load the SOP as the System Prompt

The agent's behavior is defined by a :link[Standard Operating Procedure (SOP)]{href="https://github.com/strands-agents/agent-sop" external=true} — a markdown file with RFC 2119 constraints (MUST, SHOULD, MAY) that guide the agent's decisions. Find `TODO 2.3.1` near the top of the file and replace the basic one-liner with the SOP loader:

::::expand{header="💡 Need help with TODO 2.3.1? Click to see the solution"}
:::code{language=python showCopyAction=true}
SYSTEM_PROMPT = load_system_prompt()
:::
::::

::alert[The SOP file (`unicorn_rental_analytics.sop.md`) is already in the folder. Take a moment to open it and skim the structure — query classification rules, tool priority, response formatting constraints, and the human-in-the-loop workflow for Custom SQL.]{type="info"}

#### TODO 2.3.2: Create the Agent

Wire the components together. Find `TODO 2.3.2` — replace `None` with an :link[Agent]{href="https://strandsagents.com/latest/user-guide/concepts/agents/agent-loop/" external=true} constructor. Pass it `system_prompt` (the SOP you loaded in TODO 2.3.1) and `agent_tools` — both assembled for you just above. `agent_tools` includes the `mcp_client`, which is what connects the agent to the Gateway's MCP tools; the Gateway has no toolsets yet (you'll add them in later steps), but the wiring is in place. Leave `hooks=[]` for now — you'll switch it on in the next TODO.

::::expand{header="💡 Need help with TODO 2.3.2? Click to see the solution"}
:::code{language=python showCopyAction=true}
request_agent = Agent(
    model=bedrock_model,
    system_prompt=system_prompt,
    tools=agent_tools,
    hooks=[],
    callback_handler=None,
    state={"actor_id": actor_id, "session_id": runtime_session_id},
)
:::
::::

### Step 2.3: Add the Runtime Entrypoint (TODO 2.4)

#### TODO 2.4: Add the @app.entrypoint Decorator

This tells AgentCore Runtime which function handles incoming requests. Find `TODO 2.4` in `unicorn_rental_agent.py`:

::::expand{header="💡 Need help with TODO 2.4? Click to see the solution"}
:::code{language=python showCopyAction=true}
@app.entrypoint
async def agent_invocation(payload, context):
:::
::::

### Step 2.4: Enable Memory in the Agent (TODO 2.8)

The **AgentCore Memory** resource is already in the template's baseline — but your agent isn't using it yet. The `MemoryHookProvider` class is already defined in `unicorn_rental_agent.py`: it loads recent conversation history when the agent starts and saves each new message. You just need to wire it into the Agent constructor.

Find `TODO 2.8` — it's a **one-line change** to the Agent you just wrote: change `hooks=[]` to `hooks=memory_hooks`.

::::expand{header="💡 Need help with TODO 2.8? Click to see the solution"}
In the `Agent(...)` constructor from TODO 2.3.2, change this one line:
:::code{language=python showCopyAction=true}
    hooks=memory_hooks,
:::
(replacing `hooks=[]`). `memory_hooks` is already built for you near the top of the file. That's the only change — leave everything else as is.
::::

::alert[**Why memory matters.** AgentCore Runtime is stateless — each invocation starts fresh. Without the memory hook, the agent can't recall what you asked a moment ago. The `MemoryHookProvider` loads the last few turns from the Memory resource on start (`on_agent_initialized`) and saves each new message (`on_message_added`), giving the agent continuity across turns. You'll see this work in the chat UI in Step 3 — ask a question, then a follow-up that refers back to it.]{type="info"}

::alert[AgentCore Memory also supports **long-term memory** (LTM) for extracting insights across sessions — user preferences, behavioral patterns. This workshop uses short-term memory only, but you can enable both on the same resource. See the :link[AgentCore Memory documentation]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html"}.]{type="info"}

Do not forget the save the changes on the file.

### Step 2.5: Deploy with `make deploy`

You've edited and saved the agent code; now deploy the whole AgentCore layer. From the agent folder:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy
```

This runs `aws cloudformation deploy` on `agentcore-topup-stack.yaml`. The **first** deploy does a lot: it creates the Gateway, Interceptor, Memory, Runtime, and observability, and the container build chain **builds and pushes your agent image automatically** (the build trigger runs during the deploy — no `agentcore` CLI). Expect this first run to take several minutes while the image builds.

::alert[**Model access is handled for you.** The template includes a small `ModelSubscription` resource that accepts the Bedrock model's access agreement at deploy time, so your Runtime can invoke the model immediately — no manual "request model access" step in the Bedrock console. The `AgentRuntime` depends on it, so the subscription is in place before the agent ever runs.]{type="info"}

When it finishes, check the outputs:

```bash
make outputs
```

You'll see the Gateway URL and the Runtime ARN/ID:

```
GatewayUrl          https://agenticanalyticsmcpgateway-...gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
AgentRuntimeArn     arn:aws:bedrock-agentcore:us-east-1:xxxxxxxxxxxx:runtime/agentic_analytics_agent-xxxxxxxxxx
```

### Step 2.6: Redeploying after a code change — `make build`

`make deploy` deploys **infrastructure** changes (uncommenting a section, flipping a value). On its own it does **not** rebuild the agent image, so a Python change in `unicorn_rental_agent.py` (or an SOP edit) needs an explicit rebuild:

```bash
make build
```

`make build` runs a small, readable bash script (`scripts/rebuild-agent.sh` — open it, there's no hidden magic): it re-zips the agent code, uploads it, and runs `make deploy` with a **fresh image tag**. CloudFormation does the rest — it rebuilds the image under that tag and rolls the Runtime to a new version, re-applying the auth, network, and environment settings straight from the template. Use `make deploy` when you change the **template**; use `make build` when you change the **agent code**.

::alert[**You won't fully test the agent here.** The Runtime is JWT-only — it's invoked by the chat UI with the signed-in user's token, not from the command line. You'll connect that UI in Step 3 and watch the agent (and its memory) work end to end.]{type="info"}

## Verification

- `make deploy` completes; the stack reaches `CREATE_COMPLETE` / `UPDATE_COMPLETE` (check `make status`)
- `make outputs` shows a `GatewayUrl` and an `AgentRuntimeArn`
- The CodeBuild project `agentic-analytics-agent-build` shows a succeeded build (first `make deploy`)
- TODOs 2.3.1, 2.3.2, 2.4, and 2.8 are completed in `unicorn_rental_agent.py`

## Troubleshooting

**`make deploy` fails with "No export named agentic-analytics-… found"**
- The base stack hasn't finished, or `EnvironmentName` doesn't match. The exports come from the base infrastructure; confirm it's deployed and that you didn't override `ENV_NAME`.

**First `make deploy` takes a long time / times out on the build**
- The first deploy builds the ARM container image (a few minutes). If the build itself fails, open the `agentic-analytics-agent-build` project in the CodeBuild console and read the phase logs.

**`make build` succeeds but the agent behaves like the old code**
- The Runtime rolls to a new version at the end of `make build`; confirm it reached `READY`. If you only ran `make deploy` after a code edit, run `make build` — `make deploy` doesn't rebuild the image.

## Summary

You deployed the production infrastructure as code: an MCP Gateway (empty, ready for toolsets), AgentCore Memory, and your agent on AgentCore Runtime — all from one CloudFormation template, with the container image built automatically and no `agentcore` CLI. In the next step you'll connect the chat UI and watch it work.

Next → [Step 3: Connect the Chat UI](../03-connect-ui/)

## Reference Materials

- :link[AgentCore Gateway]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html"}
- :link[AgentCore Runtime]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html"}
- :link[MCP Protocol]{href="https://modelcontextprotocol.io/docs/getting-started/intro" external=true}
- :link[Strands Agents — MCP Client]{href="https://strandsagents.com/latest/user-guide/concepts/tools/mcp-tools/" external=true}
