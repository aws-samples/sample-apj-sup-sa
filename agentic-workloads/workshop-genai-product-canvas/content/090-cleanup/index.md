---
title: "Cleanup"
weight: 90
---

:::alert{type="info"}
**At an AWS-run event you do not need to clean anything up.** Workshop Studio
terminates the participant account and everything in it when the event ends, so
every resource below goes with it and you are not billed. The steps in this page
are for anyone who deployed the workshop into **their own** AWS account.
:::

## What exists in the account

| Resource | Created by | Removed when… |
|----------|-----------|---------------|
| VPC, EC2 Code Editor, CloudFront distribution, Secrets Manager token | **Part 0 Code Editor** stack | the stack is deleted |
| S3 data, audit and log buckets, and the seeded datasets and agent source | **Tools backend** stack | the stack is deleted (its provisioner empties all three buckets first) |
| Tools Lambda + IAM role | Tools backend stack | the stack is deleted |
| AgentCore Gateway + target, Cognito user pool, gateway IAM role, `gateway.env` SSM parameters | Tools backend stack (`GatewaySetup` custom resource) | the stack is deleted |
| AgentCore Runtime, its ECR image, CodeBuild project and CDK stack | **you**, via `agentcore deploy` | you remove it (step 1 below) |
| CDK bootstrap stack (`CDKToolkit`) and its S3 bucket | `cdk bootstrap`, if you ran it | you delete it (step 4 below) |

## Nothing is deliberately retained

Every resource this workshop creates is deleted with its stack. No bucket, log
group, IAM role, secret or parameter uses `DeletionPolicy: Retain`, and the
CloudFormation stacks are designed to roll back and delete without leaving
anything behind — the tools stack's provisioner empties the data, audit and log
buckets on delete so CloudFormation can remove them.

Two things sit outside that, and neither belongs to this workshop's own templates:

- **The CDK bootstrap assets bucket.** `agentcore deploy` bootstraps CDK the first
  time it runs, and AWS's bootstrap template deliberately retains
  `cdk-hnb659fds-assets-<account>-<region>` so deleting the stack cannot destroy
  build artefacts. Step 4 below removes it by hand.
- **A *timer*, not a resource.** Deleting a KMS key or a Secrets Manager secret
  yourself schedules rather than performs the deletion (7–30 days). The Code
  Editor token secret is removed immediately with the stack, so there is nothing
  to wait for; the CDK bootstrap key created by `agentcore deploy` does go to
  pending-deletion.

Verified by deleting both stacks in a live account: no workshop buckets, no
running instances, no secrets, no SSM parameters, no VPC, no Cognito user pool, no
AgentCore gateway and no AgentCore runtime remained.

:::alert{type="warning"}
**Costs if you leave it running.** In your own account, the resources above bill
until you delete them. The EC2 Code Editor instance and the CloudFront
distribution are the meaningful ones — an `m8g.xlarge` left running costs roughly
a few US dollars a day. Bedrock, AgentCore Runtime and Lambda are per-request, so
they stop costing when you stop calling them. Delete both stacks when you are
done. Pricing:
:link[Amazon EC2]{href="https://aws.amazon.com/ec2/pricing/" external=true},
:link[Amazon CloudFront]{href="https://aws.amazon.com/cloudfront/pricing/" external=true},
:link[Amazon Bedrock]{href="https://aws.amazon.com/bedrock/pricing/" external=true},
:link[Amazon Bedrock AgentCore]{href="https://aws.amazon.com/bedrock/agentcore/pricing/" external=true}.
:::

## If you deployed into your own account

Do these in order — the runtime you deployed depends on the backend, so remove it
first.

### 1. Remove the AgentCore Runtime you deployed

The AgentCore CLI tears the runtime down by removing it from the project config
and re-deploying, so CDK deletes the removed resources:

```bash
cd /workshop/repo/agent
agentcore remove all -y      # clears the runtime from agentcore/agentcore.json
agentcore deploy -y          # CDK tears down the removed AWS resources
```

Confirm it is gone:

```bash
agentcore status             # should report no runtime
```

That empties the CDK stack but leaves the stack itself behind, so delete it too:

```bash
aws cloudformation delete-stack --stack-name AgentCore-biodiversity-default
aws cloudformation wait stack-delete-complete --stack-name AgentCore-biodiversity-default
```

### 2. Delete the Part 0 Code Editor stack

```bash
aws cloudformation delete-stack --stack-name <your-code-editor-stack-name>
aws cloudformation wait stack-delete-complete --stack-name <your-code-editor-stack-name>
```

CloudFront takes 10–15 minutes to disable and delete, so the wait is normal.

### 3. Delete the tools backend stack

Its custom resources clean up after themselves: the provisioner empties the data,
audit and log buckets, and `GatewaySetup` removes the Gateway, its target, the
Cognito pool, the gateway IAM role and the SSM parameters.

```bash
aws cloudformation delete-stack --stack-name biodiversity-anomaly-tools
aws cloudformation wait stack-delete-complete --stack-name biodiversity-anomaly-tools
```

### 4. Remove the CDK bootstrap, if you added it

`agentcore deploy` bootstraps CDK in the account the first time it runs, so this
stack exists even though you never asked for it. Remove it only if you do not want
it for anything else - anything else using CDK in this account and region needs it.

```bash
aws cloudformation delete-stack --stack-name CDKToolkit
aws cloudformation wait stack-delete-complete --stack-name CDKToolkit
```

:::alert{type="warning"}
**Deleting that stack leaves its assets bucket behind, on purpose.** AWS's own
bootstrap template marks `cdk-hnb659fds-assets-<account>-<region>` as retained so
a delete cannot destroy build artefacts. Verified: after `CDKToolkit` is gone the
bucket is still there holding a few objects, and its KMS key goes to
pending-deletion rather than being destroyed. Empty and remove the bucket yourself
if you want the account completely clean:

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws s3 rm "s3://cdk-hnb659fds-assets-$ACCOUNT-$AWS_REGION" --recursive
aws s3 rb "s3://cdk-hnb659fds-assets-$ACCOUNT-$AWS_REGION"
```

At an AWS-run event you can ignore all of this - the account is reclaimed whole.
:::

### 5. Verify nothing is left

```bash
aws cloudformation describe-stacks --query "Stacks[].StackName" --output text
aws s3 ls | grep -E "biodiversity-anomaly|touch-grass" || echo "no workshop buckets"
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=*code-editor*" "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" --output text
```

All three should come back empty.

### 6. Local files

Anything you wrote in `/workshop` lives on the Code Editor instance and goes with
it. If you cloned or copied files to your own machine, they stay there — keep them
as a reference or delete them. Temporary Workshop Studio credentials expire on
their own, typically within 1–4 hours.
