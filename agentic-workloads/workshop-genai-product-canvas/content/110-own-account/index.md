---
title: "Running this in your own AWS account"
weight: 110
---

The workshop is designed for an AWS-run event, where the account and all the
infrastructure are provisioned for you. You can also deploy the whole thing into
an account you own — for a self-paced run, or to try it with your own team.

:::alert{type="warning"}
**This costs real money in your own account.** The EC2 Code Editor and the
CloudFront distribution are the meaningful line items — roughly a few US dollars a
day for an `m8g.xlarge` left running — plus per-request charges for Bedrock,
AgentCore Runtime, Lambda and CodeBuild. Delete both stacks when you are done
([Cleanup](../090-cleanup/index.md)). Pricing:
:link[Amazon EC2]{href="https://aws.amazon.com/ec2/pricing/" external=true},
:link[Amazon CloudFront]{href="https://aws.amazon.com/cloudfront/pricing/" external=true},
:link[Amazon Bedrock]{href="https://aws.amazon.com/bedrock/pricing/" external=true},
:link[Amazon Bedrock AgentCore]{href="https://aws.amazon.com/bedrock/agentcore/pricing/" external=true}.
:::

## What you need

- An AWS account you can create IAM roles, VPCs, EC2 instances and CloudFront
  distributions in.
- **us-east-1** or **us-west-2**. Both are validated; the templates contain no
  region-specific values.
- Amazon Bedrock model access for an Anthropic Claude model in that region
  (**Bedrock console → Model catalog**, select the model, submit the use case
  form). Claude Sonnet 4.6 is the default the workshop uses.
- The AWS CLI v2, configured with credentials for that account.

## Get the templates

Both CloudFormation templates are served by this workshop itself, so you can take
them straight from the page you are reading:

- :link[infra/template.yaml]{href="/static/infra/template.yaml" external=true} — the
  tools backend. Self-contained: the datasets, the Lambda handlers and the baseline
  agent source are embedded in it, so there is nothing to upload first.
- :link[infra/code-editor.yaml]{href="/static/infra/code-editor.yaml" external=true}
  — the Part 0 Code Editor.

Or on the command line, replacing `<workshop-url>` with the address in your browser's
address bar:

```bash
curl -O <workshop-url>/static/infra/template.yaml
curl -O <workshop-url>/static/infra/code-editor.yaml
```

The full sample - the agent, the canvas, the deploy skill and the docs, not just the
two templates - is published as `workshop-genai-product-canvas` in the
`aws-samples/sample-apj-sup-sa` repository. Ask your facilitator for the link if you
want the whole thing rather than the infrastructure.

## Deploy, in this order

The tools backend must exist before the Code Editor, because the Code Editor syncs
the baseline agent source out of the bucket the tools stack creates.

### 1. Tools backend

The template embeds its assets, so it is larger than CloudFormation's inline
limit — pass `--s3-bucket` and the CLI stages it for you:

```bash
export AWS_REGION=us-east-1          # or us-west-2
STAGE="touch-grass-cfn-$(aws sts get-caller-identity --query Account --output text)"
aws s3 mb "s3://$STAGE" 2>/dev/null || true

aws cloudformation deploy \
  --template-file infra/template.yaml \
  --stack-name biodiversity-anomaly-tools \
  --s3-bucket "$STAGE" \
  --capabilities CAPABILITY_NAMED_IAM
```

This takes about 5–10 minutes; most of it is the AgentCore Gateway reaching
`READY`. When it finishes:

```bash
aws cloudformation describe-stacks --stack-name biodiversity-anomaly-tools \
  --query "Stacks[0].Outputs" --output table
```

You should see the data, audit and log bucket names, the Gateway URL, and
`AgentSourceUri` — the S3 prefix holding the agent, canvas and skill files.

### 2. Part 0 Code Editor

```bash
aws cloudformation deploy \
  --template-file infra/code-editor.yaml \
  --stack-name touch-grass-code-editor \
  --parameter-overrides ToolsProjectName=biodiversity-anomaly \
  --capabilities CAPABILITY_IAM
```

`ToolsProjectName` must match the tools stack's `ProjectName` (default
`biodiversity-anomaly`) — that is how the Code Editor finds the agent source.

CloudFront takes 10–15 minutes. Then open the editor:

```bash
aws cloudformation describe-stacks --stack-name touch-grass-code-editor \
  --query "Stacks[0].Outputs[?OutputKey=='CodeEditorUrl'].OutputValue" --output text
```

### 3. Check the environment came up

Open that URL, start a terminal in the editor, and run:

```bash
cat /workshop/ENVIRONMENT.md
bedrock-status
ls /workshop/repo/agent
```

You should see a model pinned, and the baseline agent source in the workspace.
From here, [Part 0](../020-part-0-environment/index.md) onward reads exactly the
same as it does at an event.

## What differs from an AWS-run event

| | AWS-run event | Your own account |
|---|---|---|
| Account | Provisioned and reclaimed for you | Yours, and yours to clean up |
| Participant permissions | `WSParticipantRole` + the workshop's scoped policy | Whatever your identity already has |
| Cost | Covered by the event | **Billed to you** |
| Cleanup | Automatic at event end | [Manual](../090-cleanup/index.md) |
| Model access | Pre-checked by the facilitator | You enable it in the Bedrock console |
