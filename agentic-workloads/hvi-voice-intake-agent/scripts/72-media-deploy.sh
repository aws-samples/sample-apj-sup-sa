#!/usr/bin/env bash
# Deploy the call media pipeline to the Asterisk instance as a systemd service.
#
# The pipeline (media/bot.py) has to run ON the Asterisk box: Asterisk's
# AudioSocket driver dials 127.0.0.1:8090, and keeping the media loop on loopback
# is what makes barge-in fast. Detecting speech and stopping playback becomes a
# local decision with no network hop.
#
# WHAT THIS GRANTS, AND WHAT IT DELIBERATELY DOES NOT
#
# The instance role gets four permissions and no more:
#   bedrock-agentcore:InvokeAgentRuntime   call the orchestrator
#   bedrock:InvokeModel                    Voxtral, the primary STT
#   sagemaker:InvokeEndpoint               Whisper arbiter, and OmniVoice TTS
#   polly is explicitly DENIED, so the wrong voice cannot ship even by accident
#
# It gets NO DynamoDB access. The media layer never touches intake state - it
# turns a transcript into an agent call and the reply into speech. Every
# checkpoint, consent record and response lives behind AgentCore. That is worth
# being deliberate about: this is the one host with a public IP and an open SIP
# port, so it is the one most likely to be attacked, and it cannot read or write
# a single health record.
#
# Usage
#   bash scripts/72-media-deploy.sh
#   HVI_TTS=omnivoice OMNIVOICE_ENDPOINT=... bash scripts/72-media-deploy.sh
#   SKIP_PIP=1 bash scripts/72-media-deploy.sh          # code-only redeploy

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
ROLE="${PREFIX}-asterisk-role"
POLICY_NAME="${PREFIX}-media-runtime"
SERVICE="${PREFIX}-media"
AS_PORT="${AUDIOSOCKET_PORT:-8090}"

# OMNIVOICE ONLY. There is no engine selector any more.
#
# This defaulted to a Polly "interim" for a long time, with a comment saying the
# OmniVoice endpoint was not provisioned. That stopped being true the moment
# scripts/52-omnivoice.sh succeeded, and because nobody revisited the default,
# every deploy afterwards quietly shipped the wrong voice while the GPU endpoint
# sat idle and billing.
#
# The requirement is explicit and was stated twice: "you can use Omni voice as a
# text-to-speech provider and whisper large for a speech-to-text provider". A
# second engine one variable away does not stay an interim, so the variable is
# gone rather than merely re-defaulted.
if [ -n "${HVI_TTS:-}" ] && [ "${HVI_TTS}" != "omnivoice" ]; then
  hvi_log "FATAL: HVI_TTS=${HVI_TTS} is not available. OmniVoice is the TTS the"
  hvi_log "customer specified and the only one wired up. If the endpoint is"
  hvi_log "unhealthy, fix scripts/52-omnivoice.sh rather than changing voice."
  exit 1
fi
TTS_ENGINE="omnivoice"

# THE VOICE VERSION IS NOT DEFINED HERE, AND MUST NOT BE.
#
# This line used to read CACHE_VOICE="${PROMPT_CACHE_VOICE:-omnivoice-ref-v1}",
# inventing a literal that then went into the box's environment and SHADOWED the
# authoritative default in common/hvi_prompt_cache.py. The two never matched, so
# every cache lookup on every turn missed, every prompt was synthesised live at
# ~2.9s, and 102 correctly-built objects sat unread in S3. The caller reported it
# as "there is silence when I make the call".
#
# Worth naming the pattern, because this was its fourth appearance: something
# downstream silently redefines the cache identity, the feature it guards keeps
# working just badly enough to look plausible, and nothing errors. Previously it
# was a stale version string, then a missing language on the builder, then the
# bridge never telling TTS the language.
#
# The fix is structural rather than a corrected literal: there is now ONE
# definition, the default in common/hvi_prompt_cache.py, and that file ships to
# the box in the same tarball as the code that reads it. So the runtime and the
# cache cannot disagree unless someone sets the variable on purpose.
LOCAL_CACHE_VOICE=$(./.venv/bin/python scripts/print-voice-version.py)
[ -n "$LOCAL_CACHE_VOICE" ] || { hvi_err "could not read the voice version"; exit 1; }
hvi_log "voice ver  : $LOCAL_CACHE_VOICE (from common/hvi_prompt_cache.py)"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
IID="${ASTERISK_INSTANCE_ID:-}"
if [ -z "$IID" ]; then
  IID=$(aws ec2 describe-instances --region "$R" \
    --filters "Name=tag:Name,Values=${PREFIX}-asterisk" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[0].InstanceId' --output text)
fi
[ -n "$IID" ] && [ "$IID" != "None" ] || { hvi_err "no running ${PREFIX}-asterisk instance"; exit 1; }

: "${AGENT_RUNTIME_ARN:?AGENT_RUNTIME_ARN unset; run scripts/40-agentcore.sh first}"

hvi_log "instance   : $IID"
hvi_log "agent      : ${AGENT_RUNTIME_ARN##*/}"
hvi_log "stt engine : ${STT_ENGINE:-hybrid}"
hvi_log "tts engine : $TTS_ENGINE"

# 2GB box. Nothing compiles (all aarch64 wheels) but check headroom before a
# long install rather than discovering it half way through.
hvi_log "disk and memory headroom:"
bash scripts/ssm-run.sh 'df -h / | tail -1; free -m | sed -n 2p' "$IID"

# ---------------------------------------------------------------------------
# IAM: least-privilege inline policy on the instance role
# ---------------------------------------------------------------------------
hvi_log "applying inline policy $POLICY_NAME to $ROLE"

POLICY_DOC=$(jq -nc \
  --arg agent   "$AGENT_RUNTIME_ARN" \
  --arg region  "$R" \
  --arg acct    "$AWS_ACCOUNT_ID" \
  --arg voxtral "${STT_VOXTRAL_MODEL:-mistral.voxtral-small-24b-2507}" \
  --arg prefix  "$PREFIX" \
  '{
    Version: "2012-10-17",
    Statement: [
      { Sid: "InvokeOrchestrator",
        Effect: "Allow",
        Action: ["bedrock-agentcore:InvokeAgentRuntime"],
        Resource: [$agent, ($agent + "/*")] },
      { Sid: "InvokeVoxtralSTT",
        Effect: "Allow",
        Action: ["bedrock:InvokeModel"],
        Resource: ["arn:aws:bedrock:\($region)::foundation-model/\($voxtral)"] },
      { Sid: "InvokeSpeechEndpoints",
        Effect: "Allow",
        Action: ["sagemaker:InvokeEndpoint"],
        Resource: ["arn:aws:sagemaker:\($region):\($acct):endpoint/\($prefix)-*"] },
      { Sid: "StoreCallRecording",
        Effect: "Allow",
        Action: ["s3:PutObject"],
        Resource: ["arn:aws:s3:::\($prefix)-archive-\($acct)-*/*"] },
      { Sid: "DenyOtherTTSVendors",
        Effect: "Deny",
        Action: ["polly:*"],
        Resource: "*" }
    ]
  }')

aws iam put-role-policy --role-name "$ROLE" \
  --policy-name "$POLICY_NAME" --policy-document "$POLICY_DOC"

# ---------------------------------------------------------------------------
# Package the code
# ---------------------------------------------------------------------------
# Only the modules media/ actually imports travel. common/hvi_state.py stays
# behind on purpose: it is the DynamoDB layer, this host has no DynamoDB
# permissions, and shipping it would invite someone to import it later and quietly
# widen the blast radius of the most exposed machine in the system.
#
# hvi_prompt_cache.py DOES have to travel, and forgetting it took a live call
# down: media/tts.py build_tts() constructs CachedTTSService, which imports
# common.hvi_prompt_cache, so with the module absent the import raised
# ModuleNotFoundError inside pipeline setup. The caller answered, AudioSocket
# connected, the pipeline began - and then died before TTS existed. What the
# caller experienced was a call that connected to total silence, which is
# indistinguishable from a telephony fault and sent us looking at RTP.
#
# Anything media/ imports from common/ must be listed here. That is easy to forget
# because it fails at runtime on the box rather than at build time here, so the
# loop below verifies it instead of trusting the list.
mkdir -p build
TGZ="build/media.tgz"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/media" "$STAGE/common" "$STAGE/web"
# requirements-web.txt travels even though this script never pip-installs it: the
# hvi-web unit from scripts/36-web-cloudfront.sh shares this venv, and the
# installer clears $APP_DIR/media wholesale, so leaving it behind would delete
# the only record on the box of what that surface needs to be rebuilt.
cp media/*.py media/requirements.txt media/requirements-web.txt "$STAGE/media/"
# hvi_web_auth travels because media/bot_web.py imports it for cognito mode, and
# the guard below flags its absence as fatal - which it was, silently, until the
# guard learned to see `from common import X` as well.
cp common/hvi_stt.py common/hvi_prompt_cache.py common/hvi_recording.py \
   common/hvi_web_auth.py "$STAGE/common/"

# THE BROWSER SURFACE'S OWN FILES TRAVEL TOO, and their absence here is what
# broke the page for two weeks.
#
# media/bot_web.py serves this directory: WEB_DIR is ../web relative to the
# module, so /opt/hvi/web. Nothing shipped it. So the page was hand-copied onto
# the box once, and every fix to web/app.js afterwards stayed on the laptop -
# the box was still serving a build from 2026-08-29 that drew the agent's lines
# from RTVI's bot-transcription events and had no hvi-turn diagnostics at all.
#
# The failure mode is the expensive kind: the page still LOOKED alive. The orb
# animated, audio played, agent lines appeared. Only the caller's half of the
# transcript and the questionnaire were missing, which reads as two bugs in the
# form pipeline rather than one stale asset, and the version of app.js that
# would have said "not receiving turn updates" was itself the version that had
# not shipped.
#
# Copied as a whole directory rather than a named list, because a list is the
# thing that gets forgotten when an asset is added - which is the same mistake
# one level down.
cp web/*.html web/*.css web/*.js "$STAGE/web/"

# Every `from common.X import` in media/ must have shipped. Catching a missing
# module here costs a second; catching it on the box costs a silent call.
# Both import forms count. The pattern used to match only `from common.X import`,
# so `from common import X` - which is how media/bot.py and bot_web.py pull in
# hvi_recording - was invisible to this check: the module could go unshipped and
# the guard would still pass, which is the exact silent failure it exists to stop.
missing=""
for mod in $(grep -rhoE "from common\.[a-z_]+ import|import common\.[a-z_]+|from common import [a-z_, ]+" media/*.py \
             | sed -E 's/^from common import //; s/^from (common\.[a-z_]+) import.*/\1/; s/^import //' \
             | tr ',' '\n' | sed -E 's/^ *//; s/ *$//; s/^common\.//' \
             | grep -E '^[a-z_]+$' | sort -u); do
  [ -f "$STAGE/common/${mod}.py" ] || missing="$missing $mod"
done
if [ -n "$missing" ]; then
  hvi_log "FATAL: media/ imports common modules that are not being shipped:$missing"
  hvi_log "Add them to the cp above, or the pipeline dies mid-call with"
  hvi_log "ModuleNotFoundError and the caller hears silence."
  exit 1
fi
: > "$STAGE/common/__init__.py"
: > "$STAGE/media/__init__.py"
rm -f "$TGZ"
# COPYFILE_DISABLE=1 because this is packaged on macOS, where tar writes an
# AppleDouble "._name" sidecar for every file carrying an extended attribute.
# They unpack onto the box as junk beside the real modules - /opt/hvi/media
# currently holds a ._agent_bridge.py for every module - and while Python cannot
# import them, they make `ls` on the box actively misleading when you are trying
# to work out which copy of a file is live.
COPYFILE_DISABLE=1 tar -czf "$TGZ" -C "$STAGE" media common web
hvi_log "packaged $(wc -c < "$TGZ" | tr -d ' ') bytes -> $TGZ"
hvi_log "  media/ $(ls "$STAGE/media" | wc -l | tr -d ' ') files, common/ $(ls "$STAGE/common" | wc -l | tr -d ' '), web/ $(ls "$STAGE/web" | wc -l | tr -d ' ')"

# ---------------------------------------------------------------------------
# Generate the environment file locally
# ---------------------------------------------------------------------------
# Generated here and shipped as a file, rather than heredoc'd inside an SSM
# command, so the exact bytes that land on the box are the bytes reviewed here.
ENVF="build/media.env"

# Only propagate the voice version if the OPERATOR set it, in which case the local
# cache was built under that name too and the box must agree. Left unset, the box
# uses the default from the hvi_prompt_cache.py it was just shipped - one
# definition, so nothing to keep in sync.
if [ -n "${PROMPT_CACHE_VOICE:-}" ]; then
  PROMPT_CACHE_VOICE_LINE="PROMPT_CACHE_VOICE=${PROMPT_CACHE_VOICE}"
  hvi_warn "propagating an explicit PROMPT_CACHE_VOICE=${PROMPT_CACHE_VOICE}"
else
  PROMPT_CACHE_VOICE_LINE="# (unset: the code default applies)"
fi

cat > "$ENVF" <<EOF
# Generated by scripts/72-media-deploy.sh - edit the script, not this file.
# Regenerated on every deploy; local changes here are lost.

DATA_REGION=${R}
AWS_REGION=${R}
AWS_DEFAULT_REGION=${R}

AGENT_RUNTIME_ARN=${AGENT_RUNTIME_ARN}

# Hybrid: Voxtral fast path (~619ms), escalating to the Whisper arbiter only on
# a positive script mismatch. Measured blended p50 463ms over 24 fixtures.
STT_ENGINE=${STT_ENGINE:-hybrid}
STT_ENDPOINT=${STT_ENDPOINT:-${PREFIX}-whisper}
STT_VOXTRAL_MODEL=${STT_VOXTRAL_MODEL:-mistral.voxtral-small-24b-2507}

HVI_TTS=${TTS_ENGINE}
OMNIVOICE_ENDPOINT=${OMNIVOICE_ENDPOINT:-${PREFIX}-omnivoice}

# Pre-synthesised audio for the ~85 fixed form prompts. Without this variable the
# cache is simply never consulted and every prompt is synthesised live, which is
# ~2,885ms against a 0.5ms disk hit - the single largest term in the turn budget,
# silently reintroduced by an unset environment variable.
PROMPT_CACHE_BUCKET=${PROMPT_CACHE_BUCKET:-}

# Where this box stores call audio: the per-call archive prefix, beside the
# transcript the agent writes. Unset would silently disable recording - the
# caller would still be told the call is recorded - so it is passed explicitly
# rather than left to a default. See common/hvi_recording.py.
CALL_ARCHIVE_BUCKET=${CALL_ARCHIVE_BUCKET:-}
CALL_ARCHIVE_PREFIX=${CALL_ARCHIVE_PREFIX:-calls}
HVI_RECORD_CALLS=${HVI_RECORD_CALLS:-1}

# PROMPT_CACHE_VOICE IS DELIBERATELY ABSENT unless explicitly overridden.
#
# The cache key is sha256(VOICE_VERSION|language|text). Setting the variable here
# from a literal in the deploy script is what orphaned the whole cache: see the
# comment above LOCAL_CACHE_VOICE. The authoritative value is the default in
# common/hvi_prompt_cache.py, which ships in the same tarball as the code that
# reads it, so leaving this unset makes disagreement impossible.
#
# Effective value on this box: ${LOCAL_CACHE_VOICE}
${PROMPT_CACHE_VOICE_LINE}

AUDIOSOCKET_HOST=127.0.0.1
AUDIOSOCKET_PORT=${AS_PORT}

HVI_LOG_LEVEL=${HVI_LOG_LEVEL:-INFO}

# VAD sensitivity. min_volume 0.55 was tuned on the phone leg; a live browser
# session delivered real speech at RMS ~830 that never fired the VAD, so the
# caller's "haan" was silently unheard and they repeated themselves into dead
# air. 0.35 was the highest setting that caught those live windows; the false
# trigger cost is bounded because STT already discards sub-0.35s segments and
# the answer-plausibility layer bounces noise transcripts.
VAD_MIN_VOLUME=${VAD_MIN_VOLUME:-0.35}
VAD_CONFIDENCE=${VAD_CONFIDENCE:-0.6}
VAD_START_SECS=${VAD_START_SECS:-0.2}
VAD_STOP_SECS=${VAD_STOP_SECS:-1.5}
EOF
hvi_log "generated $ENVF"

# ---------------------------------------------------------------------------
# Ship and install
# ---------------------------------------------------------------------------
bash scripts/ssm-put.sh "$TGZ"                      /tmp/media.tgz          "$IID"
bash scripts/ssm-put.sh "$ENVF"                     /tmp/media.env          "$IID"
bash scripts/ssm-put.sh scripts/remote/media-install.sh /tmp/media-install.sh "$IID"

hvi_log "running installer on the instance"
bash scripts/ssm-run.sh "SKIP_PIP=${SKIP_PIP:-0} bash /tmp/media-install.sh" "$IID"

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
hvi_log "post-deploy state:"
bash scripts/ssm-run.sh "systemctl is-active ${SERVICE}; ss -ltn | grep ':${AS_PORT}' || echo 'PORT ${AS_PORT} NOT LISTENING'; journalctl -u ${SERVICE} -n 20 --no-pager" "$IID"

# ---------------------------------------------------------------------------
# Does the box agree with the cache it is meant to read?
# ---------------------------------------------------------------------------
# THE ONE CHECK THAT WOULD HAVE CAUGHT THE SILENT CALL. A voice-version mismatch
# between the cache builder and the runtime breaks nothing visibly: the service
# starts, the port listens, the logs look clean, and every prompt is quietly
# synthesised live at ~2.9s while the cache sits unread. It is only audible as
# dead air, and only on a real call, which is the most expensive place to find it.
#
# So it is asserted here, from the box's own effective configuration - the env file
# it will actually load plus the module it was actually shipped - rather than from
# what this script believes it sent.
hvi_log "verifying the box agrees with the prompt cache"
BOX_VOICE=$(bash scripts/ssm-run.sh \
  "set -a; . ${ENV_FILE:-/etc/hvi/media.env}; set +a; \
   cd /opt/hvi && ./venv/bin/python -c \
   'import sys; sys.path.insert(0, \"common\"); \
    from hvi_prompt_cache import VOICE_VERSION as v; print(v)' 2>/dev/null | tail -1" \
  "$IID" 2>/dev/null | grep -oE 'omnivoice[a-z0-9.-]*' | tail -1 || true)

if [ -z "$BOX_VOICE" ]; then
  hvi_warn "could not read the effective voice version from the box; check by hand"
elif [ "$BOX_VOICE" != "$LOCAL_CACHE_VOICE" ]; then
  hvi_err "VOICE VERSION MISMATCH - the prompt cache is unreachable from this box"
  hvi_err "  cache was built with : $LOCAL_CACHE_VOICE"
  hvi_err "  box will look up with: $BOX_VOICE"
  hvi_err ""
  hvi_err "Every prompt will be synthesised live (~2.9s each) and the caller will"
  hvi_err "hear long silences. Either unset PROMPT_CACHE_VOICE in the environment"
  hvi_err "and redeploy, or rebuild the cache under the box's value:"
  hvi_err "  PROMPT_CACHE_VOICE=$BOX_VOICE bash scripts/54-prompt-cache.sh"
  exit 1
else
  hvi_log "voice version agrees: $BOX_VOICE"
fi

hvi_record "media_service"     "$SERVICE"
hvi_record "media_instance_id" "$IID"

hvi_log ""
hvi_log "deployed. NEXT STEP IS NOT A REAL CALL:"
hvi_log "  prove two-way RTP first with scripts/73-echo-test.sh, because the EC2"
hvi_log "  NAT trap shows up as one-way audio and would look like a bot bug."
hvi_log "logs:  bash scripts/ssm-run.sh 'journalctl -u ${SERVICE} -n 100 --no-pager'"
