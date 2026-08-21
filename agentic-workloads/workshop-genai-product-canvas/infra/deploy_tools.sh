#!/usr/bin/env bash
# Deploy the remote tool backend: S3 buckets + Lambda, then upload datasets and
# the real Lambda code. Idempotent - safe to re-run.
#
# NOTE: In the workshop path this is NOT required. infra/template.yaml now seeds
# the datasets and injects the tools Lambda code via its provisioner custom
# resource when the stack is created. This script is a dev / own-account fallback
# for seeding from LOCAL files (e.g. before the repo is pushed to GitHub, so the
# stack's GitHub download would have nothing to pull).
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-biodiversity-anomaly}"
STACK_NAME="${STACK_NAME:-${PROJECT_NAME}-tools}"
REGION="${AWS_REGION:-us-east-1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Account: $(aws sts get-caller-identity --query Account --output text)"
echo "==> Region : ${REGION}"

echo "==> Deploying CloudFormation stack: ${STACK_NAME}"
aws cloudformation deploy \
  --stack-name "${STACK_NAME}" \
  --template-file "${HERE}/template.yaml" \
  --parameter-overrides "ProjectName=${PROJECT_NAME}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${REGION}"

get_output () {
  aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

DATA_BUCKET="$(get_output DataBucketName)"
AUDIT_BUCKET="$(get_output AuditBucketName)"
TOOLS_FN="$(get_output ToolsFunctionName)"
TOOLS_ARN="$(get_output ToolsFunctionArn)"

echo "==> Uploading datasets to s3://${DATA_BUCKET}/data/"
aws s3 cp "${HERE}/../agent/data/" "s3://${DATA_BUCKET}/data/" --recursive \
  --exclude "*" --include "*.json" --region "${REGION}"

echo "==> Packaging and updating Lambda code (${TOOLS_FN})"
TMP="$(mktemp -d)"
cp "${HERE}/lambda/tools/index.py" "${TMP}/index.py"
( cd "${TMP}" && zip -q tools.zip index.py )
aws lambda update-function-code \
  --function-name "${TOOLS_FN}" \
  --zip-file "fileb://${TMP}/tools.zip" \
  --region "${REGION}" >/dev/null
aws lambda wait function-updated --function-name "${TOOLS_FN}" --region "${REGION}"
rm -rf "${TMP}"

echo "==> Smoke test: query_detections"
aws lambda invoke \
  --function-name "${TOOLS_FN}" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"species":"Malayan Tapir","start_month":"2026-01","end_month":"2026-07","station_id":"STN-03"}' \
  --region "${REGION}" \
  /tmp/tool-out.json >/dev/null 2>&1 || true
echo "    (note: direct invoke returns 'Unknown tool' because the tool name is"
echo "     supplied by Gateway context - that is expected. Full test via Gateway.)"

cat > "${HERE}/../tools.env" <<EOF
export PROJECT_NAME=${PROJECT_NAME}
export AWS_REGION=${REGION}
export DATA_BUCKET=${DATA_BUCKET}
export AUDIT_BUCKET=${AUDIT_BUCKET}
export TOOLS_FUNCTION_ARN=${TOOLS_ARN}
EOF

echo ""
echo "==> Done. Wrote workshop/tools.env"
echo "    Data bucket : ${DATA_BUCKET}"
echo "    Tools Lambda: ${TOOLS_ARN}"
echo "    Next: python infra/create_gateway.py   (expose these tools as remote MCP)"
