#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

load_dotenv() {
  local env_file="$1"
  if [[ ! -f "$env_file" ]]; then
    return
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *=* ]] && continue

    local key="${line%%=*}"
    local value="${line#*=}"

    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value%$'\r'}"

    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:-1}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:-1}"
    fi

    export "$key=$value"
  done < "$env_file"
}

load_dotenv ".env"

APP_NAME="Meeting Assistant"
APP_SLUG="meeting-assistant"
VERSION="$(node -p "require('./package.json').version")"
TIMESTAMP="$(date +"%Y%m%d-%H%M%S")"
OUTPUT_DIR="$ROOT_DIR/out/release/${APP_SLUG}-v${VERSION}-${TIMESTAMP}"

NOTARIZE_VALUE="${NOTARIZE:-}"
if [[ "$NOTARIZE_VALUE" == "false" ]]; then
  SHOULD_NOTARIZE=false
elif [[ "$NOTARIZE_VALUE" == "true" ]]; then
  SHOULD_NOTARIZE=true
elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" && -n "${APPLE_TEAM_ID:-}" ]]; then
  SHOULD_NOTARIZE=true
else
  SHOULD_NOTARIZE=false
fi

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "❌ Required command not found: $cmd"
    exit 1
  fi
}

collect_files() {
  local pattern="$1"
  local newer_than="$2"
  local target_var="$3"
  while IFS= read -r -d '' file; do
    eval "$target_var+=(\"\$file\")"
  done < <(find out/make -type f -name "$pattern" -newer "$newer_than" -print0)
}

collect_apps() {
  local newer_than="$1"
  local target_var="$2"
  while IFS= read -r -d '' app; do
    eval "$target_var+=(\"\$app\")"
  done < <(find out -maxdepth 2 -type d -name "*.app" -newer "$newer_than" -print0)
}

validate_codesign_identity() {
  if [[ -z "${APPLE_IDENTITY:-}" ]]; then
    echo "❌ APPLE_IDENTITY is not set."
    echo "   Set APPLE_IDENTITY in .env for Developer ID signing."
    exit 1
  fi

  local identities
  identities="$(security find-identity -v -p codesigning || true)"
  if ! grep -Fq "$APPLE_IDENTITY" <<< "$identities"; then
    echo "❌ APPLE_IDENTITY was not found in keychain."
    echo "   APPLE_IDENTITY=$APPLE_IDENTITY"
    echo "   Install/restore your Developer ID certificate and private key."
    exit 1
  fi
}

verify_app() {
  local app_path="$1"
  local asar_path="$app_path/Contents/Resources/app.asar"

  if [[ ! -f "$asar_path" ]]; then
    echo "❌ app.asar not found: $asar_path"
    exit 1
  fi

  if ! npx asar list "$asar_path" | grep -Eq "^/\\.vite/renderer/main_window/(index\\.html|src/renderer/index\\.html)$"; then
    echo "❌ Renderer entry HTML not found in app.asar."
    echo "   Expected one of:"
    echo "   - /.vite/renderer/main_window/index.html"
    echo "   - /.vite/renderer/main_window/src/renderer/index.html"
    echo "   This build will show a blank window."
    exit 1
  fi

  echo "🔍 Verifying app signature: $app_path"
  codesign --verify --deep --strict --verbose=2 "$app_path"
  spctl --assess --type execute --verbose=4 "$app_path"

  if [[ "$SHOULD_NOTARIZE" == true ]]; then
    echo "🔍 Verifying app notarization ticket: $app_path"
    xcrun stapler validate "$app_path"
  fi
}

verify_dmg() {
  local dmg_path="$1"

  echo "🔍 Verifying DMG code signature: $dmg_path"
  codesign --verify --verbose=2 "$dmg_path"

  if [[ "$SHOULD_NOTARIZE" == true ]]; then
    echo "🔍 Verifying DMG notarization ticket: $dmg_path"
    xcrun stapler validate "$dmg_path"
  fi

  echo "🔍 Verifying DMG Gatekeeper assessment: $dmg_path"
  local spctl_output
  if spctl_output="$(spctl --assess --type open --context context:primary-signature --verbose=4 "$dmg_path" 2>&1)"; then
    echo "$spctl_output"
    return
  fi

  echo "$spctl_output"
  if grep -q "source=Insufficient Context" <<< "$spctl_output"; then
    echo "⚠️  Gatekeeper returned 'Insufficient Context' for local file assessment. Continuing because codesign and notarization validation succeeded."
    return
  fi

  return 1
}

copy_artifact_with_checksum() {
  local src="$1"
  local filename
  filename="$(basename "$src")"
  cp "$src" "$OUTPUT_DIR/$filename"
  shasum -a 256 "$OUTPUT_DIR/$filename" > "$OUTPUT_DIR/$filename.sha256"
}

print_summary() {
  echo ""
  echo "✅ macOS release build complete"
  echo "   Version: v$VERSION"
  echo "   Output:  $OUTPUT_DIR"
  echo ""
  echo "Artifacts:"
  find "$OUTPUT_DIR" -maxdepth 1 -type f | sort | sed 's/^/  - /'
  echo ""
  echo "다음 단계:"
  echo "  1) $OUTPUT_DIR 내 파일을 사내 파일 스토리지/수동 업로드 경로에 업로드"
  echo "  2) 수신자는 DMG 또는 ZIP을 내려받아 설치"
}

require_cmd npm
require_cmd node
require_cmd codesign
require_cmd spctl
require_cmd security
require_cmd shasum
require_cmd xcrun
require_cmd npx

validate_codesign_identity

if [[ "$SHOULD_NOTARIZE" == true ]]; then
  if [[ -z "${APPLE_ID:-}" || -z "${APPLE_APP_SPECIFIC_PASSWORD:-}" || -z "${APPLE_TEAM_ID:-}" ]]; then
    echo "❌ Notarization is enabled but Apple notarization credentials are incomplete."
    echo "   Required: APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID"
    exit 1
  fi
  echo "🔐 Notarization: enabled"
else
  echo "⚠️  Notarization: disabled (NOTARIZE=false or missing credentials)"
fi

echo "🏗️  Building signed macOS artifacts (zip + dmg)..."
# Avoid stale build artifacts causing missing renderer entry HTML in app.asar.
rm -rf out .vite src/renderer/.vite
BUILD_MARKER="$(mktemp)"
npm run make:mac

declare -a APP_FILES=()
declare -a DMG_FILES=()
declare -a ZIP_FILES=()

collect_apps "$BUILD_MARKER" APP_FILES
collect_files "*.dmg" "$BUILD_MARKER" DMG_FILES
collect_files "*.zip" "$BUILD_MARKER" ZIP_FILES
rm -f "$BUILD_MARKER"

if [[ "${#APP_FILES[@]}" -eq 0 ]]; then
  echo "❌ No .app artifacts found in out/"
  exit 1
fi

if [[ "${#DMG_FILES[@]}" -eq 0 && "${#ZIP_FILES[@]}" -eq 0 ]]; then
  echo "❌ No distributable artifacts (.dmg/.zip) found in out/"
  exit 1
fi

for app in "${APP_FILES[@]}"; do
  verify_app "$app"
done

for dmg in "${DMG_FILES[@]}"; do
  verify_dmg "$dmg"
done

mkdir -p "$OUTPUT_DIR"

for dmg in "${DMG_FILES[@]}"; do
  copy_artifact_with_checksum "$dmg"
done

for zip in "${ZIP_FILES[@]}"; do
  copy_artifact_with_checksum "$zip"
done

print_summary
