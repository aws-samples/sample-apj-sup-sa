#!/bin/bash
# Deploy the AnyCompanyPay Aurora PostgreSQL module (private cluster + seeded
# multi-tenant transactions) into ap-southeast-1 (Singapore).
#
# Prereqs: valid AWS credentials (run `aws login` if the session expired),
# Docker running (CDK bundles the seed Lambda), and the account/region
# bootstrapped for CDK (the main infra already bootstraps ap-southeast-1).
set -euo pipefail
cd "$(dirname "$0")"

# Region from the environment (never hardcoded). Set AWS_REGION (or R /
# CDK_DEFAULT_REGION) to deploy to another region, e.g. AWS_REGION=us-west-2.
R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
if [[ -z "$R" ]]; then
  echo "ERROR: no region. Set AWS_REGION (or R, or CDK_DEFAULT_REGION), e.g. AWS_REGION=us-west-2 $0" >&2
  exit 1
fi
export CDK_DEFAULT_REGION="$R"
export AWS_REGION="$R"
echo "==> Region: $R"

echo "==> Installing module dependencies"
npm install

echo "==> Deploying AnyCompanyPayAuroraStack (Aurora provisioning takes ~10-15 min)"
npx cdk deploy AnyCompanyPayAuroraStack --require-approval never --outputs-file cdk-outputs.json

echo
echo "=== Done. Outputs in cdk-outputs.json ==="
echo "The cluster is in isolated private subnets and is NOT publicly reachable."
echo "Credentials live in Secrets Manager (see the SecretArn output)."
