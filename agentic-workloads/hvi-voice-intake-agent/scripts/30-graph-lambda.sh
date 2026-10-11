#!/usr/bin/env bash
# Package and deploy the in-VPC Neptune graph Lambda, then seed the graph.
#
# This function has to run inside the VPC because Neptune has no public
# endpoint. It is also the backing implementation for three AgentCore tools:
# get_followups, resolve_condition and check_interactions.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

: "${NEPTUNE_ENDPOINT:?run scripts/25-neptune.sh and re-source config}"
: "${SUBNET_A:?run scripts/20-vpc.sh first}"
: "${SG_LAMBDA:?run scripts/20-vpc.sh first}"

FN="${PREFIX}-graph"
BUILD="build/graph"
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${PREFIX}-lambda-role"

hvi_log "packaging $FN"
rm -rf "$BUILD" && mkdir -p "$BUILD"
cp lambdas/graph/handler.py "$BUILD/"
cp seed/health-kg.json "$BUILD/"

# Only requests is needed; SigV4 signing uses botocore, which the Lambda runtime
# already provides. requests is pure Python so a plain --target install is
# portable to the runtime.
./.venv/bin/pip install --quiet --target "$BUILD" requests 2>&1 | tail -2 || true

# Trim bytes that never execute.
find "$BUILD" -type d -name '__pycache__'   -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -type d -name '*.dist-info'   -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -type d -name 'tests'         -prune -exec rm -rf {} + 2>/dev/null || true

( cd "$BUILD" && zip -qr ../graph.zip . )
hvi_log "package size: $(du -h build/graph.zip | cut -f1)"

# Record which KG revision is now inside the deployed package.
#
# The graph is seeded from the copy of health-kg.json bundled in this zip, not
# from the working tree. Editing seed/health-kg.json and running 31-graph-seed.sh
# without repackaging therefore re-seeds the OLD data and still prints "seed and
# verification complete" - which is exactly what happened when 51 new Devanagari
# synonyms silently failed to reach Neptune. 31-graph-seed.sh compares against
# this file and refuses to run stale.
shasum -a 256 seed/health-kg.json | awk '{print $1}' > build/.kg-deployed-sha256
hvi_log "recorded KG revision $(cut -c1-12 build/.kg-deployed-sha256)"

ENV_VARS="Variables={NEPTUNE_ENDPOINT=${NEPTUNE_ENDPOINT},NEPTUNE_PORT=${NEPTUNE_PORT:-8182},NEPTUNE_REGION=${DATA_REGION}}"

if aws lambda get-function --function-name "$FN" --region "$DATA_REGION" >/dev/null 2>&1; then
  hvi_log "updating existing function $FN"
  aws lambda update-function-code \
    --function-name "$FN" --zip-file fileb://build/graph.zip \
    --region "$DATA_REGION" --query 'LastModified' --output text >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$DATA_REGION"
  aws lambda update-function-configuration \
    --function-name "$FN" --timeout 300 --memory-size 512 \
    --environment "$ENV_VARS" \
    --region "$DATA_REGION" --query 'LastModified' --output text >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$DATA_REGION"
else
  hvi_log "creating function $FN in VPC $VPC_ID"
  aws lambda create-function \
    --function-name "$FN" \
    --runtime python3.12 \
    --architectures x86_64 \
    --role "$ROLE_ARN" \
    --handler handler.lambda_handler \
    --zip-file fileb://build/graph.zip \
    --timeout 300 --memory-size 512 \
    --environment "$ENV_VARS" \
    --vpc-config "SubnetIds=${SUBNET_A},${SUBNET_B},SecurityGroupIds=${SG_LAMBDA}" \
    --tags "Project=health-voice-intake" \
    --region "$DATA_REGION" \
    --query 'FunctionArn' --output text
  hvi_record "lambda.$FN" "created"
  hvi_log "waiting for the function to become active"
  aws lambda wait function-active-v2 --function-name "$FN" --region "$DATA_REGION"
fi

hvi_log "connectivity check"
aws lambda invoke --function-name "$FN" --region "$DATA_REGION" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"health"}' /tmp/hvi-graph-health.json >/dev/null
cat /tmp/hvi-graph-health.json; echo

hvi_log "done. Seed the graph with: bash scripts/31-graph-seed.sh"
