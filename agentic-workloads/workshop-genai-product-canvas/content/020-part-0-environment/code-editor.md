---
title: "Open the Code Editor"
weight: 21
---

Your AWS account comes with VS Code already running on an EC2 instance and
published through CloudFront. You open it with a link - no SSH, no key pair, no
local install.

## Step 1 - Join the event and open your account

1. Open the join link your facilitator gave you, for example
   `https://catalog.us-east-1.prod.workshops.aws/join?access-code=xxxx-xxxxxx-xx`
2. Choose **Email one-time password (OTP)**, enter your email, choose **Send passcode**
3. Enter the code from your inbox and choose **Sign in**
4. Tick **I agree with the Terms and Conditions**, then **Join event**

:::alert{type="warning"}
This AWS account is temporary. It is reclaimed when the event ends, and everything
in it - including anything you build - goes with it. Copy out anything you want to
keep before you leave.
:::

## Step 2 - Open the Code Editor

On the event page, in the left sidebar, open the panel that lists your
CloudFormation outputs and find **`CodeEditorUrl`**.

Click it. VS Code opens in a new browser tab. The connection token is already in
the link, so you should not be asked for anything.

:::alert{type="info"}
Can't find the output? Open **AWS console -> CloudFormation -> Stacks**, select the
Code Editor stack, and read the **Outputs** tab. `CodeEditorUrl` is the first row.
:::

## Step 3 - Look around

Two things to open straight away.

**A terminal.** Use the hamburger menu at the top left, then
**Terminal -> New Terminal**. You are the `participant` user, and the workspace
root is `/workshop`.

**`ENVIRONMENT.md`.** The boot script writes a summary of what it installed and
what it found. Open it from the file tree, or print it:

```bash
cat /workshop/ENVIRONMENT.md
```

It tells you which Claude model this environment settled on, which matters in the
next section.

## Step 4 - Verify the environment

Run these four. All four should answer without error.

```bash
aws sts get-caller-identity     # you are in the workshop account
claude --version                # Claude Code is installed
python3.11 --version            # 3.11.x
node --version                  # v20 or newer
```

Then the one that matters most - it prints how Claude Code is wired up:

```bash
bedrock-status
```

Expected output (the region is your event's region, so it may read `us-west-2`):

```
region: us-east-1
CLAUDE_CODE_USE_BEDROCK: 1
primary model (ANTHROPIC_MODEL): us.anthropic.claude-sonnet-4-6
opus alias:   built-in default
sonnet alias: us.anthropic.claude-sonnet-4-6
haiku alias:  us.anthropic.claude-haiku-4-5-20251001-v1:0
```

Claude Sonnet 4.6 is the model this environment runs on, with Haiku 4.5 for
background work. `opus alias: built-in default` is expected - Opus is not available
in a temporary workshop account, and
[Path A](claude-code-bedrock.md#why-not-opus) explains why.

## What is on the instance

| Tool | Where | Notes |
|------|-------|-------|
| Claude Code | `claude` | Pre-wired to Amazon Bedrock in this account. See [Path A](claude-code-bedrock.md). |
| Kiro CLI | `kiro-cli` | Installed but not signed in. See [Path B](bring-your-own.md). |
| Python 3.11 + venv | `/workshop/.venv` | Activated automatically in new terminals. |
| Node.js 20+ and npm | `node`, `npm` | Global installs go to `~/.npm-global`, which is on your `PATH` - no `sudo` needed. |
| AgentCore CLI | `agentcore` | Pre-installed. Used in Part 1 to deploy the agent. |
| AWS CLI v2 | `aws` | Uses the instance role - already authenticated. |
| `bedrock-status` | `/usr/local/bin` | Prints the Claude Code and Bedrock wiring. |

The instance role can invoke Bedrock, read the workshop's `gateway.env` from SSM,
and deploy an AgentCore Runtime. You do not need to configure credentials at any
point.

## Troubleshooting

**The URL returns 403, 502, or a blank page.**
Give it two or three minutes. CloudFront finishes before the instance does, so the
link can exist slightly before the editor is listening. Refresh.

**Still nothing after five minutes.**
Check the stack reached `CREATE_COMPLETE` in **CloudFormation -> Stacks**. If it
did, the boot script is the place to look - see the note for facilitators below.

**It asks for a password.**
The token in the link may have been trimmed when it was copied. Re-copy
`CodeEditorUrl` from the stack outputs in full. If you need the token itself, the
output `CodeEditorTokenSecret` names a Secrets Manager secret holding it: open
**Secrets Manager** in the console, find that secret, and choose
**Retrieve secret value**.

**A terminal command is not found.**
Open a *new* terminal. The environment is set up in a login profile, so a terminal
started mid-boot can miss it.

**`aws sts get-caller-identity` fails.**
That means the instance profile is not attached, which is a stack problem rather
than something you can fix from the terminal. Tell your facilitator.

:::alert{type="info"}
**For facilitators.** The whole bootstrap is logged on the instance at
`/var/log/user-data.log`, and the Bedrock model probe writes its errors to
`/var/log/bedrock-probe.log`. Reach the instance without SSH using Session
Manager: **EC2 -> Instances -> select `touch-grass-code-editor` -> Connect ->
Session Manager**. The instance id is also a stack output
(`CodeEditorInstanceId`).
:::

Next: [Path A - Claude Code on Amazon Bedrock](claude-code-bedrock.md).
