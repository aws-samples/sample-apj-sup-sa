#!/bin/bash
# Package deployment artifacts for Workshop Studio
# Copies templates, Lambdas, data, and other assets into workshop/static/ and workshop/assets/
#
# Usage:
#   ./package_for_workshop.sh
#
# After running:
#   1. Sync assets to S3:  aws s3 sync ./workshop/assets s3://... (using Workshop Studio credentials)
#   2. Commit and push:    git add -A && git commit -m "Update workshop" && git push

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$INFRA_DIR")"
WORKSHOP_DIR="$PROJECT_ROOT/workshop"
STATIC_DIR="$WORKSHOP_DIR/static"
ASSETS_DIR="$WORKSHOP_DIR/assets"
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

echo "Packaging for Workshop Studio..."

# Clean previous assets (preserve static images and contentspec)
rm -rf "$ASSETS_DIR"
mkdir -p "$ASSETS_DIR/templates" "$ASSETS_DIR/lambdas" "$ASSETS_DIR/schema" "$ASSETS_DIR/data" "$ASSETS_DIR/docs" "$ASSETS_DIR/drivers" "$ASSETS_DIR/repo" "$ASSETS_DIR/lambda" "$ASSETS_DIR/sops"

# 1. Copy root template to static/
echo "Copying root template to static/..."
cp "$INFRA_DIR/stacks/main-stack.yaml" "$STATIC_DIR/main-stack.yaml"

# 2. Copy child templates to assets/templates/
echo "Copying child templates to assets/templates/..."
for tmpl in aurora-stack.yaml database-init-stack.yaml glue-stack.yaml bedrock-kb-stack.yaml \
            cognito-stack.yaml code-editor-stack.yaml agentcore-stack.yaml amplify-stack.yaml cube-stack.yaml observability-stack.yaml; do
    cp "$INFRA_DIR/stacks/$tmpl" "$ASSETS_DIR/templates/$tmpl"
done

# 3. Package Lambda ZIPs
echo "Packaging Lambdas..."

package_lambda() {
    local name=$1
    local source_dir=$2
    shift 2
    local source_files=("$@")
    [ ${#source_files[@]} -eq 0 ] && source_files=("handler.py")
    cd "$source_dir"
    zip -j "$ASSETS_DIR/lambdas/${name}.zip" "${source_files[@]}" > /dev/null
    echo "  $name"
}

package_lambda "database_init" "$INFRA_DIR/custom-resource-lambdas/database_init"
package_lambda "glue_crawler_trigger" "$INFRA_DIR/custom-resource-lambdas/glue_crawler_trigger"
package_lambda "bedrock_kb_ingestion" "$INFRA_DIR/custom-resource-lambdas/bedrock_kb_ingestion"
package_lambda "observability_setup" "$INFRA_DIR/custom-resource-lambdas/observability_setup"

# NOTE: the old agentcore_gateway Lambda (which bundled the imperative
# deploy_policy.py + deploy_interceptor.py) was retired — the Gateway, Policy
# Engine, Cedar policies and interceptor are now native CloudFormation resources
# in agentcore-topup-stack.yaml. The live interceptor zip is built from
# interceptor_lambda.py below (InterceptorLambdaKey).

# DataFoundation Lambda
echo "  datafoundation"
cd "$PROJECT_ROOT/app/agentcore_strands/tools"
zip -j "$ASSETS_DIR/lambda/prebaked_sql_toolset_lambda.zip" prebaked_sql_toolset_lambda.py > /dev/null

# --- Workshop AgentCore top-up Lambda zips ---------------------------------
# The participant deploys agentcore-topup-stack.yaml, whose Lambda resources read
# FIXED S3 keys (the template's parameter defaults). package_and_upload.sh builds
# these for demo mode with hash-suffixed keys; the workshop top-up needs them at
# stable keys. Build them here so `make deploy` finds every zip.
echo "  topup: datafoundation_lambda (DataFoundationLambdaKey)"
zip -j "$ASSETS_DIR/lambda/datafoundation_lambda.zip" prebaked_sql_toolset_lambda.py > /dev/null
echo "  topup: api_integration_toolset (ApiIntegLambdaKey)"
zip -j "$ASSETS_DIR/lambdas/api_integration_toolset.zip" api_integration_toolset_lambda.py > /dev/null
echo "  topup: custom_sql_toolset (CustomSqlLambdaKey)"
zip -j "$ASSETS_DIR/lambdas/custom_sql_toolset.zip" custom_sql_toolset_lambda.py > /dev/null
echo "  topup: gateway_interceptor (InterceptorLambdaKey)"
zip -j "$ASSETS_DIR/lambdas/gateway_interceptor.zip" "$PROJECT_ROOT/app/agentcore_strands/infra/interceptor_lambda.py" > /dev/null

# psycopg2 layer for the SQL Lambdas (NeedsPsycopg2Layer self-create path expects
# layers/psycopg2-py312.zip). Build a python3.12 manylinux wheel layer.
echo "  topup: psycopg2 layer (layers/psycopg2-py312.zip)"
mkdir -p "$ASSETS_DIR/layers"
PSY_BUILD="$TEMP_DIR/psy/python"
mkdir -p "$PSY_BUILD"
# x86_64 to match the toolset Lambdas (AWS::Lambda::Function defaults to x86_64;
# an arm64 wheel fails at import with "No module named 'psycopg2._psycopg'").
if pip3 install --quiet \
      --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
      --only-binary=:all: --target "$PSY_BUILD" "psycopg2-binary==2.9.9" 2>/dev/null; then
    (cd "$TEMP_DIR/psy" && zip -r "$ASSETS_DIR/layers/psycopg2-py312.zip" python > /dev/null)
    echo "    psycopg2 layer built"
else
    echo "    [warn] could not build psycopg2 layer locally; deploy can pass Psycopg2LayerArn instead"
fi

# PyJWT + cryptography layer for the SQL/API tool Lambdas. They verify the
# propagated Cognito access token (RS256 signature via JWKS) for defense-in-depth
# before trusting account_id/role for RLS. cryptography ships compiled wheels, so
# build for python3.12 x86_64 manylinux to match the toolset Lambdas' architecture.
echo "  topup: pyjwt layer (layers/pyjwt-py312.zip)"
PYJWT_BUILD="$TEMP_DIR/pyjwt/python"
mkdir -p "$PYJWT_BUILD"
if pip3 install --quiet \
      --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
      --only-binary=:all: --target "$PYJWT_BUILD" "PyJWT==2.10.1" "cryptography==44.0.0" 2>/dev/null; then
    (cd "$TEMP_DIR/pyjwt" && zip -r "$ASSETS_DIR/layers/pyjwt-py312.zip" python > /dev/null)
    echo "    pyjwt layer built"
else
    echo "    [warn] could not build pyjwt layer locally; deploy can pass PyJwtLayerArn instead"
fi

# Agent code ZIP for the AgentCore Runtime container build (AgentCodeS3Key default
# agent/agent_code.zip). The top-up's CodeBuild pulls this and builds the image.
echo "  topup: agent_code (AgentCodeS3Key)"
mkdir -p "$ASSETS_DIR/agent"
cd "$PROJECT_ROOT/app/agentcore_strands"
zip -r "$ASSETS_DIR/agent/agent_code.zip" \
    unicorn_rental_agent.py \
    unicorn_rental_analytics.sop.md \
    requirements.txt \
    config.env.sample \
    -x "*.pyc" "*__pycache__*" > /dev/null

# Amplify hosting (with common/ utilities)
echo "  amplify_hosting"
AMPLIFY_DIR="$INFRA_DIR/custom-resource-lambdas/amplify_hosting"
mkdir -p "$TEMP_DIR/amplify_pkg/common"
cp "$AMPLIFY_DIR/handler.py" "$TEMP_DIR/amplify_pkg/"
cp "$PROJECT_ROOT/common/amplify_utils.py" "$TEMP_DIR/amplify_pkg/common/"
cp "$PROJECT_ROOT/common/__init__.py" "$TEMP_DIR/amplify_pkg/common/"
cd "$TEMP_DIR/amplify_pkg"
zip -r "$ASSETS_DIR/lambdas/amplify_hosting.zip" . > /dev/null

# 4. Copy schema and data
echo "Copying schema and data..."
cp "$PROJECT_ROOT/dataset/schema/schema.sql" "$ASSETS_DIR/schema/"
cp "$PROJECT_ROOT/dataset/data/"*.csv "$ASSETS_DIR/data/"
cp "$PROJECT_ROOT/dataset/docs/business-context.md" "$ASSETS_DIR/docs/"

# 5. Copy SOP
echo "Copying SOP..."
cp "$PROJECT_ROOT/app/agentcore_strands/unicorn_rental_analytics.sop.md" "$ASSETS_DIR/sops/"

# 6. Download JDBC driver
echo "Downloading JDBC driver..."
DRIVER_PATH="$ASSETS_DIR/drivers/postgresql-42.7.3.jar"
if [ ! -f "$DRIVER_PATH" ]; then
    curl -sL https://jdbc.postgresql.org/download/postgresql-42.7.3.jar -o "$DRIVER_PATH"
fi

# 7. Package repo ZIP (with workshop code overlay)
echo "Packaging repo..."
cd "$PROJECT_ROOT"
zip -r "$ASSETS_DIR/repo/agentic-analytics.zip" . \
    -x "*.git*" \
    -x "*.venv*" \
    -x "*__pycache__*" \
    -x "*.pyc" \
    -x "*DS_Store*" \
    -x "*.bedrock_agentcore*" \
    -x "*.zip" \
    -x "*gateway_config.json" \
    -x "*Dockerfile" \
    -x "*.env" \
    -x "*config.env" \
    -x "*node_modules*" \
    -x "*app/ui/build*" \
    -x "*.hypothesis*" \
    -x "*changes.md" \
    -x "*lambda_tests*" \
    -x "*.pytest_cache*" \
    -x "*/tmp/*" \
    -x "*.kiro*" \
    -x "*dev/*" \
    -x "*infrastructure/*" \
    -x "*workshop/*" \
    > /dev/null

# Apply workshop code overlay (replace files with TODO versions)
if [ -d "$WORKSHOP_DIR/code" ]; then
    echo "Applying workshop code overlay..."
    cd "$WORKSHOP_DIR/code"
    zip -r "$ASSETS_DIR/repo/agentic-analytics.zip" . > /dev/null
    echo "  Overlaid $(find . -type f | wc -l | tr -d ' ') files with TODO versions"
fi

echo ""
echo "[OK] Packaging complete"
echo ""
echo "Next steps:"
echo "  1. Get Workshop Studio credentials (Repository credentials → Assets access instructions)"
echo "  2. Sync assets:  aws s3 sync $ASSETS_DIR s3://... "
echo "  3. Commit:       git add -A && git commit -m 'Update workshop assets' && git push"
