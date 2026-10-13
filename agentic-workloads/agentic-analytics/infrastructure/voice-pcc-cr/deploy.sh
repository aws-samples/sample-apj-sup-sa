#!/usr/bin/env bash
# Deploy the Pipecat Cloud agent AS a CloudFormation custom resource.
#
# The agent's lifecycle is now bound to the stack:
#   - cfn deploy  -> cloud-build the bot image + deploy the PCC service
#   - change MinAgents param + redeploy -> rescales the live agent
#   - delete-stack -> tears the agent down (no orphaned billing)
#
# Usage:
#   PCC_API_KEY=sk_xxx MIN_AGENTS=0 ./deploy.sh
#
# PCC_API_KEY is the PRIVATE API key (sk_...), NOT the pcc_pat_ PAT — the
# documented /v1/agents + /v1/builds API only accepts the private key (the PAT
# 401s there).
#
# Prereqs: the PCC secret set 'voice-analytics-secrets' must already exist
# (created by scripts/deploy_voice_pcc.sh or `pipecatcloud secrets set ...`).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
REGION="${AWS_REGION:-us-west-2}"
ENV_NAME="${ENV_NAME:-agentic-analytics}"
STACK="${STACK:-agentic-analytics-voice-pcc}"
BUCKET="${ARTIFACTS_BUCKET:-agentic-analytics-artifacts}"
MIN_AGENTS="${MIN_AGENTS:-0}"
MAX_AGENTS="${MAX_AGENTS:-5}"

[ -n "${PCC_API_KEY:-}" ] || { echo "ERROR: set PCC_API_KEY (sk_... PRIVATE API key)"; exit 1; }

echo "==> Bundling Lambda: index.py + bot build context"
rm -rf "$HERE/build"; mkdir -p "$HERE/build/bot_context"
cp "$HERE/index.py" "$HERE/build/"
# The cloud build needs the SAME context the laptop/PCC Dockerfile uses.
cp "$ROOT/app/voice/bot.py" "$ROOT/app/voice/analytics_processor.py" "$ROOT/app/voice/auth.py" \
   "$ROOT/app/voice/pyproject.toml" "$ROOT/app/voice/uv.lock" "$HERE/build/bot_context/"
cp "$ROOT/app/voice/Dockerfile" "$HERE/build/bot_context/Dockerfile"   # PCC base-image Dockerfile

# Hash the bot source so a CODE-ONLY change flips the CustomResource's SourceHash
# property → CFN fires the Update handler → the bot image is rebuilt. (Without this,
# CFN sees no property change for a code edit and the agent is never rebuilt.)
if command -v sha256sum >/dev/null 2>&1; then
  SOURCE_HASH="$(cat "$HERE/build/bot_context/"* | sha256sum | cut -c1-16)"
else
  SOURCE_HASH="$(cat "$HERE/build/bot_context/"* | shasum -a 256 | cut -c1-16)"
fi
echo "    bot source hash: $SOURCE_HASH"

echo "==> Packaging + deploying ${STACK}"
aws cloudformation package \
  --template-file "$HERE/voice-pcc-cr-stack.yaml" \
  --s3-bucket "$BUCKET" --s3-prefix voice-pcc-cr \
  --region "$REGION" --output-template-file "$HERE/build/packaged.yaml"

aws cloudformation deploy \
  --template-file "$HERE/build/packaged.yaml" \
  --stack-name "$STACK" --region "$REGION" \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides \
    EnvironmentName="$ENV_NAME" \
    AgentName="${AGENT_NAME:-voice-analytics-agent}" \
    SecretSet="${SECRET_SET:-voice-analytics-secrets}" \
    MinAgents="$MIN_AGENTS" \
    MaxAgents="$MAX_AGENTS" \
    PccPrivateApiKey="$PCC_API_KEY" \
    SourceHash="$SOURCE_HASH"

echo "==> Done. PCC agent managed by stack ${STACK} (MinAgents=${MIN_AGENTS})."
echo "    Rescale: redeploy with MIN_AGENTS=1.  Tear down: aws cloudformation delete-stack --stack-name ${STACK}"
