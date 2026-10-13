#!/usr/bin/env bash
# Deploy the async-batch path: a nightly EventBridge Scheduler -> Lambda that
# invokes the deployed AgentCore Runtime. Run AFTER the agent is deployed
# (`agentcore deploy`).
#
# Requires:
#   - workshop/tools.env  (DATA_BUCKET, AUDIT_BUCKET)  -> `source tools.env`
#   - AGENT_RUNTIME_ARN    exported, or pass as first arg
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-biodiversity-anomaly}"
STACK_NAME="${STACK_NAME:-${PROJECT_NAME}-schedule}"
REGION="${AWS_REGION:-us-east-1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

AGENT_RUNTIME_ARN="${1:-${AGENT_RUNTIME_ARN:-}}"
SCHEDULE_EXPRESSION="${SCHEDULE_EXPRESSION:-cron(0 18 * * ? *)}"

if [[ -z "${AGENT_RUNTIME_ARN}" ]]; then
  echo "ERROR: set AGENT_RUNTIME_ARN (from 'agentcore deploy') or pass it as arg 1." >&2
  echo "  find it with: agentcore status" >&2
  exit 1
fi
: "${DATA_BUCKET:?source workshop/tools.env first}"
: "${AUDIT_BUCKET:?source workshop/tools.env first}"

echo "==> Deploying schedule stack: ${STACK_NAME}"
aws cloudformation deploy \
  --stack-name "${STACK_NAME}" \
  --template-file "${HERE}/template.yaml" \
  --parameter-overrides \
      "ProjectName=${PROJECT_NAME}" \
      "AgentRuntimeArn=${AGENT_RUNTIME_ARN}" \
      "DataBucket=${DATA_BUCKET}" \
      "AuditBucket=${AUDIT_BUCKET}" \
      "ScheduleExpression=${SCHEDULE_EXPRESSION}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${REGION}"

FN="$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='InvokerFunctionName'].OutputValue" --output text)"

echo "==> Updating invoker Lambda code (${FN})"
TMP="$(mktemp -d)"
cp "${HERE}/lambda/index.py" "${TMP}/index.py"
( cd "${TMP}" && zip -q invoker.zip index.py )
aws lambda update-function-code --function-name "${FN}" \
  --zip-file "fileb://${TMP}/invoker.zip" --region "${REGION}" >/dev/null
aws lambda wait function-updated --function-name "${FN}" --region "${REGION}"
rm -rf "${TMP}"

echo ""
echo "==> Done. Nightly scan scheduled: ${SCHEDULE_EXPRESSION} (UTC)"
echo "    Test it now without waiting for the cron:"
echo "      aws lambda invoke --function-name ${FN} --region ${REGION} /tmp/batch-out.json && cat /tmp/batch-out.json"
echo "    Run summaries land in: s3://${AUDIT_BUCKET}/batch-runs/"
