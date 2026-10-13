---
title: "Path A: Claude Code on Amazon Bedrock"
weight: 22
---

Claude Code is already installed in your Code Editor and already pointed at Claude
on Amazon Bedrock in this account. There is nothing to sign in to.

## Start it

In a terminal in the Code Editor:

```bash
cd /workshop
claude
```

Ask it something that proves it reached Bedrock:

```
Which Claude model am I talking to, and which AWS region is serving it?
```

Then check the provider from inside the session:

```
/status
```

The provider line should read **Amazon Bedrock**. If it does, Part 0 is done for
you.

## Which model you are on, and why

**Claude Sonnet 4.6**, with **Claude Haiku 4.5** handling background work like
session titles. `/model sonnet` is your active model.

```
/model sonnet    # Claude Sonnet 4.6 - fast, 1M context, and what this account can run
```

:::alert{type="info"}
**`/model opus` is not available here, and that is expected.** Claude Opus needs a
higher account trust score than a temporary workshop account carries - the detail is
in [Why not Opus](#why-not-opus) below. You will see a startup warning saying
`Opus: Opus 5 not available — using Opus 4.6 for this session`. Ignore it; stay on
Sonnet. Everything in this workshop is designed for Sonnet 4.6.
:::

Worth carrying past this workshop: **model choice is a cost-and-quality dial, not
an architecture decision.** A heavier model earns its tokens on judgement work -
turning a product canvas into a system prompt, as you do in Part 2 - while a
lighter one is better value for mechanical edits, file rewrites and repeated runs.
In Part 1 you will see the token cost of those choices in AgentCore Observability,
which makes the tradeoff concrete rather than theoretical. Here that dial is set
once, to Sonnet 4.6, because that is what the account can run.

## How the wiring actually works

Worth understanding, because you will reuse it. Three pieces, no secrets:

**1. Claude Code uses the standard AWS SDK credential chain.** On an EC2 instance
that means the instance profile. No API key, no `aws configure`, nothing on disk to
leak or rotate.

**2. Environment variables select Bedrock and pin the models.** The boot script
wrote these to `/etc/profile.d/claude-code-bedrock.sh`, which is why every new
terminal already has them:

```bash
export AWS_REGION=us-east-1          # your event's region
export AWS_DEFAULT_REGION=us-east-1
export CLAUDE_CODE_USE_BEDROCK=1
export ANTHROPIC_MODEL='us.anthropic.claude-sonnet-4-6'
export ANTHROPIC_DEFAULT_SONNET_MODEL='us.anthropic.claude-sonnet-4-6'
export ANTHROPIC_DEFAULT_HAIKU_MODEL='us.anthropic.claude-haiku-4-5-20251001-v1:0'
```

The `us.` prefix is a
[cross-region inference profile](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html):
Bedrock spreads the request across US regions for capacity, and you address it as
one model id. `ANTHROPIC_MODEL` is the model a session starts on; the
`ANTHROPIC_DEFAULT_*_MODEL` variables are what `/model sonnet` and the background
tasks resolve to.

Note what is **not** there: `ANTHROPIC_DEFAULT_OPUS_MODEL`. The boot script pins an
alias only to a model it has confirmed this account can invoke, because a pinned but
unavailable alias fails the moment you select it. That is why `/model opus` is not
offered here.

**3. The instance role grants exactly the Bedrock actions Claude Code needs**, and
nothing else on the Bedrock side:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
    "bedrock:ListInferenceProfiles",
    "bedrock:GetInferenceProfile",
    "bedrock:ListFoundationModels",
    "bedrock:GetFoundationModel",
    "bedrock:CountTokens"
  ],
  "Resource": [
    "arn:aws:bedrock:*::foundation-model/*",
    "arn:aws:bedrock:*:<account-id>:inference-profile/*",
    "arn:aws:bedrock:*:<account-id>:application-inference-profile/*"
  ]
}
```

`InvokeModel` and `InvokeModelWithResponseStream` are the calls that carry your
prompts. The three read-only actions let Claude Code discover which inference
profiles exist before it picks one - which is what makes the fallback below
possible.

:::alert{type="info"}
Reusing this in your own account? Those seven actions, plus
`aws-marketplace:ViewSubscriptions` and `aws-marketplace:Subscribe` for the first
invocation, are the whole policy. Attach it to a role, set the environment
variables above, and Claude Code is on Bedrock. The full reference is
[Claude Code on Amazon Bedrock](https://code.claude.com/docs/en/amazon-bedrock).
:::

## Why not Opus

AWS releases its newest, most capable models - the Opus family, and Claude Sonnet 5 -
only to accounts that meet a trust threshold, as a fraud-prevention measure. A
temporary workshop account is created fresh for the event and does not meet it, which
is why `/model opus` is not wired up here. It is not something you can change from the
console; if you need Opus for an event, your facilitator arranges it in advance.

The boot script does not assume any of this. It sends one tiny request to the model
it intends to use, and pins an alias only if that request succeeded:

| Model | Role | Available in a workshop account |
|-------|------|---------------------------------|
| `us.anthropic.claude-sonnet-4-6` | Active model, and `/model sonnet` | Yes |
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Background tasks (session titles) | Yes |
| `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Standby, used only if Sonnet 4.6 does not answer | Yes |
| Opus family, Sonnet 5 | Not used - above this account's trust threshold | No |


`cat /workshop/ENVIRONMENT.md` shows what the probe settled on, and
`/var/log/bedrock-probe.log` holds the raw errors if something looks wrong.

If you specifically want to evaluate Opus, use your own AWS account or your own
Claude plan - see [Path B](bring-your-own.md), options B3 and B4.

:::alert{type="info"}
**For facilitators.** Neither the Bedrock console model-access form nor a quota
request lifts this - it needs an account trust elevation arranged well before the
event. If your event's accounts have been elevated, set the Code Editor stack's
`PrimaryModel` parameter to `us.anthropic.claude-opus-5` and `FallbackModel` to
`us.anthropic.claude-sonnet-5`; everything else adapts on its own. The elevation is
scored per account and is not something the Bedrock console exposes, so ask your AWS
account team to arrange it well before the event; if you are running this inside
Amazon, the facilitator notes alongside the workshop source name the exact mechanism
and request path.
:::

## Troubleshooting

**`/status` shows something other than Amazon Bedrock.**
`CLAUDE_CODE_USE_BEDROCK` is not reaching the process. Run `bedrock-status` in the
same terminal - if it prints `unset`, open a new terminal so the login profile is
picked up, or source it by hand:
`source /etc/profile.d/claude-code-bedrock.sh`.

**A startup warning about Opus.**
`Opus: Opus 5 not available — using Opus 4.6 for this session` is expected and
harmless. It is Claude Code checking its own built-in default before falling back.
Stay on Sonnet - see [Why not Opus](#why-not-opus).

**`AccessDeniedException` on a prompt.**
If it names an Opus model or Sonnet 5, that is the trust threshold described in
[Why not Opus](#why-not-opus) - use `/model sonnet`. If it names Sonnet 4.6, the
account is in a state your facilitator needs to look at; show them
`/var/log/bedrock-probe.log`.

**`on-demand throughput isn't supported`.**
The model id lost its `us.` prefix. On-demand invocation of these models goes
through an inference profile, so the id must be `us.anthropic.claude-...`, not
`anthropic.claude-...`.

**`ThrottlingException` when the whole room prompts at once.**
Sandbox accounts have modest Bedrock quotas. Wait a few seconds and retry - the
cross-region inference profile spreads load across US regions, so bursts usually
clear quickly.

**Web search does not work inside Claude Code.**
Expected. The WebSearch tool is not available on Bedrock. Everything else is.

## Verification

- `claude` starts and answers a prompt
- `/status` reports Amazon Bedrock as the provider
- `bedrock-status` shows an active model, and `/model sonnet` selects it
- You did not type a single credential

Next: skip [Path B](bring-your-own.md) unless you want your own tooling, and go to
[Part 1: The building blocks](../030-part-1-building-blocks/index.md).
