---
title: "Touch Grass: Building Agents on Amazon Bedrock AgentCore with Real Data"
weight: 0
---

Design, build, and deploy a real AI agent on Amazon Bedrock AgentCore — one that
investigates wildlife anomalies in synthetic field data and explains what happened
and why.

> Duration: 90 minutes
> Audience: CXOs, Technical Leads, Senior Engineers
> Format: Instructor-led, AWS Workshop Studio
> Regions: us-east-1 or us-west-2

:::alert{type="warning"}
**Cost and where this runs.** This workshop is built for an **AWS-run event**,
where a temporary AWS account and all its infrastructure are provisioned for you
and reclaimed afterwards — at an event you are not billed and have nothing to clean
up. You can also
[deploy it into your own AWS account](110-own-account/index.md), in which case the
resources are billable and yours to remove.

In your own account it deploys an EC2 Code Editor and a CloudFront distribution,
an AgentCore Gateway, Lambda, S3, Cognito and an AgentCore Runtime, plus per-token
charges for every Bedrock call. Budget a few US dollars for a 90-minute run, and
delete the stacks afterwards (see [Cleanup](090-cleanup/index.md)). Pricing: :link[Amazon Bedrock]{href="https://aws.amazon.com/bedrock/pricing/" external=true},
:link[Amazon Bedrock AgentCore]{href="https://aws.amazon.com/bedrock/agentcore/pricing/" external=true},
:link[Amazon EC2]{href="https://aws.amazon.com/ec2/pricing/" external=true}.
:::

## What you will do

The scenario: a conservation NGO, the AnyCompany Conservation Trust, runs 6 camera-trap stations in
Desaru, Johor, Malaysia, tracking 12 species. Something is wrong at some of the
stations. An agent's job is to find out what, and why — investigating the anomaly
against field data and explaining the probable cause.

**In Part 0, you set up your workshop environment.** You open a browser Code Editor
that is already running in your AWS account, with Claude Code wired to Claude on
Amazon Bedrock — or sign in with your own Kiro or Claude Code instead. The result is
a working AI coding agent, with nothing installed on your laptop.

**In Part 1, you deploy a pre-built agent.** All the AgentCore wiring — the tools,
the Gateway, the deployment config — is already done for you. You open the baseline
agent already in your Code Editor workspace and run it there, deploy it to a managed
AgentCore Runtime, invoke it against a live anomaly, and inspect its cost and
behaviour in AgentCore Observability. The
goal is to see what "done" looks like and build a mental model of the target.

**In Part 2, you design and deploy your own agent.** Working in teams, you fill in
the GenAI Product Canvas to design your own version, translate that design into a
system prompt, validate it locally, and deploy it to AgentCore Runtime — then read
its telemetry and compare it against the decisions you made on the canvas.

This workshop is about the **design process** of an agent, not infrastructure
decisions. The tool backend and Gateway are already provisioned in your account,
so you spend your time deciding what the agent should do and how it should behave —
not making specific infrastructure decisions.

## The three parts

| Part | You will... | Outcome |
|------|-------------|---------|
| Part 0: Your environment | Open a browser Code Editor that is already running in your AWS account, with Claude Code wired to Claude on Amazon Bedrock - or sign in with your own Kiro or Claude Code instead. | A working AI coding agent, with nothing installed on your laptop. |
| Part 1: See it work | Learn the pipeline-vs-loop distinction and the AgentCore building blocks, then watch a finished agent investigate a live anomaly. | Shared vocabulary and a mental model of the target. |
| Part 2: Build it | Fill your canvas, translate the design into a system prompt, run the agent locally against remote Gateway tools, deploy it to AgentCore with a skill, and read its telemetry. | A working, deployed agent you designed. |

## Pages

1. [Prerequisites](010-prerequisites/index.md) — account, tooling, and the datasets
2. [Architecture and security controls](015-architecture/index.md) — what is deployed, and how it is contained
3. [Part 0: Your workshop environment](020-part-0-environment/index.md)
   - [Open the Code Editor](020-part-0-environment/code-editor.md)
   - [Path A: Claude Code on Amazon Bedrock](020-part-0-environment/claude-code-bedrock.md)
   - [Path B: bring your own Kiro or Claude Code](020-part-0-environment/bring-your-own.md)
4. **Part 1: The building blocks**
   1. [Frame: pipeline vs loop, and the AgentCore building blocks](030-part-1-building-blocks/031-frame.md)
   2. [See a working agent](030-part-1-building-blocks/032-see-working-agent.md)
   3. [Observability: see the price mechanisms](030-part-1-building-blocks/035-observability.md)
5. **Part 2: Design and build your own agent**
   1. [Design your canvas](040-part-2-design/041-design-canvas.md)
   2. [Build: translate your canvas into an agent](040-part-2-design/050-build.md)
   3. [Validate](040-part-2-design/080-validate.md)
6. [Cleanup](090-cleanup/index.md)
7. [Extensions](100-extensions/index.md)
8. [Running this in your own AWS account](110-own-account/index.md)
