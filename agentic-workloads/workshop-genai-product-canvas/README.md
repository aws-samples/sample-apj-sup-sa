# GenAI Product Canvas workshop — biodiversity anomaly agent

The code behind a 90-minute workshop in which a team turns a **GenAI Product
Canvas** into a working agent on **Amazon Bedrock AgentCore**: designed on paper,
run locally, pointed at remote tools through an **AgentCore Gateway**, then promoted
to **AgentCore Runtime** unchanged, with OpenTelemetry traces and token cost visible
at the end.

The scenario is a wildlife-monitoring one. A conservation NGO runs six camera-trap
stations in Johor, Malaysia; detections of some species have dropped, and the agent's
job is to work out what happened and why — correlating detection counts against
species baselines, weather, land-use change and local news, then writing a
structured report.

> **All data here is synthetic.** The species and the geography are real; every
> detection, weather reading, land-use change and news article is invented for
> teaching, and the NGO, the publications and the businesses named in the data are
> fictional. Nothing in this sample contains personal or customer data.

## Who it is for

Two audiences, in the same room:

- **Product managers**, who fill in the canvas — the decisions about users,
  latency, evaluation and cost that separate a shippable feature from a demo.
- **Builders**, who translate those decisions into a system prompt, a tool
  contract, a deployment target and an observability story.

It assumes you can read Python and use a terminal. It does not assume you have
built an agent before. A companion, code-free facilitator kit for running the
canvas session on its own is published separately as `genai-product-canvas`.

## What is in here

| Path | What it is |
|------|-----------|
| `agent/` | The agent participants build on: `agent.py` (one file, three modes — local tools, remote tools, AgentCore Runtime entrypoint), `tools_local.py`, `gateway_client.py`, `system_prompt.txt`, `tool_definition.json`, `data/` |
| `agent/agentcore/` | AgentCore CLI project — `agentcore.json` plus the CDK app the CLI generates and deploys |
| `infra/template.yaml` | The tool backend: three S3 buckets, the tools Lambda, the AgentCore Gateway with a Cognito machine-to-machine authorizer, and a provisioner that seeds every dataset and the agent source from blobs embedded in the template itself |
| `infra/code-editor.yaml` | Part 0: a browser Code Editor on EC2 behind CloudFront, with Claude Code wired to Amazon Bedrock through the instance role |
| `infra/lambda/tools/` | The six tools as the Gateway sees them |
| `infra/gateway_provisioner/` | The custom resource that creates the Cognito pool, the Gateway and its target — and tears them down again |
| `infra/scheduled/` | The optional async-batch path: EventBridge Scheduler plus an invoker Lambda that scans every station nightly |
| `infra/bundle_assets.py` | Embeds the datasets, Lambda handlers and agent workspace into `template.yaml` as gzip+base64 blobs. Run it after editing anything under `agent/`, `canvas/`, `skills/` or `infra/lambda/` |
| `canvas/` | The blank canvas, a filled-in reference answer, and the canvas → configuration mapping |
| `skills/deploy-to-agentcore/` | The skill that drives `agentcore deploy` |
| `observability/` | Turning on traces and reading token cost |
| `docs/` | Instructor guide, architecture, cost estimation, troubleshooting |

## Running it

### As a Workshop Studio workshop (how it is designed to run)

Both templates deploy into each participant account before the event starts, and
Workshop Studio stages them to S3 itself. The participant-facing pages live in
Workshop Studio rather than in this folder;
[`infra/workshop-studio/SETUP.md`](infra/workshop-studio/SETUP.md) is the
facilitator's guide to wiring it up, including the `contentspec.yaml` and the
three least-privilege participant policies.

There is nothing to pre-upload and no repository for the workshop to clone: the
datasets, both Lambda handlers and the whole agent workspace travel inside
`template.yaml` and are written to S3 by a custom resource when the stack deploys.

### In your own account

```bash
export AWS_REGION=us-east-1

# Tool backend, Gateway and datasets. Stages the template through S3, because
# embedding the assets puts it past CloudFormation's 51,200-byte inline limit.
bash infra/deploy_tools.sh

# Fetch the endpoints and the Cognito secret the Gateway expects
aws ssm get-parameter --name /touch-grass/gateway-env --with-decryption \
  --query Parameter.Value --output text > gateway.env
source gateway.env

# Local tools
python agent/agent.py tapir

# The same agent, same prompt, tools now called through the Gateway
TOOL_MODE=gateway python agent/agent.py tapir
```

Then follow [`skills/deploy-to-agentcore/SKILL.md`](skills/deploy-to-agentcore/SKILL.md)
to put the same file on AgentCore Runtime.

## Cost and cleanup

[`docs/COST_ESTIMATION.md`](docs/COST_ESTIMATION.md) has the numbers, tied to the
canvas decisions that drive them. Delete the stacks when you are done —
`aws cloudformation delete-stack` — and the provisioner empties all three buckets
and removes the Gateway, the Cognito pool and its IAM role on the way out. If you
ran `infra/deploy_tools.sh`, also delete the template staging bucket it created,
which is deliberately not part of the stack.

## Security notes

- No long-lived credentials anywhere: the Code Editor uses an EC2 instance role and
  the deployed agent an execution role.
- The Gateway is not an open endpoint — it authenticates with a Cognito
  client-credentials JWT, and the client secret is written only to an SSM
  SecureString.
- The tools Lambda is read-only on the data bucket and write-only on the audit
  bucket, so a tool cannot rewrite the data it reads, and only an AgentCore Gateway
  in the same account may invoke it.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is explicit about where this
  deliberately departs from production practice, and what you would do instead.
