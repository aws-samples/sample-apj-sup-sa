"""Runs a generated bpy script in headless Blender and returns the GLB bytes."""

import json
import os
import pwd
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

HARNESS = Path(__file__).with_name("blender_harness.py")
BLENDER_TIMEOUT_SECONDS = int(os.environ.get("BLENDER_TIMEOUT_SECONDS", "180"))
MAX_GLB_BYTES = 40 * 1024 * 1024
MAX_METRICS_BYTES = 64 * 1024
MAX_LOG_BYTES = 256 * 1024  # only the tail of Blender's output is kept (the traceback is at the end)
SANDBOX_USER = "sandbox"


class BlenderError(RuntimeError):
    pass


@dataclass
class BlenderResult:
    glb: bytes
    seconds: float
    metrics: dict | None = None  # mesh-quality counts from mesh_metrics.py, if they were measured


def _sandbox_user():
    """The unprivileged user Blender runs as, if the agent runs as root (it does in the container)."""
    return pwd.getpwnam(SANDBOX_USER) if os.getuid() == 0 else None


def _drop_privileges(user) -> dict:
    """Popen arguments that switch the child to `user` with no supplementary groups.

    Popen applies these in the child itself, which is safe in a threaded server (the AgentCore app
    is threaded); a preexec_fn is not (Python docs: it may deadlock when threads are running).
    """
    if user is None:
        return {}
    return {"user": user.pw_uid, "group": user.pw_gid, "extra_groups": []}


def run_script(source: str) -> BlenderResult:
    work = Path(tempfile.mkdtemp(prefix="gen-"))
    try:
        script_path = work / "script.py"
        out_path = work / "model.glb"
        metrics_path = work / "metrics.json"
        script_path.write_text(source, encoding="utf-8")
        user = _sandbox_user()
        if user is not None:
            os.chown(work, user.pw_uid, user.pw_gid)
            os.chown(script_path, user.pw_uid, user.pw_gid)

        # Empty environment: the generated code never sees the agent's AWS credentials.
        env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "TMPDIR": str(work), "LANG": "C.UTF-8"}
        cmd = [
            "blender", "-b", "--factory-startup", "--python-exit-code", "1",
            "--python", str(HARNESS), "--", str(script_path), str(out_path), str(metrics_path),
        ]
        started = time.monotonic()
        # Output goes to unlinked temp files, not pipes, so a process the script leaves behind
        # can't hold Blender "open"; we wait for Blender alone, then kill its whole session.
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(
                cmd, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, **_drop_privileges(user),
            )
            try:
                proc.wait(timeout=BLENDER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise BlenderError(f"Blender did not finish within {BLENDER_TIMEOUT_SECONDS} seconds.") from exc
            finally:
                _kill_group(proc.pid)
                proc.wait()
            log.seek(max(0, os.fstat(log.fileno()).st_size - MAX_LOG_BYTES))
            output = log.read().decode("utf-8", errors="replace")
        seconds = time.monotonic() - started

        owner = user.pw_uid if user is not None else os.getuid()
        glb = _read_sandbox_file(out_path, owner, MAX_GLB_BYTES) if proc.returncode == 0 else None
        if glb is None:
            raise BlenderError(_error_excerpt(output))
        try:
            metrics_raw = _read_sandbox_file(metrics_path, owner, MAX_METRICS_BYTES)
        except BlenderError:
            metrics_raw = None  # metrics are best effort; a bad file never fails the export
        return BlenderResult(glb=glb, seconds=seconds, metrics=_parse_metrics(metrics_raw))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _read_sandbox_file(path: Path, owner: int, max_bytes: int) -> bytes | None:
    """Read a file the sandbox wrote, without following links out of the work folder.

    The sandbox user owns the work folder, so it could replace an output with a symlink or
    hard link to a file only the (root) agent can read. Open without following symlinks,
    then check the open file itself: a regular file, owned by the sandbox user, one link.
    Returns None if the file is missing; raises BlenderError if it fails a check.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:  # ELOOP: the path is a symlink
        raise BlenderError(f"Rejected the output file {path.name}: it is not a regular file.") from exc
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != owner:
            raise BlenderError(f"Rejected the output file {path.name}: it is not a regular file written by the script.")
        if st.st_size > max_bytes:
            raise BlenderError(f"The exported {path.name} is {st.st_size // (1024 * 1024)} MB; keep the model under "
                               f"{max_bytes // (1024 * 1024)} MB.")
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BlenderError(f"The exported {path.name} is over {max_bytes // (1024 * 1024)} MB.")
    return data


METRIC_COUNTS = (
    "objects", "pieces", "vertices", "faces", "triangles", "holes", "boundaryEdges", "nonManifoldEdges",
    "flippedFaces", "looseVertices", "looseEdges", "zeroAreaFaces", "duplicateVertices",
)


def _parse_metrics(data: bytes | None) -> dict | None:
    """Keep only the expected fields, as plain ints and bools: the file is written inside the sandbox."""
    if data is None:
        return None
    try:
        raw = json.loads(data.decode("utf-8"))
    except ValueError:
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
