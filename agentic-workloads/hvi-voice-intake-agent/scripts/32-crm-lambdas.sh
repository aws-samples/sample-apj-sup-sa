#!/usr/bin/env bash
# Deploy the Salesforce sync/writeback Lambdas and the SES confirmation sender.
#
# WHY THIS SCRIPT EXISTS AT ALL, having been missed once
#
# Both handlers were written, tested and reported working - and neither was ever
# deployed. The tests import the handler in-process via importlib, which verifies
# the CONTRACT but never crosses the DEPLOYMENT seam, so 16 green suites sat on
# top of a completion path that could not work:
#
#   agent/tools.py submit_to_crm() invokes FunctionName="hvi-crm-writeback"
#   ... which did not exist, in either region.
#
# orchestrator._complete() wraps that invoke in try/except and logs, so a live
# call would have finished the questionnaire, written every answer to DynamoDB,
# and then failed to reach Salesforce WITHOUT failing the call. Scenario 1
# ("reflects in CRM") would have broken at the last step, quietly, with a green
# test suite.
#
# NOT in the VPC, deliberately. hvi-graph is in-VPC because Neptune has no public
# endpoint. These two need the opposite: Salesforce is on the public internet, so
# putting them in private subnets would make them depend on the NAT gateway for
# no benefit.
#
# Credentials come from Secrets Manager (hvi/salesforce). That needs
# secretsmanager:GetSecretValue on hvi-lambda-role, which was also missing and is
# added by scripts/15-iam.sh - re-run it before this if the role predates that.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

# Validate every required deployment input before packaging or touching AWS.
# Previously this check ran only after two Lambda updates had already occurred.
if [[ -z "${SES_FROM_EMAIL:-}" ]]; then
  hvi_warn "SES_FROM_EMAIL is unset; refusing to deploy a sender that cannot deliver"
  exit 1
fi

ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${PREFIX}-lambda-role"

package() {
  # $1 = short name, rest = files to place at the zip root
  local name="$1"; shift
  local build="build/${name}"
  rm -rf "$build" && mkdir -p "$build"
  cp "$@" "$build/"

  # requests is needed for the Salesforce REST calls. Pure Python, so a plain
  # --target install is portable to the Lambda runtime. botocore ships with the
  # runtime and provides SigV4, so it is deliberately not vendored.
  ./.venv/bin/pip install --quiet --target "$build" "requests==2.32.3" 2>&1 | tail -1

  find "$build" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find "$build" -type d -name '*.dist-info' -prune -exec rm -rf {} + 2>/dev/null || true
  find "$build" -type d -name 'tests'       -prune -exec rm -rf {} + 2>/dev/null || true

  ( cd "$build" && zip -qr "../${name}.zip" . )
}

deploy() {
  local fn="$1" zip="$2" env_vars="$3" timeout="${4:-120}" mem="${5:-512}"
  if aws lambda get-function --function-name "$fn" --region "$DATA_REGION" >/dev/null 2>&1; then
    hvi_log "updating $fn"
    aws lambda update-function-code --function-name "$fn" \
      --zip-file "fileb://${zip}" --region "$DATA_REGION" \
      --query 'LastModified' --output text >/dev/null
    aws lambda wait function-updated --function-name "$fn" --region "$DATA_REGION"
    aws lambda update-function-configuration --function-name "$fn" \
      --timeout "$timeout" --memory-size "$mem" --environment "$env_vars" \
      --region "$DATA_REGION" --query 'LastModified' --output text >/dev/null
    aws lambda wait function-updated --function-name "$fn" --region "$DATA_REGION"
  else
    hvi_log "creating $fn"
    aws lambda create-function --function-name "$fn" \
      --runtime python3.12 --architectures x86_64 --role "$ROLE_ARN" \
      --handler handler.lambda_handler --zip-file "fileb://${zip}" \
      --timeout "$timeout" --memory-size "$mem" --environment "$env_vars" \
      --tags "Project=health-voice-intake" \
      --region "$DATA_REGION" --query 'FunctionArn' --output text
    aws lambda wait function-active-v2 --function-name "$fn" --region "$DATA_REGION"
    hvi_record "lambda.$fn" "created"
  fi
}

# Shared Salesforce configuration. SF_CLIENT_ID / SF_CLIENT_SECRET are
# deliberately NOT passed: hvi_salesforce.py prefers them from the environment
# when present, which is a local-development convenience, and falls back to
# Secrets Manager. In a deployed function the secret is the only correct source,
# so leaving them unset makes that the only code path available.
# HVI_SF_VIA_GATEWAY selects the Salesforce transport and defaults to 0, the direct
# client. Set it to 1 to route through the AgentCore Gateway, where the OAuth token
# comes from the Identity vault instead of being negotiated in hvi_salesforce.py.
# Both transports are proven against this org; the direct one stays the default
# because this is the path a medical record takes to the insurer. Rolling back is
# `aws lambda update-function-configuration` on this one variable.
SF_ENV="SF_LOGIN_URL=${SF_LOGIN_URL},SF_SECRET_NAME=${SF_SECRET_NAME:-hvi/salesforce},SF_MOCK=${SF_MOCK:-false},SF_API_VERSION=${SF_API_VERSION:-67.0},DATA_REGION=${DATA_REGION},HVI_SF_VIA_GATEWAY=${HVI_SF_VIA_GATEWAY:-0},HVI_GATEWAY_NAME=${HVI_GATEWAY_NAME:-hvi-spike-gw}"

# hvi_salesforce_gateway.py rides along even though the default transport does not
# import it. salesforce_client() imports it lazily, so the bundle costs nothing until
# HVI_SF_VIA_GATEWAY=1, and shipping it means turning the flag on is an environment
# change rather than a redeploy - which is what makes rolling back one too.
hvi_log "packaging ${PREFIX}-crm-writeback"
package "crm_writeback" \
  lambdas/crm_writeback/handler.py \
  common/hvi_state.py \
  common/hvi_salesforce.py \
  common/hvi_salesforce_gateway.py
hvi_log "  $(du -h build/crm_writeback.zip | cut -f1)"
deploy "${PREFIX}-crm-writeback" "build/crm_writeback.zip" \
  "Variables={${SF_ENV}}" 120 512

hvi_log "packaging ${PREFIX}-crm-sync"
package "crm_sync" \
  lambdas/crm_sync/handler.py \
  common/hvi_state.py \
  common/hvi_salesforce.py \
  common/hvi_salesforce_gateway.py
hvi_log "  $(du -h build/crm_sync.zip | cut -f1)"
deploy "${PREFIX}-crm-sync" "build/crm_sync.zip" \
  "Variables={${SF_ENV},FORM_ID=${FORM_ID:-arogya-sanjeevani-v1},SLA_HOURS_FROM_PURCHASE=${SLA_HOURS_FROM_PURCHASE:-48},INSURER_NAME=${INSURER_NAME:-Meridian Health Cover}}" 300 512

CONFIRM_ENV="DATA_REGION=${DATA_REGION},SES_REGION=${DATA_REGION},SES_FROM_EMAIL=${SES_FROM_EMAIL}"
if [[ -n "${CORRECTION_REPLY_TO:-}" ]]; then
  CONFIRM_ENV="${CONFIRM_ENV},CORRECTION_REPLY_TO=${CORRECTION_REPLY_TO}"
fi
hvi_log "packaging ${PREFIX}-send-confirmation"
package "send_confirmation" \
  lambdas/send_confirmation/handler.py \
  common/hvi_state.py
hvi_log "  $(du -h build/send_confirmation.zip | cut -f1)"
deploy "${PREFIX}-send-confirmation" "build/send_confirmation.zip" \
  "Variables={${CONFIRM_ENV}}" 60 256

hvi_log "smoke test: crm-sync dry run"
aws lambda invoke --function-name "${PREFIX}-crm-sync" --region "$DATA_REGION" \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"sync","dryRun":true}' /tmp/hvi-crm-sync.json >/dev/null
head -c 400 /tmp/hvi-crm-sync.json; echo

hvi_log "done. AgentCore already has CRM_FN set, so submit_to_crm now resolves."
