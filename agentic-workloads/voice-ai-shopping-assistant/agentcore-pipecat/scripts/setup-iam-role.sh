#!/bin/bash

# Create the IAM execution role AgentCore Runtime assumes to pull the image,
# write logs, and invoke Bedrock models.

set -e

if [ ! -f "./agent/.env" ]; then
    echo "❌ Error: agent/.env not found"
    exit 1
fi

source ./agent/.env

ROLE_NAME="AmazonBedrockAgentCoreSDKRuntime-${AWS_REGION}-aisle"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "Creating IAM execution role for AgentCore..."
echo "Account: $ACCOUNT_ID  Region: $AWS_REGION  Role: $ROLE_NAME"

cat > /tmp/trust-policy.json << 'EOF'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
            "Action": "sts:AssumeRole"
        }
    ]
}
EOF

aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document file:///tmp/trust-policy.json \
    --description "Execution role for AgentCore Runtime (Aisle voice agent)" \
    2>/dev/null || echo "Role already exists, continuing..."

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly \
    2>/dev/null || echo "ECR policy already attached"

cat > /tmp/runtime-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ECRImageAccess",
            "Effect": "Allow",
            "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
            "Resource": ["arn:aws:ecr:${AWS_REGION}:${ACCOUNT_ID}:repository/*"]
        },
        {
            "Sid": "ECRTokenAccess",
            "Effect": "Allow",
            "Action": ["ecr:GetAuthorizationToken"],
            "Resource": "*"
        },
        {
            "Sid": "CloudWatchLogsDescribe",
            "Effect": "Allow",
            "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
            "Resource": ["arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"]
        },
        {
            "Sid": "CloudWatchLogsGroupDescribe",
            "Effect": "Allow",
            "Action": ["logs:DescribeLogGroups"],
            "Resource": ["arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:*"]
        },
        {
            "Sid": "CloudWatchLogsWrite",
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": ["arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"]
        },
        {
            "Sid": "XRayTracing",
            "Effect": "Allow",
            "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
            "Resource": "*"
        },
        {
            "Sid": "CloudWatchMetrics",
            "Effect": "Allow",
            "Action": "cloudwatch:PutMetricData",
            "Resource": "*",
            "Condition": { "StringEquals": { "cloudwatch:namespace": "bedrock-agentcore" } }
        },
        {
            "Sid": "GetAgentAccessToken",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:GetWorkloadAccessToken",
                "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                "bedrock-agentcore:GetWorkloadAccessTokenForUserId"
            ],
            "Resource": [
                "arn:aws:bedrock-agentcore:${AWS_REGION}:${ACCOUNT_ID}:workload-identity-directory/default",
                "arn:aws:bedrock-agentcore:${AWS_REGION}:${ACCOUNT_ID}:workload-identity-directory/default/workload-identity/*"
            ]
        },
        {
            "Sid": "BedrockModelInvocation",
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": [
                "arn:aws:bedrock:*::foundation-model/*",
                "arn:aws:bedrock:${AWS_REGION}:${ACCOUNT_ID}:*"
            ]
        },
        {
            "Sid": "InvokeAgentCoreGateway",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:InvokeGateway"],
            "Resource": ["arn:aws:bedrock-agentcore:${AWS_REGION}:${ACCOUNT_ID}:gateway/*"]
        },
        {
            "Sid": "AgentCoreMemoryDataPlane",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:GetEvent",
                "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:GetMemoryRecord",
                "bedrock-agentcore:ListMemoryRecords",
                "bedrock-agentcore:ListActors",
                "bedrock-agentcore:ListSessions",
                "bedrock-agentcore:GetMemory"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:${AWS_REGION}:${ACCOUNT_ID}:memory/*"]
        }
    ]
}
EOF

aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name AgentCoreRuntimePolicy \
    --policy-document file:///tmp/runtime-policy.json

rm -f /tmp/trust-policy.json /tmp/runtime-policy.json

echo "✅ IAM role ready: $ROLE_ARN"
