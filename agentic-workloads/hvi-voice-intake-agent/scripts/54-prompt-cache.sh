#!/usr/bin/env bash
# Build the shared prompt cache and grant the media host read access to it.
#
# WHY THIS IS THE BIGGEST LATENCY CHANGE IN THE SYSTEM
# The turn budget was ~463ms STT + ~600ms LLM + ~1712ms TTS. TTS dominated, and
# tools/prompt_inventory.py showed that 85 of the 92 utterances the agent can
# speak contain no placeholders - identical on every call, forever. So the largest
# cost in the budget was being paid repeatedly for text that never changes.
#
# After this, a cache hit is a local disk read and the perceived gap after the
# caller stops speaking drops from roughly 2.8s to a bit over a second, most of
# which is the 800ms end-of-speech decision that cannot be removed.
#
# The 7 personalised utterances ({{customerName}} and friends) are NOT built here;
# they are synthesised per call while the phone rings, which costs nothing because
# nothing is on the critical path until the callee answers.
#
# Usage
#   bash scripts/54-prompt-cache.sh
#   bash scripts/54-prompt-cache.sh --dry-run

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
ACC="$AWS_ACCOUNT_ID"
# Same bucket as the model artifacts, under its own prefix. It already has
# encryption and a public-access block, and one fewer bucket is one fewer thing
# to get wrong.
BUCKET="${PREFIX}-artifacts-${ACC}-${R}"
export PROMPT_CACHE_BUCKET="$BUCKET"

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  hvi_log "bucket $BUCKET exists"
else
  hvi_log "creating bucket $BUCKET"
  aws s3api create-bucket --bucket "$BUCKET" --region "$R" \
    --create-bucket-configuration "LocationConstraint=$R" >/dev/null
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
  aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
fi

# Endpoint check first: 85 synthesis calls against a missing endpoint is 85
# identical errors, and the message is clearer here.
STATUS=$(aws sagemaker describe-endpoint --endpoint-name "${OMNIVOICE_ENDPOINT:-${PREFIX}-omnivoice}" \
  --region "$R" --query 'EndpointStatus' --output text 2>/dev/null || echo NONE)
if [ "$STATUS" != "InService" ]; then
  hvi_err "OmniVoice endpoint is $STATUS, not InService. Run scripts/52-omnivoice.sh"
  exit 1
fi
hvi_log "omnivoice endpoint InService"

hvi_log "building the cache (this walks the form schema in DynamoDB, so runtime-added items are included)"
./.venv/bin/python tools/build_prompt_cache.py ${1:+"$1"}

[ "${1:-}" = "--dry-run" ] && exit 0

COUNT=$(aws s3 ls "s3://$BUCKET/prompt-cache/" --recursive --region "$R" 2>/dev/null | wc -l | tr -d ' ')
BYTES=$(aws s3 ls "s3://$BUCKET/prompt-cache/" --recursive --region "$R" --summarize 2>/dev/null \
  | awk '/Total Size/ {print $3}')
hvi_log "cache holds $COUNT object(s), ${BYTES:-0} bytes"

# ---------------------------------------------------------------------------
# Let the media host read it
# ---------------------------------------------------------------------------
ROLE="${PREFIX}-asterisk-role"
hvi_log "granting $ROLE read access to the cache"
aws iam put-role-policy --role-name "$ROLE" \
  --policy-name "${PREFIX}-prompt-cache-read" \
  --policy-document "$(jq -nc --arg b "$BUCKET" \
    '{Version:"2012-10-17",
      Statement:[{Sid:"ReadPromptCache",Effect:"Allow",
                  Action:["s3:GetObject"],
                  Resource:["arn:aws:s3:::\($b)/prompt-cache/*"]},
                 {Sid:"ListPromptCache",Effect:"Allow",
                  Action:["s3:ListBucket"],
                  Resource:["arn:aws:s3:::\($b)"],
                  Condition:{StringLike:{"s3:prefix":["prompt-cache/*"]}}},
                 {Sid:"WriteBackOnMiss",Effect:"Allow",
                  Action:["s3:PutObject"],
                  Resource:["arn:aws:s3:::\($b)/prompt-cache/*"]}]}')"

touch config/env.local.sh
if grep -q '^export PROMPT_CACHE_BUCKET=' config/env.local.sh 2>/dev/null; then
  hvi_warn "PROMPT_CACHE_BUCKET already in config/env.local.sh"
else
  printf 'export PROMPT_CACHE_BUCKET="%s"\n' "$BUCKET" >> config/env.local.sh
  hvi_log "appended PROMPT_CACHE_BUCKET to config/env.local.sh"
fi

hvi_log ""
hvi_log "next: redeploy the media service so it picks up the cache"
hvi_log "  bash scripts/72-media-deploy.sh"
