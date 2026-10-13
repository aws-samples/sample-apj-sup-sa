#!/bin/bash
# Patch Pegasus 5.1.0 MonocularCamera so the depth topic actually publishes.
#
# Pegasus ships with these two lines COMMENTED OUT inside update():
#     #if self._depth:
#     #    self._state["depth"] = self._camera.get_depth()
# which means `depth=True` in the constructor registers the Isaac Sim
# annotator but the ROS 2 backend's depth writer never fires (it gates on
# `"depth" in self._state`). Uncommenting the two lines activates depth
# publishing on /drone<id>/camera/depth.
#
# Idempotent — re-running on a patched install is a no-op.
#
# Usage:
#   ./scripts/apply_pegasus_depth_patch.sh
#
# The fix only takes effect after a full sim restart (Pegasus extension
# loads once per Kit process).

set -euo pipefail

CAM="$HOME/.local/share/ov/data/exts/v2/pegasus.simulator-5.1.0/pegasus/simulator/logic/graphical_sensors/monocular_camera.py"

if [[ ! -f "$CAM" ]]; then
  echo "[depth-patch] ERROR: $CAM not found." >&2
  echo "[depth-patch] Is Pegasus 5.1.0 installed? Check: ls ~/.local/share/ov/data/exts/v2/ | grep pegasus" >&2
  exit 2
fi

if grep -q '^            if self._depth:$' "$CAM"; then
  echo "[depth-patch] already applied — no changes"
  exit 0
fi

if ! grep -q '^            #if self._depth:$' "$CAM"; then
  echo "[depth-patch] ERROR: expected commented line '#if self._depth:' not found in $CAM" >&2
  echo "[depth-patch] The Pegasus source may have been updated. Inspect manually." >&2
  exit 1
fi

cp "$CAM" "$CAM.bak"
echo "[depth-patch] backup: $CAM.bak"

sed -i 's|#if self._depth:|if self._depth:|' "$CAM"
sed -i 's|#    self._state\["depth"\] = self._camera.get_depth()|    self._state["depth"] = self._camera.get_depth()|' "$CAM"

if grep -q '^            if self._depth:$' "$CAM" && \
   grep -q '^                self\._state\["depth"\] = self\._camera\.get_depth()$' "$CAM"; then
  echo "[depth-patch] OK — depth lines uncommented"
  echo "[depth-patch] restart sim for change to take effect"
else
  echo "[depth-patch] FAILED verification — restoring backup" >&2
  mv "$CAM.bak" "$CAM"
  exit 1
fi
