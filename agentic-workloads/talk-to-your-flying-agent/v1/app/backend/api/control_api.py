#!/usr/bin/env python3
"""
HTTP API server for drone control.

Endpoints:
  POST /send-command   — send a text command to the agent
  GET  /status         — drone state snapshot
  WS   /events         — real-time event stream

Run:  uvicorn api.control_api:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import queue
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api import config, session as session_mod
from api.agent import command_queue, memory, mission_active, process_command
from api.drone import (
    connect,
    execute_command,
    get_position,
    is_flying,
    safety_monitor,
)
from api.events import emit, subscribe, unsubscribe
from api.perception import perception_worker
from api.sensors import (
    depth_to_jpeg_bytes,
    frame_to_jpeg_bytes,
    get_obstacle_distances,
    grab_camera_frame,
    grab_depth_frame,
)


def _short_model_id(full_id: str) -> str:
    """Strip profile prefix and version suffix for UI display.

    jp.anthropic.claude-opus-4-7              -> opus-4.7
    jp.anthropic.claude-haiku-4-5-20251001-v1:0 -> haiku-4.5
    apac.anthropic.claude-sonnet-4-6          -> sonnet-4.6
    qwen.qwen3-vl-235b-a22b                   -> qwen3-vl-235b
    """
    parts = full_id.split(".")
    name = parts[-1] if parts else full_id
    # Trim "claude-" prefix if present.
    if name.startswith("claude-"):
        name = name[len("claude-"):]
    # Strip a trailing ISO date + version tag like "-20251001-v1:0".
    tokens = name.split("-")
    keep = []
    for tok in tokens:
        if tok.isdigit() and len(tok) == 8:
            break
        if tok.startswith("v") and ":" in tok:
            break
        keep.append(tok)
    short = "-".join(keep)
    # Normalise "4-7" → "4.7"
    if len(keep) >= 3 and keep[-1].isdigit() and keep[-2].isdigit():
        short = "-".join(keep[:-2]) + "-" + keep[-2] + "." + keep[-1]
    return short


# ---------------------------------------------------------------------------
# Command consumer — drains the tool queue and executes on the drone
# ---------------------------------------------------------------------------
async def command_consumer():
    while True:
        # Collaboration interrupt (Tier 1): while paused/halted, drain queued
        # commands without executing them. This prevents the "pause fires but
        # the drone keeps moving for 1-2 more setpoints" drift. HALT behaviour:
        # drone holds position automatically in MAVSDK offboard since we stop
        # sending new setpoints. Human types `land` afterwards to actually land.
        if memory.paused or memory.halted:
            try:
                cmd = command_queue.get_nowait()
                action_name = cmd if isinstance(cmd, str) else f"{cmd[0]}({cmd[1]})"
                emit("command_dropped", {"command": action_name,
                                         "reason": "paused" if memory.paused else "halted"})
                print(f"[drone] Dropped (paused/halted): {action_name}")
            except queue.Empty:
                pass
            await asyncio.sleep(0.1)
            continue

        try:
            cmd = command_queue.get_nowait()
            action_name = cmd if isinstance(cmd, str) else f"{cmd[0]}({cmd[1]})"
            print(f"[drone] Executing: {action_name}")
            result = await execute_command(cmd, memory)
            print(f"[drone] Result: {result}")
            memory.record_action(action_name, result)
            emit("command_executed", {"command": action_name, "result": result})
            try:
                memory.last_position = await get_position()
            except Exception:
                pass
        except queue.Empty:
            await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    consumer_task = asyncio.create_task(command_consumer())
    safety_task = asyncio.create_task(safety_monitor())
    perception_task = asyncio.create_task(perception_worker(memory))
    print("\n=== Control API ready ===")
    yield
    consumer_task.cancel()
    safety_task.cancel()
    perception_task.cancel()


app = FastAPI(title="Drone Control API", lifespan=lifespan)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/")
async def index():
    return FileResponse(_STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# POST /send-command
# ---------------------------------------------------------------------------
class CommandRequest(BaseModel):
    text: str


class CommandResponse(BaseModel):
    status: str
    agent_response: str | None = None
    error: str | None = None


@app.post("/send-command", response_model=CommandResponse)
async def send_command(req: CommandRequest):
    text = req.text.strip()
    if not text:
        return CommandResponse(status="error", error="Empty command.")

    # Collaboration interrupt: if mission is paused, capture the text as
    # human input for the planner to read on resume — instead of routing it
    # through the voice agent (which could fire tool calls we don't want
    # during a paused mission). Simple escape hatches: "land" / "halt" /
    # "resume" / "takeoff" still take normal paths so recovery works.
    lowered = text.lower()
    if memory.paused and lowered not in {"land", "halt", "resume", "takeoff", "take off"}:
        from datetime import datetime
        entry = {"time": datetime.now().strftime("%H:%M:%S"), "text": text}
        memory.human_input.append(entry)
        emit("human_input_received", {"text": text, "during": "pause"})
        return CommandResponse(
            status="ok",
            agent_response=(
                "Captured for resume. Click ▶ Resume to apply, or add more context."
            ),
        )

    try:
        loop = asyncio.get_event_loop()
        agent_text = await loop.run_in_executor(None, process_command, text)
        return CommandResponse(status="ok", agent_response=agent_text)
    except Exception as e:
        return CommandResponse(status="error", error=str(e))


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------
@app.get("/status")
async def status():
    obstacles = get_obstacle_distances()
    safe_obstacles = {k: (v if v != float("inf") else None) for k, v in obstacles.items()}
    # Read mission_active live from the module, not the stale top-level import
    # (QA-C6/D2 footgun, documented in docs/LEARNING_JOURNAL.md): `from api.agent import
    # mission_active` binds the False-at-import-time value forever. The UI needs
    # the LIVE value so Halt/Pause buttons show during an active mission.
    from api import agent as _agent
    return {
        "flying": is_flying(),
        "mission_active": _agent.mission_active,
        "position": memory.last_position,
        "obstacles": safe_obstacles,
        "task_complete": memory.task_complete,
        "actions_count": len(memory.actions_taken),
        "observations_count": len(memory.observations),
        "models": {
            "region": config.AWS_BEDROCK_REGION,
            "planner": config.BEDROCK_MODEL_PLANNER,
            "planner_short": _short_model_id(config.BEDROCK_MODEL_PLANNER),
            "router": config.BEDROCK_MODEL_ROUTER,
            "vision": config.BEDROCK_MODEL_VISION,
        },
        "session": session_mod.session_status(),
        # Collaboration interrupt state — UI reads these to swap button set
        # (● Pause ↔ ▶ Resume, show ■ Halt, etc.)
        "interrupt": {
            "paused": memory.paused,
            "halted": memory.halted,
            "human_input_count": len(memory.human_input),
            "consecutive_failures": memory.consecutive_failures,
        },
    }


# ---------------------------------------------------------------------------
# Session recorder
# ---------------------------------------------------------------------------
@app.post("/session/start")
async def session_start():
    return session_mod.start_session()


@app.post("/session/end")
async def session_end():
    return session_mod.end_session()


@app.get("/session/status")
async def session_status_endpoint():
    return session_mod.session_status()


# ---------------------------------------------------------------------------
# Collaboration interrupt (Tier 1) — /pause, /resume, /halt
# ---------------------------------------------------------------------------
# Pause: mission_loop idles, command_consumer drops queued actions, drone
# hovers automatically (MAVSDK holds last setpoint). Human can type context
# via /send-command — text lands in memory.human_input and the planner reads
# it on resume.
#
# Halt: mission ends on next loop check. Drone still hovers — the human must
# type `land` separately to land. CHAN's Q3 answer from DISCUSSION Topic 8.
#
# Endpoints are idempotent: /pause while already paused returns
# `already_paused`; /resume while not paused returns `not_paused`.
# ---------------------------------------------------------------------------

@app.post("/pause")
async def pause():
    if memory.paused:
        return {"status": "already_paused"}
    memory.paused = True
    emit("mission_paused", {"reason": "human"})
    print("[mission] PAUSED by human")
    return {"status": "paused"}


@app.post("/resume")
async def resume():
    if not memory.paused:
        return {"status": "not_paused"}
    memory.paused = False
    # Reset failure counter on resume — human input is a fresh start, not
    # a continuation of whatever was failing before.
    if memory.human_input:
        memory.consecutive_failures = 0
    emit("mission_resumed", {"human_input_count": len(memory.human_input)})
    print("[mission] RESUMED by human")
    return {
        "status": "resumed",
        "pending_human_input": memory.human_input[-5:] if memory.human_input else [],
    }


@app.post("/halt")
async def halt():
    # Halt sets both flags: halted ends the mission; paused gates the
    # command_consumer so any in-flight setpoints stop. Drone hovers;
    # human types `land` afterwards.
    memory.paused = True
    memory.halted = True
    emit("mission_halted", {"reason": "human"})
    print("[mission] HALTED by human")
    return {"status": "halted"}


# ---------------------------------------------------------------------------
# Debug: /tick-once — manually run one planner tick (T1.4 loop-split)
# ---------------------------------------------------------------------------
# Useful for:
#   - Stepping through a mission at a booth demo (pause, single-step, resume).
#   - Debugging whether a specific tick would land a tool call correctly.
#   - Reproducing a bad tick deterministically after reading session log.
#
# Requires an active mission (or at least an intention set). Returns the
# tick-result dict from tick_once(). Does NOT bypass pause/halt gates —
# those return `{"executed": false, "reason": "paused|halted"}`.
# ---------------------------------------------------------------------------
from api.agent import tick_once, mission_active as _ma  # noqa: E402


@app.post("/tick-once")
async def tick_once_endpoint():
    # Import fresh each call to see the current module-level mission_active
    from api import agent as _agent
    if not _agent.mission_active:
        return {
            "executed": False,
            "reason": "no_active_mission",
            "hint": "Call /send-command with a mission intention first (e.g. 'find a box'), then /tick-once can step through it.",
        }

    loop = asyncio.get_event_loop()
    # tick_once is sync and may block for tens of seconds on queue drain.
    # Run in the default executor so we don't block FastAPI's event loop.
    result = await loop.run_in_executor(None, _agent.tick_once, None)
    return result


# ---------------------------------------------------------------------------
# Live camera streams — MJPEG over multipart/x-mixed-replace
# ---------------------------------------------------------------------------
# Two endpoints read the existing _sampled_frame / _sampled_depth caches in
# sensors.py (both are already refreshed at CAMERA_SAMPLE_FPS=10 via the ROS
# spin thread). Encoding overhead per frame is ~5ms; native <img src=...>
# support in every browser means no JS plumbing.
#
# Bandwidth at 640×480 q=70 is ~200 KB/frame × 10 fps = ~2 MB/s per viewer.
# Fine for one developer over an SSH tunnel. If booth visitors watch live,
# revisit.

MJPEG_BOUNDARY = b"--frame"


async def _mjpeg_stream(kind: str):
    """Yield multipart/x-mixed-replace frames forever (until client disconnects)."""
    last_sent = 0.0
    # Match the sensor sample rate — no point encoding faster than we grab.
    target_interval = 1.0 / max(1, config.CAMERA_SAMPLE_FPS)
    while True:
        now = time.monotonic()
        if now - last_sent < target_interval:
            await asyncio.sleep(target_interval - (now - last_sent))
        last_sent = time.monotonic()

        if kind == "rgb":
            arr = grab_camera_frame()
            if arr is None:
                await asyncio.sleep(0.1)
                continue
            jpeg = frame_to_jpeg_bytes(arr)
        else:  # depth
            depth = grab_depth_frame()
            if depth is None:
                await asyncio.sleep(0.1)
                continue
            jpeg = depth_to_jpeg_bytes(depth)

        yield (
            MJPEG_BOUNDARY + b"\r\n"
            + b"Content-Type: image/jpeg\r\n"
            + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
            + jpeg + b"\r\n"
        )


# time import is lazy to avoid touching the module header; control_api already
# imports asyncio but not time.
import time  # noqa: E402


@app.get("/camera.mjpg")
async def camera_stream():
    """Live RGB camera feed. <img src="/camera.mjpg"> in any browser."""
    return StreamingResponse(
        _mjpeg_stream("rgb"),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/depth.mjpg")
async def depth_stream():
    """Live depth map (grayscale: dark=near, bright=far, clipped at 30m).

    Returns 503 if no depth frame has been seen yet (camera up but Pegasus
    depth patch not applied, or sim still initialising)."""
    return StreamingResponse(
        _mjpeg_stream("depth"),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# WS /events
# ---------------------------------------------------------------------------
@app.websocket("/events")
async def events_ws(ws: WebSocket):
    await ws.accept()
    eq = subscribe()
    try:
        while True:
            try:
                event = eq.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            payload = json.loads(
                json.dumps(event, default=str).replace("Infinity", "null")
            )
            await ws.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe(eq)
