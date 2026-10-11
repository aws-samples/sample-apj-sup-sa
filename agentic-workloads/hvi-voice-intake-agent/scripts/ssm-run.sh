#!/usr/bin/env bash
# Run a shell command on the Asterisk box over SSM and print the output.
#
# SSM rather than SSH because the security group deliberately has no port 22
# ingress: an Asterisk host with a public IP is a target, and every open port is
# one more thing to defend. This keeps management on the AWS control plane.
#
# Usage
#   bash scripts/ssm-run.sh 'asterisk -rx "pjsip show endpoints"'
#   bash scripts/ssm-run.sh 'systemctl status asterisk --no-pager' i-0abc123

set -euo pipefail
cd "$(dirname "$0")/.."
source config/env.sh

CMD="${1:?usage: ssm-run.sh '<command>' [instance-id]}"
IID="${2:-${ASTERISK_INSTANCE_ID:-}}"
R="$DATA_REGION"

if [ -z "$IID" ]; then
  IID=$(aws ec2 describe-instances --region "$R" \
    --filters "Name=tag:Name,Values=${PREFIX}-asterisk" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[0].InstanceId' --output text)
fi

# RUN THE PAYLOAD UNDER BASH, NOT SH.
#
# AWS-RunShellScript writes the commands to a script and executes it with
# /bin/sh, which on Ubuntu is dash. That silently breaks anything bashish - the
# first symptom here was `set: Illegal option -o pipefail` failing on line 1, so
# the command aborted before doing anything. A shebang in the payload does not
# help, because SSM invokes the interpreter itself rather than the script.
#
# So the payload is base64'd and piped into bash. That also removes every layer
# of quote-mangling between here and the instance: the bytes that arrive are the
# bytes sent, whatever they contain.
CMD_B64=$(printf '%s' "$CMD" | base64 | tr -d '\n')
WRAPPED="printf '%s' '$CMD_B64' | base64 -d | bash"

CID=$(aws ssm send-command --region "$R" \
  --instance-ids "$IID" \
  --document-name "AWS-RunShellScript" \
  --parameters "$(jq -nc --arg c "$WRAPPED" '{commands: [$c], executionTimeout: ["600"]}')" \
  --query 'Command.CommandId' --output text)

# Poll rather than `aws ssm wait`, so a failed command still prints its stderr
# instead of just returning a non-zero exit with no explanation.
for _ in $(seq 1 120); do
  STATUS=$(aws ssm get-command-invocation --region "$R" \
    --command-id "$CID" --instance-id "$IID" \
    --query 'Status' --output text 2>/dev/null || echo "Pending")
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 2
done

OUT=$(aws ssm get-command-invocation --region "$R" \
  --command-id "$CID" --instance-id "$IID" --output json)

echo "--- status: $(echo "$OUT" | jq -r '.Status') ---"
echo "$OUT" | jq -r '.StandardOutputContent'
ERR=$(echo "$OUT" | jq -r '.StandardErrorContent')
if [ -n "$ERR" ] && [ "$ERR" != "null" ]; then
  echo "--- stderr ---" >&2
  echo "$ERR" >&2
fi
[ "$(echo "$OUT" | jq -r '.Status')" = "Success" ]
