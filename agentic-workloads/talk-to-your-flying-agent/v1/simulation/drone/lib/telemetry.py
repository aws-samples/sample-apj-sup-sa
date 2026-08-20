"""Per-tick flight telemetry logger.

Starts an asyncio background task inside Kit that samples drone state +
obstacle sensors and appends a JSON line to `v1/simulation/drone/logs/
flight_<ts>.jsonl`. Lets you post-mortem any mission with a single grep.

Schema (one line per sample):
    {
      "t_mono": 1234.567,          # time.monotonic() since the task started
      "t_wall": "2026-05-03T20:04:12.123456",
      "pos": [n, e, alt],          # NED metres, alt positive-up
      "yaw_deg": 90.0,
      "vel_body": [vx, vy, vz],    # body-frame m/s (if MAVSDK attached)
      "lidar_front_min_m": 2.3,
      "depth_front_cone_min_m": 1.8,
      "effective_min_m": 1.8,
      "status": "slowdown",
      "winner": "depth"
    }

Usage (in a scene script):
    from lib import telemetry
    t = await telemetry.start(drone_info={"prim_path": "/World/quadrotor"},
                              depth_getter=get_latest_depth,
                              lidar_getter=get_lidar_sectors,
                              hz=10)
    # ... fly ...
    await telemetry.stop(t)

Both getters are callables returning the current frame / sectors dict.
Telemetry module doesn't know how you get them — just that they're callable.
"""
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

import omni.kit.app
import omni.usd
from pxr import UsdGeom

from . import drone_safety


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _extract_pose(stage, prim_path):
    """Return (pos, yaw_deg) for a prim, or ([0,0,0], 0) if missing."""
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return [0.0, 0.0, 0.0], 0.0
    m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0)
    t = m.ExtractTranslation()
    rot = m.ExtractRotationQuat()
    qw = rot.GetReal()
    qx, qy, qz = rot.GetImaginary()
    # z-up yaw
    import math
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    yaw_deg = math.degrees(math.atan2(siny_cosp, cosy_cosp))
    return [float(t[0]), float(t[1]), float(t[2])], yaw_deg


class _Session:
    """Hold the running telemetry task + its open file."""
    def __init__(self, path, task, file_handle, t0):
        self.path = path
        self.task = task
        self.file = file_handle
        self.t0 = t0
        self.samples = 0


async def start(drone_info,
                depth_getter=None,
                lidar_getter=None,
                hz=10,
                tag="flight"):
    """Spawn a background sampling task. Returns a session object — pass it
    to `stop(session)` to flush and close.

    `drone_info`: dict with at least `prim_path` key (e.g. from spawn_drone_into).
    `depth_getter`: callable → numpy.ndarray (depth frame) or None. Sampled per tick.
    `lidar_getter`: callable → dict of sector → distance (metres) or None.
    `hz`: sample rate. 10 Hz default is plenty for 3 m/s drone.
    `tag`: filename prefix.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / f"{tag}_{ts}.jsonl"
    fh = open(path, "w", buffering=1)   # line-buffered so tail -f works live

    t0 = time.monotonic()
    period = 1.0 / max(1, hz)

    # Header line — so readers know the schema without having to guess
    fh.write(json.dumps({
        "kind": "header",
        "tag": tag,
        "started_wall": datetime.now().isoformat(),
        "hz": hz,
        "prim_path": drone_info.get("prim_path"),
        "depth_enabled": depth_getter is not None,
        "lidar_enabled": lidar_getter is not None,
    }) + "\n")

    stage = omni.usd.get_context().get_stage()
    prim_path = drone_info.get("prim_path")

    async def _sample_loop():
        app = omni.kit.app.get_app()
        count = 0
        while True:
            loop_start = time.monotonic()
            try:
                pos, yaw_deg = _extract_pose(stage, prim_path)

                depth_frame = depth_getter() if depth_getter else None
                lidar_sectors = lidar_getter() if lidar_getter else {}

                safety = drone_safety.safety_report(
                    lidar_sectors or {}, depth_frame
                )

                sample = {
                    "kind": "sample",
                    "t_mono": round(loop_start - t0, 3),
                    "t_wall": datetime.now().isoformat(),
                    "pos": [round(x, 3) for x in pos],
                    "yaw_deg": round(yaw_deg, 1),
                    **safety,
                }
                fh.write(json.dumps(sample) + "\n")
                count += 1

            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Don't let logging errors kill the flight
                try:
                    fh.write(json.dumps({
                        "kind": "error",
                        "t_mono": round(time.monotonic() - t0, 3),
                        "message": repr(e),
                    }) + "\n")
                except Exception:
                    pass

            # Respect the tick rate
            elapsed = time.monotonic() - loop_start
            if elapsed < period:
                await asyncio.sleep(period - elapsed)
            else:
                await app.next_update_async()

    task = asyncio.ensure_future(_sample_loop())
    session = _Session(path=str(path), task=task, file_handle=fh, t0=t0)
    print(f"[telemetry] recording -> {path}")
    return session


async def stop(session):
    """Cancel the sampling task and close the file cleanly.

    Writes a trailer line with total samples for quick sanity-check."""
    if session is None:
        return
    try:
        session.task.cancel()
        try:
            await session.task
        except asyncio.CancelledError:
            pass
    except Exception:
        pass
    try:
        session.file.write(json.dumps({
            "kind": "trailer",
            "stopped_wall": datetime.now().isoformat(),
            "duration_s": round(time.monotonic() - session.t0, 3),
        }) + "\n")
        session.file.close()
    except Exception:
        pass
    print(f"[telemetry] stopped -> {session.path}")
