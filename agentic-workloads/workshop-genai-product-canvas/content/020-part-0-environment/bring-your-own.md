---
title: "Path B: Bring your own Kiro or Claude Code"
weight: 23
---

Optional. Skip this page if [Path A](./claude-code-bedrock.md) is working for you.

Some teams have already standardised on Kiro, or want their own Claude Code
history, settings, and subscription. All four options below work in this workshop.
Only the coding agent changes - the AWS account, the datasets, the Gateway, and
everything you build in Parts 1 and 2 stay exactly the same.

## The one rule

Whatever you use for coding, **the agent you build must still run against this
workshop account.** In Parts 1 and 2 the code calls Bedrock, reads
`/touch-grass/gateway-env` from SSM, and deploys an AgentCore Runtime - all in the
temporary account this event gave you.

Inside the Code Editor that is already true: the instance role handles it. If you
move to your own laptop, get credentials from the event page
(**Get AWS CLI credentials**) and export them first:

```bash
export AWS_ACCESS_KEY_ID=ASIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
# Export BOTH, set to your event's region. The AWS CLI is happy with either, but
# commands further down (and the cleanup page) build bucket names out of
# $AWS_REGION - with only AWS_DEFAULT_REGION set, those names come out with an
# empty region on the end and the command fails against a bucket that cannot exist.
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity      # must show the workshop account
```

:::alert{type="warning"}
Those credentials expire during the event. When a command starts failing with an
expired-token error, copy a fresh set from the event page.
:::

## Option B1 - Kiro CLI, in the Code Editor

Already installed on the instance. It signs in with your own identity, so nothing
was pre-configured.

The instance has no browser, so use the device flow: it prints a code and a URL
that you open in the browser on your own machine.

```bash
kiro-cli login --use-device-flow
```

Choose your licence when prompted:

- `free` - Kiro / AWS Builder ID, or Google or GitHub sign-in
- `pro` - your organisation's IAM Identity Center. You will also need your
  Identity Center start URL and region:
  ```bash
  kiro-cli login --license pro \
    --identity-provider https://your-org.awsapps.com/start \
    --region us-east-1 \
    --use-device-flow
  ```

Then confirm and start:

```bash
kiro-cli whoami
cd /workshop
kiro-cli chat
```

Useful to know:

| Command | What it does |
|---------|--------------|
| `kiro-cli chat` | Interactive session in the current directory |
| `kiro-cli chat --no-interactive "..."` | One-shot answer to stdout, handy in scripts |
| `kiro-cli doctor` | Diagnoses install and config problems |
| `kiro-cli logout` | Clears your tokens from the instance |

:::alert{type="info"}
`kiro-cli` is a half-gigabyte download and it installs last during boot, so if the
command is not found in the first couple of minutes, wait and open a new terminal.
Confirm with `kiro-cli --version`. If it is still missing,
`grep -i kiro /var/log/user-data.log` shows what happened, and
[Path A](claude-code-bedrock.md) needs no install at all.
:::

## Option B2 - Kiro IDE, on your own laptop

If you would rather stay in the Kiro desktop IDE:

1. Install Kiro from [kiro.dev](https://kiro.dev/downloads/) - macOS, Windows, or
   Linux (Ubuntu 24+, Debian 13+, Fedora 40+, Arch, Mint 22+)
2. Sign in with your usual provider (Google, GitHub, AWS Builder ID, or your
   organisation identity)
3. Export the workshop AWS credentials into the terminal you run commands from -
   see [The one rule](#the-one-rule)
4. Copy the baseline agent source down to your laptop and work there. There is no
   repository to clone - it is seeded into your account's data bucket:

   ```bash
   AGENT_BUCKET="biodiversity-anomaly-data-$(aws sts get-caller-identity \
     --query Account --output text)-$AWS_REGION"
   aws s3 sync "s3://$AGENT_BUCKET/workspace/" ./repo/
   aws s3 sync "s3://$AGENT_BUCKET/data/"      ./repo/agent/data/
   ```

Everything in Parts 1 and 2 is plain `python`, `aws`, and `agentcore` commands, so
it does not matter which editor you type in.

:::alert{type="info"}
Also install **Python 3.10+**, **Node.js 20+**, **git**, and **AWS CLI v2**
locally. The Code Editor already has all four, which is the main reason to stay in
it.
:::

## Option B3 - Claude Code on your own Anthropic plan

If you have a Claude Pro, Max, Team, or Enterprise plan and want your own settings
and history, switch Claude Code off Bedrock in your terminal:

```bash
unset CLAUDE_CODE_USE_BEDROCK
unset ANTHROPIC_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL
unset ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL
claude
```

Then `/login` inside the session and follow the printed URL in your own browser.

To go back to the account-provided Bedrock setup, open a new terminal - the login
profile puts the variables back.

:::alert{type="info"}
This bills your plan, not the sandbox. It also means the model no longer runs in
`us-east-1` under this account's IAM - worth knowing if your reason for being here
is to evaluate the Bedrock path specifically.
:::

## Option B4 - Claude Code on your own Bedrock account

Evaluating Bedrock for your own organisation and want to see it under your own
account and quotas? Claude Code ships a wizard:

```bash
claude
/setup-bedrock
```

It walks through the credential source (an AWS profile, a Bedrock API key, access
keys, or credentials already in your environment), picks up the region, checks
which Claude models your account can invoke, and pins them. It writes to
`~/.claude/settings.json`, so it survives across sessions on this instance.

Your own account needs, once:

1. Anthropic models enabled - **Bedrock console -> Model catalog**, select the
   model, submit the use case form. Access is immediate. With AWS Organizations you
   can submit once from the management account and it extends to child accounts.
2. An IAM identity with the seven `bedrock:*` actions listed in
   [Path A](claude-code-bedrock.md), plus `aws-marketplace:ViewSubscriptions` and
   `aws-marketplace:Subscribe`.

:::alert{type="warning"}
Keep the two account roles straight. Your Bedrock account serves the *coding
assistant*; the workshop account still hosts the *agent you build* - datasets,
Gateway, and AgentCore Runtime. Do not point the Part 2 deploy at your own account:
the tools backend does not exist there.
:::

## Choosing

| You want | Use |
|----------|-----|
| The shortest path to working | [Path A](claude-code-bedrock.md) - already done |
| Kiro, no laptop setup | B1 - `kiro-cli login --use-device-flow` |
| Kiro's full IDE experience | B2 - Kiro on your laptop |
| Your own Claude history and settings | B3 - `/login` |
| To evaluate Bedrock under your own account | B4 - `/setup-bedrock` |

Whichever you pick, you end up in the same place: an agent that can read and write
files and run commands, in an environment that can reach Bedrock and this
account's tool backend.

Next: [Part 1: The building blocks](../030-part-1-building-blocks/index.md).
