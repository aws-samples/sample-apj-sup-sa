#!/usr/bin/env bash
# Seed a demo user in the G1 Cognito pool (the pool is admin-create-only — no open sign-up).
# Sets a permanent password so the sign-in screen works immediately (no forced reset).
#
# Usage:  infra/g1/seed-user.sh <email> <password>
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
DEMO_STACK="interviewcoach-g1-demo"
EMAIL="${1:?usage: seed-user.sh <email> <password>}"
PASSWORD="${2:?usage: seed-user.sh <email> <password>}"

USER_POOL_ID="$(aws cloudformation describe-stacks --stack-name "$DEMO_STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)"

echo "Creating $EMAIL in pool $USER_POOL_ID"
aws cognito-idp admin-create-user --region "$REGION" \
  --user-pool-id "$USER_POOL_ID" \
  --username "$EMAIL" \
  --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
  --message-action SUPPRESS >/dev/null

# Permanent password so the demo can sign in without the NEW_PASSWORD_REQUIRED challenge.
aws cognito-idp admin-set-user-password --region "$REGION" \
  --user-pool-id "$USER_POOL_ID" \
  --username "$EMAIL" \
  --password "$PASSWORD" \
  --permanent >/dev/null

echo "Done. Sign in at the demo URL with $EMAIL."
