#!/usr/bin/env bash
# Builds deployment zips for all Lambda functions.
#
# interceptor.zip: pure-stdlib handler, no deps.
# tools.zip: handler + retail_catalog + a *trimmed* vendored boto3/botocore
#   (only the sts + bedrock-agentcore + s3vectors + bedrock-runtime service
#   models kept, since the
#   Lambda runtime's built-in boto3 may lag behind the bedrock-agentcore
#   API surface we depend on). Trimming keeps the package well under
#   Lambda's size limits and speeds up cold start.
# webui_backend.zip: handler + chat_core (the chat loop shared with the
#   AgentCore Runtime agent, used here as the fallback) + a *trimmed*
#   vendored boto3/botocore (only
#   ssm + bedrock-runtime service models -- it talks to the Gateway over
#   plain HTTP/MCP via stdlib urllib, not boto3, so no bedrock-agentcore
#   service model is needed here).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VENV_SITE="$ROOT/.venv/lib/python3.12/site-packages"
BUILD="$ROOT/.build"
rm -rf "$BUILD"
mkdir -p "$BUILD/interceptor" "$BUILD/tools" "$BUILD/webui_backend"

echo "[build] interceptor.zip"
cp "$ROOT/lambda_src/interceptor/handler.py" "$BUILD/interceptor/"
( cd "$BUILD/interceptor" && zip -q -r9 "$BUILD/interceptor.zip" . )

vendor_boto3() {
  local dest="$1"
  for pkg in boto3 botocore s3transfer jmespath dateutil urllib3; do
    cp -r "$VENV_SITE/$pkg" "$dest/$pkg"
  done
  cp "$VENV_SITE/six.py" "$dest/six.py"
}

trim_botocore_data() {
  local dest="$1"
  shift
  local data_dir="$dest/botocore/data"
  local keep_file
  keep_file="$(mktemp)"
  printf '%s\n' "$@" > "$keep_file"
  for d in "$data_dir"/*/; do
    name="$(basename "$d")"
    if ! grep -qx "$name" "$keep_file"; then
      rm -rf "$d"
    fi
  done
  rm -f "$keep_file"
}

echo "[build] tools.zip (vendoring trimmed boto3/botocore: sts, bedrock-agentcore, s3vectors, bedrock-runtime)"
cp "$ROOT/lambda_src/tools/handler.py" "$ROOT/lambda_src/tools/retail_catalog.py" "$BUILD/tools/"
vendor_boto3 "$BUILD/tools"
trim_botocore_data "$BUILD/tools" "sts" "bedrock-agentcore" "s3vectors" "bedrock-runtime"
( cd "$BUILD/tools" && zip -q -r9 "$BUILD/tools.zip" . -x '*.dist-info/*' -x '__pycache__/*' )

echo "[build] webui_backend.zip (vendoring trimmed boto3/botocore: ssm, bedrock-runtime)"
cp "$ROOT/lambda_src/webui_backend/handler.py" "$ROOT/lambda_src/chat_core/chat_core.py" "$BUILD/webui_backend/"
vendor_boto3 "$BUILD/webui_backend"
trim_botocore_data "$BUILD/webui_backend" "ssm" "bedrock-runtime" "bedrock"
( cd "$BUILD/webui_backend" && zip -q -r9 "$BUILD/webui_backend.zip" . -x '*.dist-info/*' -x '__pycache__/*' )

echo "[build] sizes:"
du -h "$BUILD/interceptor.zip" "$BUILD/tools.zip" "$BUILD/webui_backend.zip"
