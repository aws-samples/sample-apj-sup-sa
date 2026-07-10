#!/bin/bash

# Lists CloudHSM keys (private or public) matching a regex pattern on their labels.
# Paginates through all keys in CloudHSM, filters by configurable label pattern,
# and outputs results to a JSON file as a proper JSON array.
#
# Usage:
#   ./list-cloudhsm-keys.sh [KEY_CLASS] [OUTPUT_FILE] [PATTERN]
#
# Arguments:
#   KEY_CLASS    - Type of keys to list: "private-key" or "public-key" (default: "private-key")
#   OUTPUT_FILE  - Output JSON file path (default: "private_keys.json")
#   PATTERN      - Regex pattern to match key labels (default: ".*" matches all)
#
# Examples:
#   ./list-cloudhsm-keys.sh                                               # List all private keys to private_keys.json
#   ./list-cloudhsm-keys.sh public-key                                    # List all public keys to private_keys.json
#   ./list-cloudhsm-keys.sh private-key "my_keys.json"                    # List private keys to my_keys.json
#   ./list-cloudhsm-keys.sh public-key "test_keys.json" "^mvgx-*"         # List public keys starting with "mvgx-" to test_keys.json
#   ./list-cloudhsm-keys.sh private-key "backup_keys.json" ".*"           # List all private keys to backup_keys.json

set -eo pipefail
export PATH=$PATH:/opt/cloudhsm/bin

# Set defaults
default_key_class="private-key"
default_pattern=".*"
default_output_file="private_keys.json"

# Get KEY_CLASS from 1st argument with default
KEY_CLASS="${1:-$default_key_class}"

# Validate KEY_CLASS
if [ "$KEY_CLASS" != "private-key" ] && [ "$KEY_CLASS" != "public-key" ]; then
    echo "Error: KEY_CLASS must be 'private-key' or 'public-key', got '$KEY_CLASS'"
    exit 1
fi

# Get output file from 2nd command line argument, use default if no argument provided
OUTPUT_FILE="${2:-$default_output_file}"
# Get label pattern from 3rd command line argument, use default if no argument provided
label_pattern="${3:-$default_pattern}"

MAX_ITEMS=10
STARTING_TOKEN=""
FIRST_RUN=true

echo "Searching for $KEY_CLASS keys matching pattern: $label_pattern"
echo "Output will be saved to: $OUTPUT_FILE"

# Empty the output file before starting
> "$OUTPUT_FILE"

while true; do
  # Build the base command
  CMD="cloudhsm-cli key list --filter attr.class=$KEY_CLASS --verbose --max-items $MAX_ITEMS"
  if [ -n "$STARTING_TOKEN" ]; then
    CMD="$CMD --starting-token $STARTING_TOKEN"
  fi

  # Run the command and capture output
  OUTPUT=$($CMD)

  # Filter keys by label pattern and save to file
  if $FIRST_RUN; then
    # For first run, create the initial structure with filtered keys
    echo "$OUTPUT" | jq --arg pattern "$label_pattern" '
      .data.matched_keys |= map(select(.attributes.label // "" | test($pattern)))
    ' > "$OUTPUT_FILE"
    FIRST_RUN=false
  else
    # For subsequent runs, append filtered keys to matched_keys array
    FILTERED_KEYS=$(echo "$OUTPUT" | jq --arg pattern "$label_pattern" '
      .data.matched_keys | map(select(.attributes.label // "" | test($pattern)))
    ')
    
    # Only update if there are filtered keys
    if [ "$(echo "$FILTERED_KEYS" | jq 'length')" -gt 0 ]; then
      jq --argjson new_keys "$FILTERED_KEYS" '.data.matched_keys += $new_keys' "$OUTPUT_FILE" > "$OUTPUT_FILE.tmp" && mv "$OUTPUT_FILE.tmp" "$OUTPUT_FILE"
    fi
  fi

  # Get the next_token from the output
  NEXT_TOKEN=$(echo "$OUTPUT" | jq -r '.data.next_token // empty')

  # Stop if there is no next_token
  if [ -z "$NEXT_TOKEN" ]; then
    break
  fi

  STARTING_TOKEN="$NEXT_TOKEN"
done

# Report results
KEY_COUNT=$(jq '.data.matched_keys | length' "$OUTPUT_FILE")
echo "Found $KEY_COUNT $KEY_CLASS keys matching pattern '$label_pattern' saved to $OUTPUT_FILE"