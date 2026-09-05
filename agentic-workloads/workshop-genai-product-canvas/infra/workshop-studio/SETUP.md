# Workshop Studio setup guide

This folder contains the Workshop Studio-specific setup notes for the portal (not
into the participant's hands).

The tool backend, dataset seeding, Lambda code injection, and the AgentCore
Gateway are all handled by the single stack `infra/template.yaml` (see below).
There is no separate data-seeder template and no provisioning script to run.

## Step-by-step portal setup

### 1. Create the workshop

1. Go to [studio.us-east-1.prod.workshops.aws](https://studio.us-east-1.prod.workshops.aws)
2. Click **Create Workshop**
3. Fill in:
   - **Title:** Touch Grass: Hug a Tree, Play with Data
   - **Subtitle:** Building a biodiversity anomaly agent on Amazon Bedrock AgentCore
   - **Description:** Design an AI agent with the GenAI Product Canvas, then build it on Amazon Bedrock AgentCore. 90 minutes, instructor-led.
   - **Duration:** 90 minutes
   - **Level:** 300
   - **Services:** Amazon Bedrock, AWS Lambda, Amazon S3, Amazon Bedrock AgentCore
   - **Categories:** AI/ML, Generative AI

### 2. Configure infrastructure

`ws-studio/contentspec.yaml` already declares both templates, in the order
Workshop Studio deploys them. **Keep that order: the tools stack has to deploy
first.** It seeds the baseline agent source to
`s3://<ToolsProjectName>-data-<account>-<region>/workspace/`, and the Code Editor
syncs that into `/workshop/repo` at boot, retrying while the tools stack finishes.
Part 0 itself still comes up if the tools stack is late - Claude Code and the Kiro
CLI do not need it - but Part 1 has nothing to open, so the order is not optional.

Run `bash infra/sync_to_ws_studio.sh` after editing either template - it
re-embeds the assets and copies both files into the Workshop Studio content
repo's `static/infra/`. Workshop Studio only ever sees those copies.

#### Template 1 - `code-editor.yaml` (Part 0)

Creates the participant workstation:

- a small dedicated VPC with one public subnet (so it never depends on the
  account having a usable default VPC),
- an EC2 instance (Graviton `m8g.xlarge`, encrypted gp3, IMDSv2 only) running
  Code Editor behind nginx,
- a CloudFront distribution in front of it, with the origin security group
  restricted to the AWS-managed `com.amazonaws.global.cloudfront.origin-facing`
  prefix list,
- a Secrets Manager secret holding the Code Editor connection token, and a
  custom resource that joins the CloudFront domain and the token into one
  clickable `CodeEditorUrl` output,
- an instance role granting exactly the Bedrock actions Claude Code needs, plus
  what `agentcore deploy` needs in Part 2.

Its UserData installs Claude Code and points it at Bedrock using the instance
role - no API key anywhere. It then **probes** which Anthropic models this
account can actually invoke (one tiny Converse call each) and pins only those:

The defaults are the models a Workshop Studio account can actually invoke:

| Parameter | Default | Role |
|-----------|---------|------|
| `PrimaryModel` | `us.anthropic.claude-sonnet-4-6` | Active model and the `sonnet` alias |
| `FallbackModel` | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Standby if the primary does not answer |
| `SmallFastModel` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Background tasks |

Only an alias that answered the probe gets pinned, so no alias ever resolves to a
model the account cannot invoke. The `opus` alias is deliberately left unset. The
result lands in `/workshop/ENVIRONMENT.md` (what participants see);
`bedrock-status` reprints it and `/var/log/bedrock-probe.log` holds raw errors.

**If your event's accounts have been elevated** (see the note below), switch to the
frontier models by overriding two parameters - nothing else needs to change, and the
`opus` alias starts being pinned automatically:

```
PrimaryModel  = us.anthropic.claude-opus-5
FallbackModel = us.anthropic.claude-sonnet-5
```

> **Verify model access before the event.** A leased Workshop Studio account can
> invoke **Sonnet 4.6, Sonnet 4.5 and Haiku 4.5**, but **not Opus 5, Sonnet 5,
> Opus 4.8 or Opus 4.7**. Those return
> `AccessDeniedException: <model> is not available for this account`.
>
> This is an AWS-wide account trust threshold applied to newly created accounts, not
> a Workshop Studio limitation, and neither the Bedrock console model-access form nor
> a quota request lifts it. It has to be arranged in advance for the event's accounts.
>
> **AWS-internal facilitators:** the exact mechanism, the numbers, how to check an
> account and how to request an elevation are in `FACILITATOR-NOTES-INTERNAL.md` at
> the root of the internal repo. If your accounts do get elevated, override two
> parameters:
>
> ```
> PrimaryModel  = us.anthropic.claude-opus-5
> FallbackModel = us.anthropic.claude-sonnet-5
> ```
>
> If you do nothing, the workshop still runs: the probe falls through to Sonnet 4.6
> and the content explains that to participants.

The Kiro CLI is installed last - a ~0.5 GB download that never blocks the editor -
and is left unauthenticated, because Kiro signs in with the participant's own
identity. Note it installs the **musl** build: Amazon Linux 2023 ships glibc 2.34
and the GNU build requires 2.39+, so the GNU installer aborts with *"try
installing the musl version of the CLI"*. The glibc build is retried second in
case a future AMI moves past 2.39.

Other parameters worth knowing:
- **`RepoUrl` - leave it empty.** There is nothing to set before an event. The
  baseline agent source is embedded in the tools template, written to S3 when that
  stack deploys, and synced into `/workshop/repo` at boot, so neither a parameter
  nor a content page carries a repository URL that can go stale. Give `RepoUrl` a
  value only to pull an *extra* repository in alongside the agent source - it lands
  in `/workshop/repo-git`, and a clone failure never fails the stack.

  If the agent source is missing, the cause is the tools stack rather than this
  parameter: the workspace gets an `AGENT-SOURCE-MISSING.md` telling the
  participant to ask you, and `ENVIRONMENT.md` flags the source as MISSING. Check
  that the tools stack reached CREATE_COMPLETE.

**The CloudFront prefix list needs no parameter.** There is nothing to set when you
run outside us-east-1: the template looks the origin-facing prefix list up by name
(`com.amazonaws.global.cloudfront.origin-facing`) at deploy time through the
`PrefixListLookupFunction` custom resource, because the id differs in every region.
To see the id a region resolves to:

```bash
aws ec2 describe-managed-prefix-lists --region <region> \
  --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing
```

**Debugging a participant account:** the whole bootstrap is logged to
`/var/log/user-data.log`, and probe failures to `/var/log/bedrock-probe.log`.
Reach the instance with Session Manager (no SSH, no key pair) - the instance id
is the `CodeEditorInstanceId` stack output.

#### Template 2 - `template.yaml` (Parts 1-2 backend)

It creates, in one coherent stack:
- the S3 data bucket and audit bucket,
- the tools IAM role and the tools Lambda,
- a provisioner custom resource that, on stack create, seeds the datasets to
  `s3://<DataBucket>/data/` (the exact prefix the tools Lambda reads) and injects
  the real tools Lambda code,
- a `GatewaySetup` custom resource that creates Cognito + the AgentCore Gateway
  and writes `gateway.env` to SSM.

All of its assets - handler code, tool contract, datasets - are embedded in the
template as gzip+base64 blobs, so there is no external repo to reach and no
pre-upload step. Regenerate them with `python3 infra/bundle_assets.py` (or just
run `sync_to_ws_studio.sh`, which calls it) whenever a source file changes.

### 3. Participant permissions (read this before changing it)

`ws-studio/contentspec.yaml` attaches `static/ws-participant-policy.json` to the
participant role. It is **not** AdministratorAccess: it grants Bedrock invoke,
the AgentCore control plane, the CloudFormation / ECR / CodeBuild / IAM-role path
`agentcore deploy` needs, the workshop's own buckets, parameters and secrets, and
read-only observability - all scoped to the account and to us-east-1, with
explicit Denies over the organization, billing and identity-administration
control planes.

Verified against real IAM in a live test account (19 of 19 workshop operations
allowed; `iam:CreateUser`, `iam:UpdateAssumeRolePolicy`, `organizations:*`,
role creation outside the workshop prefixes, and buckets outside the workshop
prefixes all denied).

Two things to know if you change it:

- **6144 characters is a hard cap** on an IAM policy, and the file sits at
  6119 - only 25 characters of headroom. Trim something before you add anything,
  or provisioning fails.
- **Role writes are name-scoped** to `touch-grass-*`,
  `biodiversity-anomaly-*`, `cdk-*` and `*gentCore*`/`*gentcore*`. If
  `agentcore deploy` picks a role name outside those, add the prefix - do not
  widen the actions.

Deliberately left out, because the participant does not perform them: creating
Cognito pools and Lambda functions (the tools stack's own Lambda does that under
its own role), Bedrock Guardrail writes (an extension challenge), the scheduled
batch extension, and Session Manager (facilitators use `WSOpsRole`). If you run
an extension that needs one of these, widen the policy for that event.

> **Path B caveat.** Participants who work from their own laptop rather than the
> Code Editor use this participant role, so they get the same boundary. The
> Code Editor's own instance role is separate and slightly broader; the
> documented primary path is to run `agentcore deploy` from the Code Editor.

### 4. Upload content

Workshop Studio uses a Git-backed content model. You can either:

**Option A: Push via Git**
1. Get the Workshop Studio Git remote URL from the portal
2. Push the `content/` folder structure
3. Each folder needs a `_index.yaml` or front matter in `index.md`

**Option B: Manual Upload**
1. Go to **Content** tab
2. Create sections matching `content/` structure
3. Copy markdown into each section's editor

#### Content structure (already built)

```
content/
├── index.md                              (Overview)
├── 010-prerequisites/
├── 015-architecture/                     (diagram + security controls)
├── 020-part-0-environment/               (index, code-editor, claude-code-bedrock, bring-your-own)
├── 030-part-1-building-blocks/           (index, 031-frame, 032-see-working-agent, 035-observability)
├── 040-part-2-design/                    (index, 041-design-canvas, 050-build, 080-validate)
├── 090-cleanup/
├── 100-extensions/
└── 110-own-account/
```

### 5. Lifecycle hooks (none required)

There is nothing to configure here beyond the CloudFormation template in step 2.
The single stack provisions the entire backend on account creation:

1. Creates the buckets, tools Lambda, and gateway-setup Lambda.
2. A provisioner custom resource decodes the assets embedded in the template,
   seeds the datasets to `s3://<DataBucket>/data/`, writes the agent source,
   canvas and skills to `s3://<DataBucket>/workspace/`, and injects the real code
   into both Lambdas. Nothing is downloaded from outside the account.
3. A `GatewaySetup` custom resource then creates the Cognito user pool + M2M
   client and the AgentCore Gateway + Lambda target, and writes the full
   `gateway.env` contents to the SSM SecureString parameter `/touch-grass/gateway-env`.

Participants run NO infrastructure scripts. They fetch `gateway.env` with a single
`aws ssm get-parameter` command (see `content/030-part-1-building-blocks/032-see-working-agent.md`), design the agent, and
run the deploy skill.

**On cleanup (event ends):** Workshop Studio deletes the stack. The custom
resources tear themselves down: the provisioner empties `s3://<DataBucket>/data/`,
and `GatewaySetup` deletes the Gateway, target, Cognito pool, gateway IAM role, and
the SSM parameters. The AgentCore Runtime a participant deploys is removed with the
account.

**Validation caveats (verify in a test event before go-live):**
- The gateway-setup Lambda calls `bedrock-agentcore-control`. Confirm the Lambda
  runtime's bundled `boto3` includes that client in your region; if not, attach a
  newer boto3 layer to `GatewaySetupFunction`.
- Gateway creation is asynchronous; the function polls for `READY` (timeout 600s).
- The provisioner and gateway-setup Lambdas need outbound internet (default for a
  non-VPC Lambda) to reach GitHub and the AgentCore/Cognito endpoints.

### 6. Source repository setup

**Nothing to do.** There is no repository for the workshop to clone, and no
pre-upload step for you to perform.

The agent source, the product canvas, the deploy skill, the tool contract, the
Lambda handlers and the wildlife datasets are all embedded in `infra/template.yaml`
as gzip+base64 blobs by `infra/bundle_assets.py`. The provisioner custom resource
writes them from the template into the account's own S3 bucket at deploy time, and
the Code Editor mirrors them into `/workshop/repo` at boot. No Lambda reaches out
to GitHub, so there is no public repository to keep in sync and nothing that can
roll the stack back because a URL is wrong.

Run `python3 infra/bundle_assets.py` after changing anything under `agent/`,
`canvas/`, `skills/` or `infra/lambda/`, then
`bash infra/sync_to_ws_studio.sh` to copy both templates into the Workshop Studio
content folder. The bundler fails loudly if an expected directory is missing, if a
file looks like it contains a real credential, or if the Code Editor's UserData
would exceed EC2's 16 KB limit.

The Code Editor template still accepts an optional `RepoUrl`. That clones an extra
repository into the workspace alongside the agent source and is only useful if you
are running a variant from your own repo; leave it empty otherwise, and a clone
failure never fails the stack.

### 7. Create a test event

1. Go to **Events** > **Create Event**
2. Set type: **Test** (free, no approval needed)
3. Capacity: 1 participant
4. Walk through the entire workshop yourself
5. Verify:
   - [ ] CFN deploys successfully
   - [ ] S3 has all 5 JSON datasets
   - [ ] Lambda responds to test invocations
   - [ ] gateway.env values are filled into `agent/agentcore/agentcore.json` envVars
   - [ ] `agentcore deploy` (from `agent/`) produces a runtime
   - [ ] `agentcore invoke` returns an investigation report

### 8. Publish

For an internal or invite-only run:
- Keep as **Private** workshop
- Create an event with access code for participants
- Share the join URL: `catalog.workshops.aws/join?access-code=XXXX-XXXXXX-XX`

For public catalog:
- Submit for review (requires AppSec + content review, allow 2 weeks)
