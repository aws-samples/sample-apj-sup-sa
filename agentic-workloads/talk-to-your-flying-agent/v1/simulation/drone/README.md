# simulation — Isaac Sim + PX4 drone authoring

Config-driven drone simulation framework. YAML scenarios, one-command launch.

This is the extracted sim-side capability copied from `summit2026/simulation`
and rewired to run from `v1/simulation/drone`.

## Quick start

```bash
# First time: install Python deps
pip install -r requirements.txt

# From repo root:
cd v1/simulation/drone

# Launch the full sim (Isaac Sim + PX4 SITL + ROS 2 publishers):
./scripts/run_simulation.sh --config warehouse_shelves
# Wait ~60-90s for "Ready for takeoff!"

# In a SECOND terminal (keyboard-focused, DCV desktop):
python3 ./scripts/keyboard_fly.py
```

Controls: `t` takeoff · `wasd` move · `rf` alt · `qe` yaw · `Space` hover · `l` land · `Esc` quit + auto-land · Shift = 2× · Ctrl = 0.5×
Full manual: [docs/CONTROLS.md](docs/CONTROLS.md).

## Launching — full reference

`run_simulation.sh` is the single entry point. It kills stale processes, sources ROS 2, sets `PYTHONPATH`, runs the Pegasus depth patch idempotently, then launches `fly_drone.py` under `~/IsaacSim/python.sh` (standalone SimulationApp — required for PX4 autolaunch).

```bash
# Default (warehouse_shelves, GUI, wait for Kit port):
./scripts/run_simulation.sh

# Pick a scenario (writes .current_config + launches):
./scripts/run_simulation.sh --config warehouse_shelves
./scripts/run_simulation.sh --config warehouse_noflight   # spawn-only smoke test, no PX4/ROS2

# Headless (no GUI — for eval / automation):
./scripts/run_simulation.sh --config warehouse_shelves --headless

# Help:
./scripts/run_simulation.sh --help
```

**Success signs** (tail `logs/drone_sim_*.log` or the log path the launcher prints):
- `[run_sim] sourced ROS 2 env`
- `[drone] spawned Iris at pos=...`
- `Received first hearbeat` — Pegasus ↔ PX4 MAVLink link is up
- `INFO  [commander] Ready for takeoff!` — PX4 is armable

**MAVSDK link check** (from a separate terminal, before launching kbfly):
```bash
python3 -c "
import asyncio
from mavsdk import System
async def t():
    drone = System()
    await drone.connect(system_address='udp://:14540')
    async for s in drone.core.connection_state():
        if s.is_connected: print('connected'); break
    async for h in drone.telemetry.health():
        print(f'armable={h.is_armable}'); break
asyncio.run(asyncio.wait_for(t(), timeout=10))
"
```

## Stopping everything

`run_simulation.sh` kills stale processes automatically on the NEXT launch. To stop without relaunching:

```bash
pkill -9 -f 'isaacsim.exp.full.kit'
pkill -9 px4
pkill -9 -f MicroXRCEAgent
pkill -9 -f fly_drone
pkill -9 -f keyboard_fly
pkill -9 -f mavsdk_server
```

Note: `pkill` returns before the OS reaps the target — if you need a verified kill, follow with `pgrep -x px4` (and friends) and loop until empty.

Quick reference for quitting `keyboard_fly.py` alone: press **Esc** (auto-lands + writes flight log trailer + exits cleanly). See [docs/CONTROLS.md](docs/CONTROLS.md) for other exit paths.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Launcher hangs forever at "Kit ready" wait | Display server not available | Make sure you're in a DCV session or `export DISPLAY=:1` |
| `Ready for takeoff!` never appears | PX4 defunct / Pegasus heartbeat not received | Check for stale processes: `pgrep -af 'fly_drone|px4'`. Re-launch — `run_simulation.sh` pkills stale entries first. |
| MAVSDK connection check times out | Port 14540 bound by old `keyboard_fly` / `mavsdk_server` | `pkill -9 -f mavsdk_server` then retry |
| `rclpy` ImportError inside Kit | ROS 2 env not sourced before Kit started | Always launch via `run_simulation.sh`, not directly via `~/IsaacSim/python.sh` |
| `/drone1/camera/depth` not publishing | Pegasus depth patch missing | `./scripts/apply_pegasus_depth_patch.sh` — idempotent, safe to re-run. Then restart sim. |
| Drone POV viewport black | Camera prim path not resolved at spawn | Check `fly_drone.py` log for `[drone] camera prim path: ...`. If missing, `cfg.cameras.pov.enabled` was `false` or Pegasus didn't expose `stage_prim_path`. |

## What's in here

| Folder | Purpose |
|---|---|
| `lib/` | Shared Python modules — YAML loader, spawn helper, depth+lidar safety, camera rig, telemetry |
| `scripts/` | Executables — launcher, standalone sim entry, keyboard flight, Pegasus depth patch |
| `configs/` | YAML scenarios — `warehouse_shelves.yaml` (full flight), `warehouse_noflight.yaml` (spawn-only smoke test) |
| `docs/` | Extra docs — keyboard controls, etc. |
| `snapshots/` | PNG captures (gitignored) |
| `logs/` | Flight telemetry JSONL (gitignored) |

## Architecture

- `configs/*.yaml` — declarative scenario (scene, spawn pose, sensors, backends, cameras)
- `lib/scene_config.py` — YAML → typed dataclass
- `scripts/fly_drone.py` — standalone SimulationApp entry (what `run_simulation.sh` launches)
- `lib/drone.py` — Pegasus drone spawn
- `lib/cams.py` — smoothed + collision-aware follow cam + drone POV viewport
- `lib/drone_safety.py` — fused depth+lidar safety — the key improvement over 2D-lidar-only
- `scripts/keyboard_fly.py` — MAVSDK keyboard flight with auto-land + logging
