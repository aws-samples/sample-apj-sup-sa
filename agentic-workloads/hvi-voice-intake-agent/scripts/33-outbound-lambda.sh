#!/usr/bin/env bash
# Deploy the outbound dialler Lambda - the compliance gates plus call origination.
#
# WHY THIS IS A SEPARATE SCRIPT FROM 32
# 32 deploys the CRM and email functions, which are safe to run at any time. This
# one deploys a function that can ring a real mobile in India, so it is separate
# and it deliberately does NOT create a schedule. Nothing dials until something
# invokes it.
#
# WHAT A TYPICAL CALL-FLOW GUIDE CALLS THIS STEP
# The guide's flow reaches "Connect: Place Call - Lambda calls
# StartOutboundVoiceContact". Connect was ruled out, so this is the replacement:
# the same gates, then origination through our own Asterisk over SSM. See
# docs/SOLUTION.md 5.2 "How a call is actually placed".
#
# IAM
# The handler needs ssm:SendCommand, ssm:GetCommandInvocation and
# ec2:DescribeInstances, which scripts/15-iam.sh only grants from the commit that
# added OriginateThroughAsterisk. Re-run 15-iam.sh first if the role predates it -
# this script checks and refuses rather than deploying a function that cannot work.
#
# NOT in the VPC, like the CRM Lambdas. It talks to SSM, DynamoDB and EC2, all of
# which are reachable over IAM-signed public endpoints, so private subnets would
# only add a NAT dependency.
#
# Usage
#   bash scripts/33-outbound-lambda.sh
#   bash scripts/33-outbound-lambda.sh --dry-run-test   # deploy, then gates-only
set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

FN="${PREFIX}-outbound"
ROLE_NAME="${PREFIX}-lambda-role"
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"

# ---- required inputs, checked before anything is packaged -------------------
# OUTBOUND_CALLER_ID is read with os.environ[...] in the handler, so a missing
# value is an import-time crash rather than a bad call. Fail here instead.
: "${OUTBOUND_CALLER_ID:?set OUTBOUND_CALLER_ID to a DID on the Voice Connector}"
: "${ASTERISK_INSTANCE_ID:?run scripts/70-asterisk.sh first, or set ASTERISK_INSTANCE_ID}"

# ---- refuse to deploy a function the role cannot run -----------------------
# This is the failure 32-crm-lambdas.sh documents: handlers that were written,
# tested and never able to work in place. A green suite does not cross the
# deployment seam, and neither does a Lambda that lacks one action.
hvi_log "checking $ROLE_NAME can originate"
if ! aws iam get-role-policy --role-name "$ROLE_NAME" \
      --policy-name "${PREFIX}-lambda-inline" \
      --query 'PolicyDocument.Statement[].Action' --output json 2>/dev/null \
      | grep -q 'ssm:SendCommand'; then
  hvi_warn "$ROLE_NAME has no ssm:SendCommand, so the dialler could pass every"
  hvi_warn "gate and then fail to place the call. Run: bash scripts/15-iam.sh"
  exit 1
fi
hvi_log "  ssm:SendCommand present"

# ---- verify the Asterisk host is actually reachable by SSM -----------------
# The dialler's only route to the trunk. If the SSM agent is not online the
# function deploys cleanly and every call fails at the last step.
PING=$(aws ssm describe-instance-information --region "$DATA_REGION" \
  --filters "Key=InstanceIds,Values=${ASTERISK_INSTANCE_ID}" \
  --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "None")
if [[ "$PING" != "Online" ]]; then
  hvi_warn "SSM reports Asterisk instance ${ASTERISK_INSTANCE_ID} as '${PING}'."
  hvi_warn "Deploying anyway, but origination will fail until it is Online."
else
  hvi_log "  asterisk ${ASTERISK_INSTANCE_ID} is Online in SSM"
fi

# ---- package ---------------------------------------------------------------
# Flat, like 32: files land at the zip root. The handler adds ./common to
# sys.path and falls back to the root, so hvi_state resolves either way.
BUILD="build/outbound"
rm -rf "$BUILD" && mkdir -p "$BUILD"
cp lambdas/outbound/handler.py common/hvi_state.py "$BUILD/"
find "$BUILD" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
( cd "$BUILD" && zip -qr ../outbound.zip . )
hvi_log "packaged $(du -h build/outbound.zip | cut -f1)"

ENV_VARS="Variables={DATA_REGION=${DATA_REGION}"
ENV_VARS+=",TELEPHONY_REGION=${TELEPHONY_REGION}"
ENV_VARS+=",PREFIX=${PREFIX}"
ENV_VARS+=",OUTBOUND_CALLER_ID=${OUTBOUND_CALLER_ID}"
ENV_VARS+=",ASTERISK_INSTANCE_ID=${ASTERISK_INSTANCE_ID}"
ENV_VARS+=",FORM_ID=${FORM_ID:-arogya-sanjeevani-v1}"
ENV_VARS+=",TRAI_CALL_START_HOUR=${TRAI_CALL_START_HOUR:-9}"
ENV_VARS+=",TRAI_CALL_END_HOUR=${TRAI_CALL_END_HOUR:-21}"
ENV_VARS+=",TRAI_MAX_ATTEMPTS_PER_DAY=${TRAI_MAX_ATTEMPTS_PER_DAY:-3}"
ENV_VARS+=",SLA_HOURS_FROM_PURCHASE=${SLA_HOURS_FROM_PURCHASE:-48}"
# Defaults to false ON PURPOSE. Setting it true lets a demo call go out outside
# 09:00-21:00 IST, and the override is written to hvi-gate-audit so it can never
# read as though the gate passed.
ENV_VARS+=",ALLOW_WINDOW_OVERRIDE=${ALLOW_WINDOW_OVERRIDE:-false}}"

# ---- deploy ----------------------------------------------------------------
if aws lambda get-function --function-name "$FN" --region "$DATA_REGION" >/dev/null 2>&1; then
  hvi_log "updating $FN"
  aws lambda update-function-code --function-name "$FN" \
    --zip-file fileb://build/outbound.zip --region "$DATA_REGION" \
    --query 'LastModified' --output text >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$DATA_REGION"
  aws lambda update-function-configuration --function-name "$FN" \
    --timeout 120 --memory-size 512 --environment "$ENV_VARS" \
    --region "$DATA_REGION" --query 'LastModified' --output text >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$DATA_REGION"
else
  hvi_log "creating $FN"
  aws lambda create-function --function-name "$FN" \
    --runtime python3.12 --architectures x86_64 --role "$ROLE_ARN" \
    --handler handler.lambda_handler --zip-file fileb://build/outbound.zip \
    --timeout 120 --memory-size 512 --environment "$ENV_VARS" \
    --tags "Project=health-voice-intake" \
    --region "$DATA_REGION" --query 'FunctionArn' --output text
  aws lambda wait function-active-v2 --function-name "$FN" --region "$DATA_REGION"
  hvi_record "lambda.$FN" "created"
fi

hvi_log "deployed $FN"
hvi_log ""
hvi_log "NOTHING DIALS ON ITS OWN. There is no EventBridge schedule attached."
hvi_log "Gates only, no call placed, no session written:"
hvi_log "  aws lambda invoke --function-name $FN --region $DATA_REGION \\"
hvi_log "    --cli-binary-format raw-in-base64-out \\"
hvi_log "    --payload '{\"phone\":\"+91XXXXXXXXXX\",\"dryRun\":true}' /tmp/out.json"
hvi_log "Remove --payload's dryRun to place a REAL call to that number."
