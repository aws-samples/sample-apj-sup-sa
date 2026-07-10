#!/bin/bash
# Deploy workshop content to Workshop Studio
# Interactive script that packages artifacts, syncs to S3, and pushes to WS repo
#
# Usage:
#   ./deploy_to_workshop.sh          # Interactive (prompts or reads .deploy-config)
#   ./deploy_to_workshop.sh --skip-push  # Package and sync only, don't git push

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
WORKSHOP_DIR="$PROJECT_ROOT/workshop"
CONFIG_FILE="$WORKSHOP_DIR/.deploy-config"
SKIP_PUSH=false

[[ "$1" == "--skip-push" ]] && SKIP_PUSH=true

# Load or prompt for config
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
    echo "Found previous config:"
    echo "  WS repo path: $WS_REPO_PATH"
    echo "  S3 assets:    s3://$WS_S3_BUCKET/$WS_S3_PREFIX"
    read -p "Use these settings? [Y/n] " confirm
    if [[ "$confirm" =~ ^[Nn] ]]; then
        unset WS_REPO_PATH WS_S3_BUCKET WS_S3_PREFIX
    fi
fi

if [ -z "$WS_REPO_PATH" ]; then
    read -p "Workshop Studio repo path (e.g., ../ws): " WS_REPO_PATH
fi

if [ -z "$WS_S3_BUCKET" ]; then
    read -p "S3 assets bucket (e.g., ws-assets-us-east-1): " WS_S3_BUCKET
fi

if [ -z "$WS_S3_PREFIX" ]; then
    read -p "S3 assets prefix / workshop ID (e.g., 173b87a9-...): " WS_S3_PREFIX
fi

# Resolve to absolute path
WS_REPO_PATH="$(cd "$PROJECT_ROOT" && cd "$WS_REPO_PATH" 2>/dev/null && pwd)" || {
    echo "Error: WS repo path does not exist: $WS_REPO_PATH"
    exit 1
}

# Save config
cat > "$CONFIG_FILE" << EOF
WS_REPO_PATH="$WS_REPO_PATH"
WS_S3_BUCKET="$WS_S3_BUCKET"
WS_S3_PREFIX="$WS_S3_PREFIX"
EOF
echo "Config saved to $CONFIG_FILE"

# Step 1: Package
echo ""
echo "=== Step 1: Packaging artifacts ==="
"$SCRIPT_DIR/package_for_workshop.sh"

# Step 2: Copy to WS repo
echo ""
echo "=== Step 2: Copying to WS repo ==="
cp "$WORKSHOP_DIR/contentspec.yaml" "$WS_REPO_PATH/contentspec.yaml"
mkdir -p "$WS_REPO_PATH/static"
cp "$WORKSHOP_DIR/static/main-stack.yaml" "$WS_REPO_PATH/static/main-stack.yaml"
# Copy any IAM policies if they exist
for f in "$WORKSHOP_DIR/static/"*.json; do
    [ -f "$f" ] && cp "$f" "$WS_REPO_PATH/static/"
done
# Copy workshop content (markdown instructions)
if [ -d "$WORKSHOP_DIR/content" ]; then
    rm -rf "$WS_REPO_PATH/content"
    cp -r "$WORKSHOP_DIR/content" "$WS_REPO_PATH/content"
    echo "Copied content/ to WS repo"
fi
echo "Copied contentspec.yaml, static/, and content/ to $WS_REPO_PATH"

# Step 3: Sync assets to S3
echo ""
echo "=== Step 3: Syncing assets to S3 ==="
aws s3 sync "$WORKSHOP_DIR/assets" "s3://$WS_S3_BUCKET/$WS_S3_PREFIX" --delete
echo "Assets synced to s3://$WS_S3_BUCKET/$WS_S3_PREFIX"

# Step 4: Commit and push WS repo
if [ "$SKIP_PUSH" = true ]; then
    echo ""
    echo "=== Skipping git push (--skip-push) ==="
    echo "To push manually: cd $WS_REPO_PATH && git add -A && git commit -m 'Update' && git push"
else
    echo ""
    echo "=== Step 4: Pushing WS repo ==="
    cd "$WS_REPO_PATH"
    git add -A
    git commit -m "Update workshop content and infrastructure" || echo "Nothing to commit"
    git push
    echo "Pushed to Workshop Studio — build will start automatically"
fi

echo ""
echo "[OK] Deploy complete"
