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
#
# Do NOT pipe this into head/grep. Under `set -euo pipefail` the reader closing the
# pipe early kills the script with SIGPIPE, and it has already rewritten
# template.yaml by then - so the bundler's output looks fine while the copy never
# happens. That shipped a stale template to Workshop Studio once: a fixed IAM policy
# sat in github-samples/ while the built workshop still carried the broken one, and
# every participant account failed to provision. The verification at the end of this
# script is there to make that impossible to miss a second time.
set -euo pipefail

# Report where we died if a signal (SIGPIPE from `| head`) or an error cuts us off
# between the bundler and the copy, rather than exiting quietly mid-way.
trap 'rc=$?; if [ "${SYNC_DONE:-no}" != yes ]; then
        echo "sync_to_ws_studio.sh exited early (rc=$rc) - the Workshop Studio copies" >&2
        echo "may be STALE. Re-run it without piping the output anywhere." >&2
      fi' EXIT

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
  SYNC_DONE=yes
  exit 0
fi
WS_STUDIO_ROOT_ABS="$(cd "${WS_STUDIO_ROOT}" && pwd)"
WS_STUDIO_INFRA="${WS_STUDIO_ROOT_ABS}/static/infra"
mkdir -p "${WS_STUDIO_INFRA}"
for tpl in code-editor.yaml template.yaml; do
  cp "${HERE}/${tpl}" "${WS_STUDIO_INFRA}/${tpl}"
  echo "Synced ${tpl} -> ${WS_STUDIO_INFRA}/${tpl}"
done

# 3. Copy the participant pages too. This directory is the source for them, and
#    Workshop Studio builds from its own copy, so the two have to stay identical -
#    the same reason the templates are copied rather than symlinked.
SAMPLE_CONTENT="$(cd "${HERE}/.." && pwd)/content"
if [ -d "${SAMPLE_CONTENT}" ]; then
  # This copy is one-way, and that is easy to forget: edit the Workshop Studio copy
  # by mistake and this would silently overwrite it. So say what is about to be
  # discarded. (Learned the hard way - an architecture-page fix was made in the
  # wrong tree and vanished on the next sync.)
  if [ -d "${WS_STUDIO_ROOT_ABS}/content" ] \
     && ! diff -rq "${SAMPLE_CONTENT}" "${WS_STUDIO_ROOT_ABS}/content" >/dev/null 2>&1; then
    echo "NOTE: the Workshop Studio copy of content/ differs and will be overwritten from"
    echo "      ${SAMPLE_CONTENT} - this direction is one-way. Differing files:"
    # `|| true`: diff exits 1 when it finds differences, which is the case we are in,
    # and pipefail would take that as a script failure.
    { diff -rq "${SAMPLE_CONTENT}" "${WS_STUDIO_ROOT_ABS}/content" 2>/dev/null || true; } | sed 's/^/        /'
    echo "      If you meant to keep the Workshop Studio version, stop now and copy it back."
  fi
  rsync -a --delete "${SAMPLE_CONTENT}/" "${WS_STUDIO_ROOT_ABS}/content/"
  echo "Synced content/ -> ${WS_STUDIO_ROOT_ABS}/content/"
fi

# 4. Prove it. Workshop Studio builds from the copies, so a copy that is not
#    byte-identical to the source is a stale workshop, not a cosmetic problem.
for tpl in code-editor.yaml template.yaml; do
  if ! cmp -s "${HERE}/${tpl}" "${WS_STUDIO_INFRA}/${tpl}"; then
    echo "SYNC FAILED: ${tpl} differs from ${WS_STUDIO_INFRA}/${tpl}" >&2
    exit 1
  fi
done
if [ -d "${SAMPLE_CONTENT}" ]; then
  if ! diff -rq "${SAMPLE_CONTENT}" "${WS_STUDIO_ROOT_ABS}/content" >/dev/null; then
    echo "SYNC FAILED: content/ differs from ${WS_STUDIO_ROOT_ABS}/content" >&2
    exit 1
  fi
  echo "Verified: both templates and content/ are identical to their sources."
else
  echo "Verified: both templates are byte-identical to their sources."
fi
SYNC_DONE=yes
