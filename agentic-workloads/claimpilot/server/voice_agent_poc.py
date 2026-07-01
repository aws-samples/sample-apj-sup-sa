#
# Copyright (c) 2024-2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Deepgram-native Voice Agent relay for ClaimPilot.

The browser connects to this local WebSocket instead of connecting to Deepgram
directly. That keeps the Deepgram key server-side while letting the React demo
stream microphone PCM to Deepgram Voice Agent and receive assistant audio,
transcripts, function-call results, and UI actions.
"""

import asyncio
import json
import os
import time

from deepgram import AsyncDeepgramClient
from deepgram.agent.v1.types.agent_v1inject_user_message import AgentV1InjectUserMessage
from deepgram.agent.v1.types.agent_v1send_function_call_response import (
    AgentV1SendFunctionCallResponse,
)
from deepgram.agent.v1.types.agent_v1settings import AgentV1Settings
from dotenv import load_dotenv
from loguru import logger

import bot

load_dotenv(override=True)

VOICE_AGENT_RELAY_HOST = os.getenv("CLAIMPILOT_VOICE_AGENT_RELAY_HOST", "127.0.0.1")
VOICE_AGENT_RELAY_PORT = int(os.getenv("CLAIMPILOT_VOICE_AGENT_RELAY_PORT", "8790"))


class VoiceAgentFunctionParams:
    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result = None

    async def result_callback(self, result):
        self.result = result


FUNCTION_HANDLERS = {
    "send_secure_link": bot.send_secure_link,
    "sync_claim_intake": bot.sync_claim_intake,
    "update_claim_field": bot.update_claim_field,
    "update_claim_fields": bot.update_claim_fields,
    "highlight_missing": bot.highlight_missing,
    "update_timeline": bot.update_timeline,
    "update_claim_summary": bot.update_claim_summary,
    "update_supervisor_state": bot.update_supervisor_state,
    "open_claim_details": bot.open_claim_details,
    "request_evidence": bot.request_evidence,
    "analyze_evidence": bot.analyze_evidence,
    "create_claim": bot.create_claim,
    "append_aws_proof": bot.append_aws_proof,
    "estimate_fault_and_excess": bot.estimate_fault_and_excess,
}


def model_dump(message):
    if hasattr(message, "model_dump"):
        return message.model_dump()
    if hasattr(message, "dict"):
        return message.dict()
    return message


def get_event_detail(message: dict):
    payload = message.get("payload")
    if isinstance(payload, dict):
        return {**payload, **message}
    return message


class VoiceAgentRelaySession:
    def __init__(self, browser_ws):
        self.browser_ws = browser_ws
        self.browser_send_lock = asyncio.Lock()
        self.started_at = time.perf_counter()
        self.first_audio_at = None
        self.first_text_at = None
        self.agent = None

    async def send_json(self, payload: dict):
        async with self.browser_send_lock:
            await self.browser_ws.send(json.dumps(payload, default=str))

    async def send_ui_actions(self, actions: list[dict]):
        if not actions:
            return
        await self.send_json({"type": "ui_actions", "actions": actions})

    async def inject_app_event(self, content: str):
        if self.agent is None:
            return
        await self.agent.send_inject_user_message(
            AgentV1InjectUserMessage(
                type="InjectUserMessage",
                content=f"[ClaimPilot app event] {content}",
            )
        )

    async def execute_function_call(self, function_call):
        name = getattr(function_call, "name", "")
        call_id = getattr(function_call, "id", None)
        try:
            arguments = json.loads(getattr(function_call, "arguments", "{}") or "{}")
        except json.JSONDecodeError:
            arguments = {}

        await self.send_json(
            {
                "type": "ui_action",
                "action": "append_tool_event",
                "tool": name or "voice_agent_function",
                "status": "running",
                "detail": "Deepgram Voice Agent function call",
                "payloadPreview": json.dumps(arguments, default=str),
            }
        )

        handler = FUNCTION_HANDLERS.get(name)
        if handler is None:
            result = {"ok": False, "reason": f"Unsupported ClaimPilot function {name}"}
        else:
            params = VoiceAgentFunctionParams(arguments)
            await handler(params)
            result = params.result or {"ok": True}

        await self.send_ui_actions(result.get("uiActions", []))
        await self.send_json(
            {
                "type": "ui_action",
                "action": "append_tool_event",
                "tool": name or "voice_agent_function",
                "status": "complete" if result.get("ok", True) else "failed",
                "detail": result.get("reason") or "Deepgram Voice Agent function completed",
            }
        )
        return AgentV1SendFunctionCallResponse(
            type="FunctionCallResponse",
            id=call_id,
            name=name,
            content=json.dumps(result, default=str),
        )

    async def handle_app_event(self, message: dict):
        action = str(message.get("action") or "")
        detail = get_event_detail(message)

        if action == "secure_handoff_completed":
            await bot.mark_secure_handoff_completed(message)
            await self.send_json(
                {
                    "type": "ui_action",
                    "action": "append_tool_event",
                    "tool": "secure_handoff_completed",
                    "status": "complete",
                    "detail": "Verified Northstar app handoff received",
                }
            )
            await self.inject_app_event(
                "The customer tapped the secure Northstar link and the app confirms "
                "they are authenticated. Continue now with the safety check. Do not "
                "ask the customer to tell you when they are in."
            )
        elif action == "manual_evidence_upload_requested":
            params = VoiceAgentFunctionParams(
                {
                    "requestedEvidence": str(
                        detail.get("requestedEvidence") or "vehicle damage photo"
                    )
                }
            )
            await bot.request_evidence(params)
            result = params.result or {}
            await self.send_ui_actions(result.get("uiActions", []))
        elif action == "evidence_upload_completed":
            await bot.mark_evidence_uploaded(message)
            evidence_type = str(
                detail.get("requestedEvidence")
                or detail.get("evidenceType")
                or "uploaded vehicle damage photo"
            )
            analysis = await bot.analyze_evidence_core(
                evidence_type=evidence_type,
                evidence_id=str(detail.get("evidenceId") or ""),
                s3_key=str(detail.get("s3Key") or ""),
            )
            await self.send_ui_actions(analysis["uiActions"])
            await self.inject_app_event(
                "Evidence upload completed in the Northstar app and photo analysis "
                f"is back. Result: {analysis['finding']}. Severity: {analysis['severity']}. "
                "Tell the customer the result is attached, then continue to police "
                "report or review without asking them to upload again."
            )
        elif action == "evidence_upload_failed":
            await bot.mark_evidence_upload_failed(message)
            await self.inject_app_event(
                "The app reports the evidence upload failed. Ask the customer to try "
                "again or continue with local demo evidence."
            )
        else:
            await self.inject_app_event(
                f"The ClaimPilot app sent browser event {action}: {json.dumps(detail, default=str)}"
            )

    async def handle_browser_message(self, message):
        if isinstance(message, bytes):
            if self.agent is not None:
                await self.agent.send_media(message)
            return

        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            logger.debug(f"Ignoring non-JSON browser relay message: {message}")
            return

        message_type = str(payload.get("type") or "")
        if message_type == "app_event":
            await self.handle_app_event(payload)
        elif message_type == "inject_user_message":
            content = str(payload.get("content") or "")
            if content:
                await self.inject_app_event(content)
        elif message_type == "keep_alive":
            await self.send_json({"type": "status", "status": "connected"})
        elif message_type == "stop":
            await self.browser_ws.close()

    async def handle_agent_message(self, message):
        if isinstance(message, bytes):
            if self.first_audio_at is None:
                self.first_audio_at = time.perf_counter()
                await self.send_json(
                    {
                        "type": "latency",
                        "metric": "voice_agent_first_audio_ms",
                        "ms": round((self.first_audio_at - self.started_at) * 1000),
                    }
                )
            async with self.browser_send_lock:
                await self.browser_ws.send(message)
            return

        message_data = model_dump(message)
        message_type = message_data.get("type") if isinstance(message_data, dict) else None

        if message_type == "ConversationText":
            role = str(message_data.get("role") or "")
            content = str(message_data.get("content") or "")
            if role == "assistant" and self.first_text_at is None:
                self.first_text_at = time.perf_counter()
                await self.send_json(
                    {
                        "type": "latency",
                        "metric": "voice_agent_first_text_ms",
                        "ms": round((self.first_text_at - self.started_at) * 1000),
                    }
                )
            await self.send_json(
                {
                    "type": "transcript",
                    "speaker": "Assistant" if role == "assistant" else "Customer",
                    "text": content,
                }
            )
        elif message_type == "FunctionCallRequest":
            responses = []
            for function_call in getattr(message, "functions", []):
                responses.append(await self.execute_function_call(function_call))
            for response in responses:
                await self.agent.send_function_call_response(response)
        elif message_type == "AgentStartedSpeaking":
            await self.send_json(
                {
                    "type": "latency",
                    "metric": "voice_agent_total_latency_ms",
                    "ms": round(float(message_data.get("total_latency") or 0) * 1000),
                    "tttMs": round(float(message_data.get("ttt_latency") or 0) * 1000),
                    "ttsMs": round(float(message_data.get("tts_latency") or 0) * 1000),
                }
            )
        elif message_type == "AgentAudioDone":
            await self.send_json({"type": "status", "status": "agent_audio_done"})
        elif message_type in {"Error", "Warning"}:
            await self.send_json(
                {
                    "type": "status",
                    "status": "error" if message_type == "Error" else "warning",
                    "detail": message_data,
                }
            )
        else:
            await self.send_json({"type": "voice_agent_event", "event": message_data})

    async def browser_to_agent(self):
        async for message in self.browser_ws:
            await self.handle_browser_message(message)

    async def agent_to_browser(self):
        async for message in self.agent:
            await self.handle_agent_message(message)

    async def run(self):
        settings = bot.build_deepgram_voice_agent_settings()
        await self.send_json(
            {
                "type": "status",
                "status": "connecting",
                "detail": "Connecting to Deepgram Voice Agent",
            }
        )

        client = AsyncDeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))
        async with client.agent.v1.connect() as agent:
            self.agent = agent
            await agent.send_settings(AgentV1Settings(**settings))
            await self.send_json(
                {
                    "type": "status",
                    "status": "connected",
                    "detail": "Deepgram Voice Agent connected",
                }
            )

            test_utterance = os.getenv("CLAIMPILOT_VOICE_AGENT_TEST_UTTERANCE", "").strip()
            if test_utterance:
                await agent.send_inject_user_message(
                    AgentV1InjectUserMessage(
                        type="InjectUserMessage",
                        content=test_utterance,
                    )
                )

            browser_task = asyncio.create_task(self.browser_to_agent())
            agent_task = asyncio.create_task(self.agent_to_browser())
            done, pending = await asyncio.wait(
                {browser_task, agent_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()


async def relay_handler(websocket, _path=None):
    session = VoiceAgentRelaySession(websocket)
    try:
        await session.run()
    except Exception as exc:
        logger.exception(f"Voice Agent relay session failed: {exc}")
        try:
            await session.send_json({"type": "status", "status": "error", "detail": str(exc)})
        except Exception:
            pass
    finally:
        logger.info("Voice Agent relay session closed")


async def run_voice_agent_relay():
    if bot.websockets is None:
        raise RuntimeError("websockets package unavailable; cannot start Voice Agent relay")

    server = await bot.websockets.serve(
        relay_handler,
        VOICE_AGENT_RELAY_HOST,
        VOICE_AGENT_RELAY_PORT,
        max_size=None,
    )
    logger.info(
        "ClaimPilot Voice Agent relay listening on "
        f"ws://{VOICE_AGENT_RELAY_HOST}:{VOICE_AGENT_RELAY_PORT}"
    )
    await asyncio.Future()
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(run_voice_agent_relay())
