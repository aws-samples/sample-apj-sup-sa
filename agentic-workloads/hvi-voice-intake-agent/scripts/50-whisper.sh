#!/usr/bin/env bash
# Deploy Whisper large-v3 as a SageMaker real-time endpoint in ap-south-1.
#
# Why large-v3 and not a small CPU model: the requirement is en-IN, hi-IN AND
# Hinglish code-mixing. The tiny and base checkpoints degrade badly on Hindi and
# fall apart on code-switching, so a GPU is not an indulgence here.
#
# Why the HuggingFace DLC and not a custom image: transformers already ships a
# working Whisper ASR pipeline, so there is nothing to hand-write. The model is
# pulled from the Hub at container start, which means no Docker build, no ECR
# push, and no 3GB layer to maintain.
#
# Why not in the VPC: a SageMaker endpoint is reached through the SageMaker
# runtime API with SigV4, not over the network, so keeping it out of the VPC
# avoids NAT egress for the model download while inference still stays inside
# ap-south-1. Data residency is a function of the region, not of the subnet.
#
# Cost: ml.g5.xlarge is roughly $1.4/hr on-demand. Delete the endpoint when the
# demo is done - scripts/59-whisper-down.sh.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
ACC="$AWS_ACCOUNT_ID"
MODEL_NAME="${PREFIX}-whisper-large-v3"
ENDPOINT_CONFIG="${PREFIX}-whisper-config"
ENDPOINT="${PREFIX}-whisper"
ROLE_NAME="${PREFIX}-sagemaker-role"

# Instance selection is a PRIORITY LADDER, not a single type.
#
# The first attempt at this endpoint died with InsufficientInstanceCapacity on
# ml.g5.xlarge - ap-south-1 simply had no A10G to give us that morning. Quota
# was never the problem (we hold 4.0 on g5.xlarge and 4.0 on g4dn.xlarge), so
# asking again for the same single type would just fail again.
#
# SageMaker InstancePools takes several types with an explicit Priority and
# falls back down the list until one has capacity. whisper-large-v3 is ~6GB in
# fp16, so the 16GB T4 in g4dn is as capable as the 24GB A10G in g5 for this
# workload - it is only slower. T4 is Turing: fp16 yes, bf16 no, which the DLC
# handles. So we lead with the cheapest adequate card and let AWS find metal.
#
# SageMaker caps InstancePools at 5 entries with Priority 1..5, so this ladder
# is deliberately exactly 5 long.
#
# Override with STT_INSTANCE_POOLS="ml.g5.xlarge,ml.g4dn.xlarge".
INSTANCE_POOLS="${STT_INSTANCE_POOLS:-ml.g4dn.xlarge,ml.g5.xlarge,ml.g6.xlarge,ml.g4dn.2xlarge,ml.g5.2xlarge}"

# HuggingFace PyTorch inference DLC, ap-south-1. The DLC account id is the same
# in every commercial region.
DLC="763104351884.dkr.ecr.${R}.amazonaws.com/huggingface-pytorch-inference:2.6.0-transformers4.51.3-gpu-py312-cu124-ubuntu22.04-v2.3"

# ---- Execution role ---------------------------------------------------------
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  hvi_warn "role $ROLE_NAME exists"
else
  hvi_log "creating role $ROLE_NAME"
  aws iam create-role --role-name "$ROLE_NAME" \
    --description "Execution role for the hvi Whisper SageMaker endpoint" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"sagemaker.amazonaws.com"},"Action":"sts:AssumeRole"}]
    }' \
    --tags Key=Project,Value=health-voice-intake >/dev/null
fi

cat > /tmp/hvi-sm-inline.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PullDlc",
      "Effect": "Allow",
      "Action": ["ecr:GetAuthorizationToken","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability"],
      "Resource": "*"
    },
    {
      "Sid": "Logs",
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams","cloudwatch:PutMetricData"],
      "Resource": "*"
    },
    {
      "Sid": "AudioBuckets",
      "Effect": "Allow",
      "Action": ["s3:GetObject","s3:PutObject","s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::${PREFIX}-audio-${ACC}-*",
        "arn:aws:s3:::${PREFIX}-audio-${ACC}-*/*"
      ]
    }
  ]
}
JSON

aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "${PREFIX}-sagemaker-inline" \
  --policy-document file:///tmp/hvi-sm-inline.json
rm -f /tmp/hvi-sm-inline.json
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
hvi_log "role: $ROLE_ARN"

# ---- Model ------------------------------------------------------------------
if aws sagemaker describe-model --model-name "$MODEL_NAME" --region "$R" >/dev/null 2>&1; then
  hvi_warn "model $MODEL_NAME exists - deleting to apply current settings"
  aws sagemaker delete-model --model-name "$MODEL_NAME" --region "$R"
fi

hvi_log "creating model $MODEL_NAME (whisper large-v3 from the HF Hub)"
aws sagemaker create-model \
  --model-name "$MODEL_NAME" \
  --execution-role-arn "$ROLE_ARN" \
  --region "$R" \
  --tags Key=Project,Value=health-voice-intake \
  --primary-container "$(jq -nc \
      --arg img "$DLC" \
      '{
         Image: $img,
         Environment: {
           HF_MODEL_ID: "openai/whisper-large-v3",
           HF_TASK: "automatic-speech-recognition",
           # HF_HUB_ENABLE_HF_TRANSFER is deliberately NOT set. Enabling it for
           # download speed is what killed the second deploy: the repo is on
           # HuggingFace Xet storage, and hf_transfer against the Xet CDN
           # returned 403 Forbidden ("no permits available") on the weight
           # shards, so the container never came up and the endpoint failed the
           # ping health check. Plain HTTP download is slower but works, which
           # is why the startup timeouts below are generous.
           HF_HUB_ENABLE_HF_TRANSFER: "0",
           SAGEMAKER_CONTAINER_LOG_LEVEL: "20",
           SAGEMAKER_REGION: "ap-south-1",
           MMS_DEFAULT_RESPONSE_TIMEOUT: "300"
         }
       }')" \
  --query 'ModelArn' --output text

# ---- Endpoint config --------------------------------------------------------
if aws sagemaker describe-endpoint-config --endpoint-config-name "$ENDPOINT_CONFIG" \
     --region "$R" >/dev/null 2>&1; then
  hvi_warn "endpoint config exists - deleting"
  aws sagemaker delete-endpoint-config --endpoint-config-name "$ENDPOINT_CONFIG" --region "$R"
fi

# The startup timeouts are raised to 900s because large-v3 is roughly 3GB from
# the Hub and the default 5 minute health-check window fails on a cold start.
#
# VariantInstanceProvisionTimeoutInSeconds bounds how long SageMaker waits on
# one pool before demoting to the next priority. Without it a capacity-starved
# first choice can stall the whole rollout for half an hour, which is exactly
# what happened on the first attempt.
hvi_log "creating endpoint config with instance pool ladder: $INSTANCE_POOLS"
aws sagemaker create-endpoint-config \
  --endpoint-config-name "$ENDPOINT_CONFIG" \
  --region "$R" \
  --tags Key=Project,Value=health-voice-intake \
  --production-variants "$(jq -nc --arg m "$MODEL_NAME" --arg pools "$INSTANCE_POOLS" \
      '[{
          VariantName: "AllTraffic",
          ModelName: $m,
          InitialInstanceCount: 1,
          InitialVariantWeight: 1.0,
          ContainerStartupHealthCheckTimeoutInSeconds: 900,
          ModelDataDownloadTimeoutInSeconds: 900,
          VariantInstanceProvisionTimeoutInSeconds: 600,
          InstancePools: (
            $pools | split(",")
                   | to_entries
                   | map({InstanceType: .value, Priority: (.key + 1)})
          )
        }]')" \
  --query 'EndpointConfigArn' --output text

# ---- Endpoint ---------------------------------------------------------------
EXISTING_STATUS=$(aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" \
  --region "$R" --query 'EndpointStatus' --output text 2>/dev/null || echo "NONE")

# A Failed endpoint cannot be updated into health - update-endpoint on it is
# rejected. It has to be torn down and rebuilt from the new config.
if [ "$EXISTING_STATUS" = "Failed" ]; then
  hvi_warn "endpoint $ENDPOINT is in Failed state - deleting before rebuild"
  aws sagemaker delete-endpoint --endpoint-name "$ENDPOINT" --region "$R"
  while aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" \
          --region "$R" >/dev/null 2>&1; do
    printf '.'
    sleep 5
  done
  printf '\n'
  EXISTING_STATUS="NONE"
fi

if [ "$EXISTING_STATUS" != "NONE" ]; then
  hvi_log "updating existing endpoint $ENDPOINT (status $EXISTING_STATUS)"
  aws sagemaker update-endpoint --endpoint-name "$ENDPOINT" \
    --endpoint-config-name "$ENDPOINT_CONFIG" --region "$R" \
    --query 'EndpointArn' --output text
else
  hvi_log "creating endpoint $ENDPOINT"
  aws sagemaker create-endpoint --endpoint-name "$ENDPOINT" \
    --endpoint-config-name "$ENDPOINT_CONFIG" --region "$R" \
    --tags Key=Project,Value=health-voice-intake \
    --query 'EndpointArn' --output text
  hvi_record "sagemaker.endpoint" "$ENDPOINT"
fi

hvi_log "endpoint is provisioning; the model download makes this 8-15 minutes"
hvi_log "watch with: bash scripts/51-whisper-status.sh"

# Idempotent: this script is re-run on every capacity retry, so appending
# blindly would stack duplicate exports in env.local.sh.
touch config/env.local.sh
if grep -q '^export STT_ENDPOINT=' config/env.local.sh 2>/dev/null; then
  hvi_warn "STT_ENDPOINT already set in config/env.local.sh"
else
  printf 'export STT_ENDPOINT="%s"\n' "$ENDPOINT" >> config/env.local.sh
  hvi_log "appended STT_ENDPOINT to config/env.local.sh"
fi
