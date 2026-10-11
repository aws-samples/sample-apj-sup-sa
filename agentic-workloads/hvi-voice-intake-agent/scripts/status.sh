#!/usr/bin/env bash
# One-shot status report for every resource in the hvi stack.
set -uo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

line() { printf '%s\n' "------------------------------------------------------------"; }

printf '\n=== DynamoDB ===\n'
aws dynamodb list-tables --region "$AWS_REGION" \
  --query "TableNames[?starts_with(@,'${PREFIX}-')]" --output text 2>/dev/null | tr '\t' '\n' | sed 's/^/  ACTIVE  /'

printf '\n=== Form schema ===\n'
cnt=$(aws dynamodb query --table-name "$TBL_FORM_SCHEMA" --region "$AWS_REGION" \
  --key-condition-expression 'formId = :f' \
  --expression-attribute-values "{\":f\":{\"S\":\"${FORM_ID}\"}}" \
  --select COUNT --query 'Count' --output text 2>/dev/null)
printf '  %s items for formId=%s\n' "${cnt:-0}" "$FORM_ID"

printf '\n=== IAM roles ===\n'
for r in "${PREFIX}-lambda-role" "${PREFIX}-agentcore-role"; do
  arn=$(aws iam get-role --role-name "$r" --query 'Role.Arn' --output text 2>/dev/null)
  printf '  %-26s %s\n' "$r" "${arn:-MISSING}"
done

printf '\n=== Neptune ===\n'
aws neptune describe-db-clusters --db-cluster-identifier "${PREFIX}-health-kg" --region "$AWS_REGION" \
  --query 'DBClusters[0].{Status:Status,Endpoint:Endpoint,Port:Port}' --output table 2>/dev/null \
  || printf '  not created\n'
aws neptune describe-db-instances --db-instance-identifier "${PREFIX}-health-kg-1" --region "$AWS_REGION" \
  --query 'DBInstances[0].{Status:DBInstanceStatus,Class:DBInstanceClass}' --output table 2>/dev/null \
  || printf '  instance not created\n'

printf '\n=== OpenSearch ===\n'
aws opensearch describe-domain --domain-name "${PREFIX}-logs" --region "$AWS_REGION" \
  --query 'DomainStatus.{Processing:Processing,Endpoint:Endpoint,Version:EngineVersion}' --output table 2>/dev/null \
  || printf '  not created\n'

printf '\n=== Lambda functions ===\n'
aws lambda list-functions --region "$AWS_REGION" \
  --query "Functions[?starts_with(FunctionName,'${PREFIX}-')].{Name:FunctionName,Runtime:Runtime,Mem:MemorySize}" \
  --output table 2>/dev/null || printf '  none\n'

printf '\n=== AgentCore runtimes ===\n'
aws bedrock-agentcore-control list-agent-runtimes --region "$AWS_REGION" \
  --query 'agentRuntimes[].{Name:agentRuntimeName,Status:status,Id:agentRuntimeId}' --output table 2>/dev/null \
  || printf '  none\n'

printf '\n=== Telephony (Chime SDK Voice - NOT Amazon Connect) ===\n'
aws chime-sdk-voice list-sip-media-applications --region "$AWS_REGION" \
  --query 'SipMediaApplications[].{Name:Name,Id:SipMediaApplicationId,Endpoint:Endpoints[0].LambdaArn}' \
  --output table 2>/dev/null
aws chime-sdk-voice list-phone-numbers --region "$AWS_REGION" \
  --query 'PhoneNumbers[].{Number:E164PhoneNumber,Type:ProductType,Status:Status}' --output table 2>/dev/null

# Both read from config/env.sh rather than being re-spelled here. The audio name
# used to be built inline WITHOUT the region suffix, which is not the name any code
# or IAM policy uses, so this panel could only ever report "not created".
printf '\n=== S3 buckets ===\n'
aws s3 ls "s3://${CALL_ARCHIVE_BUCKET}" >/dev/null 2>&1 \
  && printf '  archive : s3://%s exists\n' "$CALL_ARCHIVE_BUCKET" \
  || printf '  archive : NOT created - run scripts/55-call-archive.sh\n'
aws s3 ls "s3://${AUDIO_BUCKET}" >/dev/null 2>&1 \
  && printf '  audio   : s3://%s exists\n' "$AUDIO_BUCKET" \
  || printf '  audio   : not created (expected - nothing records call audio today)\n'

printf '\n=== ECR repositories ===\n'
aws ecr describe-repositories --region "$AWS_REGION" \
  --query "repositories[?starts_with(repositoryName,'${PREFIX}')].{Repo:repositoryName,Uri:repositoryUri}" \
  --output table 2>/dev/null || printf '  none\n'

printf '\n'
