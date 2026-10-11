#!/usr/bin/env bash
# Shared configuration for the Health Voice Intake (hvi) stack.
# Source this before running any deploy script:  source config/env.sh

set -o pipefail

# ---- Account ----
export AWS_ACCOUNT_ID="111122223333"

# =============================================================================
# TWO-REGION SPLIT -- this is deliberate, and forced by a hard AWS constraint.
#
#   Amazon Chime SDK Voice Connector is NOT available in ap-south-1.
#   Confirmed via list-available-voice-connector-regions:
#     us-east-1, us-west-2, ca-central-1, eu-west-1, eu-west-2,
#     eu-central-1, ap-northeast-1, ap-northeast-2, ap-southeast-1, ap-southeast-2
#
# So for the demo the telephony edge lives in us-east-1 while every store that
# holds health data lives in ap-south-1, satisfying DPDP localisation and the
# DoT requirement that the agent be hosted in India.
#
# In PRODUCTION this split disappears: Tata Communications terminates the SIP
# trunk inside India, so no PSTN media or health data ever leaves the country.
# The fact that AWS telephony cannot terminate a call in India is precisely the
# reason an Indian carrier is required.
#
# Cost of the demo split: one extra cross-region hop per turn (~200ms each way).
# Documented, not hidden.
# =============================================================================

# Everything that stores or processes health data.
export DATA_REGION="ap-south-1"

# Telephony edge only: SIP trunk, call control, transient audio.
export TELEPHONY_REGION="us-east-1"

# Default for plain `aws` calls in the data-plane scripts.
export AWS_REGION="$DATA_REGION"
export AWS_DEFAULT_REGION="$DATA_REGION"

# ---- Naming ----
export PREFIX="hvi"                       # health-voice-intake

# ---- DynamoDB tables (DATA_REGION) ----
export TBL_CALL_STATE="${PREFIX}-call-state"           # PK callId,     SK turnIndex   (per-turn checkpoint)
export TBL_CALL_SESSION="${PREFIX}-call-session"       # PK callId                     (resume pointer + status)
export TBL_CRM="${PREFIX}-crm-cache"                   # PK customerId                 (local projection of Salesforce)
export TBL_FORM_SCHEMA="${PREFIX}-form-schema"         # PK formId,     SK itemId      (dynamic health items)
export TBL_RESPONSES="${PREFIX}-intake-responses"      # PK customerId, SK callId      (finalised submission)
export TBL_CONSENT="${PREFIX}-consent-ledger"          # PK customerId, SK grantedAt   (DPDP consent, append-only)
export TBL_ATTEMPTS="${PREFIX}-call-attempts"          # PK customerId, SK attemptDate (TRAI 3/day cap)
export TBL_AUDIT="${PREFIX}-gate-audit"                # PK callId,     SK checkedAt   (compliance gate evidence)

# ---- Durable call archive (DATA_REGION) ----
# The per-turn checkpoint in TBL_CALL_STATE expires after TURN_TTL_DAYS (30), so
# it is not the record of what was said - it is a resume pointer that happens to
# contain one. This bucket is the durable copy: one transcript per call, kept for
# as long as the policy can be contested, and the target of Salesforce's
# Transcript_URI__c. Created by scripts/55-call-archive.sh.
#
# Named -archive- deliberately: scripts/15-iam.sh already grants the Lambda role
# s3 access to ${PREFIX}-archive-${AWS_ACCOUNT_ID}-*, so the name is what makes
# the existing policy cover it.
export CALL_ARCHIVE_BUCKET="${PREFIX}-archive-${AWS_ACCOUNT_ID}-${DATA_REGION}"
export CALL_ARCHIVE_PREFIX="calls"

# ---- Call recording (DATA_REGION) ----
# The live surfaces DO record now, and the audio goes into CALL_ARCHIVE_BUCKET
# above - s3://.../calls/<callId>/audio/call.wav, beside that call's transcript -
# NOT into AUDIO_BUCKET below. common/hvi_recording.py explains why one bucket
# rather than two: the archive bucket already has the versioning, SSE, TLS-only
# policy, public-access block, ap-south-1 residency and IAM grant that a second
# bucket would have to be given, and one prefix per call is how a person looks
# for it. Stereo, caller left / agent right, so "was that the caller or our own
# echo?" is answerable by listening.
#
# The lawful basis is the consent captured at A020, which tells the caller in
# their own language that the call is recorded, before any health question is
# asked. Recording was previously OFF while that sentence was still being spoken,
# which was the mismatch this closes. A deployment that changes the A020 wording
# must set HVI_RECORD_CALLS=0.
#
#   Browser / WebRTC (web surface)          records - media/bot_web.py
#   Asterisk / AudioSocket (telephony)      records - media/bot.py
#   HVI_DEBUG_RECORD_INBOUND                still OFF and still not this: it
#       dumps UNENCRYPTED raw PCM to an instance volume. This path writes an
#       encrypted object to S3 instead, which is the difference that matters.
export HVI_RECORD_CALLS="${HVI_RECORD_CALLS:-1}"

# ---- Legacy Chime per-turn audio (DATA_REGION) ----
# ONLY the abandoned Chime SMA RecordAudio path ever wrote here, and it was
# replaced by Asterisk because an action-based SMA cannot play and listen at once,
# which makes barge-in impossible - see lambdas/outbound/handler.py. No script
# creates this bucket and it does not exist in the account; the only hvi buckets
# are -archive- and -artifacts-.
#
# It is still exported for two reasons: lambdas/sma/handler.py requires the name,
# and hvi_archive copies any per-turn clips that DO carry an audioKey out of it
# into the per-call archive. That copy short-circuits when no turn carries one, so
# an absent bucket cannot fail a call. Live recording does not use this path.
export AUDIO_BUCKET="${AUDIO_BUCKET:-${PREFIX}-audio-${AWS_ACCOUNT_ID}-${DATA_REGION}}"

# ---- Form ----
# IRDAI-standard Arogya Sanjeevani proposal form. Every Indian insurer must
# offer this product with identical wording, which makes it the canonical
# reference for a health-insurance medical-history script.
export FORM_ID="arogya-sanjeevani-v1"

# ---- Bedrock reasoning model (DATA_REGION) ----
#
# Chosen for data residency, not for benchmark scores. Re-probed with
# tests/diag_residency.py on 2026-08-14; the picture in ap-south-1 is:
#
# Do not infer availability from `aws bedrock list-foundation-models`. The region
# ADVERTISES Opus 5, Sonnet 5, Sonnet 4.6, Opus 4.5 and Nova Pro; every one of
# them rejects on-demand invocation with ValidationException. Listing is not
# invocability, and the only way to know is to call it.
#
# The rule is by model family, NOT by recency:
#
#   anthropic.*      raw ids all REFUSE on-demand ... except
#                    claude-3-haiku-20240307, which works but is far weaker
#   amazon.nova.*    raw ids all REFUSE on-demand (pro, 2-lite, lite alike)
#   mistral.*        works, tool use works, stays in ap-south-1
#   deepseek.v3.2    works, tool use works, stays in ap-south-1
#   apac.*           works, but leaves India for the wider APAC region set
#   global.*         works, but routes worldwide -> breaks DPDP
#
# DPDP-clean and tool-use capable, strongest first:
#   mistral.mistral-large-3-675b-instruct   <-- chosen
#   deepseek.v3.2                           viable second, same residency posture
#   anthropic.claude-3-haiku-20240307-v1:0  weaker
#   mistral.ministral-3-14b-instruct        weaker
#
# So: the strongest model that can be invoked without inference leaving India.
# If the customer accepts APAC-scoped routing, switch to
# apac.anthropic.claude-3-7-sonnet-20250219-v1:0 and note the trade-off. To keep
# a frontier Claude AND India-only inference, the option is Provisioned
# Throughput in ap-south-1, which pins the model to the region.
export BEDROCK_MODEL_ID="mistral.mistral-large-3-675b-instruct"

# Alternatives, in descending order of data-residency strictness.
export BEDROCK_MODEL_INREGION="mistral.mistral-large-3-675b-instruct"
export BEDROCK_MODEL_APAC="apac.anthropic.claude-3-7-sonnet-20250219-v1:0"
export BEDROCK_MODEL_GLOBAL="global.anthropic.claude-sonnet-4-5-20250929-v1:0"

# ---- Speech (DATA_REGION) ----
export STT_MODEL="large-v3"               # Whisper large-v3: handles hi-IN + Hinglish code-mixing
export STT_INSTANCE="ml.g5.xlarge"        # GPU required for large-v3 at conversational latency
# TTS engine. The published default is Amazon Polly (managed, no GPU). OmniVoice
# (k2-fsa/OmniVoice) is OPT-IN only and licensed CC-BY-NC (non-commercial), so it
# is NOT the default. Selection is a DEPLOY-TIME choice (CDK -c hvi:tts=omnivoice
# or HVI_TTS=omnivoice on the media host) with no silent runtime fallback: a
# misconfigured engine fails loudly at startup.
export TTS_MODEL="${TTS_MODEL:-polly}"    # polly (managed default) | k2-fsa/OmniVoice (opt-in, CC-BY-NC)
# In-region managed fallback if self-hosting Whisper proves awkward:
export STT_FALLBACK_MODEL="mistral.voxtral-small-24b-2507"

# ---- Telephony (TELEPHONY_REGION) -- Chime SDK Voice, NOT Amazon Connect ----
export VOICE_CONNECTOR_ID="abcdefghij0123456789"
# Outbound caller ID. MUST be a number assigned to the Voice Connector - Chime
# rejects an INVITE whose From it does not recognise, and does it as
# "486 Busy Here", which reads like the callee is on another call.
#
# Assigned to this VC and outbound capable:
#   +12025550143  Local      (placeholder — replace with your assigned DID)
#   +18005550199  TollFree   (US carriers spam-filter cloud Local DIDs to mobiles
#                             far more aggressively than toll-free)
#
# :- so an explicit override survives being sourced. Without it,
# `OUTBOUND_CALLER_ID=x bash scripts/71-asterisk-config.sh` silently ignores x,
# because sourcing this file overwrites the caller's own value.
export OUTBOUND_CALLER_ID="${OUTBOUND_CALLER_ID:-+12025550143}"
export SMA_ID="00000000-0000-0000-0000-000000000000"

# ---- Tata Communications SIP trunk (production path; supplied by carrier) ----
export TATA_SIP_HOSTS=""      # e.g. "203.0.113.10/32,203.0.113.11/32"
export TATA_SIP_DID=""        # 1600-series, requires KYC + MPLS (3-6 week lead time)

# ---- Salesforce CRM (system of record) ----
export SF_LOGIN_URL="https://login.salesforce.com"
export SF_CLIENT_ID=""
export SF_CLIENT_SECRET=""
export SF_USERNAME=""
# Credentials live in Secrets Manager under hvi/salesforce, never here. Verify the
# org with tools/sf_check.py, which proves the token is accepted for DATA access
# rather than merely issued.
export SF_SECRET_NAME="hvi/salesforce"
# false = write to the real org. The mock path remains in the writeback and is
# CONTRACT-IDENTICAL - same field API names, same upsert key - so it stays useful
# for offline tests. A looser mock would prove nothing and would hide field-name
# bugs until the day Salesforce was switched on.
export SF_MOCK="false"

# ---- SES (DATA_REGION, so confirmation email data stays in India) ----
# SES_FROM_EMAIL is the confirmation Lambda's runtime contract. Keep
# SES_SENDER as a compatibility alias for older local tools.
export SES_FROM_EMAIL="${SES_FROM_EMAIL:-sender@example.com}"
export SES_SENDER="${SES_SENDER:-$SES_FROM_EMAIL}"
export SES_CONFIG_SET="${PREFIX}-confirmations"

# ---- Compliance ----
export TRAI_CALL_START_HOUR="9"    # 09:00 IST
export TRAI_CALL_END_HOUR="21"     # 21:00 IST
export TRAI_MAX_ATTEMPTS_PER_DAY="3"
export SLA_HOURS_FROM_PURCHASE="48"
export IRDAI_RETENTION_YEARS="8"

# ---- Repo root -------------------------------------------------------------
# BASH_SOURCE is not reliably populated here (the login shell is zsh, and this
# file is also sourced from scripts that have already cd'd to the repo root),
# so locate the root by walking up from $PWD looking for a known marker rather
# than trusting $0 or BASH_SOURCE.
_hvi_find_root() {
  local d="$PWD"
  while [ "$d" != "/" ]; do
    if [ -f "$d/config/env.sh" ]; then printf '%s' "$d"; return 0; fi
    d="$(dirname "$d")"
  done
  printf '%s' "$PWD"
}
export HVI_ROOT="${HVI_ROOT:-$(_hvi_find_root)}"

# ---- Deploy state ----
export STATE_FILE="${HVI_ROOT}/.deploy-state.json"

# ---- Generated infrastructure IDs (written by scripts/20-vpc.sh etc.) ----
# shellcheck disable=SC1091
[ -f "${HVI_ROOT}/config/env.local.sh" ] && source "${HVI_ROOT}/config/env.local.sh"

# ---- Helpers ----
# All diagnostics go to stderr. These helpers are called from inside $(...)
# command substitutions, so anything on stdout would corrupt the captured value.
hvi_log()  { printf '\033[1;36m[hvi]\033[0m %s\n' "$*" >&2; }
hvi_warn() { printf '\033[1;33m[hvi:warn]\033[0m %s\n' "$*" >&2; }
hvi_err()  { printf '\033[1;31m[hvi:err]\033[0m %s\n' "$*" >&2; }

# Record a created resource into the deploy-state file so teardown is possible.
hvi_record() {
  local key="$1" value="$2"
  [ -f "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"
  local tmp; tmp="$(mktemp)"
  jq --arg k "$key" --arg v "$value" '.[$k]=$v' "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"
}
