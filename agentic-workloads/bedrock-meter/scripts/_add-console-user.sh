#!/usr/bin/env bash
# Shared implementation for add-console-admin.sh / add-console-viewer.sh. Not meant to be run
# directly — the wrappers set CONSOLE_TARGET_GROUP (console-admins | console-viewers).
#
# Creates (or reuses) a Cognito user, sets a PERMANENT password, and puts them in the target
# group. The group is the real permission tier the BFF authorizes on:
#   console-admins  → full read + write    console-viewers → read-only
#
# Usage (via a wrapper):  <wrapper>.sh <username> <email> [password]
#   - password omitted → prompted (hidden, with confirmation): the SAFE default for humans.
#   - password as the 3rd arg is for CI/automation ONLY — it lands in shell history and `ps`
#     argv, so never type it interactively; prefer the prompt.
#   - Uses the terminal's AWS credentials/region. POOL_ID may explicitly select a pool.
# Re-running an existing user resets the password and moves them to the requested tier.
# Report success only after verifying target membership and absence from the opposite tier.
set -euo pipefail

STACK="${STACK:-BedrockMeterCoreStack}"
GROUP="${CONSOLE_TARGET_GROUP:?internal: CONSOLE_TARGET_GROUP must be set by the wrapper}"
OTHER_GROUP="${CONSOLE_OTHER_GROUP:?internal: CONSOLE_OTHER_GROUP must be set by the wrapper}"

USERNAME="${1:-}"
EMAIL="${2:-}"
PASSWORD="${3:-}"

if [ -z "$USERNAME" ] || [ -z "$EMAIL" ]; then
  echo "usage: add-console-admin.sh | add-console-viewer.sh <username> <email> [password]" >&2
  exit 2
fi
case "$EMAIL" in
  *@*.*) : ;;
  *) echo "error: '$EMAIL' does not look like an email" >&2; exit 2 ;;
esac

if [ "$GROUP" = "$OTHER_GROUP" ]; then
  echo "error: admin and viewer groups must be different" >&2
  exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required to verify the final group membership" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AWS_PAGER="" AWS_CLI_AUTO_PROMPT=off
# shellcheck source=_aws-context.sh
. "$ROOT/scripts/_aws-context.sh"
load_project_settings "$ROOT"
resolve_aws_target

# Resolve against the live stack; a lookup failure must never select a stale local pool.
if [ -z "${POOL_ID:-}" ]; then
  POOL_ID="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ConsoleUserPoolId'].OutputValue | [0]" \
    --output text)" || {
      echo "error: could not query the user pool. Check the AWS target and stack; no user was changed." >&2
      exit 1
    }
fi
if [ -z "${POOL_ID:-}" ] || [ "$POOL_ID" = "None" ]; then
  echo "error: could not resolve ConsoleUserPoolId (set POOL_ID=... or deploy first)" >&2
  exit 1
fi
if [[ "$POOL_ID" != "$REGION"_* ]]; then
  echo "error: POOL_ID does not match AWS region $REGION; no user was changed." >&2
  exit 1
fi
POOL_ARN="$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" \
  --region "$REGION" --query UserPool.Arn --output text)" || {
    echo "error: could not verify the user pool; no user was changed." >&2
    exit 1
  }
if [[ "$POOL_ARN" != arn:*:cognito-idp:"$REGION":"$ACCOUNT":userpool/"$POOL_ID" ]]; then
  echo "error: user pool ARN does not match the current AWS account and region; no user was changed." >&2
  exit 1
fi

# Prompt for the password if not supplied, so it never lands in shell history or `ps` args.
if [ -z "$PASSWORD" ]; then
  read -rsp "Password for $USERNAME: " PASSWORD; echo
  read -rsp "Confirm password: " CONFIRM; echo
  [ "$PASSWORD" = "$CONFIRM" ] || { echo "error: passwords do not match" >&2; exit 2; }
fi

# Validate locally against the pool policy so a weak password
# fails fast with a clear message instead of a cryptic InvalidPasswordException mid-run.
if [ "${#PASSWORD}" -lt 7 ] || ! printf '%s' "$PASSWORD" | grep -q '[0-9]'; then
  echo "error: password must be >= 7 chars and include at least one digit" >&2
  exit 2
fi

echo "Pool=$POOL_ID  user=$USERNAME  group=$GROUP  region=$REGION"

# Only UsernameExistsException permits continuing with an existing user. AccessDenied,
# throttling, and transport errors must not be misreported as "already exists".
# Pin only this call's error format so JSON/YAML settings do not change exception matching.
if CREATE_RESULT="$(AWS_CLI_ERROR_FORMAT=legacy aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$USERNAME" \
     --user-attributes "Name=email,Value=$EMAIL" Name=email_verified,Value=true \
     --message-action SUPPRESS --region "$REGION" 2>&1)"; then
  echo "  created user"
elif [[ "$CREATE_RESULT" == *"(UsernameExistsException)"* ]]; then
  echo "  user already exists — updating password/group"
else
  printf 'error: could not create user; stopping\n%s\n' "$CREATE_RESULT" >&2
  exit 1
fi
unset CREATE_RESULT

aws cognito-idp admin-set-user-password --user-pool-id "$POOL_ID" --username "$USERNAME" \
  --password "$PASSWORD" --permanent --region "$REGION"
echo "  password set (permanent)"

# Demote/promote is a MOVE, not an add: remove the user from the opposite tier group first, so
# e.g. add-console-viewer.sh on an existing admin actually drops write access. The BFF grants
# isAdmin on console-admins membership, so leaving the old group would silently keep the old
# tier. A failed removal must stop the move, even if adding the target group might succeed.
if ! aws cognito-idp admin-remove-user-from-group --user-pool-id "$POOL_ID" --username "$USERNAME" \
     --group-name "$OTHER_GROUP" --region "$REGION"; then
  echo "error: failed to remove $USERNAME from $OTHER_GROUP; tier change not confirmed" >&2
  exit 1
fi
echo "  removed from $OTHER_GROUP (tier move)"

if ! aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" --username "$USERNAME" \
     --group-name "$GROUP" --region "$REGION"; then
  echo "error: failed to add $USERNAME to $GROUP; group changes may be partially applied" >&2
  exit 1
fi
echo "  added to $GROUP"

# AWS CLI automatically collects all pages before applying this JSON query. Do not use
# --no-paginate or --max-items: the opposite group could otherwise be hidden on a later page.
if ! GROUP_NAMES="$(aws cognito-idp admin-list-groups-for-user \
     --user-pool-id "$POOL_ID" --username "$USERNAME" --region "$REGION" \
     --query 'Groups[].GroupName' --output json --no-cli-pager)"; then
  echo "error: could not verify final groups; group changes may be partially applied" >&2
  exit 1
fi
if ! jq -e --arg target "$GROUP" --arg other "$OTHER_GROUP" \
     'type == "array" and all(.[]; type == "string")
      and (index($target) != null) and (index($other) == null)' \
     <<<"$GROUP_NAMES" >/dev/null; then
  echo "error: final groups must include $GROUP and exclude $OTHER_GROUP; tier change not confirmed" >&2
  exit 1
fi

echo "Done. Verified $USERNAME belongs to $GROUP and not $OTHER_GROUP."
