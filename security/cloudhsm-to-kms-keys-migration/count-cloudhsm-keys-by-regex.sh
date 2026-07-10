#!/bin/bash

# Counts CloudHSM keys matching a regex pattern on their labels.
# Paginates through all keys in CloudHSM, filters by configurable label pattern,
# and displays count statistics grouped by key type and class.
#
# Usage:
#   ./count-cloudhsm-keys-by-regex.sh [PATTERN]
#
# Arguments:
#   PATTERN      - Regex pattern to match key labels (default: ".*" matches all)
#
# Examples:
#   ./count-cloudhsm-keys-by-regex.sh                  # Count all keys
#   ./count-cloudhsm-keys-by-regex.sh "test.*"        # Count keys starting with "test"
#   ./count-cloudhsm-keys-by-regex.sh ".*-backup$"    # Count keys ending with "-backup"

set -eo pipefail
export PATH=$PATH:/opt/cloudhsm/bin

# Default pattern to match all labels
default_pattern=".*"
# Get label pattern from command line argument, use default if no argument provided
label_pattern="${1:-$default_pattern}"   

MAX_ITEMS=50
STARTING_TOKEN=""

echo "Counting keys matching pattern: $label_pattern"
echo "=========================================="

# Step 1: List all keys matching the label pattern
echo "Step 1: Collecting all keys matching pattern '$label_pattern'..."
all_matching_keys=""

while true; do
  # Build the base command
  CMD="cloudhsm-cli key list --max-items $MAX_ITEMS --verbose"
  if [ -n "$STARTING_TOKEN" ]; then
    CMD="$CMD --starting-token $STARTING_TOKEN"
  fi

  # Run the command and capture output
  OUTPUT=$($CMD)

  # Filter keys that match the pattern and append to collection
  keys_batch=$(echo "$OUTPUT" | jq -c --arg pattern "$label_pattern" '
    .data.matched_keys[] | select(.attributes.label // "" | test($pattern))
  ')
  
  if [ -n "$keys_batch" ]; then
    all_matching_keys="$all_matching_keys$keys_batch"$'\n'
  fi

  # Get the next_token from the output
  NEXT_TOKEN=$(echo "$OUTPUT" | jq -r '.data.next_token // empty')

  # Stop if there is no next_token
  if [ -z "$NEXT_TOKEN" ]; then
    break
  fi

  STARTING_TOKEN="$NEXT_TOKEN"
done

# Step 2: Count keys by type and class
echo "Step 2: Counting keys by type and class..."
echo

if [ -n "$all_matching_keys" ]; then
  total_matching=$(echo "$all_matching_keys" | grep -c '^{' || echo "0")
  echo "Found $total_matching keys matching the pattern"
  echo
  
  echo "Key Type        | Class       | Count"
  echo "----------------|-------------|-------"
  
  # Use jq to extract key-type and class, then count
  echo "$all_matching_keys" | jq -r '[(.attributes."key-type" // "unknown"), (.attributes.class // "unknown")] | @tsv' | \
    sort | uniq -c | while read count key_type key_class; do
      printf "%-15s | %-11s | %d\n" "$key_type" "$key_class" "$count"
    done
  
  echo "----------------|-------------|-------"
  printf "%-15s | %-11s | %d\n" "TOTAL" "" "$total_matching"
else
  echo "No keys found matching pattern '$label_pattern'"
fi