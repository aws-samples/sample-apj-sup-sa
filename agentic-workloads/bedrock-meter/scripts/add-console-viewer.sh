#!/usr/bin/env bash
# Create a console VIEWER (read-only — can view all pages but cannot change limits or unblock)
# and add them to the console-viewers group.
#
#   scripts/add-console-viewer.sh <username> <email> [password]
#
# Password is prompted (hidden) if omitted. The user pool id is resolved from the
# BedrockMeterCoreStack output automatically (override with POOL_ID=).
set -euo pipefail
CONSOLE_TARGET_GROUP="${CONSOLE_VIEWER_GROUP:-console-viewers}" \
CONSOLE_OTHER_GROUP="${CONSOLE_ADMIN_GROUP:-console-admins}" \
  exec "$(cd "$(dirname "$0")" && pwd)/_add-console-user.sh" "$@"
