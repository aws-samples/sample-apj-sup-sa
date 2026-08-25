---
title: "Architecture and security controls"
weight: 15
---

Two CloudFormation stacks are deployed into your account before the event starts.
Everything you use in the workshop is in this picture, including the controls that
keep it contained.

![Architecture of the Touch Grass workshop. A participant reaches a browser Code Editor over HTTPS through CloudFront; the Code Editor runs on EC2 with an instance role and calls Amazon Bedrock. A second stack holds three encrypted S3 buckets, a tools Lambda, and an AgentCore Gateway protected by Cognito. The agent the participant deploys runs on AgentCore Runtime, calls the tools through the Gateway, and emits logs and traces to CloudWatch and X-Ray.](/static/architecture.svg)

## What each stack gives you

| Stack | Contains | You interact with it by |
|-------|----------|------------------------|
| **Part 0 Code Editor** | VPC, EC2 instance running VS Code, CloudFront distribution, Secrets Manager token | Opening `CodeEditorUrl` in your browser |
| **Tools backend** | S3 data / audit / log buckets, tools Lambda, AgentCore Gateway, Cognito user pool, `gateway.env` in SSM | Your agent calling the tools; you never configure it |

The tools backend deploys first, because it seeds the baseline agent source to S3
and the Code Editor syncs that into your workspace at boot.

## The security controls in place

These are worth knowing because they are the same decisions you would make taking
an agent to production.

| Control | Where | Why |
|---------|-------|-----|
| No long-lived credentials | Code Editor uses an **EC2 instance role**; the deployed agent uses a scoped **execution role** | Nothing to type, store, leak or rotate |
| Editor reachable only through CloudFront | Origin security group allows port 80 **only from the CloudFront origin-facing prefix list** — never `0.0.0.0/0` | The instance is not exposed to the internet |
| HTTPS enforced | CloudFront `redirect-to-https`; the editor URL carries a connection token | The token never travels in plaintext |
| Instance hardening | IMDSv2 required, encrypted gp3 volume, Session Manager instead of SSH (no key pair, no port 22) | Removes the usual EC2 attack surface |
| Least-privilege Bedrock access | Instance role grants `bedrock:InvokeModel` and profile discovery, nothing else on Bedrock | The editor can run models, not manage them |
| Buckets closed by default | All three S3 buckets: public access blocked, SSE encryption, server access logging, versioning, and a bucket policy denying any request that arrives over plain HTTP | No accidental exposure of workshop data |
| Tool Lambda split permissions | Read-only on the data bucket, write-only on the audit bucket — and the audit bucket is versioned, so a second write cannot quietly replace the first | A tool cannot rewrite the data it reads, and cannot overwrite its own trail |
| Token under a key you control | The Code Editor connection token is encrypted with a **customer-managed KMS key**; only the instance role and the URL builder are granted `kms:Decrypt`, and only via Secrets Manager | Holding `secretsmanager:GetSecretValue` is not enough to read the one real credential in the account |
| Gateway behind machine auth | AgentCore Gateway uses a Cognito client-credentials JWT; the secret lives in an SSM **SecureString** | Tools are not open endpoints |
| Region-pinned participant role | Every action in the workshop policy is named - no wildcards on anything that writes - and scoped by ARN and region. A companion guardrail policy denies organization, billing and identity administration, restricts `iam:PassRole` by target service, and blocks attaching admin-grade policies | A workshop account cannot reach beyond itself, and cannot escalate inside itself |
| No retained resources | Every resource is deleted with its stack; the tools stack empties its buckets on delete | Nothing survives to surprise you later — see [Cleanup](../090-cleanup/index.md) |

:::alert{type="info"}
**All data in this workshop is synthetic.** The species, stations, detections,
weather, land-use changes and news articles were generated for teaching. Nothing
in the workshop contains, stores or processes personal or customer data, and you
are never asked to enter personal information.
:::

## Where this deliberately departs from best practice

A teaching environment is not a production one. These are the places this stack
knowingly does something you should not copy, and what you would do instead.

| Choice here | Why | What production would do |
|-------------|-----|---------------------------|
| **One Code Editor instance, not a redundant fleet.** An Auto Scaling group of exactly one, across three Availability Zones. | The editor serves one participant for one event. The ASG is there to survive a capacity shortfall at launch - it will try three AZs and four instance types - not to survive an instance failure mid-workshop. If the instance dies, redeploy the stack. | An Auto Scaling group of two or more behind a load balancer, with the workspace on a durable volume rather than instance storage. |
| **Plain HTTP from CloudFront to the instance.** | The origin has no certificate of its own, and the hop is inside AWS. The participant-facing leg is HTTPS-only and the origin only accepts traffic from CloudFront's origin-facing ranges. | An ACM certificate on the origin (via a load balancer) and `origin-protocol-policy: https-only`, so the internal hop is encrypted too. |
| **AWS-managed keys, not customer-managed, for the buckets and the log group.** (The Code Editor token *does* get a customer-managed key — see the controls table above.) | Both hold synthetic workshop data and are deleted with the stack. The token is different: it is the one real credential here, so it is worth the key policy. | Customer-managed KMS keys with explicit key policies and rotation, so you control the key lifecycle independently of the data. |
| **No CloudFront access logging on the editor distribution.** | This is a security choice, not a shortcut. The editor URL carries the connection token as a query parameter, and CloudFront's standard log record includes `cs-uri-query` — so enabling it would write a live credential into an S3 object on every request. | Keep the credential out of the URL (a session cookie or an authenticating proxy), then log freely. If the credential has to stay in the URL, use standard logging v2 and drop `cs-uri-query` from the selected fields. |
| **Indexing 100% of traces.** | Workshop volume is a few dozen invocations, and complete traces are the point of the observability section. | Sampling - 5% or a targeted rule - because at production volume full indexing is the dominant observability cost. |
| **A single participant identity with deploy permissions.** | One person per account, and the account is destroyed afterwards. | Separate build and deploy roles, assumed by CI rather than by a person, with the human identity holding read-only. |
| **No write action in your participant policy is granted on `Resource: "*"`.** Every one names an ARN. Two read statements keep a wildcard: the read-only discovery block, and one `kms:Decrypt` that `kms:ViaService` confines to Secrets Manager — a key cannot be named before the stack creates it, and the reachable set is already pinned by the ARN-scoped `secretsmanager:GetSecretValue` above it. | Two grants used to need it — `kms:CreateKey`, because a key cannot be named before it exists, and `aws-marketplace:Subscribe`, because Marketplace supports no resource-level permissions for it. Both turned out to be unnecessary *here*: `cdk bootstrap` and the account's first Bedrock call both run under the Code Editor's **EC2 instance role**, which holds them, not under your identity. So they were removed from your policy rather than justified. | Nothing — this is the state you want. The transferable lesson: when a grant looks like it has to be a wildcard, check which principal actually makes the call before writing the justification. |
| **The CDK deploy role holds `AdministratorAccess`.** This is not our choice: `agentcore deploy` runs `cdk bootstrap`, and AWS's bootstrap template attaches `AdministratorAccess` to the `cdk-hnb659fds-cfn-exec-role`, which CloudFormation then uses to deploy your agent stack. Your own participant policy is far narrower - every action named, no wildcards on writes - but anything you deploy through CDK is created by that admin role. | Bootstrapping with `--cloudformation-execution-policies` set to a policy scoped to exactly the resource types the stack creates, so the deploy role is not an administrator. Worth doing in any account you keep. | 

Nothing here is retained after the event; see [Cleanup](../090-cleanup/index.md).

## Where the code comes from

There is no external repository to clone and nothing for a facilitator to
pre-upload. The datasets, the Lambda handlers and the baseline agent source are
embedded in the tools template as compressed blobs, written to S3 when the stack
deploys, and synced into your workspace by the Code Editor. That is why Part 1 can
open `/workshop/repo/agent` immediately.

Next: [Part 0 - Your workshop environment](../020-part-0-environment/index.md).
