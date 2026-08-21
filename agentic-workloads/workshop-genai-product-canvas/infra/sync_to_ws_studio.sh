#!/usr/bin/env bash
# Keep the Workshop Studio copy of the CloudFormation template in sync with the
# source of truth in github-samples/infra.
#
# github-samples/infra/template.yaml is the single source. ws-studio is a
# separate repo that Workshop Studio packages on its own, so it needs a REAL
# copy of the template (a symlink pointing outside the repo would not be
# included at publish time).
#
# Run this after editing the template or any embedded asset:
#   bash github-samples/infra/sync_to_ws_studio.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"        # .../github-samples/infra
WS_STUDIO_INFRA="$(cd "${HERE}/../../ws-studio" && pwd)/static/infra"

# 1. Re-embed assets (datasets + Lambda code) into the template so the copy is
#    always current. bundle_assets.py is idempotent.
python3 "${HERE}/bundle_assets.py"

# 2. Copy the template into the ws-studio static folder.
mkdir -p "${WS_STUDIO_INFRA}"
cp "${HERE}/template.yaml" "${WS_STUDIO_INFRA}/template.yaml"

echo "Synced template.yaml -> ${WS_STUDIO_INFRA}/template.yaml"
