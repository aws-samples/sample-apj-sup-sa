#!/usr/bin/env bash
# Build the SPA with the deployed stack outputs and publish it to Amplify Hosting (manual deployment).
# Usage: scripts/publish_web.sh [aws-profile]   (region us-east-1)
set -euo pipefail
PROFILE=${1:-${AWS_PROFILE:-default}}
REGION=us-east-1
out() { aws cloudformation describe-stacks --profile "$PROFILE" --region "$REGION" --stack-name "$1" \
  --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }

API_URL=$(out BedrockTierBench-Api ApiUrl)
DOMAIN=$(out BedrockTierBench-Api CognitoDomain)
CLIENT=$(out BedrockTierBench-Api UserPoolClientId)
APP_ID=$(out BedrockTierBench-Web AppId)
IDP=${IDENTITY_PROVIDER:-}   # "Federate" for the Midway variant

cd "$(dirname "$0")/../web"
npm ci --ignore-scripts --no-audit --no-fund
printf '{"apiUrl":"%s","cognitoDomain":"%s","clientId":"%s","identityProvider":"%s"}\n' \
  "$API_URL" "$DOMAIN" "$CLIENT" "$IDP" > public/config.json
npm run build
rm -f site.zip && (cd dist && zip -qr ../site.zip .)

read -r JOB URL < <(aws amplify create-deployment --profile "$PROFILE" --region "$REGION" \
  --app-id "$APP_ID" --branch-name main --query '[jobId,zipUploadUrl]' --output text)
curl -sSf -T site.zip "$URL"
aws amplify start-deployment --profile "$PROFILE" --region "$REGION" --app-id "$APP_ID" --branch-name main --job-id "$JOB" \
  --query 'jobSummary.status' --output text
echo "Published job $JOB to app $APP_ID"
