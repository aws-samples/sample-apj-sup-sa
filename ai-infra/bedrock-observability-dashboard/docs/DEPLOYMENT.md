# Deployment Guide

End-to-end instructions to deploy the Amazon Bedrock FM observability dashboard, from zero to a populated dashboard, and to upgrade between tiers later. Works in any account, any Bedrock-supported region, with any model set and any team-tagging convention.

> **TL;DR** — Pick a tier from the table, run the one matching `aws cloudformation deploy` command, open the `DashboardURL` output. Tiers upgrade in place: re-run with one more parameter, same stack, same URL.

---

## Contents

1. [How it works (1 minute)](#1-how-it-works)
2. [Choose your tier](#2-choose-your-tier)
3. [Prerequisites](#3-prerequisites)
4. [Tier 1 — deploy (metrics only)](#4-tier-1--deploy-metrics-only)
5. [Tier 2 — add per-tenant attribution](#5-tier-2--add-per-tenant-attribution)
6. [Tier 3 — add CloudTrail identity & routing](#6-tier-3--add-cloudtrail-identity--routing)
7. [Verify it's working](#7-verify-its-working)
8. [Populate it for a demo (optional)](#8-populate-it-for-a-demo-optional)
9. [Day-2 operations](#9-day-2-operations)
10. [Upgrade / downgrade between tiers](#10-upgrade--downgrade-between-tiers)
11. [Multi-region](#11-multi-region)
12. [Tear down](#12-tear-down)
13. [Production hardening](#13-production-hardening)
14. [Troubleshooting](#14-troubleshooting)
15. [Parameter & output reference](#15-parameter--output-reference)

---

## 1. How it works

One CloudFormation template — `cloudformation/dashboard.yaml` — builds a single multi-section CloudWatch dashboard plus alarms and saved Logs Insights queries. Which sections get created is decided by **which parameters you pass**:

- Pass nothing extra → **Tier 1**: CloudWatch metrics only (latency, errors, tokens, quota headroom).
- Add `ModelInvocationLogGroup` → **Tier 2**: per-tenant attribution from Bedrock Model Invocation Logs.
- Add `CloudTrailLogGroup` → **Tier 3**: caller identity, error reasons, and cross-region routing from CloudTrail.

An empty parameter means the dependent widgets/queries/alarms are **never created** — not created-and-blank. The dashboard you see has zero placeholders for capabilities you didn't opt into.

There is only ever **one stack per region** to think about. The `DeployedTier` stack output (`1`/`2`/`3`) tells you which tier materialised.

---

## 2. Choose your tier

| Tier | What you get | What you set (beyond `DashboardName`) | Est. cost @ ~10K calls/day |
|------|--------------|----------------------------------------|----------------------------|
| **1** | Overview, performance, errors/throttles, tokens, quota headroom. Throttle + 5xx alarms. | nothing | <$5/mo (CW metrics) |
| **2** | Tier 1 + per-tenant attribution + 5 Logs Insights queries + log-delivery-failure alarm | `ModelInvocationLogGroup` | + CW Logs ingest (~$0.50–$45/mo by volume) |
| **3** | Tier 2 + caller IAM identity + error reasons + cross-region routing (3 widgets, 3 queries) | `CloudTrailLogGroup` | + CW Logs ingest of CloudTrail **management** events (default-on, free to capture) |

> **Tier 3 is not the expensive "data events" path.** Bedrock runtime calls (`InvokeModel`/`Converse`/`ConverseStream`) are CloudTrail **management events** — captured by default at no per-event charge. You pay only to deliver them to CloudWatch Logs. Data events (billed per 100K) apply only to async/bidirectional/Agent/KB operations, which this dashboard does not cover.

Throughout this guide, replace the placeholders:

| Placeholder | Meaning |
|---|---|
| `<REGION>` | Your target region, e.g. `us-east-1`, `eu-west-1`, `ap-southeast-2` |
| `<ACCOUNT_ID>` | Your 12-digit AWS account ID |
| `<PARTITION>` | `aws` (commercial), `aws-us-gov` (GovCloud), or `aws-cn` (China) |
| `<your-invocation-log-group>` | CloudWatch Logs group receiving Bedrock invocation logs (Tier 2) |
| `<your-cloudtrail-log-group>` | CloudWatch Logs group receiving CloudTrail events (Tier 3) |

---

## 3. Prerequisites

### All tiers

- **AWS CLI v2**, authenticated for the target account/region: `aws sts get-caller-identity`.
- **Bedrock model access** granted in the target region (Bedrock console → *Model access*). Verify:
  ```bash
  aws bedrock list-foundation-models --region <REGION> --query 'modelSummaries[0].modelId'
  ```
- **IAM permission to deploy the stack** — create/update CloudWatch dashboards, alarms, and Logs query definitions. See `docs/iam-policy.md` for a least-privilege policy. Quick self-check:
  ```bash
  aws iam simulate-principal-policy \
    --policy-source-arn "$(aws sts get-caller-identity --query Arn --output text)" \
    --action-names cloudwatch:PutDashboard cloudwatch:PutMetricAlarm logs:PutQueryDefinition \
    --query 'EvaluationResults[].{action:EvalActionName,decision:EvalDecision}' --output table
  ```
- **`cfn-lint`** (optional, recommended) to validate before deploying: `pip install cfn-lint`.

### Tier 2 adds

- **Bedrock Model Invocation Logging** enabled in the region, delivering to a **CloudWatch Logs** group. (S3-only delivery cannot be queried by Logs Insights and will leave Tier 2 widgets empty.) See [§5](#5-tier-2--add-per-tenant-attribution) to enable it from scratch.
- *(Optional)* Application callers that tag `requestMetadata` with your chargeback key — see [§5](#5-tier-2--add-per-tenant-attribution).

### Tier 3 adds

- A **CloudTrail trail delivering management events to a CloudWatch Logs group**. If you don't have one, the repo ships `cloudformation/cloudtrail-logging.yaml` to create it — see [§6](#6-tier-3--add-cloudtrail-identity--routing).

### What you do **not** need

- No CDK / Terraform / Pulumi — this is plain CloudFormation.
- No Athena database, CUR export, or S3 data lake.
- No runtime IAM role for the dashboard itself — dashboards have no compute.

### Get the code

```bash
git clone <repository-url>
cd bedrock-llm-dashboard
cfn-lint cloudformation/dashboard.yaml   # optional: validate locally
```

---

## 4. Tier 1 — deploy (metrics only)

The fastest path. No data sources to enable; works the moment you have Bedrock traffic.

```bash
aws cloudformation deploy \
  --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard \
  --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides \
    DashboardName=Bedrock-FM-Dashboard
```

Get the dashboard link:

```bash
aws cloudformation describe-stacks --stack-name bedrock-fm-dashboard --region <REGION> \
  --query "Stacks[0].Outputs[?OutputKey=='DashboardURL'].OutputValue" --output text
```

Overview tiles go non-zero within ~2 minutes of any `Converse`/`InvokeModel` call. Sections 6 and 7 are not rendered at this tier.

**Optional Tier-1 knobs:**

```bash
  --parameter-overrides \
    DashboardName=Bedrock-FM-Dashboard \
    PrimaryModelId=us.anthropic.claude-sonnet-4-6 \   # header label only
    ThrottleRateAlarmThreshold=5 \                    # % over 5 min
    ServerErrorsAlarmThreshold=1 \                    # 5xx Sum over 5 min
    AlarmSnsTopicArn=arn:<PARTITION>:sns:<REGION>:<ACCOUNT_ID>:my-alerts
```

---

## 5. Tier 2 — add per-tenant attribution

Tier 2 adds Section 6 (top tenants by tokens, tokens by model+operation, guardrail interventions, log-delivery health) and 5 saved Logs Insights queries.

### 5a. Enable Model Invocation Logging (one-time, per region)

Skip if `aws bedrock get-model-invocation-logging-configuration --region <REGION>` already shows a `cloudWatchConfig` with `textDataDeliveryEnabled: true`.

```bash
REGION=<REGION>
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
LOG_GROUP=/aws/bedrock/model-invocations
ROLE_NAME=bedrock-modelinvocation-logging-${REGION}

# 1) Log group + retention
aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION"
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 30 --region "$REGION"

# 2) IAM role Bedrock assumes to write logs
cat > /tmp/trust.json <<EOF
{ "Version": "2012-10-17", "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "bedrock.amazonaws.com" },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": { "aws:SourceAccount": "$ACCOUNT_ID" },
      "ArnLike": { "aws:SourceArn": "arn:aws:bedrock:$REGION:$ACCOUNT_ID:*" } } }] }
EOF
cat > /tmp/policy.json <<EOF
{ "Version": "2012-10-17", "Statement": [{
    "Effect": "Allow",
    "Action": ["logs:CreateLogStream","logs:PutLogEvents"],
    "Resource": "arn:aws:logs:$REGION:$ACCOUNT_ID:log-group:$LOG_GROUP:log-stream:aws/bedrock/modelinvocations" }] }
EOF
aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document file:///tmp/trust.json
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name BedrockModelInvocationLoggingPolicy --policy-document file:///tmp/policy.json
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)

# 3) Turn logging on (text only; image/embedding/video go to S3 if enabled)
aws bedrock put-model-invocation-logging-configuration --region "$REGION" \
  --logging-config "{
    \"cloudWatchConfig\": { \"logGroupName\": \"$LOG_GROUP\", \"roleArn\": \"$ROLE_ARN\" },
    \"textDataDeliveryEnabled\": true,
    \"imageDataDeliveryEnabled\": false,
    \"embeddingDataDeliveryEnabled\": false,
    \"videoDataDeliveryEnabled\": false }"
```

> **PII warning:** invocation logs contain full prompt/response text and `requestMetadata` in plain text. Don't log sensitive modalities you don't need, and never put PII/secrets in `requestMetadata`.

### 5b. Deploy Tier 2

```bash
aws cloudformation deploy \
  --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard \
  --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides \
    DashboardName=Bedrock-FM-Dashboard \
    ModelInvocationLogGroup=/aws/bedrock/model-invocations \
    TenantMetadataKey=team
```

`TenantMetadataKey` is the `requestMetadata` key the per-tenant widgets group by. Default `team`; set it to whatever you use for chargeback (`tenant`, `cost_center`, `business_unit`, …) — the widgets and the 3 tenant-scoped saved queries all follow it.

### 5c. Tag your callers (unlocks the per-tenant widgets)

Until callers tag `requestMetadata`, the per-tenant widgets render with headers but no rows. In application code:

```python
import boto3
client = boto3.client("bedrock-runtime")

resp = client.converse(
    modelId="us.anthropic.claude-sonnet-4-6",
    messages=[...],
    requestMetadata={        # key must match TenantMetadataKey
        "team": "platform",
        "app":  "chatbot-v2",
        "env":  "prod",
    },
)
```

Limits: up to 16 keys, ≤256 chars each. **No PII** — values are plain-text in logs and CloudTrail.

First Logs Insights results land ~3 minutes after tagged traffic begins.

---

## 6. Tier 3 — add CloudTrail identity & routing

Tier 3 adds Section 7: top callers by IAM identity, throttle/error reasons by caller, and cross-region inference routing (the routed region lives **only** in CloudTrail — `additionalEventData.inferenceRegion`).

### 6a. Make sure a CloudTrail → CloudWatch Logs group exists

**If you already have a trail delivering management events to CloudWatch Logs**, note its log-group name and skip to [§6c](#6c-deploy-tier-3). Confirm:

```bash
aws cloudtrail describe-trails --region <REGION> \
  --query 'trailList[].{Name:Name,CWLogGroupArn:CloudWatchLogsLogGroupArn}' --output table
```
A non-null `CWLogGroupArn` is what you want.

### 6b. If you don't have one — deploy the prerequisite stack

`cloudformation/cloudtrail-logging.yaml` provisions the log group (and optionally the trail) in four modes. It is **multi-region** — deploy it **once** (its IAM role name is global and would collide on a second regional deploy).

```bash
# Greenfield (default): new trail + S3 bucket + log group + delivery role
aws cloudformation deploy \
  --template-file cloudformation/cloudtrail-logging.yaml \
  --stack-name bedrock-cloudtrail-logging \
  --region <REGION> --no-fail-on-empty-changeset \
  --capabilities CAPABILITY_NAMED_IAM
```

Other modes (add `--parameter-overrides`):

| Situation | Override |
|---|---|
| Reuse an existing CloudTrail S3 bucket | `ExistingTrailBucketName=<bucket>` (your bucket policy must already allow this trail — see `docs/setup.md`) |
| Trail managed by another team / Organization | `CreateTrail=false` (creates **only** the log group + role; wire them to your trail via the `DeliveryRoleArn` and `CloudTrailLogGroupArn` outputs) |
| Encrypt with a customer-managed KMS key | `KmsKeyArn=arn:<PARTITION>:kms:<REGION>:<ACCOUNT_ID>:key/<key-id>` (key policy must allow CloudTrail + CloudWatch Logs) |
| Names collide with existing resources | `TrailName=<name> LogGroupName=/aws/cloudtrail/<name>` |

Read the log-group name to pass on:

```bash
aws cloudformation describe-stacks --stack-name bedrock-cloudtrail-logging --region <REGION> \
  --query "Stacks[0].Outputs[?OutputKey=='CloudTrailLogGroup'].OutputValue" --output text
```

> A brand-new trail takes ~10–15 minutes for its first delivery to CloudWatch Logs; Section 7 stays empty until then.

### 6c. Deploy Tier 3

```bash
aws cloudformation deploy \
  --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard \
  --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides \
    DashboardName=Bedrock-FM-Dashboard \
    ModelInvocationLogGroup=/aws/bedrock/model-invocations \
    TenantMetadataKey=team \
    CloudTrailLogGroup=<your-cloudtrail-log-group>
```

---

## 7. Verify it's working

```bash
# 1) Which tier materialised?
aws cloudformation describe-stacks --stack-name bedrock-fm-dashboard --region <REGION> \
  --query "Stacks[0].Outputs[?OutputKey=='DeployedTier'].OutputValue" --output text

# 2) Metrics arriving (non-zero within ~2 min of a call)
aws cloudwatch list-metrics --namespace AWS/Bedrock --region <REGION> --query 'length(Metrics)'

# 3) (Tier 2) Invocation logs delivered
aws logs filter-log-events --log-group-name /aws/bedrock/model-invocations \
  --region <REGION> --max-items 3 --query 'events[].message'

# 4) (Tier 3) CloudTrail events queryable — run the routing query
QID=$(aws logs start-query --region <REGION> \
  --log-group-name <your-cloudtrail-log-group> \
  --start-time $(($(date +%s)-3600)) --end-time $(date +%s) \
  --query-string 'filter eventSource = "bedrock.amazonaws.com" and ispresent(additionalEventData.inferenceRegion) | stats count(*) as calls by requestParameters.modelId, additionalEventData.inferenceRegion | sort calls desc' \
  --query queryId --output text)
sleep 8 && aws logs get-query-results --region <REGION> --query-id "$QID" --query '{Status:status,Rows:length(results)}'

# 5) Open the dashboard
aws cloudformation describe-stacks --stack-name bedrock-fm-dashboard --region <REGION> \
  --query "Stacks[0].Outputs[?OutputKey=='DashboardURL'].OutputValue" --output text
```

Expected data-arrival timeline after real traffic: Overview ~2 min · Logs Insights (Tier 2) ~3 min · CloudTrail widgets (Tier 3) ~5–15 min.

---

## 8. Populate it for a demo (optional)

No real workload yet? The repo ships a synthetic generator that lights up every widget within ~10 minutes.

```bash
cd data-generator
pip install -r requirements.txt
# Optional portability: target a non-us region / custom models / custom tenant key
export AWS_REGION=<REGION>
export TENANT_METADATA_KEY=team   # match the dashboard's TenantMetadataKey
nohup python3 -u generate.py > generator.log 2>&1 < /dev/null & disown
echo $! > generator.pid
tail -f generator.log
```

Cost ≈ $15–25 over a 24h run at us-east-1 list prices. Lifecycle (start/monitor/stop/restart) → `data-generator/OPERATIONS.md`. Note: the generator's calls appear in CloudTrail under **its own** IAM identity, so Tier-3 identity widgets reflect the generator, not your real callers.

---

## 9. Day-2 operations

| To do this | Action |
|---|---|
| Change the chargeback key | Set `TenantMetadataKey=<key>`, re-deploy |
| Raise a noisy alarm threshold | Set `ThrottleRateAlarmThreshold` / `ServerErrorsAlarmThreshold`, re-deploy |
| Route alarms to SNS | Set `AlarmSnsTopicArn=<topic-arn>`, re-deploy |

---

## 10. Upgrade / downgrade between tiers

Upgrading is **one re-deploy, not a teardown** — same stack name, same dashboard URL, existing widgets untouched.

```bash
# Tier 1 -> 2: enable logging (§5a), then add the parameter
aws cloudformation deploy --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides DashboardName=Bedrock-FM-Dashboard \
                        ModelInvocationLogGroup=/aws/bedrock/model-invocations

# Tier 2 -> 3: add the CloudTrail log group
aws cloudformation deploy --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides DashboardName=Bedrock-FM-Dashboard \
                        ModelInvocationLogGroup=/aws/bedrock/model-invocations \
                        CloudTrailLogGroup=<your-cloudtrail-log-group>
```

**Downgrade** works too: re-deploy with the parameter blanked (e.g. `CloudTrailLogGroup=`) and CloudFormation deletes only the gated resources. The stack name and dashboard URL stay constant.

> Because `aws cloudformation deploy` replaces the parameter set each run, **pass every parameter you still want** on each deploy — an omitted override reverts to the template default (e.g. dropping `ModelInvocationLogGroup` silently downgrades you to Tier 1).

---

## 11. Multi-region

CloudWatch dashboards are regional. To cover N regions, deploy the **dashboard** stack once per region, each with that region's own `ModelInvocationLogGroup`.

The **CloudTrail prerequisite** stack is different: its trail is multi-region, so deploy `cloudtrail-logging.yaml` **once** total. Then point each regional dashboard's `CloudTrailLogGroup` at that one log group.

---

## 12. Tear down

```bash
# Dashboard (removes dashboard, alarms, saved queries — all tiers, one stack)
aws cloudformation delete-stack --stack-name bedrock-fm-dashboard --region <REGION>

# CloudTrail prerequisite (if you deployed it). The trail bucket has VERSIONING
# ENABLED, so you must delete ALL OBJECT VERSIONS (not just the current objects)
# before delete-stack will succeed. `aws s3 rm --recursive` only removes current
# objects — it leaves the noncurrent versions and delete-markers behind, and the
# stack delete then fails on the still-non-empty bucket.
TRAIL_BUCKET=$(aws cloudformation describe-stacks --stack-name bedrock-cloudtrail-logging \
  --region <REGION> --query "Stacks[0].Outputs[?OutputKey=='TrailBucketName'].OutputValue" --output text)

if [ -n "$TRAIL_BUCKET" ]; then
  # Delete every object version and delete-marker in the bucket
  aws s3api list-object-versions --bucket "$TRAIL_BUCKET" --region <REGION> \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json > /tmp/versions.json
  aws s3api delete-objects --bucket "$TRAIL_BUCKET" --region <REGION> --delete file:///tmp/versions.json 2>/dev/null || true
  aws s3api list-object-versions --bucket "$TRAIL_BUCKET" --region <REGION> \
    --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json > /tmp/markers.json
  aws s3api delete-objects --bucket "$TRAIL_BUCKET" --region <REGION> --delete file:///tmp/markers.json 2>/dev/null || true
fi

aws cloudformation delete-stack --stack-name bedrock-cloudtrail-logging --region <REGION>
```

> **Why the extra steps?** The trail bucket is created with `VersioningConfiguration: Status: Enabled`
> (a security best practice for audit-log storage — it makes log tampering detectable). The trade-off
> is that a versioned bucket is not "empty" until every object *version* and *delete-marker* is removed,
> so a plain `aws s3 rm --recursive` is not enough for teardown. The commands above purge versions and
> markers first. (This only applies to the **greenfield** trail bucket this stack created — if you used
> bring-your-own-bucket or bring-your-own-trail mode, there is no bucket for this stack to delete.)

Deleting the dashboard stack does **not**: disable Bedrock invocation logging, delete the invocation-log group, or remove the IAM role from §5a. Clean those up separately if desired:

```bash
aws bedrock delete-model-invocation-logging-configuration --region <REGION>
aws logs delete-log-group --log-group-name /aws/bedrock/model-invocations --region <REGION>
aws iam delete-role-policy --role-name bedrock-modelinvocation-logging-<REGION> --policy-name BedrockModelInvocationLoggingPolicy
aws iam delete-role --role-name bedrock-modelinvocation-logging-<REGION>
```

---

## 13. Production hardening

The dashboard itself (Tiers 1-3) creates only CloudWatch dashboards, alarms, and Logs query definitions — no data-plane resources to harden. The hardening notes below apply to the optional **CloudTrail prerequisite** (`cloudformation/cloudtrail-logging.yaml`) and to your own account standards.

**Already built into the templates:**
- Trail S3 bucket: public access fully blocked, ACLs disabled (`BucketOwnerEnforced`), SSE encryption (SSE-S3 by default, or SSE-KMS via `KmsKeyArn`), **versioning enabled**, and a 90-day lifecycle rule.
- CloudTrail: `EnableLogFileValidation: true` (tamper-evident digest files), multi-region trail.
- IAM delivery role: scoped to the specific log-group log-stream ARN, not `*`.
- Optional CMK encryption (`KmsKeyArn`) for the log group, bucket, and trail.

**Consider before production use:**
- **Enable S3 server access logging** on the trail bucket. The template does **not** configure this today (Checkov flags it as `CKV_AWS_18`). To add it, give the `TrailBucket` resource a `LoggingConfiguration` pointing at a **separate** access-log bucket you own — do not point it at itself (that creates a CloudFormation circular dependency). The clean pattern is an optional `AccessLogBucketName` parameter + a `LoggingConfiguration: !If [HasAccessLogBucket, {...}, !Ref 'AWS::NoValue']` block.
- **Deny non-TLS requests** on the trail bucket. Add a bucket-policy statement that denies any request where `aws:SecureTransport` is `false`:
  ```json
  { "Sid": "DenyInsecureTransport", "Effect": "Deny", "Principal": "*",
    "Action": "s3:*", "Resource": ["arn:<PARTITION>:s3:::<BUCKET>", "arn:<PARTITION>:s3:::<BUCKET>/*"],
    "Condition": { "Bool": { "aws:SecureTransport": "false" } } }
  ```
- **Use a customer-managed KMS key** (`KmsKeyArn`) rather than the AWS-managed default, if your org requires CMK control over the log group, bucket, and trail.
- **Review the IAM deploy policy** (`docs/iam-policy.md`) against your org's least-privilege standards before granting it to a deploy principal.
- **Set log-group retention** (`LogRetentionInDays`) and the bucket lifecycle to match your compliance retention requirements (defaults are 30 days / 90 days).
- **Route alarms to SNS** (`AlarmSnsTopicArn`) so throttle / 5xx / log-delivery alarms actually notify someone.
- **Pin `DashboardName`** per environment (dev/stage/prod) so alarm and query-definition names don't collide across stacks in the same account.

---

## 14. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| All widgets empty | No Bedrock traffic yet, or wrong region | Make a `Converse` call; confirm CLI region matches the stack region |
| Section 6/7 not present | That tier's parameter wasn't passed | Check `DeployedTier` output; re-deploy with the parameter |
| Per-tenant widgets render but have no rows | Callers aren't tagging `requestMetadata`, or `TenantMetadataKey` ≠ the key they tag | Tag calls (§5c) with the key matching `TenantMetadataKey` |
| Tier-2 widgets empty despite logging on | Logs delivered to **S3 only**, not CloudWatch Logs | Add a CloudWatch Logs destination (Logs Insights can't read S3) |
| Section 7 empty at Tier 3 | CloudTrail→CW Logs delivery lag (new trail), or wrong eventSource | Wait ~15 min; confirm events use `eventSource = bedrock.amazonaws.com` |
| `CREATE_FAILED` on `AWS::Logs::QueryDefinition` | A query name collides in the account | Change `DashboardName` (query names are scoped by it) |
| CloudTrail stack rollback fails on bucket delete | Bucket has objects | Empty the bucket (§12), then delete the stack |
| Throttle alarm fires constantly | Threshold too low for your traffic shape | Re-deploy with a higher `ThrottleRateAlarmThreshold` |

---

## 15. Parameter & output reference

### `cloudformation/dashboard.yaml` parameters

| Parameter | Default | Purpose |
|---|---|---|
| `DashboardName` | `Bedrock-FM-Dashboard` | Dashboard name; also scopes alarm and saved-query names (keep unique per account) |
| `ModelInvocationLogGroup` | `''` | Empty = Tier 1. Set to a CloudWatch Logs group to reach Tier 2 |
| `CloudTrailLogGroup` | `''` | Empty = Tier 1/2. Set to a CloudWatch Logs group to reach Tier 3 |
| `TenantMetadataKey` | `team` | `requestMetadata` key the per-tenant widgets group by |
| `PrimaryModelId` | `us.anthropic.claude-sonnet-4-6` | Shown in the Section-1 header title only |
| `ThrottleRateAlarmThreshold` | `5` | Throttle rate (%) over 5 min that triggers the alarm |
| `ServerErrorsAlarmThreshold` | `1` | 5xx (`InvocationServerErrors`) Sum over 5 min that triggers the alarm |
| `AlarmSnsTopicArn` | `''` | Optional SNS topic ARN for alarm notifications |

### `cloudformation/dashboard.yaml` outputs

| Output | Meaning |
|---|---|
| `DashboardURL` | Deep link to the deployed dashboard |
| `ModelInvocationLogGroupUsed` | Log group powering the Logs Insights widgets |
| `DeployedTier` | `1`, `2`, or `3` — which tier the parameters produced |

### `cloudformation/cloudtrail-logging.yaml` parameters

| Parameter | Default | Purpose |
|---|---|---|
| `TrailName` | `bedrock-dashboard-trail` | Trail name (greenfield/BYO-bucket) and seed for the delivery-role name |
| `LogGroupName` | `/aws/cloudtrail/bedrock-dashboard` | Log group to create; pass this to the dashboard as `CloudTrailLogGroup` |
| `LogRetentionInDays` | `30` | Retention for the log group |
| `CreateTrail` | `true` | `false` = create only the log group + delivery role (BYO trail) |
| `ExistingTrailBucketName` | `''` | Reuse an existing S3 bucket instead of creating one |
| `KmsKeyArn` | `''` | Optional CMK to encrypt log group / bucket / trail |

### `cloudformation/cloudtrail-logging.yaml` outputs

| Output | Meaning |
|---|---|
| `CloudTrailLogGroup` | Pass to the dashboard as `CloudTrailLogGroup` |
| `CloudTrailLogGroupArn` | For BYO-trail mode: set as your trail's `CloudWatchLogsLogGroupArn` |
| `DeliveryRoleArn` | For BYO-trail mode: set as your trail's `CloudWatchLogsRoleArn` |
| `TrailName` / `TrailBucketName` | Created trail / bucket names (when applicable) |

---

For panel-by-panel metric definitions see `docs/architecture.md`; for the data-source trade-offs (metrics vs invocation logs vs CloudTrail) see `docs/data-sources-comparison.md`.
