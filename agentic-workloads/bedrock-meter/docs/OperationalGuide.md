[English](OperationalGuide.md) | [한국어](OperationalGuide.ko.md)

# Bedrock Meter Operational Guide

This guide covers daily Bedrock Meter operations: limits and exemptions, warnings, unblocking,
console accounts, and troubleshooting.

See [README.md](../README.md) for installation/deployment,
[DeveloperGuide.md](DeveloperGuide.md) for code and AWS architecture, and [API.md](API.md) for
the complete API reference.

## 1. Operator Essentials

1. Bedrock Meter uses **Bedrock invocation logs**, not a proxy.
2. After a call executes, logs arrive, usage accumulates, and an IAM deny can be attached.
3. Daily blocking typically takes 5–30 seconds; weekly/monthly blocking takes up to about five minutes.
4. IAM propagation can add seconds, so zero overshoot is not guaranteed.
5. Limits use relative-cost `credits`, not a simple sum of raw tokens.
6. Notifications go to configured SNS operator emails, not individual users.
7. Always use the unblock API rather than manually deleting only the IAM deny.
8. `exempt` usage remains visible, but warnings and blocks are skipped.

## 2. Prepare Operational Access

Set deployment outputs as environment variables for CLI operations:

```bash
export AWS_REGION=ap-northeast-2
export BEDROCK_METER_API_ENDPOINT="$(
  jq -r '.BedrockMeterCoreStack.CoreApiEndpoint' cdk-outputs.json
)"
export BEDROCK_METER_CONSOLE_URL="$(
  jq -r '.BedrockMeterCoreStack.ConsoleUrl' cdk-outputs.json
)"
export BEDROCK_METER_USER_POOL_ID="$(
  jq -r '.BedrockMeterCoreStack.ConsoleUserPoolId' cdk-outputs.json
)"
```

Core API CLI dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install boto3 requests requests-aws4auth
```

Check your current AWS identity:

```bash
aws sts get-caller-identity
```

Core API admin routes require one of:

- An IAM User included in deployment configuration `BEDROCK_METER_ADMIN_USERNAMES`
- The deployed Console BFF execution role

## 3. Console Permissions

| Cognito group | Permissions |
|---|---|
| `console-admins` | Read, change limits/exemptions, unblock |
| `console-viewers` | Read only |

The console uses username/password authentication.

- Self-signup: Disabled
- MFA: Disabled
- Password: At least seven characters, including one digit
- Session tokens: Browser session storage

### Create an admin or reset a password

Rerunning the same command updates an existing user's password and group.

```bash
scripts/add-console-admin.sh <iam-username> <email>
```

### Create a viewer or demote an admin

```bash
scripts/add-console-viewer.sh <iam-username> <email>
```

The script removes the user from the opposite group before adding the target group, so it also
supports changing an admin to a viewer.

The command succeeds only after reading all group memberships and confirming that the target
group is present and the opposite group is absent. A failed removal stops the command before
the target group is added. An add or verification failure also returns a nonzero exit status;
changes may already be partially applied. Inspect the user's groups before retrying. The command
does not restore the old admin group automatically.

This verifies Cognito group membership, not immediate revocation of existing console sessions.
The current BFF still trusts groups in previously issued tokens until they expire, and
`CONSOLE_ALLOWED_USERS` can independently grant admin access. Account/session checks and removal
of that authorization exception are separate changes.

### Disable a user

```bash
aws cognito-idp admin-user-global-sign-out \
  --user-pool-id "$BEDROCK_METER_USER_POOL_ID" \
  --username alice \
  --region "$AWS_REGION"

aws cognito-idp admin-disable-user \
  --user-pool-id "$BEDROCK_METER_USER_POOL_ID" \
  --username alice \
  --region "$AWS_REGION"
```

Reenable:

```bash
aws cognito-idp admin-enable-user \
  --user-pool-id "$BEDROCK_METER_USER_POOL_ID" \
  --username alice \
  --region "$AWS_REGION"
```

### Cognito and IAM username rules

Cognito accounts and IAM Users are not linked accounts. The Console BFF calls Core using its
Lambda role, not an IAM User.

Console admins can change IAM User limits and `exempt`, including for themselves. Cognito and
IAM usernames need not match. Audit records the console operator responsible for the change.

## 4. Credits and Periods

Default calculation:

```text
credits =
  raw input × 1
  + cache read × 0.1
  + cache write × 1.25
  + output × 5
```

| Period | Definition | Evaluation |
|---|---|---|
| DAY | 00:00–23:59 in the global quota timezone | Immediately after invocation log ingestion |
| WEEK | ISO week, Monday–Sunday, in that timezone | Every five minutes |
| MONTH | Calendar month in that timezone | Every five minutes |

Check the current timezone on Overview cards and **Global Quota Calendar** under Limits.
Athena time axes in Statistics and User Detail history remain UTC, so chart days can differ
from quota DAY boundaries.

Default limits:

| Scope | Limit | Warning |
|---|---:|---:|
| DAY | 3,000,000 | 2,400,000 |
| WEEK | 15,000,000 | 12,000,000 |
| MONTH | 45,000,000 | 36,000,000 |

## 5. Check Usage

### Console

- Overview: Overall usage, warning/blocked counts, recent events
- Users: Per-user day/week/month quota pressure
- User Detail: Token types, trends, effective limits, state
- Statistics: Model trends and invocations
- Audit: Warnings, blocks, limit changes, unblocks

| Page | Data source | Contents |
|---|---|---|
| Overview | DynamoDB | Overall day/week/month, warning/blocked counts, top users, 30-day overall trends, audit |
| Users | DynamoDB | Current per-user day/week/month and state |
| User Detail | DynamoDB + Athena | Current snapshot/limits/state from DynamoDB; daily/hourly history from Athena |
| Statistics | Athena | Model totals and daily/hourly trends |
| Limits | DynamoDB | Global quota timezone, effective limits, per-user overrides |
| Blocked | DynamoDB | Users blocked according to STATE and their current usage |
| Audit | DynamoDB | Warnings, blocks, resolutions, limit changes, force-unblocks |

DynamoDB APIs:

- Current/overall usage: `/usage/me`, `/usage/{username}`, `/users`,
  `/users/{username}`, `/usage/aggregate`, `/usage/history/all`
- Limits and operations: `/limits/me`, `/limits/{username}`, `/admin/blocked`,
  `/admin/unblock/{username}`, `/admin/audit`, `/admin/settings/quota`

Athena APIs:

- User history: `/usage/me/history`, `/usage/{username}/history`, `/users/{username}/history`
- Analytics: `/usage/top`, `/usage/by-model`, `/usage/by-model/trend`

DynamoDB pages typically update within 5–30 seconds after invocation logs are collected.
Athena Statistics and user history commonly lag by 5–10 minutes due to S3 delivery. Repeated
queries can return the warm Lambda's cached result for up to 10 seconds. Athena lag does not
imply delayed quota decisions or blocking: all daily/weekly/monthly limits use DynamoDB DAY rows.

### CLI

Self-service:

```bash
scripts/api-call.py GET /usage/me
scripts/api-call.py GET '/usage/me/history?period=month'
```

Admin:

```bash
scripts/api-call.py GET '/usage/aggregate?limit=100'
scripts/api-call.py GET '/usage/alice'
scripts/api-call.py GET '/usage/alice/history?period=last30d'
scripts/api-call.py GET '/usage/by-model/trend?period=month&top=10'
```

## 6. Manage Limits

### Global quota timezone

Select an IANA timezone under **Limits → Global Quota Calendar**. Changes require admin access
and an audit reason.

CLI example:

```bash
scripts/api-call.py GET '/admin/settings/quota'
scripts/api-call.py PUT '/admin/settings/quota' \
  '{"quotaTimeZone":"Asia/Seoul","reason":"Korean operating calendar"}'
```

Changing timezone increments the policy version and restarts current DAY/WEEK/MONTH counters
at zero in a new namespace. This prevents mixing UTC and local-date rows. Old rows remain until
their 90-day TTL. The next five-minute periodic check reevaluates blocks using the new counters.

Avoid repeated timezone changes during normal operations. Review currently blocked users and
quota utilization beforehand, preferably changing at the start of a new organizational quota
period. Always use the Console or Core API: editing only `quotaTimeZone` directly in ConfigTable
does not increment the version and can mix counters from different periods.

### Set per-user limits

Use the console Limits page where possible.

CLI example:

```bash
scripts/api-call.py PUT '/limits/alice' \
  '{"dailyTokenLimit":2000000,"weeklyTokenLimit":8000000,"monthlyTokenLimit":25000000}'
```

When only some fields are supplied, the rest inherit defaults.

Validation rules:

- Each limit is an integer from 0 to 10^12.
- `monthly >= weekly >= daily`
- `warningThresholdPct` is 1–100.
- `exempt` is a JSON boolean.
- Limits of `0` or below are treated as disabling enforcement for that scope.

Check current configuration:

```bash
scripts/api-call.py GET '/limits/alice'
```

### Remove an override

```bash
scripts/api-call.py DELETE '/limits/alice'
```

The default policy applies immediately. Check usage first: if the user already exceeds default
limits, the next evaluation may block them.

### Change global defaults

The Core API currently has no dedicated endpoint for the default row. Schedule a change window
and update ConfigTable directly.

```bash
aws dynamodb update-item \
  --table-name BedrockMeterConfigTable \
  --region "$AWS_REGION" \
  --key '{"ConfigKey":{"S":"default"}}' \
  --update-expression \
    'SET dailyTokenLimit=:d, weeklyTokenLimit=:w, monthlyTokenLimit=:m, warningThresholdPct=:p' \
  --expression-attribute-values \
    '{":d":{"N":"3000000"},":w":{"N":"15000000"},":m":{"N":"45000000"},":p":{"N":"80"}}' \
  --return-values ALL_NEW
```

Direct DynamoDB edits bypass API validation and SNS audit.

Before changing defaults:

1. Verify `monthly >= weekly >= daily`.
2. Identify users whose current usage exceeds the new limits.
3. Ensure operator principals have exemptions or sufficient limits.
4. Record the command and reason in a separate change record.

Changing CDK seed values and redeploying does not update an existing default row.

## 7. Warnings

SNS publishes `WARNING` when usage reaches `warningThresholdPct`.

- DAY: Once per day
- WEEK: Once per ISO week
- MONTH: Once per calendar month
- `exempt`: No warnings
- A single jump past the limit may produce only a block event, without a warning

Emails do not use per-user ConfigTable email addresses. They go to operators listed in
`BEDROCK_METER_ADMIN_EMAILS` who have confirmed their SNS subscriptions.

Check subscriptions:

```bash
TOPIC_ARN="$(
  jq -r '.BedrockMeterCoreStack.SnsTopicArn' cdk-outputs.json
)"

aws sns list-subscriptions-by-topic \
  --topic-arn "$TOPIC_ARN" \
  --region "$AWS_REGION"
```

If `SubscriptionArn` is `PendingConfirmation`, follow the subscription confirmation link in
that recipient's inbox.

Email subscriptions have no default filter, so they also receive resolutions, limit-change audits,
and CloudWatch alarms.

## 8. Exemptions

`exempt: true` means:

- Usage and history continue to be recorded.
- No warnings.
- No automatic blocks.
- It is not a command that automatically and immediately clears an existing block.

Enable:

```bash
scripts/api-call.py PUT '/limits/service-admin' '{"exempt":true}'
```

Disable:

```bash
scripts/api-call.py PUT '/limits/service-admin' '{"exempt":false}'
```

Console admins can set exemptions for their own accounts too. Because exemptions skip warnings
and automatic blocking, verify authorization and intended use and record the reason before applying them.

## 9. Blocking

When a limit is exceeded, this inline policy is attached to the target IAM entity:

```text
AutoBlocked-BedrockMeter-QuotaExceeded
```

It explicitly denies Bedrock `Invoke*`, `Converse*`, `RetrieveAndGenerate*`, and `InvokeAgent*`.
It does not block other AWS services or all console logins.

List blocked users:

```bash
scripts/api-call.py GET /admin/blocked
```

Inspect the IAM policy:

```bash
aws iam get-user-policy \
  --user-name alice \
  --policy-name AutoBlocked-BedrockMeter-QuotaExceeded
```

Use `get-role-policy` when role enforcement is enabled.

### Automatic unblocking

During five-minute reevaluation, a principal whose DAY/WEEK/MONTH totals are all below their
limits is automatically unblocked and a `RESOLVED` event is published.

- DAY block: Clears after the date changes in the quota timezone, if other scopes are also below limits.
- WEEK block: Clears in the next ISO week, if other scopes are below limits.
- MONTH block: Clears next month, if other scopes are below limits.

### Force-unblock

Use the console Blocked page or:

```bash
scripts/manual-unblock.sh alice "approved temporary recovery"
```

Alternatively:

```bash
scripts/api-call.py POST '/admin/unblock/alice'
```

Force-unblock does not raise limits or erase usage. If usage remains over a limit, the next
invocation or five-minute evaluation blocks again. Change the limit/exemption policy first if needed.

### Actions to avoid

```text
Manually deleting only the inline deny in the IAM Console
Manually deleting only DynamoDB STATE
Arbitrarily decreasing UsageTable DAY counters
```

These actions break IAM/STATE consistency. PeriodicCheck may reattach the deny, or audit records
may be missing.

## 10. IAM User and Role Policies

### IAM Users

- Aggregated by full IAM User ARN.
- Receive warnings/blocks by default.
- Supported as username targets in management APIs.

### STS assumed roles

- Session ARNs are combined under stable IAM Role ARNs.
- Usage from all sessions sharing a role is summed.
- Under defaults, DAY warnings can occur in the 80–100% range, but role blocking is disabled.
- By default, PeriodicCheck skips roles for WEEK/MONTH warnings and blocks.
- `roleEnforcementEnabled=true` blocks every session of the shared role.

Individual SSO users are not distinguished or blocked individually. Blocking an SSO permission-set
role can affect many people simultaneously. Do not enable role enforcement without separate approval.

## 11. Audit

Main events:

| Event | Meaning |
|---|---|
| `WARNING` | Scope warning threshold reached |
| `BLOCKED` | IAM deny attached |
| `RESOLVED` | Automatically unblocked after reevaluation |
| `MANUAL_UNBLOCK` | Operator force-unblock |
| `LIMIT_CHANGED` | Per-user override changed |
| `LIMIT_REMOVED` | Override removed |

Use the console Audit page or API:

```bash
scripts/api-call.py GET '/admin/audit?limit=100'
```

AuditTable's default TTL is 730 days (two years). SNS redelivery is stored idempotently using
message IDs. `scripts/deploy-all.sh` also extends surviving older records to a 730-day TTL.
It cannot recover records already deleted by TTL.

## 12. Routine Checks

### Daily

- Check warning/blocked users on Console Overview.
- Review Audit for unexpected limit/exemption changes.
- Check CloudWatch alarm emails.
- Confirm no UsageAggregator or PeriodicCheck error alarms.

### Weekly

- SNS email subscription status.
- Bedrock CloudWatch/S3 delivery failure metrics.
- Athena scan costs and query failures.
- Drift between IAM policies and blocked STATE.
- Continued creation of S3 archive objects.

### After deployment

- CloudFormation `UPDATE_COMPLETE`.
- Bedrock text logging `true`.
- Core/Console URL HTTP status.
- Cognito admin/viewer groups.
- DAY row increase after a test invocation.
- Warning/block/unblock smoke test.

### Console hosting scalability review

The console targets a small internal operator group. Static HTML/JS/CSS is bundled in the BFF
Lambda package. API Gateway sends both static and `/api/console/*` requests to that Lambda,
which returns build-time files rather than generating HTML.

Static requests missing from the browser cache invoke Lambda. Hashed `/_next/static/*` assets
cache for one year, while HTML uses `no-cache`. Consider separating static UI into CloudFront +
private S3 (OAC) or Amplify Hosting if these signals persist for weeks:

| Area | Signal to review hosting separation |
|---|---|
| Audience | External users or many regular users |
| Requests | Static traffic materially raises API Gateway/Lambda request costs |
| Concurrency | Increasing BFF `ConcurrentExecutions` or `Throttles` |
| Latency | Repeated HTML/asset download delays for distant users |
| Static size | Sustained growth in image/font artifact sizes and transfer volume |
| Deployment | Repeated need for independent UI releases, previews, or rollbacks |
| Edge security | Need for custom domains, WAF, bot control, or edge access policy |

With CloudFront, keep S3 private and permit only OAC access. Prefer routing `/api/console/*` to
the existing API Gateway/BFF origin to preserve same-origin access. Disable caching for that
behavior and forward `Authorization` and query strings. Add rewrites so exports such as
`/users.html` open through `/users`. If Amplify uses a separate origin, update CORS, CSP, allowed
origins, and the runtime API base together. Always retain Cognito verification and Core SigV4
signing in the BFF; never put AWS credentials in the web bundle.

### Athena scalability review

Raw JSON + Athena suits typical early operations. These thresholds are signals for evaluating
Parquet preaggregation's cost/benefit, not limits indicating immediate failure.

| Area | Signal to begin review |
|---|---|
| Query latency | Statistics/history repeatedly takes 5–10 seconds or more |
| Query scan | Common 30-day queries scan hundreds of MB to 1 GB or more |
| CloudWatch alarm | `Athena ProcessedBytes > 2GB/hour` recurs for multiple days |
| S3 growth | Several GB of invocation archives daily |
| S3 object count | Thousands of small JSON objects per day |
| Query frequency | Multiple operators/dashboards query dozens of times per hour or more |
| Long-term analytics | Repeated queries across 6–12 months or longer |
| Cost | Athena costs warrant separate monthly optimization |

Do not redesign for one temporary spike. Observe query duration, scan bytes, S3 object count,
query frequency, and cost together for at least a week; review when two or more issues persist.

Operational principles for adoption:

1. Preserve original S3 JSON for auditing and reprocessing.
2. Store raw/cache/output tokens and invocation counts per principal/model in daily or hourly Parquet.
3. Switch historical Statistics/history to Parquet; combine with raw logs if same-day freshness matters.
4. Ensure reprocessing and backfills do not double count.
5. Regularly reconcile daily token totals between raw data and Parquet.
6. Leave DynamoDB limit evaluation, warnings, blocking, and unblocking unchanged.

Parquet optimizes analytics performance and cost. It is not a fix for quota accuracy or blocking latency.

## 13. Troubleshooting Runbooks

### 13.1 Usage never increases

1. Verify that invocation and stack regions match.
2. Check Bedrock logging configuration.

```bash
aws bedrock get-model-invocation-logging-configuration \
  --region "$AWS_REGION"
```

Check these values:

- `cloudWatchConfig.logGroupName`
- `s3Config.bucketName`
- `textDataDeliveryEnabled=true`

3. Check the Subscription Filter.

```bash
aws logs describe-subscription-filters \
  --log-group-name /aws/bedrock/modelinvocations \
  --region "$AWS_REGION"
```

4. Check UsageAggregator errors.

```bash
AGGREGATOR_LOG_GROUP="$(
  aws cloudformation list-stack-resources \
    --stack-name BedrockMeterCoreStack \
    --region "$AWS_REGION" \
    --query \
      "StackResourceSummaries[?ResourceType=='AWS::Logs::LogGroup' && contains(LogicalResourceId, 'UsageAggregatorLogGroup')].PhysicalResourceId | [0]" \
    --output text
)"

aws logs tail "$AGGREGATOR_LOG_GROUP" --since 30m --region "$AWS_REGION"
```

`TransactionConflict` warnings can be transient when invocations concurrently update one
principal's DAY row. The function makes up to five attempts using full-jitter exponential backoff
starting at 50 ms. No action is needed if it succeeds after the warning. A final
`TransactionCanceledException` at `ERROR` level or recurring alarms means retries were exhausted.
Check concentrated traffic for one principal and DynamoDB throttling. If needed, adjust CDK's
`DDB_TRANSACTION_MAX_ATTEMPTS`, `DDB_TRANSACTION_BASE_BACKOFF_MS`, and
`DDB_TRANSACTION_MAX_BACKOFF_MS`, then redeploy.

5. Verify that invocation logs contain `identity.arn` and token fields.

An incorrect inference profile or different source region can prevent this stack from receiving logs.

### 13.2 Day usage appears but Statistics/history is empty

The hot path is likely healthy while S3/Athena is delayed.

1. Wait up to 10 minutes.
2. Check for new S3 archive objects.
3. Check Bedrock S3 delivery failure alarms.
4. Verify the Athena workgroup is `ENABLED`.
5. Verify query-period partitions match the invocation region/date.

### 13.3 Warning emails do not arrive

1. Check whether the user is `exempt`.
2. Verify actual usage crossed the warning threshold.
3. Check Audit for an earlier warning in the same period.
4. Check whether one batch crossed 100% directly, producing only `BLOCKED`.
5. Verify the SNS subscription is confirmed.
6. Check spam/quarantine policies.

### 13.4 A user remains blocked

1. Identify which day/week/month scope is still over its limit.
2. Check effective limits and overrides.
3. Inspect both the IAM inline policy and UsageTable STATE.
4. Check PeriodicCheck Lambda errors.
5. For immediate recovery, change limits/exemptions first, then force-unblock through the API.

### 13.5 STATE is blocked but the IAM policy is missing

This is drift from manual deletion or an intermediate failure. PeriodicCheck restores the policy
if usage is still over a limit. Use the unblock API if intentional unblocking is the goal.

### 13.6 IAM policy exists but STATE is not blocked

Try API unblocking first. If the policy remains, inspect Lambda/IAM errors and reconcile policy
and STATE under operational approval. Preserve Audit and CloudTrail before manual deletion.

### 13.7 An operator is blocked

Bedrock deny does not itself prevent Console/API access. Have another console admin:

1. Raise the operator's limit or set `exempt=true`.
2. Force-unblock.
3. Retry Bedrock after about 10 seconds to allow IAM propagation.

If no other admin exists, a break-glass operator with deployment permissions can use ConfigTable
and the Core API.

### 13.8 Console login fails

1. Verify the Cognito user is `Enabled=true` and `CONFIRMED`.
2. Check `console-admins` or `console-viewers` membership.
3. Reset the password with `add-console-admin.sh` or `add-console-viewer.sh`.
4. Clear browser session storage and sign in again.
5. Inspect BFF Lambda errors and Console API access logs.

### 13.9 Deployment fails due to a Bedrock logging owner conflict

Inspect the existing destination first:

```bash
aws bedrock get-model-invocation-logging-configuration \
  --region "$AWS_REGION"
```

Do not automatically overwrite another system's configuration. Use a one-time overwrite option
only after reviewing whether the Log Group/S3/role belongs to this stack.

## 14. Does a Change Require Redeployment?

| Change | Redeployment |
|---|---|
| Per-user limits/warnings/exemptions | No |
| Force-unblock | No |
| Add Cognito admins/viewers or reset passwords | No |
| Change global ConfigTable defaults | No; direct update |
| Change SNS email list | Yes |
| Change Core API IAM admin list | Yes |
| Enable role enforcement | Yes |
| Change retention, resource names, or schedules | Yes |
| Change console code | Yes |
| Change Lambda/CDK code | Yes |

The UI is bundled with the BFF Lambda, so even changes limited to CSS, pages, or images require
redeploying the Lambda code asset.

Redeploy:

```bash
./scripts/deploy-all.sh
```

For new installations, if `BEDROCK_METER_QUOTA_TIME_ZONE` is absent, the script detects the host's
IANA timezone and saves it in `.env.deploy.local`. CI, CloudShell, CodeBuild, and containers may
report UTC, so explicitly setting it is safer:

```bash
BEDROCK_METER_QUOTA_TIME_ZONE=Asia/Seoul ./scripts/deploy-all.sh
```

Redeployment alone does not change an existing installation's timezone. Change it through Limits afterward.

## 15. Data Retention and Deletion

- UsageTable, ConfigTable, AuditTable: PITR + RETAIN
- Invocation Log Group: RETAIN
- S3 archive/result buckets: RETAIN
- Cognito User Pool: RETAIN
- DedupTable: DESTROY
- Lambda/HTTP API and operational Log Groups: Managed by the stack
- Bedrock logging configuration: Retained by default on stack deletion

Deleting the stack does not delete all data. Complete disposal requires a separately approved
cleanup of retained resources and account-level Bedrock logging.

### Manual retention extension

Deployment does not extend the expiry of existing DAY/audit rows. Run this tool only when you
intend to lengthen retention for existing data (Python 3.12+ and AWS CLI v2; no Python packages needed).
It uses the current terminal's credentials and `AWS_REGION` → `AWS_DEFAULT_REGION` → profile region,
and prints the account, region, and tables. No configured region means it stops.

First verify the AWS account and that `cdk-outputs.json` belongs to this installation:

```bash
aws sts get-caller-identity
aws cloudformation describe-stacks --stack-name BedrockMeterCoreStack \
  --query 'Stacks[0].Outputs' --output table
```

After verifying the target, an example extension to 180 days of usage and 730 days of audit is:

```bash
python3 scripts/backfill-retention.py \
  --usage-table "$(jq -er '.BedrockMeterCoreStack.UsageTableName' cdk-outputs.json)" \
  --audit-table "$(jq -er '.BedrockMeterCoreStack.AuditTableName' cdk-outputs.json)" \
  --usage-days 180 --audit-days 730
```

This scans the selected tables and only increases expiry timestamps; it does not shorten retention.
An error may occur after some rows were updated. Inspect the error and rerun after resolving it;
a separate deployment is not needed. Changing this command's values does not change retention for
future records; configure that policy separately in CDK.

### Installation failures and manual recovery

Use the same terminal credentials and region as the installation. Inspect the actual target and
failure events before retrying:

```bash
aws sts get-caller-identity
aws cloudformation describe-stacks --stack-name BedrockMeterCoreStack \
  --query 'Stacks[0].StackStatus' --output text
aws cloudformation describe-stack-events --stack-name BedrockMeterCoreStack \
  --query 'StackEvents[?ResourceStatusReason!=null].[LogicalResourceId,ResourceStatus,ResourceStatusReason]' \
  --output table
```

- **Tools/build:** Use Node.js 24, AWS CLI v2 and `jq`. Resolve the original npm/build error and rerun;
  no stack mutation is attempted when the build fails. Project configuration files must not change
  `AWS_*` settings. Remove legacy `CDK_DEFAULT_*` account/region entries.
- **Credentials/region:** Refresh the selected AWS login. Set `AWS_REGION` or a profile region.
  `CDK_DEFAULT_REGION` and `REGION` are not target-selection inputs for the scripts.
- **Bootstrap/permissions:** Run `./scripts/deploy-all.sh --bootstrap` after resolving the reported
  permission error. A bootstrap failure prevents application deployment. The script does not
  automatically retry or grant broader permissions.
- **Operation in progress:** Wait for the actual stack operation to finish, then inspect its result.
- **`ROLLBACK_COMPLETE`:** Inspect the failed resource and resolve the original cause. List the stack's
  resources with `aws cloudformation list-stack-resources --stack-name BedrockMeterCoreStack` and
  determine what must be retained. Only after review, manually delete the failed stack using
  `aws cloudformation delete-stack --stack-name BedrockMeterCoreStack` and wait with
  `aws cloudformation wait stack-delete-complete --stack-name BedrockMeterCoreStack`.
  Other failure states may need different CloudFormation recovery; do not apply deletion blindly.
- **Name/logging conflict:** Stack deletion can leave tables, the invocation log group, buckets,
  the logging role and user pool. Inspect remaining resources and
  `aws bedrock get-model-invocation-logging-configuration`. Do not assume a conflicting resource is
  an orphan. Only its operator may remove or change confirmed unnecessary remnants. If data or
  shared logging must be preserved, stop this cleanup path; plan reuse/migration separately.
- **Administrator setup:** Check the displayed AWS account/region and live `ConsoleUserPoolId` output.
  Retry the user command after correcting the lookup or Cognito error. It never silently uses an old
  local output file. Re-running the command resets that user's password and requested tier.

After manual recovery, rerun the deployment command. Scripts never automatically delete retained
resources, empty buckets, or enable logging overwrite. A non-empty bucket needs an explicit operator
decision about its contents before manual removal. For uninstall, inspect resources before deleting
the stack and separately review retained data, IAM blocking policies on metered principals, and
account-level Bedrock logging. Do not delete shared or needed data just to get a fresh installation.

## 16. Operational Security

- Invocation logs may contain prompts/responses.
- Minimize permissions for archive buckets, CloudWatch Logs, and Athena queries.
- The console endpoint is internet-reachable; Cognito/BFF authenticates data access.
- MFA is disabled. Do not share passwords; disable/sign out users immediately on departure or role changes.
- Use emergency allowlists only for temporary recovery.
- Never use an operator's IAM User for block testing.
- Approve the impact on shared roles before changing role enforcement.
- Direct DynamoDB/IAM manipulation is a last resort; record the reason and recovery result.
