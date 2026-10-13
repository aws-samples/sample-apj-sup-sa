#!/usr/bin/env python3
"""Standalone drone launcher — the ONLY path that works for PX4 + ROS2.

Must be launched via `~/IsaacSim/python.sh` with ROS 2 env sourced and
PYTHONPATH pointing at Pegasus. Use `scripts/run_simulation.sh` as the
wrapper; don't invoke this script directly.

Why standalone: Pegasus's `PX4MavlinkBackend(px4_autolaunch=True)` requires
a `SimulationApp({...})` bootstrap at t=0 — it won't work against a Kit
process that was already running via kit_exec (the event loop + PX4
subprocess handshake falls apart). This file does it the right way.

Reads the config path from `--config` or the `.current_config` pointer
file written by the bash launcher.
"""
import argparse
import os
import sys
from pathlib import Path


# --- 1) SimulationApp FIRST — before anything else Isaac-flavoured -------
from isaacsim import SimulationApp  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--headless", action="store_true",
                    help="Run without GUI. For eval / automation.")
parser.add_argument("--config", default=None,
                    help="Path to YAML config. Overrides CLAUDE_DRONE_CONFIG env.")
args, _unknown = parser.parse_known_args()

simulation_app = SimulationApp({"headless": args.headless})


# --- 2) Now we can import everything else ---
DRONE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DRONE_DIR))

import omni.timeline                                    # noqa: E402
from lib import scene_config, drone as drone_lib, cams  # noqa: E402


# --- 3) Pick the config ---
DEFAULT_CONFIG = DRONE_DIR / "configs" / "warehouse_shelves.yaml"
POINTER = DRONE_DIR / ".current_config"

if args.config:
    config_path = args.config
elif POINTER.exists():
    with POINTER.open() as fh:
        config_path = fh.read().strip() or str(DEFAULT_CONFIG)
else:
    config_path = str(DEFAULT_CONFIG)

cfg = scene_config.load(config_path)
print(f"[standalone] config: {config_path}")
print(scene_config.describe(cfg))


# --- 4) Build the sim — same dance as flying-agent-dev's drone_simulation.py,
# but config-driven via our lib.drone + lib.cams. ---
# IMPORTANT: NOT using our async `prepare_world`. Standalone SimulationApp
# pumps physics via simulation_app.update(), so sync setup works.
#
# Mirror lib.drone.spawn_drone_into's steps but inline — the async helper
# is only needed for kit_exec mode.

from pegasus.simulator.params import ROBOTS, SIMULATION_ENVIRONMENTS
from pegasus.simulator.logic.graphical_sensors.monocular_camera import MonocularCamera
from pegasus.simulator.logic.graphical_sensors.lidar import Lidar
from pegasus.simulator.logic.vehicles.multirotor import Multirotor, MultirotorConfig
from pegasus.simulator.logic.interface.pegasus_interface import PegasusInterface
from omni.isaac.core.world import World
from scipy.spatial.transform import Rotation


# Pegasus interface
pg = PegasusInterface()
pg._world = World(**pg._world_settings)
world = pg.world

# Environment
if cfg.scene.usd_path:
    env_url = os.path.expanduser(cfg.scene.usd_path)
else:
    if cfg.scene.environment not in SIMULATION_ENVIRONMENTS:
        raise ValueError(
            f"scene.environment '{cfg.scene.environment}' unknown. "
            f"Valid: {sorted(SIMULATION_ENVIRONMENTS.keys())}"
        )
    env_url = SIMULATION_ENVIRONMENTS[cfg.scene.environment]
pg.load_environment(env_url)
print(f"[standalone] loaded environment: {env_url}")

# Backends — PX4 autolaunch NOW WORKS because we're in SimulationApp mode
backends = []
if cfg.backends.px4.enabled:
    from pegasus.simulator.logic.backends.px4_mavlink_backend import (
        PX4MavlinkBackend, PX4MavlinkBackendConfig,
    )
    mavlink_cfg = PX4MavlinkBackendConfig({
        "vehicle_id": cfg.backends.px4.vehicle_id,
        "px4_autolaunch": cfg.backends.px4.autolaunch,
        "px4_dir": os.path.expanduser(cfg.backends.px4.px4_dir),
    })
    backends.append(PX4MavlinkBackend(mavlink_cfg))
    print(f"[standalone] PX4 backend attached (autolaunch={cfg.backends.px4.autolaunch})")

if cfg.backends.ros2.enabled:
    from pegasus.simulator.logic.backends.ros2_backend import ROS2Backend
    backends.append(ROS2Backend(
        vehicle_id=cfg.backends.ros2.vehicle_id,
        config={
            "namespace": cfg.backends.ros2.namespace,
            "pub_sensors": cfg.backends.ros2.pub_sensors,
            "pub_graphical_sensors": cfg.backends.ros2.pub_graphical_sensors,
            "pub_state": cfg.backends.ros2.pub_state,
            "sub_control": cfg.backends.ros2.sub_control,
        },
    ))
    print(f"[standalone] ROS2 backend attached (ns={cfg.backends.ros2.namespace})")

# Sensors
graphical_sensors = []
camera_sensor = None
lidar_sensor = None
if cfg.sensors.camera.enabled:
    camera_sensor = MonocularCamera("camera", config={
        "update_rate": cfg.sensors.camera.update_rate,
        "depth": cfg.sensors.camera.depth,
    })
    graphical_sensors.append(camera_sensor)
    print(f"[standalone] camera sensor (depth={cfg.sensors.camera.depth})")
if cfg.sensors.lidar.enabled:
    lidar_sensor = Lidar("lidar")
    graphical_sensors.append(lidar_sensor)
    print("[standalone] lidar sensor")

# Multirotor
mr_cfg = MultirotorConfig()
mr_cfg.backends = backends
mr_cfg.graphical_sensors = graphical_sensors

orient_quat = Rotation.from_euler(
    "XYZ", list(cfg.spawn.rotation_deg), degrees=True
).as_quat()
Multirotor(
    "/World/quadrotor",
    ROBOTS["Iris"],
    0,
    list(cfg.spawn.position),
    orient_quat,
    config=mr_cfg,
)
world.reset()
print(f"[standalone] spawned Iris at pos={cfg.spawn.position}")

# Camera prim path — exposed by Pegasus after sensor.start()
camera_prim_path = None
if camera_sensor is not None:
    state = getattr(camera_sensor, "_state", None) or {}
    camera_prim_path = state.get("stage_prim_path") or getattr(camera_sensor, "_stage_prim_path", None)

# --- 5) Follow + POV cameras via our lib ---
drone_body = "/World/quadrotor/body"

if cfg.cameras.follow.enabled:
    cams.install_follow_cam(
        target_prim_path=drone_body,
        distance=cfg.cameras.follow.distance,
        height=cfg.cameras.follow.height,
        focal_length=cfg.cameras.follow.focal_length,
        smoothing=cfg.cameras.follow.smoothing,
        collision_avoid=cfg.cameras.follow.collision_avoid,
        min_dist_from_target=cfg.cameras.follow.min_dist_from_target,
        make_active=True,
    )
    print(f"[standalone] follow cam installed (smooth={cfg.cameras.follow.smoothing})")

if cfg.cameras.pov.enabled and camera_prim_path:
    cams.install_drone_pov_viewport(
        camera_prim_path=camera_prim_path,
        width=cfg.cameras.pov.width,
        height=cfg.cameras.pov.height,
    )

if cfg.cameras.topdown.enabled:
    cams.install_topdown_cam(
        target_prim_path=drone_body,
        height=cfg.cameras.topdown.height,
        focal_length=cfg.cameras.topdown.focal_length,
    )

# --- 6) Run the sim loop ---
timeline = omni.timeline.get_timeline_interface()
timeline.play()
print("[standalone] timeline playing — PX4 should print 'Ready for takeoff!' shortly")
print("[standalone] connect from another terminal:")
print(f"  python3 {DRONE_DIR / 'scripts' / 'keyboard_fly.py'}")
print()

try:
    while simulation_app.is_running():
        world.step(render=True)
except KeyboardInterrupt:
    print("\n[standalone] interrupt — shutting down")
finally:
    timeline.stop()
    simulation_app.close()
