---
title: "See a Working Agent"
weight: 32
---

Before you design anything, run the finished product. In this section you open the
baseline agent already in your workspace, run it there, deploy it to a managed
AgentCore Runtime, and invoke it against a live anomaly. Seeing "done" first
grounds the canvas decisions you make in Part 2.

:::alert{type="info"}
The backend (S3 datasets, tools Lambda, and the AgentCore Gateway) is
already provisioned in your account by the workshop CloudFormation stack. You are
running and deploying the **agent**, not the tool infrastructure.
:::

## Step 1 - Open the baseline agent

The baseline agent, its tools and its deploy project are **already in your
workspace**. There is nothing to clone: the tools stack ships the source with it
and the Code Editor synced it in when your account was provisioned. Open a
terminal and go there:

```bash
cd /workshop/repo/agent
ls
```

:::alert{type="warning"}
If that directory is empty or missing, open
`/workshop/AGENT-SOURCE-MISSING.md` - it means the tools backend stack had not
finished when the editor started. Tell your facilitator; Part 0 is unaffected and
Claude Code still works.
:::

:::alert{type="info"}
Working on your own laptop instead of the Code Editor
([Path B](../020-part-0-environment/bring-your-own.md))? Copy the source down from
the workshop's data bucket:
`aws s3 sync s3://biodiversity-anomaly-data-$(aws sts get-caller-identity --query Account --output text)-$AWS_REGION/workspace/ ./repo/`
then `cd` into it. Everything below is identical.
:::

The pieces you will touch, relative to `/workshop/repo`:

| Path | What it is |
|------|-----------|
| `agent/agent.py` | The agent. Runs three ways: local tools, remote gateway tools, and on AgentCore Runtime - unchanged. |
| `agent/system_prompt.txt` | The reasoning rules (the investigation methodology). |
| `agent/tool_definition.json` | The tool contract exposed through the Gateway. |
| `agent/data/*.json` | The wildlife datasets the local tools read. |
| `agent/agentcore/` | The AgentCore CLI project used to deploy to the runtime. |
| `canvas/` | The GenAI Product Canvas: blank template, worked reference, and how to turn one into agent config. You use these in Part 2. |
| `skills/deploy-to-agentcore/` | The skill your coding agent uses to deploy in Part 2. |

## Step 2 - Test the agent locally

The Code Editor already created a Python 3.11 virtual environment at
`/workshop/.venv` and activates it in every new terminal, so you only need the
agent's dependencies. From `/workshop/repo/agent`:

```bash
python --version          # 3.11.x, from the pre-made venv
pip install -r requirements.txt
```

:::alert{type="info"}
If `python --version` shows something other than 3.11, the venv is not active.
Activate it with `source /workshop/.venv/bin/activate` and try again. The agent
needs Python 3.10 or newer.
:::

**Run against local tools first.** In `local` mode the tools read `data/*.json`
straight off disk - no Gateway needed. The model still calls Amazon Bedrock, which
the Code Editor's instance role already allows:

```bash
TOOL_MODE=local python agent.py tapir
```

You should see the agent investigate the Malayan Tapir anomaly and print a
structured report.

**Now run the same agent against the remote Gateway tools.** Fetch the
pre-provisioned connection details, which the tools stack wrote to SSM, then source
them so `TOOL_MODE=gateway`:

```bash
aws ssm get-parameter --name /touch-grass/gateway-env --with-decryption \
  --query Parameter.Value --output text > gateway.env
source gateway.env
python agent.py tapir
```

Same agent, same prompt - the tools are now remote (MCP over the AgentCore
Gateway). That portability is the whole point: the agent does not change when the
tools move.

## Step 3 - Deploy the agent to AgentCore Runtime

The AgentCore CLI is already installed in the Code Editor, and the baseline ships
a ready CLI project at `agentcore/` wired to `agent.py`, so there is no
`agentcore create` step. Confirm the CLI is there:

```bash
agentcore --version
```

:::alert{type="info"}
Nothing to install, but `npm install -g @aws/agentcore` also works here if you
want the latest - the Code Editor gives you a writable npm prefix at
`~/.npm-global`, already on your `PATH`. On your own machine
([Path B](../020-part-0-environment/bring-your-own.md)) run that install first.
:::

Fill the Gateway values from `gateway.env` into the runtime's `envVars` in
`agentcore/agentcore.json` (replace the `REPLACE_FROM_gateway.env` placeholders)
so the deployed runtime runs in gateway mode:

```json
"envVars": [
  { "name": "TOOL_MODE", "value": "gateway" },
  { "name": "AGENTCORE_GATEWAY_URL", "value": "<from gateway.env>" },
  { "name": "COGNITO_TOKEN_URL", "value": "<from gateway.env>" },
  { "name": "COGNITO_CLIENT_ID", "value": "<from gateway.env>" },
  { "name": "COGNITO_CLIENT_SECRET", "value": "<from gateway.env>" },
  { "name": "COGNITO_SCOPE", "value": "<from gateway.env>" }
]
```

Then deploy and wait for the runtime to report ready:

```bash
agentcore deploy -y        # packages code, deploys via CDK
agentcore status
```

`AWS_REGION` is already exported in the Code Editor, so the deploy lands in the
event's region.

:::alert{type="info"}
Deploying creates real AWS resources (a CloudFormation stack, IAM role, S3
staging, and the runtime). In Workshop Studio these are cleaned up when the event
ends. For the full walkthrough and the deploy skill, see
[Build: translate your canvas into an agent](../040-part-2-design/050-build.md).
:::

## Step 4 - Invoke the agent remotely

With the runtime deployed, invoke it against the tapir anomaly:

```bash
agentcore invoke "Malayan Tapir dropped to 0 at STN-03 in June 2026. Investigate the probable cause."
```

Watch for these behaviours and connect each to a design decision on the canvas:

| What you see the agent do | The design decision behind it |
|---------------------------|-------------------------------|
| Calls `get_species_baseline` first, learns the tapir is Endangered | Outcome plus severity logic |
| Calls `query_detections` for STN-03, confirms the drop is real and localized | Definition of done: confirm before concluding |
| Calls `get_weather_data`, sees no flood, rules weather out | "Consider >= 2 causes" reasoning rule |
| Calls `check_land_use`, finds illegal logging 0.8 km away | The methodology's anthropogenic step |
| Calls `search_news`, finds corroborating articles | Evidence threshold for "done" |
| Calls `generate_anomaly_report` and stops | The terminal output contract plus loop termination |

Notice it did not call all six tools in a fixed order. It chose a path. That is
the loop. If you drew a call graph beforehand, it would be a guess.

## What powers the demo

Everything you just ran:

- The reasoning rules live in `agent/system_prompt.txt`.
- The six tools live behind an AgentCore Gateway (remote MCP), backed by a
  Lambda reading the S3 datasets.
- The agent runs on AgentCore Runtime.
- The token usage and tool spans you will inspect next come from
  AgentCore Observability.

## Before you design

You have seen the target and run it end to end. Next, look at what that run cost
and how it behaved in Observability, then design the agent yourself on the canvas
and rebuild it from this baseline in Part 2.
