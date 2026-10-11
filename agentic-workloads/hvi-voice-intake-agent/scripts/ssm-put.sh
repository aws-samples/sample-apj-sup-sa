#!/usr/bin/env bash
# Copy a local file to the Asterisk instance over SSM, in verified chunks.
#
# There is no SSH to this box (no port 22 ingress, deliberately), and no S3
# staging bucket, so code has to travel through the SSM control plane. A single
# RunShellScript command cannot carry an arbitrary payload, so this base64-encodes
# the file, splits it, appends the pieces, then decodes and checks sha256 on the
# instance. The hash check is the point: a truncated chunk would otherwise land as
# a corrupt tarball and fail much later with a confusing error.
#
# Usage
#   bash scripts/ssm-put.sh <local-file> <remote-path> [instance-id]
#
# Example
#   bash scripts/ssm-put.sh build/media.tgz /tmp/media.tgz

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

LOCAL="${1:?usage: ssm-put.sh <local-file> <remote-path> [instance-id]}"
REMOTE="${2:?usage: ssm-put.sh <local-file> <remote-path> [instance-id]}"
IID="${3:-${ASTERISK_INSTANCE_ID:-}}"
R="$DATA_REGION"

# Chunk size in base64 characters. SSM accepts more, but the whole command is
# JSON-encoded and counted against a document-parameter limit, so this stays
# conservative: a failed 24KB chunk is cheap to retry.
CHUNK="${SSM_PUT_CHUNK:-24000}"

[ -f "$LOCAL" ] || { hvi_err "no such file: $LOCAL"; exit 1; }

if [ -z "$IID" ]; then
  IID=$(aws ec2 describe-instances --region "$R" \
    --filters "Name=tag:Name,Values=${PREFIX}-asterisk" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[0].InstanceId' --output text)
fi
[ -n "$IID" ] && [ "$IID" != "None" ] || { hvi_err "could not resolve instance id"; exit 1; }

# Run one command on the instance and fail loudly with its stderr.
_ssm() {
  local cmd="$1" cid status out
  cid=$(aws ssm send-command --region "$R" \
    --instance-ids "$IID" \
    --document-name "AWS-RunShellScript" \
    --parameters "$(jq -nc --arg c "$cmd" '{commands: [$c], executionTimeout: ["600"]}')" \
    --query 'Command.CommandId' --output text)

  for _ in $(seq 1 150); do
    status=$(aws ssm get-command-invocation --region "$R" \
      --command-id "$cid" --instance-id "$IID" \
      --query 'Status' --output text 2>/dev/null || echo Pending)
    case "$status" in Success|Failed|Cancelled|TimedOut) break ;; esac
    sleep 2
  done

  out=$(aws ssm get-command-invocation --region "$R" \
    --command-id "$cid" --instance-id "$IID" --output json)
  if [ "$(jq -r '.Status' <<<"$out")" != "Success" ]; then
    hvi_err "remote command failed: $(jq -r '.Status' <<<"$out")"
    jq -r '.StandardErrorContent' <<<"$out" >&2
    return 1
  fi
  jq -r '.StandardOutputContent' <<<"$out"
}

STAGE="/tmp/.ssm-put.$$.b64"
SUM_LOCAL=$(shasum -a 256 "$LOCAL" | awk '{print $1}')
SIZE=$(wc -c < "$LOCAL" | tr -d ' ')

hvi_log "pushing $LOCAL -> $IID:$REMOTE (${SIZE} bytes, sha256 ${SUM_LOCAL:0:12}...)"

# base64 with no line wrapping. BSD base64 (macOS) has no -w, GNU needs -w0, so
# strip newlines afterwards instead of guessing which one is installed.
B64=$(base64 < "$LOCAL" | tr -d '\n')
TOTAL=${#B64}
NCHUNKS=$(( (TOTAL + CHUNK - 1) / CHUNK ))

i=0; off=0
while [ "$off" -lt "$TOTAL" ]; do
  i=$((i + 1))
  piece="${B64:$off:$CHUNK}"
  if [ "$i" -eq 1 ]; then
    # First chunk truncates, so a re-run does not append to a stale staging file.
    _ssm "printf '%s' '$piece' > $STAGE" >/dev/null
  else
    _ssm "printf '%s' '$piece' >> $STAGE" >/dev/null
  fi
  hvi_log "  chunk $i/$NCHUNKS"
  off=$((off + CHUNK))
done

hvi_log "decoding and verifying on instance"
REMOTE_DIR=$(dirname "$REMOTE")
OUT=$(_ssm "set -e
mkdir -p '$REMOTE_DIR'
base64 -d '$STAGE' > '$REMOTE'
rm -f '$STAGE'
sha256sum '$REMOTE' | awk '{print \$1}'")

SUM_REMOTE=$(echo "$OUT" | tr -d '[:space:]')
if [ "$SUM_REMOTE" != "$SUM_LOCAL" ]; then
  hvi_err "checksum mismatch after transfer"
  hvi_err "  local:  $SUM_LOCAL"
  hvi_err "  remote: $SUM_REMOTE"
  exit 1
fi

hvi_log "verified: $REMOTE matches local sha256"
