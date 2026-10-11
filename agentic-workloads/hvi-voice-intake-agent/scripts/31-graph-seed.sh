#!/usr/bin/env bash
# Seed the Neptune health-condition graph and prove the ENFORCES behaviour.
#
# The verification at the end is the point: it shows that a confirmed condition
# yields a deterministic set of mandatory follow-ups, that a colloquial Hindi
# phrase resolves to the right ICD-10 node, and that drug interactions surface.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

FN="${PREFIX}-graph"
R="$DATA_REGION"

invoke() {
  local payload="$1" out
  out="$(mktemp)"
  aws lambda invoke --function-name "$FN" --region "$R" \
    --cli-binary-format raw-in-base64-out \
    --payload "$payload" "$out" >/dev/null
  cat "$out"
  rm -f "$out"
}

# ---- staleness guard -------------------------------------------------------
# The Lambda seeds from the health-kg.json bundled inside its own deployment
# package, so a working-tree edit is invisible here until the function is
# repackaged. Without this check the script happily re-seeds the previous
# revision and still reports success.
STAMP="build/.kg-deployed-sha256"
LOCAL_SHA="$(shasum -a 256 seed/health-kg.json | awk '{print $1}')"

if [ ! -f "$STAMP" ]; then
  hvi_warn "no record of the deployed KG revision ($STAMP missing)"
  hvi_warn "cannot verify the Lambda carries the current seed/health-kg.json"
  hvi_warn "run 'bash scripts/30-graph-lambda.sh' first if you edited the KG"
elif [ "$LOCAL_SHA" != "$(cat "$STAMP")" ]; then
  hvi_err "seed/health-kg.json has changed since hvi-graph was last packaged."
  hvi_err "  working tree: ${LOCAL_SHA:0:12}"
  hvi_err "  deployed    : $(cut -c1-12 "$STAMP")"
  hvi_err "Seeding now would silently load the OLD graph. Run this first:"
  hvi_err "  bash scripts/30-graph-lambda.sh"
  exit 1
else
  hvi_log "KG revision ${LOCAL_SHA:0:12} matches the deployed package"
fi

hvi_log "seeding the graph (this issues a few hundred Gremlin writes, ~1-2 min)"
invoke '{"action":"seed"}' | jq '.'

echo
hvi_log "graph contents"
invoke '{"action":"stats"}' | jq '.result'

echo
hvi_log "ENFORCES check - follow-ups mandated for diabetes (E11), in Hinglish"
invoke '{"action":"get_followups","icd10":"E11","language":"hinglish"}' \
  | jq '.result | {conditionLabel, mandatoryCount, protocolSource,
                   followUps: [.followUps[] | {questionId, prompt}]}'

echo
hvi_log "ENFORCES check - TB (A15) gets TB-specific follow-ups, not generic ones"
invoke '{"action":"get_followups","icd10":"A15","language":"en"}' \
  | jq '.result | {conditionLabel, protocolSource,
                   followUps: [.followUps[] | .questionId]}'

echo
hvi_log "colloquial resolution - patient says \"sugar ki bimari\""
invoke '{"action":"resolve_condition","phrase":"sugar ki bimari"}' | jq '.result'

echo
hvi_log "colloquial resolution - patient says \"lakwa\""
invoke '{"action":"resolve_condition","phrase":"lakwa"}' | jq '.result'

echo
hvi_log "drug interaction check - Aspirin + Warfarin + Metformin"
invoke '{"action":"check_interactions","medications":["Aspirin 75mg","Warfarin 5mg","Metformin 500mg"]}' \
  | jq '.result | {resolved, interactionCount, interactions}'

echo
hvi_log "graph seed and verification complete."
