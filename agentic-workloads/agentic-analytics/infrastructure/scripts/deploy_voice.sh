#!/usr/bin/env bash
# Voice deploy entrypoint — points you at the right path per mode.
#
# The SAME bot (app/voice/bot.py) runs in all modes; only WHERE it runs and HOW the
# browser connects differ.
#
#   laptop         Pipecat pipeline on your laptop + UI on localhost:3001 (dev).
#                  → runs the local bot here.
#   agentcore      Pipecat pipeline on its OWN AgentCore Runtime (WebRTC + KVS TURN).
#                  → FULLY CFN: deploy the main stack with EnableVoice=true
#                    VoiceMode=agentcore (see infrastructure/scripts/package_and_upload.sh
#                    and DEPLOYMENT.md). Not a standalone script. Fast iteration:
#                    deploy_backend.sh --voice-only.
#   pipecat-cloud  Pipecat Cloud + Amplify UI (Daily transport).
#                  → main CFN (EnableVoice=true VoiceMode=pipecat-cloud) THEN the
#                    post-deploy finisher infrastructure/scripts/deploy_voice_pcc.sh.
#
# Voice always uses the SIGNED-IN user's own token (RBAC/RLS per user); there is
# no demo identity in hosted mode.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-laptop}"

case "$TARGET" in
  laptop)
    echo "==> Mode: laptop (local Pipecat pipeline + local UI)"
    exec bash "$HERE/run_voice_laptop.sh" "${@:2}"
    ;;
  agentcore)
    cat >&2 <<'EOF'
==> agentcore mode is deployed via the MAIN CloudFormation stack (fully CFN-native).

  1. cd infrastructure/scripts && ./package_and_upload.sh <artifacts-bucket>
  2. run the printed create-stack command WITH:
       ParameterKey=EnableVoice,ParameterValue=true
       ParameterKey=VoiceMode,ParameterValue=agentcore
       ParameterKey=DeepgramApiKey,ParameterValue=<key>
       ParameterKey=DeepgramVoiceId,ParameterValue=aura-2-apollo-en

Fast iteration after the first deploy:
  infrastructure/scripts/deploy_backend.sh --voice-only

See DEPLOYMENT.md. (No standalone script — voice is a stack parameter.)
EOF
    exit 0
    ;;
  pipecat-cloud|pipecat|cloud)
    cat >&2 <<'EOF'
==> pipecat-cloud mode = main CFN deploy, THEN the post-deploy finisher.

  1. deploy the main stack with EnableVoice=true VoiceMode=pipecat-cloud
  2. PCC_PAT=... PCC_PUBLIC_KEY=... DEEPGRAM_API_KEY=... DAILY_API_KEY=... \
       infrastructure/scripts/deploy_voice_pcc.sh

See DEPLOYMENT.md.
EOF
    exit 0
    ;;
  *)
    cat >&2 <<EOF
ERROR: unknown deploy target '$TARGET'

Valid targets:
  laptop          local Pipecat pipeline + UI on localhost:3001 (dev)
  agentcore       main CFN deploy with EnableVoice=true VoiceMode=agentcore
  pipecat-cloud   main CFN deploy + infrastructure/scripts/deploy_voice_pcc.sh

Example:
  infrastructure/scripts/deploy_voice.sh laptop
EOF
    exit 2
    ;;
esac
