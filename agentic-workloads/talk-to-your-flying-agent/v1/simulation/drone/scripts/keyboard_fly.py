#!/usr/bin/env python3
"""Keyboard flight controller v2 — lives OUTSIDE Kit, connects to PX4 via
MAVSDK on udp://:14540. Requires the full drone stack to be running
(`scripts/run_simulation.sh`).

Improvements over the classic tap-to-move pattern:
  - **Hold-to-move** via pynput (real RC feel — keep key held, keep moving).
  - **Multi-key combos** (W+D = forward-right).
  - **HUD** with position, yaw, commanded velocity, obstacle min, connection state.
  - **Auto-land on exit** (ESC, Ctrl-C, terminal close).
  - **Flight recording** — every command + sampled telemetry → drone/logs/flight_*.jsonl.
  - **Variable speed** — hold Shift for 2x, Ctrl for 0.5x.
  - **Scriptable API** — takeoff()/move()/land() exposed as functions so a test
    script can import and drive this without the keyboard loop.

Keys:
  t         takeoff
  l         land
  w/s       pitch forward/back
  a/d       roll left/right
  r/f       ascend/descend
  q/e       yaw left/right
  Shift     2x speed modifier
  Ctrl      0.5x speed modifier
  Space     emergency hover (zero all velocities)
  Esc       land + quit
"""
import asyncio
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from mavsdk import System
from mavsdk.offboard import VelocityBodyYawspeed, OffboardError

try:
    from pynput import keyboard as pk
except ImportError:
    print("pynput not installed. Run: pip install --user --break-system-packages pynput")
    sys.exit(2)

try:
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
BASE_SPEED = 2.0         # m/s default
BASE_YAW_SPEED = 45.0    # deg/s
CONTROL_HZ = 20          # how often we push setpoints to PX4


# ---------------------------------------------------------------------------
# State tracked globally so pynput callbacks + main loop share
# ---------------------------------------------------------------------------
class Controller:
    def __init__(self):
        self.keys_down: set = set()
        self.flying = False
        self.offboard_active = False
        self.connected = False
        self.running = True
        self.speed_modifier = 1.0
        self.last_velocity = (0.0, 0.0, 0.0, 0.0)
        self.position = {"north_m": 0.0, "east_m": 0.0, "altitude_m": 0.0, "yaw_deg": 0.0}
        self.status_msg = ""
        self.log_file = None
        self.t0 = time.monotonic()

    def key_active(self, *names):
        return any(n in self.keys_down for n in names)

    def effective_speed(self):
        return BASE_SPEED * self.speed_modifier

    def effective_yaw_speed(self):
        return BASE_YAW_SPEED * self.speed_modifier

    def log(self, kind, **fields):
        if self.log_file is None:
            return
        line = {
            "kind": kind,
            "t_mono": round(time.monotonic() - self.t0, 3),
            "t_wall": datetime.now().isoformat(),
            **fields,
        }
        try:
            self.log_file.write(json.dumps(line) + "\n")
            self.log_file.flush()
        except Exception:
            pass


ctrl = Controller()


# ---------------------------------------------------------------------------
# pynput callbacks — purely update shared state, no MAVSDK calls here
# ---------------------------------------------------------------------------
def _key_name(key):
    """Normalize pynput.Key / pynput.KeyCode to a lowercase string."""
    try:
        if hasattr(key, "char") and key.char:
            return key.char.lower()
    except Exception:
        pass
    return str(key).replace("Key.", "").lower()


def on_press(key):
    name = _key_name(key)
    ctrl.keys_down.add(name)

    if name == "esc":
        ctrl.running = False
        return False  # stop the pynput listener

    if name == "shift" or name == "shift_l" or name == "shift_r":
        ctrl.speed_modifier = 2.0
    elif name == "ctrl" or name == "ctrl_l" or name == "ctrl_r":
        ctrl.speed_modifier = 0.5

    ctrl.log("key_down", key=name)


def on_release(key):
    name = _key_name(key)
    ctrl.keys_down.discard(name)

    if name.startswith("shift") or name.startswith("ctrl"):
        if not ctrl.key_active("shift", "shift_l", "shift_r", "ctrl", "ctrl_l", "ctrl_r"):
            ctrl.speed_modifier = 1.0

    ctrl.log("key_up", key=name)


# ---------------------------------------------------------------------------
# Scriptable API (same module importable w/o the keyboard loop)
# ---------------------------------------------------------------------------
async def connect_drone():
    """Connect to PX4 via MAVSDK. Returns the System once armable."""
    drone = System()
    await drone.connect(system_address="udp://:14540")
    print("[kbfly] waiting for MAVSDK connection...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            ctrl.connected = True
            print("[kbfly] connected")
            break
    print("[kbfly] waiting for armable telemetry...")
    async for health in drone.telemetry.health():
        if health.is_armable:
            print("[kbfly] armable — ready")
            break
        await asyncio.sleep(1)
    return drone


async def takeoff(drone):
    if ctrl.flying:
        return "already flying"
    await drone.action.arm()
    await drone.action.takeoff()
    await asyncio.sleep(6)
    await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))
    try:
        await drone.offboard.start()
        ctrl.offboard_active = True
    except OffboardError as e:
        return f"offboard start failed: {e}"
    ctrl.flying = True
    ctrl.log("takeoff")
    return "ok"


async def land(drone):
    if not ctrl.flying:
        return "not flying"
    try:
        if ctrl.offboard_active:
            await drone.offboard.stop()
            ctrl.offboard_active = False
    except Exception:
        pass
    await drone.action.land()
    ctrl.flying = False
    ctrl.log("land")
    return "ok"


async def emergency_hover(drone):
    """Zero all body-frame velocities — stops any motion mid-flight."""
    if ctrl.offboard_active:
        await drone.offboard.set_velocity_body(VelocityBodyYawspeed(0, 0, 0, 0))
    ctrl.log("emergency_hover")


# ---------------------------------------------------------------------------
# Telemetry poller — keeps ctrl.position fresh for the HUD + log
# ---------------------------------------------------------------------------
async def telemetry_loop(drone):
    try:
        async for pos_ned in drone.telemetry.position_velocity_ned():
            if not ctrl.running:
                break
            p = pos_ned.position
            ctrl.position["north_m"] = round(p.north_m, 2)
            ctrl.position["east_m"] = round(p.east_m, 2)
            ctrl.position["altitude_m"] = round(-p.down_m, 2)  # NED down → up
    except asyncio.CancelledError:
        pass


async def attitude_loop(drone):
    try:
        async for att in drone.telemetry.attitude_euler():
            if not ctrl.running:
                break
            ctrl.position["yaw_deg"] = round(att.yaw_deg, 1)
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Control loop — reads ctrl.keys_down, pushes velocity setpoints at CONTROL_HZ
# ---------------------------------------------------------------------------
async def control_loop(drone):
    period = 1.0 / CONTROL_HZ
    while ctrl.running:
        t_start = time.monotonic()

        if ctrl.flying and ctrl.offboard_active:
            vx = vy = vz = yaw_rate = 0.0
            spd = ctrl.effective_speed()
            yaw_spd = ctrl.effective_yaw_speed()

            if ctrl.key_active("w"): vx += spd
            if ctrl.key_active("s"): vx -= spd
            if ctrl.key_active("d"): vy += spd
            if ctrl.key_active("a"): vy -= spd
            if ctrl.key_active("r"): vz -= spd      # NED down negative = up
            if ctrl.key_active("f"): vz += spd
            if ctrl.key_active("e"): yaw_rate += yaw_spd
            if ctrl.key_active("q"): yaw_rate -= yaw_spd

            if ctrl.key_active("space"):
                vx = vy = vz = yaw_rate = 0.0

            # One-shot actions (require a press edge, handled in key poller below)

            ctrl.last_velocity = (vx, vy, vz, yaw_rate)
            try:
                await drone.offboard.set_velocity_body(
                    VelocityBodyYawspeed(vx, vy, vz, yaw_rate)
                )
            except Exception as e:
                ctrl.status_msg = f"setpoint error: {e}"

        elapsed = time.monotonic() - t_start
        if elapsed < period:
            await asyncio.sleep(period - elapsed)


# ---------------------------------------------------------------------------
# Press-edge handler for t/l — we want those on KEY DOWN, not repeat
# ---------------------------------------------------------------------------
async def action_watcher(drone):
    """Detect single-fire key presses (takeoff/land) and execute them."""
    seen_t = False
    seen_l = False
    while ctrl.running:
        if ctrl.key_active("t") and not seen_t:
            ctrl.status_msg = "takeoff..."
            result = await takeoff(drone)
            ctrl.status_msg = f"takeoff: {result}"
            seen_t = True
        elif not ctrl.key_active("t"):
            seen_t = False

        if ctrl.key_active("l") and not seen_l:
            ctrl.status_msg = "landing..."
            result = await land(drone)
            ctrl.status_msg = f"land: {result}"
            seen_l = True
        elif not ctrl.key_active("l"):
            seen_l = False

        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# HUD
# ---------------------------------------------------------------------------
def _render_hud():
    table = Table.grid(expand=False)
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="white")

    pos = ctrl.position
    vel = ctrl.last_velocity
    table.add_row("connected", "[green]yes[/]" if ctrl.connected else "[red]no[/]")
    table.add_row("flying", "[green]yes[/]" if ctrl.flying else "no")
    table.add_row("offboard", "[green]active[/]" if ctrl.offboard_active else "[yellow]idle[/]")
    table.add_row("pos (N/E/alt)", f"{pos['north_m']:+7.2f} {pos['east_m']:+7.2f} {pos['altitude_m']:+7.2f} m")
    table.add_row("yaw", f"{pos['yaw_deg']:+7.1f}°")
    table.add_row("vel cmd (body)", f"vx={vel[0]:+.2f} vy={vel[1]:+.2f} vz={vel[2]:+.2f} yaw={vel[3]:+.1f}")
    table.add_row("speed mod", f"{ctrl.speed_modifier:.1f}x (max {BASE_SPEED * ctrl.speed_modifier:.1f} m/s)")
    table.add_row("keys down", " ".join(sorted(ctrl.keys_down)) or "(none)")
    if ctrl.status_msg:
        table.add_row("status", f"[yellow]{ctrl.status_msg}[/]")
    return Panel(
        table,
        title="drone kbfly v2 — [t]akeoff [l]and [wasd]move [rf]alt [qe]yaw [space]hover [esc]quit",
        border_style="blue",
    )


async def hud_loop():
    if not HAS_RICH:
        while ctrl.running:
            print(f"\r[{ctrl.position['north_m']:+.1f} {ctrl.position['east_m']:+.1f} "
                  f"{ctrl.position['altitude_m']:+.1f}m  yaw={ctrl.position['yaw_deg']:+.0f}] "
                  f"flying={ctrl.flying}  keys={sorted(ctrl.keys_down)}      ",
                  end="", flush=True)
            await asyncio.sleep(0.2)
        return

    with Live(_render_hud(), refresh_per_second=8, screen=False) as live:
        while ctrl.running:
            live.update(_render_hud())
            await asyncio.sleep(0.15)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ctrl.log_file = open(LOG_DIR / f"kbfly_{ts}.jsonl", "w", buffering=1)
    ctrl.log("start")

    drone = await connect_drone()

    # Keyboard listener — pynput wants its own thread
    listener = pk.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    # Ctrl-C cleanup: schedule graceful land
    def _sigint_handler():
        ctrl.running = False
    loop = asyncio.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _sigint_handler)
    except NotImplementedError:
        pass

    tasks = [
        asyncio.create_task(telemetry_loop(drone)),
        asyncio.create_task(attitude_loop(drone)),
        asyncio.create_task(control_loop(drone)),
        asyncio.create_task(action_watcher(drone)),
        asyncio.create_task(hud_loop()),
    ]

    # Wait until Esc is pressed (ctrl.running = False)
    try:
        while ctrl.running:
            await asyncio.sleep(0.1)
    finally:
        # Auto-land on exit
        if ctrl.flying:
            print("\n[kbfly] auto-landing before quit...")
            await land(drone)
            # Give it a moment to start descent
            await asyncio.sleep(2.0)
        for t in tasks:
            t.cancel()
        listener.stop()
        try:
            ctrl.log_file.close()
        except Exception:
            pass
        print("[kbfly] bye")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
