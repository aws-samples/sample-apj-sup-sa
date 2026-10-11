#!/usr/bin/env bash
# Prove two-way RTP on a real call, with no bot in the path.
#
# DO THIS BEFORE THE FIRST BOT CALL. An EC2 instance never sees its Elastic IP on
# its NIC, so if Asterisk is not explicitly told to advertise the EIP it puts a
# 10.42.x.x address in the SDP, Chime sends RTP to an unroutable host, and the
# call connects with total silence. That is indistinguishable from a broken bot
# if the bot is the first thing you test. Ten minutes here saves an hour of
# debugging the wrong layer.
#
# Usage
#   bash scripts/73-echo-test.sh install   # swap in the echo dialplan
#   bash scripts/73-echo-test.sh status    # RTP counters + recent Asterisk log
#   bash scripts/73-echo-test.sh restore   # put the bot dialplan back
#
# The test call is INBOUND, which is the useful property: it needs no phone
# number from anyone, because you dial the toll-free DID from your own phone.

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

R="$DATA_REGION"
MODE="${1:-install}"
BAK="/etc/asterisk/extensions.conf.hvi-bak"
INBOUND_DID="${INBOUND_DID:-+18005550199}"
SERVICE="${PREFIX}-media"

IID="${ASTERISK_INSTANCE_ID:-}"
if [ -z "$IID" ]; then
  IID=$(aws ec2 describe-instances --region "$R" \
    --filters "Name=tag:Name,Values=${PREFIX}-asterisk" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[0].InstanceId' --output text)
fi
[ -n "$IID" ] && [ "$IID" != "None" ] || { hvi_err "no running ${PREFIX}-asterisk instance"; exit 1; }

case "$MODE" in

install)
  hvi_log "installing the echo dialplan on $IID"
  bash scripts/ssm-put.sh scripts/remote/extensions-echotest.conf \
    /tmp/extensions-echotest.conf "$IID"

  # Back up once and only once. A second install must not overwrite the backup
  # with the echo dialplan - that would lose the real one.
  bash scripts/ssm-run.sh "set -euo pipefail
if [ ! -f ${BAK} ]; then
  cp /etc/asterisk/extensions.conf ${BAK}
  echo 'backed up real dialplan -> ${BAK}'
else
  echo 'backup already exists at ${BAK}, keeping it'
fi
install -o asterisk -g asterisk -m 0640 /tmp/extensions-echotest.conf /etc/asterisk/extensions.conf
rm -f /tmp/extensions-echotest.conf

# Asterisk ships logging at notice,warning,error. Verbose is off, so both the
# dialplan's Verbose() markers AND the RTP debug output would go nowhere and the
# test would produce no evidence at all. Turn verbose on in the log file before
# the call, not after. Idempotent.
if ! grep -qE '^messages.log =>.*verbose' /etc/asterisk/logger.conf; then
  sed -i 's/^messages.log => notice,warning,error/messages.log => notice,warning,error,verbose/' /etc/asterisk/logger.conf
  asterisk -rx 'logger reload' >/dev/null
  echo 'enabled verbose in messages.log'
fi
asterisk -rx 'core set verbose 3' >/dev/null

asterisk -rx 'dialplan reload'
# RTP debug prints the peer address of every packet, which is precisely the
# NAT-trap question: is Asterisk sending to a routable address or to 10.42.x.x?
asterisk -rx 'rtp set debug on'
echo '--- active dialplan ---'
asterisk -rx 'dialplan show from-chime'" "$IID"

  hvi_log ""
  hvi_log "=========================================================="
  hvi_log " CALL THIS NUMBER FROM ANY PHONE:  $INBOUND_DID"
  hvi_log "=========================================================="
  hvi_log ""
  hvi_log "expect, in order:"
  hvi_log "  1. a short congratulations recording   (downstream RTP works)"
  hvi_log "  2. 'you will now hear your voice'      "
  hvi_log "  3. your own voice echoed back          (upstream RTP works)"
  hvi_log ""
  hvi_log "how to read a failure:"
  hvi_log "  total silence      -> SDP is advertising a private IP, or the SG is"
  hvi_log "                        dropping RTP. Check external_media_address."
  hvi_log "  recording, no echo -> inbound RTP not arriving; check SG media ranges"
  hvi_log "  echo, no recording -> codec negotiation; check ulaw/alaw"
  hvi_log ""
  hvi_log "then run:  bash scripts/73-echo-test.sh status"
  hvi_log "and after: bash scripts/73-echo-test.sh restore"
  ;;

status)
  # Packet counters are the objective evidence. Ears can be fooled by a bad
  # handset or a muted phone; RTP counters cannot. Both directions must be
  # non-zero for the media path to be proven.
  hvi_log "reading the evidence from the last call"
  bash scripts/ssm-put.sh scripts/remote/echo-verdict.sh  /tmp/echo-verdict.sh  "$IID"
  bash scripts/ssm-put.sh scripts/remote/audio-energy.py  /tmp/audio-energy.py  "$IID"
  bash scripts/ssm-run.sh 'bash /tmp/echo-verdict.sh' "$IID"
  ;;

restore)
  hvi_log "restoring the production dialplan"
  bash scripts/ssm-run.sh "set -euo pipefail
if [ ! -f ${BAK} ]; then
  echo 'ERROR: no backup at ${BAK}; re-run scripts/71-asterisk-config.sh' >&2
  exit 1
fi
install -o asterisk -g asterisk -m 0640 ${BAK} /etc/asterisk/extensions.conf
rm -f ${BAK}
asterisk -rx 'dialplan reload'
asterisk -rx 'rtp set debug off'
echo '--- restored dialplan ---'
asterisk -rx 'dialplan show from-chime'
echo '--- media service ---'
systemctl is-active ${SERVICE}" "$IID"

  hvi_log "restored. AudioSocket path is live again."
  ;;

*)
  hvi_err "unknown mode: $MODE (expected install|status|restore)"
  exit 1
  ;;
esac
