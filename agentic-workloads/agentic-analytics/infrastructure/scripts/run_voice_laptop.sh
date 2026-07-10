#!/usr/bin/env bash
# Laptop mode (1/): run the Pipecat pipeline locally + the UI on localhost.
#
# This is the original dev workflow, preserved. The bot's /start server runs on
# localhost:7860 (it creates Daily rooms itself via DAILY_API_KEY and forwards
# the request body to bot()), and the React dev server runs on localhost:3001
# with REACT_APP_VOICE_START_URL pointing at the local bot.
#
# Prereqs: app/voice/.env filled (DAILY_API_KEY, DEEPGRAM_API_KEY, AWS_AGENT_ARN,
# COGNITO_CLIENT_ID, AWS creds via ambient chain). For laptop dev with no signed-in
# user, set ALLOW_DEMO_FALLBACK=true (+ DEMO_USERNAME/PASSWORD) to mint a token via
# Cognito ROPC; hosted modes never do this — they forward the user's own token. Also
# app/ui/.env.local with REACT_APP_VOICE_START_URL=http://localhost:7860/start.
set -euo pipefail

# This script lives in infrastructure/scripts/, so repo root is two levels up.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/app/voice"

echo "==> Laptop mode. Starting the local Pipecat bot on http://localhost:7860"
echo "    (Ctrl-C to stop.)"
echo ""
echo "    In a SECOND terminal, start the UI:"
echo "       cd $ROOT/app/ui && npm start        # serves http://localhost:3001/app"
echo ""
echo "    Ensure app/ui/.env.local has:"
echo "       REACT_APP_VOICE_START_URL=http://localhost:7860/start"
echo ""

# Default transport is daily (matches the cloud modes). USE_FLUX / USE_SAGEMAKER
# honoured from app/voice/.env as before.
exec uv run bot.py --transport daily
