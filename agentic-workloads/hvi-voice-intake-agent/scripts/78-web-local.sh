#!/usr/bin/env bash
# Run the browser intake surface LOCALLY, against the deployed agent.
#
#   http://localhost:8095  ->  media/bot_web.py  ->  AgentCore runtime (ap-south-1)
#                                              ->  Voxtral / Whisper / OmniVoice
#
# WHY LOCALHOST FIRST, AND NOT STRAIGHT TO THE MEDIA HOST
#
# Two browser constraints make a deployed WebRTC page materially harder than a
# deployed WebSocket page, and neither has anything to do with our code:
#
#   SECURE CONTEXT  getUserMedia is refused over plain http. localhost is
#       exempt, so a local run needs no certificate at all. Deployed, the page
#       has to be behind TLS - which is what scripts/75-ws-test.sh uses
#       CloudFront for.
#
#   MEDIA PATH      CloudFront cannot carry the media. It proxies TCP; WebRTC
#       audio is UDP (ICE/DTLS/SRTP) directly between the browser and the host.
#       So a deployed setup needs UDP ingress on the instance AND a STUN server,
#       because the box sits behind EC2's 1:1 NAT and aiortc would otherwise
#       advertise only its private address. On localhost both peers are on the
#       loopback interface and none of that applies.
#
# So this script exists to prove the pipeline - WebRTC audio, barge-in, live
# transcript, the form filling in, the Salesforce link - with the NAT and TLS
# problems removed. Deployment is a separate, riskier step.
#
# Usage
#   bash scripts/78-web-local.sh
#   HVI_WEB_PORT=9000 bash scripts/78-web-local.sh
#   HVI_WEB_DEFAULT_PHONE=+911234567890 bash scripts/78-web-local.sh

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh
# Local overrides (endpoints, phone numbers) if present.
[ -f config/env.local.sh ] && source config/env.local.sh

PORT="${HVI_WEB_PORT:-8095}"

: "${AGENT_RUNTIME_ARN:?AGENT_RUNTIME_ARN unset; run scripts/40-agentcore.sh first}"

# The dependencies live in the project venv for a local run. On the media host
# they come from media/requirements-web.txt instead.
if ! ./.venv/bin/python -c 'import aiortc, fastapi, uvicorn' 2>/dev/null; then
  hvi_err "the local venv is missing the web dependencies. Install them with:"
  hvi_err "  ./.venv/bin/pip install -r media/requirements-web.txt"
  exit 1
fi

# The caller must already exist in the CRM: the orchestrator declines anyone it
# cannot identify, which is correct for a call that collects medical history and
# is NOT a bug to work around. This is the same number the WebSocket harness
# uses, so a demo does not need new CRM data.
export HVI_WEB_DEFAULT_PHONE="${HVI_WEB_DEFAULT_PHONE:-+911234567890}"

# No ICE servers on loopback. Setting one here would only add a round trip to a
# STUN server to discover an address the browser already has.
unset HVI_WEB_ICE_SERVERS || true

# Loopback only. Stated explicitly rather than relying on the code default, so
# that reading this script tells you the exposure.
export HVI_WEB_HOST="127.0.0.1"

# Prompt-cache disk tier. The code default (/var/lib/hvi/prompt-cache) is owned by
# the deployed media host and is NOT writable on a laptop, so a local run logs
# "prompt cache dir ... is NOT WRITABLE" and synthesises every sentence live —
# ~2.9s of dead air per prompt, which reads as "the bot is broken / slow". Point
# the cache at a writable, gitignored project dir so the local surface gets the
# same fast cached prompts (fetched once from PROMPT_CACHE_BUCKET, then served
# from disk in ~0.5ms).
export PROMPT_CACHE_DIR="${PROMPT_CACHE_DIR:-$PWD/build/prompt-cache}"
mkdir -p "$PROMPT_CACHE_DIR"

# A fresh token per run, unless one is supplied. Persisting it would mean an old
# browser tab could still open a session tomorrow.
if [ -z "${HVI_WEB_TOKEN:-}" ]; then
  HVI_WEB_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)
fi
export HVI_WEB_TOKEN

hvi_log "agent      : ${AGENT_RUNTIME_ARN##*/}"
hvi_log "region     : ${DATA_REGION}"
hvi_log "stt / tts  : ${STT_ENGINE:-hybrid} / omnivoice"
hvi_log "default no.: ${HVI_WEB_DEFAULT_PHONE}"
hvi_log "bind       : 127.0.0.1:${PORT} (loopback only - nothing is published)"
hvi_log ""
hvi_log "OPEN THIS URL - the token is required and changes every run:"
hvi_log ""
hvi_log "  http://localhost:${PORT}/?token=${HVI_WEB_TOKEN}"
hvi_log ""
hvi_log "Use headphones, or the agent's own voice will be transcribed as your"
hvi_log "answer. The browser cancels echo from the speakers it knows about, but"
hvi_log "a loud room still leaks."
hvi_log ""

exec ./.venv/bin/python -m media.bot_web --port "$PORT"
