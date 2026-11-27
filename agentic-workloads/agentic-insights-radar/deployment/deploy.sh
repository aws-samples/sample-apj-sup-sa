#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Deploy Agentic Insights Radar into the current AWS account and Region, or update an existing deployment.
#
#   ./deployment/deploy.sh admin@example.com [alarm@example.com] [--diff] [--allow-data-loss] [--env-file=FILE]
#
# --env-file=FILE reads the options below from a file of `export NAME=value` lines before anything else,
# so one deployment's settings live in one file (for example deployment/seeds/<name>/deploy.env). The
# file wins over the environment. Only AIR_* options, AWS_PROFILE and AWS_REGION
# may be set; a value runs to the first # (a comment), and one pair of quotes around it is removed.
#
# Needs: AWS credentials for the target account, Python 3.12+, uv, Node.js 20+ and a current AWS CLI v2.
# Optional environment (each maps to a CDK context value; see the implementation guide, Deployment parameters):
#   AIR_PREFIX (default air)                        AIR_RETAIN_DATA (default true)
#   AIR_WEB_SEARCH (default true; falls back to false where the AgentCore web-search connector is unavailable)
#   AIR_LOG_RETENTION_DAYS (default 365)            AIR_AGENT_LOG_RETENTION_DAYS (default 30)
#   AIR_CHAT_MEMORY_DAYS (default 30; 7-365)        AIR_ALARM_EMAIL (default: the first administrator's email)
#   AIR_CHAT_MEMORY_KEY (data-key or aws-owned; a new install uses data-key, an existing deployment keeps its own)
#   AIR_WAF_RATE_LIMIT (default 2000 requests per 5 minutes per IP)
#   AIR_WORKSPACE_RATE_PER_MINUTE (default 600 model-backed API calls a minute across non-admin users)
# Every option above that is a switch takes only true or false; 0, 1, no and yes are refused.
#   AIR_CHAT_MODEL (default sonnet-5-5)             AIR_PRICE_CLASS (default 100)
#   AIR_MONTHLY_BUDGET_USD (default 0: no AWS Budget)
#   AIR_RESERVED_CONCURRENCY (default false)        AIR_BACKUP (default false)
#   AIR_SUPPRESS_INVITATION (default false)         AIR_E2E_SIGN_IN (default false; the e2e suite needs true)
#   AIR_CFN_EXECUTION_POLICIES (CDK bootstrap only)
#   AIR_SKIP_PREFLIGHT=true skips the Region and model checks.
# An update keeps every option the deployment was created with unless you set it again: the stack
# records its options in its DeployOptions output, and this script reads them back.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deployment/common.sh
source "$ROOT/deployment/common.sh"

DIFF=false
MIN_BOOTSTRAP_VERSION=21  # modern bootstrap (no bucket ACLs, SSM-published version)
# Only the flag, never an environment variable: AIR_ALLOW_DATA_LOSS=true left in a shell profile
# silently defeated the one guard there was (P2-051).
ALLOW_DATA_LOSS=false
ENV_FILE=""
POSITIONAL=()
USAGE="usage: $0 admin@example.com [alarm@example.com] [--diff] [--allow-data-loss] [--env-file=FILE]"
for a in "$@"; do
  case "$a" in
    --diff) DIFF=true ;;
    --allow-data-loss) ALLOW_DATA_LOSS=true ;;
    --env-file=?*) ENV_FILE="${a#--env-file=}" ;;
    -*) air_die "unknown option $a ($USAGE)" ;;
    *) POSITIONAL+=("$a") ;;
  esac
done
if [ -n "$ENV_FILE" ]; then
  [ -f "$ENV_FILE" ] || air_die "--env-file: no file $ENV_FILE"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      export\ [A-Z_]*=*)
        kv="${line#export }"; kv="${kv%%#*}"; kv="${kv%"${kv##*[![:space:]]}"}"
        name="${kv%%=*}"; value="${kv#*=}"
        # Only the deployment's own options: a name such as ALLOW_DATA_LOSS or DIFF would otherwise overwrite
        # this script's variables, and the data-loss guard is the --allow-data-loss flag alone (P2-051).
        [[ "$name" =~ ^(AIR_[A-Z0-9_]+|AWS_PROFILE|AWS_REGION|AWS_DEFAULT_REGION)$ ]] \
          || air_die "--env-file: only AIR_* options, AWS_PROFILE and AWS_REGION may be set: $line"
        # One pair of matching quotes around the value is removed. Nothing is expanded ($VAR and backslashes stay as
        # written), and the value ends at its first # even inside quotes.
        if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then value="${BASH_REMATCH[1]}"; fi
        export "$name=$value" ;;
      ''|'#'*) ;;
      *) air_die "--env-file: only 'export NAME=value' lines and comments are read: $line" ;;
    esac
  done < "$ENV_FILE"
fi
# Every -c value is printable ASCII on one line. The CDK CLI reads -c key=value only up to the first line
# break, LF, CR, U+2028 or U+2029 included, while the checks here read the whole value, so anything else
# could let synth and a data-loss guard see different values (scan cycles 68 to 70). JSON values can still
# carry any character as a \u escape. One bash test, no fork, so it fails closed.
plain_value() {  # $1: the option, $2: the value, $3: where it came from
  local LC_ALL=C
  [[ "$2" != *[!\ -~]* ]] \
    || air_die "$1 must be printable ASCII on one line: the value has a line break or another character (from $3)"
}
ADMIN_EMAIL="${POSITIONAL[0]:-${AIR_ADMIN_EMAIL:-}}"
ALARM_EMAIL="${POSITIONAL[1]:-${AIR_ALARM_EMAIL:-}}"
plain_value alarmEmail "$ALARM_EMAIL" "the second argument or AIR_ALARM_EMAIL"
plain_value prefix "${AIR_PREFIX:-air}" "AIR_PREFIX"

air_check_tools

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGION="$(air_region)"
export AWS_REGION="$REGION" CDK_DEFAULT_ACCOUNT="$ACCOUNT" CDK_DEFAULT_REGION="$REGION"
PREFIX="${AIR_PREFIX:-air}"
STACK="$(air_stack "$PREFIX")"
echo "Account $ACCOUNT, Region $REGION, stack $STACK"

# ---- Stack state ------------------------------------------------------------------------------------
STATUS="$(air_stack_status "$STACK")"
case "$STATUS" in
  NONE|ROLLBACK_COMPLETE|REVIEW_IN_PROGRESS) EXISTING=false ;;  # a failed or never-run first install is fresh
  *_IN_PROGRESS)
    air_die "stack $STACK is $STATUS: wait for it to finish (aws cloudformation describe-stacks --stack-name $STACK), then run this again" ;;
  UPDATE_ROLLBACK_FAILED)
    air_die "stack $STACK is UPDATE_ROLLBACK_FAILED: run 'aws cloudformation continue-update-rollback --stack-name $STACK', wait for UPDATE_ROLLBACK_COMPLETE, then run this again" ;;
  ROLLBACK_FAILED|DELETE_FAILED)
    air_die "stack $STACK is $STATUS: see 'aws cloudformation describe-stack-events --stack-name $STACK', remove it with ./deployment/destroy.sh --stack $STACK, then deploy again" ;;
  *) EXISTING=true ;;
esac
# A first install needs the administrator's email; an upgrade does not, because the stack records
# the first administrator and this script reads it back below (P2-028: the guide's upgrade command
# is "./deployment/deploy.sh --diff", with no email).
[[ -n "$ADMIN_EMAIL" || "$EXISTING" == true ]] \
  || air_die "usage: $0 admin@example.com [alarm@example.com] [--diff] [--allow-data-loss] (the email is needed only for a first install)"

# ---- Options: environment first, then what the deployment was created with, then the defaults ---------
# context key | environment variable | default
OPTIONS=(
  "retainData|AIR_RETAIN_DATA|true"
  "webSearch|AIR_WEB_SEARCH|true"
  "logRetentionDays|AIR_LOG_RETENTION_DAYS|365"
  "agentLogRetentionDays|AIR_AGENT_LOG_RETENTION_DAYS|30"
  "chatMemoryDays|AIR_CHAT_MEMORY_DAYS|30"
  "chatModel|AIR_CHAT_MODEL|sonnet-5-5"
  "chatMemoryKey|AIR_CHAT_MEMORY_KEY|data-key"
  "priceClass|AIR_PRICE_CLASS|100"
  "wafRateLimit|AIR_WAF_RATE_LIMIT|2000"
  "guardrailGeneration|AIR_GUARDRAIL_GENERATION|1"
  "slackSecretGeneration|AIR_SLACK_SECRET_GENERATION|0"
  "unsubscribeKeyGeneration|AIR_UNSUBSCRIBE_KEY_GENERATION|0"
  "workspaceRatePerMinute|AIR_WORKSPACE_RATE_PER_MINUTE|600"
  "monthlyBudgetUsd|AIR_MONTHLY_BUDGET_USD|0"
  "reservedConcurrency|AIR_RESERVED_CONCURRENCY|false"
  "backupPlan|AIR_BACKUP|false"
  "suppressInvitation|AIR_SUPPRESS_INVITATION|false"
  "seed|AIR_SEED|"
  "domainName|AIR_DOMAIN_NAME|"
  "parentZoneId|AIR_PARENT_ZONE_ID|"
  "redirectDomains|AIR_REDIRECT_DOMAINS|"
  "certificateArn|AIR_CERTIFICATE_ARN|"
  "authMode|AIR_AUTH_MODE|cognito"
  "oidcIssuer|AIR_OIDC_ISSUER|"
  "oidcClientId|AIR_OIDC_CLIENT_ID|"
  "oidcClientSecretArn|AIR_OIDC_CLIENT_SECRET_ARN|"
  "oidcProviderName|AIR_OIDC_PROVIDER_NAME|SSO"
  "oidcTrustEmail|AIR_OIDC_TRUST_EMAIL|false"
  "oidcClientSecretVersion|AIR_OIDC_CLIENT_SECRET_VERSION|"
  "oidcSignOutPrincipal|AIR_OIDC_SIGN_OUT_PRINCIPAL|"
  "adminEmails|AIR_ADMIN_EMAILS|"
  "mcpIssuer|AIR_MCP_ISSUER|"
  "mcpAudience|AIR_MCP_AUDIENCE|"
  "mcpProviderArn|AIR_MCP_PROVIDER_ARN|"
  "digestDelivery|AIR_DIGEST_DELIVERY|"
)
DEPLOYED=""
ADMIN_SOURCE="the first argument or AIR_ADMIN_EMAIL"  # set here, so nothing in the environment chooses it (scan cycle 73)
if [[ "$EXISTING" == true ]]; then
  OUT="$(aws cloudformation describe-stacks --stack-name "$STACK" \
         --query "Stacks[0].Outputs[?OutputKey=='DeployOptions' || OutputKey=='FirstAdmin'].[OutputKey,OutputValue]" \
         --output text)" || air_die "could not read the outputs of $STACK; re-run when the AWS call succeeds"
  # The first administrator is created once. Changing the email on an update would replace that user
  # (deleting the original admin), so an update keeps it; invite more admins from the app instead.
  FIRST_ADMIN="$(awk -F'\t' '$1=="FirstAdmin"{print $2}' <<<"$OUT")"
  if [[ -n "$FIRST_ADMIN" && "$FIRST_ADMIN" != None ]]; then
    if [[ -n "$ADMIN_EMAIL" \
          && "$(tr '[:upper:]' '[:lower:]' <<<"$FIRST_ADMIN")" != "$(tr '[:upper:]' '[:lower:]' <<<"$ADMIN_EMAIL")" ]]; then
      echo "Keeping the first administrator $FIRST_ADMIN (invite $ADMIN_EMAIL from Administration instead)."
    fi
    ADMIN_EMAIL="$FIRST_ADMIN"
    ADMIN_SOURCE="the FirstAdmin output of $STACK: this release takes printable-ASCII addresses only, and the first administrator cannot change, so it cannot update this stack"
  fi
  [[ -n "$ADMIN_EMAIL" ]] \
    || air_die "$STACK has no FirstAdmin output (it predates it): pass the first administrator's email as the first argument"
  DEPLOYED="$(awk -F'\t' '$1=="DeployOptions"{print $2}' <<<"$OUT" | python3 -I -c '
import json, sys
text = sys.stdin.read().strip()
for key, value in (json.loads(text) if text else {}).items():
    print(f"{key}={value}")
')" || air_die "the DeployOptions output of $STACK is not valid JSON"
fi
deployed() { awk -F= -v k="$1" '$1==k{sub(/^[^=]*=/, ""); print; exit}' <<<"$DEPLOYED"; }
lower() { tr '[:upper:]' '[:lower:]' <<<"$1"; }

# On an update the address usually is the stack's FirstAdmin, which replaced the first argument above.
# Nothing can change that one: a new address would replace the first administrator, so the message says
# this release cannot update such a stack rather than point at a way round (scan cycles 71 and 72).
plain_value adminEmail "$ADMIN_EMAIL" "$ADMIN_SOURCE"
ARGS=(-c "adminEmail=$ADMIN_EMAIL" -c "prefix=$PREFIX")
SUMMARY=()
WEB_SEARCH_FROM_ENV=false
# Declared so shellcheck sees them; every one is set by the option loop below (printf -v).
OPT_retainData=""; OPT_webSearch=""; OPT_backupPlan=""; OPT_suppressInvitation=""; OPT_chatModel=""
OPT_chatMemoryKey=""; OPT_chatMemoryDays=""; OPT_authMode=""; OPT_oidcProviderName=""
# shellcheck disable=SC2034  # read only through eval in the federated identity check
OPT_oidcIssuer=""
OPT_redirectDomains=""
# A deployment created before chatMemoryKey was recorded has a chat memory on an AWS-owned key. Its
# key is create-only, so it keeps that key unless the operator asks otherwise (CR2-01).
WAS_MEMORY_KEY=""
if [[ "$EXISTING" == true ]]; then
  WAS_MEMORY_KEY="$(deployed chatMemoryKey)"
  WAS_MEMORY_KEY="${WAS_MEMORY_KEY:-aws-owned}"
fi

for spec in "${OPTIONS[@]}"; do
  IFS='|' read -r key var default <<<"$spec"
  was="$(deployed "$key")"
  if [[ -z "$default" && "$(lower "${!var:-}")" == none ]]; then
    # An optional setting is carried over from the deployed stack when its variable is unset, so unsetting
    # it cannot remove it: the word none does (AIR_SEED=none, AIR_OIDC_SIGN_OUT_PRINCIPAL=none).
    value=""
    [[ -n "$was" ]] && echo "Clearing $key (was $was; $var=none)."
  elif [[ -n "${!var:-}" ]]; then
    value="${!var}"
    [[ "$key" == webSearch ]] && WEB_SEARCH_FROM_ENV=true
    if [[ -n "$was" && "$(lower "$was")" != "$(lower "$value")" ]]; then
      echo "Changing $key from $was to $value ($var)."
    fi
  elif [[ -n "$was" ]]; then
    value="$was"
  elif [[ "$key" == chatMemoryKey && -n "$WAS_MEMORY_KEY" ]]; then
    value="$WAS_MEMORY_KEY"
  else
    value="$default"
  fi
  # Switches take only true or false, in any case: 0, no and yes used to mean different things for
  # different options, so AIR_WEB_SEARCH=0 left web search on while AIR_BACKUP=0 turned it off (P2-050).
  case "$key" in
    retainData|webSearch|reservedConcurrency|backupPlan|suppressInvitation|oidcTrustEmail)
      value="$(lower "$value")"
      [[ "$value" == true || "$value" == false ]] \
        || air_die "$key must be true or false, not '${!var:-$value}' (set $var=true or $var=false)" ;;
    authMode)
      value="$(lower "$value")"
      [[ "$value" == cognito || "$value" == federated ]] \
        || air_die "authMode must be cognito or federated, not '${!var:-$value}'" ;;
    chatMemoryKey)
      value="$(lower "$value")"
      [[ "$value" == data-key || "$value" == aws-owned ]] \
        || air_die "chatMemoryKey must be data-key or aws-owned, not '${!var:-$value}' (set $var=data-key or $var=aws-owned)" ;;
  esac
  if [[ -n "${!var:-}" ]]; then
    plain_value "$key" "$value" "$var"
  else
    plain_value "$key" "$value" "the DeployOptions output of $STACK; set $var to replace it"
  fi
  printf -v "OPT_$key" "%s" "$value"  # bash 3.2 has no associative arrays
  ARGS+=(-c "$key=$value")
  SUMMARY+=("$key=$value")
done
# The sign-in mode decides whether the pool holds password accounts (the first administrator among them)
# or only accounts made by the identity provider: switching it on a live deployment would delete the
# first administrator or leave password accounts nobody can use, so it is fixed at creation.
if [[ "$EXISTING" == true ]]; then
  WAS_AUTH="$(lower "$(deployed authMode)")"
  WAS_AUTH="${WAS_AUTH:-cognito}"
  [[ "$WAS_AUTH" == "$OPT_authMode" ]] \
    || air_die "authMode cannot be changed on an existing deployment ($STACK uses $WAS_AUTH); deploy a new stack instead."
  # Federated usernames are <provider name>_<subject at the provider>: a new name or issuer replaces the
  # identity provider and makes every person a new account, losing their roles and what they own.
  if [[ "$WAS_AUTH" == federated ]]; then
    for key in oidcProviderName oidcIssuer; do
      was="$(deployed "$key")"
      now="$(eval "printf '%s' \"\${OPT_$key}\"")"
      [[ -z "$was" || "$was" == "$now" ]] \
        || air_die "$key cannot be changed on an existing federated deployment ($was): every user would become a new account. Deploy a new stack instead."
    done
  fi
  # The stack's original DNS records (zone, delegation, alias) belong to the first redirectDomains entry, the
  # name the stack had before its rename. Any other first entry would move them to another name while that
  # name's own NS record still exists, and the update would fail on the duplicate record (code review r13).
  # The first non-blank entry, as config.py reads the list (it skips empty entries).
  first() { tr ',' '\n' <<<"$1" | tr -d '[:blank:]' | awk 'NF{print tolower($0); exit}'; }
  WAS_REDIRECTS="$(deployed redirectDomains)"
  if [[ -n "$WAS_REDIRECTS" ]]; then
    [[ "$(first "$OPT_redirectDomains")" == "$(first "$WAS_REDIRECTS")" ]] \
      || air_die "redirectDomains must keep $(first "$WAS_REDIRECTS") first ($STACK's original DNS records are under that name); add other names after it."
  elif [[ -n "$OPT_redirectDomains" ]]; then
    [[ "$(first "$OPT_redirectDomains")" == "$(lower "$(deployed domainName)")" ]] \
      || air_die "redirectDomains must start with the name $STACK serves now ($(deployed domainName)): a rename sets AIR_DOMAIN_NAME to the new name and AIR_REDIRECT_DOMAINS to the old one."
  fi
fi
# SRP sign-in on the MCP app client is for the e2e suite only and reopens password guessing against
# InitiateAuth, so it is never carried over from the deployed options: each deploy asks for it again.
E2E_SIGN_IN="$(lower "${AIR_E2E_SIGN_IN:-false}")"
[[ "$E2E_SIGN_IN" == true || "$E2E_SIGN_IN" == false ]] \
  || air_die "e2eSignIn must be true or false, not '${AIR_E2E_SIGN_IN}' (AIR_E2E_SIGN_IN=1 used to open SRP sign-in with no warning)"
ARGS+=(-c "e2eSignIn=$E2E_SIGN_IN")
SUMMARY+=("e2eSignIn=$E2E_SIGN_IN")
if [[ "$E2E_SIGN_IN" == true ]]; then
  echo "WARNING: AIR_E2E_SIGN_IN=true turns on SRP password sign-in for the MCP app client. Use it only on a"
  echo "         test stack; the next deploy without it turns it off again."
elif [[ "$(lower "$(deployed e2eSignIn)")" == true ]]; then
  echo "Turning off e2eSignIn (SRP sign-in for the e2e suite): set AIR_E2E_SIGN_IN=true on each deploy that needs it."
fi
# Alarm email: the argument or AIR_ALARM_EMAIL, else the deployed one, else the stack uses the admin email.
# AIR_ALARM_EMAIL=admin goes back to the first administrator: a recorded address was otherwise kept for
# good, so a departed recipient could be replaced but never removed (P3-060).
RESET_ALARM=false
if [[ "$(lower "$ALARM_EMAIL")" == admin ]]; then
  ALARM_EMAIL=""
  RESET_ALARM=true
fi
WAS_ALARM="$(deployed alarmEmail)"
[[ -n "$ALARM_EMAIL" || -z "$WAS_ALARM" || "$RESET_ALARM" == true ]] || ALARM_EMAIL="$WAS_ALARM"
if [[ -n "$ALARM_EMAIL" ]]; then
  plain_value alarmEmail "$ALARM_EMAIL" "the deployed alarmEmail of $STACK; pass another, or AIR_ALARM_EMAIL=admin"
  ARGS+=(-c "alarmEmail=$ALARM_EMAIL")
  SUMMARY+=("alarmEmail=$ALARM_EMAIL")
else
  SUMMARY+=("alarmEmail=$ADMIN_EMAIL (the first administrator)")
fi

# How synth will read a digestDelivery value: on or off. It asks DigestDelivery.parse itself (config.py
# needs only the standard library), and anything it cannot read stops the deploy with the parser's own
# message: this python3 -I and synth's interpreter can differ (version, PYTHON* variables), so a value
# refused here might still deploy, and skipping the guards for it would fail open (scan cycles 67 to 71).
digest_state() {  # $1: the value
  python3 -I -c '
import sys
sys.path.insert(0, sys.argv[2])
from insights_radar_infra.config import DigestDelivery
try:
    print("on" if DigestDelivery.parse(sys.argv[1] or None).enabled else "off")
except (ValueError, RecursionError) as error:
    text = str(error) or type(error).__name__
    print(f"error: {text}" if text.startswith("digestDelivery") else f"error: digestDelivery: {text}", file=sys.stderr)
    sys.exit(3)
' "$1" "$ROOT/infra" || {
    if (( $? == 3 )); then  # refused: the parser said why; say where an unset value came from (scan cycle 73)
      [[ -n "${AIR_DIGEST_DELIVERY:-}" ]] \
        || echo "error: that digestDelivery is the DeployOptions output of $STACK; set AIR_DIGEST_DELIVERY to replace it" >&2
      exit 1
    fi
    air_die "could not run the digestDelivery check in infra/insights_radar_infra/config.py (python3 -I above says why)"
  }
}
# Changes that delete data, or make the next destroy delete it, must be asked for explicitly.
guard_data_loss() {  # $1: what would be lost, in one sentence
  [[ "$ALLOW_DATA_LOSS" == true ]] \
    || air_die "on the existing deployment $STACK, $1. Pass --allow-data-loss if that is what you want."
  echo "WARNING: --allow-data-loss: $1."
}
if [[ "$EXISTING" == true ]]; then
  if [[ "$(lower "$(deployed retainData)")" != false && "${OPT_retainData}" == false ]]; then
    guard_data_loss "AIR_RETAIN_DATA=false would turn off deletion protection and let the next destroy delete all data"
  fi
  # Lowering a retention period deletes log data older than the new period at once, and the access-log
  # bucket's lifecycle rule expires objects with it (P2-051).
  for key in logRetentionDays agentLogRetentionDays; do
    was="$(deployed "$key")"
    now="$(eval "printf '%s' \"\$OPT_$key\"")"
    if [[ "$was" =~ ^[0-9]+$ && "$now" =~ ^[0-9]+$ ]] && (( now < was )); then
      guard_data_loss "$key from $was to $now would delete log data older than $now days, including the WAF and API access logs an investigation needs"
    fi
  done
  # Shortening chat retention expires every user's older chat history at once (CR2-14).
  was="$(deployed chatMemoryDays)"
  now="${OPT_chatMemoryDays}"
  if [[ "$was" =~ ^[0-9]+$ && "$now" =~ ^[0-9]+$ ]] && (( now < was )); then
    guard_data_loss "chatMemoryDays from $was to $now would delete every user's chat history older than $now days"
  fi
  # The chat memory's key is create-only: changing it replaces the memory, deleting every user's
  # chat history (CR2-01). Keeping it is the default, so this needs both the variable and the flag.
  if [[ "${OPT_chatMemoryKey}" != "$WAS_MEMORY_KEY" ]]; then
    guard_data_loss "chatMemoryKey from $WAS_MEMORY_KEY to ${OPT_chatMemoryKey} would replace the chat memory and delete every user's chat history"
  elif [[ "$WAS_MEMORY_KEY" == aws-owned ]]; then
    echo "Chat history stays on an AWS-owned key (chatMemoryKey=aws-owned). AIR_CHAT_MEMORY_KEY=data-key with"
    echo "--allow-data-loss moves it to the deployment's data key and deletes every user's chat history."
  fi
  # Digest delivery recorded as on means both digest secrets exist (options_json records it only then).
  # Turning it fully off retires both: the Slack value put there and the unsubscribe key, so every
  # unsubscribe link already sent stops working, and turning it on again makes new ones (scan cycle 65).
  # While it stays on, a secret's generation is part of its construct ID, so a lower one makes that secret
  # new again: a new unsubscribe key voids every link already sent, and a new Slack secret is an empty
  # placeholder (scan cycles 62 to 64). Unset, a generation is carried over like every other option.
  if [[ -n "$(deployed digestDelivery)" ]]; then
    state="$(digest_state "${OPT_digestDelivery:-}")" || exit 1  # the check printed why
    case "$state" in
      off)
        guard_data_loss "turning digest delivery off would retire both digest secrets: the Slack value put there, and the unsubscribe key, so every unsubscribe link already sent stops working"
        ;;
      on)
        for key in slackSecretGeneration unsubscribeKeyGeneration; do
          was="$(deployed "$key")"
          now="$(eval "printf '%s' \"\$OPT_$key\"")"
          if [[ "$was" =~ ^[0-9]+$ && "$now" =~ ^[0-9]+$ ]] && (( now < was )); then
            guard_data_loss "$key from $was to $now would replace that secret (a new unsubscribe key voids every link already sent; a new Slack secret is an empty placeholder)"
          fi
        done
        ;;
      *)
        air_die "the digestDelivery check printed '$state', not on or off" ;;
    esac
  fi
  # Turning the backup plan off leaves the vault and its recovery points orphaned: nothing deletes
  # them and nothing adds to them, and the data key they need is still scheduled for deletion by a
  # later --purge unless --delete-backups is used.
  if [[ "$(lower "$(deployed backupPlan)")" == true && "${OPT_backupPlan}" == false ]]; then
    guard_data_loss "AIR_BACKUP=false would stop daily backups and orphan the existing vault and its recovery points"
  fi
  # MessageAction on the first-admin user is create-only, so changing suppressInvitation replaces
  # that user: the replacement collides with the existing username and the update rolls back (P2-085).
  WAS_SUPPRESS="$(lower "$(deployed suppressInvitation)")"
  if [[ -n "$WAS_SUPPRESS" && "$WAS_SUPPRESS" != "${OPT_suppressInvitation}" ]]; then
    air_die "suppressInvitation cannot be changed on an existing deployment (it is create-only on the first administrator's user, so every deploy would fail at FirstAdmin). Leave AIR_SUPPRESS_INVITATION=$WAS_SUPPRESS, or deploy a new stack."
  fi
fi

# digestDelivery.sesHostedZoneId must be the zone the world asks for sesDomain: the closest one. If the zone
# holds an NS delegation for sesDomain or a name above it, the DKIM and DMARC records written there are
# never seen, and the identity stays unverified with nothing failing (seen on a deployment, 16 Nov 2026).
ses_zone() {  # prints "<sesDomain> <sesHostedZoneId> dmarc|-" when both are set; a refused value stops the deploy
  python3 -I -c '
import sys
sys.path.insert(0, sys.argv[2])
from insights_radar_infra.config import DigestDelivery
try:
    d = DigestDelivery.parse(sys.argv[1] or None)
except (ValueError, RecursionError) as error:
    text = str(error) or type(error).__name__
    print(f"error: {text}" if text.startswith("digestDelivery") else f"error: digestDelivery: {text}", file=sys.stderr)
    sys.exit(3)
if d.ses_domain and d.ses_hosted_zone_id:
    print(d.ses_domain.lower().rstrip("."), d.ses_hosted_zone_id, "dmarc" if d.ses_dmarc else "-")
' "$1" "$ROOT/infra" || {
    # A value the parser refuses stops here too, whichever interpreter synth uses (scan cycle 2, as cycle 71).
    (( $? == 3 )) && exit 1
    air_die "could not run the digestDelivery zone check in infra/insights_radar_infra/config.py (python3 -I above says why)"
  }
}
SES_ZONE="$(ses_zone "${OPT_digestDelivery:-}")" || exit 1
# One NS record set of a zone, by name: a single targeted call, not a listing of the whole zone (scan cycle 1).
ns_names() {  # $1: zone id, $2: record name; prints the name back when the zone has an NS record set for it
  aws route53 list-resource-record-sets --hosted-zone-id "$1" --start-record-name "$2" --start-record-type NS \
    --max-items 1 --query "ResourceRecordSets[?Type=='NS'].Name" --output text
}
if [[ -n "$SES_ZONE" ]]; then
  read -r SES_DOMAIN SES_ZONE_ID SES_DMARC <<<"$SES_ZONE"
  zone_error="cannot read the hosted zone $SES_ZONE_ID (digestDelivery.sesHostedZoneId; deploy.sh needs route53:GetHostedZone and route53:ListResourceRecordSets)"
  ZONE_INFO="$(aws route53 get-hosted-zone --id "$SES_ZONE_ID" \
               --query '[HostedZone.Name, HostedZone.Config.PrivateZone]' --output text)" || air_die "$zone_error"
  ZONE_NAME="${ZONE_INFO%%$'\t'*}"  # name<TAB>private; split by hand so an empty name stays empty
  ZONE_PRIVATE="${ZONE_INFO##*$'\t'}"
  ZONE_NAME="$(lower "${ZONE_NAME%.}")"
  [[ -n "$ZONE_NAME" && ( "$SES_DOMAIN" == "$ZONE_NAME" || "$SES_DOMAIN" == *".$ZONE_NAME" ) ]] \
    || air_die "digestDelivery.sesHostedZoneId $SES_ZONE_ID is the zone '$ZONE_NAME', which does not hold sesDomain $SES_DOMAIN"
  [[ "$(lower "$ZONE_PRIVATE")" != true ]] \
    || air_die "digestDelivery.sesHostedZoneId $SES_ZONE_ID ($ZONE_NAME) is a private hosted zone: SES reads public DNS, so the DKIM and DMARC records written there would never be seen. Use the public zone."
  # A delegation of sesDomain, a name between it and the zone, or the record names SES uses under it, hides the
  # records CDK writes here (16 Nov 2026; scan cycle 1).
  # _dmarc only when the stack writes a DMARC record: a policy hosted elsewhere is why sesDmarc exists (scan cycle 3).
  below=("_domainkey.$SES_DOMAIN")
  [[ "$SES_DMARC" == dmarc ]] && below+=("_dmarc.$SES_DOMAIN")
  for name in "${below[@]}"; do
    found="$(ns_names "$SES_ZONE_ID" "$name")" || air_die "$zone_error"
    [[ "$(lower "${found%.}")" != "$name" ]] \
      || air_die "digestDelivery.sesHostedZoneId $SES_ZONE_ID ($ZONE_NAME) delegates $name to another hosted zone, so the records the stack writes for it in $ZONE_NAME would never be seen. Remove that NS delegation$([[ "$name" == _dmarc.* ]] && echo ', or leave sesDmarc off and keep the DMARC policy where it is')."
  done
  name="$SES_DOMAIN"
  while [[ "$name" != "$ZONE_NAME" ]]; do
    found="$(ns_names "$SES_ZONE_ID" "$name")" || air_die "$zone_error"
    [[ "$(lower "${found%.}")" != "$name" ]] \
      || air_die "digestDelivery.sesHostedZoneId $SES_ZONE_ID ($ZONE_NAME) delegates $name to another hosted zone, so the DKIM and DMARC records for $SES_DOMAIN written there would never be seen. Set sesHostedZoneId to the hosted zone for $name."
    name="${name#*.}"
  done
  # Whether the zone is the one its parent delegates to (and not a second zone with the same name) only public
  # DNS can say: the delegation may run through zones in other accounts, so it is the operator's to confirm and
  # the guide says so (scan cycle 2 dropped a Route 53 check that refused working setups).
fi

# An existing deployment from before DeployOptions without the web-search target keeps it off.
if [[ "$EXISTING" == true && -z "$(deployed webSearch)" && "$WEB_SEARCH_FROM_ENV" == false ]]; then
  # A failed lookup stops the deploy: read as "no target", it would delete a working web search.
  TARGETS="$(aws cloudformation list-stack-resources --stack-name "$STACK" \
             --query "StackResourceSummaries[?ResourceType=='AWS::BedrockAgentCore::GatewayTarget'].LogicalResourceId" \
             --output text)" || air_die "cannot list the resources of $STACK"
  if [[ -z "$TARGETS" ]]; then
    echo "This deployment has no web search (the connector was unavailable); keeping it off. AIR_WEB_SEARCH=true retries."
    ARGS=("${ARGS[@]/webSearch=true/webSearch=false}")
    SUMMARY=("${SUMMARY[@]/webSearch=true/webSearch=false}")
  fi
fi

# ---- Web-search gateways a failed delete left behind (r3 P3-002) ----------------------------------------
# When AgentCore cannot delete a gateway, CloudFormation gives up on it and forgets it, but the gateway
# stays, holding its name. Before the role's key grant was ordered before it (P3-001), every rollback
# did that. This deployment's gateways are named <prefix>-web (1.0.0), <prefix>-web-k (an earlier
# build of this release) and <prefix>-web-<last group of the stack id> (now). A leftover with the
# name this deploy creates fails it late with "A gateway with name ... already exists", so that one
# stops the deploy here; any other leftover is reported. Both need the steps in the guide, because
# the gateway's role is usually gone and AgentCore needs it to delete the gateway.
if GATEWAYS="$(aws bedrock-agentcore-control list-gateways --query 'items[].[gatewayId,name]' \
               --output text 2>&1)"; then
  OWNED=""
  WANT=""
  if [[ "$EXISTING" == true ]]; then
    OWNED="$(aws cloudformation list-stack-resources --stack-name "$STACK" \
             --query "StackResourceSummaries[?ResourceType=='AWS::BedrockAgentCore::Gateway'].PhysicalResourceId" \
             --output text)" || air_die "cannot list the resources of $STACK"
    STACK_ID="$(aws cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].StackId' \
                --output text)" || air_die "cannot read the stack id of $STACK"
    if [[ "$STACK_ID" == arn:* ]]; then WANT="$PREFIX-web-${STACK_ID##*-}"; fi
  fi
  ORPHANS=()
  BLOCKING=""
  while read -r gw_id gw_name; do
    [[ -n "$gw_name" && "$gw_name" =~ ^${PREFIX}-web(-k|-[0-9a-f]{12})?$ ]] || continue
    [[ " ${OWNED//$'\t'/ } " == *" $gw_id "* ]] && continue
    ORPHANS+=("$gw_id ($gw_name)")
    if [[ -n "$WANT" && "$gw_name" == "$WANT" ]]; then BLOCKING="$gw_id"; fi
  done <<< "$GATEWAYS"
  if [[ ${#ORPHANS[@]} -gt 0 ]]; then
    echo "AgentCore gateways of this deployment that the stack no longer owns (left by a failed delete):"
    printf '  %s\n' "${ORPHANS[@]}"
    echo "Remove each with the steps in the implementation guide, Troubleshooting: \"Clearing an orphaned"
    echo "web-search gateway\" (the gateway's role must exist again before AgentCore can delete it)."
  fi
  if [[ -n "$BLOCKING" ]]; then
    air_die "gateway $BLOCKING has the name this deploy creates ($WANT), so the update would fail and roll back. Clear it first (see above)."
  fi
else
  echo "warning: cannot list AgentCore gateways in $REGION, so a gateway left by a failed delete is not detected:"
  echo "         ${GATEWAYS%%$'\n'*}"
fi

# ---- A backup vault kept from an earlier creation of this stack (r3 P3-010) -------------------------------
# The vault's name is derived from the stack name, so it is the same for every creation of the stack,
# and the vault is kept when backups are turned off or the stack is deleted (its recovery points are
# the reason it exists). Turning backups on again, or a new install of the prefix with backups, then
# fails late with AlreadyExists and rolls the whole stack back.
if [[ "${OPT_backupPlan}" == true ]]; then
  HAS_VAULT=""
  if [[ "$EXISTING" == true ]]; then
    HAS_VAULT="$(aws cloudformation list-stack-resources --stack-name "$STACK" \
                 --query "StackResourceSummaries[?ResourceType=='AWS::Backup::BackupVault'].PhysicalResourceId" \
                 --output text)" || air_die "cannot list the resources of $STACK"
    if [[ "$HAS_VAULT" == None ]]; then HAS_VAULT=""; fi
  fi
  if [[ -z "$HAS_VAULT" ]]; then
    vault_base="${STACK//[^A-Za-z0-9]/}BackupsVault"
    VAULTS="$(aws backup list-backup-vaults \
              --query "BackupVaultList[?starts_with(BackupVaultName, '$vault_base')].BackupVaultName" \
              --output text)" || air_die "cannot list the AWS Backup vaults in $REGION"
    for vault in $VAULTS; do
      if [[ "$vault" =~ ^${vault_base}[0-9A-F]{8}$ ]]; then
        air_die "backup vault $vault is left from an earlier deployment of $STACK (kept with its recovery points), and backupPlan=true would create it again, fail with AlreadyExists and roll back. Deploy with AIR_BACKUP=false, or, once you no longer need its recovery points, delete them (aws backup list-recovery-points-by-backup-vault --backup-vault-name $vault, then aws backup delete-recovery-point for each) and the vault (aws backup delete-backup-vault --backup-vault-name $vault)."
      fi
    done
  fi
fi

echo "Options:"
printf '  %s\n' "${SUMMARY[@]}"
if [[ "${OPT_retainData}" == false ]]; then
  echo "Data protection: OFF (retainData=false): deleting the stack deletes every table, bucket and document."
else
  echo "Data protection: on (retainData=true): tables and the user pool have deletion protection and are kept on delete."
fi

# ---- Preflight: services and models in this Region ---------------------------------------------------
if [[ "${AIR_SKIP_PREFLIGHT:-false}" != true ]]; then
  for check in "bedrock-agentcore-control list-agent-runtimes --max-results 1|Amazon Bedrock AgentCore" \
               "s3vectors list-vector-buckets --max-results 1|Amazon S3 Vectors"; do
    IFS='|' read -r cmd service <<<"$check"
    # shellcheck disable=SC2086  # the command is several words on purpose
    if ! ERR="$(aws $cmd 2>&1 >/dev/null)"; then
      if [[ "$ERR" == *"Could not connect to the endpoint"* || "$ERR" == *"UnknownEndpoint"* ]]; then
        air_die "$service is not available in $REGION; deploy to a Region from the README's Supported Regions"
      fi
      echo "warning: could not check $service in $REGION: ${ERR:0:200}"
    fi
  done
  # The default module model and the chosen chat model, which may be another one (P2-052).
  for key in sonnet-5-5 "${OPT_chatModel}"; do
    profile="$(air_model_profile "$key")"
    if ! aws bedrock get-inference-profile --inference-profile-identifier "$profile" >/dev/null 2>&1; then
      echo "warning: the $profile inference profile ($key) is not available to this account in $REGION;"
      echo "         runs and chat on it will fail until it is (see README: Amazon Bedrock model access)."
    fi
  done
  if ! aws bedrock get-foundation-model --model-identifier amazon.titan-embed-text-v2:0 >/dev/null 2>&1; then
    echo "warning: Amazon Titan Text Embeddings V2 is not available in $REGION; search and the knowledge base need it."
  fi
  # No API lists the AgentCore built-in tools an account may use, so they are named rather than
  # checked: a Region without one of them fails at the first run or chat question, not here.
  echo "This deployment also needs these Amazon Bedrock AgentCore features in $REGION: Runtime, Memory,"
  echo "Browser and Code Interpreter (built-in tools), and Gateway with the web-search connector"
  echo "(AIR_WEB_SEARCH=false deploys without that one). See README: AWS account requirements."
fi

# ---- Fresh install: a deployment deleted with retainData=true leaves fixed names behind ----------------
# Every fixed name is checked here, because CloudFormation finds a collision only well into the create
# and then rolls the whole stack back (P2-053).
if [[ "$EXISTING" == false ]]; then
  bucket="$PREFIX-assets-$ACCOUNT-$REGION"
  if HEAD="$(aws s3api head-bucket --bucket "$bucket" 2>&1)"; then
    air_die "bucket $bucket is left from an earlier deployment (kept data). Delete it, or deploy with another AIR_PREFIX."
  elif [[ "$HEAD" == *403* || "$HEAD" == *Forbidden* ]]; then
    # S3 bucket names are global: another account already owns this one.
    air_die "bucket name $bucket is taken by another AWS account. Deploy with another AIR_PREFIX."
  fi
  # The WAF request log keeps its fixed name on delete, and it always lives in us-east-1 (where
  # CloudFront web ACLs live), named after the Region this deployment serves.
  waf_logs="aws-waf-logs-$PREFIX-$REGION"
  FOUND="$(aws logs describe-log-groups --region us-east-1 --log-group-name-prefix "$waf_logs" \
           --query "logGroups[?logGroupName=='$waf_logs'].logGroupName" --output text 2>/dev/null || true)"
  if [[ -n "$FOUND" && "$FOUND" != None ]]; then
    air_die "log group $waf_logs is left in us-east-1 from an earlier deployment (kept data). Delete it (aws logs delete-log-group --region us-east-1 --log-group-name $waf_logs), or deploy with another AIR_PREFIX."
  fi
fi

# ---- Build ---------------------------------------------------------------------------------------------
python3 "$ROOT/deployment/build.py"
[[ -f "$ROOT/build/site/index.html" ]] || air_die "the web app was not built (build/site/index.html is missing)"
# --frozen: fail on a lock that does not match pyproject.toml rather than quietly re-resolving, so a
# customer deploys the dependency versions that were reviewed and scanned (P2-082).
(cd "$ROOT/infra" && uv sync --frozen --quiet)
air_cdk
cd "$ROOT/infra"

# Outside us-east-1 a small edge stack holds CloudFront's web ACL there, so bootstrap both Regions.
BOOTSTRAP=()
[[ -n "${AIR_CFN_EXECUTION_POLICIES:-}" ]] && BOOTSTRAP+=(--cloudformation-execution-policies "$AIR_CFN_EXECUTION_POLICIES")
for r in $(printf '%s\n' "$REGION" us-east-1 | sort -u); do
  BOOTSTRAP_STATE="$(aws cloudformation describe-stacks --region "$r" --stack-name CDKToolkit \
                     --query 'Stacks[0].StackStatus' --output text 2>/dev/null || true)"
  if [[ -z "$BOOTSTRAP_STATE" || "$BOOTSTRAP_STATE" == None ]]; then
    "$CDK" bootstrap "aws://$ACCOUNT/$r" ${BOOTSTRAP[@]+"${BOOTSTRAP[@]}"} "${ARGS[@]}"
    continue
  fi
  # The bootstrap stack already exists, so --cloudformation-execution-policies would be ignored.
  # A customer who set AIR_CFN_EXECUTION_POLICIES must not be left believing CloudFormation deploys
  # with their scoped policy when another CDK app bootstrapped the account with AdministratorAccess.
  case "$BOOTSTRAP_STATE" in
    CREATE_COMPLETE|UPDATE_COMPLETE|IMPORT_COMPLETE|UPDATE_ROLLBACK_COMPLETE) ;;
    *) air_die "the CDK bootstrap stack CDKToolkit in $r is $BOOTSTRAP_STATE: fix or delete it (see https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html), then deploy again" ;;
  esac
  # Not readable (no ssm:GetParameter, or a bootstrap too old to publish it): the check is skipped.
  BOOTSTRAP_VERSION="$(aws ssm get-parameter --region "$r" --name /cdk-bootstrap/hnb659fds/version \
                       --query Parameter.Value --output text 2>/dev/null || true)"
  if [[ "$BOOTSTRAP_VERSION" =~ ^[0-9]+$ ]] && (( BOOTSTRAP_VERSION < MIN_BOOTSTRAP_VERSION )); then
    air_die "the CDK bootstrap in $r is version $BOOTSTRAP_VERSION; this sample needs $MIN_BOOTSTRAP_VERSION or later. Run: $CDK bootstrap aws://$ACCOUNT/$r"
  fi
  if [[ -n "${AIR_CFN_EXECUTION_POLICIES:-}" ]]; then
    DEPLOYED_POLICIES="$(aws cloudformation describe-stacks --region "$r" --stack-name CDKToolkit \
                         --query "Stacks[0].Parameters[?ParameterKey=='CloudFormationExecutionPolicies'].ParameterValue" \
                         --output text 2>/dev/null || true)"
    if [[ "$DEPLOYED_POLICIES" != "$AIR_CFN_EXECUTION_POLICIES" ]]; then
      air_die "CDKToolkit in $r already exists and deploys with CloudFormationExecutionPolicies='${DEPLOYED_POLICIES:-<none: the default AdministratorAccess role>}', not the AIR_CFN_EXECUTION_POLICIES you set. Re-bootstrap that Region with your policy ($CDK bootstrap aws://$ACCOUNT/$r --cloudformation-execution-policies $AIR_CFN_EXECUTION_POLICIES), or unset the variable to accept the existing role."
    fi
    echo "CDK bootstrap in $r already deploys with your CloudFormationExecutionPolicies."
  fi
done

# An update that would broaden IAM permissions asks first when someone is at the terminal.
APPROVAL=never
[[ "$EXISTING" == true && -t 0 ]] && APPROVAL=broadening

EDGE="$STACK-$REGION-Edge"

# --diff shows every stack this run would change, and nothing is deployed until it is approved: the
# edge stack carries the WAF rules and WAF logging, so deploying it first would change them whatever
# the answer. A diff that errors stops the deploy rather than asking to approve an unseen change.
if [[ "$DIFF" == true ]]; then
  DIFF_ARGS=("${ARGS[@]}")
  TARGETS=("$STACK")
  if [[ "$REGION" != us-east-1 ]]; then
    TARGETS=("$EDGE" "$STACK")
    # Synthesising the main stack needs the edge stack's web ACL. Read it from the deployed edge
    # stack so the diff is the real one; on a first install there is nothing to compare anyway.
    DIFF_ACL="$(aws cloudformation describe-stacks --region us-east-1 --stack-name "$EDGE" \
                --query "Stacks[0].Outputs[?OutputKey=='WebAclArn'].OutputValue" --output text 2>/dev/null || true)"
    [[ -n "$DIFF_ACL" && "$DIFF_ACL" != None ]] || DIFF_ACL="arn:aws:wafv2:us-east-1:$ACCOUNT:global/webacl/not-yet-created/0"
    DIFF_ARGS+=(-c "webAclArn=$DIFF_ACL")
  fi
  for target in "${TARGETS[@]}"; do
    "$CDK" diff "$target" --exclusively "${DIFF_ARGS[@]}" \
      || air_die "cdk diff failed for $target; nothing was deployed. Fix the error, or deploy without --diff."
  done
  # No terminal, or anything but "yes", means no: nothing has been deployed at this point.
  answer=""
  read -r -p "Apply these changes to ${TARGETS[*]}? Type yes to continue: " answer || true
  [[ "$answer" == yes ]] || { echo "Not applied; nothing was deployed." >&2; exit 1; }
  APPROVAL=never  # reviewed above
fi

if [[ "$REGION" != us-east-1 ]]; then
  # CloudFront web ACLs live in us-east-1: deploy that first and hand its ARN to the main stack. The
  # name carries the Region, so deployments in two Regions never share (or delete) one ACL.
  "$CDK" deploy "$EDGE" --require-approval "$APPROVAL" "${ARGS[@]}"
  ACL="$(aws cloudformation describe-stacks --region us-east-1 --stack-name "$EDGE" \
         --query "Stacks[0].Outputs[?OutputKey=='WebAclArn'].OutputValue" --output text)"
  ARGS+=(-c "webAclArn=$ACL")
fi

mkdir -p "$ROOT/build"
deploy_main() {
  "$CDK" deploy "$STACK" --exclusively --require-approval "$APPROVAL" --outputs-file "$ROOT/build/outputs.json" "$@" \
    2>&1 | tee "$ROOT/build/deploy.log"
  return "${PIPESTATUS[0]}"
}
# A fresh install that retains data is created without deletion protection and protected in a second,
# quick update: CloudFormation cannot roll back a failed first deployment through protected resources.
PROTECT_AFTER=false
if [[ "${OPT_retainData}" != false && "$EXISTING" == false ]]; then
  ARGS+=(-c deletionProtection=false)
  PROTECT_AFTER=true
fi
if ! deploy_main "${ARGS[@]}"; then
  # Web search is on unless AIR_WEB_SEARCH says otherwise. When the default meets an account or
  # Region without the AgentCore web-search connector, deploy without it instead of failing. No API
  # lists the connectors an account may use, so the failure message is the signal.
  # Only on a fresh install: on an existing deployment the same message would switch off a web search
  # that works, record webSearch=false, and keep it off on every later deploy (P3-052).
  if [[ "$WEB_SEARCH_FROM_ENV" == false && "${OPT_webSearch}" != false && "$EXISTING" == false ]] \
     && grep -Eiq "web-?search.{0,80}(not available|unavailable|not supported|not enabled)" "$ROOT/build/deploy.log"; then
    echo
    echo "The AgentCore web-search connector is not available to this account in $REGION."
    echo "Deploying without web search; modules still read your sources and fetch pages."
    ARGS=("${ARGS[@]/webSearch=true/webSearch=false}")
    deploy_main "${ARGS[@]}" || exit 1
  else
    if [[ "$EXISTING" == true ]] \
       && grep -Eiq "web-?search.{0,80}(not available|unavailable|not supported|not enabled)" "$ROOT/build/deploy.log"; then
      echo
      echo "The update failed on web search, which this deployment has on. It was not turned off: deploy"
      echo "again with AIR_WEB_SEARCH=false if you mean to remove it."
    fi
    exit 1
  fi
fi
if [[ "$PROTECT_AFTER" == true ]]; then
  echo
  echo "Turning on deletion protection for the tables and the user pool."
  if ! deploy_main "${ARGS[@]/deletionProtection=false/deletionProtection=true}"; then
    echo
    echo "WARNING: $STACK was created, but DELETION PROTECTION IS OFF on its two DynamoDB tables and"
    echo "         its Cognito user pool: deleting the stack now would delete your data. The update"
    echo "         above failed; fix the error and run this script again to turn protection on."
    exit 1
  fi
fi

# ---- After: is anyone told when an alarm fires? -----------------------------------------------------------
TOPIC="$(python3 -I -c '
import json, sys
outputs = json.load(open(sys.argv[1]))
print(next(iter(outputs.values()), {}).get("AlarmTopicArn", ""))
' "$ROOT/build/outputs.json" 2>/dev/null || true)"
if [[ -n "$TOPIC" ]]; then
  CONFIRMED="$(aws sns list-subscriptions-by-topic --topic-arn "$TOPIC" \
               --query "length(Subscriptions[?SubscriptionArn!='PendingConfirmation' && SubscriptionArn!='Deleted'])" \
               --output text 2>/dev/null || echo unknown)"
  if [[ "$CONFIRMED" == 0 ]]; then
    echo
    echo "WARNING: no one has confirmed the alarm subscription yet, so alarms notify nobody."
    echo "         Confirm the email from Amazon SNS sent to ${ALARM_EMAIL:-$ADMIN_EMAIL}."
  fi
fi

echo
if [[ "$OPT_authMode" == federated ]]; then
  echo "Done. Open the AppUrl above and sign in through ${OPT_oidcProviderName:-the identity provider}; $ADMIN_EMAIL becomes an administrator at first sign-in."
else
  echo "Done. Open the AppUrl above; on a first deployment $ADMIN_EMAIL receives a temporary password by email."
fi
