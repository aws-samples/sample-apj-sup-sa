"""YAML scene-config loader for drone scenarios.

A scenario is a self-contained description of what the drone simulation
should look like: which scene to load, where to spawn, which sensors to
attach, which cameras to install. One YAML per scenario — scripts stay
dumb and just read the config.

Usage:

    from lib.scene_config import load

    cfg = load("v1/simulation/drone/configs/warehouse_shelves.yaml")
    print(cfg.scene.environment)       # "Warehouse with Shelves"
    print(cfg.spawn.position)           # (0.0, -10.0, 0.5)
    if cfg.backends.px4.enabled:
        ...

See v1/simulation/drone/configs/warehouse_shelves.yaml for an annotated
reference.
"""
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Dataclass schema — one dataclass per YAML block. Defaults mean every block
# is optional; unset = "use sensible default." New config keys add new fields.
# ---------------------------------------------------------------------------


@dataclass
class SceneConfig:
    # Pegasus shipped environment name, e.g. "Warehouse with Shelves"
    environment: Optional[str] = None
    # OR: path to a USD file to open directly (used for our custom scenes)
    usd_path: Optional[str] = None


@dataclass
class SpawnConfig:
    # World position in metres (x, y, z). Z is up in Isaac Sim.
    position: tuple[float, float, float] = (0.0, 0.0, 0.5)
    # Euler angles in degrees (roll, pitch, yaw). Applied as XYZ.
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class CameraSensorConfig:
    enabled: bool = True
    update_rate: float = 60.0
    depth: bool = True


@dataclass
class LidarSensorConfig:
    enabled: bool = True


@dataclass
class SensorsConfig:
    camera: CameraSensorConfig = field(default_factory=CameraSensorConfig)
    lidar: LidarSensorConfig = field(default_factory=LidarSensorConfig)


@dataclass
class PX4BackendConfig:
    enabled: bool = False
    px4_dir: str = "~/workspace/PX4-Autopilot"
    vehicle_id: int = 0
    autolaunch: bool = True


@dataclass
class ROS2BackendConfig:
    enabled: bool = True
    namespace: str = "drone"
    vehicle_id: int = 1
    pub_sensors: bool = False
    pub_graphical_sensors: bool = True
    pub_state: bool = False
    sub_control: bool = False


@dataclass
class BackendsConfig:
    px4: PX4BackendConfig = field(default_factory=PX4BackendConfig)
    ros2: ROS2BackendConfig = field(default_factory=ROS2BackendConfig)


@dataclass
class FollowCamConfig:
    enabled: bool = True
    distance: float = 6.0
    height: float = 3.0
    focal_length: float = 24.0
    smoothing: float = 0.15
    collision_avoid: bool = True
    min_dist_from_target: float = 1.5


@dataclass
class DronePOVConfig:
    """Secondary viewport pointed at the drone's onboard camera."""
    enabled: bool = True
    width: int = 640
    height: int = 360


@dataclass
class TopDownCamConfig:
    enabled: bool = False
    height: float = 3.0
    focal_length: float = 14.0


@dataclass
class CamerasConfig:
    follow: FollowCamConfig = field(default_factory=FollowCamConfig)
    pov: DronePOVConfig = field(default_factory=DronePOVConfig)
    topdown: TopDownCamConfig = field(default_factory=TopDownCamConfig)


@dataclass
class SceneFileConfig:
    """Top-level container. `name` lets scenarios identify themselves in logs."""
    name: str = "unnamed"
    description: str = ""
    scene: SceneConfig = field(default_factory=SceneConfig)
    spawn: SpawnConfig = field(default_factory=SpawnConfig)
    sensors: SensorsConfig = field(default_factory=SensorsConfig)
    backends: BackendsConfig = field(default_factory=BackendsConfig)
    cameras: CamerasConfig = field(default_factory=CamerasConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _merge_into_dataclass(dc_class, raw: dict | None):
    """Recursively build a dataclass instance from a (possibly partial) dict.

    Keys not present in the dict fall back to the dataclass default. Nested
    dataclass fields are merged recursively. Extra keys in the YAML raise
    ValueError so typos don't silently become defaults."""
    raw = raw or {}
    kwargs = {}
    known = {f.name: f for f in fields(dc_class)}

    # Reject unknown keys (typos)
    unknown = set(raw.keys()) - known.keys()
    if unknown:
        raise ValueError(
            f"unknown keys in {dc_class.__name__}: {sorted(unknown)}. "
            f"allowed: {sorted(known.keys())}"
        )

    for name, f in known.items():
        if name not in raw:
            continue
        value = raw[name]
        ft = f.type
        # If the field is itself a dataclass (nested block), recurse
        if hasattr(ft, "__dataclass_fields__"):
            kwargs[name] = _merge_into_dataclass(ft, value)
        else:
            ft_str = ft if isinstance(ft, str) else getattr(ft, "__name__", str(ft))
            if "tuple" in ft_str and isinstance(value, list):
                kwargs[name] = tuple(value)
            else:
                kwargs[name] = value
    return dc_class(**kwargs)


def load(path: str | Path) -> SceneFileConfig:
    """Read YAML + produce a SceneFileConfig. Expanduser so ~ works.

    Any ValueError raised from nested dataclass merging is re-raised with the
    config file path prefixed, so users can tell WHICH YAML had the typo.
    """
    p = Path(str(path)).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    with open(p, "r") as fh:
        raw = yaml.safe_load(fh) or {}
    try:
        cfg = _merge_into_dataclass(SceneFileConfig, raw)
    except ValueError as e:
        # _merge_into_dataclass reports the field path inside (e.g. "unknown
        # keys in FollowCamConfig: ['foocal_length']"). Prefix the source
        # file so the user knows which scenario to fix.
        raise ValueError(f"{p}: {e}") from e
    # Light sanity: scene must have either environment or usd_path
    if not cfg.scene.environment and not cfg.scene.usd_path:
        raise ValueError(
            f"{p}: scene must specify either `environment` (Pegasus shipped "
            f"scene name) or `usd_path` (path to a USD file)."
        )
    return cfg


def apply_overrides(cfg: SceneFileConfig, overrides: dict) -> SceneFileConfig:
    """Apply a flat dict of dotted-key overrides to a config.

    Example: {"spawn.position": [1, 2, 3], "backends.px4.enabled": True}
    """
    for dotted, value in overrides.items():
        obj = cfg
        parts = dotted.split(".")
        for part in parts[:-1]:
            obj = getattr(obj, part)
        last = parts[-1]
        # Convert YAML-style list to tuple for position/rotation
        if last in ("position", "rotation_deg") and isinstance(value, list):
            value = tuple(value)
        setattr(obj, last, value)
    return cfg


def describe(cfg: SceneFileConfig) -> str:
    """One-screen summary of a config — useful for startup logs."""
    lines = [
        f"scene: {cfg.name}",
        f"  {cfg.description}" if cfg.description else "",
        f"  environment: {cfg.scene.environment or cfg.scene.usd_path}",
        f"  spawn:   pos={cfg.spawn.position}  rot_deg={cfg.spawn.rotation_deg}",
        f"  camera:  enabled={cfg.sensors.camera.enabled}  depth={cfg.sensors.camera.depth}  rate={cfg.sensors.camera.update_rate}Hz",
        f"  lidar:   enabled={cfg.sensors.lidar.enabled}",
        f"  px4:     enabled={cfg.backends.px4.enabled}  dir={cfg.backends.px4.px4_dir}",
        f"  ros2:    enabled={cfg.backends.ros2.enabled}  ns={cfg.backends.ros2.namespace}",
        f"  follow:  enabled={cfg.cameras.follow.enabled}  dist={cfg.cameras.follow.distance} height={cfg.cameras.follow.height} smooth={cfg.cameras.follow.smoothing} collision_avoid={cfg.cameras.follow.collision_avoid}",
        f"  pov:     enabled={cfg.cameras.pov.enabled}  {cfg.cameras.pov.width}x{cfg.cameras.pov.height}",
        f"  topdown: enabled={cfg.cameras.topdown.enabled}  height={cfg.cameras.topdown.height}",
    ]
    return "\n".join(l for l in lines if l)
