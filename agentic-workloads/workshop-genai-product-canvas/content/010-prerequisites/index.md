---
title: "Prerequisites"
weight: 10
---

## What you should already know

You do not need to have built an agent before - that is what the workshop is for.
It does assume:

| Assumed | Level | Where it shows up |
|---------|-------|-------------------|
| Reading Python | Able to follow a ~200-line script and change a string in it. You never write Python from scratch. | `agent.py` and `system_prompt.txt` in Parts 1 and 2 |
| A terminal | Running a given command, reading its output, `cd` and `ls`. | Every part |
| AWS basics | What an IAM role, an S3 bucket and a CloudFormation stack are. You deploy stacks but never author one. | Parts 1 and 2 |
| JSON | Editing a value inside a JSON file. | `agentcore.json` in Part 1 Step 3 |
| Large language models | The idea of a prompt and a token. No ML background needed. | Part 1 onward |

If you have used a coding assistant such as Claude Code, Kiro, Cursor or Copilot
before, Part 2 will feel familiar. If you have not, Part 0 gets you started and
the prompts are given to you in full.

## What you need

Just a browser and the join link from your facilitator.

Everything else - the AWS account, the code editor, the AI coding agent, the
language runtimes, and the tool backend - is provisioned for you when the event
starts. Part 0 walks you into it.

- An **AWS Workshop Studio account**, provided at the event, with Amazon Bedrock
  access to Claude in your event's region (`us-east-1` or `us-west-2`) and
  permissions for Lambda, S3, CloudFormation,
  IAM, Bedrock AgentCore, and CloudWatch.
- A **browser Code Editor** running in that account, already carrying Python 3.11,
  Node.js 20+, git, AWS CLI v2, and Claude Code wired to Amazon Bedrock. See
  [Part 0](../020-part-0-environment/index.md).

:::alert{type="info"}
**Nothing to install locally.** The Code Editor authenticates to AWS through its
EC2 instance role, so there are no credentials to copy and no tools to install. If
you would rather work on your own laptop with your own Kiro or Claude Code, that is
supported too - see
[Path B](../020-part-0-environment/bring-your-own.md), which lists what you then
need locally and how to get workshop credentials.
:::

## The tooling, and where it comes from

| Tool | Why the workshop needs it | Already on the Code Editor |
|------|---------------------------|----------------------------|
| Python 3.10+ and `pip` | The agent code is Python | Yes - 3.11, with a venv at `/workshop/.venv` |
| Node.js 20+ and `npm` | The AgentCore CLI is a Node tool | Yes |
| Git | Version-controlling your own changes. You do not clone anything: the agent source is already in `/workshop/repo/agent` | Yes |
| AWS CLI v2 | Fetching `gateway.env`, reading outputs | Yes, authenticated by the instance role |
| Claude Code | Turning your canvas into a system prompt | Yes, on Bedrock - [Part 0](../020-part-0-environment/claude-code-bedrock.md) shows which model your account gets |
| Kiro CLI | Same, if you prefer Kiro | Installed; sign in with your own identity |

The AgentCore CLI is **already installed** on the Code Editor, into a writable npm
prefix at `~/.npm-global` that is on your `PATH`. Part 1 Step 3 only asks you to
confirm it:

```bash
agentcore --version
```

You do not install CDK or run `cdk bootstrap` yourself: the CLI carries its own CDK
and bootstraps the account on the first `agentcore deploy`. If you are working on
your own laptop instead ([Path B](../020-part-0-environment/bring-your-own.md)),
that one install is the whole setup:

```bash
npm install -g @aws/agentcore     # needs Node.js 20+
agentcore --version               # verify
```

:::alert{type="warning"}
The older `bedrock-agentcore-starter-toolkit` pip package is deprecated. Do not use
it.
:::

Docker is only needed for Container builds. The default CodeZip build does not use
it, so you can ignore Docker entirely.

The baseline agent source brings the Python side (`strands-agents`,
`bedrock-agentcore`, `mcp`, `aws-opentelemetry-distro`) via its
`requirements.txt`, which you install in Part 1.

## The infrastructure is already provisioned

Two CloudFormation stacks are deployed into your account before you arrive:

| Stack | What it gives you |
|-------|-------------------|
| **Part 0 Code Editor** | VS Code in your browser on EC2 behind CloudFront, with Claude Code on Bedrock, the Kiro CLI, and the language runtimes |
| **Biodiversity Anomaly Tools Backend** | The S3 datasets, the tools Lambda, the AgentCore Gateway, and the Cognito auth that protects it |

You pull the gateway connection details (`gateway.env`) with a single command in
Part 1. You do not run any infrastructure scripts. You only design the agent, then
use the skill to deploy it onto this existing infrastructure.

## The datasets 

All wildlife data is synthetic, generated for this workshop. It models real
species and real places around Desaru, but every detection, weather reading,
land-use change, and news article is fabricated for teaching.

| File | Contents |
|------|----------|
| `detections.json` | Monthly camera-trap counts, 12 species x 6 stations, Jan-Jul 2026 |
| `weather.json` | Monthly rainfall / temperature / humidity / flood events per station |
| `land_use_changes.json` | 5 habitat-change events (logging, construction, agriculture) |
| `news_articles.json` | 9 local news articles (some relevant, some noise) |
| `species_baselines.json` | IUCN status, normal ranges, threats, ecology for 12 species |

You do not need to understand the data in depth. The agent's job is to make sense
of it. But knowing it exists helps you design good tools around it.

Next: [Part 0 - Your workshop environment](../020-part-0-environment/index.md).


