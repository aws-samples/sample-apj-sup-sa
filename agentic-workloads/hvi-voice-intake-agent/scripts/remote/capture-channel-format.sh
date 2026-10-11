#!/usr/bin/env bash
# Capture the ACTUAL audio formats Asterisk is using on a live call.
#
# Why this exists: the dialplan tried to force slin with
#   Set(CHANNEL(audioreadformat)=slin)
#   Set(CHANNEL(audiowriteformat)=slin)
# and both are READ-ONLY in Asterisk, so both were silently ignored - Asterisk
# logged "Unknown or unavailable item requested" on every call and carried on with
# whatever the SIP leg negotiated. Which format that actually is decides whether
# our 16-bit 8kHz writes are being interpreted correctly, and guessing it from
# symptoms has already cost two wrong diagnoses.
#
# Polls while a call is up and records the native/read/write formats plus the
# codec on the PJSIP leg, then prints what it saw.
#
# Usage (on the Asterisk host):  bash capture-channel-format.sh [seconds]

set -uo pipefail
DURATION="${1:-45}"
OUT=/tmp/hvi-channel-format.txt
: > "$OUT"

echo "watching for a channel for up to ${DURATION}s..."
end=$(( $(date +%s) + DURATION ))
seen=0

while [ "$(date +%s)" -lt "$end" ]; do
  # Concise output is stable and parseable; verbose adds the format columns.
  chans=$(asterisk -rx "core show channels concise" 2>/dev/null | grep -v '^$' || true)
  if [ -n "$chans" ]; then
    seen=1
    while IFS= read -r line; do
      name="${line%%!*}"
      [ -z "$name" ] && continue
      {
        echo "=== $(date '+%H:%M:%S') channel: $name"
        asterisk -rx "core show channel $name" 2>/dev/null \
          | grep -iE "NativeFormats|WriteFormat|ReadFormat|WriteTranscode|ReadTranscode|Codec"
      } >> "$OUT"
    done <<< "$chans"
    # One good sample is enough; formats do not change mid-call.
    grep -q "WriteFormat" "$OUT" && break
  fi
  sleep 1
done

echo "--- captured ---"
if [ "$seen" -eq 0 ]; then
  echo "NO CHANNEL APPEARED. Place the call while this is running."
  exit 1
fi
sort -u "$OUT" | head -40
