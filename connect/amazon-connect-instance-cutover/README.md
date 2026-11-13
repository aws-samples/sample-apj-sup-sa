# Amazon Connect Customer Migration Tool

A serverless tool that copies Amazon Connect Customer (formerly Amazon Connect) configuration from a source instance to a target instance, including across AWS accounts and Regions. A signed in user opens the web UI, enters both instance ARNs, the target role ARN and its ExternalId, picks resource types and starts a Step Functions execution. The state machine lists each resource type in the source and creates it in the target in dependency order, rewriting ARNs inside flows for the target account and Region.

## Supported resources

Agent statuses, user hierarchy groups, hours of operation, prompts (audio included), predefined attributes, data tables, security profiles, routing profiles, queues, users (new random password each), contact flow modules, contact flows, quick connects, views, Contact Lens rules, evaluation forms, custom vocabularies, Lambda function associations, Lex bot associations, and AI agents, AI prompts and AI guardrails.

Not migrated: instance settings, storage configuration, Customer Profiles, Cases, AI agents knowledge bases, Lex bots and Lambda code themselves (only associations), phone numbers across accounts or Regions (AWS Support moves them, or they are ported; see Phone numbers), user passwords.

Lex V1 (Lex Classic) bots are not supported: the tool lists, matches and associates Lex V2 bots only. Rebuild any Lex V1 bot in Lex V2 before migrating the flows that use it.

> **Voice ID**: Amazon Connect Customer Voice ID reached end of support on May 20, 2026, and the tool does not migrate it (no domains, speakers, watchlists or settings). Target verification lists every migrated flow or module that still has a Set Voice ID or Check Voice ID block as a follow up with reason `VoiceIdEndOfSupport`.

## Supported Regions

The tool works in any Region where Amazon Connect Customer is available ([Amazon Connect Customer Regions](https://docs.aws.amazon.com/connect/latest/adminguide/regions.html)). It checks Region codes by format only and keeps no Region list, so a Region launched later needs no code change.

- **A feature a Region doesn't offer is skipped and reported.** For example, when the source or target Region doesn't offer AI agents, that step ends `SKIPPED` instead of failing, and the migration report lists a `FeatureNotAvailableInRegion` follow up with the side (source or target), the Region, the error code and, for a create step, the number of items not created. Target verification reports the same code as a follow up, not a failure. A quota the preflight can't read in a Region is reported as `unknown`.
- **AI prompts keep their model ID.** When the source and target Regions differ, the report lists each created AI prompt so you can check that the target Region offers its model or inference profile.
- **Cross partition moves aren't supported**, for example from a commercial Region to AWS GovCloud (US). The source instance, the target instance and the target role must be in the same partition; the start API refuses any other pair with `CrossPartitionNotSupported`.
- **The web UI stack can't be deployed in AWS GovCloud (US)**, because Amazon CloudFront isn't offered there. `scripts/deploy.sh` deploys that stack (`connect-migration-frontend-<env>`) first and passes its artifacts bucket, Cognito user pool and CloudFront URL to the other three stacks, so the script stops in an AWS GovCloud (US) Region. Deploying the tool in a commercial Region doesn't help: the tool runs in the source account, and a move can't cross partitions. The Lambda and state machine templates take every ARN partition from `${AWS::Partition}` and need nothing from the web UI stack except two parameter values (`S3Bucket`, an artifacts bucket of your own, and `AllowedOrigin`, any `https://` origin). So a move between two AWS GovCloud (US) instances needs those two stacks deployed without the scripts and each execution started directly (see Starting an execution directly). The scripts don't do this, and it hasn't been tested in AWS GovCloud (US).

## Scope: a one time cutover

The tool copies configuration once, for a cutover: you migrate, move traffic and phone numbers to the target instance, and retire the source instance afterwards. It is not a warm standby, a backup or a multi Region setup, and it does not keep the two instances in sync. A later run creates only what the target is still missing (matched by name); it never updates or deletes what is already there.

For multi Region resiliency, AWS supports only Amazon Connect Customer Global Resiliency (ACGR). AWS does not support custom built alternatives: a custom copy used as a secondary instance can get denied or reduced service quotas on that instance, and downtime attributed to such a setup can affect SLA coverage. The migration report and the web UI state the same scope.

### This tool or ReplicateInstance (Global Resiliency)

`ReplicateInstance` is the API behind Amazon Connect Customer Global Resiliency. Check its requirements before choosing it:

1. **AWS Enterprise Support or AWS Unified Operations** is required to onboard to Global Resiliency. This tool needs no AWS Support plan.
2. **SAML 2.0 on a production instance**: the source must be an existing production instance with SAML 2.0 enabled. This tool works with any identity management type (users get new random passwords, see Needs manual follow up).
3. **A supported Region pair**: US East (N. Virginia) and US West (Oregon) in either direction, Europe (Frankfurt) and Europe (London) in either direction, or Asia Pacific (Tokyo) to Asia Pacific (Osaka). This tool copies between any two Regions in one partition where Amazon Connect Customer is available (see Supported Regions).
4. **Same account only**: the replica is created in the same AWS account as the source instance. This tool also copies to another account.
5. **Bidirectional sync**: Global Resiliency mirrors configuration between the two linked instances so both stay in service. This tool copies once, for a cutover after which the source instance is retired.

If you need two Regions in service at the same time, use Global Resiliency. If you are moving to another account or Region and retiring the source, this tool fits.

### Why this matters for startups

1. **Quota first ordering**: a new target account starts at the default quotas (for example 10 concurrent active calls per instance). The quota preflight reads both instances before anything is written, so you can request increases first and cut over once they are granted.
2. **No Enterprise Support**: Global Resiliency needs AWS Enterprise Support or AWS Unified Operations. This tool needs no AWS Support plan to copy the configuration.
3. **Founder account to production account**: many startups build the first contact center in the account they started with, and later move it into a production account in AWS Organizations, often in another Region. ReplicateInstance stays in the same account; this tool copies across accounts and Regions.

## Architecture

![Architecture diagram: the operator signs in with Cognito and uses the CloudFront web UI; API Gateway starts a Step Functions migration whose worker Lambdas read the source Connect instance with the read only source reader role and assume the target writer role with an ExternalId to write to the target Connect instance and the temporary prompt bucket in another account and Region; the status API returns the migration report.](docs/images/architecture.png)

Editable source: [`docs/images/architecture.dot`](docs/images/architecture.dot) (Graphviz; `dot -Tsvg` renders [`architecture.svg`](docs/images/architecture.svg)). More detail in [docs/architecture.md](docs/architecture.md).

| Stack | Template | Contents |
|---|---|---|
| `connect-migration-frontend-<env>` | `cloudformation/1-frontend-infrastructure.yaml` | Private S3 website bucket behind CloudFront, private artifacts bucket, Cognito user pool, sign in domain and app client |
| `connect-migration-lambdas-<env>` | `cloudformation/3-lambda-functions.yaml` | Worker Lambdas, the `ConnectMigrationSourceReader` execution role, and four single purpose read roles |
| `connect-migration-stepfunctions-<env>` | `cloudformation/4-step-functions.yaml` | State machine (definition in `step-functions/migration-state-machine.json`) |
| `connect-migration-backend-<env>` | `cloudformation/2-backend-api.yaml` | REST API with a Cognito authorizer, CORS for the CloudFront origin only, stage throttling |
| `connect-migration-target-role` (target account) | `cloudformation/target-role.yaml` | `ConnectMigrationTargetWriter`, trusted only by the source reader role, with a required ExternalId |

The tool is deployed in the **source** account. It reads the source instance with its own read only role and writes to the target only through `sts:AssumeRole` on the target writer role, always with the ExternalId. The same applies to same account migrations: deploy the target role in that account too.

## Get the code

This tool lives in the `connect/amazon-connect-instance-cutover` folder of the [AWS APJ Startup Samples](https://github.com/aws-samples/sample-apj-sup-sa) repository. To check out only this folder:

```bash
git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-apj-sup-sa.git
cd sample-apj-sup-sa
git sparse-checkout set connect/amazon-connect-instance-cutover
cd connect/amazon-connect-instance-cutover
```

Run every command in this guide from that folder. The scripts locate their own files, so `scripts/deploy.sh` also works when you call it by its full path.

## Prerequisites

- AWS CLI v2, `zip`, `shasum` and bash (the macOS bash 3.2 works).
- Credentials for the source account (tool deployment) and for the target account (target role).
- The ARNs of both Connect instances.
- For Lambda and Lex references in flows: deploy the functions and create the Lex V2 bots in the target account and Region and associate them with the target instance before migrating flows (see Cross account and cross Region).

## Deployment

### 1. Choose an ExternalId

Generate a random value into a private file and keep a copy somewhere safe (a password manager). You need it in step 3 and in the UI.

```bash
(umask 077; openssl rand -hex 32 > externalid.txt)
```

### 2. Deploy the tool in the source account

```bash
scripts/deploy.sh \
  --source-instance-arn arn:aws:connect:SOURCE_REGION:SOURCE_ACCOUNT_ID:instance/SOURCE_INSTANCE_ID \
  --target-role-arn arn:aws:iam::TARGET_ACCOUNT_ID:role/ConnectMigrationTargetWriter \
  --admin-email you@example.com \
  --profile source-account-profile
```

The target role does not need to exist yet; its name is fixed, so its ARN is known in advance. The script:

1. checks that your credentials are for the account of the source instance;
2. deploys the frontend stack (CloudFront, Cognito, artifacts bucket);
3. zips every function with the shared `region_utils.py`, `arn_mapping.py` and `migration_report.py` and uploads them to the private artifacts bucket under a content hash;
4. deploys the Lambda stack (execution roles scoped to the source instance and the target role ARN, and the cleanup failure queue with its alarm);
5. writes the `--timeout-seconds` value (default `3600`) into a build copy of the state machine definition (`scripts/render-state-machine.sh`; the definition file is valid JSON and holds the default, because a CloudFormation substitution can only produce a string and Step Functions needs a number), then packages and deploys the Step Functions stack with the same value as its `TimeoutSeconds` parameter (the state machine, and the EventBridge rule that backs up the ExternalId secret cleanup);
6. deploys the API stack, pushes a new API Gateway deployment and, when the API ID changed, updates the frontend stack so its Content-Security-Policy allows the API;
7. writes `frontend/config.js` from the stack outputs (API URL, Cognito domain and client, role ARNs) and uploads it with `index.html`;
8. creates a Cognito user for `--admin-email` (a temporary password is emailed).

Options: `--region` (default: the source instance Region), `--environment` (default `prod`), `--cognito-domain-prefix`, `--enable-phone-number-move` (see Phone numbers), `--timeout-seconds` (the `TimeoutSeconds` parameter of the Step Functions stack: maximum run time of one migration execution, default `3600`; a longer run ends `TIMED_OUT`), `--cleanup-alarm-topic-arn` (the `CleanupFailureAlarmTopicArn` parameter of the Lambda stack, see Cleanup failure queue). Run `scripts/deploy.sh --help` for details.

### 3. Create the target role in the target account

Use the source reader role ARN printed at the end of step 2 (stack output `LambdaRoleArn`):

```bash
scripts/deploy-target-role.sh \
  --target-instance-arn arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID \
  --trusted-source-role-arn arn:aws:iam::SOURCE_ACCOUNT_ID:role/ConnectMigrationSourceReader \
  --external-id-file externalid.txt \
  --profile target-account-profile
```

The ExternalId is read from `--external-id-file` or, with `--external-id-stdin`, from standard input (on an interactive terminal without either option the script prompts without echo). It never appears on a command line or in the environment: the script writes the CloudFormation parameters to a mode 600 temporary file, passes it with `--parameter-overrides file://...`, and deletes it on exit. CloudFormation stores it as a `NoEcho` parameter. Deploy this after step 2: IAM rejects a trust policy whose principal role does not exist yet.

### 4. Sign in and migrate

1. Open the CloudFront URL, choose **Sign in** and use the emailed temporary password (you are asked to set a new one; TOTP MFA can be enabled).
2. Enter the source and target instance ARNs. The target role ARN is prefilled; enter the ExternalId.
3. Optionally run **Check Service Quotas** and **Check Flow Dependencies**.
4. Select resources and choose **Start Migration**. When the execution ends, the page shows a migration report with failed steps, the target verification and every item that needs manual follow up.
5. Optionally run **Post-Migration Verification** again later; it runs the same read only checks as the `VerifyTarget` step (see below).

More users: `aws cognito-idp admin-create-user --user-pool-id <UserPoolId output> --username user@example.com --user-attributes Name=email,Value=user@example.com Name=email_verified,Value=true`. Self sign up is disabled.

### Quota preflight

**Check Service Quotas** compares 14 per instance quotas of both instances before you migrate. The values are read at run time from Service Quotas (`GetServiceQuota` for the instance, then for the account, then `GetAWSDefaultServiceQuota`), so they do not go stale; the defaults below are only for reference. Count quotas warn when the target count plus the source count exceeds the target quota. Concurrent active calls per instance is checked on every run and warns when the target quota is lower than the source quota, because the target carries the source's traffic after the cutover.

| Quota | Code | Default | Adjustable |
|---|---|---|---|
| Users per instance | `L-9A46857E` | 500 | Yes |
| Queues per instance | `L-19A87C94` | 100 | Yes |
| Quick connects per instance | `L-68BBE2E8` | 100 | Yes |
| Contact flows per instance | `L-22922690` | 100 | Yes |
| Flow modules per instance | `L-19755C7E` | 200 | Yes |
| Hours of operation per instance | `L-20CD02F7` | 100 | Yes |
| Routing profiles per instance | `L-D3E7BE26` | 500 | Yes |
| Security profiles per instance | `L-F325A715` | 100 | Yes |
| Phone numbers per instance | `L-8F812903` | 10 | Yes |
| Prompts per instance | `L-0865B754` | 500 | Yes |
| Agent status per instance | `L-D945C9A8` | 50 | No: it cannot be raised, so migrate fewer statuses or remove unused ones in the target |
| Predefined Attributes | `L-3828FBF0` | 150 | Yes |
| Concurrent active calls per instance | `L-12AB7C57` | 10 | Yes |
| Amazon Lex bots per instance | `L-B93A6612` | 70 | Yes |

Request increases for the target instance before you migrate; an increase can take time to be granted.

### Target verification

After the migration phases the state machine runs `VerifyTarget` (`lambda/verify_target.py`), before `CleanupExternalIdSecret` because it reads the target through the target role. It only calls List and Describe APIs, and checks, for the selected resource types:

- **Names**: per type, source names found in the target, missing (listed by source ID) and extra. What the migration skips on purpose is counted as skipped, never as missing, and is decided by the same predicates the list and create steps use (`lambda/migration_filters.py`, so the two sides cannot drift; `tests/test_migration_filters.py` fails if they disagree): default and sample flows, agent queues, AWS managed and unpublished views, inactive vocabularies, inactive evaluation forms (their status is read with `DescribeEvaluationForm` on the source), unpublished rules, Connect owned (`connect:`) predefined attributes and built-in agent statuses. AI resources whose option is off are not checked (an option counts as on only when it is the boolean `true`, as in the state machine), and phone numbers, Lambda functions and Lex bots are not compared by name.
- **Flow and module content**: every ARN or ID of the source account, source Region or source instance left in a migrated flow or module is judged with the flow rewriter's own mapping (`arn_mapping.build_rewriter`, built only when such a reference is found). A reference the rewriter would map now (the target has the matching resource, for example a queue or function with the same name) should have been rewritten, so it fails the check (`MappableSourceReference`). A reference the tool has no way to map is a follow up (`UnmappableSourceReference`), as are `MIGRATION-PLACEHOLDER` Lambda ARNs and `MIGRATEBOT` Lex aliases (`Placeholder`). Flows are listed by target flow ID with the ARN types (lambda, lex, queue, prompt, wisdom, other).
- **Queues**: the outbound caller ID number is not a source number; the outbound flow and hours of operation exist in the target with the source's names.
- **Users**: routing profile, security profiles and hierarchy group have the names of the source user's ones (unless the user options keep the defaults).
- **Quick connects**: USER, QUEUE and PHONE_NUMBER quick connects reference users, queues and flows that exist in the target.

The verdict is `PASS`, `PASS_WITH_FOLLOW_UPS` or `FAIL`, with a pass or fail per check; `ERROR` means the step could not run (an error code says why). The result holds IDs, codes and counts only. It is part of the migration report (`verification`) and the status response; a `FAIL` ends the execution in `TargetVerificationFailed`, an `ERROR` (or a crashed step) still runs the cleanup and the report and ends in `TargetVerificationErrored`. **Post-Migration Verification** in the UI (`POST /verify-migration`) runs the same code on demand; its Describe based checks stop after 20 seconds to fit the API Gateway timeout and then fail with `VerificationIncomplete`.

`PASS_WITH_FOLLOW_UPS` means every check passed but the target still needs manual work: placeholders, source references the tool cannot map, or a dropped caller ID number. The execution ends `Succeeded`, and the migration report lists every follow up. Flow and module follow ups come from the VerifyTarget scan of the target flows, so a rerun whose flows already exist (and are not touched by the create step) still lists them; each entry holds the target flow or module ID, the ARN type, a reason code and a count, never flow content or names. How to resolve each reason code (then publish the flow; the next run or **Post-Migration Verification** no longer lists it):

| Reason code | Meaning | How to resolve |
|---|---|---|
| `NoTargetAssistant` | An AI agents (wisdom) assistant ARN of the source, and the target has no assistant | Create an AI agents assistant in the target Region, integrate it with the target instance, and select it in the AI agents block of the flow |
| `AssistantNotMapped` | A source assistant ARN; the target has an assistant, but the tool does not map assistant ARNs | Select the target assistant in the AI agents block of the flow |
| `NoMatchingLambda` | A source Lambda ARN or a `MIGRATION-PLACEHOLDER` ARN; no function with that name is associated with the target instance | Deploy the function in the target account and Region, add it to the target instance, and select it in the Invoke AWS Lambda function block |
| `NoMatchingLexBot` | A source Lex V2 alias ARN or the `MIGRATEBOT` placeholder; no bot alias with the same bot and alias names is associated with the target | Create or import the bot in the target account and Region, add it to the target instance, and select it in the Get customer input block |
| `NoMatchingTargetResource` | A source Amazon Connect Customer resource ARN with no resource of the same name in the target | Migrate or create the resource in the target, then select it in the block |
| `UnsupportedArnType` | A source ARN of a service the tool does not map | Create the resource in the target account and Region and update the block |
| `SourceIdOutsideArn` | The source account or instance ID appears outside an ARN (for example in a text or an attribute value) | Replace it with the target value |
| `VoiceIdEndOfSupport` | The flow has a Set Voice ID or Check Voice ID block (flow actions `StartVoiceIdStream`, `CheckVoiceId`); Voice ID reached end of support on May 20, 2026 | Remove the blocks or replace them with another caller authentication method |

## Security model

- **Authentication**: all API methods except CORS preflight require a Cognito ID token (managed login, authorization code flow with PKCE). Preflight `OPTIONS` methods are unauthenticated mock integrations with no backend, because browsers send preflights without credentials.
- **CORS** allows only the CloudFront origin, also on API Gateway's own error responses.
- **Throttling**: stage wide limits, `ThrottlingRateLimit` (default 5 rps) and `ThrottlingBurstLimit` (default 10) on the API stack.
- **Tracing**: X-Ray tracing is on for the API stage.
- **TLS on CloudFront (known limitation by default).** With the default `*.cloudfront.net` certificate CloudFront cannot enforce a minimum TLS version. For TLS 1.2 or later, deploy with `--custom-domain-name <name> --acm-certificate-arn <ARN>` (parameters `CustomDomainName` and `AcmCertificateArn`; the certificate must be issued in US East, N. Virginia, which CloudFront requires). The distribution then uses `MinimumProtocolVersion: TLSv1.2_2021`, and the Cognito callback, CORS origin and frontend config switch to the custom domain. Create the DNS record yourself, pointing to the `CloudFrontDomainName` output.
- **API access logging is opt in.** API Gateway can only write access logs through the account level CloudWatch Logs role (`AWS::ApiGateway::Account`), a single setting per account and Region shared by every REST API. The stack does not change it unless you ask. To turn logging on, deploy with `--enable-api-access-logging` (parameter `EnableApiAccessLogging=true`). If the account has no API Gateway CloudWatch role yet, also pass `--api-gateway-cloudwatch-role-arn <existing role ARN>` (a role trusted by `apigateway.amazonaws.com` with the AWS managed `AmazonAPIGatewayPushToCloudWatchLogs` policy); that sets the account setting, and deleting the stack does not unset it. Logs go to `/aws/apigateway/connect-migration-api-<env>/access` with `ApiAccessLogRetentionDays` (default 90) and contain request ID, source IP, caller, method, resource path, status, response length and time only: no bodies, no Authorization header, no token claims. Scanners may still report missing access logging when it is off; that is a documented exception.
- **ExternalId**: required for every AssumeRole of the target role; validated, never logged, redacted from status responses. The start API stores it in a per execution Secrets Manager secret (`connect-migration/<env>/<execution name>`, default `aws/secretsmanager` key) and the execution input carries only the secret ARN, so it is not in the Step Functions input or history. Migration steps read it with `GetSecretValue` and fail closed: a step that receives a raw `externalId` key refuses it (`RawExternalIdRejected`) instead of using it. Only the API handlers the UI calls directly (start, quota check, flow pre check, verification) read the raw value from their own request body, and they never read a secret. A cleanup step deletes the secret at the end of every run, before the migration report; if that delete fails, the report says the secret may be left behind and the execution ends `Failed`. The start API deletes the secret if the execution does not start (a failure of that delete is logged by error code, and the original start error is returned). An ExternalId protects against the confused deputy problem; it is not a password.
- **ExternalId secret backstop**: runs that never reach the cleanup step (aborted, timed out after `TimeoutSeconds`, or failed with `States.Runtime` or `States.DataLimitExceeded`) are covered by an EventBridge rule on the Step Functions *Execution Status Change* events of this state machine only, for status `ABORTED`, `TIMED_OUT` and `FAILED`. It invokes the cleanup function, which derives the secret name from the execution name under the `connect-migration/<env>/` prefix and deletes it with `ForceDeleteWithoutRecovery`; a missing secret counts as success, so it is safe for runs whose cleanup step already ran. Only that rule may invoke the function through EventBridge (`lambda:InvokeFunction`, principal `events.amazonaws.com`, `SourceArn` set to the rule). There is no scheduled sweep: listing secrets needs `secretsmanager:ListSecrets` on `"Resource": "*"`.
- **Cleanup failure queue**: the backstop invokes the cleanup function asynchronously, so a delete that keeps failing is never returned to a caller. Instead of being dropped, it lands in the SQS queue `connect-migration-cleanup-external-id-failures-<env>` (Lambda stack outputs `CleanupFailureQueueUrl` and `CleanupFailureQueueArn`):
  - the function retries twice (`AWS::Lambda::EventInvokeConfig`, `MaximumRetryAttempts` 2) and then sends the event to the queue as its dead letter queue (`DeadLetterConfig`; its role may only `sqs:SendMessage` to this queue);
  - EventBridge retries delivery to the function (`RetryPolicy`: 10 attempts within an hour) and then sends the event to the same queue (`DeadLetterConfig` on the rule target; the queue policy lets only the `connect-migration-secret-backstop-<env>` rule send);
  - the queue uses SSE-SQS encryption (`SqsManagedSseEnabled`), denies the message actions without TLS, and keeps messages for `CleanupFailureRetentionSeconds` (default 1209600, 14 days, the SQS maximum);
  - the CloudWatch alarm `connect-migration-cleanup-external-id-failures-<env>` goes to `ALARM` when the queue holds a message (`ApproximateNumberOfMessagesVisible` > 0). It notifies the SNS topic in `CleanupFailureAlarmTopicArn` (default empty: no action, check the alarm state yourself). Set it with `scripts/deploy.sh --cleanup-alarm-topic-arn <topic ARN>`. To subscribe, create a standard topic in the tool account and Region (`aws sns create-topic --name connect-migration-alerts`), subscribe to it (`aws sns subscribe --topic-arn <topic ARN> --protocol email --notification-endpoint <your address>`, then confirm the email) and redeploy with the option. A topic encrypted with a customer managed KMS key needs a key policy that lets `cloudwatch.amazonaws.com` use the key.
  - Each message body is the Step Functions *Execution Status Change* event of one execution (IDs, ARNs, status and the execution input, which holds the secret ARN but never the ExternalId). The message attributes `ErrorCode` and `ErrorMessage` hold the exception type, the error code and the action only (take the execution name from `detail.name` in the body).
  - **Cleaning up a secret listed there**: read the message (`aws sqs receive-message --queue-url <CleanupFailureQueueUrl> --attribute-names All --message-attribute-names All`), take `detail.name` (the execution name) from the body, fix the cause (for example a missing permission), then delete the secret `connect-migration/<env>/<execution name>`: `aws secretsmanager delete-secret --secret-id connect-migration/<env>/<execution name> --force-delete-without-recovery` (a `ResourceNotFoundException` means it is already gone). Then delete the message (`aws sqs delete-message --queue-url <CleanupFailureQueueUrl> --receipt-handle <ReceiptHandle>`) so the alarm returns to `OK`.
- **Starting an execution directly** (without the web UI or `POST /start`): because steps reject a raw `externalId`, create the secret first, named `connect-migration/<env>/<execution name>` with the ExternalId as its value, then start the execution with that execution name and `"externalIdSecretArn": "<secret ARN>"` in the input (never `externalId`). Using the execution name keeps the backstop able to delete it. Same account runs without a target role need no secret (`"externalIdSecretArn": null`).
- **Code and definitions** live in a private artifacts bucket that CloudFront does not serve.
- **Storage**: every bucket blocks public access, uses SSE-S3 and denies non TLS requests. The website and artifacts buckets are versioned (noncurrent versions expire after `NoncurrentVersionRetentionDays`, default 30) and write S3 server access logs to a dedicated access log bucket, which also receives the CloudFront standard logs. Logs expire after `LogRetentionDays` (default 90). The access log bucket keeps ACLs enabled (`BucketOwnerPreferred`) because CloudFront standard logging delivers with an ACL, so the deploying principal needs `s3:GetBucketAcl` and `s3:PutBucketAcl` on it.
- AccessDenied and throttling errors fail the step with the action and resource in the log, instead of being reported as empty results.
- **Logs** contain resource IDs, ARNs, counts, action names and error codes only; names, usernames, emails, phone numbers, attribute and data table values and payloads are never logged (`tests/test_log_hygiene.py` enforces this).
- **Error messages**: AWS error messages can echo request values, so no function reads them. Log lines, API responses and step outputs carry the error code, the action name and the resource ID only (`region_utils.describe_error`, `error_text`, `fatal_error`), never `str(e)` or the `Message` of a `ClientError` (`tests/test_log_hygiene.py` enforces this for every file in `lambda/`). Every state machine handler is wrapped by `region_utils.step_handler`: an unexpected exception is re-raised with the text `<type>: error code <code>, action <action>` only (no message, no resource names or IDs) and its context suppressed, so the Step Functions execution history (the Catch `Cause`), the function log and the cleanup dead letter queue never carry an AWS message. The re-raised exception keeps the original class name and type, so Retry and Catch `ErrorEquals` still match (`tests/test_step_errors.py` fails if a handler is not wrapped).
- **Browser**: all API and Connect data is HTML escaped before it is shown (`tests/test_frontend_escaping.py` enforces this).
- **Security headers**: a CloudFront response headers policy (`SecurityHeadersPolicy`) adds `Strict-Transport-Security` (one year, subdomains), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and a `Content-Security-Policy`. The CSP allows nothing by default; scripts, styles and images come from the page's own origin, and the page may connect only to the Cognito sign in domain of the stack and to the tool's API. The API host is built from the frontend stack parameter `ApiId`, which `scripts/deploy.sh` sets once the API stack exists (so the first deploy updates the frontend stack twice). `index.html` keeps its script, styles and event handlers inline, so `script-src` and `style-src` include `'unsafe-inline'`; the escaping above is what keeps API and Connect data from running as script.

### Accepted scanner findings

These scanner findings are accepted on purpose:

| Rule | Resource | Why it is accepted |
|---|---|---|
| LAMBDA_INSIDE_VPC | All Lambda functions | The functions only call public AWS service endpoints and hold no network reachable data; a VPC would add NAT or endpoint cost and complexity without reducing exposure |
| LAMBDA_CONCURRENCY_CHECK | All Lambda functions | Concurrency is bounded by the state machine and the throttled API; reserving concurrency for 55 functions would take account concurrency away from other workloads |
| LAMBDA_DLQ_CHECK | All Lambda functions except `connect-migration-cleanup-external-id-<env>` | These functions are invoked synchronously only (Step Functions tasks or API Gateway); failures go back to the caller and the migration report, so a dead letter queue would never receive anything. The cleanup function is also invoked asynchronously by the EventBridge backstop, so it has a dead letter queue (see Cleanup failure queue) |
| IAM_NO_INLINE_POLICY_CHECK | IAM roles | Each inline policy belongs to exactly one role and is scoped to one instance, account and Region; separate managed policies would add resources without reducing permissions |
| S3_DEFAULT_ENCRYPTION_KMS | S3 buckets | SSE-S3 is used; CloudFront standard logs cannot be delivered to SSE-KMS buckets, and KMS adds key management for public website files |
| S3_BUCKET_DEFAULT_LOCK_ENABLED | All buckets, including AccessLogsBucket | Object Lock would block normal redeploys, log expiry and cleanup of website, artifact and log objects |
| CKV_AWS_18, S3_BUCKET_LOGGING_ENABLED | AccessLogsBucket | It is the log destination; a log bucket does not log into itself (that would loop) |
| CKV_AWS_21, S3_BUCKET_VERSIONING_ENABLED | AccessLogsBucket | Logs are written once and expire after `LogRetentionDays`; the website and artifacts buckets are versioned |
| S3_BUCKET_NO_PUBLIC_RW_ACL | AccessLogsBucket | ACLs stay enabled (`BucketOwnerPreferred`) only because CloudFront standard logging delivers with an ACL; all four public access blocks are on, so no public ACL or policy can apply |
| CLOUDWATCH_LOG_GROUP_ENCRYPTED, CW_LOGGROUP_RETENTION_PERIOD_CHECK | ApiAccessLogGroup | The log group exists only when API access logging is turned on; it holds request metadata only (no bodies, tokens or personal data beyond source IP) and is encrypted by CloudWatch Logs by default; retention is set by `ApiAccessLogRetentionDays` (default 90 days) |
| API_GW_CACHE_ENABLED_AND_ENCRYPTED | API stage | Responses are per request and per user; caching them would be wrong, so the cache is off |
| CKV_AWS_76 | API stage | API access logging is off by default; see the API access logging item above |
| CKV_AWS_174, CLOUDFRONT_CUSTOM_SSL_CERTIFICATE | CloudFront distribution (default deployment) | The default certificate cannot enforce a minimum TLS version; the optional custom domain uses TLSv1.2_2021 (see above) |
| html.security.audit.insecure-document-method (Semgrep) | frontend/index.html | An audit rule that flags any `innerHTML` use. Every interpolated value is passed through `escapeHtml`, status messages use `textContent`, and `tests/test_frontend_escaping.py` fails the build if an unescaped value is added |
| CKV_AWS_27 (Checkov, High) | CleanupExternalIdFailureQueue | The queue is encrypted at rest with SSE-SQS (`SqsManagedSseEnabled: true`), and a TLS only queue policy denies requests without TLS. The check only accepts a KMS key, and a customer managed key would add cost and key permissions for a queue that only holds execution names and error codes |
| SQS_QUEUE_KMS_MASTER_KEY_ID_RULE (cfn-guard, Low) | CleanupExternalIdFailureQueue | Same reason as CKV_AWS_27: SSE-SQS at rest plus a TLS only queue policy; a customer managed key would add cost and key permissions for a queue that only holds execution names and error codes |

If an organization requires a customer managed KMS key on the cleanup failure queue, set `KmsMasterKeyId` on `CleanupExternalIdFailureQueue` and grant the cleanup role (`CleanupExternalIdRole`) and `events.amazonaws.com` `kms:GenerateDataKey` and `kms:Decrypt` on that key.

## Minimum IAM permissions

These are exactly the policies the templates create (rendered with placeholder values). The blocks show the `aws` partition; the templates use `${AWS::Partition}`, so in AWS GovCloud (US) every ARN starts with `arn:aws-us-gov:`.

### Tool account: `ConnectMigrationSourceReader`

Execution role of every worker Lambda that reads the source or writes through the target role, plus `AWSLambdaBasicExecutionRole` for logs. Read only on the source; the only write capability is assuming the target role.

<details><summary>SourceReader policy</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ConnectReadSourceInstance",
      "Effect": "Allow",
      "Action": [
        "connect:BatchDescribeDataTableValue",
        "connect:DescribeContactFlow",
        "connect:DescribeContactFlowModule",
        "connect:DescribeDataTable",
        "connect:DescribeEvaluationForm",
        "connect:DescribeHoursOfOperation",
        "connect:DescribePredefinedAttribute",
        "connect:DescribePrompt",
        "connect:DescribeQueue",
        "connect:DescribeQuickConnect",
        "connect:DescribeRoutingProfile",
        "connect:DescribeRule",
        "connect:DescribeSecurityProfile",
        "connect:DescribeUser",
        "connect:DescribeUserHierarchyGroup",
        "connect:DescribeUserHierarchyStructure",
        "connect:DescribeView",
        "connect:GetPromptFile",
        "connect:ListAgentStatuses",
        "connect:ListBots",
        "connect:ListContactFlowModules",
        "connect:ListContactFlows",
        "connect:ListDataTableAttributes",
        "connect:ListDataTablePrimaryValues",
        "connect:ListDataTableValues",
        "connect:ListDataTables",
        "connect:ListEvaluationForms",
        "connect:ListHoursOfOperations",
        "connect:ListLambdaFunctions",
        "connect:ListPredefinedAttributes",
        "connect:ListPrompts",
        "connect:ListQueues",
        "connect:ListQuickConnects",
        "connect:ListRoutingProfiles",
        "connect:ListRules",
        "connect:ListSecurityProfilePermissions",
        "connect:ListSecurityProfiles",
        "connect:ListUserHierarchyGroups",
        "connect:ListUserProficiencies",
        "connect:ListUsers",
        "connect:ListViews",
        "connect:SearchVocabularies"
      ],
      "Resource": [
        "arn:aws:connect:SOURCE_REGION:SOURCE_ACCOUNT_ID:instance/SOURCE_INSTANCE_ID",
        "arn:aws:connect:SOURCE_REGION:SOURCE_ACCOUNT_ID:instance/SOURCE_INSTANCE_ID/*"
      ]
    },
    {
      "Sid": "ConnectDescribeAwsManagedViews",
      "Effect": "Allow",
      "Action": "connect:DescribeView",
      "Resource": "arn:aws:connect:SOURCE_REGION:aws:view/*"
    },
    {
      "Sid": "ConnectListSourcePhoneNumbers",
      "Effect": "Allow",
      "Action": "connect:ListPhoneNumbersV2",
      "Resource": "arn:aws:connect:SOURCE_REGION:SOURCE_ACCOUNT_ID:phone-number/*"
    },
    {
      "Sid": "LexDescribeSourceBots",
      "Effect": "Allow",
      "Action": [
        "lex:DescribeBot",
        "lex:DescribeBotAlias"
      ],
      "Resource": [
        "arn:aws:lex:SOURCE_REGION:SOURCE_ACCOUNT_ID:bot/*",
        "arn:aws:lex:SOURCE_REGION:SOURCE_ACCOUNT_ID:bot-alias/*"
      ]
    },
    {
      "Sid": "QConnectListNoResourceLevel",
      "Effect": "Allow",
      "Action": [
        "wisdom:ListAIAgents",
        "wisdom:ListAIGuardrails",
        "wisdom:ListAIPrompts",
        "wisdom:ListAssistants"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "SOURCE_REGION"
        }
      }
    },
    {
      "Sid": "ServiceQuotasReadConnect",
      "Effect": "Allow",
      "Action": "servicequotas:GetServiceQuota",
      "Resource": "arn:aws:servicequotas:SOURCE_REGION:SOURCE_ACCOUNT_ID:connect/*"
    },
    {
      "Sid": "ServiceQuotasDefaultNoResourceLevel",
      "Effect": "Allow",
      "Action": "servicequotas:GetAWSDefaultServiceQuota",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "SOURCE_REGION"
        }
      }
    }
  ]
}
```

</details>

<details><summary>AssumeTargetWriter policy</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AssumeTargetWriter",
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::TARGET_ACCOUNT_ID:role/ConnectMigrationTargetWriter"
    }
  ]
}
```

</details>

Trust: `lambda.amazonaws.com`.

A third inline policy, `ReadExternalIdSecret`, allows `secretsmanager:GetSecretValue` on `arn:aws:secretsmanager:TOOL_REGION:SOURCE_ACCOUNT_ID:secret:connect-migration/<env>/*` only, so migration steps can read the per execution ExternalId secret.

### Tool account: single purpose read roles

The list functions for AI agents and vocabularies run with their own roles and cannot assume the target role.

| Role | Function | Actions | Resources |
|---|---|---|---|
| ListAiAgentsRole | `connect-migration-list-ai-agents-<env>` | `wisdom:ListAssistants`, `wisdom:ListAIAgents`, `wisdom:GetAIAgent` | `*` (Region condition), `arn:aws:wisdom:SOURCE_REGION:SOURCE_ACCOUNT_ID:ai-agent/*` |
| ListAiPromptsRole | `connect-migration-list-ai-prompts-<env>` | `wisdom:ListAssistants`, `wisdom:ListAIPrompts`, `wisdom:GetAIPrompt` | `*` (Region condition), `arn:aws:wisdom:SOURCE_REGION:SOURCE_ACCOUNT_ID:ai-prompt/*` |
| ListAiGuardrailsRole | `connect-migration-list-ai-guardrails-<env>` | `wisdom:ListAssistants`, `wisdom:ListAIGuardrails`, `wisdom:GetAIGuardrail`, `wisdom:ListAIGuardrailVersions` | `*` (Region condition), `arn:aws:wisdom:SOURCE_REGION:SOURCE_ACCOUNT_ID:ai-guardrail/*` |
| ListVocabulariesRole | `connect-migration-list-vocabularies-<env>` | `connect:SearchVocabularies`, `connect:DescribeVocabulary` | `arn:aws:connect:SOURCE_REGION:SOURCE_ACCOUNT_ID:instance/SOURCE_INSTANCE_ID`, `arn:aws:connect:SOURCE_REGION:SOURCE_ACCOUNT_ID:instance/SOURCE_INSTANCE_ID/*` |

### Tool account: API stack roles

| Role | Actions | Resources |
|---|---|---|
| Start migration | `states:StartExecution`; `secretsmanager:CreateSecret`, `secretsmanager:DeleteSecret` | the `connect-user-migration-<env>` state machine; secrets `connect-migration/<env>/*` in the tool account and Region |
| ExternalId secret cleanup (`connect-migration-cleanup-external-id-<env>`) | `secretsmanager:DeleteSecret`; `sqs:SendMessage` | secrets `connect-migration/<env>/*` in the tool account and Region; the `connect-migration-cleanup-external-id-failures-<env>` queue only (the function's dead letter queue) |
| ExternalId secret backstop (resource policy on the cleanup function, Step Functions stack) | `lambda:InvokeFunction` for `events.amazonaws.com`, `SourceAccount` the tool account | `SourceArn` the `connect-migration-secret-backstop-<env>` EventBridge rule only |
| Cleanup failure queue (queue policy, Lambda stack) | `sqs:SendMessage` for `events.amazonaws.com`; every message action denied without TLS | that queue, `aws:SourceArn` the `connect-migration-secret-backstop-<env>` rule only |
| Migration report (`connect-migration-build-migration-report-<env>`) | `states:GetExecutionHistory` | executions of `connect-user-migration-<env>` |
| Migration status | `states:DescribeExecution`, `states:GetExecutionHistory` | executions of that state machine |
| Phone numbers and quick connect prefixes | `connect:ListQuickConnects`, `connect:DescribeQuickConnect`, `connect:ListPhoneNumbersV2` | the source instance and its phone numbers |
| Step Functions | `lambda:InvokeFunction` | `connect-migration-*-<env>` functions in the tool account and Region |

### Target account: `ConnectMigrationTargetWriter`

<details><summary>TargetWriter policy</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ConnectWriteTargetInstance",
      "Effect": "Allow",
      "Action": [
        "connect:ActivateEvaluationForm",
        "connect:AssociateBot",
        "connect:AssociateLambdaFunction",
        "connect:AssociateUserProficiencies",
        "connect:BatchCreateDataTableValue",
        "connect:CreateAgentStatus",
        "connect:CreateContactFlow",
        "connect:CreateContactFlowModule",
        "connect:CreateDataTable",
        "connect:CreateDataTableAttribute",
        "connect:CreateEvaluationForm",
        "connect:CreateHoursOfOperation",
        "connect:CreatePredefinedAttribute",
        "connect:CreatePrompt",
        "connect:CreateQueue",
        "connect:CreateQuickConnect",
        "connect:CreateRoutingProfile",
        "connect:CreateRule",
        "connect:CreateSecurityProfile",
        "connect:CreateUser",
        "connect:CreateUserHierarchyGroup",
        "connect:CreateView",
        "connect:CreateVocabulary",
        "connect:UpdateUserHierarchyStructure"
      ],
      "Resource": [
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID",
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID/*"
      ]
    },
    {
      "Sid": "ConnectReadTargetInstance",
      "Effect": "Allow",
      "Action": [
        "connect:DescribeUserHierarchyStructure",
        "connect:DescribeVocabulary",
        "connect:ListAgentStatuses",
        "connect:ListBots",
        "connect:ListContactFlowModules",
        "connect:ListContactFlows",
        "connect:ListDataTables",
        "connect:ListEvaluationForms",
        "connect:ListHoursOfOperations",
        "connect:ListLambdaFunctions",
        "connect:ListPredefinedAttributes",
        "connect:ListPrompts",
        "connect:ListQueues",
        "connect:ListQuickConnects",
        "connect:ListRoutingProfiles",
        "connect:ListRules",
        "connect:ListSecurityProfiles",
        "connect:ListUserHierarchyGroups",
        "connect:ListUsers",
        "connect:ListViews",
        "connect:SearchVocabularies"
      ],
      "Resource": [
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID",
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID/*"
      ]
    },
    {
      "Sid": "ConnectDescribeTargetForVerify",
      "Effect": "Allow",
      "Action": [
        "connect:DescribeContactFlow",
        "connect:DescribeContactFlowModule",
        "connect:DescribeQueue",
        "connect:DescribeQuickConnect",
        "connect:DescribeUser"
      ],
      "Resource": [
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID/contact-flow/*",
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID/flow-module/*",
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID/queue/*",
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID/transfer-destination/*",
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID/agent/*"
      ]
    },
    {
      "Sid": "ConnectTagOnCreate",
      "Effect": "Allow",
      "Action": "connect:TagResource",
      "Resource": "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID/*"
    },
    {
      "Sid": "ConnectListTargetPhoneNumbers",
      "Effect": "Allow",
      "Action": "connect:ListPhoneNumbersV2",
      "Resource": "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:phone-number/*"
    },
    {
      "Sid": "LexDescribeTargetBots",
      "Effect": "Allow",
      "Action": [
        "lex:DescribeBot",
        "lex:DescribeBotAlias"
      ],
      "Resource": [
        "arn:aws:lex:TARGET_REGION:TARGET_ACCOUNT_ID:bot/*",
        "arn:aws:lex:TARGET_REGION:TARGET_ACCOUNT_ID:bot-alias/*"
      ]
    },
    {
      "Sid": "QConnectNoResourceLevel",
      "Effect": "Allow",
      "Action": [
        "wisdom:CreateAIAgent",
        "wisdom:CreateAIGuardrail",
        "wisdom:CreateAIPrompt",
        "wisdom:ListAIAgents",
        "wisdom:ListAIGuardrails",
        "wisdom:ListAIPrompts",
        "wisdom:ListAssistants"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "TARGET_REGION"
        }
      }
    },
    {
      "Sid": "QConnectTagOnCreate",
      "Effect": "Allow",
      "Action": "wisdom:TagResource",
      "Resource": [
        "arn:aws:wisdom:TARGET_REGION:TARGET_ACCOUNT_ID:ai-agent/*",
        "arn:aws:wisdom:TARGET_REGION:TARGET_ACCOUNT_ID:ai-prompt/*",
        "arn:aws:wisdom:TARGET_REGION:TARGET_ACCOUNT_ID:ai-guardrail/*"
      ]
    },
    {
      "Sid": "ServiceQuotasReadConnect",
      "Effect": "Allow",
      "Action": "servicequotas:GetServiceQuota",
      "Resource": "arn:aws:servicequotas:TARGET_REGION:TARGET_ACCOUNT_ID:connect/*"
    },
    {
      "Sid": "ServiceQuotasDefaultNoResourceLevel",
      "Effect": "Allow",
      "Action": "servicequotas:GetAWSDefaultServiceQuota",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "TARGET_REGION"
        }
      }
    },
    {
      "Sid": "PromptTempBucket",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:ListBucket",
        "s3:PutLifecycleConfiguration",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutEncryptionConfiguration",
        "s3:PutBucketPolicy"
      ],
      "Resource": "arn:aws:s3:::connect-migration-prompts-temp-TARGET_ACCOUNT_ID-TARGET_REGION"
    },
    {
      "Sid": "PromptTempObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::connect-migration-prompts-temp-TARGET_ACCOUNT_ID-TARGET_REGION/prompts/TARGET_INSTANCE_ID/*"
    },
    {
      "Sid": "OptInPhoneNumberMove",
      "Effect": "Allow",
      "Action": "connect:UpdatePhoneNumber",
      "Resource": [
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:instance/TARGET_INSTANCE_ID",
        "arn:aws:connect:TARGET_REGION:TARGET_ACCOUNT_ID:phone-number/*"
      ]
    }
  ]
}
```

</details>

<details><summary>Trust policy</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AssumeFromMigrationTool",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::SOURCE_ACCOUNT_ID:role/ConnectMigrationSourceReader"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "sts:ExternalId": "YOUR_EXTERNAL_ID"
        }
      }
    }
  ]
}
```

</details>

The `OptInPhoneNumberMove` statement exists only with `AllowPhoneNumberMove=true`. `ConnectDescribeTargetForVerify` holds only the five Describe calls of the read only `VerifyTarget` step, limited to flows, flow modules, queues, quick connects and users inside the target instance. `s3:GetObject` on the temporary prompt prefix is not called by the tool; it is granted in case `CreatePrompt` reads the object with the caller's credentials (not verified yet).

Actions without resource level permissions (`wisdom:List*`, `wisdom:Create*`, `servicequotas:GetAWSDefaultServiceQuota`) use `"Resource": "*"` limited by an `aws:RequestedRegion` condition.

### Deploying

Deploying creates CloudFormation stacks, named IAM roles, Lambda functions, S3 buckets, a CloudFront distribution, API Gateway, Step Functions and Cognito resources, so the deploying principal needs broad rights in the tool account (and IAM role creation in the target account). A least privilege deployer policy has not been written.

## Cross account and cross Region

### Rewritten automatically

| Item | How |
|---|---|
| Connect ARNs of the source instance in flows and modules (queues, prompts, hours, flows, modules, agents, quick connects, routing and security profiles, hierarchy groups, customer managed views) | Mapped by name to the target resource; account, Region and instance ID are replaced |
| The source instance ARN | Replaced with the target instance ARN |
| AWS managed ARNs (account `aws`, for example managed views in Show view blocks) | Region changed to the target Region |
| Lambda ARNs in flows | Replaced with the function of the same name associated with the target instance |
| Lex V2 bot aliases in flows | Matched by bot name and alias name to a bot associated with the target instance |
| Queue outbound caller configuration | Outbound whisper flow mapped by name; caller ID name kept |
| API calls | Source calls use the source Region, target calls the target Region and the target role |

### Needs manual follow up (listed in the migration report)

The report and every step output identify resources by ID or ARN, never by name (names would end up in the Step Functions execution history). The exception is predefined attributes, whose name is their only identifier.

| Item | Why | What to do |
|---|---|---|
| Phone numbers | The tool moves numbers only within one account and Region, and only with the opt in | Follow the path the report names (`PhoneNumberSelfMove`, `PhoneNumberSupportMove` or `PhoneNumberPorting`, see Phone numbers); attach the numbers to flows; set them as outbound caller ID on the queues the report lists |
| Lambda functions without a match | Code is not copied, and the tool no longer edits source function policies (that was a write in the source account and created cross Region calls) | Deploy the function in the target account and Region, associate it with the target instance, then replace the `MIGRATION-PLACEHOLDER-<name>` function ARN in the flows the report lists (or delete the flows and run the contact flow step again) |
| Lex V2 bots without a match | Bots belong to one account | Export and import the bot into the target account and Region, associate it, then replace the `bot-alias/MIGRATEBOT/PLACEHOLDR` alias in the listed flows |
| Unmapped ARNs | Resource types with no name mapping, other instances, other services in the source account | Fix the listed references in the target |
| Users | Each user gets a new random password; SAML or directory based instances may reject passwords | Reset passwords or use your identity provider |

### Phone numbers

How a number gets from the source to the target instance depends on the accounts and Regions ([Move a phone number across instances](https://docs.aws.amazon.com/connect/latest/adminguide/move-phone-number-across-instances.html)). The migration report names the path that applies, computed from the source and target account and Region, in one follow up per path with its reason code, the count and the phone number IDs (never the numbers):

| Source and target | Path | Reason code |
|---|---|---|
| Same account and same Region | Self move with `ListPhoneNumbersV2` and `UpdatePhoneNumber`. This is the tool's opt in phone number step | `PhoneNumberSelfMove` |
| Different account in the same Region, or different Region in the same account | AWS Support moves the numbers: one case per account (Service **Connect (Number Management)**, Category **Phone Number Migration**), scheduled Monday to Friday, about 20 to 30 minutes per number. Calls to a number can fail while it moves. At migration time the number must not be the outbound caller ID on any queue | `PhoneNumberSupportMove` |
| Different account and different Region | Porting | `PhoneNumberPorting` |

In every case, set the outbound caller ID on the target queues again afterwards (the verification lists queues whose caller ID number was dropped).

The self move is disabled by default. With `--enable-phone-number-move` on `deploy.sh` and `--allow-phone-number-move` on `deploy-target-role.sh`, the step calls `UpdatePhoneNumber` with the target role in the target Region, and only when source and target are in the same account and Region. This **moves** the number: it stops working on the source instance. Without the opt in, or for the other two paths, the step moves nothing and only lists the numbers in the report. The step output and the execution input hold phone number IDs and ARNs only.

### Known limits

- AI agents: the first assistant in each account and Region is used, not necessarily the one linked to the instance.
- Prompts are copied through a pre signed URL from the list step; very large prompt sets can outlive the URL or the 256 KB Step Functions payload.
- The quota check covers 14 per instance Connect quotas with their real Service Quotas codes, including Concurrent active calls per instance (target quota compared with the source quota) and Amazon Lex bots per instance. Each value is labelled `applied-instance`, `applied-account` or `aws-default` (used only when no applied value exists); there are no built in guesses, and a value or count that can't be read is `unknown`. Agent status per instance cannot be increased.
- Verification checks names, references and the settings listed under Target verification; it does not compare every attribute (for example routing profile queue priorities or flow logic), and Lambda functions, Lex bots and phone numbers are only covered through the flow content check.

## Cleanup

`scripts/teardown.sh` removes everything the tool deployed and never touches either Amazon Connect Customer instance. Run it with `--dry-run` first to see the plan:

```bash
scripts/teardown.sh --region TOOL_REGION --profile source-account-profile \
  --target-profile target-account-profile --target-region TARGET_REGION --dry-run
```

Without `--dry-run` it asks you to type the environment name (or pass `--yes`). It deletes, in this order: `connect-migration-backend-<env>`, `connect-migration-stepfunctions-<env>` and `connect-migration-lambdas-<env>` (with the tool's IAM roles in them); empties the website, artifacts and access log buckets, every object version and delete marker; deletes `connect-migration-frontend-<env>` (CloudFront, the Cognito user pool and its users, the buckets). With `--target-profile` (or `--target-same-credentials` for a same account run) it also deletes the target role stack and empties and deletes the temporary prompt bucket `connect-migration-prompts-temp-<target account>-<target Region>`. Keep a copy of the access logs first if you need them. If the frontend stack fails to delete because late CloudFront logs arrived in the access log bucket, run the script again.

It does not remove the CloudWatch log groups the functions created, the account level API Gateway CloudWatch role setting, or an ExternalId secret a failed cleanup left behind (see Cleanup failure queue).

The same steps by hand:

1. Target account: `aws cloudformation delete-stack --stack-name connect-migration-target-role`. This removes the role, so the tool can no longer write to the target.
2. Target account: delete the temporary prompt bucket `connect-migration-prompts-temp-<target account>-<target Region>` (objects expire after one day; the bucket stays).
3. Source account, in this order: `connect-migration-backend-<env>`, `connect-migration-stepfunctions-<env>`, `connect-migration-lambdas-<env>`. Then empty the website, artifacts and access log buckets (stack outputs `WebsiteBucket`, `ArtifactsBucket`, `AccessLogsBucket`) and delete `connect-migration-frontend-<env>` (this deletes the Cognito user pool and its users). The website and artifacts buckets are versioned, so delete all object versions and delete markers, for example in the S3 console with **Empty**; `aws s3 rm --recursive` alone leaves the versions behind. Keep a copy of the access logs first if you need them.

Afterwards, review the target instance for placeholder ARNs (`MIGRATION-PLACEHOLDER-`, `MIGRATEBOT`) and resources you do not want to keep, and retire the source instance when the cutover is done. The tool never deletes anything in either instance.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHON=.venv/bin/python scripts/check.sh
```

`scripts/check.sh` runs Python syntax checks, ruff (default rules), the unit tests (botocore Stubber, no AWS calls), cfn-lint on every template and `bash -n` on the scripts.

## Troubleshooting

- `403` with an `action` in the response: the named role lacks that action, or the ExternalId does not match the target trust policy.
- `401`: the session expired; the page sends you back to sign in.
- Step failures are listed in the migration report; details are in the CloudWatch log group of the failing function, for example `aws logs tail /aws/lambda/connect-migration-create-contact-flows-prod --follow`.

## License

This sample is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
