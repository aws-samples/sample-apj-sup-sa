#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$PIPELINE_PROJECT_DIR"
load_shared_resources

if [[ -z "${INPUT_DATA_S3_URI:-}" ]]; then
  if [[ ! -f artifacts/input/input-location.json ]]; then
    printf 'Run ./scripts/setup.sh to prepare the UCI input first.\n' >&2
    exit 1
  fi
  INPUT_DATA_S3_URI="$(uv run --no-sync python -c \
    'import json; print(json.load(open("artifacts/input/input-location.json"))["input_data_s3_uri"])')"
fi
export INPUT_DATA_S3_URI
uv run --no-sync sagemaker-xgboost-pipeline "$@"
