# Deployment Guide (Temporal variant)

## Prerequisites

- AWS CLI v2 configured for the target account and region
- Docker installed (ARM64 build support required)
- Node.js 18+ & npm (for the CDK CLI)
- Python 3.11+ & uv
- Temporal Cloud account with a Namespace created
- AWS CLI `bedrock-agentcore` extension (verify with `aws bedrock-agentcore help`)

## 1. Temporal Cloud setup

Create a Namespace in Temporal Cloud and issue an API Key.

```bash
# Store the Temporal API Key in Secrets Manager
aws secretsmanager create-secret \
  --name daf/temporal-api-key \
  --secret-string "<your-temporal-api-key>" \
  --region us-west-2
```

## 2. Deploy CDK infrastructure

```bash
cd cdk
pip install -r requirements.txt

# Install CDK CLI if not already installed
npm install -g aws-cdk

# Review and update cdk.json context values:
# - region: target AWS region (e.g. us-west-2)
# - temporal_address: Temporal Cloud gRPC endpoint
# - temporal_namespace: Temporal Cloud Namespace name

# Bootstrap (first time only)
cdk bootstrap aws://<ACCOUNT_ID>/<REGION>

# Deploy all 3 stacks
cdk deploy --all -c account=<ACCOUNT_ID>
```

Deployed resources:
- **DafNetwork**: VPC (2 AZs, 1 NAT Gateway, public/private subnets)
- **DafAgents**: ECR Repository x4, SSM Parameter x4, Agent IAM Role
- **DafOrchestrator**: ECS Cluster, Fargate Service (ARM64), Security Group

## 3. Build and push Agent container images

```bash
cd agents

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-west-2

# Log in to ECR
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

# Build and push each Agent image (ARM64)
for AGENT in gather analyze evaluate synthesize; do
  docker build --platform linux/arm64 --build-arg AGENT_NAME=${AGENT} \
    -t ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/daf-agent-${AGENT}:latest .
  docker push ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/daf-agent-${AGENT}:latest
done
```

## 4. Deploy AgentCore Runtimes

AgentCore Runtime has no CDK L2 support, so it is created via the AWS CLI.

> **Note on network mode**: This sample uses `PUBLIC` mode for AgentCore Runtimes. Although
> the CDK stack provisions private subnets, AgentCore Runtimes in `VPC` mode are only
> reachable from within the attached VPC — the Orchestrator (ECS Fargate) invokes Agents
> via the AgentCore control-plane endpoint (`bedrock-agentcore.<region>.amazonaws.com`),
> which routes traffic externally. `PUBLIC` mode is therefore required for this
> architecture. All agent endpoints remain IAM-authenticated (SigV4); there is no
> unauthenticated network path. If your security requirements mandate VPC-only traffic,
> consider co-locating the Orchestrator and Agents in the same VPC and switching to
> direct HTTP invocation instead of the AgentCore control-plane route.

```bash
AGENT_ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name DafAgents --region ${REGION} \
  --query "Stacks[0].Outputs[?OutputKey=='AgentRoleArn'].OutputValue" --output text)

for AGENT in gather analyze evaluate synthesize; do
  RUNTIME_ID=$(aws bedrock-agentcore create-agent-runtime \
    --agent-runtime-name "daf_${AGENT}" \
    --description "DAF ${AGENT} agent" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/daf-agent-${AGENT}:latest\"}}" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --role-arn "${AGENT_ROLE_ARN}" \
    --environment-variables '{"AWS_REGION":"'${REGION}'"}' \
    --region ${REGION} \
    --query 'agentRuntimeId' --output text)

  echo "${AGENT}: ${RUNTIME_ID}"

  # Store the ARN in SSM Parameter Store
  aws ssm put-parameter \
    --name "/agents/${AGENT}/arn" \
    --value "arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${RUNTIME_ID}" \
    --type String \
    --overwrite \
    --region ${REGION}
done
```

Wait for all Runtimes to reach ACTIVE status (takes a few minutes):

```bash
aws bedrock-agentcore get-agent-runtime \
  --agent-runtime-id <RUNTIME_ID> \
  --region ${REGION} \
  --query 'status'
```

## 5. Build and push the Orchestrator container image

```bash
cd workflow

docker build --platform linux/arm64 -t daf-orchestrator:latest .

aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

docker tag daf-orchestrator:latest \
  ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/daf-orchestrator:latest
docker push ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/daf-orchestrator:latest
```

## 6. Start the ECS Service

```bash
SERVICE_NAME=$(aws ecs list-services --cluster daf-orchestrator --region ${REGION} \
  --query 'serviceArns[0]' --output text | xargs basename)

aws ecs update-service \
  --cluster daf-orchestrator \
  --service ${SERVICE_NAME} \
  --desired-count 1 \
  --region ${REGION}
```

## 7. Run the demo

```bash
cd workflow
uv run python demo.py "Investigate the latest trends in quantum computing"
```

Set the required environment variables beforehand:

```bash
export TEMPORAL_ADDRESS="<namespace>.tmprl.cloud:7233"
export TEMPORAL_NAMESPACE="<namespace>"
export TEMPORAL_API_KEY="<your-api-key>"
```

The demo CLI submits a workflow to Temporal Cloud and displays live progress:

```
============================================================
  Research Pipeline
  Query: Investigate the latest trends in quantum computing
  Workflow ID: demo-a1b2c3d4
============================================================

DAG: gather → [analyze | evaluate] → (re_analyze?) → synthesize

Progress:
  ✅ gather: completed
  ⏳ analyze: running
  ⏳ evaluate: running
  ⬜ re_analyze: pending
  ⬜ synthesize: pending
```

You can also monitor execution in the Temporal UI at https://cloud.temporal.io.

## Deployment order summary

```
1. Store Temporal API Key in Secrets Manager (manual)
2. cdk deploy --all  (VPC → ECR/SSM → ECS)
3. Build Agent images → push to ECR → create AgentCore Runtimes → update SSM
4. Build Orchestrator image → push to ECR
5. Start ECS Service (desired-count 1)
6. Verify with demo.py
```
