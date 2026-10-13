#!/usr/bin/env bash
# =============================================================================
#  rebuild-agent.sh — rebuild the agent container image after a code edit
# =============================================================================
#  `make deploy` applies INFRASTRUCTURE changes (uncomment a fence, flip a
#  value). It does NOT rebuild the agent's Docker image, because the template
#  only rebuilds when the image *tag* changes. So after you edit the agent's
#  Python (unicorn_rental_agent.py) or its SOP, run `make build`, which runs
#  this script.
#
#  What it does (all plain AWS CLI — nothing hidden):
#    1. Zip the agent code and upload it to the deploy bucket (CodeBuild's source).
#    2. Pick a fresh, unique image tag (build-<timestamp>).
#    3. Run `make deploy AGENT_IMAGE_TAG=<tag>`. CloudFormation then:
#         - rebuilds + pushes the image under the new tag (CodeBuild), and
#         - rolls the AgentCore Runtime to a new version pointing at that tag,
#           re-applying the JWT auth / network / env from the template.
#
#  That last point is the whole reason this is a one-liner now: CloudFormation
#  owns the runtime update, so we DON'T call `update-agent-runtime` by hand (which
#  replaces the entire config and would silently drop the JWT authorizer).
#
#  Override anything on the command line, e.g.:
#    AGENT_IMAGE_TAG=my-tag ./scripts/rebuild-agent.sh
# =============================================================================
set -euo pipefail

# Resolve paths relative to this script so it works from any directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # app/agentcore_strands
cd "$AGENT_DIR"

# --- read the few values we need from config.env (same file the Makefile uses) -
cfg() { grep -E "^$1=" config.env 2>/dev/null | head -1 | cut -d= -f2-; }
REGION="${REGION:-$(cfg AWS_REGION)}"
: "${REGION:?AWS_REGION not found in config.env — run from app/agentcore_strands/}"
ACCOUNT_ID="${ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
CFN_BUCKET="${CFN_BUCKET:-bedrock-agentcore-cfn-${ACCOUNT_ID}-${REGION}}"
AGENT_CODE_KEY="${AGENT_CODE_KEY:-agent/agent_code.zip}"

# A unique tag per build so CloudFormation always sees a change and re-rolls the
# runtime. (date is fine here — every build wants a new tag.)
AGENT_IMAGE_TAG="${AGENT_IMAGE_TAG:-build-$(date +%Y%m%d-%H%M%S)}"

echo "==> [build] zipping agent code"
ZIP="$(mktemp).zip"
zip -j "$ZIP" \
  unicorn_rental_agent.py \
  unicorn_rental_analytics.sop.md \
  requirements.txt \
  config.env.sample \
  -x '*.pyc' '*__pycache__*' >/dev/null
trap 'rm -f "$ZIP"' EXIT

echo "==> [build] uploading -> s3://${CFN_BUCKET}/${AGENT_CODE_KEY}"
aws s3 cp "$ZIP" "s3://${CFN_BUCKET}/${AGENT_CODE_KEY}" --region "$REGION"

echo "==> [build] deploying with image tag ${AGENT_IMAGE_TAG}"
echo "    (CloudFormation rebuilds the image under this tag and rolls the runtime)"
make deploy AGENT_IMAGE_TAG="$AGENT_IMAGE_TAG"

echo "==> [build] done — the runtime is now serving image tag ${AGENT_IMAGE_TAG}"
