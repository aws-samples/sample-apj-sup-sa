#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-${CLAUDE_VERSION:-2.1.195}}"
PLATFORM="${CLAUDE_PLATFORM:-linux-arm64}"
REPO="${CLAUDE_RELEASE_REPO:-https://downloads.claude.ai/claude-code-releases}"
KEY_URL="${CLAUDE_SIGNING_KEY_URL:-https://downloads.claude.ai/keys/claude-code.asc}"
EXPECTED_FINGERPRINT="31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"
OUTPUT="${CLAUDE_BINARY_OUTPUT:-docker/claude}"

for tool in curl gpg node; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Required tool not found: $tool" >&2
    exit 1
  fi
done

workdir="$(mktemp -d)"
GNUPGHOME="$(mktemp -d)"
chmod 700 "$GNUPGHOME"
export GNUPGHOME
cleanup() {
  rm -rf "$workdir" "$GNUPGHOME"
}
trap cleanup EXIT

echo "Downloading Claude Code manifest for version ${VERSION}..."
curl -fsSL \
  -o "$workdir/manifest.json" "$REPO/$VERSION/manifest.json" \
  -o "$workdir/manifest.json.sig" "$REPO/$VERSION/manifest.json.sig"

echo "Importing Anthropic Claude Code release signing key..."
# Import into an isolated keyring (GNUPGHOME above) so the verify step can only
# trust the key we just downloaded, never a key already present on the host.
curl -fsSL "$KEY_URL" | gpg --import >/dev/null
fingerprint="$(gpg --with-colons --fingerprint security@anthropic.com | awk -F: '/^fpr:/ {print $10; exit}')"
if [ "$fingerprint" != "$EXPECTED_FINGERPRINT" ]; then
  echo "Unexpected signing key fingerprint: $fingerprint" >&2
  exit 1
fi

echo "Verifying signed manifest..."
# Bind verification to the pinned fingerprint: assert VALIDSIG for the expected
# key rather than accepting any good signature from the keyring.
verify_status="$(gpg --status-fd 1 --verify "$workdir/manifest.json.sig" "$workdir/manifest.json")"
if ! grep -q "^\[GNUPG:\] VALIDSIG .*$EXPECTED_FINGERPRINT" <<<"$verify_status"; then
  echo "Manifest signature is not from the pinned signing key" >&2
  echo "$verify_status" >&2
  exit 1
fi

manifest_fields="$(node -e "const fs=require('fs'); const m=JSON.parse(fs.readFileSync('$workdir/manifest.json','utf8')); const p=m.platforms['$PLATFORM']; if(!p) throw new Error('Unknown platform: $PLATFORM'); process.stdout.write(p.binary+'\n'+p.checksum);")"
{
  read -r binary_name
  read -r expected_checksum
} <<<"$manifest_fields"

echo "Downloading ${PLATFORM}/${binary_name}..."
curl -fsSLo "$workdir/$binary_name" "$REPO/$VERSION/$PLATFORM/$binary_name"

if command -v sha256sum >/dev/null 2>&1; then
  actual_checksum="$(sha256sum "$workdir/$binary_name" | awk '{print $1}')"
else
  actual_checksum="$(shasum -a 256 "$workdir/$binary_name" | awk '{print $1}')"
fi

if [ "$actual_checksum" != "$expected_checksum" ]; then
  echo "Checksum mismatch for $binary_name" >&2
  echo "expected: $expected_checksum" >&2
  echo "actual:   $actual_checksum" >&2
  exit 1
fi

install -m 0755 "$workdir/$binary_name" "$OUTPUT"
echo "Wrote verified Claude Code binary to $OUTPUT"
