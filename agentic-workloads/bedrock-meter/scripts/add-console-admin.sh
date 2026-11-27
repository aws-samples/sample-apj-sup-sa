#!/usr/bin/env bash
# Create a console ADMIN (full read + write) and add them to the console-admins group.
#
#   scripts/add-console-admin.sh <username> <email> [password]
#
# Password is prompted (hidden) if omitted, so it never lands in shell history. The user pool
# id is resolved from the BedrockMeterCoreStack output automatically (override with POOL_ID=).
set -euo pipefail
CONSOLE_TARGET_GROUP="${CONSOLE_ADMIN_GROUP:-console-admins}" \
CONSOLE_OTHER_GROUP="${CONSOLE_VIEWER_GROUP:-console-viewers}" \
  exec "$(cd "$(dirname "$0")" && pwd)/_add-console-user.sh" "$@"
