# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Runs inside Blender: blender -b --factory-startup --python blender_harness.py -- <script.py> <out.glb> [<metrics.json>]
# Starts from an empty scene, executes the generated script, exports every object to GLB, then
# writes mesh-quality metrics (a metrics failure never fails the export).
import json
import os
import sys

import bpy

# Imported before the generated script runs, so the script can't replace it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesh_metrics  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
script_path, out_path = args[0], args[1]
metrics_path = args[2] if len(args) > 2 else None

bpy.ops.wm.read_factory_settings(use_empty=True)

with open(script_path, encoding="utf-8") as f:
    source = f.read()

exec(compile(source, "generated_script.py", "exec"), {"__name__": "__main__"})

meshes = [o for o in bpy.context.scene.objects if o.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}]
if not meshes:
    raise RuntimeError("The script finished but created no mesh objects.")

bpy.ops.export_scene.gltf(
    filepath=out_path,
    export_format="GLB",
    export_apply=True,
    export_cameras=False,
    export_lights=False,
    use_selection=False,
)
print(f"HARNESS_OK objects={len(meshes)}")

if metrics_path:
    try:
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(mesh_metrics.measure(), f)
    except Exception as exc:  # noqa: BLE001 - metrics are best effort
        print(f"HARNESS_METRICS_FAILED {type(exc).__name__}: {exc}")
