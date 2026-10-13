#!/usr/bin/env bash
# Read-only preflight: checks identity, Omni space, and Transaction Search.
# It never changes anything; for each missing piece it prints the command to run.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/common/load-env.sh"
omni_load_env "$ROOT/.env"
unset -f omni_load_env
: "${AWS_REGION:?set AWS_REGION in .env}"

ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

echo "== Identity"
if ID=$(aws sts get-caller-identity --output text --query Arn 2>&1); then
  ok "$ID"
  case "$ID" in *[Pp]rod*) warn "This looks like a PRODUCTION identity. Use a sandbox account for these samples." ;; esac
else
  warn "No credentials: $ID"; exit 1
fi

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
MGMT=$(aws organizations describe-organization --query Organization.MasterAccountId --output text 2>/dev/null || true)
if [ -n "$MGMT" ] && [ "$MGMT" = "$ACCOUNT" ]; then
  warn "This is the AWS Organizations MANAGEMENT account ($ACCOUNT). It cannot create a space under an"
  warn "organization domain. Set OMNI_MEMBER_ROLE_ARN in .env, then: source common/assume-member-role.sh"
fi

echo "== Region: $AWS_REGION"
case "$AWS_REGION" in us-east-1|us-west-2|eu-west-1) ok "Omni is available here" ;;
  *) warn "Omni is available in us-east-1, us-west-2, eu-west-1 only" ;; esac

echo "== Omni spaces"
if SPACES=$(aws cloudwatchomni list-spaces --region "$AWS_REGION" \
      --query 'items[].[spaceId,name,status]' --output text 2>&1); then
  if [ -z "$SPACES" ]; then
    warn "No space in $AWS_REGION. Deploy step 0 (00-prerequisites, CDK) or use the CloudWatch console's Omni setup."
  else
    echo "$SPACES" | while read -r id name status; do ok "$name  $id  ($status)"; done
  fi
else
  case "$SPACES" in
    *"invalid choice"*)
      warn "Your AWS CLI ($(aws --version 2>&1 | cut -d' ' -f1)) predates Omni (no 'cloudwatchomni' command)."
      warn "Omni needs AWS CLI 2.37.0+. Upgrade it (see Prerequisites in the README) to check spaces from here." ;;
    *) warn "list-spaces failed: $SPACES" ;;
  esac
fi

echo "== Transaction Search (trace segment destination)"
if DEST=$(aws xray get-trace-segment-destination --region "$AWS_REGION" --output text \
      --query '[Destination,Status]' 2>&1); then
  case "$DEST" in
    CloudWatchLogs*ACTIVE*) ok "$DEST" ;;
    *) warn "Destination/status: $DEST"
       warn "Enable it (account-level, takes ~10 min) with step 0, which also adds the CloudWatch Logs"
       warn "resource policy X-Ray needs to write spans:"
       echo "      cd 00-prerequisites && cdk deploy -c enableTransactionSearch=true"
       warn "Or use CloudWatch console > Application Signals > Transaction Search." ;;
  esac
else
  warn "Could not read: $DEST"
fi

echo "== Bedrock model: ${MODEL_ID:-<unset>}"
if [ -n "${MODEL_ID:-}" ]; then
  # Invoke it (5 output tokens): a listed inference profile can still be unusable, e.g. Anthropic
  # models before the account's use-case form is submitted.
  if OUT=$(aws bedrock-runtime converse --model-id "$MODEL_ID" --region "$AWS_REGION" \
             --messages '[{"role":"user","content":[{"text":"Say OK"}]}]' \
             --inference-config maxTokens=5 --query 'output.message.content[0].text' --output text 2>&1); then
    ok "invocable"
  else
    warn "Cannot invoke: $(echo "$OUT" | tail -1)"
    case "$OUT" in *"use case details"*)
      warn "Submit the Anthropic use-case form in the Bedrock console for this account, or use MODEL_ID=us.amazon.nova-2-lite-v1:0" ;; esac
  fi
fi
