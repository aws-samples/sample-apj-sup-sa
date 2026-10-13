"""Streaming perception worker (T1.5.2).

A background asyncio task that runs Bedrock Qwen VL against the latest
camera + depth frame at ~1 Hz and writes structured observations to
`memory.latest_perception`. Planner ticks read the buffer instead of
blocking on their own vision call.

Trade-off at 1 Hz: perception is up to ~1s stale when the planner reads
it. For indoor flight at our movement speeds that's fine — the drone
moves < 0.5 m/s during scan segments.

If the Bedrock call fails, we record the error in `latest_perception`
so the planner can see "perception offline — fall back to RGB tool
calls" rather than acting on stale data silently.
"""

import asyncio
import base64
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

from api import config
from api.events import emit
from api.sensors import (
    depth_to_png_bytes,
    frame_to_png_bytes,
    grab_camera_frame,
    grab_depth_frame,
)


# Tunable via env var so we can throttle live without restarting.
PERCEPTION_INTERVAL_S = float(os.getenv("PERCEPTION_INTERVAL_S", "1.0"))
PERCEPTION_MAX_TOKENS = int(os.getenv("PERCEPTION_MAX_TOKENS", "400"))

# Async Bedrock client — boto3 is sync, so we run each call in an executor.
_bedrock_runtime = boto3.client(
    "bedrock-runtime", region_name=config.AWS_BEDROCK_REGION
)


PERCEPTION_PROMPT = """\
You are a vision sensor for a drone. You receive the drone's current RGB
camera frame and (usually) a depth map. Return a COMPACT JSON observation
that the planner can read. Do not return any prose outside the JSON.

Depth map format (if present): dark=near, bright=far, max 30m. Pixels at
max brightness (255) may mean either very-far or no-depth-return (smooth
/ reflective surface, sky) — treat those as "unclear".

Return strictly this JSON shape:
{
  "scene": "<one short sentence, what the drone is looking at>",
  "objects": [
    {"label": "<type>", "position": "left|centre|right",
     "distance_m": <float or null>, "notes": "<optional short detail>"}
  ],
  "clearances": {
    "front_m": <float or null>,
    "left_m":  <float or null>,
    "right_m": <float or null>
  },
  "issues": "<'' if none, else short description — e.g. blurry, too dark,
             no depth return>"
}

Rules:
- If uncertain about a distance, set it to null rather than guess.
- `clearances` is how far it's CLEAR to move in that direction from the
  drone's current position — read from the depth map when available.
- Keep everything terse. Total output under ~200 tokens.
- Return only the JSON, nothing else.

## Describe close surfaces honestly — don't stonewall

If the drone is pressed against something (one surface fills most of
the frame, depth <1m for most pixels), DO describe what it is based
on the visible features:
- "grey brick wall with metal ladder at 0.4m"
- "orange container side with black base, ~0.6m ahead"
- "wooden crate, slats visible, ~0.5m"

Always fill in `clearances` from the depth map when present
(e.g. `front_m: 0.5`). `null` means "I genuinely couldn't read it",
NOT "I won't tell you." A concrete distance — even a small one — lets
the planner reverse or rotate. Nulls across the board just paralyse it.

What you MUST NOT do:
- Claim a close-up surface IS the mission target without evidence.
  Do not call a warehouse wall a pallet just because it's orange or
  wooden-textured. The planner has a separate `target_verified` gate;
  your job is honest description, not target identification.
- Guess at object type from pure texture at <0.3m (you really cannot
  tell a wall from a crate at that range). Say "wall or flat surface,
  ~0.3m" — vague but honest.

Populate `issues` only for actual sensor problems (blurry, dark, no
depth return, NaN in depth). `issues` is NOT the place to say "I'm
too close to identify" — describe the surface in `scene` instead."""


# ---------------------------------------------------------------------------
# Shared Qwen invocation helpers — used by both the perception worker AND
# the on-demand `analyze_camera` tool. Keep payload format in one place.
# ---------------------------------------------------------------------------

def _build_payload(rgb_png: bytes, depth_png: bytes | None, prompt_text: str,
                   max_tokens: int = PERCEPTION_MAX_TOKENS) -> dict:
    """OpenAI-shape payload — verified against Bedrock Qwen spike (1e79907)."""
    rgb_b64 = base64.b64encode(rgb_png).decode("ascii")
    content = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{rgb_b64}"}},
    ]
    if depth_png is not None:
        depth_b64 = base64.b64encode(depth_png).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{depth_b64}"}}
        )
    content.append({"type": "text", "text": prompt_text})

    return {
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }


def _invoke_sync(payload: dict) -> dict:
    """Sync call to Bedrock. Meant to be wrapped in asyncio.to_thread."""
    resp = _bedrock_runtime.invoke_model(
        modelId=config.BEDROCK_MODEL_VISION,
        body=json.dumps(payload),
    )
    raw = resp["body"].read()
    return json.loads(raw)


def _parse_observation(response: dict) -> dict:
    """Extract the JSON observation from Qwen's response.

    Qwen sometimes wraps JSON in markdown fences — strip those defensively.
    Returns {"error": ...} on any parse issue so the planner sees it.
    """
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return {"error": f"envelope mismatch: {e}"}

    if not isinstance(content, str):
        return {"error": "content not a string"}

    text = content.strip()
    # Strip ```json ... ``` fences if the model included them
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Model returned prose — store as scene summary rather than fail hard.
        return {"scene": text[:200], "issues": "qwen returned prose, not JSON"}


def invoke_qwen_vl_text(rgb_png: bytes, depth_png: bytes | None, prompt: str,
                        max_tokens: int = 512) -> str:
    """Synchronous Qwen VL call that returns free-text (NOT structured JSON).

    Used by `analyze_camera` for on-demand scene questions where we want
    a human-readable answer rather than the structured observation schema
    the perception worker uses.

    Returns the raw text from Qwen's response. Raises on Bedrock errors
    (the tool caller should catch and surface to the event bus).
    """
    payload = _build_payload(rgb_png, depth_png, prompt, max_tokens=max_tokens)
    response = _invoke_sync(payload)
    try:
        content = response["choices"][0]["message"]["content"]
        if isinstance(content, str):
            # Strip markdown fences if Qwen decides to wrap the answer
            text = content.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if len(lines) >= 2:
                    text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
            return text
        return str(content)
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"unexpected Qwen response shape: {e}") from e


async def perception_worker(memory):
    """Background loop: grab frames → invoke Qwen → write memory.latest_perception.

    Runs forever; driven by the FastAPI lifespan. Cancellation-safe.

    Gating: the worker only invokes Qwen when a mission is active OR a session
    is being recorded. Idle periods (no mission, no recording) sleep cheaply
    so we don't burn Bedrock calls on a parked drone staring at a wall — which
    was ~$5-10/hour of waste before this gate landed.
    """
    print(f"[perception] worker started @ {1/PERCEPTION_INTERVAL_S:.1f} Hz "
          f"(model={config.BEDROCK_MODEL_VISION}, gated on mission_active | session)")
    consecutive_failures = 0

    while True:
        # Idle gate — sleep cheaply when nothing's happening. Lazy-import to
        # avoid a module-load-time circular with api.agent / api.session.
        from api import agent as _agent
        from api import session as _session
        session_state = _session.session_status()
        should_run = _agent.mission_active or session_state.get("active", False)
        if not should_run:
            await asyncio.sleep(PERCEPTION_INTERVAL_S)
            continue

        loop_start = time.time()
        try:
            rgb = grab_camera_frame()
            if rgb is None:
                # Sim not up yet, or ROS not subscribing. Back off and retry.
                memory.latest_perception = {
                    "timestamp": time.time(),
                    "error": "camera frame unavailable",
                }
                await asyncio.sleep(PERCEPTION_INTERVAL_S)
                continue

            depth = grab_depth_frame()  # may be None, that's fine
            rgb_png = frame_to_png_bytes(rgb)
            depth_png = depth_to_png_bytes(depth) if depth is not None else None

            payload = _build_payload(rgb_png, depth_png, PERCEPTION_PROMPT)
            response = await asyncio.to_thread(_invoke_sync, payload)
            obs = _parse_observation(response)
            obs["timestamp"] = time.time()
            obs["has_depth"] = depth_png is not None
            obs["latency_s"] = round(time.time() - loop_start, 2)

            memory.latest_perception = obs
            consecutive_failures = 0

            # Surface on the event bus so the WS log and recorder see it.
            emit("perception_update", {
                "scene": obs.get("scene"),
                "object_count": len(obs.get("objects", []) or []),
                "clearances": obs.get("clearances"),
                "latency_s": obs["latency_s"],
                "has_depth": obs["has_depth"],
                "issues": obs.get("issues") or obs.get("error") or "",
            })

        except ClientError as e:
            consecutive_failures += 1
            err = f"{e.response.get('Error', {}).get('Code', 'ClientError')}: " \
                  f"{e.response.get('Error', {}).get('Message', '')[:120]}"
            memory.latest_perception = {
                "timestamp": time.time(),
                "error": err,
                "consecutive_failures": consecutive_failures,
            }
            if consecutive_failures == 1 or consecutive_failures % 10 == 0:
                print(f"[perception] Bedrock call failed ({consecutive_failures}×): {err}")
                emit("error", {"source": "perception", "message": err})

        except asyncio.CancelledError:
            print("[perception] worker cancelled")
            raise

        except Exception as e:
            consecutive_failures += 1
            memory.latest_perception = {
                "timestamp": time.time(),
                "error": f"worker error: {e!r}",
                "consecutive_failures": consecutive_failures,
            }
            if consecutive_failures == 1 or consecutive_failures % 10 == 0:
                print(f"[perception] worker error ({consecutive_failures}×): {e!r}")

        # Honour the interval regardless of how long the call took.
        elapsed = time.time() - loop_start
        if elapsed < PERCEPTION_INTERVAL_S:
            await asyncio.sleep(PERCEPTION_INTERVAL_S - elapsed)
