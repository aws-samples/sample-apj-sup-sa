#!/usr/bin/env bash
# Install the bundled Amazon Ember TTFs into the current user's OS font directory so
# browsers and PNG export render diagrams with the real face instead of the fallback stack.
#
#   <skill-dir>/scripts/install_fonts.sh
#
# Optional — generated HTML also embeds the woff2 files via @font-face, so diagrams work
# without this step. Review assets/fonts/Amazon-Ember-Licensing-Guidelines.pdf first.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FONT_SRC="$SKILL_DIR/assets/fonts/ttf"
[[ -d "$FONT_SRC" ]] || { echo "error: font directory not found: $FONT_SRC" >&2; exit 1; }

case "$(uname -s)" in
  Darwin) FONT_DST="$HOME/Library/Fonts" ;;
  *)      FONT_DST="$HOME/.local/share/fonts/amazon-ember" ;;
esac
mkdir -p "$FONT_DST"
cp "$FONT_SRC"/*.ttf "$FONT_DST/"
echo "installed Amazon Ember TTFs to: $FONT_DST"
if command -v fc-cache >/dev/null 2>&1; then
  fc-cache -f "$FONT_DST" >/dev/null && echo "font cache refreshed (fc-cache)"
fi
echo "Diagrams declaring 'Amazon Ember' will now render with the real face in local browsers."
