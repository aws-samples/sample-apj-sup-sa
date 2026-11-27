#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Rebuild and publish only the web app to an existing deployment (no infrastructure change).
#
#   ./deployment/publish-site.sh [--stack <name>]
#
# Files from earlier builds are kept, so a browser that still holds the previous index.html can load
# its hashed scripts. The hashed assets get the same year-long immutable Cache-Control the CDK
# deployment sets; index.html is uploaded last, marked no-cache, and invalidated in CloudFront.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deployment/common.sh
source "$ROOT/deployment/common.sh"

STACK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stack) [[ $# -ge 2 ]] || air_die "--stack needs a stack name"; STACK="$2"; shift ;;
    *) echo "usage: publish-site.sh [--stack <name>]" >&2; exit 2 ;;
  esac
  shift
done
[[ -n "$STACK" ]] || STACK="${AIR_STACK:-$(air_stack "${AIR_PREFIX:-air}")}"

air_check_tools
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
AWS_REGION="$(air_region)"
export AWS_REGION
# The account is printed like deploy.sh and destroy.sh print it: this script uploads to whatever
# account the current credentials name, and said nothing about which one (P2-059).
echo "Account $ACCOUNT, Region $AWS_REGION, stack $STACK"
out() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
BUCKET="$(out SiteBucket)"; DIST="$(out DistributionId)"
[[ -n "$BUCKET" && "$BUCKET" != "None" ]] || air_die "stack $STACK has no SiteBucket output; run deploy.sh"
python3 "$ROOT/deployment/build.py" site
[[ -f "$ROOT/build/site/index.html" ]] || air_die "the web app was not built (build/site/index.html is missing)"
# Matches infra/insights_radar_infra/frontend.py: hashed assets are immutable for a year, so a
# published build is not revalidated on every visit.
aws s3 sync "$ROOT/build/site/" "s3://$BUCKET/" --exclude index.html --only-show-errors \
  --cache-control "public, max-age=31536000, immutable"
aws s3 cp "$ROOT/build/site/index.html" "s3://$BUCKET/index.html" --cache-control "no-cache" --only-show-errors
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/index.html" "/" --query Invalidation.Id --output text
