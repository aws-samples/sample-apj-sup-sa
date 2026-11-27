#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Start this repository's MCP server (mcp/dist/index.js) with Node.js, passing the arguments through: what
# e2e/mcp_live.py runs as `./mcp-server.sh login --no-browser`, `logout` and `serve`.
set -euo pipefail
SERVER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/mcp/dist/index.js"
command -v node > /dev/null || { echo "the MCP server needs Node.js 20 or later on PATH" >&2; exit 1; }
[ -f "$SERVER" ] || { echo "$SERVER is missing: cd mcp && npm ci --ignore-scripts && npm run build" >&2; exit 1; }
exec node "$SERVER" "$@"
