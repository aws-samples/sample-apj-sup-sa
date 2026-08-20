# Drone Controls

Run `python3 ./scripts/keyboard_fly.py` from `v1/simulation/drone/` in a desktop
terminal (needs keyboard focus).

## Flight

| Key      | Action                  |
|----------|-------------------------|
| **t**    | Takeoff                 |
| **l**    | Land                    |
| **Esc**  | Auto-land + quit        |
| **Space**| Emergency hover (zero velocity) |

## Movement (hold to keep moving)

| Key   | Action              |
|-------|---------------------|
| **w** | Pitch forward       |
| **s** | Pitch backward      |
| **a** | Roll left           |
| **d** | Roll right          |
| **r** | Ascend (up)         |
| **f** | Descend (down)      |
| **q** | Yaw left (CCW)      |
| **e** | Yaw right (CW)      |

## Speed modifiers (hold while moving)

| Key        | Effect       |
|------------|--------------|
| **Shift**  | 2× speed     |
| **Ctrl**   | 0.5× speed   |
| (neither)  | 1× = 2.0 m/s |

## Multi-key combos work

- `w+d` = forward-right diagonal
- `w+r` = forward + climb
- `q+a+f` = yaw-left while strafing-left while descending
- `shift+w` = forward at 4.0 m/s

## HUD legend

```
connected       yes             MAVSDK link to PX4 alive
flying          yes             armed + in offboard mode
offboard        active          PX4 accepting our velocity setpoints
pos (N/E/alt)   +1.23 -4.56 +2.30 m    NED metres, alt positive-up
yaw             +45.0°          compass heading
vel cmd (body)  vx=+2.00 vy=+0.00 vz=+0.00 yaw=+0.0    what we told PX4
speed mod       2.0x (max 4.0 m/s)    current modifier + effective top speed
keys down       w d             what you're holding right now
```

## Safety

- **Auto-land on Esc or Ctrl-C** — the drone doesn't just stop mid-air, it lands.
- **Auto-land on terminal close** — if you close the terminal, script's cleanup lands it first.
- **Emergency hover** — Space zeros all velocities instantly (good if you're about to hit something).
- **Depth + lidar fused brake** — not wired into kbfly's control loop yet; it's available in `lib.drone_safety` for when we build the mission loop.

## How to quit kbfly

| Method | What happens | Use when |
|---|---|---|
| **Esc** (preferred) | Auto-land sequence → log trailer written → pynput listener stops → `bye` | Normal exit |
| **Ctrl-C** | Same as Esc — signal handler triggers cleanup | Keyboard focus is stuck |
| Close terminal | Script dies; `mavsdk_server` helper exits too; flight log may miss its trailer line | Quick exit, OK but not clean |
| `kill <pid>` from another terminal | Graceful SIGTERM → same cleanup path | Can't reach the kbfly terminal |
| `kill -9 <pid>` | Skips auto-land! Drone may keep hovering | Only if hung; then fly again to recover |

**If kbfly dies mid-flight and the drone is still airborne:** run kbfly again from another terminal — it'll re-connect to the same PX4 (offboard was stopped when kbfly exited, but the drone is holding position). Press `l` to land.

**Stack-wide stop** (kills Kit + PX4 + DDS + kbfly + mavsdk_server):
```bash
pkill -9 -f 'isaacsim.exp.full.kit'
pkill -9 px4
pkill -9 -f MicroXRCEAgent
pkill -9 -f keyboard_fly
pkill -9 -f mavsdk_server
```

## Recording

Every keystroke + telemetry sample is logged to `v1/simulation/drone/logs/kbfly_<timestamp>.jsonl`.
Post-flight:

```bash
ls -t logs/ | head -1
jq -c 'select(.kind=="key_down")' logs/kbfly_*.jsonl | tail -20
```

## Launching the full stack

Before running kbfly, the sim must be up. In a DCV terminal:

```bash
cd v1/simulation/drone
./scripts/run_simulation.sh --config warehouse_shelves
```

Wait for `Ready for takeoff!` then in a **second** terminal:

```bash
python3 ./scripts/keyboard_fly.py
```

## Stop everything

```bash
pkill -9 px4 && pkill -9 -f isaacsim.exp.full.kit
```
