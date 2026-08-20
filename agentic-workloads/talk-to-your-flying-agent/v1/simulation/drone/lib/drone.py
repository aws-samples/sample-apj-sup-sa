"""Config-driven drone spawner.

Reads a SceneFileConfig (from lib.scene_config), does the Pegasus dance:
- load environment
- build MultirotorConfig with optional PX4 + ROS2 backends
- attach MonocularCamera + Lidar sensors based on config
- spawn the Iris multirotor at the configured pose

Does NOT install follow/topdown cameras — that's lib.cams' job (call the
installers separately from your scene script after spawn_drone returns).

Runs INSIDE Kit (via kit_exec). Requires Pegasus 5.1.0 to be importable
(add `~/.local/share/ov/data/exts/v2/pegasus.simulator-5.1.0` to sys.path
before calling, or launch under run_drone_sim.sh which sets PYTHONPATH).
"""
from typing import Any

import omni.usd


DRONE_PRIM_PATH = "/World/quadrotor"


def _lazy_imports(*, need_px4: bool, need_ros2: bool):
    """Pegasus + scipy imports that only work after the Kit app is up.
    Only imports the backends we actually need — ROS2 backend pulls rclpy
    which requires `source /opt/ros/jazzy/setup.bash` before Kit launched.
    PX4 backend is standalone.
    """
    from pegasus.simulator.params import ROBOTS, SIMULATION_ENVIRONMENTS
    from pegasus.simulator.logic.graphical_sensors.monocular_camera import MonocularCamera
    from pegasus.simulator.logic.graphical_sensors.lidar import Lidar
    from pegasus.simulator.logic.vehicles.multirotor import Multirotor, MultirotorConfig
    from pegasus.simulator.logic.interface.pegasus_interface import PegasusInterface
    from omni.isaac.core.world import World
    from scipy.spatial.transform import Rotation

    out = dict(
        ROBOTS=ROBOTS,
        SIMULATION_ENVIRONMENTS=SIMULATION_ENVIRONMENTS,
        MonocularCamera=MonocularCamera,
        Lidar=Lidar,
        Multirotor=Multirotor,
        MultirotorConfig=MultirotorConfig,
        PegasusInterface=PegasusInterface,
        World=World,
        Rotation=Rotation,
    )
    if need_px4:
        from pegasus.simulator.logic.backends.px4_mavlink_backend import (
            PX4MavlinkBackend, PX4MavlinkBackendConfig,
        )
        out["PX4MavlinkBackend"] = PX4MavlinkBackend
        out["PX4MavlinkBackendConfig"] = PX4MavlinkBackendConfig
    if need_ros2:
        # rclpy requires `source /opt/ros/jazzy/setup.bash` BEFORE Kit launched.
        # If this raises, Kit was started without ROS env — launch via
        # ~/run_drone_sim.sh (which sources ROS) instead of start_kit.sh.
        from pegasus.simulator.logic.backends.ros2_backend import ROS2Backend
        out["ROS2Backend"] = ROS2Backend
    return out


def _resolve_environment(p, env_name, usd_path):
    """Return a URL/path suitable for pg.load_environment()."""
    if usd_path is not None:
        import os
        return os.path.expanduser(usd_path)
    shipped = p["SIMULATION_ENVIRONMENTS"]
    if env_name not in shipped:
        raise ValueError(
            f"scene.environment '{env_name}' is not a shipped Pegasus env. "
            f"Valid names: {sorted(shipped.keys())}"
        )
    return shipped[env_name]


async def prepare_world(cfg):
    """Stage 1 (async): build PegasusInterface, load the environment, and
    await the async physics-context init. Returns (pg, world, env_url, imports).

    Caller then passes those to `spawn_drone_into(...)` to actually create
    the Multirotor — the two-stage split is needed because the physics init
    is only available as an async call inside a live Kit.

    Opens a fresh stage so repeated calls don't accumulate stale drones
    (Pegasus's `get_stage_next_free_path` + VehicleManager singleton are
    both bypass-proof unless we start clean).
    """
    p = _lazy_imports(
        need_px4=cfg.backends.px4.enabled,
        need_ros2=cfg.backends.ros2.enabled,
    )

    # Fresh stage — kills any drones from prior spawns in this Kit session,
    # plus their physics callbacks + VehicleManager registrations.
    await omni.usd.get_context().new_stage_async()
    print("[drone] fresh stage opened")
    # Also clear the Pegasus VehicleManager singleton so its _vehicles dict
    # doesn't hold dangling references to the drones that just disappeared.
    try:
        from pegasus.simulator.logic.vehicle_manager import VehicleManager
        VehicleManager.get_vehicle_manager().remove_all_vehicles()
    except Exception:
        pass

    pg = p["PegasusInterface"]()
    pg._world = p["World"](**pg._world_settings)
    world = pg.world

    env_url = _resolve_environment(p, cfg.scene.environment, cfg.scene.usd_path)
    pg.load_environment(env_url)
    print(f"[drone] loaded environment: {env_url}")

    await world.initialize_simulation_context_async()
    print("[drone] physics context initialized (async)")

    return pg, world, env_url, p


def spawn_drone_into(cfg, pg, world, env_url, p, prim_path: str = DRONE_PRIM_PATH) -> dict:
    """Stage 2 (sync): build backends + sensors + Multirotor into the world
    that prepare_world() already initialized.

    Idempotent: if a prim at `prim_path` (or Pegasus-style `_NN` suffixes
    from a prior run) already exists, it's removed first. Prevents the
    five-drones-piled-up bug from repeated calls."""

    # If PX4 is being attached, kill any existing PX4 SITL process so we
    # don't end up with two competing for MAVLink port 14540.
    if cfg.backends.px4.enabled:
        import subprocess
        subprocess.run(["pkill", "-9", "px4"], check=False, stderr=subprocess.DEVNULL)  # nosec B603
        print("[drone] killed any existing PX4 SITL before launch")

    # 3) Backends
    backends: list = []
    if cfg.backends.px4.enabled:
        mavlink_cfg = p["PX4MavlinkBackendConfig"]({
            "vehicle_id": cfg.backends.px4.vehicle_id,
            "px4_autolaunch": cfg.backends.px4.autolaunch,
            "px4_dir": __expand(cfg.backends.px4.px4_dir),
        })
        backends.append(p["PX4MavlinkBackend"](mavlink_cfg))
        print("[drone] PX4 backend attached")
    if cfg.backends.ros2.enabled:
        backends.append(p["ROS2Backend"](
            vehicle_id=cfg.backends.ros2.vehicle_id,
            config={
                "namespace": cfg.backends.ros2.namespace,
                "pub_sensors": cfg.backends.ros2.pub_sensors,
                "pub_graphical_sensors": cfg.backends.ros2.pub_graphical_sensors,
                "pub_state": cfg.backends.ros2.pub_state,
                "sub_control": cfg.backends.ros2.sub_control,
            },
        ))
        print(f"[drone] ROS2 backend attached (ns={cfg.backends.ros2.namespace})")

    # 4) Sensors — keep handles so callers can introspect (e.g. get camera prim path)
    graphical_sensors: list = []
    camera_sensor = None
    lidar_sensor = None
    if cfg.sensors.camera.enabled:
        camera_sensor = p["MonocularCamera"]("camera", config={
            "update_rate": cfg.sensors.camera.update_rate,
            "depth": cfg.sensors.camera.depth,
        })
        graphical_sensors.append(camera_sensor)
        print(f"[drone] camera sensor (rate={cfg.sensors.camera.update_rate}Hz, depth={cfg.sensors.camera.depth})")
    if cfg.sensors.lidar.enabled:
        lidar_sensor = p["Lidar"]("lidar")
        graphical_sensors.append(lidar_sensor)
        print("[drone] lidar sensor")

    # 5) MultirotorConfig
    mr_cfg = p["MultirotorConfig"]()
    mr_cfg.backends = backends
    mr_cfg.graphical_sensors = graphical_sensors

    # 6) Spawn
    orient_quat = p["Rotation"].from_euler(
        "XYZ", list(cfg.spawn.rotation_deg), degrees=True
    ).as_quat()
    drone = p["Multirotor"](
        prim_path,
        p["ROBOTS"]["Iris"],
        0,
        list(cfg.spawn.position),
        orient_quat,
        config=mr_cfg,
    )
    # Second reset re-initializes physics now that the vehicle is present.
    world.reset()
    print(f"[drone] spawned Iris at pos={cfg.spawn.position} rot_deg={cfg.spawn.rotation_deg}")

    # Camera prim path — Pegasus sets it at sensor.start() (triggered inside
    # Multirotor __init__). We pull it from the sensor's state dict using the
    # public key `stage_prim_path`. Used by install_drone_pov_viewport().
    camera_prim_path = None
    if camera_sensor is not None:
        # Pegasus's MonocularCamera.update writes stage_prim_path into _state;
        # the attribute is also accessible as _stage_prim_path. Try both.
        try:
            state = getattr(camera_sensor, "_state", None) or {}
            camera_prim_path = state.get("stage_prim_path")
        except Exception:
            pass
        if not camera_prim_path:
            camera_prim_path = getattr(camera_sensor, "_stage_prim_path", None)
        if camera_prim_path:
            print(f"[drone] camera prim path: {camera_prim_path}")

    return {
        "pg": pg,
        "world": world,
        "drone": drone,
        "prim_path": prim_path,
        "env_url": env_url,
        "camera_sensor": camera_sensor,
        "lidar_sensor": lidar_sensor,
        "camera_prim_path": camera_prim_path,
    }


# ---------------------------------------------------------------------------
# Telemetry getters — pass these to lib.telemetry.start(...) so the log knows
# where to read depth + lidar from without coupling the telemetry module to
# Pegasus internals.
# ---------------------------------------------------------------------------

def make_depth_getter(camera_sensor):
    """Return a zero-arg callable that yields the latest depth frame
    (numpy ndarray, float32 m/pixel) or None if not yet available."""
    def _getter():
        if camera_sensor is None:
            return None
        try:
            state = getattr(camera_sensor, "_state", None) or {}
            return state.get("depth")
        except Exception:
            return None
    return _getter


def make_lidar_getter(lidar_sensor):
    """Return a zero-arg callable that yields an 8-sector distance dict
    (or {} if lidar not attached / no data yet).

    Uses the standard 8 compass sectors (front, front_right, right, ...).
    Raw Pegasus Lidar state has angle+range arrays; we bucket them here so
    drone_safety.lidar_front_min_m works against the result.
    """
    import math
    SECTORS = ["front", "front_right", "right", "back_right",
               "back", "back_left", "left", "front_left"]

    def _getter():
        if lidar_sensor is None:
            return {}
        try:
            state = getattr(lidar_sensor, "_state", None) or {}
        except Exception:
            return {}
        ranges = state.get("ranges")
        angles = state.get("angles") or state.get("azimuth")
        if ranges is None or angles is None:
            # Pegasus lidar output shapes vary by build; fall through to empty
            return {}
        out = {s: float("inf") for s in SECTORS}
        two_pi = 2 * math.pi
        sector_size = two_pi / 8.0
        try:
            for r, a in zip(ranges, angles):
                if not (r > 0 and math.isfinite(r)):
                    continue
                idx = int((float(a) % two_pi) / sector_size) % 8
                if r < out[SECTORS[idx]]:
                    out[SECTORS[idx]] = float(r)
        except Exception:
            pass
        return out

    return _getter


def __expand(path: str) -> str:
    import os
    return os.path.expanduser(path) if path else path
