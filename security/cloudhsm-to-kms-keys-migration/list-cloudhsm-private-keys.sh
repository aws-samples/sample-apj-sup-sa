
#!/bin/bash

# Lists CloudHSM private keys matching a regex pattern on their labels.
# This is a wrapper script that calls list-cloudhsm-keys.sh for backward compatibility.
#
# Usage:
#   ./list-cloudhsm-private-keys.sh [PATTERN] [OUTPUT_FILE]
#
# Arguments:
#   PATTERN      - Regex pattern to match key labels (default: ".*" matches all)
#   OUTPUT_FILE  - Output JSON file path (default: "private_keys.json")
#
# Examples:
#   ./list-cloudhsm-private-keys.sh                            # List all private keys to private_keys.json
#   ./list-cloudhsm-private-keys.sh "^mvgx-*"                  # List keys starting with "mvgx-"
#   ./list-cloudhsm-private-keys.sh "test.*" "test_keys.json"  # List test keys to custom file
#   ./list-cloudhsm-private-keys.sh ".*" "backup_keys.json"    # List all keys to backup file

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Set defaults for private keys
default_pattern=".*"
default_output_file="private_keys.json"

# Get arguments with defaults
PATTERN="${1:-$default_pattern}"
OUTPUT_FILE="${2:-$default_output_file}"

# Call the unified script: list-cloudhsm-keys.sh [KEY_CLASS] [OUTPUT_FILE] [PATTERN]
exec "$SCRIPT_DIR/list-cloudhsm-keys.sh" "private-key" "$OUTPUT_FILE" "$PATTERN"


