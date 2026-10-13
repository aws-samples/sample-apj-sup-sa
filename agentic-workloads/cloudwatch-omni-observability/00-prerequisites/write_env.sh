#!/usr/bin/env bash
# Copy the deployed stack's outputs into ../.env (OMNI_SPACE_ID, SHOP_LOG_GROUP, ALERTS_TOPIC_ARN).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"; ENV="$ROOT/.env"
[ -f "$ENV" ] || cp "$ROOT/.env.example" "$ENV"
. "$ROOT/common/load-env.sh"
omni_load_env "$ENV"
unset -f omni_load_env
out() {
  local value
  value=$(aws cloudformation describe-stacks --stack-name OmniSamplesPrereqs --region "$AWS_REGION" \
            --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text)
  if [ -z "$value" ] || [ "$value" = "None" ]; then
    echo "Stack OmniSamplesPrereqs has no output $1 in $AWS_REGION. Run cdk deploy first." >&2; exit 1
  fi
  echo "$value"
}
setvar() {  # replace or append KEY=VALUE, including a commented-out "# KEY=" line (works with BSD and GNU sed)
  if grep -qE "^#? ?$1=" "$ENV"; then
    sed -E "s|^#? ?$1=.*|$1=$2|" "$ENV" > "$ENV.tmp" && mv "$ENV.tmp" "$ENV"
  else
    echo "$1=$2" >> "$ENV"
  fi
}
SPACE_ID=$(out SpaceId); LOG_GROUP=$(out ShopLogGroupName); TOPIC_ARN=$(out AlertsTopicArn); URL=$(out DomainEndpointUrl)
setvar OMNI_SPACE_ID "$SPACE_ID"
setvar SHOP_LOG_GROUP "$LOG_GROUP"
setvar ALERTS_TOPIC_ARN "$TOPIC_ARN"
echo "Updated $ENV:"; grep -E '^(AWS_REGION|OMNI_SPACE_ID|SHOP_LOG_GROUP|ALERTS_TOPIC_ARN)=' "$ENV"
echo; echo "Omni sign-in URL: $URL"
