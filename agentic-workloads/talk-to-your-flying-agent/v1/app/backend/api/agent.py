"""Strands agent tools, planner, and mission loop."""

import asyncio
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from strands import Agent, tool
from strands.models.bedrock import BedrockModel

from api import config
from api.events import emit
from api.sensors import (
    depth_to_png_bytes,
    frame_to_png_bytes,
    get_obstacle_distances,
    grab_camera_frame,
    grab_depth_frame,
    save_depth_frame,
    save_frame,
)

# ---------------------------------------------------------------------------
# Shared Bedrock model. Region + model pinned explicitly (see DEC-009).
# To swap model or region, edit `api/config.py` — do NOT hardcode here.
# ---------------------------------------------------------------------------
_bedrock_model = BedrockModel(
    region_name=config.AWS_BEDROCK_REGION,
    model_id=config.BEDROCK_MODEL_PLANNER,
)


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
command_queue: queue.Queue = queue.Queue()
mission_active = False


@dataclass
class MissionMemory:
    intention: str = ""
    plan: str = ""
    observations: list = field(default_factory=list)
    actions_taken: list = field(default_factory=list)
    task_complete: bool = False
    # NED metres, relative to arm-time home position. altitude_m is positive-up.
    last_position: dict = field(default_factory=lambda: {
        "north_m": 0.0, "east_m": 0.0, "altitude_m": 0.0, "yaw_deg": 0.0,
    })
    captured_images: list = field(default_factory=list)
    # Written ~1 Hz by api/perception.py. Planner reads this instead of
    # waiting for its own vision call. Can be None before first update,
    # or contain {"error": ...} when perception is offline.
    latest_perception: dict | None = None
    # Most recent on-demand vision tool response. Distinct from the streaming
    # perception buffer — this captures the Q&A pair so the planner can say
    # "last tick I asked X, vision said Y, so now I'll..." across ticks.
    # Shape: {"time", "question", "answer", "image_path", "depth_path"}
    last_analysis_response: dict | None = None
    # Target confirmation (hard gate against false-success).
    # Set to True only by the `confirm_target` tool after the planner has
    # gathered explicit evidence. `mark_task_complete(success=True)` is
    # rejected unless this is True. Reset to False at mission start.
    target_verified: bool = False
    target_verification_evidence: str | None = None
    # --- Collaboration interrupt (Tier 1) ---
    # paused: mission_loop idles; command_consumer drains queue without acting;
    #         /send-command text is captured as human_input instead of routed.
    # halted: mission ends on next loop check; drone hovers (MAVSDK holds last
    #         setpoint). Human must type `land` for an actual landing.
    # human_input: list of {time, text} entries captured during pauses; the
    #         planner's next tick prompt leads with the latest one.
    # consecutive_failures: counted from action outcomes; auto-pause trigger.
    paused: bool = False
    halted: bool = False
    human_input: list = field(default_factory=list)
    consecutive_failures: int = 0

    def summary(self) -> str:
        return json.dumps({
            "intention": self.intention,
            "plan": self.plan,
            "position": self.last_position,
            "obstacles": get_obstacle_distances(),
            # Streaming perception (T1.5.2) — Qwen VL updates this ~1 Hz.
            # Planner reads this INSTEAD of calling analyze_camera for
            # routine scene awareness. None before first update; may
            # contain an "error" key when perception is offline.
            "latest_perception": self.latest_perception,
            # Most recent on-demand vision tool Q&A — lets the planner reason
            # "I asked X, got Y, therefore Z" across multiple ticks.
            "last_analysis_response": self.last_analysis_response,
            # 15 slots covers a full 360° scan sweep (4 observations) plus
            # subsequent mid-flight vision captures, giving the planner a
            # spatially-grounded history to reason over.
            "recent_observations": self.observations[-15:],
            "recent_actions": self.actions_taken[-10:],
            "captured_images": self.captured_images[-5:],
            "task_complete": self.task_complete,
            # Collaboration interrupt — only populated when the mission was
            # paused and a human typed something. Planner's next tick should
            # lead with the latest entry.
            "latest_human_input": (
                self.human_input[-1] if self.human_input else None
            ),
            "consecutive_failures": self.consecutive_failures,
            # Target-confirmation gate — see `confirm_target` tool. The
            # planner CANNOT call mark_task_complete(success=True) until
            # target_verified is True.
            "target_verified": self.target_verified,
            "target_verification_evidence": self.target_verification_evidence,
            # Last 3 non-ok outcomes, with the specific failure type. Lets
            # the planner (and a human reading the session log) distinguish
            # "blocked 3x in a row — walls" from "timed out 3x — drone is
            # stuck on something the lidar can't see." Different problems.
            "recent_failures": [
                {"action": a["action"], "outcome": a["outcome"], "result": a["result"]}
                for a in self.actions_taken
                if a.get("outcome") in {"failed_obstacle", "failed_timeout", "failed_safety", "partial"}
            ][-3:],
        }, indent=2)

    def record_action(self, action: str, result: str):
        """Record a tool-call outcome on the action log, classify it, and
        update the consecutive-failures counter. The classifier lets the
        outer loop auto-pause when the drone's been unable to make progress.
        """
        outcome = classify_outcome(action, result)
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": action,
            "result": result,
            "outcome": outcome,
        }
        self.actions_taken.append(entry)

        # Counter updates: any non-ok outcome increments, ok resets.
        # Note: `partial` and `ok` both reset because the drone DID move —
        # even if less than commanded, we made progress. Only full failures
        # (blocked, timed-out, safety-braked) count as failures.
        if outcome in {"failed_obstacle", "failed_timeout", "failed_safety"}:
            self.consecutive_failures += 1
        elif outcome in {"ok", "partial"}:
            self.consecutive_failures = 0
        # "noop" actions (takeoff when already flying, etc.) don't change
        # the counter in either direction.

    def record_observation(self, description: str):
        self.observations.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "position": dict(self.last_position),
            "description": description,
        })


# ---------------------------------------------------------------------------
# Outcome classification (T1.2) — turn the command_consumer's result string
# into a structured label the planner + auto-pause logic can reason about.
# ---------------------------------------------------------------------------

def classify_outcome(action: str, result: str) -> str:
    """Map a tool-call result string to one of a fixed vocabulary.

    Labels:
      ok              — moved commanded distance, no obstacles hit.
      partial         — moved some distance but less than commanded (actual < 50% commanded).
      failed_obstacle — BLOCKED or EMERGENCY stop from lidar.
      failed_timeout  — altitude change or move didn't complete in time budget.
      failed_safety   — safety monitor triggered a hard brake.
      noop            — no-op outcome (already flying, not flying, etc.).
      ok_nonmove      — non-movement tool (rotate, capture, observe, plan) succeeded.
    """
    r = (result or "").lower()
    a = (action or "").lower()

    # Safety / emergency / blocked prefixes come from drone.py's
    # _continuous_move return strings (T0.4 closed-loop reporting).
    if "safety brake" in r:
        return "failed_safety"
    if "emergency stop" in r:
        return "failed_obstacle"
    if "blocked" in r:
        return "failed_obstacle"
    if "timed out" in r:
        return "failed_timeout"

    # Detect partial move: string contains "commanded Xm, actual Ym" and Y/X < 0.5
    # Example: "Moved forward: commanded 3.0m, actual 0.4m." -> partial
    if "commanded" in r and "actual" in r:
        try:
            import re
            m = re.search(r"commanded\s+[^\d]*([\d.]+)m?.*?actual\s+[^\d]*([\d.]+)m?", r)
            if m:
                commanded = float(m.group(1))
                actual = float(m.group(2))
                if commanded > 0.1 and actual / commanded < 0.5:
                    return "partial"
        except Exception:
            pass

    # Noops: "already flying", "not flying", "landing initiated"
    if "already flying" in r or "not flying" in r:
        return "noop"

    # Non-movement tools that succeeded (rotate, capture, observation etc.)
    non_move_prefixes = ("rotate_", "capture_", "update_plan", "record_observation",
                         "analyze_camera", "mark_task_complete")
    if any(a.startswith(p) for p in non_move_prefixes):
        return "ok_nonmove"

    # Otherwise: movement tool that returned a normal result → ok.
    return "ok"


memory = MissionMemory()


# ---------------------------------------------------------------------------
# Strands tools
# ---------------------------------------------------------------------------
@tool
def takeoff() -> str:
    """Take off the drone. Use at the start of any mission if not already flying."""
    command_queue.put("takeoff")
    return "Takeoff queued."

@tool
def land() -> str:
    """Land the drone. Use when the mission is complete or user asks to land."""
    command_queue.put("land")
    return "Land queued."

@tool
def move_forward(distance: float = 2.0) -> str:
    """Move the drone forward continuously over the given distance.
    Always pass the exact distance the user specifies.

    Args:
        distance: Distance in metres to travel. Must match user's request. Defaults to 2.0.
    """
    command_queue.put(("move_forward", distance))
    return f"Move forward {distance:.1f}m queued."

@tool
def move_backward(distance: float = 2.0) -> str:
    """Move the drone backward continuously over the given distance.
    Always pass the exact distance the user specifies.

    Args:
        distance: Distance in metres to travel. Must match user's request. Defaults to 2.0.
    """
    command_queue.put(("move_backward", distance))
    return f"Move backward {distance:.1f}m queued."

@tool
def move_left(distance: float = 2.0) -> str:
    """Strafe the drone left continuously over the given distance.
    Always pass the exact distance the user specifies.

    Args:
        distance: Distance in metres to travel. Must match user's request. Defaults to 2.0.
    """
    command_queue.put(("move_left", distance))
    return f"Move left {distance:.1f}m queued."

@tool
def move_right(distance: float = 2.0) -> str:
    """Strafe the drone right continuously over the given distance.
    Always pass the exact distance the user specifies.

    Args:
        distance: Distance in metres to travel. Must match user's request. Defaults to 2.0.
    """
    command_queue.put(("move_right", distance))
    return f"Move right {distance:.1f}m queued."

@tool
def change_altitude(meters: float) -> str:
    """Change altitude by N metres (positive=up, negative=down) and hold.

    Args:
        meters: Altitude delta in metres.
    """
    command_queue.put(("change_altitude", meters))
    return f"Altitude change {meters:+.1f}m queued."

@tool
def rotate_left(angle: float) -> str:
    """Smoothly rotate the drone left (counter-clockwise) by the given angle.

    You MUST always provide the angle explicitly — there is no default.
    Common mappings:
    - "turn left" with no angle specified → 90.0
    - "turn left 45" → 45.0
    - "turn around" → 180.0
    - "spin around" → 360.0

    Args:
        angle: Rotation in degrees. REQUIRED — always pass a value.
    """
    command_queue.put(("rotate_left", angle))
    return f"Rotate left {angle:.0f}° queued."

@tool
def rotate_right(angle: float) -> str:
    """Smoothly rotate the drone right (clockwise) by the given angle.

    You MUST always provide the angle explicitly — there is no default.
    Common mappings:
    - "turn right" with no angle specified → 90.0
    - "turn right 45" → 45.0
    - "turn around" → 180.0
    - "spin around" → 360.0

    Args:
        angle: Rotation in degrees. REQUIRED — always pass a value.
    """
    command_queue.put(("rotate_right", angle))
    return f"Rotate right {angle:.0f}° queued."

@tool
def capture_camera() -> str:
    """Capture and save a photo from the drone camera."""
    command_queue.put("capture_camera")
    return "Camera capture queued."

@tool
def update_plan(new_plan: str) -> str:
    """Update the mission plan. Call this to revise the high-level strategy.

    Args:
        new_plan: The new plan text.
    """
    memory.plan = new_plan
    emit("plan_updated", {"plan": new_plan})
    return "Plan updated."

@tool
def record_observation(description: str) -> str:
    """Record what you see or notice into mission memory.

    Args:
        description: Free-text description of what was observed.
    """
    memory.record_observation(description)
    emit("observation", {"description": description})
    return "Observation recorded."


@tool
def scan_360(step_deg: float = 90.0, settle_s: float = 2.2) -> dict:
    """Rotate through a full turn, capturing a perception snapshot at each heading.

    Condenses what would otherwise be ~8 planner ticks (rotate → wait → observe,
    ×4) into a single tool call. Each heading's snapshot is also pose-tagged and
    appended to `memory.observations` — so later ticks see the same spatial record
    they'd have built manually.

    Returns a compass dict plus the top-ranked-by-clearance headings, so you
    can decide the next move in one more LLM turn instead of eight.

    Use this when:
    - You just need a quick "what's around me" at mission start or after a block.
    - You don't expect to abort mid-rotation.

    Don't use this when:
    - You're actively tracking a moving subject and need to react mid-turn.
    - You've already scanned this position (check `memory.observations` — look
      for entries tagged with your current `position`).

    Args:
        step_deg: Degrees per rotation step. 90.0 gives 4 cardinals (default).
                  Use 45.0 for 8-point compass if you need finer resolution.
        settle_s: Seconds to wait after each rotation before reading perception
                  (perception worker refreshes at PERCEPTION_INTERVAL_S; this
                  needs to be >= that interval, default 2.2s for the 2Hz worker).

    Returns:
        Dict with keys:
          - `compass`: {yaw_deg → {clearance_front, clearance_left, clearance_right,
                                   scene, object_count, issues}}
          - `ranked_by_clearance`: [(yaw, front_clearance_m), ...] descending; null
                                   clearances sink to the bottom.
          - `position_at_start`: NED position when scan began.
          - `already_observed`: True if observations at this rounded position
                                 already exist (tool still runs but planner may skip).
    """
    # Guard against pathological step_deg
    if step_deg <= 0 or step_deg >= 360:
        return {"error": f"invalid step_deg={step_deg}; use 45 or 90"}
    n_steps = int(round(360.0 / step_deg))
    settle_s = max(settle_s, float(os.getenv("PERCEPTION_INTERVAL_S", "2.0")) + 0.2)

    from api.drone import get_position  # delayed — drone module imports agent

    # Snapshot start position so we can tag observations even as the drone yaws.
    try:
        start_pos = asyncio.run(get_position()) if not asyncio.get_event_loop().is_running() \
            else _block_on_coro(get_position())
    except Exception:
        # get_position() may fail if MAVSDK isn't attached yet — fall through.
        start_pos = dict(memory.last_position) if memory.last_position else {}

    # Check if we've scanned this rounded position already — useful to the planner
    # but NOT a blocker, we still do the scan (planner may have moved slightly).
    already = False
    if start_pos:
        key = (
            round(start_pos.get("north_m", 0)),
            round(start_pos.get("east_m", 0)),
            round(start_pos.get("altitude_m", 0)),
        )
        for obs in memory.observations[-16:]:
            p = obs.get("position") or {}
            if (
                round(p.get("north_m", 0)) == key[0]
                and round(p.get("east_m", 0)) == key[1]
                and round(p.get("altitude_m", 0)) == key[2]
            ):
                already = True
                break

    compass: dict[str, dict] = {}
    for i in range(n_steps):
        if i > 0:
            # Queue a rotation and wait for command_consumer to drain it.
            command_queue.put(("rotate_right", step_deg))
            _wait_for_queue_drain(timeout_s=10.0)
        # Let streaming perception settle on the new view.
        time.sleep(settle_s)

        perception = dict(memory.latest_perception or {})
        clearances = perception.get("clearances") or {}
        # Read yaw from live telemetry so it reflects actual, not commanded.
        try:
            cur_pos = _block_on_coro(get_position())
        except Exception:
            cur_pos = start_pos

        yaw = round(cur_pos.get("yaw_deg", i * step_deg), 1)

        entry = {
            "yaw_deg": yaw,
            "clearance_front_m": clearances.get("front_m"),
            "clearance_left_m": clearances.get("left_m"),
            "clearance_right_m": clearances.get("right_m"),
            "scene": perception.get("scene"),
            "object_count": len(perception.get("objects") or []),
            "issues": perception.get("issues"),
        }
        compass[f"yaw_{yaw}"] = entry

        # Append to memory.observations so cross-tick spatial memory still works.
        description = (
            f"scan yaw {yaw}°: {entry['scene'] or '(no scene)'} | "
            f"front={entry['clearance_front_m']} left={entry['clearance_left_m']} "
            f"right={entry['clearance_right_m']}"
        )
        memory.observations.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "position": dict(cur_pos),
            "description": description,
        })

    # Rank headings by front clearance (None sinks to bottom).
    ranked = sorted(
        compass.items(),
        key=lambda kv: (kv[1]["clearance_front_m"] is None,
                        -(kv[1]["clearance_front_m"] or 0)),
    )
    ranked_out = [(k.replace("yaw_", "") + "°", v["clearance_front_m"]) for k, v in ranked]

    emit("scan_360_complete", {
        "n_headings": n_steps,
        "ranked_by_clearance": ranked_out,
        "already_observed": already,
    })

    return {
        "compass": compass,
        "ranked_by_clearance": ranked_out,
        "position_at_start": start_pos,
        "already_observed": already,
    }


def _wait_for_queue_drain(timeout_s: float = 10.0) -> bool:
    """Block until command_queue is empty (i.e. the rotation has been dispatched).

    Command_consumer in control_api.py pulls from this queue and executes via
    MAVSDK; we wait for the queue to drain before assuming the rotation is done.
    Returns True on drain, False on timeout. ~0.05s polling matches the consumer.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if command_queue.empty():
            # Queue drained — give the drone a moment to actually complete the move
            # since command_consumer fires-and-forgets; empirically rotate takes
            # ~1.5s for 90°.
            time.sleep(1.6)
            return True
        time.sleep(0.1)
    return False


def _block_on_coro(coro):
    """Run an async coroutine from a sync @tool context.

    Strands @tool bodies are synchronous; drone.get_position() is async. We
    spin up a fresh event loop in a worker thread to avoid interfering with
    FastAPI's running loop.
    """
    import concurrent.futures

    def _run():
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_run).result(timeout=10.0)

@tool
def confirm_target(evidence: str) -> str:
    """Confirm that the mission's target has been found, with explicit evidence.

    Call this BEFORE `mark_task_complete(success=True)`. `mark_task_complete`
    will refuse to mark a successful outcome unless this has been called.

    The evidence string is the planner's case for why this is actually the
    target — not a guess. Recorded to the session log so a human can audit.

    Good evidence:
      "Wooden pallet visible at centre, ~2.3m ahead per depth map. I see
       the characteristic horizontal slats with gaps between them, and a
       yellow barcode sticker on the front-left corner."

    Bad evidence (will still set the flag, but the audit log will make the
    bad decision obvious):
      "It looks orange"
      "I think this is a pallet"

    Args:
        evidence: Specific visual features of the target that distinguish it
                  from walls/containers/other objects. Include distance from
                  the depth map if available, and any text (labels/tags) read
                  from the object.
    """
    memory.target_verified = True
    memory.target_verification_evidence = evidence
    # Snapshot the frame the planner was LOOKING AT when it made this call,
    # so an eval reviewer can judge whether the evidence matches the image.
    from api.session import capture_decision_frame
    frame = capture_decision_frame("target_confirmed")
    emit("target_confirmed", {"evidence": evidence, "frame": frame})
    return f"Target confirmed. Evidence: {evidence[:200]}"


@tool
def mark_task_complete(success: bool = True, summary: str = "") -> str:
    """End the current mission.

    Args:
        success: True if the mission's intention was fulfilled; False if you
                 exhausted options without finding the target. Honest answers
                 only — don't claim success to end the mission.
        summary: One-sentence summary of outcome for the human (what you found,
                 or why you gave up).
    """
    # Hard gate: refuse success without target confirmation. Prevents the
    # failure mode from 2026-04-30 morning session where the planner
    # declared victory on a wall without ever calling analyze_camera to
    # verify. The planner must call confirm_target(evidence) first.
    if success and not memory.target_verified:
        return (
            "REFUSED: cannot mark mission successful without target confirmation. "
            "Call `confirm_target(evidence=\"...\")` first with specific visual "
            "evidence that the target was actually found. If you cannot confirm "
            "the target, call `mark_task_complete(success=False, summary=...)` "
            "to end the mission honestly."
        )
    memory.task_complete = True
    # Snapshot the final frame so eval reviewers can see what the drone saw
    # when the mission ended — matters even for success=False (did we miss
    # a pallet that was right there?).
    from api.session import capture_decision_frame
    frame = capture_decision_frame("mission_marked_complete")
    emit("mission_marked_complete", {
        "success": success,
        "summary": summary,
        "target_verified": memory.target_verified,
        "evidence": memory.target_verification_evidence,
        "frame": frame,
    })
    return f"Task marked complete (success={success}). {summary}"


# ---------------------------------------------------------------------------
# Vision analysis — capture + LLM in one step
# ---------------------------------------------------------------------------
VISION_SYSTEM_PROMPT = """\
You are a visual analyst on a drone. You receive:
- **Image 1**: RGB camera frame from the drone's forward-facing camera.
- **Image 2 (may be absent)**: a depth map of the same view. Each pixel encodes
  distance-to-surface: dark = near (0 m), bright = far (up to ~30 m). Max
  brightness (255) may indicate either "far away (>30 m)" or "no depth return"
  (sky, reflective or smooth surfaces) — treat such regions as `unclear`.

Answer the question directly. Base every claim on what is actually visible.

Rules:
- **Counting**: give a specific number. If uncertain, say "approximately N"
  with a range (e.g. "approximately 5–7").
- **Distances**: quote values from the depth map when available
  ("pallet ~4.2 m ahead, centre"). If a distance claim is not supportable
  from the depth map, say "unclear" — do not guess.
- **Positions**: describe objects as left / centre / right, and near / mid / far.
- **No depth image**: say so in one sentence and answer from RGB only with
  hedged distances ("probably within a few metres").
- **Blurry / dark / corrupt image**: say so in one sentence rather than
  hallucinating content.

Example response:
  3 pallets visible. Left pallet ~5.2 m, centre ~6.8 m, right ~8.1 m.
  Left pallet's front-right corner has torn shrink-wrap.
  Front-centre flight path clear to ~10 m."""

@tool
def analyze_camera(question: str) -> str:
    """Capture a photo from the drone camera and analyze it with vision AI.
    Use this when the user asks what the drone sees, asks to describe the scene,
    count objects, identify items, or any question about the visual environment.

    Args:
        question: The question or instruction about what to look for in the image.
    """
    # Delayed import avoids a circular dependency at module load time.
    from api.perception import invoke_qwen_vl_text

    emit("vision_capture", {"question": question})
    arr = grab_camera_frame()
    if arr is None:
        return "Camera timeout — could not capture image."

    path = save_frame(arr, "vision")
    memory.captured_images.append(path)
    png_bytes = frame_to_png_bytes(arr)

    # Grab a matching depth frame if available. RGB-only is a valid fallback.
    depth = grab_depth_frame()
    depth_png = None
    depth_path = None
    if depth is not None:
        depth_png = depth_to_png_bytes(depth)
        depth_path = save_depth_frame(depth, "vision")

    # Combine the vision system prompt with the planner's question so Qwen
    # treats it as a single request (no separate system-role needed in the
    # OpenAI-shape payload we use for Bedrock Qwen).
    full_prompt = f"{VISION_SYSTEM_PROMPT}\n\nQuestion: {question}"

    try:
        analysis = invoke_qwen_vl_text(png_bytes, depth_png, full_prompt)
        emit("vision_result", {
            "question": question,
            "analysis": analysis,
            "image_path": path,
            "depth_path": depth_path,
        })
        memory.record_observation(f"[vision] {analysis[:200]}")
        # Keep the Q&A available to the planner across ticks via the memory
        # summary. Distinct from record_observation (which compacts to a
        # one-line description) — here the planner sees the full question
        # it asked and the full answer it got.
        memory.last_analysis_response = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "question": question,
            "answer": analysis,
            "image_path": path,
            "depth_path": depth_path,
        }
        return analysis
    except Exception as e:
        emit("error", {"source": "vision", "message": str(e)})
        return f"Vision analysis failed: {e}"


# ---------------------------------------------------------------------------
# Planner agent
# ---------------------------------------------------------------------------
PLANNER_SYSTEM_PROMPT = """\
You are an autonomous drone mission planner. You control a quadrotor drone
in a simulated warehouse environment.

## Inputs you receive each tick
- **Intention**: the user's high-level goal.
- **Camera frame**: the latest image from the drone's onboard camera.
- **Memory**: JSON summary with plan, observations, actions, position, obstacles, images.

## Position format
`position` is `{north_m, east_m, altitude_m, yaw_deg}` — NED metres relative to the takeoff point. `altitude_m` is positive-up. Origin is wherever the drone armed; treat (0, 0) as "the spot you took off from," not world origin.

## Human-in-the-loop input (READ FIRST IF PRESENT)
Memory may contain `latest_human_input: {time, text}` when a person paused the mission to redirect you. **When this field is populated, it OVERRIDES your previous plan for this tick** — the human has new information or corrected intent. Priority order:
1. Read `latest_human_input.text` carefully.
2. If it contradicts your current plan, call `update_plan` with the revised strategy before any action.
3. If it's a specific instruction ("look at the box on the left", "go back and check aisle 2"), follow it.
4. If unclear, proceed with your existing plan but note the input in your reasoning.

The human is a teammate, not an obstacle. Their input is usually a correction or extra context. Don't ignore it.

## Streaming perception (NEW — read this FIRST each tick)
`latest_perception` is a ~1 Hz structured feed from a dedicated vision model
(Qwen VL). It refreshes automatically — you don't need to call a tool to
populate it. Shape:
```
{
  "scene": "<one-sentence summary of the current view>",
  "objects": [{"label", "position": "left|centre|right", "distance_m", "notes"}],
  "clearances": {"front_m", "left_m", "right_m"},
  "issues": "<'' if all good>",
  "timestamp": <unix seconds>,
  "has_depth": true|false,
  "latency_s": <how long that update took>
}
```

**Use `latest_perception.scene` + `.clearances` BEFORE choosing a direction.**
It's up to ~1s stale (and rarely contains `{"error": ...}` when the Qwen
call failed). Prefer it to calling `analyze_camera` for routine "what's
ahead?" questions. Reserve `analyze_camera` for specific follow-up questions
the streaming feed can't answer ("is that a pallet or a crate?", "read the
label on the box on the left shelf").

If `latest_perception` is `None` or has `{"error": ...}`, fall back to
`analyze_camera` — perception worker is offline and we shouldn't fly blind.

## Movement results
Each move returns both commanded and actual distance (e.g. `"commanded 3.0m, actual 2.7m"`). If actual is noticeably less than commanded, the drone hit something or the command didn't complete. Trust the actual number, not the commanded one, when reasoning about where you are.

## Spatial memory — reason over observations as a SET, not one at a time
Every entry in `recent_observations` is pose-tagged with the drone's position AND heading (`yaw_deg`) at the moment the observation was made. This is your mental map. Use it.

Before picking a direction:
- Look at the last 4-8 observations together.
- Which headings were made from which positions? E.g. "observation from (N=0, E=0) facing yaw=0° said wall; observation from (N=0, E=0) facing yaw=90° said open space with a pallet at ~5m."
- Combine them to form a world picture: "pallet is approximately at (N=0, E=5)." Not perfect triangulation, but good enough to pick a heading that aims toward what you want.
- If two observations from different positions describe the same thing (e.g. "brick wall with ladder"), that's the SAME landmark — don't treat it as two things.
- If every observation reports the same obstacle from every heading, you're boxed in. Land or ask the operator rather than pounding walls.

Always prefer "observation from (N, E) at yaw=θ°" language over vague "I saw earlier" when justifying a decision — it forces you to actually use the spatial data.

## Obstacle data
The `obstacles` field shows closest obstacle distance (metres) in 8 sectors:
front, front_right, right, back_right, back, back_left, left, front_left.
- Below 3.0m: drone slows down.  Below 1.5m: direction BLOCKED.  "Infinity": clear.

**CRITICAL OBSTACLE RULES:**
- ALWAYS check obstacles BEFORE calling any movement tool.
- NEVER move into a direction where relevant sectors are below 2.0m.
- If blocked, ROTATE first to find a clear path.

## Your job
1. Analyse the camera image briefly.
2. Check obstacles and avoid blocked directions.
3. Consult memory — what have you done? Where are you?
4. Call tools to advance the mission. Prefer 3-5m moves over many small steps.
   Always call `record_observation` after analysing the camera.
5. Update the plan if strategy needs to change.
6. When done, call `capture_camera` if needed, then `mark_task_complete`.

## Rules
- If not flying, call `takeoff` then `change_altitude(meters=5.0)` first.
- NEVER move into a blocked direction. Rotate or strafe first.
- Keep observations concise (one sentence).
- Do NOT loop forever. After ~20 actions without finding the target, mark complete with `success=False`.

## VERIFY BEFORE DECLARING SUCCESS (hard rule + code-enforced gate)

The single easiest failure mode is calling `mark_task_complete(success=True)` on something that isn't the target. There are BOTH a code-side guard and a behavioural expectation. Read both.

**Code guard:** `mark_task_complete(success=True)` is REFUSED by the tool unless you first called `confirm_target(evidence=<specific visual evidence>)`. Getting the refusal response back means you need to call `confirm_target` first, then retry the completion.

**What counts as good evidence for `confirm_target`:**

1. **The object matches the mission's target noun.** A "pallet" is a wooden shipping platform with visible slats and spaces beneath — not an orange wall, not a yellow panel, not a vehicle side. A "box" has six flat faces. A "person" is a human figure. If you're squinting at a texture and calling it the target, you're wrong.

2. **You MOVED to investigate.** Scan-sweep + rotations-in-place does NOT count as searching. If `recent_actions` contains zero `move_forward / move_backward / move_left / move_right` entries, you didn't search — you only spun around. Continue exploring.

3. **You called `analyze_camera` with an explicit verification question**, e.g. `analyze_camera("Is this a wooden pallet? Describe its slats, spacing, and any visible tags.")`. The answer must be SPECIFIC about features unique to the target — not ambiguous prose about surfaces or textures. The Q&A gets recorded in `memory.last_analysis_response` so you can cite it as evidence.

4. **`latest_perception.scene` shouldn't be ambiguous** ("close-up of a mustard-yellow surface" / "too dark to identify" = NOT verified). If Qwen refuses to label the object or returns `objects: []`, the target is not confirmed.

When you call `confirm_target`, the `evidence` argument should summarise what you saw, including distance from the depth map and any labels/tags visible on the object. This gets audited in the session log.

## When blocked, BACKTRACK before giving up (hard rule)

When a move under-executes (actual < 50% of commanded) or outright BLOCKs, do NOT:
- Rotate in place and try again from the SAME position
- Immediately call `mark_task_complete(success=False)` on the first few failures
- Keep nudging forward into the same obstacle

Instead, your DEFAULT next action should be:
1. **Reverse** — e.g. if `move_forward` failed, `move_backward` to where you had clear movement a tick ago. Read `position` from memory to confirm you actually backed up (actual > 50% of commanded).
2. From that earlier position, try a **different cardinal heading** — rotate 90° or 180° and move the NEW direction. Do NOT retry the heading that failed.
3. If the new heading also blocks, try the THIRD cardinal. You have four headings and multiple altitudes — there is almost always a way out you haven't tried.

**Only after** you have:
- Attempted moves in at least **3 distinct headings**
- From at least **2 distinct positions** (backing up and moving sideways counts as a new position)
- At **2 distinct altitudes** (try `change_altitude(+2)` or `(-2)` at some point — obstacles on the ground don't always exist at 5m)

…are you justified in calling `mark_task_complete(success=False)`. "I rotated 4 times at one spot and everything was close" is NOT "I searched the space."

**Obstacle sensor says Infinity but movement is blocked?** That's real — our lidar sometimes misses thin or reflective surfaces. Trust the commanded-vs-actual distance in your last move result, NOT the obstacle sensor.

## When you're truly stuck
You have genuinely exhausted options ONLY when the backtrack rule above has been applied in full. If 3 headings × 2 positions × 2 altitudes have all failed, then:
- Call `analyze_camera("I'm surrounded by walls at close range — what are my options? Is there a door or passage I'm missing?")` and follow the answer, OR
- Call `mark_task_complete(success=False)` with a summary of what was tried (cite the specific headings and altitudes you tested).

The mission loop auto-pauses after 3 consecutive failed actions and asks the human for help. If that happens, a human will see your failure count and may provide redirection via `latest_human_input`. Read it carefully when it arrives.

## OBSERVE BEFORE COMMITTING — mandatory scan at mission start
A human pilot entering an unknown indoor space doesn't fly blind. You don't either. Streaming perception gives you scenes automatically — just give it time to refresh at each heading.

**On the FIRST tick of every mission, after takeoff + climbing to ~3-5m, you MUST perform a 360° observation sweep BEFORE any horizontal movement:**
  1. Read `latest_perception` while facing the initial heading. Call `record_observation` summarising it by yaw:
     *"yaw 0°: white wall at ~2m, ladder visible; no pallet"*.
     The observation is already pose-tagged — you don't need to repeat the position in the text; just the yaw as a label helps you read the set back later.
  2. `rotate_right(90)`. Then **wait until `latest_perception.timestamp` has advanced** (it refreshes ~1 Hz) before recording — otherwise you're recording the view from the previous heading.
  3. Record the new heading's observation. Repeat rotate + wait + record for 180° and 270° (four observations total).
  4. Call `update_plan` with your chosen heading.
     **Reason over the four observations as a set**: which heading had the most open space (check `clearances.front_m`)? Which had the target? Which headings were walls?
     Write the plan in a way that references the observations by yaw:
     *"sweep showed walls at yaw 0/90/270; open corridor at yaw 180 (front_m=7.8); target likely south."*
  5. Only NOW do you start moving.

Do not skip this. Streaming perception makes the scan cheap (~1s per heading), but skipping it means flying blind.

**Shortcut: `scan_360()` tool.** If you just need a quick compass reading (4 headings, ranked by clearance) and don't expect to react mid-rotation, call `scan_360()` — it does the rotate+wait+observe cycle in code in ~10-15s total and returns a structured `{compass, ranked_by_clearance}` dict. Each heading still gets appended to your pose-tagged `observations`. Prefer `scan_360()` on tick 1 when you're simply characterising the space. Fall back to the manual 4-step sweep above when you may need to abort mid-rotation (e.g. you just glimpsed the target and want to stop turning).

**Only call `analyze_camera` during the sweep if `latest_perception.issues` is non-empty or `latest_perception` has an `error` field** — that's the perception feed telling you it can't read the current view and you need a fresh manual look.

## RE-OBSERVE ON BLOCK — don't retry blind
If a move returned `actual < commanded * 0.5` (you got blocked or stalled):
  1. Do NOT immediately rotate and retry in a new direction.
  2. FIRST call `analyze_camera("what's directly in front of me and to the sides?")` to build a fresh picture of what's actually around you.
  3. THEN call `update_plan` with the new understanding.
  4. THEN choose the next movement.

Rotating without looking gives you a different guess, not a better one.

## Self-check before each action
Before issuing the next tool call, briefly reason through:
1. **Expected position** — based on the last action's commanded delta, where should the drone be?
2. **Actual position** — what does `memory.last_position` say (NED metres: north / east / altitude / yaw)?
3. **Match?** — if the deltas disagree by more than ~0.5 m, something went wrong last tick.

If they don't match:
- Call `update_plan` with a one-line note on what diverged.
- Call `record_observation` to capture what you actually see now.
- Then choose the next action from reality, not from what you expected.

If the scene in the camera frame looks substantially different from what
memory expects (e.g. you thought you faced a wall, but see open floor),
call `record_observation` BEFORE any movement to re-ground your model.

Don't use this self-check to end the mission early — the outer loop handles
failure budgets. Your job is perception + honest re-planning, not termination.
"""

ALL_TOOLS = [
    takeoff, land,
    move_forward, move_backward, move_left, move_right,
    change_altitude, rotate_left, rotate_right,
    capture_camera,
    update_plan, record_observation, scan_360, confirm_target, mark_task_complete,
]

planner_agent = Agent(
    model=_bedrock_model,
    system_prompt=PLANNER_SYSTEM_PROMPT,
    tools=ALL_TOOLS,
    callback_handler=None,
)


# ---------------------------------------------------------------------------
# Mission loop
# ---------------------------------------------------------------------------
_planner_tick_counter = 0


def _build_planner_message(frame_png_bytes: bytes | None) -> list:
    content = []
    if frame_png_bytes:
        content.append({
            "image": {
                "format": "png",
                "source": {"bytes": frame_png_bytes},
            }
        })
    text = f"""## Current tick
**Intention**: {memory.intention}

**Memory**:
```json
{memory.summary()}
```

Analyse the camera image, decide your next actions, and call the appropriate tools."""
    content.append({"text": text})
    return content


def run_planner_tick():
    global _planner_tick_counter
    _planner_tick_counter += 1
    tick_id = _planner_tick_counter

    emit("planner_tick_start", {"tick": tick_id})

    arr = grab_camera_frame()
    frame_bytes = frame_to_png_bytes(arr) if arr is not None else None
    if arr is not None:
        save_frame(arr, f"tick_{tick_id:03d}")

    message_content = _build_planner_message(frame_bytes)
    try:
        result = planner_agent(message_content)
        resp_parts = result.message.get("content", [])

        # Log all parts — text and tool calls
        texts = []
        tool_calls = []
        for part in resp_parts:
            if isinstance(part, dict):
                if "text" in part and part["text"].strip():
                    texts.append(part["text"])
                elif "toolUse" in part:
                    tu = part["toolUse"]
                    tool_calls.append(f"{tu.get('name', '?')}({tu.get('input', {})})")

        summary = ""
        if texts:
            summary = " | ".join(t[:150] for t in texts)
        if tool_calls:
            tools_str = ", ".join(tool_calls)
            summary = f"{summary} | tools: {tools_str}" if summary else f"tools: {tools_str}"

        if summary:
            print(f"[planner] {summary[:400]}")
            emit("planner_response", {"tick": tick_id, "text": summary[:400]})
        else:
            print(f"[planner] (no text or tool calls in response)")
    except Exception as e:
        print(f"[planner] Error: {e}")
        emit("error", {"source": "planner", "tick": tick_id, "message": str(e)})


# --- Mission loop config ---
# Auto-pause threshold: at N consecutive failures, stop burning ticks and
# ask the human for help. See T1.2 / DISCUSSION Topic 8.
FAILURE_BUDGET = 3
MAX_TICKS = 30


def _init_mission():
    """Reset all per-mission state and (re-)create the planner agent.

    Called once at mission_loop entry. Separated from the loop so /tick-once
    debug callers don't have to duplicate this logic.
    """
    global mission_active, planner_agent, _planner_tick_counter

    print(f"[mission] Started — intention: {memory.intention}")
    memory.task_complete = False
    memory.plan = ""
    memory.observations.clear()
    memory.actions_taken.clear()
    memory.captured_images.clear()
    # Reset collaboration-interrupt state for a fresh mission
    memory.paused = False
    memory.halted = False
    memory.human_input.clear()
    memory.consecutive_failures = 0
    memory.target_verified = False
    memory.target_verification_evidence = None
    memory.last_analysis_response = None
    _planner_tick_counter = 0
    mission_active = True

    emit("mission_start", {"intention": memory.intention})

    planner_agent = Agent(
        model=_bedrock_model,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        tools=ALL_TOOLS,
        callback_handler=None,
    )


def tick_once(tick_number: int | None = None) -> dict:
    """Run exactly one planner tick. Usable from the mission loop OR from a
    debug endpoint (e.g. /tick-once) when you want to step through a mission
    manually.

    Returns a small status dict the outer driver uses to decide what's next:
        {"executed": bool,            # True if a tick ran (not paused/halted)
         "tick": <tick number>,
         "reason": <optional str>}    # 'halted', 'paused', 'executed'
    """
    global _planner_tick_counter

    if memory.halted:
        return {"executed": False, "tick": tick_number, "reason": "halted"}
    if memory.paused:
        return {"executed": False, "tick": tick_number, "reason": "paused"}

    print(f"\n{'='*40} TICK {tick_number} {'='*40}")
    run_planner_tick()

    # Wait up to 30s for queued MAVSDK commands to drain. If the consumer is
    # still busy after that, proceed to the next tick anyway — the planner
    # will see the in-progress state in memory.
    deadline = time.time() + 30.0
    while not command_queue.empty() and time.time() < deadline:
        time.sleep(0.3)
    time.sleep(1)
    return {"executed": True, "tick": tick_number, "reason": "executed"}


def _should_auto_pause(already_asked: bool) -> bool:
    """Return True if we've hit the failure budget and should auto-pause this
    tick to ask the human for help. `already_asked` latch prevents us from
    firing `mission_needs_help` every tick once we're past the threshold.
    """
    return (
        not already_asked
        and memory.consecutive_failures >= FAILURE_BUDGET
        and not memory.paused  # respect manual pause — don't double-fire
    )


def _emit_needs_help():
    """Trip the auto-pause + emit the event the UI surfaces as a red alert."""
    memory.paused = True

    # Compact summary of the last N failures by type — e.g.
    # ["failed_obstacle: BLOCKED after 0.5m...", "failed_timeout: Altitude..."]
    # Much more actionable for the human than a bare failure count.
    last_failures = [
        {"action": a["action"], "outcome": a["outcome"],
         "result": (a["result"] or "")[:120]}
        for a in memory.actions_taken
        if a.get("outcome") in {"failed_obstacle", "failed_timeout", "failed_safety", "partial"}
    ][-FAILURE_BUDGET:]

    # Compute a human-friendly headline reason from the mix
    outcomes = [f["outcome"] for f in last_failures]
    if all(o == "failed_obstacle" for o in outcomes):
        headline = "blocked repeatedly — drone may be boxed in"
    elif all(o == "failed_timeout" for o in outcomes):
        headline = "movements timing out — drone may be stuck physically"
    elif all(o == "failed_safety" for o in outcomes):
        headline = "safety brakes firing — obstacles too close"
    else:
        headline = f"{memory.consecutive_failures} consecutive failed actions"

    # Capture the scene the drone is stuck in — eval reviewers can see WHY
    # the planner gave up (genuinely boxed-in vs depth sensor blind vs etc).
    from api.session import capture_decision_frame
    frame = capture_decision_frame("mission_needs_help")
    emit("mission_needs_help", {
        "reason": headline,
        "failures": memory.consecutive_failures,
        "recent_failures": last_failures,
        "frame": frame,
    })
    print(f"[mission] AUTO-PAUSED — {headline}; waiting for human")


def mission_loop():
    """Outer driver. Delegates init + per-tick work to helpers so the control
    flow is obvious and individual pieces are independently testable."""
    global mission_active

    _init_mission()

    tick = 0
    already_asked_for_help = False

    while not memory.task_complete and tick < MAX_TICKS:
        # Halt fires a hard exit path — bypass everything else.
        if memory.halted:
            print("[mission] HALT — human requested stop.")
            emit("mission_end", {"reason": "halted", "ticks": tick})
            mission_active = False
            return

        # Manual pause — idle until human resumes.
        if memory.paused:
            time.sleep(0.5)
            continue

        # Auto-pause on failure budget — once per boundary crossing.
        if _should_auto_pause(already_asked_for_help):
            already_asked_for_help = True
            _emit_needs_help()
            continue

        # Once a resume brings failures back to zero, clear the latch so
        # we're allowed to ask again if NEW failures stack up after the
        # human's redirect.
        if already_asked_for_help and memory.consecutive_failures == 0:
            already_asked_for_help = False

        tick += 1
        tick_once(tick)

    # Exit reasons
    if memory.halted:
        emit("mission_end", {"reason": "halted", "ticks": tick})
    elif not memory.task_complete:
        print("[mission] Reached max ticks without completing.")
        emit("mission_end", {"reason": "max_ticks", "ticks": tick})
    else:
        print("[mission] Task complete!")
        emit("mission_end", {"reason": "complete", "ticks": tick})
    mission_active = False


# ---------------------------------------------------------------------------
# Voice / command agent (router)
# ---------------------------------------------------------------------------
DIRECT_CMD_SYSTEM_PROMPT = """\
You are a drone flight controller. You receive commands and either:
1. Execute a direct drone action (takeoff, land, move, rotate, capture, altitude).
2. Analyze what the drone camera sees (describe scene, count objects, identify items).
3. Start an autonomous mission if the user describes a complex task
   (e.g. "find orange objects", "explore the warehouse", "search for a person").

For direct actions, call the appropriate tool.
For visual questions (e.g. "what do you see", "count the boxes", "describe the scene",
"tell me what's in front"), call `analyze_camera` with the user's question.
For complex multi-step tasks, call `start_mission` with the user's intention.

CRITICAL ROTATION RULES — you MUST follow these exactly:
- "turn left" (no angle) → rotate_left(angle=90.0)
- "turn right" (no angle) → rotate_right(angle=90.0)
- "turn around" → rotate_left(angle=180.0)
- "spin around" or "turn 360" → rotate_left(angle=360.0)
- "turn left 90 degrees" → rotate_left(angle=90.0)
- "turn right 180" → rotate_right(angle=180.0)
- Any explicit angle → use that exact number

CRITICAL: When the user specifies a number (angle, distance, altitude), you MUST pass
that exact number to the tool. Examples:
- "move forward 5 meters" → move_forward(distance=5.0)
- "go up 3 meters" → change_altitude(meters=3.0)
Never ignore the user's specified values. Never use a default when the user gave a value."""


@tool
def start_mission(intention: str) -> str:
    """Start an autonomous mission. Use for complex multi-step tasks like searching,
    exploring, or finding objects.

    Args:
        intention: The user's high-level goal in plain English.
    """
    global mission_active
    if mission_active:
        return "A mission is already running."
    memory.intention = intention
    threading.Thread(target=mission_loop, daemon=True).start()
    return f"Mission started: {intention}"


voice_agent = Agent(
    model=_bedrock_model,
    system_prompt=DIRECT_CMD_SYSTEM_PROMPT,
    tools=[
        takeoff, land,
        move_forward, move_backward, move_left, move_right,
        change_altitude, rotate_left, rotate_right,
        capture_camera, analyze_camera, start_mission,
    ],
    callback_handler=None,
)


def process_command(text: str) -> str | None:
    """Feed text into the voice agent. Returns agent response text or None."""
    text = text.strip()
    if not text:
        return None
    print(f'\n[command] "{text}"')
    emit("command_input", {"text": text})
    try:
        result = voice_agent(text)
        resp_parts = result.message.get("content", [])
        for part in resp_parts:
            if isinstance(part, dict) and "text" in part:
                print(f"[agent] {part['text'][:200]}")
                emit("agent_response", {"text": part["text"]})
                return part["text"]
    except Exception as e:
        print(f"[agent] Error: {e}")
        emit("error", {"source": "agent", "message": str(e)})
        raise
    return None
