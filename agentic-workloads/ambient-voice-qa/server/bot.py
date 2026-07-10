#
# Copyright (c) 2024-2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Ambient Voice QA for Manufacturing/Warehouse Workers - Pipecat Voice Agent.

Hands-free voice AI agent that walks factory workers through quality
inspection checklists conversationally, validates readings against
thresholds in real-time, and auto-flags anomalies.

Pipeline: Deepgram Nova-3 STT (with keyterm boosting) -> Bedrock Claude
(function calling) -> Deepgram Aura TTS, orchestrated by Pipecat.
"""

import os
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import DailyRunnerArguments, RunnerArguments
from pipecat.services.aws.llm import AWSBedrockLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.daily.transport import DailyParams, DailyTransport

load_dotenv(override=True)


# ---------------------------------------------------------------------------
# Sample QA inspection checklist for a hydraulic pump assembly station.
# Thresholds drive real-time anomaly detection.
# ---------------------------------------------------------------------------
CHECKLIST: list[dict[str, Any]] = [
    {
        "id": "PUMP-001",
        "step": 1,
        "title": "Hydraulic line pressure",
        "prompt": "Read the pressure gauge on the main hydraulic line. What is the reading in PSI?",
        "unit": "PSI",
        "type": "numeric",
        "min": 2800,
        "max": 3200,
        "keyterms": ["PSI", "pressure", "gauge"],
    },
    {
        "id": "PUMP-002",
        "step": 2,
        "title": "Drive shaft RPM",
        "prompt": "Power on the drive shaft and read the tachometer. What is the RPM?",
        "unit": "RPM",
        "type": "numeric",
        "min": 1700,
        "max": 1900,
        "keyterms": ["RPM", "tachometer", "drive shaft"],
    },
    {
        "id": "PUMP-003",
        "step": 3,
        "title": "Mounting bolt torque",
        "prompt": "Use the torque wrench on the four mounting bolts. What is the torque reading in foot-pounds?",
        "unit": "ft-lb",
        "type": "numeric",
        "min": 75,
        "max": 90,
        "keyterms": ["torque", "wrench", "foot-pounds", "ft-lb", "bolt"],
    },
    {
        "id": "PUMP-004",
        "step": 4,
        "title": "Coolant temperature",
        "prompt": "After two minutes of runtime, read the coolant temperature in degrees Fahrenheit.",
        "unit": "F",
        "type": "numeric",
        "min": 140,
        "max": 195,
        "keyterms": ["coolant", "temperature", "degrees", "Fahrenheit"],
    },
    {
        "id": "PUMP-005",
        "step": 5,
        "title": "Visual seal inspection",
        "prompt": "Visually inspect the gasket and shaft seal for leaks or cracks. Is the seal intact? Yes or no.",
        "type": "boolean",
        "expected": True,
        "keyterms": ["gasket", "seal", "leak", "crack"],
    },
]


def _all_keyterms() -> list[str]:
    terms: set[str] = set()
    for item in CHECKLIST:
        for t in item.get("keyterms", []):
            terms.add(t)
    # Domain vocabulary the worker is likely to use.
    terms.update(
        [
            "anomaly",
            "skip",
            "repeat",
            "go back",
            "complete",
            "pass",
            "fail",
            "out of spec",
            "in spec",
        ]
    )
    return sorted(terms)


class ChecklistState:
    """In-memory state for the active inspection session."""

    def __init__(self):
        self.current_index: int = 0
        self.results: list[dict[str, Any]] = [
            {
                "id": item["id"],
                "step": item["step"],
                "title": item["title"],
                "status": "pending",
                "value": None,
                "anomaly": False,
                "note": None,
            }
            for item in CHECKLIST
        ]

    def current(self) -> dict[str, Any] | None:
        if self.current_index >= len(CHECKLIST):
            return None
        return CHECKLIST[self.current_index]

    def snapshot(self) -> dict[str, Any]:
        current = self.current()
        return {
            "items": self.results,
            "current_index": self.current_index,
            "current_id": current["id"] if current else None,
            "complete": self.current_index >= len(CHECKLIST),
            "anomalies": [r for r in self.results if r.get("anomaly")],
        }


def _validate_reading(item: dict[str, Any], value: Any) -> tuple[bool, str]:
    """Return (in_spec, explanation)."""
    if item["type"] == "numeric":
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False, f"Could not parse {value!r} as a number"
        lo, hi = item["min"], item["max"]
        if v < lo:
            return False, f"{v} {item['unit']} is below the minimum of {lo} {item['unit']}"
        if v > hi:
            return False, f"{v} {item['unit']} is above the maximum of {hi} {item['unit']}"
        return True, f"{v} {item['unit']} is within spec ({lo}-{hi} {item['unit']})"
    if item["type"] == "boolean":
        truthy = str(value).strip().lower() in {"true", "yes", "y", "intact", "ok", "good", "pass"}
        if truthy == item["expected"]:
            return True, "Visual check passed"
        return False, "Visual check failed - seal/gasket not intact"
    return False, "Unknown check type"


# ---------------------------------------------------------------------------
# Pipecat function-tool schemas exposed to the LLM.
# ---------------------------------------------------------------------------
record_reading_fn = FunctionSchema(
    name="record_reading",
    description=(
        "Record the worker's reading for the current checklist step and validate it against "
        "the threshold. Call this as soon as the worker speaks a value."
    ),
    properties={
        "value": {
            "type": "string",
            "description": "The reading or answer the worker spoke (e.g. '3050', '85', 'yes').",
        },
        "raw_utterance": {
            "type": "string",
            "description": "The exact phrase the worker said, for the audit log.",
        },
    },
    required=["value"],
)

next_step_fn = FunctionSchema(
    name="next_step",
    description="Advance to the next checklist step. Call after a step is recorded or skipped.",
    properties={},
    required=[],
)

repeat_step_fn = FunctionSchema(
    name="repeat_step",
    description="Re-read the current checklist step's prompt to the worker.",
    properties={},
    required=[],
)

go_back_fn = FunctionSchema(
    name="go_back",
    description="Move back to the previous checklist step (e.g. worker wants to redo a reading).",
    properties={},
    required=[],
)

skip_step_fn = FunctionSchema(
    name="skip_step",
    description="Skip the current step (e.g. equipment unavailable). Marks the step as skipped.",
    properties={
        "reason": {
            "type": "string",
            "description": "Why the worker is skipping this step.",
        }
    },
    required=["reason"],
)

flag_anomaly_fn = FunctionSchema(
    name="flag_anomaly",
    description=(
        "Flag the current step as an anomaly when the worker reports something abnormal that "
        "isn't a numeric reading (e.g. visible leak, broken part, smoke)."
    ),
    properties={
        "description": {
            "type": "string",
            "description": "Short description of the anomaly.",
        }
    },
    required=["description"],
)


def build_system_prompt(state: ChecklistState) -> str:
    items_md = "\n".join(
        f"  Step {item['step']} [{item['id']}]: {item['title']} - {item['prompt']}"
        + (
            f" (in spec: {item['min']}-{item['max']} {item['unit']})"
            if item["type"] == "numeric"
            else " (yes/no)"
        )
        for item in CHECKLIST
    )
    return (
        "You are an Ambient Voice QA assistant guiding a manufacturing worker through a "
        "hydraulic pump quality inspection checklist. Your output is converted to audio, so "
        "speak clearly, briefly, and naturally - no emoji, no markdown, no lists. "
        "Always speak numbers as words the way an inspector would (e.g. 'three thousand fifty PSI').\n\n"
        "WORKFLOW:\n"
        "1. Greet the worker briefly and read the FIRST step's prompt verbatim.\n"
        "2. Wait for the worker's answer.\n"
        "3. As soon as you hear a numeric reading or a yes/no answer, IMMEDIATELY call "
        "the record_reading function with the value. Do not ask for confirmation first.\n"
        "4. After record_reading returns, briefly confirm whether it was in spec, and if it "
        "wasn't, tell the worker the value is out of spec and that you've flagged it.\n"
        "5. Then call next_step and read the next prompt. If the checklist is complete, give a "
        "one-sentence summary of total steps and any anomalies.\n\n"
        "VOICE COMMANDS the worker may say at any time:\n"
        "- 'repeat' / 'say that again' -> call repeat_step\n"
        "- 'go back' / 'previous' -> call go_back\n"
        "- 'skip' / 'skip this' -> call skip_step (ask why briefly)\n"
        "- describes a visible defect (leak, crack, smoke) -> call flag_anomaly\n\n"
        "CHECKLIST:\n"
        f"{items_md}\n\n"
        "Be concise. Do NOT read the spec ranges to the worker unless asked. "
        "Do NOT lecture. Stay focused on running the checklist."
    )


async def run_bot(transport: BaseTransport):
    logger.info("Starting Ambient QA bot")

    state = ChecklistState()

    # Speech-to-Text - Deepgram Nova-3 with keyterm boosting for QA vocabulary.
    stt = DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        settings=DeepgramSTTService.Settings(
            model="nova-3-general",
            keyterm=_all_keyterms(),
            smart_format=True,
            numerals=True,
            punctuate=True,
        ),
    )

    # Text-to-Speech - Deepgram Aura.
    tts = DeepgramTTSService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        voice=os.getenv("DEEPGRAM_VOICE_ID", "aura-2-helena-en"),
    )

    # LLM - Amazon Bedrock Claude.
    llm = AWSBedrockLLMService(
        aws_region=os.getenv("AWS_REGION", "us-west-2"),
        model=os.getenv("AWS_BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        params=AWSBedrockLLMService.InputParams(temperature=0.4),
    )

    # Will be set after the pipeline task is built so functions can push UI events.
    rtvi_holder: dict[str, Any] = {"task": None}

    async def push_ui_event(event_type: str, payload: dict[str, Any]):
        task = rtvi_holder["task"]
        if task is None:
            return
        try:
            await task.rtvi.send_server_message(
                {"type": event_type, "payload": payload}
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not send RTVI event: {e}")

    # ----- Function handlers ------------------------------------------------
    async def handle_record_reading(params: FunctionCallParams):
        item = state.current()
        if item is None:
            await params.result_callback(
                {"status": "error", "message": "Checklist already complete."}
            )
            return
        value = params.arguments.get("value")
        utterance = params.arguments.get("raw_utterance", str(value))
        in_spec, explanation = _validate_reading(item, value)
        record = state.results[state.current_index]
        record["value"] = value
        record["status"] = "pass" if in_spec else "anomaly"
        record["anomaly"] = not in_spec
        record["note"] = explanation
        record["utterance"] = utterance
        logger.info(
            f"[{item['id']}] reading={value} in_spec={in_spec} - {explanation}"
        )
        await push_ui_event("checklist_update", state.snapshot())
        await params.result_callback(
            {
                "step_id": item["id"],
                "title": item["title"],
                "value": value,
                "in_spec": in_spec,
                "explanation": explanation,
            }
        )

    async def handle_next_step(params: FunctionCallParams):
        if state.current_index < len(CHECKLIST):
            state.current_index += 1
        snapshot = state.snapshot()
        await push_ui_event("checklist_update", snapshot)
        nxt = state.current()
        await params.result_callback(
            {
                "complete": snapshot["complete"],
                "next_step": nxt["step"] if nxt else None,
                "next_prompt": nxt["prompt"] if nxt else None,
                "next_title": nxt["title"] if nxt else None,
            }
        )

    async def handle_repeat_step(params: FunctionCallParams):
        item = state.current()
        await params.result_callback(
            {
                "step_id": item["id"] if item else None,
                "prompt": item["prompt"] if item else "Checklist complete.",
            }
        )

    async def handle_go_back(params: FunctionCallParams):
        if state.current_index > 0:
            state.current_index -= 1
            state.results[state.current_index]["status"] = "pending"
            state.results[state.current_index]["anomaly"] = False
        snapshot = state.snapshot()
        await push_ui_event("checklist_update", snapshot)
        item = state.current()
        await params.result_callback(
            {
                "step_id": item["id"] if item else None,
                "prompt": item["prompt"] if item else None,
            }
        )

    async def handle_skip_step(params: FunctionCallParams):
        item = state.current()
        if item is None:
            await params.result_callback({"status": "error", "message": "No active step."})
            return
        reason = params.arguments.get("reason", "no reason given")
        record = state.results[state.current_index]
        record["status"] = "skipped"
        record["note"] = f"Skipped: {reason}"
        state.current_index += 1
        snapshot = state.snapshot()
        await push_ui_event("checklist_update", snapshot)
        nxt = state.current()
        await params.result_callback(
            {
                "skipped_step": item["id"],
                "reason": reason,
                "next_prompt": nxt["prompt"] if nxt else None,
            }
        )

    async def handle_flag_anomaly(params: FunctionCallParams):
        item = state.current()
        if item is None:
            await params.result_callback({"status": "error", "message": "No active step."})
            return
        desc = params.arguments.get("description", "anomaly reported")
        record = state.results[state.current_index]
        record["status"] = "anomaly"
        record["anomaly"] = True
        record["note"] = f"ANOMALY: {desc}"
        await push_ui_event("checklist_update", state.snapshot())
        await params.result_callback(
            {"step_id": item["id"], "flagged": True, "description": desc}
        )

    llm.register_function("record_reading", handle_record_reading, cancel_on_interruption=True)
    llm.register_function("next_step", handle_next_step, cancel_on_interruption=True)
    llm.register_function("repeat_step", handle_repeat_step, cancel_on_interruption=True)
    llm.register_function("go_back", handle_go_back, cancel_on_interruption=True)
    llm.register_function("skip_step", handle_skip_step, cancel_on_interruption=True)
    llm.register_function("flag_anomaly", handle_flag_anomaly, cancel_on_interruption=True)

    tools = ToolsSchema(
        standard_tools=[
            record_reading_fn,
            next_step_fn,
            repeat_step_fn,
            go_back_fn,
            skip_step_fn,
            flag_anomaly_fn,
        ]
    )

    messages = [{"role": "user", "content": build_system_prompt(state)}]
    context = LLMContext(messages=messages, tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )
    rtvi_holder["task"] = task

    @task.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        # Push the initial checklist so the UI renders immediately.
        await rtvi.send_server_message(
            {"type": "checklist_init", "payload": {"checklist": CHECKLIST}}
        )
        await rtvi.send_server_message(
            {"type": "checklist_update", "payload": state.snapshot()}
        )
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    try:
        from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter

        krisp_filter = KrispVivaFilter()
    except ImportError:
        logger.info("Krisp not available, running without noise cancellation")
        krisp_filter = None

    transport = None
    match runner_args:
        case DailyRunnerArguments():
            transport = DailyTransport(
                runner_args.room_url,
                runner_args.token,
                "Ambient QA Bot",
                params=DailyParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    await run_bot(transport)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
