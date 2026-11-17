#!/usr/bin/env bash
# Write a copy of the state machine definition with the given execution timeout.
#
# Usage: scripts/render-state-machine.sh <timeout seconds> <output file>
#
# step-functions/migration-state-machine.json is valid JSON and holds the
# template default ("TimeoutSeconds": 3600). CloudFormation DefinitionSubstitutions
# only replace text, so they cannot produce a JSON number from a template
# parameter; scripts/deploy.sh calls this script to write the timeout it passes as
# the TimeoutSeconds stack parameter into the definition it packages. No AWS calls.
set -euo pipefail

SECONDS_VALUE="${1:-}"
OUTPUT="${2:-}"
SOURCE="$(cd "$(dirname "$0")/.." && pwd)/step-functions/migration-state-machine.json"

[[ "$SECONDS_VALUE" =~ ^[1-9][0-9]{1,7}$ ]] || { echo "timeout must be a whole number of seconds" >&2; exit 2; }
(( SECONDS_VALUE >= 60 && SECONDS_VALUE <= 31536000 )) || { echo "timeout must be 60 to 31536000 seconds" >&2; exit 2; }
[[ -n "$OUTPUT" ]] || { echo "usage: $0 <timeout seconds> <output file>" >&2; exit 2; }

# Exactly one top level "TimeoutSeconds" line (two space indent) is expected.
PATTERN='^  "TimeoutSeconds": [0-9]+,$'
[[ "$(grep -cE "$PATTERN" "$SOURCE")" == "1" ]] || { echo "unexpected TimeoutSeconds line in $SOURCE" >&2; exit 1; }
mkdir -p "$(dirname "$OUTPUT")"
sed -E "s/${PATTERN}/  \"TimeoutSeconds\": ${SECONDS_VALUE},/" "$SOURCE" > "$OUTPUT"
grep -qE "^  \"TimeoutSeconds\": ${SECONDS_VALUE},$" "$OUTPUT" || { echo "timeout was not written" >&2; exit 1; }
