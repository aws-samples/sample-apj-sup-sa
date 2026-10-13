#!/usr/bin/env bash
# Deploy the remote tool backend: S3 buckets + Lambda, then upload datasets and
# the real Lambda code. Idempotent - safe to re-run.
#
# NOTE: In the workshop path this is NOT required. infra/template.yaml seeds the
# datasets and injects both Lambda handlers from blobs embedded in the template
# itself, so a stack create is already self-sufficient. This script is a dev /
# own-account fallback: it re-seeds from the LOCAL files on disk, which is what
# you want while you are still editing them.
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-biodiversity-anomaly}"
STACK_NAME="${STACK_NAME:-${PROJECT_NAME}-tools}"
REGION="${AWS_REGION:-us-east-1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "==> Account: ${ACCOUNT_ID}"
echo "==> Region : ${REGION}"

# template.yaml carries the datasets, both Lambda handlers and the whole agent
# workspace as gzip+base64 blobs, which puts it well past CloudFormation's
# 51,200-byte limit for an inline --template-body:
#   Member must have length less than or equal to 51200
# So it has to be staged in S3 (the 1 MB limit applies there, and the template is
# at ~9% of it). Workshop Studio stages templates on its own, which is why this
# only matters for a manual deploy.
STAGE_BUCKET="${STAGE_BUCKET:-${PROJECT_NAME}-cfn-stage-${ACCOUNT_ID}-${REGION}}"
if ! aws s3api head-bucket --bucket "${STAGE_BUCKET}" --region "${REGION}" 2>/dev/null; then
  echo "==> Creating template staging bucket: ${STAGE_BUCKET}"
  echo "    Not part of the stack - delete it yourself when you are done, or set"
  echo "    STAGE_BUCKET to one you already have."
  aws s3 mb "s3://${STAGE_BUCKET}" --region "${REGION}"
fi

echo "==> Deploying CloudFormation stack: ${STACK_NAME}"
aws cloudformation deploy \
  --stack-name "${STACK_NAME}" \
  --template-file "${HERE}/template.yaml" \
  --s3-bucket "${STAGE_BUCKET}" \
  --s3-prefix "${STACK_NAME}" \
  --parameter-overrides "ProjectName=${PROJECT_NAME}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${REGION}"

get_output () {
  aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

DATA_BUCKET="$(get_output DataBucketName)"
AUDIT_BUCKET="$(get_output AuditBucketName)"
TOOLS_ARN="$(get_output ToolsFunctionArn)"
# The stack exports the ARN, not the name. Take the name off the end of it -
# reading a ToolsFunctionName output left TOOLS_FN empty and every
# `aws lambda ... --function-name ""` below failed.
TOOLS_FN="${TOOLS_ARN##*:}"

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
