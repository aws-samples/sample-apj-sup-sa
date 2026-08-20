"""MAVSDK drone connection, flight control, and obstacle avoidance."""

import asyncio
import threading
import time

from mavsdk import System
from mavsdk.offboard import VelocityBodyYawspeed

from api.events import emit
from api.sensors import (
    OBSTACLE_EMERGENCY,
    OBSTACLE_SAFE_DIST,
    OBSTACLE_STOP_DIST,
    get_obstacle_distances,
    grab_camera_frame,
    save_frame,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_SPEED = 3.0
ACCEL = 1.5
YAW_SPEED = 30.0
MOVE_DISTANCE = 2.0

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
drone = System()

_flying_lock = threading.Lock()
_flying = False
safety_braked = False

_DIRECTION_SECTORS = {
    "move_forward":  ["front", "front_left", "front_right"],
    "move_backward": ["back", "back_left", "back_right"],
    "move_left":     ["left", "front_left", "back_left"],
    "move_right":    ["right", "front_right", "back_right"],
}


def is_flying() -> bool:
    with _flying_lock:
        return _flying


def _set_flying(val: bool):
    global _flying
    with _flying_lock:
        _flying = val


# ---------------------------------------------------------------------------
# Obstacle helpers
# ---------------------------------------------------------------------------
def _min_obstacle_distance(direction: str) -> float:
    dists = get_obstacle_distances()
    sectors = _DIRECTION_SECTORS.get(direction, [])
    if not sectors:
        return float("inf")
    return min(dists.get(s, float("inf")) for s in sectors)


def _direction_blocked(direction: str) -> str | None:
    d = _min_obstacle_distance(direction)
    if d < OBSTACLE_STOP_DIST:
        return f"BLOCKED: obstacle {d:.1f}m ahead (stop distance {OBSTACLE_STOP_DIST}m)."
    return None


def _obstacle_speed_limit(direction: str) -> float:
    d = _min_obstacle_distance(direction)
    if d <= OBSTACLE_STOP_DIST:
        return 0.0
    if d >= OBSTACLE_SAFE_DIST:
        return MAX_SPEED
    ratio = (d - OBSTACLE_STOP_DIST) / (OBSTACLE_SAFE_DIST - OBSTACLE_STOP_DIST)
    return max(0.3, ratio * MAX_SPEED)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
async def connect():
    await drone.connect(system_address="udp://:14540")
    print("[drone] Waiting for connection...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            print("[drone] Connected!")
            break
    print("[drone] Waiting for armable state...")
    async for health in drone.telemetry.health():
        if health.is_armable:
            print("[drone] Ready!")
            break
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# Telemetry
#
# Positions are reported in NED metres, relative to the drone's home position
# (wherever PX4 was armed). For planner/LLM consumption we flip the sign on
# `down` so altitude reads positive-up — LLMs handle that cleanly. Compass-
# cardinal deltas (north / east) stay signed as NED defines them.
# ---------------------------------------------------------------------------
async def _read_ned_once():
    """Take one sample from the NED telemetry stream and return it."""
    async for pvn in drone.telemetry.position_velocity_ned():
        return pvn  # first sample = current state; we don't consume the stream


async def _read_yaw_once() -> float:
    async for att in drone.telemetry.attitude_euler():
        return att.yaw_deg


async def get_position() -> dict:
    """Return current NED position as metres (+ yaw in degrees).

    Keys: north_m, east_m, altitude_m (positive-up), yaw_deg.
    Origin is the drone's arm-time home position, NOT world origin.
    """
    ned = None
    yaw = 0.0
    try:
        ned = await asyncio.wait_for(_read_ned_once(), timeout=3.0)
    except asyncio.TimeoutError:
        pass
    try:
        yaw = await asyncio.wait_for(_read_yaw_once(), timeout=3.0)
    except asyncio.TimeoutError:
        pass
    p = ned.position if ned else None
    return {
        "north_m":    round(p.north_m, 2) if p else 0.0,
        "east_m":     round(p.east_m, 2) if p else 0.0,
        "altitude_m": round(-p.down_m, 2) if p else 0.0,  # NED down is +ve; invert
        "yaw_deg":    round(yaw, 1),
    }


def _distance_xy(a: dict, b: dict) -> float:
    """Horizontal distance in metres between two NED position dicts."""
    dn = (a.get("north_m", 0.0) or 0.0) - (b.get("north_m", 0.0) or 0.0)
    de = (a.get("east_m",  0.0) or 0.0) - (b.get("east_m",  0.0) or 0.0)
    return (dn * dn + de * de) ** 0.5


async def _get_current_altitude() -> float:
    """Positive-up altitude in metres. Mirrors `altitude_m` in get_position()."""
    async for pvn in drone.telemetry.position_velocity_ned():
        return -pvn.position.down_m


# ---------------------------------------------------------------------------
# Movement primitives
# ---------------------------------------------------------------------------
async def _continuous_move(dx: float, dy: float, dz: float,
                           distance: float, cmd_name: str) -> str:
    # Sample start position so we can report commanded vs actual later.
    start_pos = await get_position()

    travelled = 0.0
    speed = 0.0
    dt = 0.02
    outcome_msg = None  # set on early-exit paths; completion path leaves None

    while travelled < distance:
        if safety_braked:
            await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))
            outcome_msg = f"SAFETY BRAKE after {travelled:.1f}m commanded — obstacle too close."
            break

        min_dist = _min_obstacle_distance(cmd_name)

        if min_dist <= OBSTACLE_EMERGENCY:
            await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))
            outcome_msg = f"EMERGENCY STOP after {travelled:.1f}m commanded — obstacle at {min_dist:.1f}m."
            break

        if min_dist <= OBSTACLE_STOP_DIST:
            while speed > 0.05:
                speed = max(0, speed - ACCEL * 3 * dt)
                await drone.offboard.set_velocity_body(
                    VelocityBodyYawspeed(dx * speed, dy * speed, dz * speed, 0))
                await asyncio.sleep(dt)
            await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))
            outcome_msg = f"BLOCKED after {travelled:.1f}m commanded — obstacle at {min_dist:.1f}m."
            break

        obstacle_limit = _obstacle_speed_limit(cmd_name)
        remaining = distance - travelled
        decel_dist = (speed ** 2) / (2 * ACCEL) if speed > 0 else 0

        if remaining <= decel_dist + 0.1:
            profile_speed = max(0.3, (2 * ACCEL * max(0, remaining)) ** 0.5)
        elif speed < MAX_SPEED:
            profile_speed = min(MAX_SPEED, speed + ACCEL * dt)
        else:
            profile_speed = MAX_SPEED

        target_speed = min(profile_speed, obstacle_limit)
        if speed < target_speed:
            speed = min(target_speed, speed + ACCEL * dt)
        else:
            speed = max(target_speed, speed - ACCEL * 2 * dt)

        await drone.offboard.set_velocity_body(
            VelocityBodyYawspeed(dx * speed, dy * speed, dz * speed, 0))
        await asyncio.sleep(dt)
        travelled += speed * dt

    # Decelerate smoothly if we completed the command path.
    if outcome_msg is None:
        while speed > 0.05:
            speed = max(0, speed - ACCEL * dt)
            await drone.offboard.set_velocity_body(
                VelocityBodyYawspeed(dx * speed, dy * speed, dz * speed, 0))
            await asyncio.sleep(dt)

    await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))

    # Measure what actually happened.
    end_pos = await get_position()
    actual = _distance_xy(end_pos, start_pos)
    direction = cmd_name.replace("move_", "")

    if outcome_msg is not None:
        # Obstacle/brake stopped us early — keep the reason, add actual delta.
        return f"{outcome_msg} Actual travel {actual:.2f}m."
    return f"Moved {direction}: commanded {distance:.1f}m, actual {actual:.2f}m."


async def _continuous_rotate(yaw_dir: float, angle_deg: float) -> str:
    max_yaw = YAW_SPEED
    yaw_accel = 60.0
    rotated = 0.0
    yaw_rate = 0.0
    dt = 0.05
    decel_angle = (max_yaw ** 2) / (2 * yaw_accel)

    while rotated < angle_deg:
        remaining = angle_deg - rotated
        if remaining <= decel_angle:
            target_rate = max(5.0, (2 * yaw_accel * remaining) ** 0.5)
            yaw_rate = max(target_rate, yaw_rate - yaw_accel * dt)
        elif yaw_rate < max_yaw:
            yaw_rate = min(max_yaw, yaw_rate + yaw_accel * dt)

        await drone.offboard.set_velocity_body(
            VelocityBodyYawspeed(0, 0, 0, yaw_dir * yaw_rate))
        await asyncio.sleep(dt)
        rotated += yaw_rate * dt

    await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))
    direction = "left" if yaw_dir < 0 else "right"
    return f"Rotated {direction} {angle_deg:.0f}°."


async def _fly_to_relative_altitude(delta_meters: float) -> str:
    start_alt = await _get_current_altitude()
    current = start_alt
    target = max(1.0, current + delta_meters)
    error = target - current
    speed = 0.0
    dt = 0.05
    print(f"[drone] Alt {current:.1f}m → {target:.1f}m")
    deadline = time.time() + 15.0
    while abs(error) > 0.2 and time.time() < deadline:
        target_speed = min(MAX_SPEED, abs(error) * 1.5)
        if speed < target_speed:
            speed = min(target_speed, speed + ACCEL * dt)
        else:
            speed = max(target_speed, speed - ACCEL * dt)
        vz = -speed if error > 0 else speed
        await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, vz, 0))
        await asyncio.sleep(dt)
        current = await _get_current_altitude()
        error = target - current
    await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))
    actual_delta = current - start_alt
    if time.time() >= deadline:
        return (f"Altitude change timed out at {current:.1f}m "
                f"(commanded Δ{delta_meters:+.1f}m, actual Δ{actual_delta:+.2f}m).")
    return (f"Holding at {current:.1f}m "
            f"(commanded Δ{delta_meters:+.1f}m, actual Δ{actual_delta:+.2f}m).")


# ---------------------------------------------------------------------------
# Command executor
# ---------------------------------------------------------------------------
async def execute_command(cmd, memory) -> str:
    """Translate a command into MAVSDK calls. Returns result string."""
    if isinstance(cmd, str):
        cmd = cmd.strip().lower()

    if cmd == "takeoff":
        if is_flying():
            return "Already flying."
        await drone.action.arm()
        await drone.action.takeoff()
        await asyncio.sleep(8)
        await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))
        await drone.offboard.start()
        _set_flying(True)
        return "Takeoff complete, offboard mode active."

    if cmd == "land":
        if not is_flying():
            return "Not flying."
        try:
            await drone.offboard.stop()
        except Exception:
            pass
        await drone.action.land()
        _set_flying(False)
        return "Landing initiated."

    if isinstance(cmd, tuple) and cmd[0] == "change_altitude":
        if not is_flying():
            return "Not flying."
        return await _fly_to_relative_altitude(cmd[1])

    if cmd == "capture_camera":
        loop = asyncio.get_event_loop()
        arr = await loop.run_in_executor(None, grab_camera_frame)
        if arr is None:
            return "Camera timeout."
        path = save_frame(arr, "capture")
        memory.captured_images.append(path)
        return f"Saved {path}"

    # Movement
    move_cmd = cmd[0] if isinstance(cmd, tuple) and cmd[0].startswith("move_") else cmd
    distance = cmd[1] if isinstance(cmd, tuple) and cmd[0].startswith("move_") else MOVE_DISTANCE
    direction_vectors = {
        "move_forward": (1, 0, 0), "move_backward": (-1, 0, 0),
        "move_left": (0, -1, 0), "move_right": (0, 1, 0),
    }
    if move_cmd in direction_vectors:
        if not is_flying():
            return "Not flying."
        blocked = _direction_blocked(move_cmd)
        if blocked:
            return blocked
        dx, dy, dz = direction_vectors[move_cmd]
        return await _continuous_move(dx, dy, dz, distance, move_cmd)

    # Rotation
    rot_cmd = cmd[0] if isinstance(cmd, tuple) and cmd[0].startswith("rotate_") else cmd
    angle = cmd[1] if isinstance(cmd, tuple) and cmd[0].startswith("rotate_") else 90.0
    if rot_cmd in ("rotate_left", "rotate_right"):
        if not is_flying():
            return "Not flying."
        yaw_dir = -1.0 if rot_cmd == "rotate_left" else 1.0
        return await _continuous_rotate(yaw_dir, angle)

    return f"Unknown command: {cmd}"


# ---------------------------------------------------------------------------
# Safety monitor
# ---------------------------------------------------------------------------
async def safety_monitor():
    global safety_braked
    while True:
        if is_flying():
            dists = get_obstacle_distances()
            min_all = min(dists.values())
            if min_all <= OBSTACLE_EMERGENCY:
                if not safety_braked:
                    print(f"[SAFETY] Emergency brake — obstacle at {min_all:.1f}m!")
                    emit("safety_brake", {"min_distance": round(min_all, 1)})
                    safety_braked = True
                await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))
            else:
                if safety_braked and min_all > OBSTACLE_STOP_DIST:
                    safety_braked = False
        await asyncio.sleep(0.05)
