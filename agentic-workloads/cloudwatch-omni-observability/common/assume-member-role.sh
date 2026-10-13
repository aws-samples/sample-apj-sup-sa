# Source this to run the samples in an AWS Organizations MEMBER account:
#     source common/assume-member-role.sh
# It assumes OMNI_MEMBER_ROLE_ARN (from .env) and exports temporary credentials, so
# the AWS CLI, boto3, the CDK, and docker compose (via up.sh) all act in that account.
# The credentials last about an hour; source the script again to refresh them.
#
# Why not AWS_PROFILE: when AWS_ACCESS_KEY_ID is already set in the environment (for
# example from an SSO or credential helper), the SDKs use it and ignore AWS_PROFILE.

_omni_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
. "$_omni_root/common/load-env.sh"
omni_load_env "$_omni_root/.env"
unset -f omni_load_env

_omni_token_hash() {
  python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

if [ -z "${OMNI_MEMBER_ROLE_ARN:-}" ]; then
  echo "Set OMNI_MEMBER_ROLE_ARN in .env (e.g. arn:aws:iam::<member-acct>:role/OrganizationAccountAccessRole)"
else
  # Select a new base source only when the caller supplied credentials other than
  # this script's current member session. Otherwise keep and refresh the prior source.
  _omni_current_key="${AWS_ACCESS_KEY_ID:-}"
  if [ -n "$_omni_current_key" ] \
      && [ "$_omni_current_key" != "${OMNI_MEMBER_AWS_ACCESS_KEY_ID:-}" ]; then
    OMNI_BASE_AWS_SOURCE=environment
    OMNI_BASE_AWS_PROFILE=""
    OMNI_BASE_AWS_ACCESS_KEY_ID="$_omni_current_key"
    OMNI_BASE_AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}"
    if [[ "$_omni_current_key" = AKIA* ]]; then
      OMNI_BASE_AWS_SESSION_TOKEN=""
    else
      _omni_current_token="${AWS_SESSION_TOKEN:-}"
      if [ -n "$_omni_current_token" ] \
          && [ -n "${OMNI_MEMBER_AWS_SESSION_TOKEN_SHA256:-}" ]; then
        _omni_current_token_hash=$(printf '%s' "$_omni_current_token" | _omni_token_hash)
        if [ "$_omni_current_token_hash" = "$OMNI_MEMBER_AWS_SESSION_TOKEN_SHA256" ]; then
          _omni_current_token=""
        fi
      fi
      OMNI_BASE_AWS_SESSION_TOKEN="${_omni_current_token:-${AWS_SECURITY_TOKEN:-}}"
      unset _omni_current_token _omni_current_token_hash
    fi
    OMNI_BASE_AWS_INITIALIZED=1
  elif [ -n "${AWS_PROFILE:-}" ]; then
    OMNI_BASE_AWS_SOURCE=profile
    OMNI_BASE_AWS_PROFILE="$AWS_PROFILE"
    OMNI_BASE_AWS_ACCESS_KEY_ID=""
    OMNI_BASE_AWS_SECRET_ACCESS_KEY=""
    OMNI_BASE_AWS_SESSION_TOKEN=""
    OMNI_BASE_AWS_INITIALIZED=1
  elif [ -z "${OMNI_BASE_AWS_INITIALIZED:-}" ]; then
    OMNI_BASE_AWS_SOURCE=default-chain
    OMNI_BASE_AWS_PROFILE=""
    OMNI_BASE_AWS_ACCESS_KEY_ID=""
    OMNI_BASE_AWS_SECRET_ACCESS_KEY=""
    OMNI_BASE_AWS_SESSION_TOKEN=""
    OMNI_BASE_AWS_INITIALIZED=1
  fi
  unset _omni_current_key

  export OMNI_BASE_AWS_SOURCE OMNI_BASE_AWS_PROFILE OMNI_BASE_AWS_INITIALIZED
  export -n OMNI_BASE_AWS_ACCESS_KEY_ID OMNI_BASE_AWS_SECRET_ACCESS_KEY \
            OMNI_BASE_AWS_SESSION_TOKEN 2>/dev/null || true

  case "$OMNI_BASE_AWS_SOURCE" in
    environment)
      if [ -z "${OMNI_BASE_AWS_ACCESS_KEY_ID:-}" ]; then
        echo "Base environment credentials are unavailable in this child shell; use the original shell or set AWS_PROFILE"
        unset _omni_root
        return 1 2>/dev/null || exit 1
      fi
      _omni_creds=$(
        unset AWS_PROFILE AWS_SECURITY_TOKEN
        export AWS_ACCESS_KEY_ID="$OMNI_BASE_AWS_ACCESS_KEY_ID"
        export AWS_SECRET_ACCESS_KEY="$OMNI_BASE_AWS_SECRET_ACCESS_KEY"
        export AWS_SESSION_TOKEN="$OMNI_BASE_AWS_SESSION_TOKEN"
        aws sts assume-role --role-arn "$OMNI_MEMBER_ROLE_ARN" --role-session-name omni-samples \
          --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken,Expiration]' --output text
      )
      ;;
    profile)
      _omni_creds=$(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN \
        AWS_PROFILE="$OMNI_BASE_AWS_PROFILE" \
        aws sts assume-role --role-arn "$OMNI_MEMBER_ROLE_ARN" --role-session-name omni-samples \
          --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken,Expiration]' --output text)
      ;;
    default-chain)
      # Resolve the default chain afresh on every source so refreshed SSO or instance
      # credentials are picked up rather than freezing a temporary base session.
      _omni_creds=$(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN -u AWS_PROFILE \
        aws sts assume-role --role-arn "$OMNI_MEMBER_ROLE_ARN" --role-session-name omni-samples \
          --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken,Expiration]' --output text)
      ;;
  esac || {
    echo "Unable to assume the member-account role"
    unset _omni_creds _omni_root
    return 1 2>/dev/null || exit 1
  }

  read -r AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN _omni_exp <<< "$_omni_creds"
  OMNI_MEMBER_AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID"
  OMNI_MEMBER_AWS_SESSION_TOKEN_SHA256=$(printf '%s' "$AWS_SESSION_TOKEN" | _omni_token_hash)
  export OMNI_MEMBER_AWS_ACCESS_KEY_ID OMNI_MEMBER_AWS_SESSION_TOKEN_SHA256
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
  unset AWS_SECURITY_TOKEN
  unset AWS_PROFILE
  echo "Now acting as $(aws sts get-caller-identity --query Arn --output text) until $_omni_exp"
  unset _omni_creds _omni_exp
fi
unset -f _omni_token_hash
unset _omni_root
