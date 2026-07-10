**English** | [한국어](./README.ko.md)

# rds-mysql-upgrade-agents

A multi-agent package, built on AWS Bedrock AgentCore, that automatically
analyzes MySQL upgrade readiness. It is a self-contained project that can be
deployed straight into a customer AWS account with CDK.

## Supported Versions

| Item | Supported Range |
| --- | --- |
| **MySQL (Source)** | 8.0.x |
| **MySQL (Target)** | 8.4.x |
| **AWS service** | Amazon RDS for MySQL, Amazon Aurora MySQL-compatible |
| **Region** | Validated mainly in `us-east-1`. For other regions, check Bedrock AgentCore availability |
| **CDK** | AWS CDK v2 (Python) |
| **Runtime** | Bedrock AgentCore (Linux ARM64 container) |

> Combinations outside the range above (e.g. MySQL 5.7, MariaDB, MySQL → 9.x)
> are unverified and not guaranteed to work.

## Structure

```
rds-mysql-upgrade-agents/
├── infra/                        # CDK (Python) deployment code (required)
│   ├── app.py
│   ├── cdk.json
│   ├── requirements.txt
│   ├── .env.example
│   ├── cdk_rds_mysql_upgrade/stack.py
│   └── lambda/agent_runtime_cr/handler.py
│
├── agents/                       # Agents — one folder per agent
│   ├── orchestrator/             #   Coordinates the whole pipeline
│   ├── variables-compare/        #   Compares Blue/Green SHOW VARIABLES
│   ├── error-log-analyzer/       #   Analyzes CloudWatch RDS error logs
│   └── upgrade-readiness/        #   InnoDB status + query optimizer risk analysis
│       ├── Dockerfile
│       ├── agent.py
│       └── requirements.txt
│
└── ui/streamlit/                 # (optional) GUI — runs locally
    ├── app.py
    ├── requirements.txt
    ├── .env.example
    └── README.md
```

Each agent folder is self-contained — it ships its own `Dockerfile`, an
`agent.py` entrypoint, and `requirements.txt`. To add/remove/modify an agent
you only touch its folder; add the new slug to the `agents` mapping in
`stack.py` and CDK automatically builds the image and creates the AgentCore
Runtime.

## Resources Created by CDK

- **S3 Bucket** — stores analysis reports (`REPORTS_BUCKET_NAME`)
- **IAM Role** — shared execution role for all 4 agents
  (`bedrock-agentcore.amazonaws.com` trust, Bedrock / CWLogs / ECR / VPC ENI / S3 / AgentCore invoke)
- **ECR Images × 4** — orchestrator, variables-compare, error-log-analyzer, upgrade-readiness (ARM64)
- **AgentCore Runtime × 4** — run inside the customer VPC
- **Lambda** — a CustomResource handler that creates/deletes the AgentCore Runtimes

The VPC, subnets, security groups, and RDS are **assumed to already exist** and
are not created.

## Prerequisites

### Build / deploy tools

- AWS CLI credentials
- Python 3.10+
- Node.js + AWS CDK v2 (`npm install -g aws-cdk`)
- Docker or Finch (must be able to build `linux/arm64`)
- `cdk bootstrap` completed in the target account

### MySQL environment (required at run time)

This package analyzes a **live MySQL Blue-Green Deployment** — it connects to
both instances over the network to read their parameters, status, and
statistics. The following must already be in place **before you run the
orchestrator** (not at deploy time):

- **An active Blue-Green Deployment exists.** The first step
  (`check_blue_green_deployment`) connects to **both** the Blue and Green
  instances; if either connection fails, the workflow stops. A standalone
  instance with no Green is not enough.
- **Blue = MySQL 8.0.x, Green = MySQL 8.4.x.** The analysis is built around this
  upgrade pair.
- **Both instance endpoints are reachable on port 3306** from the subnets /
  security group you give to AgentCore (`SUBNET_IDS` / `SECURITY_GROUP_IDS`).
- **The DB user can read diagnostics.** The user from the secret (or your
  `DB_USER`) needs `SHOW VARIABLES`, `SHOW ENGINE INNODB STATUS`, and `SELECT`
  on `performance_schema`.
- **A Secrets Manager secret with the DB credentials already exists.** Create it
  **before deploying** as JSON (`{"username": "...", "password": "..."}`, the
  format RDS-managed master secrets use) and set its name/ARN in `DB_SECRET_ID`.
  Blue and Green share one secret. The password is never stored in `.env`.
- **The Green instance's error log is exported to CloudWatch Logs** (see
  `GREEN_LOG_GROUP` below). Without it the Error Log Analyzer has nothing to read.

## Deployment Steps

```bash
cd infra

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Open .env and fill in VPC/Subnet/SG/S3/DB values

cdk bootstrap   # once, if this is the first time for the account/region
cdk synth       # validate the template
cdk deploy
```

## Environment Variables to Fill In (`infra/.env`)

| Variable | Description |
| --- | --- |
| `CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION` | Target account / region for deployment |
| `VPC_ID` | Existing VPC ID |
| `SUBNET_IDS` | Private subnets for AgentCore (comma-separated, 2+ recommended) |
| `SECURITY_GROUP_IDS` | SG that allows outbound 3306 to RDS |
| `REPORTS_BUCKET_NAME` | S3 bucket name prefix for reports. The actual bucket name is generated as `<prefix>-<DEPLOYMENT_SUFFIX>` (e.g. `rds-mysql-upgrade-reports-d2be5a`). Only lowercase letters, digits, and `-` are allowed |
| `REPORT_LANGUAGE` | Output language for reports / summary / UI (`ko` = Korean, `en` = English). Optional, default `ko` |
| `BEDROCK_MODEL_ID` | Bedrock model ID used by all agents (injected as an env var). Optional, default `us.anthropic.claude-sonnet-4-6`. Use a geo inference profile prefix (`us.` / `eu.` / …) since `us-east-1` has no in-region endpoint |
| `BLUE_HOST` / `GREEN_HOST` | Blue (MySQL 8.0) / Green (MySQL 8.4) instance host |
| `DB_SECRET_ID` | AWS Secrets Manager secret **name or ARN** holding the DB credentials. The deploy grants the agents' runtime role `secretsmanager:GetSecretValue` on this secret; agents resolve the password at run time |
| `DB_USER` | Optional — overrides the `username` in the secret (leave blank to use the secret's) |
| `GREEN_LOG_GROUP` | CloudWatch Log Group the Error Log Analyzer reads |

> **Credentials via Secrets Manager.** The DB password is **never** stored in
> `.env` or sent in any invocation payload. Store it in a Secrets Manager secret
> — JSON with at least `{"username": "...", "password": "..."}`, the format
> RDS-managed master secrets already use — and point `DB_SECRET_ID` at it. Blue
> and Green are clones, so one secret authenticates both. Connections use TLS
> (`ssl_verify_cert`) against the RDS CA bundle baked into the agent images.
>
> For least privilege, prefer a dedicated read-only monitoring user and the
> reader endpoint over the master `admin` user.

## Report Language

All output — the markdown reports, the LLM summary, the Streamlit UI labels, and
the live progress log — follows a single setting:

```ini
# infra/.env
REPORT_LANGUAGE=ko   # Korean (default)
REPORT_LANGUAGE=en   # English
```

To switch languages:

- **Streamlit UI** — change `REPORT_LANGUAGE` in `infra/.env`, then restart the
  app (`streamlit run app.py`). **No redeploy needed** — the language is passed
  to the agents per run.
- **Direct boto3 calls** — set `"language": "ko"` or `"language": "en"` in the
  orchestrator payload (see the example below). Omitting it defaults to `ko`.

Because the language travels in the request payload, you never need to
`cdk deploy` again just to change it. `cdk deploy` is only required when agent
code changes.

## Deployment Outputs (CloudFormation Outputs)

- `OrchestratorArn` — the main ARN your application invokes
- `VariablesCompareArn` / `ErrorLogAnalyzerArn` / `UpgradeReadinessArn`
- `ReportsBucketName`
- `RuntimeRoleArn`

## Orchestrator Invocation Example

The `<...Arn>` and `<ReportsBucketName>` placeholders below come from the
**CloudFormation Outputs** printed at the end of `cdk deploy` (also listed in
the section above). Replace each placeholder with the matching Output value.

```python
import boto3, json

client = boto3.client("bedrock-agentcore", region_name="us-east-1")

payload = {
    "blue_host":  "blue.xxxx.us-east-1.rds.amazonaws.com",
    "green_host": "green.xxxx.us-east-1.rds.amazonaws.com",
    "db_secret_id": "<Secrets Manager secret name or ARN>",  # password resolved at run time
    "s3_bucket":  "<ReportsBucketName>",  # exposed as a CfnOutput
    "language":   "ko",                   # report language: "ko" or "en" (optional, default "ko")
    "green_log_group":  "/aws/rds/instance/<green>/error",
    "variables_compare_arn":           "<VariablesCompareArn>",
    "error_log_analyzer_arn":          "<ErrorLogAnalyzerArn>",
    "upgrade_readiness_analyzer_arn":  "<UpgradeReadinessArn>",
}

resp = client.invoke_agent_runtime(
    agentRuntimeArn="<OrchestratorArn>",
    runtimeSessionId="customer-run-" + "x" * 20,   # must be 33+ characters
    payload=json.dumps(payload).encode(),
)
print(resp["response"].read().decode())
```

## Update / Teardown

```bash
# After modifying agent code, rebuild images + refresh the runtimes
cdk deploy

# Remove everything (S3 is RETAIN, so it stays)
cdk destroy
```

## (Optional) Streamlit UI

If you'd rather run a GUI than call boto3 directly, see `ui/streamlit/`. It's a
simple app launched locally with `streamlit run app.py` (not deployed by CDK —
runs only on your PC).

The UI **automatically shares** `infra/.env`, so you don't need to re-enter
VPC/DB/host values. In `ui/.env` you only paste the **4 Agent ARNs** produced
by `cdk deploy`.

```bash
cd ui/streamlit
pip install -r requirements.txt
cp .env.example .env        # enter only the 4 ARNs
streamlit run app.py
```

For details, see [ui/streamlit/README.md](./ui/streamlit/README.md).

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Docker build fails | Make sure Docker Desktop is running; macOS is ARM64-native so it's fine |
| `Cannot connect to the Docker daemon` (when using Finch) | Run `finch vm start`, then `export CDK_DOCKER=finch` |
| `CREATE_FAILED` (AgentRuntime) | Check CloudWatch Logs `/aws/lambda/*-AgentRuntimeCrHandler-*` |
| Agent can't connect to RDS | Check outbound 3306 on `SECURITY_GROUP_IDS` + inbound on the RDS SG |
| `cdk bootstrap` required error | Run it once in the account/region |
