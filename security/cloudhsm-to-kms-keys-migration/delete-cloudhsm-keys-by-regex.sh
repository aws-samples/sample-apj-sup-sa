#!/bin/bash

# Deletes CloudHSM keys matching a regex pattern on their labels.
# Uses key-reference for efficient direct deletion without duplicate lookups.
#
# Usage:
#   ./delete-cloudhsm-keys-by-regex.sh [PATTERN]
#
# Arguments:
#   PATTERN      - Regex pattern to match key labels (default: ".*" matches all)
#
# Examples:
#   ./delete-cloudhsm-keys-by-regex.sh                  # Delete all keys
#   ./delete-cloudhsm-keys-by-regex.sh "^test-*"       # Delete keys starting with "test-"
#   ./delete-cloudhsm-keys-by-regex.sh ".*-backup$"    # Delete keys ending with "-backup"
#   ./delete-cloudhsm-keys-by-regex.sh "^mvgx-*"       # Delete keys starting with "mvgx-"

set -eo pipefail

# Default pattern to match all labels
default_pattern=".*"
# Get label pattern from command line argument, use default if no argument provided
label_pattern="${1:-$default_pattern}"
MAX_ITEMS=50
STARTING_TOKEN=""

# First, collect all matching key references with pagination
echo "Step 1: Collecting all key references matching pattern: $label_pattern"
all_key_refs=""

while true; do
  # Build the base command
  CMD="cloudhsm-cli key list --max-items $MAX_ITEMS --verbose"
  if [ -n "$STARTING_TOKEN" ]; then
    CMD="$CMD --starting-token $STARTING_TOKEN"
  fi

  # Run the command and capture output
  OUTPUT=$($CMD)

  # Extract key references from keys with matching labels
  batch_refs=$(echo "$OUTPUT" | jq -r --arg pattern "$label_pattern" '
    .data.matched_keys[] | 
    select(.attributes.label // "" | test($pattern)) | 
    ."key-reference"
  ')
  
  if [ -n "$batch_refs" ]; then
    all_key_refs="$all_key_refs$batch_refs"$'\n'
  fi

  # Get the next_token from the output
  NEXT_TOKEN=$(echo "$OUTPUT" | jq -r '.data.next_token // empty')

  # Stop if there is no next_token
  if [ -z "$NEXT_TOKEN" ]; then
    break
  fi

  STARTING_TOKEN="$NEXT_TOKEN"
done

# Remove trailing newline and display found key references
key_refs=$(echo "$all_key_refs" | sed '/^$/d')

if [ -z "$key_refs" ]; then
  echo "No keys found matching pattern: $label_pattern"
  exit 0
fi

echo "Found $(echo "$key_refs" | wc -l) keys to delete"

# Now delete keys directly using key references
echo "Step 2: Deleting collected keys ..."
while read -r key_ref; do
  echo "Deleting key: $key_ref"
  cloudhsm-cli key delete --filter key-reference="$key_ref"
done <<< "$key_refs"

echo "Deletion complete!"

