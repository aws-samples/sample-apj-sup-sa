#!/usr/bin/env bash
# Build and deploy the static console/BFF and Core stack using the terminal's AWS target.
set -euo pipefail

BOOTSTRAP=false
case "${1:-}" in
  --bootstrap) BOOTSTRAP=true; shift ;;
  --help|-h)
    echo "Usage: scripts/deploy-all.sh [--bootstrap]"
    echo "Uses current AWS credentials and AWS_REGION / AWS_DEFAULT_REGION / profile region."
    exit 0 ;;
esac
if [ "$#" -ne 0 ]; then
  echo "error: usage: scripts/deploy-all.sh [--bootstrap]" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/client/app"
OUTPUTS="$ROOT/cdk-outputs.json"
STAGE="preflight"
trap 'status=$?; printf "error: deployment stopped during %s (exit %s). See docs/OperationalGuide.md → Installation failures and manual recovery.\n" "$STAGE" "$status" >&2; exit "$status"' ERR
export AWS_PAGER="" AWS_CLI_AUTO_PROMPT=off
# shellcheck source=_aws-context.sh
. "$ROOT/scripts/_aws-context.sh"
require_tool node
require_tool npm
require_tool jq
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) === 24 ? 0 : 1)'; then
  echo "error: use Node.js 24 (see .nvmrc) to build and deploy." >&2
  exit 1
fi
load_project_settings "$ROOT"
resolve_aws_target
check_stack_state BedrockMeterCoreStack

detect_local_time_zone() {
  node -e 'const z=Intl.DateTimeFormat().resolvedOptions().timeZone; if (!z) process.exit(1); process.stdout.write(z)'
}

validate_time_zone() {
  node -e 'try { new Intl.DateTimeFormat("en-US", {timeZone: process.argv[1]}).format(); } catch (_) { process.exit(1); }' "$1"
}

if [ -z "${BEDROCK_METER_QUOTA_TIME_ZONE:-}" ]; then
  BEDROCK_METER_QUOTA_TIME_ZONE="$(detect_local_time_zone 2>/dev/null || printf 'UTC')"
  export BEDROCK_METER_QUOTA_TIME_ZONE

  if [ -z "${CI:-}" ] && ! grep -q '^BEDROCK_METER_QUOTA_TIME_ZONE=' "$DEPLOY_ENV_FILE" 2>/dev/null; then
    {
      printf '\n'
      printf '# Initial calendar boundary for day/week/month quotas (IANA time zone).\n'
      printf 'BEDROCK_METER_QUOTA_TIME_ZONE=%s\n' "$BEDROCK_METER_QUOTA_TIME_ZONE"
    } >> "$DEPLOY_ENV_FILE"
    chmod 600 "$DEPLOY_ENV_FILE"
    echo "==> Detected and saved quota timezone: $BEDROCK_METER_QUOTA_TIME_ZONE"
  else
    echo "==> Detected quota timezone: $BEDROCK_METER_QUOTA_TIME_ZONE"
  fi
else
  export BEDROCK_METER_QUOTA_TIME_ZONE
  echo "==> Configured quota timezone: $BEDROCK_METER_QUOTA_TIME_ZONE"
fi

if ! validate_time_zone "$BEDROCK_METER_QUOTA_TIME_ZONE"; then
  echo "ERROR: BEDROCK_METER_QUOTA_TIME_ZONE must be a valid IANA time zone (for example Asia/Seoul or UTC)." >&2
  exit 1
fi

echo "    Used only to seed a new ConfigTable; an existing deployment keeps its runtime setting."

STAGE="dependency installation"
echo "==> Install dependencies"
( cd "$APP" && npm ci --no-fund )
( cd "$ROOT/cdk" && npm ci --no-fund )

STAGE="console build"
echo "==> Build static console and BFF"
( cd "$APP" && npm run build:console-artifact )

if [ "$BOOTSTRAP" = true ]; then
  STAGE="CDK bootstrap"
  echo "==> Bootstrap aws://$ACCOUNT/$REGION"
  ( cd "$ROOT/cdk" && ./node_modules/.bin/cdk bootstrap "aws://$ACCOUNT/$REGION" )
fi

STAGE="stack deployment"
echo "==> Deploy BedrockMeterCoreStack"
( cd "$ROOT/cdk" && ./node_modules/.bin/cdk deploy BedrockMeterCoreStack \
    --require-approval never --outputs-file "$OUTPUTS" )

STAGE="deployment output validation"
CONSOLE_URL="$(jq -er '.BedrockMeterCoreStack.ConsoleUrl | select(type == "string" and startswith("https://"))' "$OUTPUTS")"
POOL_ID="$(jq -er '.BedrockMeterCoreStack.ConsoleUserPoolId | select(type == "string" and length > 0)' "$OUTPUTS")"
if [[ "$POOL_ID" != "$REGION"_* ]]; then
  echo "error: deployed user pool does not match the selected region." >&2
  exit 1
fi

STAGE="console administrator setup"
if [ -n "${CONSOLE_ADMIN_USERNAME:-}" ] && [ -n "${CONSOLE_ADMIN_EMAIL:-}" ] && [ -n "${CONSOLE_ADMIN_PASSWORD:-}" ]; then
  POOL_ID="$POOL_ID" \
    "$ROOT/scripts/add-console-admin.sh" "$CONSOLE_ADMIN_USERNAME" "$CONSOLE_ADMIN_EMAIL" "$CONSOLE_ADMIN_PASSWORD"
else
  echo "Console users are managed separately. If you have not created one yet, run:"
  echo "  scripts/add-console-admin.sh <username> <email>"
  echo "Read-only users: scripts/add-console-viewer.sh <username> <email>"
fi

echo "==> Infrastructure deployment complete"
echo "    AWS account: $ACCOUNT   Region: $REGION"
echo "    Console: $CONSOLE_URL"
echo "    Use the same AWS credentials/region for user creation and redeployment."
echo "    Confirm the SNS subscription email, then sign in to the console."
