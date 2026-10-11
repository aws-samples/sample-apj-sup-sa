#!/usr/bin/env bash
# Decide, from RTP packet counters, whether two-way audio actually worked.
# RUNS ON THE ASTERISK INSTANCE. Invoked by scripts/73-echo-test.sh status.
#
# Why counters and not ears: a muted handset, a bad speaker or a carrier
# announcement can all make a working call sound broken, and a caller saying "I
# heard nothing" does not say which direction failed. Asterisk's RTP debug prints
# the peer address of every packet it sends and receives, so the log answers both
# questions exactly - did packets flow each way, and to what address.

set -uo pipefail

LOG="/var/log/asterisk/messages.log"
CONF="/etc/asterisk/pjsip.conf"

echo "=== trunk state ==="
asterisk -rx 'pjsip show aors' 2>/dev/null | grep -iE 'contact|aor' | head -6

echo
echo "=== advertised addresses (must be the EIP, never 10.42.x.x) ==="
grep -E 'external_(signaling|media)_address|local_net' "$CONF" || echo "(not set - THIS IS THE NAT TRAP)"

echo
echo "=== CDR: the authoritative record of what ran ==="
# The CDR is the thing to trust about call flow. Verbose log markers depend on the
# global verbose level, which is easy to get wrong, and an earlier version of this
# script concluded "the call never reached the dialplan" while 6723 RTP packets
# were sitting in the same log. lastapp is what actually executed.
CDR="/var/log/asterisk/cdr-csv/Master.csv"
if [ -f "$CDR" ] && [ -s "$CDR" ]; then
  # The CDR mixes quoted strings with UNQUOTED numerics:
  #   "","+1833...","+1855...","from-chime",...,"Echo","","start","answer","end",135,135,"ANSWERED",...
  # so splitting on '","' does not align - duration, billsec and disposition all
  # land in one field. An earlier version printed the uniqueid as the duration.
  # Pull the quoted fields positionally and the numerics by pattern instead.
  while IFS= read -r line; do
    src=$(printf '%s' "$line" | awk -F'","' '{print $2}')
    dst=$(printf '%s' "$line" | awk -F'","' '{print $3}')
    ctx=$(printf '%s' "$line" | awk -F'","' '{print $4}')
    app=$(printf '%s' "$line" | awk -F'","' '{print $8}')
    # ,<duration>,<billsec>,"<DISPOSITION>"
    nums=$(printf '%s' "$line" | grep -oE ',[0-9]+,[0-9]+,"[A-Z ]+"' | head -1)
    dur=$(printf '%s' "$nums" | cut -d, -f2)
    disp=$(printf '%s' "$nums" | grep -oE '"[A-Z ]+"' | tr -d '"')
    printf '  %s -> %s  ctx=%s  lastapp=%s  dur=%ss  %s\n' \
      "$src" "$dst" "$ctx" "$app" "${dur:-?}" "${disp:-?}"
  done < <(tail -3 "$CDR")
else
  echo "  (no CDR yet at $CDR)"
fi

echo
echo "=== dialplan progress markers ==="
if [ -f "$LOG" ] && grep -qiE 'echotest' "$LOG"; then
  grep -iE 'echotest' "$LOG" | tail -8
else
  echo "  (no markers; judge flow from the CDR above, not from this)"
fi

echo
echo "=== RTP flow ==="
if [ ! -f "$LOG" ]; then
  echo "MISSING $LOG - cannot judge"
  exit 1
fi

# Asterisk RTP debug lines look like:
#   Sent RTP packet to      3.80.17.10:19882 (type 00, seq ...)
#   Got  RTP packet from    3.80.17.10:19882 (type 00, seq ...)
SENT=$(grep -c 'Sent RTP packet to' "$LOG" 2>/dev/null || echo 0)
GOT=$(grep -c 'Got  RTP packet from' "$LOG" 2>/dev/null || echo 0)

echo "packets sent    : $SENT"
echo "packets received: $GOT"
echo
echo "peer addresses seen:"
grep -oE '(Sent RTP packet to|Got  RTP packet from)[[:space:]]+[0-9.]+:[0-9]+' "$LOG" 2>/dev/null \
  | sed -E 's/[[:space:]]+/ /g' | sort | uniq -c | sort -rn | head -10 \
  || echo "  (none)"

# A private destination is the NAT trap, and it is worth calling out explicitly
# because everything else about the call will look healthy.
if grep -qE 'Sent RTP packet to[[:space:]]+(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)' "$LOG" 2>/dev/null; then
  echo
  echo "!! Asterisk sent RTP to a PRIVATE address. This is the EC2 NAT trap:"
  echo "!! set external_media_address to the EIP in pjsip.conf and reload."
fi

echo
echo "=== audio content (packets flowing is not the same as audio) ==="
if [ -f /tmp/audio-energy.py ]; then
  python3 /tmp/audio-energy.py /tmp/echo-in.wav /tmp/echo-out.wav 2>&1 | sed 's/^/  /'
else
  echo "  (audio-energy.py not staged)"
fi

echo
echo "=== rtp verdict ==="
if [ "$SENT" -gt 0 ] && [ "$GOT" -gt 0 ]; then
  echo "TWO-WAY RTP CONFIRMED ($SENT sent, $GOT received)."
  echo "Packets flow both ways to a routable address, so the NAT trap is absent."
  echo "Whether they CARRY audio is the energy section above."
elif [ "$SENT" -gt 0 ] && [ "$GOT" -eq 0 ]; then
  echo "DOWNSTREAM ONLY. Asterisk is sending but receiving nothing."
  echo "Inbound RTP is not arriving: check the security group media ranges."
elif [ "$SENT" -eq 0 ] && [ "$GOT" -gt 0 ]; then
  echo "UPSTREAM ONLY. Receiving but not sending - suspect codec negotiation."
elif ! grep -qi 'echotest' "$LOG" 2>/dev/null; then
  echo "NO CALL REACHED THE DIALPLAN. Nothing to judge."
  echo "The call did not arrive: check the Chime origination route and the SG on UDP 5060."
else
  echo "CALL ARRIVED BUT NO RTP AT ALL."
  echo "Signalling worked and media did not. Check external_media_address and the SG."
fi
