"""Camera preset rig for /World/ClaudeSim/Cams.

Runs INSIDE Kit (via kit_exec). Usage from a scene script:

    from simulation.lib import cams
    cams.define_presets(target=(0, 0, 0), radius=6.0)
    cams.use("Hero")
"""
import math
from pxr import UsdGeom, Gf
import omni.usd
import omni.kit.viewport.utility as vpu

CAM_ROOT = "/World/ClaudeSim/Cams"


def _look_at_matrix(eye, target, up=(0.0, 0.0, 1.0)):
    """Build a USD camera local-to-world matrix that places the cam at `eye`
    looking toward `target` with `up` as the world up axis. Camera looks -Z."""
    eye = Gf.Vec3d(*eye)
    target = Gf.Vec3d(*target)
    up = Gf.Vec3d(*up)

    forward = target - eye
    forward.Normalize()
    right = Gf.Cross(forward, up)
    if right.GetLength() < 1e-6:
        # looking straight down/up — pick an arbitrary right
        right = Gf.Vec3d(1, 0, 0)
    right.Normalize()
    new_up = Gf.Cross(right, forward)

    return Gf.Matrix4d(
        right[0],    right[1],    right[2],    0.0,
        new_up[0],   new_up[1],   new_up[2],   0.0,
        -forward[0], -forward[1], -forward[2], 0.0,
        eye[0],      eye[1],      eye[2],      1.0,
    )


def _define_cam(stage, name, eye, target, focal=35.0, up=(0, 0, 1)):
    path = f"{CAM_ROOT}/{name}"
    cam = UsdGeom.Camera.Define(stage, path)
    xform = UsdGeom.Xformable(cam)
    xform.ClearXformOpOrder()
    op = xform.AddTransformOp()
    op.Set(_look_at_matrix(eye, target, up))
    cam.GetFocalLengthAttr().Set(focal)
    cam.GetClippingRangeAttr().Set(Gf.Vec2f(0.05, 100000.0))
    return path


def define_presets(target=(0.0, 0.0, 0.0), radius=6.0):
    """Create the standard preset cameras under /World/ClaudeSim/Cams.

    `target` is the look-at world-space center (scene center).
    `radius` is a rough bounding radius used to place hero/top/street cams.
    Safe to re-run; will overwrite the pose on each preset.
    """
    stage = omni.usd.get_context().get_stage()
    UsdGeom.Xform.Define(stage, "/World/ClaudeSim")
    UsdGeom.Scope.Define(stage, CAM_ROOT)

    tx, ty, tz = target
    r = float(radius)

    # Hero: 3/4 over-the-shoulder, slightly above
    _define_cam(stage, "Hero",
                eye=(tx + r * 0.9, ty - r * 0.9, tz + r * 0.55),
                target=target, focal=35.0)

    # Top: straight-down orthogonal(ish), slightly offset so not pure ortho
    _define_cam(stage, "Top",
                eye=(tx + 0.001, ty + 0.001, tz + r * 2.2),
                target=target, focal=50.0)

    # Street: low, almost eye-level, further back
    _define_cam(stage, "Street",
                eye=(tx + r * 1.4, ty - r * 1.4, tz + r * 0.15),
                target=target, focal=28.0)

    # Closeup: tight, shallow angle
    _define_cam(stage, "Closeup",
                eye=(tx + r * 0.45, ty - r * 0.45, tz + r * 0.25),
                target=target, focal=50.0)

    return [f"{CAM_ROOT}/{n}" for n in ("Hero", "Top", "Street", "Closeup")]


def aim(name, eye, target, focal=None, up=(0, 0, 1)):
    """Reposition an existing preset (or create if missing)."""
    stage = omni.usd.get_context().get_stage()
    UsdGeom.Scope.Define(stage, CAM_ROOT)
    path = _define_cam(stage, name, eye, target,
                       focal=focal if focal is not None else 35.0, up=up)
    return path


def use(name):
    """Switch the active viewport to the named preset."""
    path = f"{CAM_ROOT}/{name}"
    vp = vpu.get_active_viewport()
    vp.camera_path = path
    return path


def list_presets():
    stage = omni.usd.get_context().get_stage()
    root = stage.GetPrimAtPath(CAM_ROOT)
    if not root:
        return []
    return [str(p.GetPath()) for p in root.GetChildren() if p.IsA(UsdGeom.Camera)]


# ---------------------------------------------------------------------------
# Tracking cameras (follow + topdown)
#
# These create a Camera prim AND register a per-frame update callback that
# keeps the camera locked to a moving target prim. Works for any prim that
# has a transform — drone, robot, vehicle, pedestrian, whatever.
#
# The update callbacks live in _TRACKING_RIGS keyed by camera path so a re-run
# replaces (not duplicates) an existing rig. Use `uninstall_tracking_cam(path)`
# to remove.
# ---------------------------------------------------------------------------

import math                                                # noqa: E402
import omni.kit.app                                        # noqa: E402

# path → (subscription handle, update_fn). Subscription handles are kept alive
# here so they don't get GC'd.
_TRACKING_RIGS: dict = {}


def _get_prim_world_translation_yaw(stage, prim_path):
    """Return (translation Vec3d, yaw_deg). Tolerates missing prim."""
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return None, 0.0
    xf = UsdGeom.Xformable(prim)
    world = xf.ComputeLocalToWorldTransform(0)
    t = world.ExtractTranslation()
    rot = world.ExtractRotationQuat()
    qw = rot.GetReal()
    qx, qy, qz = rot.GetImaginary()
    # Z-up yaw extraction
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    yaw_deg = math.degrees(math.atan2(siny_cosp, cosy_cosp))
    return t, yaw_deg


def install_follow_cam(target_prim_path,
                       cam_path="/World/ClaudeSim/Cams/Follow",
                       distance=6.0,
                       height=3.0,
                       focal_length=24.0,
                       make_active=True,
                       smoothing=0.15,
                       collision_avoid=True,
                       min_dist_from_target=1.5):
    """Chase-cam that sits `distance` metres behind and `height` above the
    target, rotating with the target's yaw.

    Args:
        smoothing: 0.0 = hard-follow (snappy but jittery),
                   1.0 = fully responsive (same as 0 actually — lerp(1)=target),
                   0.15 = default cinematic (eases toward target, smooths jitter).
                   Applied per-frame: new = current + (target - current) * smoothing.

        collision_avoid: If True, raycast from target toward desired cam pose
                         each frame. If a wall/prop blocks the line of sight,
                         pull the cam to the hit point minus a small buffer so
                         you can always see the drone.

        min_dist_from_target: When collision_avoid pulls the cam in, never go
                              closer than this to the target (avoids clipping
                              INTO the drone).
    """
    stage = omni.usd.get_context().get_stage()
    UsdGeom.Scope.Define(stage, CAM_ROOT)

    cam = UsdGeom.Camera.Define(stage, cam_path)
    cam.CreateFocalLengthAttr(focal_length)
    cam.GetClippingRangeAttr().Set(Gf.Vec2f(0.05, 100000.0))

    xform = UsdGeom.Xformable(cam)
    xform.ClearXformOpOrder()
    translate_op = xform.AddTranslateOp()
    rotate_op = xform.AddRotateXYZOp()

    # Current smoothed state — initialised to None, set on first update
    state = {"pos": None, "yaw": None}

    # Lazy-imported physx scene-query helpers. `sweep_sphere_closest` is
    # preferred — a fat "ray" with radius catches thin poles/rails that a
    # hairline raycast would miss. `raycast_closest` is the fallback.
    _query = {"sweep": None, "raycast": None, "initialised": False}

    def _get_query_fns():
        if _query["initialised"]:
            return _query["sweep"], _query["raycast"]
        try:
            from omni.physx import get_physx_scene_query_interface
            iface = get_physx_scene_query_interface()
            # Different Kit builds expose this under different names:
            for name in ("sweep_sphere_closest", "sweep_closest", "sphere_sweep_closest"):
                fn = getattr(iface, name, None)
                if fn is not None:
                    _query["sweep"] = fn
                    break
            _query["raycast"] = getattr(iface, "raycast_closest", None)
        except Exception:
            pass
        _query["initialised"] = True
        return _query["sweep"], _query["raycast"]

    # Radius for the sphere-sweep — fat enough to catch thin railings,
    # narrow enough to still fit the cam through doorways.
    SWEEP_RADIUS = 0.25

    def _lerp(a, b, t):
        return a + (b - a) * t

    def _lerp_angle_deg(a, b, t):
        # Shortest-path angle lerp — prevents 359° -> 0° spinning the long way
        diff = ((b - a + 180.0) % 360.0) - 180.0
        return a + diff * t

    def _update(_event):
        target_t, target_yaw = _get_prim_world_translation_yaw(stage, target_prim_path)
        if target_t is None:
            return

        yaw_rad = math.radians(target_yaw)
        desired_x = target_t[0] - distance * math.cos(yaw_rad)
        desired_y = target_t[1] - distance * math.sin(yaw_rad)
        desired_z = target_t[2] + height

        # --- Collision avoidance: sphere-sweep from drone toward desired cam pos ---
        # Returns True if we had to pull the cam in, so we can snap (skip smoothing).
        collided_this_frame = False
        if collision_avoid:
            sweep_fn, raycast_fn = _get_query_fns()
            dx = desired_x - target_t[0]
            dy = desired_y - target_t[1]
            dz = desired_z - target_t[2]
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length > 1e-6:
                origin = (target_t[0], target_t[1], target_t[2])
                direction = (dx / length, dy / length, dz / length)
                hit = None
                try:
                    if sweep_fn is not None:
                        # Sphere sweep: fat ray with SWEEP_RADIUS catches thin
                        # poles that a hairline raycast would skim past.
                        # API signature (omni.physx _physx.pyi):
                        #   sweep_sphere_closest(radius, origin, dir, distance, bothSides=False)
                        hit = sweep_fn(SWEEP_RADIUS, origin, direction, length,
                                       bothSides=True)
                    elif raycast_fn is not None:
                        # Fallback: plain raycast. `bothSides` is camelCase in
                        # the physx binding (NOT snake_case — easy mistake).
                        #   raycast_closest(origin, dir, distance, bothSides=False)
                        hit = raycast_fn(origin, direction, length, bothSides=True)
                except Exception:
                    hit = None

                if hit and hit.get("hit") and hit.get("distance", length) < length - 0.1:
                    # Pull the cam to hit-point minus buffer (account for sweep
                    # radius so the camera volume doesn't intersect the wall),
                    # but never closer than min_dist_from_target.
                    buf = 0.20 + (SWEEP_RADIUS if sweep_fn is not None else 0.0)
                    pulled = max(min_dist_from_target, hit["distance"] - buf)
                    desired_x = target_t[0] + direction[0] * pulled
                    desired_y = target_t[1] + direction[1] * pulled
                    desired_z = target_t[2] + direction[2] * pulled
                    collided_this_frame = True

        # --- Smoothing: lerp current → desired.  SNAP on collision frames so
        # we don't spend 0.3s easing through the wall while the lerp catches up. ---
        if state["pos"] is None or smoothing >= 1.0 or collided_this_frame:
            cam_x, cam_y, cam_z = desired_x, desired_y, desired_z
            cam_yaw = target_yaw if state["yaw"] is None or collided_this_frame \
                      else _lerp_angle_deg(state["yaw"], target_yaw, smoothing)
        else:
            t = smoothing
            cx, cy, cz = state["pos"]
            cam_x = _lerp(cx, desired_x, t)
            cam_y = _lerp(cy, desired_y, t)
            cam_z = _lerp(cz, desired_z, t)
            cam_yaw = _lerp_angle_deg(state["yaw"], target_yaw, t)

        state["pos"] = (cam_x, cam_y, cam_z)
        state["yaw"] = cam_yaw

        translate_op.Set(Gf.Vec3d(cam_x, cam_y, cam_z))
        # Look-at: pitch down 60° (so drone is centred-lower), rotate with yaw
        rotate_op.Set(Gf.Vec3d(60.0, 0.0, cam_yaw - 90.0))

    stream = omni.kit.app.get_app().get_update_event_stream()
    sub = stream.create_subscription_to_pop(_update, name=f"follow_cam:{cam_path}")
    _TRACKING_RIGS[cam_path] = (sub, _update)

    if make_active:
        try:
            vpu.get_active_viewport().camera_path = cam_path
        except Exception:
            pass
    return cam_path


def install_drone_pov_viewport(camera_prim_path,
                               viewport_name="DronePOV",
                               width=640,
                               height=360):
    """Open a secondary viewport pointed at `camera_prim_path`.

    Use with Pegasus's MonocularCamera: pass the sensor's `stage_prim_path`
    (from spawn_drone_into's returned info dict) so the secondary viewport
    shows what the drone's onboard camera sees — same frame the perception
    pipeline gets, now rendered for the human too.

    Cheap: no new render product, just a second viewport instance on the
    existing drone camera. GPU hit is a second rendering pass (~10%), zero
    cost to the drone's sensor publish rate.

    Returns the viewport window if it was created (or None if the host Kit
    build doesn't expose `create_viewport_window`)."""
    stage = omni.usd.get_context().get_stage()
    prim = stage.GetPrimAtPath(camera_prim_path)
    if not prim or not prim.IsValid():
        print(f"[cams] drone POV: camera prim not found at {camera_prim_path}")
        return None

    try:
        from omni.kit.viewport.utility import create_viewport_window
    except Exception as e:
        print(f"[cams] drone POV: create_viewport_window unavailable: {e}")
        return None

    try:
        vp_window = create_viewport_window(
            viewport_name=viewport_name,
            width=width,
            height=height,
        )
    except Exception as e:
        print(f"[cams] drone POV: viewport creation failed: {e}")
        return None

    if vp_window is None:
        print("[cams] drone POV: create_viewport_window returned None")
        return None

    try:
        vp_window.viewport_api.camera_path = camera_prim_path
        print(f"[cams] drone POV viewport '{viewport_name}' pointed at {camera_prim_path}")
    except Exception as e:
        print(f"[cams] drone POV: pointing at camera failed: {e}")
    return vp_window


def install_topdown_cam(target_prim_path,
                        cam_path="/World/ClaudeSim/Cams/TopDown",
                        height=3.0,
                        focal_length=14.0,
                        make_active=False):
    """Camera hovering `height` metres above the target, pointing straight
    down. Does not rotate with yaw — always looks -Z world.

    Opens a second viewport so DCV shows chase + top-down side by side (if
    create_viewport_window is available in the Kit build).
    """
    stage = omni.usd.get_context().get_stage()
    UsdGeom.Scope.Define(stage, CAM_ROOT)

    cam = UsdGeom.Camera.Define(stage, cam_path)
    cam.CreateFocalLengthAttr(focal_length)
    cam.GetClippingRangeAttr().Set(Gf.Vec2f(0.05, 100000.0))

    xform = UsdGeom.Xformable(cam)
    xform.ClearXformOpOrder()
    translate_op = xform.AddTranslateOp()
    # +90° about world X so the camera's default -Z forward ends up pointing
    # straight down at the floor (see flying-agent-dev drone_simulation.py
    # lesson — original used -90 and pointed up)
    rotate_op = xform.AddRotateXYZOp()
    rotate_op.Set(Gf.Vec3d(90.0, 0.0, 0.0))

    def _update(_event):
        t, _yaw = _get_prim_world_translation_yaw(stage, target_prim_path)
        if t is None:
            return
        translate_op.Set(Gf.Vec3d(t[0], t[1], t[2] + height))

    stream = omni.kit.app.get_app().get_update_event_stream()
    sub = stream.create_subscription_to_pop(_update, name=f"topdown_cam:{cam_path}")
    _TRACKING_RIGS[cam_path] = (sub, _update)

    # Best-effort: open a secondary viewport pointed at this camera
    try:
        from omni.kit.viewport.utility import create_viewport_window
        vp_window = create_viewport_window(
            viewport_name="TopDown", width=640, height=480,
        )
        if vp_window is not None:
            vp_window.viewport_api.camera_path = cam_path
    except Exception as e:
        print(f"[cams] secondary viewport not opened: {e}")

    if make_active:
        try:
            vpu.get_active_viewport().camera_path = cam_path
        except Exception:
            pass
    return cam_path


def uninstall_tracking_cam(cam_path):
    """Drop the per-frame subscription for a tracking rig. The Camera prim
    stays in the stage; only the update callback is removed."""
    entry = _TRACKING_RIGS.pop(cam_path, None)
    if entry is None:
        return False
    sub, _fn = entry
    # Dropping the subscription reference stops callbacks
    del sub
    return True


def uninstall_all_tracking_cams():
    for p in list(_TRACKING_RIGS.keys()):
        uninstall_tracking_cam(p)
