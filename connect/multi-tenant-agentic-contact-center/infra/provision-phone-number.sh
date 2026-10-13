#!/bin/bash
# Claim an inbound VOICE phone number for the Connect instance and (optionally)
# associate it with a voice contact flow.
#
# Why a script, not CloudFormation: a claimed phone number is a billable,
# region-specific resource, and CFN's AWS::Connect::PhoneNumber claims a NEW
# number on each create (you can't pin an exact number or associate a contact
# flow from it). Keeping this out-of-band — like provision-agent.sh /
# provision-merchants.sh — makes it idempotent (reuses an already-claimed number)
# and keeps a billable number out of the stack's blast radius.
#
# Idempotent: if a number with the same description is already claimed on the
# instance, it is reused instead of claiming another.
#
# Env overrides:
#   R / AWS_REGION   region (default: configured region)
#   ENV_NAME     environment discriminator (default v2)
#   ALIAS        Connect instance alias (default anycompany-pay-<account>-<env>)
#   COUNTRY      phone number country code (default US)
#   TYPE         phone number type: DID | TOLL_FREE (default DID)
#   DESC         description used to find/reuse the number (default "AnyCompanyPay voice demo")
#   FLOW_NAME    voice contact flow to associate (default "Sample inbound flow (first contact experience)")
#   ASSOCIATE    1 to associate the flow, 0 to skip (default 1)
set -uo pipefail
# Region from the environment (never hardcoded).
R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
if [ -z "$R" ]; then
  echo "ERROR: no region. Set AWS_REGION (or R), e.g. AWS_REGION=us-west-2 $0" >&2; exit 1
fi
# Optional env discriminator. Empty by default → clean common names (no suffix).
ENV_NAME="${ENV_NAME:-}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
ALIAS="${ALIAS:-anycompany-pay-${ACCOUNT}${ENV_NAME:+-${ENV_NAME}}}"
COUNTRY="${COUNTRY:-US}"
TYPE="${TYPE:-DID}"
DESC="${DESC:-AnyCompanyPay voice demo}"
FLOW_NAME="${FLOW_NAME:-Sample inbound flow (first contact experience)}"
ASSOCIATE="${ASSOCIATE:-1}"

echo "Resolving Connect instance for alias: $ALIAS"
INSTANCE_ID="$(aws connect list-instances --region "$R" \
  --query "InstanceSummaryList[?InstanceAlias=='${ALIAS}'].Id | [0]" --output text)"
if [ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" = "None" ]; then
  echo "ERROR: no Connect instance found for alias $ALIAS"; exit 1
fi
INSTANCE_ARN="arn:aws:connect:${R}:${ACCOUNT}:instance/${INSTANCE_ID}"
echo "  InstanceId: $INSTANCE_ID"

# Confirm the instance accepts inbound calls (a number is useless otherwise).
INBOUND="$(aws connect describe-instance --region "$R" --instance-id "$INSTANCE_ID" \
  --query "Instance.InboundCallsEnabled" --output text 2>/dev/null)"
if [ "$INBOUND" != "True" ] && [ "$INBOUND" != "true" ]; then
  echo "WARNING: InboundCallsEnabled=$INBOUND — inbound voice may not work on this instance."
fi

# ------------------------------------------------------------------
# 1) Reuse an already-claimed number with our description, else claim one.
# ------------------------------------------------------------------
PHONE_ID=""
PHONE_NUM=""
echo "Checking for an already-claimed number described '$DESC'…"
for ID in $(aws connect list-phone-numbers-v2 --region "$R" --target-arn "$INSTANCE_ARN" \
              --query "ListPhoneNumbersSummaryList[].PhoneNumberId" --output text 2>/dev/null | tr '\t' '\n'); do
  [ -z "$ID" ] && continue
  D="$(aws connect describe-phone-number --region "$R" --phone-number-id "$ID" \
        --query "ClaimedPhoneNumberSummary.PhoneNumberDescription" --output text 2>/dev/null)"
  if [ "$D" = "$DESC" ]; then
    PHONE_ID="$ID"
    PHONE_NUM="$(aws connect describe-phone-number --region "$R" --phone-number-id "$ID" \
                  --query "ClaimedPhoneNumberSummary.PhoneNumber" --output text 2>/dev/null)"
    echo "  reusing already-claimed $PHONE_NUM ($PHONE_ID)"
    break
  fi
done

if [ -z "$PHONE_ID" ]; then
  echo "Searching for an available $COUNTRY $TYPE number…"
  aws connect search-available-phone-numbers --region "$R" --target-arn "$INSTANCE_ARN" \
    --phone-number-country-code "$COUNTRY" --phone-number-type "$TYPE" --max-results 10 \
    --query "AvailableNumbersList[].PhoneNumber" --output text 2>/dev/null | tr '\t' '\n' > /tmp/pgnums.txt
  if [ ! -s /tmp/pgnums.txt ]; then
    echo "ERROR: no available $COUNTRY $TYPE numbers (some countries require prerequisites)."; exit 1
  fi
  # Numbers can be taken between search and claim — try each until one succeeds.
  while IFS= read -r N; do
    [ -z "$N" ] && continue
    OUT="$(aws connect claim-phone-number --region "$R" --target-arn "$INSTANCE_ARN" \
      --phone-number "$N" --phone-number-description "$DESC" \
      --tags Project=AnyCompanyPay,Purpose=voice-demo 2>&1)"
    if echo "$OUT" | grep -q PhoneNumberId; then
      PHONE_ID="$(echo "$OUT" | awk -F'"' '/PhoneNumberId/{print $4; exit}')"
      PHONE_NUM="$N"
      echo "  claimed $PHONE_NUM ($PHONE_ID)"
      break
    fi
  done < /tmp/pgnums.txt
  if [ -z "$PHONE_ID" ]; then
    echo "ERROR: could not claim any available number (all taken during the attempt); re-run."; exit 1
  fi
fi

# ------------------------------------------------------------------
# 2) Associate the number with a voice contact flow (optional).
# ------------------------------------------------------------------
if [ "$ASSOCIATE" = "1" ]; then
  echo "Resolving contact flow: $FLOW_NAME"
  FLOW_ID="$(aws connect list-contact-flows --region "$R" --instance-id "$INSTANCE_ID" \
    --query "ContactFlowSummaryList[?Name=='${FLOW_NAME}'].Id | [0]" --output text 2>/dev/null)"
  if [ -z "$FLOW_ID" ] || [ "$FLOW_ID" = "None" ]; then
    echo "  WARNING: flow '$FLOW_NAME' not found — number claimed but NOT routed."
    echo "  Associate later with: aws connect associate-phone-number-contact-flow \\"
    echo "    --region $R --instance-id $INSTANCE_ID --phone-number-id $PHONE_ID --contact-flow-id <FLOW_ID>"
  else
    aws connect associate-phone-number-contact-flow --region "$R" \
      --phone-number-id "$PHONE_ID" --instance-id "$INSTANCE_ID" --contact-flow-id "$FLOW_ID" >/dev/null
    echo "  associated with '$FLOW_NAME' ($FLOW_ID)"
  fi
else
  echo "Skipping flow association (ASSOCIATE=0)."
fi

echo
echo "=== Done ==="
echo "Voice number : $PHONE_NUM"
echo "PhoneNumberId: $PHONE_ID"
echo "Instance     : $ALIAS ($INSTANCE_ID)"
[ "$ASSOCIATE" = "1" ] && echo "Inbound flow : $FLOW_NAME"
echo
echo "Test: call the number; an agent signed into the CCP (Admin -> Contact Center,"
echo "status Available) receives the call. Release later with:"
echo "  aws connect release-phone-number --region $R --phone-number-id $PHONE_ID"
