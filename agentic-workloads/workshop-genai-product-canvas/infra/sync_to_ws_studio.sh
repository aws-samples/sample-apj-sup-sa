#!/usr/bin/env bash
# Keep the Workshop Studio copy of the CloudFormation templates in sync with the
# source of truth, which is this infra/ directory:
#   code-editor.yaml - Part 0: the browser Code Editor with Claude Code on Bedrock
#   template.yaml    - Parts 1-2: the tool backend, datasets and AgentCore Gateway
#
# Workshop Studio builds from its own content repo and packages only what is
# committed there, so that repo needs REAL copies - a symlink pointing outside it
# would not be included at publish time. Point WS_STUDIO_ROOT at that checkout, or
# keep it as a sibling directory named ws-studio.
#
# Run this after editing either template or any embedded asset:
#   bash infra/sync_to_ws_studio.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"        # the sample's infra/

# 1. Re-embed assets (datasets + Lambda code) into template.yaml so the copy is
#    always current. bundle_assets.py is idempotent. code-editor.yaml embeds
#    nothing - its bootstrap is inline UserData - so it is copied as-is.
python3 "${HERE}/bundle_assets.py"

# 2. Copy both templates into the ws-studio static folder - if there is one.
#    Only the two-repo layout above has a ws-studio sibling; a standalone copy of
#    this sample does not, and re-embedding the assets is the whole job there.
WS_STUDIO_ROOT="${WS_STUDIO_ROOT:-${HERE}/../../ws-studio}"
if [ ! -d "${WS_STUDIO_ROOT}" ]; then
  echo "No Workshop Studio content repo at ${WS_STUDIO_ROOT} - embedded assets"
  echo "refreshed in template.yaml, nothing copied. Set WS_STUDIO_ROOT to point at"
  echo "one if you keep it somewhere else."
  exit 0
fi
WS_STUDIO_INFRA="$(cd "${WS_STUDIO_ROOT}" && pwd)/static/infra"
mkdir -p "${WS_STUDIO_INFRA}"
for tpl in code-editor.yaml template.yaml; do
  cp "${HERE}/${tpl}" "${WS_STUDIO_INFRA}/${tpl}"
  echo "Synced ${tpl} -> ${WS_STUDIO_INFRA}/${tpl}"
done
