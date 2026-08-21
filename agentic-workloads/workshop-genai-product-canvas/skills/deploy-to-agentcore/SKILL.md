---
name: deploy-to-agentcore
description: >
  Deploy the Biodiversity Anomaly Detection agent to Amazon Bedrock AgentCore
  Runtime using the AgentCore CLI (@aws/agentcore). Use this skill when the user
  asks to deploy, ship, or promote the local agent to AgentCore, or to invoke the
  deployed agent. Fills the gateway env vars, deploys, checks status, and invokes,
  pointing the deployed agent at the pre-provisioned remote Gateway tools.
---

# Deploy the agent to Amazon Bedrock AgentCore Runtime

This skill promotes the agent you ran locally (`agent/agent.py`, your Strands agent
plus `system_prompt.txt`) to a managed AgentCore Runtime using the AgentCore CLI.
The agent logic does not change. The repo already contains the AgentCore CLI
project (`agent/agentcore/`) wired to `agent/agent.py`, so there is no scaffolding
step; you fill in the gateway env vars and deploy (via AWS CDK).

Note: use the AgentCore CLI (`@aws/agentcore`, a Node.js tool). The older
`bedrock-agentcore-starter-toolkit` pip CLI (`agentcore configure` / `launch`) is
deprecated. Flag shapes and the `agentcore/agentcore.json` schema evolve between
versions; if a flag differs, check `agentcore <command> --help` rather than
guessing.

## Preconditions (verify before deploying)

The tool backend and AgentCore Gateway are pre-provisioned in the workshop account
(one CloudFormation stack). Do not run `infra/deploy_tools.sh` or
`infra/create_gateway.py`.

1. `gateway.env` exists in the repo root. If missing, fetch it from Parameter Store
   (the stack wrote it there):
   ```bash
   aws ssm get-parameter --name /touch-grass/gateway-env --with-decryption \
     --query Parameter.Value --output text > gateway.env
   ```
2. `agent/system_prompt.txt` reflects the user's canvas (this is what they built).
3. The AgentCore CLI is installed: `npm install -g @aws/agentcore` (Node.js 20+),
   AWS CDK is installed, and the account/region is bootstrapped (`cdk bootstrap`).
4. AWS credentials are active and Bedrock model access is enabled in the region.

## Steps

### 1. Use the project that ships in the repo

The baseline includes a ready AgentCore CLI project at `agent/agentcore/` (config +
CDK), already wired to `agent/agent.py` (`entrypoint: agent.py`,
`codeLocation: "."`, `runtimeVersion: PYTHON_3_12`). Do NOT run `agentcore create`.
Run all commands from the `agent/` directory:

```bash
cd agent
```

### 2. Fill the Gateway connection into the runtime env vars

`agent/agentcore/agentcore.json` already declares the runtime's `envVars` with
placeholder values `REPLACE_FROM_gateway.env`. Read the repo-root `gateway.env` and
replace each placeholder with the real value: `TOOL_MODE` (stays `gateway`),
`AGENTCORE_GATEWAY_URL`, `COGNITO_TOKEN_URL`, `COGNITO_CLIENT_ID`,
`COGNITO_CLIENT_SECRET`, `COGNITO_SCOPE`. `BEDROCK_MODEL_ID` is already set. Edit the
JSON in place (do not commit the filled-in secrets):

```json
"envVars": [
  { "name": "TOOL_MODE", "value": "gateway" },
  { "name": "AGENTCORE_GATEWAY_URL", "value": "..." },
  { "name": "COGNITO_TOKEN_URL", "value": "..." },
  { "name": "COGNITO_CLIENT_ID", "value": "..." },
  { "name": "COGNITO_CLIENT_SECRET", "value": "..." },
  { "name": "COGNITO_SCOPE", "value": "..." }
]
```

### 3. Deploy

```bash
agentcore deploy -y          # -v for verbose; --dry-run to preview the CDK changes
```

The CLI packages the code (CodeZip, no local Docker), then deploys the runtime and
its IAM role via CDK. AgentCore auto-instruments for Observability. Poll until
ready:

```bash
agentcore status
```

### 4. Invoke the deployed agent

```bash
agentcore invoke "Malayan Tapir dropped to 0 at STN-03 in June 2026. Investigate the probable cause."
```

### 5. Report back to the user

Return the runtime status/endpoint from `agentcore status` and the invocation
result. Then point them to Observability: CloudWatch console -> GenAI Observability
-> Bedrock AgentCore -> the agent, or `agentcore logs` / `agentcore traces list`
(see `observability/README.md`).

## Notes and safety

- Deploying creates real AWS resources (a CDK/CloudFormation stack, IAM role, S3
  staging, the runtime). Mention this before deploying. In Workshop Studio accounts
  it is cleaned up automatically.
- Do not delete or overwrite an existing runtime without confirming with the user.
- To iterate after a code change, re-run `agentcore deploy`.
- To tear down (own account only): `agentcore remove all` then `agentcore deploy`.
- `agent/agent.py` still uses the AgentCore Python SDK (`@app.entrypoint`), which
  remains supported; only the deploy CLI changed.
