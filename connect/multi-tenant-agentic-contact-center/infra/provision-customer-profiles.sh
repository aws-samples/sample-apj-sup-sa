#!/bin/bash
# Provision Amazon Connect Customer Profiles for the AnyCompanyPay merchants (B2B model).
# Per merchant: 1 ACCOUNT_PROFILE (the business) + 2 PROFILE (individual users),
# linked by AccountNumber = merchant_id. Idempotent: skips entities that already exist.
set -uo pipefail
# Region from the environment (never hardcoded).
R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
if [ -z "$R" ]; then
  echo "ERROR: no region. Set AWS_REGION (or R), e.g. AWS_REGION=us-west-2 $0" >&2; exit 1
fi
# Optional env discriminator. Empty by default → clean common names (no suffix).
ENV_NAME="${ENV_NAME:-}"
# Customer Profiles domain — matches AnyCompanyPayConnectStack (anycompany-pay-customer-profile[-<env>]).
# Override with D=... if needed.
D="${D:-anycompany-pay-customer-profile${ENV_NAME:+-${ENV_NAME}}}"
echo "Region: $R   Env: $ENV_NAME   Domain: $D"

# merchant_id | merchant_name | user1(owner) | user2(ops)
MERCHANTS=(
  "mch_luxe|Luxe Living|owner@luxeliving.example|ops@luxeliving.example"
  "mch_nova|NovaMart|owner@novamart.example|ops@novamart.example"
  "mch_pixel|PixelForge|owner@pixelforge.example|ops@pixelforge.example"
  "mch_terra|TerraFoods|owner@terrafoods.example|ops@terrafoods.example"
  "mch_volt|VoltCharge|owner@voltcharge.example|ops@voltcharge.example"
)

acct_exists () { # merchant_id -> count of ACCOUNT_PROFILE with that account number
  aws customer-profiles search-profiles --region "$R" --domain-name "$D" \
    --key-name _account --values "$1" \
    --query "length(Items[?ProfileType=='ACCOUNT_PROFILE'])" --output text 2>/dev/null || echo 0
}
indiv_exists () { # email -> count of individual PROFILE with that email
  aws customer-profiles search-profiles --region "$R" --domain-name "$D" \
    --key-name _email --values "$1" \
    --query "length(Items[?ProfileType=='PROFILE'])" --output text 2>/dev/null || echo 0
}

for row in "${MERCHANTS[@]}"; do
  IFS='|' read -r MID MNAME U1 U2 <<< "$row"
  echo "== $MNAME ($MID) =="

  if [ "$(acct_exists "$MID")" != "0" ]; then
    echo "  [account] exists, skip"
  else
    PID=$(aws customer-profiles create-profile --region "$R" --domain-name "$D" \
      --profile-type ACCOUNT_PROFILE --party-type-string BUSINESS \
      --business-name "$MNAME" --account-number "$MID" --business-email-address "$U1" \
      --additional-information "AnyCompanyPay merchant tenant $MID" \
      --attributes "{\"merchant_id\":\"$MID\"}" \
      --query ProfileId --output text)
    echo "  [account] created $PID"
  fi

  for pair in "$U1|owner|Owner" "$U2|ops|Ops"; do
    IFS='|' read -r EMAIL ROLE FIRST <<< "$pair"
    if [ "$(indiv_exists "$EMAIL")" != "0" ]; then
      echo "  [user] $EMAIL exists, skip"
    else
      PID=$(aws customer-profiles create-profile --region "$R" --domain-name "$D" \
        --profile-type PROFILE --party-type-string INDIVIDUAL \
        --email-address "$EMAIL" --account-number "$MID" \
        --first-name "$FIRST" --last-name "$MNAME" \
        --additional-information "AnyCompanyPay merchant user ($ROLE) of $MID" \
        --attributes "{\"merchant_id\":\"$MID\",\"merchant_name\":\"$MNAME\",\"role\":\"$ROLE\"}" \
        --query ProfileId --output text)
      echo "  [user] $EMAIL ($ROLE) created $PID"
    fi
  done
done

echo
echo "=== Verify: profiles per merchant (by _account) ==="
for row in "${MERCHANTS[@]}"; do
  IFS='|' read -r MID MNAME U1 U2 <<< "$row"
  aws customer-profiles search-profiles --region "$R" --domain-name "$D" \
    --key-name _account --values "$MID" \
    --query "Items[].{type:ProfileType,name:BusinessName||FirstName,email:BusinessEmailAddress||EmailAddress,acct:AccountNumber}" \
    --output table
done
