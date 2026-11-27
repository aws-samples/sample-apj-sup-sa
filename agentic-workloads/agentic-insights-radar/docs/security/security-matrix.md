# Security matrix: Agentic Insights Radar on AWS

One row per AWS service in the deployed stack (default settings). "Data key" is the stack's AWS
KMS customer managed key with automatic rotation (`infra/insights_radar_infra/data.py`). File paths
are relative to the repository root. The threat model is in [threat-model.md](threat-model.md).

## Services

| Service | Data stored or processed | Encryption at rest | Encryption in transit | Access control and IAM scope | Logging | Public exposure | Relevant suppressions |
|---|---|---|---|---|---|---|---|
| Amazon CloudFront | Web app files, `/api/*` requests and responses (not cached) | n/a (no caching of API responses) | HTTPS only to viewers (`REDIRECT_TO_HTTPS`, `HTTPS_ONLY` on `/api/*`); HTTPS only to the API origin; default `*.cloudfront.net` certificate | Origin access control to the site bucket; response headers policy with CSP, HSTS, frame denial, `nosniff`, referrer policy | Standard access logs to the access-log bucket (`cdn/`) | Yes: the app URL | cdk-nag CFR1, CFR4; Checkov CKV_AWS_174; cfn_nag W70 |
| AWS WAF | Request metadata | Request log: the data key in `us-east-1`, the edge stack's own key elsewhere | n/a | Web ACL (CLOUDFRONT scope) on the distribution: per-IP rate limit 2,000, Core rule set, Known bad inputs, Amazon IP reputation | Full request log to `aws-waf-logs-<prefix>-<region>` in `us-east-1` with the `authorization`, `cookie` and `x-mr-run-token` headers redacted; CloudWatch metrics and sampled requests per rule | Inline on CloudFront | none |
| Amazon S3: site bucket | Built web app | SSE-S3 | TLS only (bucket policy) | Block Public Access; read by CloudFront through origin access control; written by CDK `BucketDeployment`; versioned (old versions kept 30 days) | Server access logs (`site/`) | No (through CloudFront only) | none |
| Amazon S3: assets bucket | Uploaded documents, crawled pages, metadata sidecars, drafts, radar version snapshots | SSE-KMS with the data key, S3 Bucket Key | TLS only (bucket policy); presigned POST over HTTPS | Block Public Access; per-function grants; knowledge base role reads `docs/` only; CORS allows POST from the app URL only; versioned | Server access logs (`assets/`) | No (presigned POST for uploads) | cdk-nag IAM5 (S3 grant wildcards) |
| Amazon S3: access-log bucket | CloudFront and S3 access logs | SSE-S3 (S3 server access logging cannot write to SSE-KMS) | TLS only | Block Public Access; bucket-owner-preferred ownership; expiry after `logRetentionDays` | n/a | No | Checkov CKV_AWS_18, CKV_AWS_21; cfn_nag W35 |
| Amazon Cognito | User accounts, group membership, TOTP secrets | AWS managed | HTTPS | Self sign-up off; required TOTP MFA; strong password policy; public PKCE clients that allow only the hosted UI and refresh tokens (SRP on the MCP client only with `e2eSignIn=true`); token revocation; deletion protection when retaining data | Cognito events in AWS CloudTrail | Hosted UI (classic) domain `<prefix>-<account>.auth.<region>.amazoncognito.com`; the user pool's own endpoints, outside the WAF | cdk-nag COG8 |
| Amazon API Gateway (HTTP API) | User API requests | n/a | HTTPS (TLS) | JWT authorizer (this pool, the two app clients) on `/api/*` except `GET /api/config`; stage throttle 50/100, search and product questions 10/20 | JSON access logs (no headers or query strings) to a KMS-encrypted log group | Yes: the default `execute-api` endpoint, in addition to CloudFront | cdk-nag APIG4 |
| AWS Lambda function URLs (2) | Agent tool calls; chat budget reservations and usage | n/a | HTTPS (TLS) | `AWS_IAM` auth, no resource policy: callers from other accounts are refused before the function runs; only the module runtime role (`AgentTools`) and the chat runtime role (`ChatUsage`) are granted `lambda:InvokeFunctionUrl`, and the backend checks the caller's role again. `ChatUsage` may touch only the chat budget partitions of the ops table (`dynamodb:LeadingKeys`) and may not scan it; `AgentTools` may not list the assets bucket outside `radars/` and `drafts/`. With `reservedConcurrency=true` each is capped at 10 concurrent executions | The functions' log groups | Yes: HTTPS endpoints, IAM signature required | none |
| AWS Lambda (10 functions) | Request handling, agent tool gate, chat usage, run dispatch, indexing, crawling, source watching, seeding | Environment variables encrypted with the data key | HTTPS to AWS APIs; the crawler and the source watcher fetch public https pages only, through the SSRF guard, and honour robots.txt | One IAM role per function, scoped to named resources; no `AWSLambdaBasicExecutionRole` (log access to its own log group only) | KMS-encrypted log group per function; X-Ray active tracing | No | Checkov CKV_AWS_117, CKV_AWS_115, CKV_AWS_116; cfn_nag W89, W92, W58, W12, W76; cdk-nag IAM5 |
| AWS Step Functions | Module run input (includes the run token) and outcome | AWS managed | HTTPS | Started by the API and the sweep only; the module runtime role may only send task success, failure and heartbeat to this state machine. The execution history keeps the input, run token included, for 90 days: limit `states:GetExecutionHistory` and `DescribeExecution` to operators | All events to a KMS-encrypted log group, **without execution data**; X-Ray tracing; a metric filter counts task and execution timeouts | No | cdk-nag IAM5 (function qualifiers) |
| Amazon EventBridge | 5-minute sweep schedule; 2-hour source-watch schedule | n/a | n/a | One rule targets the sweep function, the other the watch function; failures go to the encrypted `FailedEvents` queue (key policy limited to these two rules' ARNs) | n/a | No | none |
| Amazon DynamoDB (2 tables) | Knowledge graph; modules, sources, runs, budgets, products, radars, feedback, audit log | Data key | HTTPS | Per-function read or read-write grants; GSI query access | Stream on the graph table feeds the indexer; CloudTrail for control plane | No | cdk-nag IAM5 (GSI wildcard) |
| Amazon S3 Vectors | Graph node embeddings and titles; knowledge base chunks and text | SSE-KMS with the data key (key policy allows the S3 Vectors indexing service for this account's vector buckets) | HTTPS | API: query and get on the graph index; indexer: put and delete on the graph index; knowledge base role: the knowledge index only | CloudTrail | No | none |
| Amazon Bedrock Knowledge Bases | Product documents and crawled pages, chunked and embedded | Vectors in S3 Vectors (data key); data source transient data encrypted with the data key | HTTPS | Service role trusted only for this account's knowledge bases; reads `docs/` in the assets bucket; invokes only Titan Text Embeddings V2 | CloudTrail | No | none |
| Amazon Bedrock models | Prompts, tool results and responses for modules and chat; embeddings | Not stored by Bedrock | HTTPS | Runtime roles may invoke only the model catalog's six `global.` inference profiles and their models; the API, the agent tool gate and the indexer may invoke only Titan Text Embeddings V2 | CloudTrail; token usage recorded per run | No | cdk-nag IAM5 (global inference profile Region wildcard) |
| Amazon Bedrock Guardrails | Users' chat messages and run start messages, model responses (never tool results) | Data key (customer managed KMS) | HTTPS | `ApplyGuardrail` granted on this guardrail only; prompt-attack filter (input, MEDIUM), content filters, and AWS keys and card numbers masked in output | CloudTrail | No | none |
| Amazon Bedrock AgentCore Runtime: module runtime | Module runs (agent code, prompts, tool results) | Code bundle in the CDK asset bucket; runtime managed by AWS | HTTPS; SigV4 to the `AgentTools` Lambda function URL (the agent routes are not on the HTTP API) | IAM inbound authorization; role may invoke models, apply the guardrail, decrypt with the data key, call the `AgentTools` function URL, send task tokens, use AWS-managed Browser and Code Interpreter and the web-search gateway; runtime-side tools (including `http_fetch`) are capped per run, at most 2 browser or code-interpreter sessions per run, and every address the model sends the browser to must be a public https address | AgentCore runtime log groups and metrics | No (IAM-authenticated endpoint) | cdk-nag IAM5 (runtime log groups, workload identity, asset bucket, endpoints, browser and code interpreter sessions) |
| Amazon Bedrock AgentCore Runtime: chat runtime | Chat turns | Code bundle in the CDK asset bucket; runtime managed by AWS | HTTPS from the browser; the user's JWT to the API | Cognito JWT inbound authorizer (this pool's discovery URL, app client audiences; only the `Authorization` header passes through); role may invoke only the configured `chatModel`'s inference profile (not the whole catalog), apply the guardrail, decrypt with the data key, call the `ChatUsage` function URL, read, write and delete chat memory events (per-user isolation is in code: AgentCore Memory has no actor condition key, see the threat model) | AgentCore runtime log groups (data key, `agentLogRetentionDays`) and metrics; sessions end after 5 idle minutes | Yes: HTTPS endpoint, JWT required | cdk-nag IAM5 (as above) |
| Amazon Bedrock AgentCore Memory | Chat history per user and session | Data key (customer managed KMS) on a new install; a deployment created before it keeps an AWS-owned key (`chatMemoryKey=aws-owned`) | HTTPS | Chat runtime role: read short-term memory, write events, and delete events (a guardrail-redacted turn replaces its stored message) | CloudTrail | No | none |
| Amazon Bedrock AgentCore Gateway (optional) | Web-search queries and results | Data key (customer managed KMS) | HTTPS | IAM inbound authorization; only the module runtime role may invoke it; gateway role may invoke only the managed web-search tool | CloudTrail | No | none |
| AgentCore Browser and Code Interpreter | Browsing sessions; code run by agents | AWS managed sandboxes | HTTPS | Module runtime role: session actions on the AWS-managed tools only | CloudTrail | No | cdk-nag IAM5 (session wildcard) |
| AWS KMS | Data key; run-token HMAC_256 key; outside `us-east-1`, the edge stack's WAF log key | HSM-backed | HTTPS | Data key: grants per function and service principals limited by source account, source ARN or encryption context; HMAC key: `GenerateMac` for API and sweep, `VerifyMac` for the API and the agent tool gate | CloudTrail management events record every MAC operation (90 days in Event history without a trail) | No | Checkov CKV_AWS_7; cfn_nag F19 (HMAC key rotation); cdk-nag IAM5 (KMS grant variants) |
| Amazon SQS (2 queues) | Failed indexer batches; failed crawls and sweeps | Data key | TLS only (queue policy) | Writers: the event source mapping, the crawler, the sweep and the EventBridge rule | CloudWatch metrics and alarms | No | cdk-nag SQS3 |
| Amazon SNS | Alarm and budget notifications | Data key | TLS only (topic policy) | CloudWatch alarms in this account publish (key policy limited by source account and alarm ARN), and AWS Budgets with `monthlyBudgetUsd`; an email subscription to the alarm email or the first administrator | CloudTrail | No | none |
| Amazon CloudWatch | Logs, metrics, 46 alarms (42 outside us-east-1), a dashboard | Log groups encrypted with the data key (key policy limited to this account's log groups) | HTTPS | Each function writes only its own log group | Retention `logRetentionDays` (default 365); AgentCore runtime logs `agentLogRetentionDays` (default 30) | No | Checkov CKV_AWS_158 and cfn_nag W84 (CDK helper log groups only) |
| AWS Budgets (optional) | The account's monthly cost | n/a | HTTPS | Notifies the alarm topic at 80% actual and 100% forecast | n/a | No | none |
| AWS Backup (optional) | Daily recovery points of both tables and the assets bucket, 120 days | Vault encrypted with the data key | HTTPS | AWS Backup's service role with its own managed backup and restore policies | CloudTrail | No | cdk-nag IAM4 (AWS Backup service-role policies) |
| AWS X-Ray | Traces of Lambda and Step Functions | AWS managed | HTTPS | Write-only tracing permissions added by CDK | X-Ray | No | cdk-nag IAM5 (`Resource::*`) |
| AWS IAM | Roles for 9 functions, 2 runtimes, the knowledge base, the gateway and CDK helpers | n/a | n/a | One role per component; no policy grants every action (tested in `infra/tests/test_stack.py`) | CloudTrail | n/a | cfn_nag W12, W76; cdk-nag IAM4, IAM5 (CDK `BucketDeployment` and `AwsCustomResource` only for IAM4) |

## Scanner suppressions

Every suppression below is copied from the source that applies it. A suppression names the
mechanism that makes the finding inapplicable.

### cdk-nag (`infra/insights_radar_infra/nag.py`)

`AwsSolutions-IAM5` suppressions are stack-wide but matched by `applies_to` regular expressions, so
a new wildcard that is not listed still fails synthesis. `infra/tests/test_docs.py` checks that
every row below matches the code word for word.

| Rule | Applies to | Reason |
|---|---|---|
| AwsSolutions-IAM5 | `/^Resource::arn:<AWS::Partition>:states:(<AWS::Region>\|[a-z0-9-]+):(<AWS::AccountId>\|[0-9]+):execution:\{"Fn::Select":\[6,.+\}:\*$/g` | states:StopExecution for an administrator's Cancel of a module run: execution ARNs are <state machine name>:<execution name>, and each run names its own execution, so the grant is on this one state machine's executions. |
| AwsSolutions-IAM5 | `/^Resource::\*$/g` | X-Ray, CloudWatch Logs delivery and cloudwatch:PutMetricData do not support resource-level permissions; CDK adds them for Lambda/Step Functions tracing, Step Functions logging and the AgentCore runtime's metrics. |
| AwsSolutions-IAM5 | `/^Action::kms:(GenerateDataKey\|ReEncrypt)\*$/g` | CDK KMS grants on the single data key: GenerateDataKey* and ReEncrypt* cover the variants (WithoutPlaintext, From/To) one encrypt/decrypt grant needs. The resource is the key itself. |
| AwsSolutions-IAM5 | `/^Action::s3:(List\|GetObject\|GetBucket\|DeleteObject\|Abort)\*$/g`, `/^Resource::<DataAssets[0-9A-F]+\.Arn>/(\*\|docs/\*\|radars/\*\|drafts/\*)$/g` | CDK S3 grants on one bucket (the assets bucket): object-level actions need the object wildcard (the crawler, the sweep and the agent tool gate are limited to their own docs/, radars/ and drafts/ prefixes), and the action wildcards are CDK's standard read/write/delete sets for that bucket only. |
| AwsSolutions-IAM5 | `/^Resource::<Data(Graph\|Ops)Table[0-9A-F]+\.Arn>/index/\*$/g` | Query access to this solution's own tables' global secondary indexes (gsi1). |
| AwsSolutions-IAM5 | `/^Resource::arn:<AWS::Partition>:bedrock:\*::foundation-model/(anthropic\|amazon)\.[a-z0-9.:-]+$/g` | Global cross-Region inference: a global profile routes a request to any Region, so the model ARN of each catalog profile needs a Region wildcard. Only the catalog's own models are named. |
| AwsSolutions-IAM5 | `/^Resource::arn:<AWS::Partition>:logs:.*:log-group:\*$/g`, `/^Resource::arn:<AWS::Partition>:logs:.*/aws/bedrock-agentcore/runtimes/\*.*$/g`, `/^Resource::arn:<AWS::Partition>:bedrock-agentcore:.*:workload-identity-directory/default/workload-identity/\*$/g` | AgentCore runtime defaults from the CDK L2: its own log groups (named after the runtime id, unknown before creation) and its workload identity in the account's default directory. |
| AwsSolutions-IAM5 | `/^Resource::arn:<AWS::Partition>:s3:::cdk-[a-z0-9]+-assets-[0-9]{12}-[a-z0-9-]+/\*$/g` | AgentCore direct code deployment: the runtime role reads its code zip from the CDK bootstrap asset bucket (read-only; the CDK L2 adds this grant). An explicit deny on the same role blocks s3:GetObject* on every object except the runtime's own code zip. |
| AwsSolutions-IAM5 | `/^Resource::arn:<AWS::Partition>:bedrock-agentcore:<AWS::Region>:aws:(browser\|code-interpreter)/\*$/g` | AWS-managed Browser and Code Interpreter (account segment 'aws'); session ids are created at run time, so the session ARN is a wildcard under the managed tool. |
| AwsSolutions-IAM5 | `/^Resource::<Backend(Invoke\|Finalize)[0-9A-F]+\.Arn>:\*$/g` | Step Functions invokes these functions by ARN with any version/alias qualifier (CDK LambdaInvoke). |
| AwsSolutions-IAM5 | `/^Resource::arn:<AWS::Partition>:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/[a-z0-9_]+_(modules\|chat)-\*$/g` | With retainData=false the stack deletes an AgentCore runtime's log group, including the old one after the runtime is replaced (a new runtime ID): DeleteLogGroup on that runtime's own name prefix. |
| AwsSolutions-IAM5 | `/^Resource::<AgentsModuleRuntime[0-9A-F]+\.AgentRuntimeArn>/\*$/g` | InvokeAgentRuntime is authorised on the runtime and its endpoints (qualifiers such as DEFAULT). |
| AwsSolutions-IAM5 | `/^Resource::arn:<AWS::Partition>:execute-api:<AWS::Region>:<AWS::AccountId>:<BackendApi[0-9A-F]+>/\*/\*/iam/api/\*$/g` | The MCP role (deploy options mcpIssuer/mcpAudience) may invoke this API's /iam/api routes only: an execute-api ARN is <api>/<stage>/<method>/<path>, and the route is a proxy over every method and path below /iam/api, so stage, method and the path tail are wildcards on this one API. |

Suppressions for other rules, each on the one resource it is about rather than on the stack: a
stack-wide APIG4 suppression would also cover a second unauthenticated route, and a stack-wide SQS3
suppression a new queue without a dead-letter queue.

| Rule | Resource (construct path in the stack) | Reason |
|---|---|---|
| AwsSolutions-COG8 | `Auth/Users/Resource` | Threat protection needs the Cognito Plus plan (per active user cost). The pool uses required TOTP MFA, admin-only sign-up and a strong password policy; the implementation guide explains how to switch to Plus. |
| AwsSolutions-CFR1 | `Web/Cdn/Resource` | Audience is the deploying organisation's staff worldwide; geo restriction is a deployment choice documented in the implementation guide. |
| AwsSolutions-CFR4 | `Web/Cdn/Resource` | The distribution uses the default *.cloudfront.net certificate (no custom domain, so the solution deploys without ACM or Route 53); its viewer TLS policy is fixed by CloudFront. The implementation guide shows adding a custom domain with an ACM certificate and TLSv1.2_2021. |
| AwsSolutions-APIG4 | `Backend/Api/GET--api--config/Resource` | Only GET /api/config is unauthenticated: the web app reads its sign-in settings there. It returns the Region, user pool, app client and Cognito domain, and the chat runtime ARN (which includes the account id). Every other route has a JWT authorizer, and this suppression covers this route only. |
| AwsSolutions-SQS3 | `Backend/IndexerDlq/Resource` | This queue is the indexer's dead-letter queue; a DLQ for a DLQ is not meaningful. |
| AwsSolutions-SQS3 | `Backend/FailedEvents/Resource` | This queue is the dead-letter queue for the crawler and the scheduled sweep, which alarms when anything lands in it; a DLQ for a DLQ is not meaningful. |
| AwsSolutions-APIG4 | `Backend/Api/GET--api--digest--unsubscribe/Resource` | The digest emails' one-click unsubscribe (RFC 8058): the mail client posts it without a session. The HMAC-signed token in the link names one subscription and can only turn that one off; GET only shows a confirmation page. The route has its own throttle of 5 requests a second, and this suppression covers this route only. |
| AwsSolutions-APIG4 | `Backend/Api/POST--api--digest--unsubscribe/Resource` | The digest emails' one-click unsubscribe (RFC 8058): the mail client posts it without a session. The HMAC-signed token in the link names one subscription and can only turn that one off; GET only shows a confirmation page. The route has its own throttle of 5 requests a second, and this suppression covers this route only. |
| AwsSolutions-SMG4 | `Backend/Digest/SlackSecret/Resource` | Slack issues the bot token and the incoming-webhook URL and Secrets Manager cannot rotate them; the operator puts a new value when Slack reissues one. Only the digest sender and the API may read it. |
| AwsSolutions-SMG4 | `Backend/Digest/UnsubscribeKey/Resource` | Rotating the unsubscribe HMAC key would break the one-click unsubscribe link in every digest email already sent, which must keep working. It signs links that each turn off one subscription and encrypts no data. |

Suppressions on CDK's own `BucketDeployment` and `AwsCustomResource` helpers only (applied to those
constructs and their children; the `AwsCustomResource` helper sets the AgentCore runtime log groups'
data key and retention):

| Rule | Reason |
|---|---|
| AwsSolutions-L1 | CDK's BucketDeployment singleton Lambda: CDK pins its runtime; it runs only during deployment. |
| AwsSolutions-IAM4 | CDK's BucketDeployment singleton Lambda uses AWSLambdaBasicExecutionRole for its logs; CDK owns it. |
| AwsSolutions-IAM5 | CDK's BucketDeployment copies the site from the CDK asset bucket into the site bucket; CDK generates the object-level grants on those two buckets. |
| AwsSolutions-IAM4 | CDK's AwsCustomResource singleton Lambda uses AWSLambdaBasicExecutionRole for its logs; CDK owns it. Its SDK-call policy is scoped to the two runtime log groups. |
| AwsSolutions-L1 | CDK's AwsCustomResource singleton Lambda: CDK pins its runtime; it runs only during deployment. |

With `backupPlan=true`, on the backup plan's construct only:

| Rule | Applies to | Reason |
|---|---|---|
| AwsSolutions-IAM4 | `AWSBackupServiceRolePolicyForBackup`, `AWSBackupServiceRolePolicyForRestores`, `AWSBackupServiceRolePolicyForS3Backup`, `AWSBackupServiceRolePolicyForS3Restore` | AWS Backup's own service-role policies for backing up and restoring the selected tables and bucket; AWS maintains them as the services add backup actions. |

### Checkov and cfn_nag (`infra/insights_radar_infra/scanners.py`)

Skips are written into each matching resource's `Metadata` (never stack-wide), so a new resource is
scanned in full.

| Resources | Tool | Rule | Reason |
|---|---|---|---|
| Solution Lambda functions (all) | checkov | CKV_AWS_117 | No VPC: the functions reach only AWS service endpoints and, for the crawler and the source watcher, the public internet; there is nothing private to reach, and a VPC would add NAT cost and no isolation. |
| Solution Lambda functions (all) | cfn_nag | W89 | No VPC: the functions reach only AWS service endpoints and, for the crawler and the source watcher, the public internet; there is nothing private to reach, and a VPC would add NAT cost and no isolation. |
| Solution Lambda functions (all) | checkov | CKV_AWS_115 | No reserved concurrency by default: new accounts have a 10-execution regional quota, and reserving any of it makes the deployment fail. reservedConcurrency=true reserves some for the run bookkeeping functions and caps the two agent gates at 10 each. User traffic is bounded by WAF rate limits and API throttling; agent traffic reaches neither, so it is bounded by the per-module token budgets, the per-run tool caps and each module's max_concurrency instead. |
| Solution Lambda functions (all) | cfn_nag | W92 | No reserved concurrency by default: new accounts have a 10-execution regional quota, and reserving any of it makes the deployment fail. reservedConcurrency=true reserves some for the run bookkeeping functions and caps the two agent gates at 10 each. User traffic is bounded by WAF rate limits and API throttling; agent traffic reaches neither, so it is bounded by the per-module token budgets, the per-run tool caps and each module's max_concurrency instead. |
| Solution Lambda functions (all) | cfn_nag | W58 | Log permissions are granted on the function's own log group in its role's separate default policy, which this rule does not inspect. |
| Synchronous or stream-driven functions: BackendApiHandler, BackendInvoke, BackendFinalize, BackendIndexer, BackendSeed, BackendAgentTools, BackendChatUsage | checkov | CKV_AWS_116 | Invoked synchronously (API Gateway, a function URL, Step Functions, a deploy-time trigger) or from a DynamoDB stream with its own on-failure queue; the caller receives the error, so a function-level DLQ would never receive anything. |
| Digest sender (BackendDigestSend, digestDelivery on), SQS-driven | checkov | CKV_AWS_116 | Invoked by an SQS event source mapping: a message that fails returns to its queue, and after three receives the queue's own redrive policy moves it to an alarmed dead-letter queue, so a function-level DLQ would never receive anything. |
| CDK-owned helper functions, log groups and policies: CustomCDKBucketDeployment, AWSCDKTriggerCustomResourceProvider, CustomS3AutoDeleteObjectsCustomResourceProvider, AWSCDKCfnUtilsProvider, AwsCustomResource (AWS679f53...) | checkov | CKV_AWS_116 | CDK-owned deployment helper; runs only during stack operations and reports to CloudFormation. |
| CDK-owned helper functions, log groups and policies: CustomCDKBucketDeployment, AWSCDKTriggerCustomResourceProvider, CustomS3AutoDeleteObjectsCustomResourceProvider, AWSCDKCfnUtilsProvider, AwsCustomResource (AWS679f53...) | checkov | CKV_AWS_173 | CDK-owned deployment helper; its environment holds no secrets. |
| CDK-owned helper functions, log groups and policies: CustomCDKBucketDeployment, AWSCDKTriggerCustomResourceProvider, CustomS3AutoDeleteObjectsCustomResourceProvider, AWSCDKCfnUtilsProvider, AwsCustomResource (AWS679f53...) | checkov | CKV_AWS_158 | CDK-owned deployment helper's log group; no application data is logged. |
| CDK-owned helper functions, log groups and policies: CustomCDKBucketDeployment, AWSCDKTriggerCustomResourceProvider, CustomS3AutoDeleteObjectsCustomResourceProvider, AWSCDKCfnUtilsProvider, AwsCustomResource (AWS679f53...) | cfn_nag | W84 | CDK-owned deployment helper's log group; no application data is logged. |
| CDK-owned helper functions, log groups and policies: CustomCDKBucketDeployment, AWSCDKTriggerCustomResourceProvider, CustomS3AutoDeleteObjectsCustomResourceProvider, AWSCDKCfnUtilsProvider, AwsCustomResource (AWS679f53...) | checkov | CKV_AWS_111 | CDK-owned deployment helper's policy, scoped by CDK to the buckets it copies between. |
| IAM policies of Backend, Agents and the CDK BucketDeployment | cfn_nag | W12 | Resource '*' only on actions with no resource-level permissions: X-Ray, CloudWatch Logs delivery and cloudwatch:PutMetricData, plus the CloudFront invalidation actions CDK's own BucketDeployment role asks for unscoped (those two do support a distribution ARN; the role is CDK's, not this solution's). nag.STAR_RESOURCE_ACTIONS lists them and a template test fails on any other action with Resource '*'. |
| IAM policies of Backend, Agents and the CDK BucketDeployment | cfn_nag | W76 | One role per function: its policy lists that function's grants, each scoped to named resources of this solution. Splitting it would not reduce what the role can do. |
| Run-token KMS key (DataRunTokenKey) | checkov | CKV_AWS_7 | HMAC KMS keys do not support automatic rotation. The key signs short-lived run tokens and encrypts no data. |
| Run-token KMS key (DataRunTokenKey) | cfn_nag | F19 | HMAC KMS keys do not support automatic rotation. The key signs short-lived run tokens and encrypts no data. |
| CloudFront distribution | checkov | CKV_AWS_174 | Default *.cloudfront.net certificate, whose TLS policy CloudFront fixes. The implementation guide shows adding a custom domain with TLSv1.2_2021. |
| CloudFront distribution | cfn_nag | W70 | Default *.cloudfront.net certificate, whose TLS policy CloudFront fixes. The implementation guide shows adding a custom domain with TLSv1.2_2021. |
| Access-log bucket (DataAccessLogs) | checkov | CKV_AWS_18 | This is the access-log bucket; logging it into itself would loop. |
| Access-log bucket (DataAccessLogs) | cfn_nag | W35 | This is the access-log bucket; logging it into itself would loop. |
| Access-log bucket (DataAccessLogs) | checkov | CKV_AWS_21 | Access logs are append-only and expire by lifecycle rule; versioning adds cost and no recovery. |

### ASH, Bandit and Semgrep (`.ash.yaml`)

ASH runs with `severity_threshold: MEDIUM` on a snapshot of git-tracked files. Suppressions match a
path glob and a rule id.

| Path | Rule | Reason |
|---|---|---|
| `**/deployment/build.py` | B103 | normalise() sets 755 on directories and executables and 644 on other files inside the Lambda and AgentCore bundles. Both services run the code as a different user from the file owner, so the files must be world-readable; nothing in a bundle is secret. |
| `**/deployment/build.py` | python.lang.security.audit.insecure-file-permissions.insecure-file-permissions | Same normalise() call as B103: deployment package permissions that Lambda and AgentCore require (world-readable code, no secrets in the bundle). |
| `**/*.py` | python.lang.compatibility.python37.python37-compatibility-importlib2 | Flags importlib.resources for Python < 3.7. Every package declares requires-python >= 3.12. |
| `**/tests/**/*.py` | B101 | assert in pytest tests is how a test asserts. B101 targets asserts in production code (removed under python -O); production modules are outside tests/. |

Ignored path: `**/.ash.yaml` (the configuration file itself: its reasons quote the strings they
justify, so scanning it re-detects them).

### Inline suppressions in code

| Location | Rule | Reason given in the code |
|---|---|---|
| `deployment/build.py`, `run()` | Ruff `S603` | fixed argument lists, no shell |
| `deployment/build.py`, `normalise()` | Ruff `S103` | deployment package permissions |
| `frontend/src/lib/html.ts` | ESLint `no-control-regex` | control characters are exactly what this strips |
| `frontend/src/hooks/useAsync.ts` | ESLint `react-hooks/exhaustive-deps` | none (a hook dependency list; not a security rule) |
