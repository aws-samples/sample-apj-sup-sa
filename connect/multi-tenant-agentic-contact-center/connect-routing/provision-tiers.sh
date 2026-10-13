#!/bin/bash
# Set each merchant's routing tier (VIP | key | shared) as the `tier` attribute on
# its Customer Profiles ACCOUNT_PROFILE (AccountNumber = merchant_id). The routing
# Lambdas read it to pick the working queue and contact priority (VIP 1, key 2,
# shared 5). Idempotent. Requires infra/provision-customer-profiles.sh to have run.
#
# Env overrides: R / AWS_REGION, ENV_NAME, D (profiles domain),
#   TIERS="mch_luxe=VIP mch_nova=key ..." (default below)
set -uo pipefail
R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
ENV_NAME="${ENV_NAME:-}"
D="${D:-anycompany-pay-customer-profile${ENV_NAME:+-${ENV_NAME}}}"
TIERS="${TIERS:-mch_luxe=VIP mch_nova=key mch_pixel=shared mch_terra=shared mch_volt=shared}"
echo "Region: $R   Domain: $D"

for pair in $TIERS; do
  MID="${pair%%=*}"; TIER="${pair#*=}"
  PID=$(aws customer-profiles search-profiles --region "$R" --domain-name "$D" \
    --key-name _account --values "$MID" \
    --query "Items[?ProfileType=='ACCOUNT_PROFILE'].ProfileId | [0]" --output text 2>/dev/null)
  if [[ -z "$PID" || "$PID" == "None" ]]; then
    echo "  $MID: no ACCOUNT_PROFILE (run infra/provision-customer-profiles.sh); skipped"
    continue
  fi
  aws customer-profiles update-profile --region "$R" --domain-name "$D" --profile-id "$PID" \
    --attributes "tier=$TIER" --query ProfileId --output text >/dev/null \
    && echo "  $MID -> $TIER ($PID)"
done
