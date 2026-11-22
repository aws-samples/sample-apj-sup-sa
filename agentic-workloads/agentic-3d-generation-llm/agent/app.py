# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AgentCore Runtime entrypoint: text prompt -> bpy script (LLM) -> Blender -> GLB.

The agent writes a script, runs it in headless Blender, and on failure sends the
error back to the same model to fix (up to MAX_ATTEMPTS). It returns the GLB
(base64), the final script, token usage and timings. The caller stores the files.
"""

import base64
import json
import logging
import os
import re
import time

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from blender_runner import BlenderError, run_script
from llm import Conversation, LlmError, Usage, chat
from script_guard import ScriptRejected, check_script

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("agent")

MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
# InvokeAgentRuntime is synchronous; stay well inside the caller's 15-minute budget.
DEADLINE_SECONDS = int(os.environ.get("DEADLINE_SECONDS", "690"))
MAX_PROMPT_CHARS = 1000

SYSTEM_PROMPT = """You are an expert 3D modeller who writes Blender 4.0 Python (bpy) scripts.
Write one complete script that builds the object the user describes, with recognisable shape, proportions and colours.

Rules:
- The scene starts empty. Build the model from bpy/bmesh/mathutils primitives, curves and modifiers. The exporter applies modifiers.
- Use real-world scale in metres, centre the model on the origin, rest it on the ground plane (z = 0), Z up.
- Give every mesh a material that uses the Principled BSDF node with Base Color, Metallic and Roughness set. Don't use image textures or procedural texture nodes: they don't export to glTF.
- Don't add cameras or lights, don't render, and don't export or save files. The harness exports the scene to GLB after your script runs.
- Only import from: bpy, bmesh, mathutils, math, random, colorsys, itertools, functools. No file, network, OS or subprocess access, no eval/exec/open, no dunder attributes.
- Use APIs that exist in Blender 4.0 (for example, material.node_tree.nodes["Principled BSDF"].inputs["Base Color"]).
- Keep the script under about 400 lines and the model under about 200,000 triangles.

Return only the script, in a single ```python code block."""

FIX_PROMPT = """The script failed when it ran in Blender 4.0:
```
{error}
```
Fix the problem and return the complete corrected script in a single ```python code block."""

CODE_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)

app = BedrockAgentCoreApp()


def extract_script(text: str) -> str:
    blocks = CODE_BLOCK.findall(text)
    if blocks:
        return max(blocks, key=len).strip()
    stripped = text.strip()
    if "import bpy" in stripped:
        return stripped
    if not stripped:
        raise ScriptRejected("The reply was empty: the model may have used its whole output budget on reasoning. "
                             "Think briefly and return only the script.")
    raise ScriptRejected("The reply did not contain a ```python code block.")


def generate(prompt: str, api: str, model_id: str, request_fields: dict | None = None,
             deadline: int = DEADLINE_SECONDS, max_tokens: int | None = None,
             mantle_path: str | None = None) -> dict:
    started = time.monotonic()
    convo = Conversation(system=SYSTEM_PROMPT, messages=[{"role": "user", "text": f"Create this 3D object: {prompt}"}])
    usage = Usage()
    blender_seconds = 0.0
    attempts = []
    script = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1 and time.monotonic() - started > deadline * 0.6:
            attempts.append({"attempt": attempt, "error": "Stopped retrying: time budget used up."})
            break
        try:
            reply, call_usage = chat(api, model_id, convo, request_fields, max_tokens, mantle_path)
        except LlmError as exc:
            log.warning("model=%s attempt=%d LLM error: %s", model_id, attempt, exc)
            attempts.append({"attempt": attempt, "error": str(exc)[:1000]})
            break
        usage.add(call_usage)
        convo.messages.append({"role": "assistant", "text": reply or "(empty reply)"})
        try:
            script = extract_script(reply)
            check_script(script)
            result = run_script(script)
            blender_seconds += result.seconds
            attempts.append({"attempt": attempt, "ok": True})
            log.info("model=%s attempt=%d ok glb_bytes=%d", model_id, attempt, len(result.glb))
            return {
                "status": "SUCCEEDED",
                "glbBase64": base64.b64encode(result.glb).decode(),
                "glbBytes": len(result.glb),
                **({"meshMetrics": result.metrics} if result.metrics else {}),
                "script": script,
                "attempts": attempts,
                "usage": usage.as_dict(),
                "timings": {
                    "agentSeconds": round(time.monotonic() - started, 2),
                    "llmSeconds": round(usage.seconds, 2),
                    "blenderSeconds": round(blender_seconds, 2),
                },
            }
        except (ScriptRejected, BlenderError) as exc:
            error = str(exc)
            log.info("model=%s attempt=%d failed: %s", model_id, attempt, error[:300])
            attempts.append({"attempt": attempt, "error": error[:1000]})
            convo.messages.append({"role": "user", "text": FIX_PROMPT.format(error=error)})

    return {
        "status": "FAILED",
        "error": attempts[-1].get("error", "Generation failed") if attempts else "Generation failed",
        "script": script,
        "attempts": attempts,
        "usage": usage.as_dict(),
        "timings": {
            "agentSeconds": round(time.monotonic() - started, 2),
            "llmSeconds": round(usage.seconds, 2),
            "blenderSeconds": round(blender_seconds, 2),
        },
    }


@app.entrypoint
def invoke(payload: dict) -> dict:
    prompt = str(payload.get("prompt", "")).strip()
    api = payload.get("api")
    model_id = payload.get("modelId")
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        return {"status": "FAILED", "error": f"prompt must be 1-{MAX_PROMPT_CHARS} characters"}
    if api not in ("converse", "mantle") or not isinstance(model_id, str) or not model_id:
        return {"status": "FAILED", "error": "api must be converse or mantle, and modelId is required"}
    request_fields = payload.get("requestFields")
    if request_fields is not None and (not isinstance(request_fields, dict) or len(json.dumps(request_fields)) > 2000):
        return {"status": "FAILED", "error": "requestFields must be a small JSON object"}
    # A retried call (after a cold-start timeout) has less time left; never more than the default.
    deadline = payload.get("deadlineSeconds")
    deadline = min(int(deadline), DEADLINE_SECONDS) if isinstance(deadline, int) and deadline > 0 else DEADLINE_SECONDS
    max_tokens = payload.get("maxOutputTokens")
    max_tokens = max_tokens if isinstance(max_tokens, int) and 256 <= max_tokens <= 128_000 else None
    mantle_path = payload.get("mantlePath")
    mantle_path = mantle_path if isinstance(mantle_path, str) else None  # llm.chat checks it against an allowlist
    try:
        return generate(prompt, api, model_id, request_fields, deadline, max_tokens, mantle_path)
    except LlmError as exc:
        log.warning("model=%s LLM error: %s", model_id, exc)
        return {"status": "FAILED", "error": str(exc)[:1000]}


if __name__ == "__main__":
    app.run()
