#!/bin/bash
# Grant agents video calls + screen sharing by ADDING the anycompany-pay-video-agent
# security profile (VideoContact.Access) to their existing profiles. Idempotent.
#   AGENTS (default "agent1 agent-live1 agent-live2 agent-ooh1")
#   REMOVE=1  take the profile away again
set -uo pipefail
R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
ENV_NAME="${ENV_NAME:-}"
SFX="${ENV_NAME:+-${ENV_NAME}}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
ALIAS="${ALIAS:-anycompany-pay-${ACCOUNT}${SFX}}"
PROFILE_NAME="anycompany-pay-video-agent${SFX}"

INSTANCE_ID=$(aws connect list-instances --region "$R" \
  --query "InstanceSummaryList[?InstanceAlias=='${ALIAS}'].Id" --output text | tr '\t' '\n' | grep -v '^None$' | head -1)
[[ -n "$INSTANCE_ID" ]] || { echo "ERROR: Connect instance $ALIAS not found in $R" >&2; exit 1; }
VIDEO_ID=$(aws connect list-security-profiles --region "$R" --instance-id "$INSTANCE_ID" \
  --query "SecurityProfileSummaryList[?Name=='${PROFILE_NAME}'].Id" --output text | tr '\t' '\n' | grep -v '^None$' | head -1)
[[ -n "$VIDEO_ID" ]] || { echo "ERROR: security profile $PROFILE_NAME not found (run deploy.sh first)" >&2; exit 1; }

for login in ${AGENTS:-agent1 agent-live1 agent-live2 agent-ooh1}; do
  USER_ID=$(aws connect search-users --region "$R" --instance-id "$INSTANCE_ID" \
    --search-criteria "{\"StringCondition\":{\"FieldName\":\"Username\",\"Value\":\"$login\",\"ComparisonType\":\"EXACT\"}}" \
    --query "Users[0].Id" --output text 2>/dev/null)
  if [[ -z "$USER_ID" || "$USER_ID" == "None" ]]; then echo "  $login: not found, skipped"; continue; fi
  CURRENT=$(aws connect describe-user --region "$R" --instance-id "$INSTANCE_ID" --user-id "$USER_ID" \
    --query "User.SecurityProfileIds" --output text | tr '\t' '\n' | grep -v '^$')
  if [[ "${REMOVE:-0}" == "1" ]]; then
    NEW=$(echo "$CURRENT" | grep -vx "$VIDEO_ID")
  else
    NEW=$(printf '%s\n%s\n' "$CURRENT" "$VIDEO_ID" | sort -u)
  fi
  # shellcheck disable=SC2086
  aws connect update-user-security-profiles --region "$R" --instance-id "$INSTANCE_ID" --user-id "$USER_ID" \
    --security-profile-ids $NEW && echo "  $login: $([[ "${REMOVE:-0}" == "1" ]] && echo removed || echo granted) video + screen sharing"
done
