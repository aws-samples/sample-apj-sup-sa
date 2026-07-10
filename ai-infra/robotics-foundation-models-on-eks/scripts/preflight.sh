#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=./scripts/common.sh
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

require_cmds aws terraform kubectl helm jq

if ! command -v osmo >/dev/null 2>&1; then
  log "OSMO CLI was not found. Install the pinned OSMO CLI before running infra/kubernetes/deploy-osmo.sh or examples/run-workflow.sh."
fi

if [[ "$(version_value image_registry)" == nvcr.io* ]]; then
  load_ngc_api_key
  log "NGC API key input is available for nvcr.io image pulls"
fi

if [[ -d "${ROOT_DIR}/upstream" || -d "${ROOT_DIR}/patches/osmo" ]]; then
  die "do not vendor upstream OSMO source or local OSMO patches in this repo"
fi

aws sts get-caller-identity >/dev/null
terraform -chdir="${TF_DIR}" fmt -check -recursive
terraform -chdir="${TF_DIR}" init -backend=false -input=false
terraform -chdir="${TF_DIR}" validate
"${ROOT_DIR}/scripts/scan-public-ingress.sh"

log "preflight checks passed"
