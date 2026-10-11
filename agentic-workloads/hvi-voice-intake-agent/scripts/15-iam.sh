#!/usr/bin/env bash
# Create the IAM roles used by the health voice intake stack.
#   hvi-lambda-role     -> orchestrator, SMA handler, confirmation sender, callback sweeper
#   hvi-agentcore-role  -> Bedrock AgentCore runtime execution role
# Idempotent.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

ACC="$AWS_ACCOUNT_ID"

role_exists() { aws iam get-role --role-name "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- Lambda role
LAMBDA_ROLE="${PREFIX}-lambda-role"
if role_exists "$LAMBDA_ROLE"; then
  hvi_warn "role $LAMBDA_ROLE exists - updating policy only"
else
  hvi_log "creating role $LAMBDA_ROLE"
  aws iam create-role --role-name "$LAMBDA_ROLE" \
    --description "Execution role for hvi voice-intake Lambdas" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]
    }' \
    --tags Key=Project,Value=health-voice-intake >/dev/null
  hvi_record "iam.$LAMBDA_ROLE" "created"
fi

aws iam attach-role-policy --role-name "$LAMBDA_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole >/dev/null
# Needed because the orchestrator and Neptune client run inside the VPC.
aws iam attach-role-policy --role-name "$LAMBDA_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole >/dev/null

# OriginateThroughAsterisk / OriginateReadResult were missing until the outbound
# dialler was deployed, and their absence was invisible: TelephonyControl still
# grants chime:CreateSipMediaApplicationCall, which is how the dialler USED to
# place calls. It stopped - the SMA cannot play and listen at once, so barge-in is
# impossible on it - and now originates through our own Asterisk over SSM. The
# policy kept describing the abandoned approach, so the role looked complete while
# lacking the one permission the code needs. ssm:SendCommand is scoped to the
# RunShellScript document and this account's instances; GetCommandInvocation and
# DescribeInstances take no resource ARNs.
#
# InvokeHviGateway is only exercised when HVI_SF_VIA_GATEWAY=1, which routes
# Salesforce through an AgentCore Gateway so the OAuth token comes from the Identity
# vault rather than being negotiated inside hvi_salesforce.py. It is granted whether
# or not the flag is on, so flipping the flag is an environment change and not an IAM
# change - which is what keeps the rollback to one command. Scoped to the gateway
# name prefix rather than "*", because that is the only gateway these lambdas have
# any business calling. Note the gateway ALSO needs its own grants, on its own role:
# secretsmanager:GetSecretValue for the referenced Salesforce secret, plus
# GetPolicyEngine and PartiallyAuthorizeActions when a policy engine is attached.
cat > /tmp/hvi-lambda-inline.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DataLayer",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:DeleteItem",
        "dynamodb:Query","dynamodb:Scan","dynamodb:BatchGetItem","dynamodb:BatchWriteItem"
      ],
      "Resource": [
        "arn:aws:dynamodb:*:${ACC}:table/${PREFIX}-*",
        "arn:aws:dynamodb:*:${ACC}:table/${PREFIX}-*/index/*"
      ]
    },
    {
      "Sid": "BedrockReasoning",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream","bedrock:Converse","bedrock:ConverseStream"],
      "Resource": "*"
    },
    {
      "Sid": "AgentCoreInvoke",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:InvokeAgentRuntime",
        "bedrock-agentcore:CreateEvent","bedrock-agentcore:ListEvents","bedrock-agentcore:GetEvent",
        "bedrock-agentcore:RetrieveMemoryRecords","bedrock-agentcore:ListSessions"
      ],
      "Resource": "*"
    },
    {
      "Sid": "InvokeHviGateway",
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeGateway",
      "Resource": "arn:aws:bedrock-agentcore:${DATA_REGION}:${AWS_ACCOUNT_ID}:gateway/hvi-spike-gw-*"
    },
    {
      "Sid": "TelephonyControl",
      "Effect": "Allow",
      "Action": [
        "chime:CreateSipMediaApplicationCall","chime:UpdateSipMediaApplicationCall",
        "chime:GetSipMediaApplication","chime:ListSipMediaApplications",
        "chime:ListVoiceConnectors","chime:GetVoiceConnector"
      ],
      "Resource": "*"
    },
    {
      "Sid": "OriginateThroughAsterisk",
      "Effect": "Allow",
      "Action": ["ssm:SendCommand"],
      "Resource": [
        "arn:aws:ssm:${DATA_REGION}::document/AWS-RunShellScript",
        "arn:aws:ec2:${DATA_REGION}:${AWS_ACCOUNT_ID}:instance/*"
      ]
    },
    {
      "Sid": "OriginateReadResult",
      "Effect": "Allow",
      "Action": ["ssm:GetCommandInvocation","ec2:DescribeInstances"],
      "Resource": "*"
    },
    {
      "Sid": "ConfirmationEmail",
      "Effect": "Allow",
      "Action": ["ses:SendEmail","ses:SendRawEmail"],
      "Resource": "*"
    },
    {
      "Sid": "SalesforceCredentials",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:*:${ACC}:secret:hvi/salesforce*"
    },
    {
      "Sid": "TranscriptSearch",
      "Effect": "Allow",
      "Action": ["es:ESHttpPost","es:ESHttpPut","es:ESHttpGet","es:ESHttpHead"],
      "Resource": "arn:aws:es:*:${ACC}:domain/${PREFIX}-*/*"
    },
    {
      "Sid": "KnowledgeGraph",
      "Effect": "Allow",
      "Action": ["neptune-db:*"],
      "Resource": "arn:aws:neptune-db:*:${ACC}:*/*"
    },
    {
      "Sid": "AudioAndArchiveBuckets",
      "Effect": "Allow",
      "Action": ["s3:GetObject","s3:PutObject","s3:ListBucket","s3:DeleteObject"],
      "Resource": [
        "arn:aws:s3:::${PREFIX}-audio-${ACC}-*",
        "arn:aws:s3:::${PREFIX}-audio-${ACC}-*/*",
        "arn:aws:s3:::${PREFIX}-archive-${ACC}-*",
        "arn:aws:s3:::${PREFIX}-archive-${ACC}-*/*"
      ]
    },
    {
      "Sid": "InvokeSiblingLambdasCrossRegion",
      "Effect": "Allow",
      "Action": ["lambda:InvokeFunction"],
      "Resource": "arn:aws:lambda:*:${ACC}:function:${PREFIX}-*"
    },
    {
      "Sid": "CallbackScheduling",
      "Effect": "Allow",
      "Action": ["scheduler:CreateSchedule","scheduler:DeleteSchedule","scheduler:GetSchedule","iam:PassRole"],
      "Resource": "*"
    }
  ]
}
JSON

aws iam put-role-policy --role-name "$LAMBDA_ROLE" \
  --policy-name "${PREFIX}-lambda-inline" \
  --policy-document file:///tmp/hvi-lambda-inline.json
hvi_log "policy attached to $LAMBDA_ROLE"

# ------------------------------------------------------------- AgentCore role
AC_ROLE="${PREFIX}-agentcore-role"

# The SourceArn condition is region-scoped. It is written for DATA_REGION and
# applied on every run, not just at creation, because a role created against one
# region will be rejected by AgentCore in another with:
#   "Role validation failed ... trust policy allows assumption by this service"
AC_TRUST=$(jq -nc \
  --arg acc "$ACC" \
  --arg region "$DATA_REGION" \
  '{
     Version: "2012-10-17",
     Statement: [{
       Effect: "Allow",
       Principal: { Service: "bedrock-agentcore.amazonaws.com" },
       Action: "sts:AssumeRole",
       Condition: {
         StringEquals: { "aws:SourceAccount": $acc },
         ArnLike: { "aws:SourceArn": ("arn:aws:bedrock-agentcore:" + $region + ":" + $acc + ":*") }
       }
     }]
   }')

if role_exists "$AC_ROLE"; then
  hvi_warn "role $AC_ROLE exists - refreshing trust policy for $DATA_REGION"
  aws iam update-assume-role-policy --role-name "$AC_ROLE" \
    --policy-document "$AC_TRUST"
else
  hvi_log "creating role $AC_ROLE"
  aws iam create-role --role-name "$AC_ROLE" \
    --description "Execution role for the hvi Bedrock AgentCore runtime" \
    --assume-role-policy-document "$AC_TRUST" \
    --tags Key=Project,Value=health-voice-intake >/dev/null
  hvi_record "iam.$AC_ROLE" "created"
fi

# Note on the ToolLambdas statement below: Neptune has no public endpoint, so the
# graph tools reach it through the in-VPC hvi-graph function, and the CRM
# writeback and confirmation email are Lambdas too. Without lambda:InvokeFunction
# the agent classifies nothing and the call dies mid-turn with AccessDenied.
cat > /tmp/hvi-agentcore-inline.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PullAgentImage",
      "Effect": "Allow",
      "Action": ["ecr:GetAuthorizationToken","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability"],
      "Resource": "*"
    },
    {
      "Sid": "Logs",
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams"],
      "Resource": "arn:aws:logs:*:${ACC}:log-group:/aws/bedrock-agentcore/*"
    },
    {
      "Sid": "Observability",
      "Effect": "Allow",
      "Action": ["xray:PutTraceSegments","xray:PutTelemetryRecords","cloudwatch:PutMetricData"],
      "Resource": "*"
    },
    {
      "Sid": "ModelInvoke",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream","bedrock:Converse","bedrock:ConverseStream"],
      "Resource": "*"
    },
    {
      "Sid": "AgentCoreMemory",
      "Effect": "Allow",
      "Action": ["bedrock-agentcore:CreateEvent","bedrock-agentcore:ListEvents","bedrock-agentcore:RetrieveMemoryRecords","bedrock-agentcore:GetMemory"],
      "Resource": "*"
    },
    {
      "Sid": "ToolDataAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:Query","dynamodb:BatchWriteItem"
      ],
      "Resource": [
        "arn:aws:dynamodb:*:${ACC}:table/${PREFIX}-*",
        "arn:aws:dynamodb:*:${ACC}:table/${PREFIX}-*/index/*"
      ]
    },
    {
      "Sid": "ToolCallArchive",
      "Effect": "Allow",
      "Action": ["s3:PutObject","s3:GetObject","s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::${PREFIX}-archive-${ACC}-*",
        "arn:aws:s3:::${PREFIX}-archive-${ACC}-*/*"
      ]
    },
    {
      "Sid": "ToolSes",
      "Effect": "Allow",
      "Action": ["ses:SendEmail"],
      "Resource": "*"
    },
    {
      "Sid": "ToolSearch",
      "Effect": "Allow",
      "Action": ["es:ESHttpPost","es:ESHttpPut","es:ESHttpGet"],
      "Resource": "arn:aws:es:*:${ACC}:domain/${PREFIX}-*/*"
    },
    {
      "Sid": "ToolGraph",
      "Effect": "Allow",
      "Action": ["neptune-db:*"],
      "Resource": "arn:aws:neptune-db:*:${ACC}:*/*"
    },
    {
      "Sid": "ToolWorkloadIdentity",
      "Effect": "Allow",
      "Action": ["bedrock-agentcore:GetWorkloadAccessToken","bedrock-agentcore:GetWorkloadAccessTokenForJWT"],
      "Resource": "*"
    },
    {
      "Sid": "ToolLambdas",
      "Effect": "Allow",
      "Action": ["lambda:InvokeFunction"],
      "Resource": "arn:aws:lambda:*:${ACC}:function:${PREFIX}-*"
    }
  ]
}
JSON

aws iam put-role-policy --role-name "$AC_ROLE" \
  --policy-name "${PREFIX}-agentcore-inline" \
  --policy-document file:///tmp/hvi-agentcore-inline.json
hvi_log "policy attached to $AC_ROLE"

rm -f /tmp/hvi-lambda-inline.json /tmp/hvi-agentcore-inline.json

echo
hvi_log "IAM roles ready:"
aws iam get-role --role-name "$LAMBDA_ROLE" --query 'Role.Arn' --output text
aws iam get-role --role-name "$AC_ROLE"     --query 'Role.Arn' --output text
