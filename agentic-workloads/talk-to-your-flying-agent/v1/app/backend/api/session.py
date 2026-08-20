"""Session recorder — writes every event-bus event to a per-session folder.

One recorder at a time. Start via POST /session/start, end via POST /session/end.

Layout on disk (gitignored dir):
    backend/sessions/
      2026-04-30_160157_abc12/
        events.jsonl   — one event per line, full payload
        summary.md     — human-readable wrap-up written on /session/end
"""

import asyncio
import json
import queue
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from api import config
from api.events import emit, subscribe, unsubscribe


# Repo-local sessions directory. Path is server-dir-relative so restarts with
# different cwd still work.
_SERVER_DIR = Path(__file__).resolve().parent.parent
SESSIONS_DIR = Path(
    __import__("os").getenv("SESSIONS_DIR", str(_SERVER_DIR / "sessions"))
)


@dataclass
class _ActiveSession:
    session_id: str
    started_at: str
    folder: Path
    events_file: object  # open file handle
    queue: "queue.Queue"
    task: asyncio.Task | None
    event_count: int = 0
    mission_count: int = 0
    first_mission_intention: str | None = None
    key_events: list = field(default_factory=list)  # first + last N for summary


_state_lock = threading.Lock()
_active: _ActiveSession | None = None


def _new_session_id() -> str:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    suffix = secrets.token_hex(2)
    return f"{ts}_{suffix}"


async def _drain_loop(session: _ActiveSession):
    """Pull events from the subscriber queue, write each as a JSON line."""
    while True:
        try:
            event = session.queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue

        try:
            line = json.dumps(event, default=str)
            session.events_file.write(line + "\n")
            session.events_file.flush()
        except Exception as e:
            # Don't let logging ever crash the server. Print and move on.
            print(f"[session] write failed: {e}")
            continue

        session.event_count += 1

        # Track interesting events for the final summary.
        etype = event.get("type", "")
        if etype == "mission_start":
            session.mission_count += 1
            intention = event.get("data", {}).get("intention")
            if session.first_mission_intention is None and intention:
                session.first_mission_intention = intention

        if etype in {
            "mission_start", "mission_end", "safety_brake", "error",
            "command_input", "vision_result",
        }:
            # Keep a running tail (caps at 20 so long sessions don't bloat)
            session.key_events.append(event)
            if len(session.key_events) > 40:
                # Keep first 5 + last 35 so we see mission starts early
                session.key_events = session.key_events[:5] + session.key_events[-35:]


def start_session() -> dict:
    """Begin a new recording session. Returns info about the started session.

    If a session is already active, returns info about that one (idempotent)."""
    global _active

    with _state_lock:
        if _active is not None:
            return {
                "status": "already_active",
                "session_id": _active.session_id,
                "folder": str(_active.folder),
                "started_at": _active.started_at,
                "event_count": _active.event_count,
            }

        session_id = _new_session_id()
        folder = SESSIONS_DIR / session_id
        folder.mkdir(parents=True, exist_ok=True)

        events_path = folder / "events.jsonl"
        events_file = open(events_path, "w", buffering=1)  # line-buffered

        q = subscribe()

        session = _ActiveSession(
            session_id=session_id,
            started_at=datetime.now().isoformat(),
            folder=folder,
            events_file=events_file,
            queue=q,
            task=None,
        )

        # Schedule the drain task on the running event loop.
        loop = asyncio.get_event_loop()
        session.task = loop.create_task(_drain_loop(session))

        _active = session

    # Emit a marker so the recording itself captures the boundary.
    emit("session_start", {
        "session_id": session_id,
        "folder": str(folder),
        "started_at": session.started_at,
        "config": {
            "planner_model": config.BEDROCK_MODEL_PLANNER,
            "region": config.AWS_BEDROCK_REGION,
        },
    })

    print(f"[session] started {session_id} → {folder}")
    return {
        "status": "started",
        "session_id": session_id,
        "folder": str(folder),
        "started_at": session.started_at,
    }


def _write_summary(session: _ActiveSession, ended_at: str) -> Path:
    """Render a human-readable summary alongside events.jsonl."""
    summary_path = session.folder / "summary.md"
    started = datetime.fromisoformat(session.started_at)
    ended = datetime.fromisoformat(ended_at)
    duration = ended - started

    lines = [
        f"# Session {session.session_id}",
        "",
        f"- Started: `{session.started_at}`",
        f"- Ended:   `{ended_at}`",
        f"- Duration: `{duration}`",
        f"- Events recorded: {session.event_count}",
        f"- Missions: {session.mission_count}",
        f"- Planner model: `{config.BEDROCK_MODEL_PLANNER}`",
        f"- Region: `{config.AWS_BEDROCK_REGION}`",
        "",
    ]
    if session.first_mission_intention:
        lines += [
            "## First mission intention",
            "",
            f"> {session.first_mission_intention}",
            "",
        ]
    if session.key_events:
        lines += [
            "## Key events (trimmed)",
            "",
            "Up to 40 events of type `mission_start / mission_end / command_input / "
            "vision_result / safety_brake / error`. Full firehose is in "
            "`events.jsonl`.",
            "",
        ]
        for ev in session.key_events:
            ts = ev.get("timestamp", "")
            etype = ev.get("type", "?")
            data = ev.get("data", {})
            if etype == "mission_start":
                snippet = f"mission START — `{data.get('intention', '?')}`"
            elif etype == "mission_end":
                snippet = f"mission END — reason `{data.get('reason', '?')}` after {data.get('ticks', '?')} ticks"
            elif etype == "command_input":
                snippet = f"command — {data.get('text', '?')}"
            elif etype == "vision_result":
                q = data.get('question', '?')
                a = (data.get('analysis') or "")[:120]
                snippet = f"vision Q: {q} → {a}"
            elif etype == "safety_brake":
                snippet = f"SAFETY brake at {data.get('min_distance', '?')}m"
            elif etype == "error":
                snippet = f"ERROR from {data.get('source', '?')}: {data.get('message', '?')}"
            else:
                snippet = json.dumps(data, default=str)[:120]
            lines.append(f"- `{ts}` **{etype}** — {snippet}")

    summary_path.write_text("\n".join(lines) + "\n")
    return summary_path


def end_session() -> dict:
    """Close the active session. Idempotent if none active."""
    global _active

    with _state_lock:
        session = _active
        if session is None:
            return {"status": "none_active"}
        _active = None

    # Emit the boundary marker BEFORE we tear down — so it lands in the log.
    ended_at = datetime.now().isoformat()
    emit("session_end", {
        "session_id": session.session_id,
        "ended_at": ended_at,
        "event_count": session.event_count + 1,  # +1 for this emit
        "mission_count": session.mission_count,
    })

    # Small pause so the drain loop picks up the session_end event.
    # We can't await here — this is a sync function. Kick the task to
    # let it drain whatever's already queued, then cancel.
    async def _finalise():
        await asyncio.sleep(0.2)
        if session.task and not session.task.done():
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        unsubscribe(session.queue)
        session.events_file.close()
        summary_path = _write_summary(session, ended_at)
        print(f"[session] ended {session.session_id} "
              f"({session.event_count} events, {session.mission_count} missions) "
              f"→ {summary_path}")

    loop = asyncio.get_event_loop()
    loop.create_task(_finalise())

    return {
        "status": "ended",
        "session_id": session.session_id,
        "folder": str(session.folder),
        "started_at": session.started_at,
        "ended_at": ended_at,
        "event_count": session.event_count + 1,
        "mission_count": session.mission_count,
    }


def session_status() -> dict:
    """Expose active-session metadata to /status consumers."""
    with _state_lock:
        if _active is None:
            return {"active": False}
        return {
            "active": True,
            "session_id": _active.session_id,
            "started_at": _active.started_at,
            "event_count": _active.event_count,
            "mission_count": _active.mission_count,
            "folder": str(_active.folder),
        }


def capture_decision_frame(label: str) -> dict | None:
    """Snapshot current RGB + depth into the active session's frames/ dir.

    Called at mission decision moments — target_confirmed, mark_task_complete,
    mission_needs_help — so eval reviewers can SEE what the drone saw when it
    made each call. Returns {"rgb": "<rel-path>", "depth": "<rel-path or null>"}
    so the caller can attach paths to the emitted event for downstream tooling.

    Returns None when no session is active (caller should still emit, just
    without frame paths). Never raises — logging must not crash the server.
    """
    with _state_lock:
        session = _active
    if session is None:
        return None

    # Delayed imports — api.sensors is imported by the main server module too,
    # keeping this local avoids any re-entrant import pain during startup.
    try:
        from api.sensors import (
            depth_to_png_bytes,
            frame_to_png_bytes,
            grab_camera_frame,
            grab_depth_frame,
        )
    except Exception as e:
        print(f"[session] frame capture: import failed: {e}")
        return None

    try:
        rgb = grab_camera_frame()
        if rgb is None:
            return None
        rgb_png = frame_to_png_bytes(rgb)

        depth_png = None
        depth = grab_depth_frame()
        if depth is not None:
            depth_png = depth_to_png_bytes(depth)

        ts = datetime.now().strftime("%H%M%S_%f")[:-3]  # ms precision
        frames_dir = session.folder / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        rgb_rel = f"frames/{label}_{ts}.png"
        (session.folder / rgb_rel).write_bytes(rgb_png)

        depth_rel = None
        if depth_png is not None:
            depth_rel = f"frames/{label}_{ts}_depth.png"
            (session.folder / depth_rel).write_bytes(depth_png)

        return {"rgb": rgb_rel, "depth": depth_rel}
    except Exception as e:
        print(f"[session] frame capture failed for {label}: {e}")
        return None
