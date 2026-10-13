---
title: "Part 0: Your workshop environment"
weight: 20
---

Before you design or build anything, get a working environment. This part takes
about ten minutes and ends with an AI coding agent running in your browser,
talking to Claude on Amazon Bedrock, in the AWS account this event handed you.

Nothing to install on your laptop. Nothing to sign up for. No API key.

## What is already running in your account

When the event started, Workshop Studio deployed two CloudFormation stacks into
your temporary AWS account:

```
your Workshop Studio AWS account (us-east-1)
|
+-- Part 0 Code Editor  (stack: touch-grass Code Editor)
|     CloudFront  --HTTPS-->  EC2 (VS Code in the browser)
|                               |- Claude Code, pre-wired to Bedrock
|                               |- Kiro CLI, ready for your own sign-in
|                               |- Python 3.11 venv, Node 22, AWS CLI v2
|                               '- instance role: bedrock:InvokeModel
|
'-- Tools backend  (stack: Biodiversity Anomaly Tools Backend)
      S3 datasets + tools Lambda + AgentCore Gateway + Cognito
      (this is what your agent will call in Parts 1 and 2)
```

The Code Editor is where you spend the rest of the workshop. The tools backend is
what your agent talks to - you do not deploy or configure it.

## Two ways to run your coding agent

Pick one. Both end up in the same Code Editor, editing the same files.

| | Path A - use this account's Bedrock | Path B - bring your own |
|---|---|---|
| Tool | Claude Code, already installed and configured | Kiro CLI, Kiro IDE, or Claude Code on your own plan |
| Sign-in | None. The EC2 instance role is the credential. | Your Kiro / AWS Builder ID, or your Anthropic account |
| Models | Claude Sonnet 4.6 on Amazon Bedrock, in `us-east-1` | Whatever your plan includes |
| Who pays | This event's sandbox account | You or your employer |
| Best for | Getting started in under a minute | Teams standardised on Kiro, or anyone who wants to keep their own setup and history |

:::alert{type="info"}
Path A is the default and needs no decision - open the editor and type `claude`.
Read Path B only if you would rather use your own Kiro or Claude Code identity.
:::

## Why we set it up this way

Two things in this workshop are worth noticing, because they are the same two
things you will face when you take agents to production:

1. **Credentials belong to the workload, not the person.** Claude Code here
   authenticates with the EC2 instance role. No key is typed, stored, pasted into
   a config file, or shared. That is the same pattern your deployed AgentCore
   Runtime will use in Part 2.
2. **The model is a configuration value, not a rewrite.** Changing which Claude
   model runs is one environment variable or one `/model` command - and this
   environment picks its own at boot by testing what the account can invoke.
   Design for that and model choice stays a cost-and-quality dial instead of a
   migration.

## Pages in this part

1. [Open the Code Editor](code-editor.md) - get into your browser IDE and look around
2. [Path A: Claude Code on Amazon Bedrock](claude-code-bedrock.md) - the pre-wired setup, and how it works
3. [Path B: bring your own Kiro or Claude Code](bring-your-own.md) - use your own identity instead

When you can run a prompt and get an answer back, you are done with Part 0. Go to
[Part 1: The building blocks](../030-part-1-building-blocks/index.md).
