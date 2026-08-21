# Workshop Studio Setup Guide

This folder contains the Workshop Studio-specific setup notes for the portal (not
into the participant's hands).

The tool backend, dataset seeding, Lambda code injection, and the AgentCore
Gateway are all handled by the single stack `infra/template.yaml` (see below).
There is no separate data-seeder template and no provisioning script to run.

## Step-by-Step Portal Setup

### 1. Create the Workshop

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

### 2. Configure Infrastructure

In the Workshop Studio portal, under **Infrastructure**:

#### CloudFormation Template (single stack)

Upload ONE template:

**`template.yaml`** (from `infra/template.yaml`), stack name `biodiversity-anomaly-tools`.

It creates, in one coherent stack:
- the S3 data bucket and audit bucket,
- the tools IAM role and the tools Lambda,
- a provisioner custom resource that, on stack create, downloads the workshop repo
  and (a) seeds the datasets to `s3://<DataBucket>/data/` (the exact prefix the
  tools Lambda reads) and (b) injects the real tools Lambda code from
  `infra/lambda/tools/index.py`.

Set these stack parameters to your published repo:
- `RepoUrl` (default `https://github.com/aws-samples/touch-grass-agentcore-workshop`)
- `RepoBranch` (default `main`)
- `DataDir` (default `agent/data/`) and `CodePath` (default `infra/lambda/tools/index.py`)
  only change if you move those files in the repo.

After the stack completes, the `DatasetsSeeded` output shows how many dataset
files were written. The tools Lambda has data and real code on first invoke, so no
`deploy_tools.sh` run is required in the workshop path.

### 3. Participant permissions

`content/contentspec.yaml` grants participants `AdministratorAccess` in their
temporary, isolated Workshop Studio account. This is intentional: `agentcore
deploy` creates roles, an ECR image, a CodeBuild project, and S3 artifacts with
non-deterministic names, so a tight boundary tends to block the deploy.

If you later want least privilege, build the boundary against the ACTUAL runtime
behaviour (allow `bedrock-agentcore:*`, `ecr:*`, `codebuild:*`, `iam:CreateRole`/
`PassRole` for the auto-created execution role, and the real `biodiversity-anomaly-*`
bucket names) and validate it in a test event before relying on it. The previous
boundary file was removed because it was unused and would have blocked deployment.

### 4. Upload Content

Workshop Studio uses a Git-backed content model. You can either:

**Option A: Push via Git**
1. Get the Workshop Studio Git remote URL from the portal
2. Push the `content/` folder structure
3. Each folder needs a `_index.yaml` or front matter in `index.md`

**Option B: Manual Upload**
1. Go to **Content** tab
2. Create sections matching `content/` structure
3. Copy markdown into each section's editor

#### Content Structure (already built)

```
content/
├── index.md                    (Overview)
├── 010-prerequisites/
├── 020-frame/
├── 030-see-it-work/
├── 035-observability/
├── 040-design/
├── 050-build/
├── 060-deploy/
├── 080-validate/
├── 090-cleanup/
└── 100-extensions/
```

### 5. Lifecycle hooks (none required)

There is nothing to configure here beyond the CloudFormation template in step 2.
The single stack provisions the entire backend on account creation:

1. Creates the buckets, tools Lambda, and gateway-setup Lambda.
2. A provisioner custom resource downloads the public repo, seeds the datasets to
   `s3://<DataBucket>/data/`, and injects the real code into both Lambdas.
3. A `GatewaySetup` custom resource then creates the Cognito user pool + M2M
   client and the AgentCore Gateway + Lambda target, and writes the full
   `gateway.env` contents to the SSM SecureString parameter `/touch-grass/gateway-env`.

Participants run NO infrastructure scripts. They fetch `gateway.env` with a single
`aws ssm get-parameter` command (see `content/050-build`), design the agent, and
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

### 6. Source Repository Setup

The participant clones a GitHub repo. You need to:

This is a HARD prerequisite. The stack's provisioner Lambda downloads the datasets
and tool code from this public repo over HTTPS. If the repo is missing, private, or
the URL is wrong, the custom resource fails and the whole stack rolls back, so no
participant account will work.

1. Push the `workshop/` folder to a PUBLIC GitHub repo
   (e.g. `aws-samples/touch-grass-agentcore-workshop`). The internal GitLab mirror
   is not reachable from Lambda and cannot be used as `RepoUrl`.
2. Make sure `RepoUrl` / `RepoBranch` match in BOTH `infra/template.yaml`
   (and its copy `content/static/template.yaml`) and in
   `content/contentspec.yaml` (the values Workshop Studio passes to the stack).
3. Confirm `content/050-build/index.md` clones the same URL.
4. Sanity check before the event: `curl -sIL <RepoUrl>/archive/refs/heads/<branch>.zip`
   should return `200`.

### 7. Create a Test Event

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

For internal/CTO Day:
- Keep as **Private** workshop
- Create an event with access code for participants
- Share the join URL: `catalog.workshops.aws/join?access-code=XXXX-XXXXXX-XX`

For public catalog:
- Submit for review (requires AppSec + content review, allow 2 weeks)
