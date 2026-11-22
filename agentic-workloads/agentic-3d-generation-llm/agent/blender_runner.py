# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Runs a generated bpy script in headless Blender and returns the GLB bytes."""

import json
import os
import pwd
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

HARNESS = Path(__file__).with_name("blender_harness.py")
BLENDER_TIMEOUT_SECONDS = int(os.environ.get("BLENDER_TIMEOUT_SECONDS", "180"))
MAX_GLB_BYTES = 40 * 1024 * 1024
SANDBOX_USER = "sandbox"


class BlenderError(RuntimeError):
    pass


@dataclass
class BlenderResult:
    glb: bytes
    seconds: float
    metrics: dict | None = None  # mesh-quality counts from mesh_metrics.py, if they were measured


def _demote():
    """Drop to the unprivileged sandbox user, if the agent runs as root (it does in the container)."""
    if os.getuid() != 0:
        return None
    user = pwd.getpwnam(SANDBOX_USER)

    def set_ids():
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)

    return set_ids


def run_script(source: str) -> BlenderResult:
    work = Path(tempfile.mkdtemp(prefix="gen-"))
    try:
        script_path = work / "script.py"
        out_path = work / "model.glb"
        metrics_path = work / "metrics.json"
        script_path.write_text(source, encoding="utf-8")
        demote = _demote()
        if demote is not None:
            user = pwd.getpwnam(SANDBOX_USER)
            os.chown(work, user.pw_uid, user.pw_gid)
            os.chown(script_path, user.pw_uid, user.pw_gid)

        # Empty environment: the generated code never sees the agent's AWS credentials.
        env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "TMPDIR": str(work), "LANG": "C.UTF-8"}
        cmd = [
            "blender", "-b", "--factory-startup", "--python-exit-code", "1",
            "--python", str(HARNESS), "--", str(script_path), str(out_path), str(metrics_path),
        ]
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, cwd=work, env=env, capture_output=True, text=True,
                timeout=BLENDER_TIMEOUT_SECONDS, preexec_fn=demote,
            )
        except subprocess.TimeoutExpired as exc:
            raise BlenderError(f"Blender did not finish within {BLENDER_TIMEOUT_SECONDS} seconds.") from exc
        seconds = time.monotonic() - started

        if proc.returncode != 0 or not out_path.exists():
            raise BlenderError(_error_excerpt(proc.stdout + "\n" + proc.stderr))
        size = out_path.stat().st_size
        if size > MAX_GLB_BYTES:
            raise BlenderError(f"The exported GLB is {size // (1024 * 1024)} MB; keep the model under 40 MB.")
        return BlenderResult(glb=out_path.read_bytes(), seconds=seconds, metrics=_read_metrics(metrics_path))
    finally:
        shutil.rmtree(work, ignore_errors=True)


METRIC_COUNTS = (
    "objects", "pieces", "vertices", "faces", "triangles", "holes", "boundaryEdges", "nonManifoldEdges",
    "flippedFaces", "looseVertices", "looseEdges", "zeroAreaFaces", "duplicateVertices",
)


def _read_metrics(path: Path) -> dict | None:
    """Keep only the expected fields, as plain ints and bools: the file is written inside the sandbox."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    out = {k: int(raw[k]) for k in METRIC_COUNTS if isinstance(raw.get(k), int) and 0 <= raw[k] < 10**9}
    if len(out) != len(METRIC_COUNTS):
        return None
    out["watertight"] = raw.get("watertight") is True
    if raw.get("partial") is True:
        out["partial"] = True
    return out


def _error_excerpt(output: str, limit: int = 3000) -> str:
    """Keep the Python traceback (the part the model needs to fix its script)."""
    marker = output.find("Traceback (most recent call last)")
    text = output[marker:] if marker >= 0 else output
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("Blender quit")]
    excerpt = "\n".join(lines)
    return excerpt[-limit:] if excerpt else "Blender exited with an error and no output."
