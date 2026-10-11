#!/usr/bin/env bash
# Provision the HVI media pipeline. RUNS ON THE ASTERISK INSTANCE, not locally.
#
# Pushed and invoked by scripts/72-media-deploy.sh. It is a file rather than an
# inline SSM command so that it is reviewable in a diff and re-runnable by hand
# over SSM when something needs poking at 2am.
#
# Expects, already staged in /tmp by the deploy script:
#   /tmp/media.tgz   the media/ and common/ python packages, plus web/ - the
#                    browser surface's static files, which media/bot_web.py
#                    serves from $APP_DIR/web
#   /tmp/media.env   the generated environment file
#
# Idempotent: safe to re-run. Creates the venv only if missing, and skips the
# dependency install when SKIP_PIP=1.

set -euo pipefail

APP_DIR="/opt/hvi"
SERVICE="hvi-media"
ENV_FILE="/etc/hvi/media.env"
SKIP_PIP="${SKIP_PIP:-0}"

log() { printf '[media-install] %s\n' "$*"; }

for f in /tmp/media.tgz /tmp/media.env; do
  [ -f "$f" ] || { echo "missing staged file: $f" >&2; exit 1; }
done

# ---------------------------------------------------------------------------
# Unprivileged service account
# ---------------------------------------------------------------------------
# The service listens only on loopback for Asterisk's AudioSocket, so it has no
# need for root. This host has a public IP and an open SIP port; the media
# process is the thing parsing audio off the PSTN, so it gets the least it can.
if ! id -u hvi >/dev/null 2>&1; then
  log "creating system user hvi"
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin hvi
fi

# ---------------------------------------------------------------------------
# Code
# ---------------------------------------------------------------------------
log "unpacking application to $APP_DIR"
mkdir -p "$APP_DIR"
# Remove old python packages so a deleted module does not linger and get
# imported. The venv is untouched, which is what keeps redeploys fast.
#
# web/ is cleared for the same reason but a sharper one: a stale asset here is
# not dead code, it is the code that RUNS. The box served a two-week-old app.js
# that had been hand-copied in, and because the page still rendered the agent's
# side of the conversation the staleness looked like a bug in the form pipeline.
# Replacing the directory wholesale is what makes the served page a function of
# the commit rather than of who last copied a file onto the instance.
rm -rf "$APP_DIR/media" "$APP_DIR/common" "$APP_DIR/web"
tar -xzf /tmp/media.tgz -C "$APP_DIR"
rm -f /tmp/media.tgz

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------
if ! dpkg -s python3-venv >/dev/null 2>&1; then
  log "installing python3-venv"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3-venv
fi

if [ ! -x "$APP_DIR/venv/bin/python" ]; then
  log "creating venv ($(python3 --version))"
  python3 -m venv "$APP_DIR/venv"
  "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip wheel
fi

if [ "$SKIP_PIP" != "1" ]; then
  log "installing dependencies (aarch64 wheels; no torch, Silero runs on onnxruntime)"
  # --no-cache-dir: the root volume is small and pip's cache would keep a second
  # copy of every wheel on a box that is redeployed by script anyway.
  "$APP_DIR/venv/bin/pip" install --no-cache-dir -r "$APP_DIR/media/requirements.txt"
else
  log "SKIP_PIP=1, leaving dependencies as they are"
fi

# ---------------------------------------------------------------------------
# Environment file
# ---------------------------------------------------------------------------
log "installing $ENV_FILE"
mkdir -p "$(dirname "$ENV_FILE")"
mv /tmp/media.env "$ENV_FILE"
# Readable by the service account, not world-readable: it names the agent
# runtime. Nothing secret lives here (the instance uses its role, not keys) but
# there is no reason to publish the topology either.
chown root:hvi "$ENV_FILE"
chmod 0640 "$ENV_FILE"

chown -R hvi:hvi "$APP_DIR"

# Shared handoff directory: Asterisk writes the caller's number here keyed by the
# AudioSocket UUID, and the media process reads it. Asterisk owns it so it can
# write; group hvi so the media user can read. 2770 sets the setgid bit, so files
# created by Asterisk inherit group hvi rather than group asterisk - without that
# the media process could list the directory and not open anything in it.
install -d -o asterisk -g hvi -m 2770 /var/lib/hvi/calls
# pending/ holds outbound tickets written by the dialler before the call is
# answered; the dialplan renames one into calls/ at answer time.
install -d -o asterisk -g hvi -m 2770 /var/lib/hvi/pending
# Local disk tier for the prompt cache. Owned by hvi because the media process
# writes into it: an entry is fetched from S3 once and served from disk after.
#
# Without this directory the cache silently drops to S3-only. PromptCache treats
# an unusable directory as "run without disk" rather than an error, which is the
# right behaviour for a live call but means a missing mkdir turns every fixed
# prompt into an S3 GET on the critical path instead of a 0.5ms local read - and
# nothing in the logs says so.
install -d -o hvi -g hvi -m 0755 /var/lib/hvi/prompt-cache
log "shared call directory: $(stat -c '%U:%G %a' /var/lib/hvi/calls) /var/lib/hvi/calls"
log "pending ticket dir  : $(stat -c '%U:%G %a' /var/lib/hvi/pending) /var/lib/hvi/pending"
log "prompt cache dir    : $(stat -c '%U:%G %a' /var/lib/hvi/prompt-cache) /var/lib/hvi/prompt-cache"

# ---------------------------------------------------------------------------
# Preflight, before we let systemd near it
# ---------------------------------------------------------------------------
log "running selfcheck"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
cd "$APP_DIR"
if ! sudo -u hvi --preserve-env "$APP_DIR/venv/bin/python" -m media.selfcheck; then
  echo "[media-install] selfcheck FAILED - not starting the service" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# systemd unit
# ---------------------------------------------------------------------------
log "writing /etc/systemd/system/${SERVICE}.service"
cat > "/etc/systemd/system/${SERVICE}.service" <<'UNIT'
[Unit]
Description=HVI call media pipeline (AudioSocket -> VAD -> STT -> agent -> TTS)
Documentation=file:///opt/hvi/media/bot.py
After=network-online.target
Wants=network-online.target
# Ordered after Asterisk so a simultaneous boot does not produce a burst of
# connection-refused retries. Deliberately not a Requires: the media service
# should come up and wait for calls even if Asterisk is down for maintenance.
After=asterisk.service

[Service]
Type=simple
User=hvi
Group=hvi
WorkingDirectory=/opt/hvi
EnvironmentFile=/etc/hvi/media.env
ExecStart=/opt/hvi/venv/bin/python -m media.bot
Restart=always
RestartSec=2
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hvi-media

# Hardening. This process parses audio arriving from the public telephone
# network, so its write access is confined to the two directories it genuinely
# owns and nothing else.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
# /var/lib/hvi MUST be writable, and leaving it out silently disabled a feature
# rather than failing.
#
# ProtectSystem=strict mounts the entire filesystem read-only except what is
# listed here. With only /opt/hvi granted, every write under /var/lib/hvi failed
# with EROFS - and the two callers that mattered both swallowed the error:
#
#   the prompt cache wrote through to /var/lib/hvi/prompt-cache on every get(),
#   so it logged "cached N bytes for next time", persisted nothing, and reported
#   a miss on the next call. The directory held 0 files while the log claimed
#   otherwise. Every single utterance therefore paid full GPU synthesis - about
#   2.9s of dead air each, on a phone call, which is what the caller heard as
#   silence. The S3 tier hid it further by making the feature look alive.
#
#   the inbound audio capture used for diagnostics failed per frame.
#
# Neither surfaced as an error because both are non-fatal by design: a cache that
# cannot persist still answers, and diagnostics must never break a call. That is
# correct behaviour and exactly why the sandbox has to be right - the failure had
# no symptom except latency, which got attributed to the model instead.
ReadWritePaths=/opt/hvi /var/lib/hvi
RuntimeDirectory=hvi-media

[Install]
WantedBy=multi-user.target
UNIT

log "starting ${SERVICE}"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"

# Give it a moment to either bind the port or die, then report honestly.
sleep 4
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "[media-install] service is NOT active; recent log follows" >&2
  journalctl -u "$SERVICE" -n 40 --no-pager >&2
  exit 1
fi

log "active. listening sockets:"
ss -ltnp 2>/dev/null | grep -E ':(8090)' || log "WARNING: nothing listening on 8090"

# ---------------------------------------------------------------------------
# The browser surface, if this box is also serving it
# ---------------------------------------------------------------------------
# hvi-web (scripts/36-web-cloudfront.sh) runs media.bot_web out of the SAME
# $APP_DIR that was just replaced, so it is running the old modules until it is
# restarted. Nothing here restarted it, and `Requires=hvi-media.service` does not
# help: a restart of hvi-media does not propagate to the units that require it.
#
# That is how the box ended up running a bot_web.py that asked its bridge for
# form state alongside an agent_bridge.py, six days older, that had no such
# parameter. The call did not fail - AgentCoreBridge takes **kwargs and forwards
# them to FrameProcessor, which ignores what it does not recognise - so on_reply
# was accepted and dropped on the floor. No hvi-turn was ever written to the data
# channel, and the surface lost the caller's transcript and the whole
# questionnaire while continuing to answer the phone perfectly.
#
# Restarted only if the unit is actually installed, so the phone-only boxes that
# never ran 36-web-cloudfront.sh are unaffected.
if systemctl list-unit-files hvi-web.service >/dev/null 2>&1 \
   && systemctl cat hvi-web.service >/dev/null 2>&1; then
  log "restarting hvi-web so the surface picks up the new modules and assets"
  systemctl restart hvi-web
  # 8s, not 3s. bot_web imports pipecat and builds the SmallWebRTC transport
  # before uvicorn binds, which measured ~3.2s on this box - so a 3s sleep
  # reported "active but nothing listening on 8095" for a service that was
  # perfectly healthy and bound a fraction of a second later. A check that cries
  # wolf on a good deploy is worse than no check, because the next real failure
  # gets waved past.
  sleep 8
  if systemctl is-active --quiet hvi-web; then
    log "hvi-web active"
    ss -ltn 2>/dev/null | grep -qE ':8095' \
      || log "WARNING: hvi-web is active but nothing is listening on 8095"
  else
    # Not fatal to this installer: the phone leg is up and healthy. But say it
    # loudly, because the symptom on the browser side is a 502 from CloudFront.
    echo "[media-install] WARNING: hvi-web failed to start; recent log follows" >&2
    journalctl -u hvi-web -n 30 --no-pager >&2
  fi
else
  log "no hvi-web unit on this host; skipping the browser surface"
fi

log "done"
