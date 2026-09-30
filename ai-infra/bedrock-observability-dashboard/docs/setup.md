# Setup & Deploy

## 0. Prereqs

- AWS account with Bedrock model access in your target region
- IAM permissions to create CloudWatch dashboards, alarms, log query definitions (see `docs/iam-policy.md`)
- AWS CLI v2 authenticated for the target account/region

## 1. (Tier 2) Verify (or enable) Model Invocation Logging

This dashboard's per-tenant Logs Insights widgets need Bedrock Model Invocation Logging delivering to a **CloudWatch Logs** group (S3-only delivery cannot be queried by Logs Insights). Skip this section for a Tier 1 (metrics-only) deploy.

```bash
aws bedrock get-model-invocation-logging-configuration --region <REGION>
```

### Already enabled
You'll get JSON like:
```json
{
  "loggingConfig": {
    "cloudWatchConfig": { "logGroupName": "/aws/bedrock/model-invocation-logs", "roleArn": "arn:aws:iam::...:role/..." },
    "textDataDeliveryEnabled": true,
    "imageDataDeliveryEnabled": false,
    "embeddingDataDeliveryEnabled": false,
    "videoDataDeliveryEnabled": false
  }
}
```
Note the `logGroupName` — you'll pass it as `ModelInvocationLogGroup`.

### Not enabled — turn it on

```bash
REGION=us-east-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
LOG_GROUP=/aws/bedrock/model-invocations
ROLE_NAME=bedrock-modelinvocation-logging-${REGION}

# 1) Log group
aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION"
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 30 --region "$REGION"

# 2) IAM role for Bedrock to write to CW Logs
cat > /tmp/trust.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "bedrock.amazonaws.com" },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals":   { "aws:SourceAccount": "$ACCOUNT_ID" },
      "ArnLike":        { "aws:SourceArn":     "arn:aws:bedrock:$REGION:$ACCOUNT_ID:*" }
    }
  }]
}
EOF

cat > /tmp/policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["logs:CreateLogStream","logs:PutLogEvents"],
    "Resource": "arn:aws:logs:$REGION:$ACCOUNT_ID:log-group:$LOG_GROUP:log-stream:aws/bedrock/modelinvocations"
  }]
}
EOF

aws iam create-role --role-name "$ROLE_NAME" \
  --assume-role-policy-document file:///tmp/trust.json
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name BedrockModelInvocationLoggingPolicy \
  --policy-document file:///tmp/policy.json

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)

# 3) Tell Bedrock to start logging
aws bedrock put-model-invocation-logging-configuration \
  --region "$REGION" \
  --logging-config "{
    \"cloudWatchConfig\": { \"logGroupName\": \"$LOG_GROUP\", \"roleArn\": \"$ROLE_ARN\" },
    \"textDataDeliveryEnabled\": true,
    \"imageDataDeliveryEnabled\": false,
    \"embeddingDataDeliveryEnabled\": false,
    \"videoDataDeliveryEnabled\": false
  }"
```

> Toggle `imageDataDeliveryEnabled` / `embeddingDataDeliveryEnabled` / `videoDataDeliveryEnabled` to `true` only if you want to log those modalities. Image and video bodies are heavy — they go to S3, not CW Logs.

## 2. (Tier 3) CloudTrail

Standard runtime calls — `InvokeModel`, `InvokeModelWithResponseStream`, `Converse`, `ConverseStream` — are recorded as CloudTrail **management events** (on by default, no per-event data-event charge; `eventSource = bedrock.amazonaws.com`). You only need a trail delivering management events to a CloudWatch Logs group; pass that group as `CloudTrailLogGroup`. **No data-event selector is required for this dashboard.**

### Which path fits your account?

The dashboard only needs a **CloudWatch Logs group** that a trail delivers Bedrock management events to. How you get one depends on what you already have:

| Your situation | What to do |
|---|---|
| You already have a trail delivering to CloudWatch Logs | **Skip the prerequisite stack.** Pass that existing log-group name to the dashboard as `CloudTrailLogGroup`. |
| You have no trail, or a trail that only delivers to S3 | Deploy `cloudformation/cloudtrail-logging.yaml` in **greenfield** mode (creates trail + bucket + log group + role). |
| You have a central CloudTrail S3 bucket to reuse | Deploy the prerequisite with `ExistingTrailBucketName=<bucket>` (creates the trail + log group + role, reuses your bucket — no new bucket, no policy change). |
| Your trail is managed by another team / Organization | Deploy the prerequisite with `CreateTrail=false` (creates **only** the log group + delivery role); attach them to your trail using the `DeliveryRoleArn` and `CloudTrailLogGroupArn` outputs. |

> The trail is **multi-region** — deploy the prerequisite **once** (its IAM role name is global and would collide on a second regional deploy). One trail captures Bedrock calls across all regions into the one log group.

### Deploy the prerequisite stack

```bash
# Greenfield (default): new trail + bucket + log group + role
aws cloudformation deploy \
  --template-file cloudformation/cloudtrail-logging.yaml \
  --stack-name bedrock-cloudtrail-logging \
  --region <REGION> --no-fail-on-empty-changeset \
  --capabilities CAPABILITY_NAMED_IAM
```

Other modes — add `--parameter-overrides`:

```bash
# Reuse an existing S3 bucket (its bucket policy must already allow this trail — see below)
  --parameter-overrides ExistingTrailBucketName=my-central-cloudtrail-bucket

# Log group + delivery role only; wire them to your own trail
  --parameter-overrides CreateTrail=false

# Encrypt log group / bucket / trail with a customer-managed KMS key
  --parameter-overrides KmsKeyArn=arn:aws:kms:<REGION>:<ACCOUNT_ID>:key/<key-id>

# Override names if the defaults collide with existing resources
  --parameter-overrides TrailName=my-bedrock-trail LogGroupName=/aws/cloudtrail/my-bedrock
```

The stack's `CloudTrailLogGroup` output is the value you pass to the dashboard's `CloudTrailLogGroup` parameter (the Tier 3 deploy in section 3). CloudTrail → CloudWatch Logs delivery for a brand-new trail typically lands within ~10–15 minutes, after which Section 7 populates.

**Prerequisites the template cannot grant for you (by design — they touch resources you own):**

- **`ExistingTrailBucketName` mode** — your bucket policy must already permit this trail. Add (replacing `<…>`):
  ```json
  { "Sid": "AWSCloudTrailAclCheck", "Effect": "Allow",
    "Principal": { "Service": "cloudtrail.amazonaws.com" },
    "Action": "s3:GetBucketAcl", "Resource": "arn:<PARTITION>:s3:::<BUCKET>",
    "Condition": { "StringEquals": { "aws:SourceArn": "arn:<PARTITION>:cloudtrail:<REGION>:<ACCOUNT_ID>:trail/<TRAIL_NAME>" } } },
  { "Sid": "AWSCloudTrailWrite", "Effect": "Allow",
    "Principal": { "Service": "cloudtrail.amazonaws.com" },
    "Action": "s3:PutObject", "Resource": "arn:<PARTITION>:s3:::<BUCKET>/AWSLogs/<ACCOUNT_ID>/*",
    "Condition": { "StringEquals": { "aws:SourceArn": "arn:<PARTITION>:cloudtrail:<REGION>:<ACCOUNT_ID>:trail/<TRAIL_NAME>" } } }
  ```
- **`KmsKeyArn` mode** — the key policy must allow `cloudtrail.amazonaws.com` (`kms:GenerateDataKey*`, `kms:DescribeKey`) and CloudWatch Logs (`logs.<REGION>.amazonaws.com` via `kms:Encrypt*`/`Decrypt*`/`GenerateDataKey*`/`Describe*`) to use it.
- **Governed environments** — the named IAM role requires `CAPABILITY_NAMED_IAM`; SCPs or Control Tower may block trail/IAM creation, in which case use `CreateTrail=false` and have your platform team wire the outputs.

### Data events (out of scope for this dashboard)

If you separately want async / bidirectional / Agent / KB / Flow / Guardrail operations (which ARE data events), add a selector such as:

```bash
aws cloudtrail put-event-selectors --trail-name <TRAIL> --advanced-event-selectors '[
  { "Name": "Bedrock model data events",
    "FieldSelectors": [
      { "Field": "eventCategory", "Equals": ["Data"] },
      { "Field": "resources.type", "Equals": ["AWS::Bedrock::Model"] } ] } ]'
```
Those operations are out of scope for this dashboard's widgets, but the trail will capture them.

## 3. Deploy the dashboard

Pick the tier you want. Tiers are additive and upgrade in place — re-deploy with one more parameter and the dashboard URL stays the same.

### Tier 1 — CloudWatch metrics only

```bash
aws cloudformation deploy \
  --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard \
  --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides \
    DashboardName=Bedrock-FM-Dashboard
```

### Tier 2 — add Model Invocation Logs (per-tenant attribution)

```bash
aws cloudformation deploy \
  --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard \
  --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides \
    DashboardName=Bedrock-FM-Dashboard \
    ModelInvocationLogGroup=<your-invocation-log-group> \
    TenantMetadataKey=team
```

### Tier 3 — add CloudTrail (IAM identity, routing, error reasons)

```bash
aws cloudformation deploy \
  --template-file cloudformation/dashboard.yaml \
  --stack-name bedrock-fm-dashboard \
  --region <REGION> --no-fail-on-empty-changeset \
  --parameter-overrides \
    DashboardName=Bedrock-FM-Dashboard \
    ModelInvocationLogGroup=<your-invocation-log-group> \
    CloudTrailLogGroup=<your-cloudtrail-log-group>
```

The output `DashboardURL` jumps straight to the deployed dashboard; `DeployedTier` confirms which tier (1/2/3) materialised.

## 4. Tag your callers (unlocks per-tenant widgets)

Without `requestMetadata`, the per-team widgets stay empty. In your application code:

```python
import boto3
client = boto3.client("bedrock-runtime")

resp = client.converse(
    modelId="us.anthropic.claude-sonnet-4-6",
    messages=[...],
    requestMetadata={
        "team":  "orchestrator",
        "app":   "chatbot-v2",
        "env":   "prod",
    },
)
```

Limits: 16 keys, ≤256 chars each. **Don't put PII here** — the value is plain-text in logs and CloudTrail.

## 5. Multi-region

Deploy one stack per region. Dashboards are regional in CloudWatch.

## 6. Tear down

```bash
aws cloudformation delete-stack --stack-name bedrock-fm-dashboard --region <REGION>
```

If you also deployed the **CloudTrail prerequisite** (`cloudformation/cloudtrail-logging.yaml`) in greenfield mode, its S3 trail bucket has **versioning enabled**. You must delete all object *versions* and *delete-markers* before the stack will delete — `aws s3 rm --recursive` alone is **not** enough. See the version-aware teardown commands in `docs/DEPLOYMENT.md` → "Tear down".
