#!/usr/bin/env bash
# Helper: set VOICE_START_URL in the live Amplify build's config.js and redeploy.
#
# Used by the pipecat-cloud post-deploy script (and handy standalone) to point an
# already-deployed UI at a voice /start URL — without rebuilding the bundle, just
# patching the runtime config.js and pushing a new Amplify deployment.
#
# Usage: _amplify_set_voice_url.sh <amplify-app-id> <voice-start-url> [branch]
set -euo pipefail

APP_ID="${1:?amplify app id required}"
VOICE_URL="${2:?voice start url required}"
BRANCH="${3:-main}"
REGION="${AWS_REGION:-us-west-2}"
# This script lives in infrastructure/scripts/, so repo root is two levels up.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Pull the CURRENT runtime config from the live site, set VOICE_START_URL, rebuild
# config.js, and deploy a fresh build zip (reusing the existing built assets).
echo "    fetching current config.js + build from $BRANCH.$APP_ID"
BUILD="$ROOT/app/ui/build"
[ -d "$BUILD" ] || { echo "    app/ui/build missing — run 'cd app/ui && npm run build' first"; exit 1; }

# Merge VOICE_START_URL into whatever config keys the live site already serves.
LIVE_CFG="$(curl -s "https://${BRANCH}.${APP_ID}.amplifyapp.com/config.js" 2>/dev/null | sed 's/^window.__APP_CONFIG__ = //; s/;[[:space:]]*$//')"
python3 - "$VOICE_URL" "$BUILD/config.js" <<'PY' "$LIVE_CFG"
import json, sys
voice_url, out_path = sys.argv[1], sys.argv[2]
live = sys.argv[3] if len(sys.argv) > 3 else "{}"
try:
    cfg = json.loads(live) if live.strip().startswith("{") else {}
except Exception:
    cfg = {}
cfg["VOICE_START_URL"] = voice_url
# Switching INTO pipecat-cloud mode: drop any AgentCore signaling URL so the
# client doesn't keep using SmallWebRTC (voiceClient.js gives VOICE_SIGNALING_URL
# precedence when both are present). This makes the UI flip a true mode-switch.
cfg.pop("VOICE_SIGNALING_URL", None)
with open(out_path, "w") as f:
    f.write("window.__APP_CONFIG__ = " + json.dumps(cfg) + ";\n")
print("    config.js VOICE_START_URL =", voice_url, "(removed VOICE_SIGNALING_URL)")
PY

ZIP="$(mktemp -d)/ui.zip"
( cd "$BUILD" && zip -r -q "$ZIP" . -x "*.DS_Store" )
python3 - "$APP_ID" "$BRANCH" "$ZIP" "$REGION" <<'PY'
import boto3, sys, urllib.request, time
app, branch, zip_path = sys.argv[1], sys.argv[2], sys.argv[3]
region = sys.argv[4] if len(sys.argv) > 4 else None
# Explicit region: this script may run without AWS_DEFAULT_REGION exported, and an
# unregioned amplify client raises NoRegionError.
c = boto3.client("amplify", region_name=region)
r = c.create_deployment(appId=app, branchName=branch)
with open(zip_path, "rb") as f:
    urllib.request.urlopen(urllib.request.Request(r["zipUploadUrl"], data=f.read(), method="PUT", headers={"Content-Type": "application/zip"}))
c.start_deployment(appId=app, branchName=branch, jobId=r["jobId"])
for _ in range(40):
    time.sleep(8)
    s = c.get_job(appId=app, branchName=branch, jobId=r["jobId"])["job"]["summary"]["status"]
    if s in ("SUCCEED", "FAILED", "CANCELLED"):
        print("    UI redeploy:", s); break
PY
