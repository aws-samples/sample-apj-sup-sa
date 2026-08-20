"""Fused depth + lidar safety evaluator.

The lesson (saved to memory as feedback_depth_for_brake.md): the other drone
project routes depth to the LLM (advisory) and lidar to the hardware brake.
Lidar alone misses overhead obstacles, thin rails, glass — drone crashes.

This module fuses both: `effective_min_m(lidar_sectors, depth_frame)`
returns the conservative minimum of (closest lidar sector, min depth inside
a forward cone). Wire this into your safety loop instead of the raw lidar
min.

Runs INSIDE Kit (no external deps beyond numpy).
"""
import numpy as np


# Tunable thresholds. Match the flying-agent-dev defaults so behaviour is
# comparable; override via your own call site if the scenario demands.
OBSTACLE_SAFE_DIST   = 3.0     # m — full speed above this
OBSTACLE_STOP_DIST   = 1.5     # m — stop commanded direction
OBSTACLE_EMERGENCY   = 0.8     # m — hard brake regardless of direction


def front_cone_min_depth_m(depth_frame, cone_fraction=0.30, min_valid_m=0.05):
    """Min depth in the central `cone_fraction` of the depth frame.

    `depth_frame`: float32 2D array, metres per pixel (Pegasus 32FC1).
    `cone_fraction`: 0.30 → central 30% width x 30% height ~ 9% of pixels.
    `min_valid_m`: ignore pixels closer than this (likely self-occlusion or
                   invalid returns from the sensor).

    Returns +inf if no valid pixels. ~2 ms per call on a 640x480 frame.
    """
    if depth_frame is None or depth_frame.size == 0:
        return float("inf")
    h, w = depth_frame.shape[:2]
    cy, cx = h // 2, w // 2
    dy, dx = int(h * cone_fraction / 2), int(w * cone_fraction / 2)
    cone = depth_frame[cy - dy:cy + dy, cx - dx:cx + dx]
    mask = np.isfinite(cone) & (cone > min_valid_m)
    if not mask.any():
        return float("inf")
    return float(cone[mask].min())


def lidar_front_min_m(sectors):
    """Minimum lidar distance across the forward three sectors
    (front / front_left / front_right). Returns +inf if all unset or if
    `sectors` is None/empty — callers shouldn't crash on a race where
    lidar telemetry hasn't arrived yet."""
    if not sectors:
        return float("inf")
    vals = [sectors.get(s, float("inf")) for s in ("front", "front_left", "front_right")]
    return min(vals) if vals else float("inf")


def effective_min_m(lidar_sectors, depth_frame, cone_fraction=0.30):
    """THE fused safety number. Whichever sensor sees the closer threat
    wins — prevents the 'lidar said clear, depth saw a rail, drone crashed'
    failure mode.
    """
    lidar_m = lidar_front_min_m(lidar_sectors)
    depth_m = front_cone_min_depth_m(depth_frame, cone_fraction=cone_fraction)
    return min(lidar_m, depth_m)


def classify(effective_m):
    """Return one of 'safe' / 'slowdown' / 'stop' / 'emergency' so callers
    can map to velocity or log levels."""
    if effective_m <= OBSTACLE_EMERGENCY:
        return "emergency"
    if effective_m <= OBSTACLE_STOP_DIST:
        return "stop"
    if effective_m <= OBSTACLE_SAFE_DIST:
        return "slowdown"
    return "safe"


def safety_report(lidar_sectors, depth_frame, cone_fraction=0.30):
    """Full breakdown — useful for telemetry logging.

    Returns a dict:
        {
          'lidar_front_min_m': 2.3,
          'depth_front_cone_min_m': 1.8,    ← closer! wouldn't show in lidar-only
          'effective_min_m': 1.8,
          'status': 'stop',
          'winner': 'depth'                 ← which sensor flagged it
        }
    """
    lidar_m = lidar_front_min_m(lidar_sectors)
    depth_m = front_cone_min_depth_m(depth_frame, cone_fraction=cone_fraction)
    eff = min(lidar_m, depth_m)
    winner = "lidar" if lidar_m <= depth_m else "depth"
    if not np.isfinite(eff):
        winner = "none"
    return {
        "lidar_front_min_m": round(lidar_m, 3) if np.isfinite(lidar_m) else None,
        "depth_front_cone_min_m": round(depth_m, 3) if np.isfinite(depth_m) else None,
        "effective_min_m": round(eff, 3) if np.isfinite(eff) else None,
        "status": classify(eff),
        "winner": winner,
    }


def velocity_scale(effective_m, max_speed=3.0):
    """Proportional slowdown between STOP and SAFE; 0 at or below STOP;
    full speed at or above SAFE. Callers multiply their desired velocity
    by this before sending to PX4."""
    if effective_m <= OBSTACLE_STOP_DIST:
        return 0.0
    if effective_m >= OBSTACLE_SAFE_DIST:
        return 1.0
    ratio = (effective_m - OBSTACLE_STOP_DIST) / (OBSTACLE_SAFE_DIST - OBSTACLE_STOP_DIST)
    return max(0.1, ratio)
