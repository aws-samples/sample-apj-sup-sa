"""ROS 2 camera and lidar — single node, single spin thread."""

import io
import math
import os
import threading
import time
from datetime import datetime

import numpy as np
from PIL import Image as PILImage

from api import config

# ---------------------------------------------------------------------------
# Shared ROS 2 state
# ---------------------------------------------------------------------------
_ros_lock = threading.Lock()
_ros_started = False
_ros_node = None
_ros_thread: threading.Thread | None = None

# Camera
CAMERA_SAMPLE_FPS = config.CAMERA_SAMPLE_FPS
_SAMPLE_INTERVAL = 1.0 / CAMERA_SAMPLE_FPS
_cam_lock = threading.Lock()
_sampled_frame: np.ndarray | None = None

# Depth camera — Pegasus publishes 32FC1 (metres per pixel) on
# config.ROS_DEPTH_TOPIC. Stored internally as float32; normalised to 8-bit
# grayscale on demand for VLM consumption (bright=far, dark=near, clipped).
DEPTH_MAX_METRES = 30.0
_depth_lock = threading.Lock()
_sampled_depth: np.ndarray | None = None  # float32, same spatial dims as RGB

# Lidar
OBSTACLE_SAFE_DIST = 3.0
OBSTACLE_STOP_DIST = 1.5
OBSTACLE_EMERGENCY = 0.8
_NUM_SECTORS = 8
_SECTOR_NAMES = [
    "front", "front_right", "right", "back_right",
    "back", "back_left", "left", "front_left",
]
_lidar_lock = threading.Lock()
_obstacle_sectors: dict[str, float] = {n: float("inf") for n in _SECTOR_NAMES}


# ---------------------------------------------------------------------------
# Image conversion
# ---------------------------------------------------------------------------
def _ros_to_rgb(msg) -> np.ndarray:
    h, w, enc = msg.height, msg.width, msg.encoding
    ch = 4 if enc in ("rgba8", "8UC4") else (1 if enc in ("mono8", "8UC1") else 3)
    arr = np.frombuffer(msg.data, dtype=np.uint8).copy().reshape(h, w, ch)
    if enc == "bgr8":
        arr = arr[:, :, ::-1].copy()
    if ch == 4:
        arr = arr[:, :, :3].copy()
    if ch == 1:
        arr = np.stack([arr[:, :, 0]] * 3, axis=-1)
    return arr


def _ros_to_depth(msg) -> np.ndarray | None:
    """Decode a ROS sensor_msgs/Image depth frame into a float32 2D array (metres).

    Pegasus publishes `32FC1` (distance-to-image-plane). Some Isaac Sim builds
    may publish `16UC1` millimetres; handle both defensively.
    """
    h, w, enc = msg.height, msg.width, msg.encoding
    if enc == "32FC1":
        arr = np.frombuffer(msg.data, dtype=np.float32).copy().reshape(h, w)
    elif enc == "16UC1":
        arr = np.frombuffer(msg.data, dtype=np.uint16).copy().reshape(h, w).astype(np.float32) / 1000.0
    else:
        # Unexpected encoding — return None so callers know depth is unavailable.
        return None
    return arr


def _process_laser_scan(msg) -> dict[str, float]:
    sectors = {n: float("inf") for n in _SECTOR_NAMES}
    angle = msg.angle_min
    sector_size = 2 * math.pi / _NUM_SECTORS
    for r in msg.ranges:
        if msg.range_min < r < msg.range_max:
            a = angle % (2 * math.pi)
            idx = int(a / sector_size) % _NUM_SECTORS
            sectors[_SECTOR_NAMES[idx]] = min(sectors[_SECTOR_NAMES[idx]], r)
        angle += msg.angle_increment
    return sectors


# ---------------------------------------------------------------------------
# Single unified ROS 2 node
# ---------------------------------------------------------------------------
def _ensure_ros_started():
    """Start one ROS 2 node with camera + lidar subscriptions, one spin thread."""
    global _ros_started, _ros_node, _ros_thread

    with _ros_lock:
        if _ros_started:
            return
        _ros_started = True

    import rclpy
    from sensor_msgs.msg import Image as RosImage
    from sensor_msgs.msg import LaserScan

    rclpy.init()
    _ros_node = rclpy.create_node("api_sensors")

    # --- Camera subscriptions ---
    _cam_pending = {}

    def _cam_cb(msg):
        _cam_pending["msg"] = msg

    # Canonical topic from config; `/drone/camera` kept for legacy scenes.
    _ros_node.create_subscription(RosImage, "/drone/camera", _cam_cb, 1)
    _ros_node.create_subscription(RosImage, config.ROS_CAMERA_TOPIC, _cam_cb, 1)

    # --- Depth subscription ---
    _depth_pending = {}
    _got_first_depth = {"v": False}

    def _depth_cb(msg):
        _depth_pending["msg"] = msg
        if not _got_first_depth["v"]:
            _got_first_depth["v"] = True
            print(f"[depth] First depth frame received ({msg.width}x{msg.height}, {msg.encoding})")

    _ros_node.create_subscription(RosImage, config.ROS_DEPTH_TOPIC, _depth_cb, 1)

    # --- Lidar subscriptions ---
    _lidar_pending = {}
    _got_first_lidar = {"v": False}

    def _lidar_cb(msg):
        _lidar_pending["msg"] = msg
        if not _got_first_lidar["v"]:
            _got_first_lidar["v"] = True
            print(f"[lidar] First scan received ({len(msg.ranges)} rays)")

    # Canonical topic from config; `/drone/lidar/laserscan` kept for legacy scenes.
    _ros_node.create_subscription(LaserScan, config.ROS_LIDAR_TOPIC, _lidar_cb, 1)
    _ros_node.create_subscription(LaserScan, "/drone/lidar/laserscan", _lidar_cb, 1)

    # --- Single spin loop for all three ---
    def _spin_loop():
        global _sampled_frame, _sampled_depth, _obstacle_sectors
        last_cam_t = 0.0
        last_depth_t = 0.0
        while True:
            rclpy.spin_once(_ros_node, timeout_sec=0.05)
            now = time.time()

            # Process camera (throttled to CAMERA_SAMPLE_FPS)
            if "msg" in _cam_pending and (now - last_cam_t) >= _SAMPLE_INTERVAL:
                arr = _ros_to_rgb(_cam_pending.pop("msg"))
                with _cam_lock:
                    _sampled_frame = arr
                last_cam_t = now

            # Process depth (throttled to same rate as camera)
            if "msg" in _depth_pending and (now - last_depth_t) >= _SAMPLE_INTERVAL:
                depth = _ros_to_depth(_depth_pending.pop("msg"))
                if depth is not None:
                    with _depth_lock:
                        _sampled_depth = depth
                    last_depth_t = now

            # Process lidar (no throttle — scans are cheap)
            if "msg" in _lidar_pending:
                sectors = _process_laser_scan(_lidar_pending.pop("msg"))
                with _lidar_lock:
                    _obstacle_sectors = sectors

    _ros_thread = threading.Thread(target=_spin_loop, daemon=True)
    _ros_thread.start()
    print(f"[sensors] Unified node started (camera + depth @ {CAMERA_SAMPLE_FPS} fps + lidar)")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def grab_camera_frame() -> np.ndarray | None:
    _ensure_ros_started()
    with _cam_lock:
        if _sampled_frame is not None:
            return _sampled_frame.copy()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        time.sleep(0.1)
        with _cam_lock:
            if _sampled_frame is not None:
                return _sampled_frame.copy()
    return None


def grab_depth_frame() -> np.ndarray | None:
    """Return the latest depth frame as float32 metres-per-pixel, or None if unavailable.

    Short timeout (1.5s) because depth is a nice-to-have overlay — callers
    should gracefully continue with RGB-only if depth isn't ready.
    """
    _ensure_ros_started()
    with _depth_lock:
        if _sampled_depth is not None:
            return _sampled_depth.copy()
    deadline = time.time() + 1.5
    while time.time() < deadline:
        time.sleep(0.1)
        with _depth_lock:
            if _sampled_depth is not None:
                return _sampled_depth.copy()
    return None


def frame_to_png_bytes(arr: np.ndarray) -> bytes:
    img = PILImage.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def frame_to_jpeg_bytes(arr: np.ndarray, quality: int = 70) -> bytes:
    """JPEG encode — used for the live camera stream in the UI. ~10× smaller
    than PNG at equivalent perceived quality, and decoded natively by browsers."""
    img = PILImage.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def depth_to_png_bytes(depth: np.ndarray, max_metres: float = DEPTH_MAX_METRES) -> bytes:
    """Normalise a float32 depth frame (metres) into 8-bit grayscale PNG bytes.

    - Clip distances to [0, max_metres] (default 30m — anything further is
      indistinguishable-from-infinity for our obstacle-avoidance purposes).
    - Invalid pixels (inf / nan / beyond max) become 255 (white = far / no-return).
    - Close surfaces become dark pixels (0 = on the camera).

    VLMs can reason about this as "bright = far, dark = near." The vision
    prompt should note that max-brightness pixels may mean either genuine
    far-away or no-depth-return (sky, smooth surface).
    """
    d = np.asarray(depth, dtype=np.float32)
    invalid = ~np.isfinite(d)
    clipped = np.clip(d, 0.0, max_metres)
    clipped[invalid] = max_metres
    gray = (clipped * (255.0 / max_metres)).astype(np.uint8)
    img = PILImage.fromarray(gray, "L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def depth_to_jpeg_bytes(depth: np.ndarray, max_metres: float = DEPTH_MAX_METRES,
                        quality: int = 70) -> bytes:
    """Same grayscale-depth normalisation as depth_to_png_bytes, but JPEG-encoded
    for live streaming. Slight compression artefacts are fine for visualisation
    (we never feed MJPEG frames back to Qwen — those still use PNG)."""
    d = np.asarray(depth, dtype=np.float32)
    invalid = ~np.isfinite(d)
    clipped = np.clip(d, 0.0, max_metres)
    clipped[invalid] = max_metres
    gray = (clipped * (255.0 / max_metres)).astype(np.uint8)
    img = PILImage.fromarray(gray, "L")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def save_frame(arr: np.ndarray, label: str = "") -> str:
    capture_dir = os.path.expanduser(os.getenv("CAPTURE_DIR", "~/captures"))
    os.makedirs(capture_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{label}" if label else ""
    path = f"{capture_dir}/drone_{ts}{tag}.png"
    PILImage.fromarray(arr, "RGB").save(path)
    return path


def save_depth_frame(depth: np.ndarray, label: str = "") -> str:
    """Save a depth frame as a normalised 8-bit grayscale PNG alongside RGB captures."""
    capture_dir = os.path.expanduser(os.getenv("CAPTURE_DIR", "~/captures"))
    os.makedirs(capture_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{label}" if label else ""
    path = f"{capture_dir}/drone_{ts}{tag}_depth.png"
    with open(path, "wb") as f:
        f.write(depth_to_png_bytes(depth))
    return path


def get_obstacle_distances() -> dict[str, float]:
    _ensure_ros_started()
    with _lidar_lock:
        return {k: round(v, 2) for k, v in _obstacle_sectors.items()}
