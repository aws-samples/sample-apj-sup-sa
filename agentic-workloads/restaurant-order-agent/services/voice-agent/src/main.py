"""
Voice Agent using Pipecat 1.3 framework with Deepgram STT/TTS and Bedrock Claude Sonnet.
Deployed on AWS Bedrock AgentCore Runtime with Memory integration.

Architecture:
  Client Audio → WebSocket → Deepgram STT (Nova-2) → Claude Sonnet (Bedrock) → Deepgram TTS (Aura) → WebSocket → Client

Memory Architecture:
  - STM (Short-Term Memory): Stores current conversation events
  - LTM (Long-Term Memory): Stores user preferences, facts, and session summaries
  - Prefetch Agent: Asynchronously retrieves relevant LTM into STM when a new prompt arrives
"""

import os
import asyncio
import logging
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.frames.frames import TextFrame, EndFrame
from pipecat.serializers.protobuf import ProtobufFrameSerializer

from anthropic import AsyncAnthropicBedrock

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from src.restaurant_tools import RESTAURANT_TOOLS, RestaurantToolClient

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# AgentCore Memory constants
MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

# Bedrock model ID (cross-region inference profile). Override via env if needed.
LLM_MODEL_ID = os.environ.get("LLM_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

if not MEMORY_ID:
    logger.warning("AGENTCORE_MEMORY_ID not set — memory features will be disabled")

# Initialize the AgentCore app
app = BedrockAgentCoreApp()

# System prompt for the voice agent
SYSTEM_PROMPT = """You are a friendly restaurant voice assistant for a food delivery service.
You help customers browse the menu, add items to their cart, place orders, and track deliveries.

Key behaviors:
- Keep responses concise and conversational (they'll be spoken aloud)
- When a customer wants to order, first check if they're authenticated. If not, ask for their phone number and guide them through OTP verification.
- When describing menu items, mention the name, price, and dietary flag naturally
- After adding items to cart, confirm what was added and offer to add more or place the order
- When placing an order, confirm the total and delivery address
- Be proactive: if a customer asks about food, show the menu. If they seem ready, help them order.

Session state:
- Once a customer verifies their OTP, you have their session. You don't need to re-authenticate for the rest of the conversation.
- The session token is managed automatically - just call the tools.

Available tools: list_menu, get_menu_item, add_cart_item, get_cart, place_order,
get_current_order, get_order, get_delivery_status, get_profile, update_profile,
request_otp, verify_otp"""


class MemoryPrefetchAgent:
    """
    Async agent that prefetches relevant Long-Term Memory (LTM) into
    Short-Term Memory (STM) based on the current user prompt.

    When the user speaks, this agent:
    1. Receives the transcribed text
    2. Queries LTM namespaces (preferences, facts, summaries) for relevant context
    3. Injects retrieved memories into the current session's STM
    4. The main LLM then has full context for generating a response
    """

    def __init__(self, memory_id: str, session_id: str, actor_id: str, region: str):
        self.memory_id = memory_id
        self.session_id = session_id
        self.actor_id = actor_id
        self.region = region
        self._client = None
        self._prefetch_task: Optional[asyncio.Task] = None
        self._cached_context: list = []

    def _get_client(self):
        """Lazily initialize the memory client."""
        if self._client is None:
            try:
                from bedrock_agentcore.memory import MemoryClient
                self._client = MemoryClient(region_name=self.region)
            except ImportError:
                import boto3
                self._client = boto3.client(
                    "bedrock-agentcore",
                    region_name=self.region,
                )
        return self._client

    async def prefetch_for_prompt(self, prompt: str) -> list:
        """
        Asynchronously query LTM for context relevant to the user's prompt.
        Returns a list of memory entries that can augment the LLM context.
        """
        memories = []
        try:
            memories = await asyncio.get_event_loop().run_in_executor(
                None, self._retrieve_ltm, prompt
            )
            self._cached_context = memories
            logger.info(f"Prefetched {len(memories)} memory entries for prompt")
        except Exception as e:
            logger.warning(f"Memory prefetch failed (non-blocking): {e}")
        return memories

    def _retrieve_ltm(self, prompt: str) -> list:
        """Retrieve relevant long-term memories from all LTM namespaces."""
        memories = []
        client = self._get_client()

        namespaces = [
            f"/preferences/{self.actor_id}",
            f"/facts/{self.actor_id}",
            f"/summaries/{self.actor_id}",
        ]

        for namespace in namespaces:
            try:
                if hasattr(client, "retrieve_memories"):
                    response = client.retrieve_memories(
                        memory_id=self.memory_id,
                        namespace=namespace,
                        query=prompt,
                        top_k=5,
                    )
                    if response and "memories" in response:
                        for mem in response["memories"]:
                            memories.append({
                                "namespace": namespace,
                                "content": mem.get("content", ""),
                                "relevance": mem.get("score", 0.0),
                            })
                else:
                    response = client.retrieve_memory(
                        memoryId=self.memory_id,
                        namespace=namespace,
                        query=prompt,
                        maxResults=5,
                    )
                    if response and "results" in response:
                        for mem in response["results"]:
                            memories.append({
                                "namespace": namespace,
                                "content": mem.get("content", ""),
                                "relevance": mem.get("score", 0.0),
                            })
            except Exception as e:
                logger.debug(f"No memories found in {namespace}: {e}")

        memories.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        return memories

    def start_prefetch(self, prompt: str):
        """Kick off an async prefetch task. Non-blocking."""
        self._prefetch_task = asyncio.create_task(self.prefetch_for_prompt(prompt))

    async def get_prefetch_results(self, timeout: float = 2.0) -> list:
        """Wait for prefetch results with a timeout."""
        if self._prefetch_task is None:
            return self._cached_context
        try:
            results = await asyncio.wait_for(self._prefetch_task, timeout=timeout)
            return results
        except asyncio.TimeoutError:
            logger.info("Memory prefetch timed out, proceeding without LTM context")
            return self._cached_context
        except Exception as e:
            logger.warning(f"Memory prefetch error: {e}")
            return []

    def format_memory_context(self, memories: list) -> str:
        """Format retrieved memories into a context string for the LLM."""
        if not memories:
            return ""
        context_parts = []
        for mem in memories[:10]:
            namespace = mem.get("namespace", "")
            content = mem.get("content", "")
            if "/preferences/" in namespace:
                context_parts.append(f"[User Preference] {content}")
            elif "/facts/" in namespace:
                context_parts.append(f"[Known Fact] {content}")
            elif "/summaries/" in namespace:
                context_parts.append(f"[Past Conversation] {content}")
        if context_parts:
            return (
                "\n\n--- Retrieved from Long-Term Memory ---\n"
                + "\n".join(context_parts)
                + "\n--- End Memory Context ---\n"
            )
        return ""


class MemorySessionManager:
    """Manages STM events for the current conversation session."""

    def __init__(self, memory_id: str, session_id: str, actor_id: str, region: str):
        self.memory_id = memory_id
        self.session_id = session_id
        self.actor_id = actor_id
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from bedrock_agentcore.memory import MemoryClient
                self._client = MemoryClient(region_name=self.region)
            except ImportError:
                import boto3
                self._client = boto3.client(
                    "bedrock-agentcore",
                    region_name=self.region,
                )
        return self._client

    async def record_event(self, role: str, content: str):
        """Record a conversation event to STM."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self._write_event, role, content
            )
        except Exception as e:
            logger.warning(f"Failed to record memory event: {e}")

    def _write_event(self, role: str, content: str):
        """Write an event to short-term memory."""
        client = self._get_client()
        event = {
            "actor_id": self.actor_id,
            "session_id": self.session_id,
            "event_timestamp": datetime.utcnow().isoformat(),
            "messages": [{"role": role, "content": content}],
        }
        try:
            if hasattr(client, "add_memory_event"):
                client.add_memory_event(memory_id=self.memory_id, event=event)
            else:
                client.create_memory_event(memoryId=self.memory_id, event=event)
            logger.debug(f"Recorded {role} event to STM")
        except Exception as e:
            logger.warning(f"STM write failed: {e}")

    async def load_session_history(self) -> list:
        """Load existing STM events for the current session."""
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._read_session
            )
        except Exception as e:
            logger.warning(f"Failed to load session history: {e}")
            return []

    def _read_session(self) -> list:
        """Read session events from STM."""
        client = self._get_client()
        messages = []
        try:
            if hasattr(client, "get_memory_events"):
                response = client.get_memory_events(
                    memory_id=self.memory_id,
                    session_id=self.session_id,
                    actor_id=self.actor_id,
                )
                if response and "events" in response:
                    for event in response["events"]:
                        for msg in event.get("messages", []):
                            messages.append(msg)
            else:
                response = client.list_memory_events(
                    memoryId=self.memory_id,
                    sessionId=self.session_id,
                    actorId=self.actor_id,
                )
                if response and "events" in response:
                    for event in response["events"]:
                        for msg in event.get("messages", []):
                            messages.append(msg)
        except Exception as e:
            logger.debug(f"No existing session history: {e}")
        return messages


@app.websocket
async def handle_websocket(websocket, context):
    """
    WebSocket handler for real-time bidirectional audio streaming with memory.

    Pipeline: Audio In → STT → Context Aggregator → LLM → TTS → Audio Out
    """
    await websocket.accept()

    deepgram_api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not deepgram_api_key:
        logger.error("DEEPGRAM_API_KEY not set")
        await websocket.close()
        return

    # Session management
    session_id = f"session_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    actor_id = "default_user"

    # Initialize memory components
    memory_session = MemorySessionManager(
        memory_id=MEMORY_ID,
        session_id=session_id,
        actor_id=actor_id,
        region=AWS_REGION,
    )
    prefetch_agent = MemoryPrefetchAgent(
        memory_id=MEMORY_ID,
        session_id=session_id,
        actor_id=actor_id,
        region=AWS_REGION,
    )

    # Load any existing session history
    session_history = await memory_session.load_session_history()

    # Build system prompt with session context
    system_prompt = SYSTEM_PROMPT
    if session_history:
        history_text = "\n".join(
            [f"{m['role']}: {m['content']}" for m in session_history[-10:]]
        )
        system_prompt += f"\n\nPrevious conversation in this session:\n{history_text}"

    # Configure WebSocket transport with VAD
    # Use explicit sample rate to avoid mismatch between STT input and TTS output
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(
                params=SileroVADAnalyzer.VADParams(
                    threshold=0.5,          # Default is 0.5, be explicit
                    min_speech_duration_ms=250,  # Ignore very short bursts
                    min_silence_duration_ms=600, # Wait 600ms of silence before cutting
                )
            ),
            add_wav_header=False,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    # Deepgram STT - Nova-2 for low-latency transcription
    stt = DeepgramSTTService(
        api_key=deepgram_api_key,
        params=DeepgramSTTService.InputParams(
            model="nova-2",
            language="en",
        ),
    )

    # Deepgram TTS - Aura-2 Andromeda for warm, natural voice
    tts = DeepgramTTSService(
        api_key=deepgram_api_key,
        voice="aura-2-andromeda-en",
        sample_rate=24000,
    )

    # Claude Sonnet via Amazon Bedrock (no OpenAI dependency)
    bedrock_client = AsyncAnthropicBedrock(aws_region=AWS_REGION)
    llm = AnthropicLLMService(
        api_key="bedrock",  # not used when client is provided
        model=LLM_MODEL_ID,
        client=bedrock_client,
    )

    # Set up conversation context with restaurant tools
    messages = [{"role": "system", "content": system_prompt}]
    for msg in session_history[-10:]:
        messages.append(msg)

    llm_context = LLMContext(messages=messages, tools=RESTAURANT_TOOLS)
    context_aggregator = LLMContextAggregatorPair(llm_context)

    # Initialize restaurant tool client for this session
    tool_client = RestaurantToolClient()

    # Register tool handlers with the LLM service
    @llm.function("request_otp")
    async def on_request_otp(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("request_otp", args)
        await result_callback(result)

    @llm.function("verify_otp")
    async def on_verify_otp(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("verify_otp", args)
        await result_callback(result)

    @llm.function("list_menu")
    async def on_list_menu(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("list_menu", args)
        await result_callback(result)

    @llm.function("get_menu_item")
    async def on_get_menu_item(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("get_menu_item", args)
        await result_callback(result)

    @llm.function("get_cart")
    async def on_get_cart(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("get_cart", args)
        await result_callback(result)

    @llm.function("add_cart_item")
    async def on_add_cart_item(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("add_cart_item", args)
        await result_callback(result)

    @llm.function("place_order")
    async def on_place_order(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("place_order", args)
        await result_callback(result)

    @llm.function("get_current_order")
    async def on_get_current_order(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("get_current_order", args)
        await result_callback(result)

    @llm.function("get_order")
    async def on_get_order(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("get_order", args)
        await result_callback(result)

    @llm.function("get_delivery_status")
    async def on_get_delivery_status(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("get_delivery_status", args)
        await result_callback(result)

    @llm.function("get_profile")
    async def on_get_profile(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("get_profile", args)
        await result_callback(result)

    @llm.function("update_profile")
    async def on_update_profile(function_name, tool_call_id, args, llm, context, result_callback):
        result = await tool_client.call_tool("update_profile", args)
        await result_callback(result)

    # Build the voice pipeline
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
        ),
    )

    # Initial greeting
    await task.queue_frames(
        [TextFrame("Hello! I'm your restaurant assistant. I can help you browse the menu, place orders, or check on your delivery. What would you like to do?")]
    )

    runner = PipelineRunner()
    await runner.run(task)


@app.entrypoint
async def handle_invocation(payload: dict) -> dict:
    """
    HTTP POST entrypoint with Claude tool-calling.
    Sends the user prompt to Claude Sonnet with restaurant tools,
    executes any tool calls, and returns the final response.
    """
    prompt = payload.get("prompt", "")
    session_token = payload.get("session_token", None)

    # Initialize tool client
    tool_client = RestaurantToolClient()
    if session_token:
        tool_client.session_token = session_token

    # Call Claude with tools via the Anthropic Bedrock client
    from anthropic import AnthropicBedrock

    bedrock = AnthropicBedrock(aws_region=AWS_REGION)

    messages = [{"role": "user", "content": prompt}]
    tools_for_api = [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in RESTAURANT_TOOLS
    ]

    # Agentic loop: keep calling Claude until it stops using tools
    max_turns = 5
    for _ in range(max_turns):
        response = bedrock.messages.create(
            model=LLM_MODEL_ID,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tools_for_api,
        )

        # Check if Claude wants to use a tool
        if response.stop_reason == "tool_use":
            # Extract tool use blocks
            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute each tool call
            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    result = await tool_client.call_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            # Claude finished — extract text response
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text

            return {
                "response": final_text,
                "session_token": tool_client.session_token,
                "status": "success",
            }

    return {"response": "Max tool turns reached", "status": "error"}
