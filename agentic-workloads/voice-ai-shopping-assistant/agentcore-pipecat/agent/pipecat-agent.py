#
# Aisle — voice AI grocery shopping assistant (Amazon Bedrock AgentCore Runtime).
#
# Cascaded pipeline: Deepgram STT (multilingual) -> Bedrock Claude -> Deepgram Aura TTS.
# Tools (search_products, variants, cart, order) call the live AgentCore Gateway
# (MCP), which fronts the Lambda tools backed by the Aisle catalogue in Aurora.
#
# The bot is invoked per session by AgentCore Runtime. The invocation payload
# carries the Daily room URL to join; media flows through Daily, not AgentCore.
#

import asyncio
import datetime
import os
import uuid
from pathlib import Path

import aiohttp
from bedrock_agentcore import BedrockAgentCoreApp
from daily_agentcore_prep import (
    prepare_daily_transport_for_agentcore,
    prepare_tavus_transport_for_agentcore,
)
from dotenv import load_dotenv
from gateway_client import GatewayMCPClient
from loguru import logger
from memory import AisleMemory
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    OutputTransportMessageUrgentFrame,
    TextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.services.aws.llm import AWSBedrockLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.transports.tavus.transport import TavusParams, TavusTransport
from pipecat.workers.runner import WorkerRunner

app = BedrockAgentCoreApp()

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Shared prompts (bundled into the container build context under ./prompts)
# ---------------------------------------------------------------------------
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "aisle-system.txt").read_text()

CUSTOM_GREETING = (
    "Hey there, I'm Aisle, your grocery sidekick. "
    "Are you planning meals at home, or shopping in store right now — "
    "and what are you after?"
)

# If no human joins within this many seconds, the bot leaves the room so it
# stops consuming Daily participant-minutes (and AgentCore compute).
NO_JOIN_TIMEOUT_SECS = int(os.getenv("NO_JOIN_TIMEOUT_SECS", "120"))

# Persistent per-user id so the grocery list / personalisation survives across
# sessions ("remembers me"). A single known demo user; set DEMO_USER_ID to use
# a per-user id in production.
DEMO_USER_ID = os.getenv("DEMO_USER_ID", "demo-user")


def _dollars(cents: int) -> str:
    return f"${cents / 100:.2f}"


# Tool definitions in FunctionSchema format for Pipecat. Backed by the live
# AgentCore Gateway (the Aisle catalogue + cart/order in Aurora).
TOOLS = ToolsSchema(
    standard_tools=[
        FunctionSchema(
            name="search_products",
            description="Search the Aisle catalogue by name or category. Returns matching products with brand, price, size, allergens, dietary tags, and any current special. Use when the shopper wants to find or buy an item or asks what's available.",
            properties={
                "query": {"type": "string", "description": "Product name or keyword, e.g. 'milk', 'gluten free pasta'."},
                "category": {"type": "string", "description": "Optional category filter, e.g. 'dairy', 'bakery'."},
                "limit": {"type": "integer", "description": "Max results (1-50, default 10)."},
            },
            required=["query"],
        ),
        FunctionSchema(
            name="get_product_variants",
            description="Compare the brand/variant options for a staple (brands, allergens, price, specials, quality). Use when the shopper asks which one to get or about differences between brands.",
            properties={
                "product_name": {"type": "string", "description": "The staple to compare, e.g. 'spaghetti' or 'milk'."},
            },
            required=["product_name"],
        ),
        FunctionSchema(
            name="add_to_cart",
            description="Add a product to the shopper's cart. Accepts the product name (e.g. 'the Sunny Meadow spaghetti') or a product_id from a recent search.",
            properties={
                "product": {"type": "string", "description": "Product name or product_id to add."},
                "qty": {"type": "integer", "description": "Quantity (default 1)."},
            },
            required=["product"],
        ),
        FunctionSchema(
            name="get_cart",
            description="Read back the shopper's current cart and subtotal.",
            properties={},
            required=[],
        ),
        FunctionSchema(
            name="remove_from_cart",
            description="Remove a product from the cart, or reduce its quantity. Accepts the product name (e.g. 'the milk') or a product_id. Use when the shopper wants to remove, delete, or take an item out of their cart.",
            properties={
                "product": {"type": "string", "description": "Product name or product_id to remove."},
                "qty": {"type": "integer", "description": "Optional amount to remove; omit to remove the item entirely."},
            },
            required=["product"],
        ),
        FunctionSchema(
            name="create_order",
            description="Place a pickup order for everything in the cart and check out. Confirm with the shopper before calling.",
            properties={
                "pickup_time": {"type": "string", "description": "Optional preferred pickup time."},
            },
            required=[],
        ),
        FunctionSchema(
            name="get_offers",
            description="Browse what's currently on special in the Aisle catalogue, biggest savings first, optionally within a category. Use when the shopper asks what's on special or what the deals are.",
            properties={
                "category": {"type": "string", "description": "Optional category filter, e.g. 'dairy', 'drinks'."},
                "limit": {"type": "integer", "description": "Max offers (1-50, default 10)."},
            },
            required=[],
        ),
        FunctionSchema(
            name="get_grocery_list",
            description="Read back the shopper's persistent grocery list (the things they still need). The list survives across sessions, unlike the cart. Use when they ask what's on their list.",
            properties={},
            required=[],
        ),
        FunctionSchema(
            name="update_grocery_list",
            description="Update the shopper's persistent grocery list. Add new items by name, mark items as already 'have', or remove them. Use when the shopper says they need something, already have something, or want it off the list.",
            properties={
                "add": {"type": "array", "items": {"type": "string"}, "description": "Item names to add, e.g. ['bread', 'oat milk']."},
                "mark_have": {"type": "array", "items": {"type": "string"}, "description": "Item names the shopper already has (marked done)."},
                "remove": {"type": "array", "items": {"type": "string"}, "description": "Item names to remove from the list."},
            },
            required=[],
        ),
        FunctionSchema(
            name="check_relevant_changes",
            description="Check what changed that's relevant to this shopper: items on their grocery list that are now on special (with savings) or out of stock. Use on connect or when they ask if anything on their list is cheaper.",
            properties={},
            required=[],
        ),
    ]
)


class UserTranscriptForwarder(FrameProcessor):
    """Forwards user STT transcriptions to the frontend via data channel."""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            await self.push_frame(OutputTransportMessageUrgentFrame(
                message={"type": "transcript", "role": "user", "text": frame.text.strip(), "final": True}
            ), FrameDirection.DOWNSTREAM)
        elif isinstance(frame, InterimTranscriptionFrame) and frame.text.strip():
            await self.push_frame(OutputTransportMessageUrgentFrame(
                message={"type": "transcript", "role": "user", "text": frame.text.strip(), "final": False}
            ), FrameDirection.DOWNSTREAM)

        await self.push_frame(frame, direction)


class AgentTranscriptForwarder(FrameProcessor):
    """Forwards agent LLM text to the frontend via data channel (cascaded: accumulate deltas)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._buffer = ""
        self._sent_speaking = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = ""
            self._sent_speaking = False
        elif isinstance(frame, TTSStartedFrame):
            if not self._sent_speaking and not self._buffer.strip():
                await self.push_frame(OutputTransportMessageUrgentFrame(
                    message={"type": "transcript", "role": "agent", "text": "", "final": False}
                ), FrameDirection.DOWNSTREAM)
                self._sent_speaking = True
        elif isinstance(frame, TextFrame):
            self._buffer += frame.text
            text = self._buffer.strip()
            if text:
                await self.push_frame(OutputTransportMessageUrgentFrame(
                    message={"type": "transcript", "role": "agent", "text": text, "final": False}
                ), FrameDirection.DOWNSTREAM)
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._buffer.strip():
                await self.push_frame(OutputTransportMessageUrgentFrame(
                    message={"type": "transcript", "role": "agent", "text": self._buffer.strip(), "final": True}
                ), FrameDirection.DOWNSTREAM)
            self._buffer = ""
            self._sent_speaking = False

        await self.push_frame(frame, direction)


def _avatar_enabled() -> bool:
    """Avatar is on only when both Tavus API key and replica id are configured."""
    return bool(os.getenv("TAVUS_API_KEY") and os.getenv("TAVUS_REPLICA_ID"))


def _build_daily_params() -> DailyParams:
    # Audio-only DailyTransport (no avatar). The bot publishes its TTS audio
    # straight into the room — reliable, no Tavus republish gating.
    return DailyParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    )


def _build_tavus_params() -> TavusParams:
    # TavusTransport: bot + avatar + user share one room. The bot sends TTS
    # audio to the avatar (persona "pipecat-stream") and the avatar serves the
    # synchronized video/audio natively — no republish, no video_out on our side.
    return TavusParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    )


async def run_bot(
    transport,
    user_id: str | None = None,
    session_id: str | None = None,
):
    logger.info("Starting cascaded bot")
    yield {"status": "initializing bot"}

    # Identity for AgentCore Memory. user_id (the long-term actor) is supplied by
    # the invocation payload / DEMO_USER_ID; session_id groups short-term events.
    # A stable session_id (passed in) makes reconnect-rehydration work; otherwise
    # a fresh uuid means short-term replay is a harmless no-op (long-term still works).
    user_id = user_id or os.getenv("DEMO_USER_ID") or "demo-user"
    session_id = session_id or str(uuid.uuid4())
    logger.info(f"Memory identity: user_id={user_id} session_id={session_id}")

    async with aiohttp.ClientSession() as session:
        avatar_on = _avatar_enabled()
        # Avatar (when on) is handled by TavusTransport itself — the bot, the
        # Tavus replica, and the user all share one room and the avatar serves
        # synchronized video/audio natively. No TavusVideoService republish.
        logger.info("Tavus avatar enabled (TavusTransport)" if avatar_on
                    else "Tavus avatar disabled — running audio-only")

        user_transcript = UserTranscriptForwarder()
        agent_transcript = AgentTranscriptForwarder()

        # --- AgentCore Memory: fetch long-term prefs + recent turns BEFORE building
        # the context, so the very first greeting can already be personalised and no
        # latency is added on the join path. No-op (empty) when MEMORY_ID is unset.
        mem = AisleMemory()
        pref_snippet = await mem.get_preferences_prompt(
            user_id,
            query="dietary needs, allergies, preferred brands, usual items, budget, shopping style",
        )
        prior_messages = await mem.get_recent_turns_messages(user_id, session_id)
        last_save_task: dict = {"t": None}  # latest in-flight save (drained on disconnect)

        # The Aisle system prompt covers persona, modes, tool usage, and multilingual.
        # Append the advisory preference snippet (may be "").
        system_prompt = SYSTEM_PROMPT + pref_snippet

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(prior_messages)  # rehydrate prior turns (empty for a new session)
        context = LLMContext(messages, tools=TOOLS)
        context_aggregator = LLMContextAggregatorPair(context)

        # Live AgentCore Gateway (the Aisle catalogue + cart/order in Aurora).
        gateway = GatewayMCPClient()
        last_products = []  # most recent search/variant results, for name -> id resolution
        participant_joined = {"v": False}  # tracked by the no-join watchdog

        stt = DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
            language="multi",
        )
        tts = DeepgramTTSService(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
            voice=os.getenv("DEEPGRAM_TTS_VOICE", "aura-2-thalia-en"),
        )
        # Credentials come from the AgentCore execution role (default chain).
        llm = AWSBedrockLLMService(
            model="au.anthropic.claude-haiku-4-5-20251001-v1:0",
            aws_region=os.getenv("AWS_REGION", "ap-southeast-2"),
        )

        # Audio-only pipeline. With the avatar on, `transport` is a TavusTransport
        # whose output() forwards TTS audio to the avatar for synchronized video.
        processors = [
            transport.input(),
            stt,
            user_transcript,
            context_aggregator.user(),
            llm,
            agent_transcript,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
        pipeline = Pipeline(processors)

        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=16000,
                audio_out_sample_rate=24000,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        # --- Tool handlers: call the live AgentCore Gateway + emit tool_result events ---
        async def emit(tool: str, data_obj: dict):
            await worker.queue_frames([OutputTransportMessageUrgentFrame(
                message={"v": 1, "type": "tool_result", "tool": tool, "data": data_obj}
            )])

        def display_price(p: dict) -> int:
            sp = (p.get("special") or {}).get("special_price_cents")
            return sp if sp else p.get("price_cents", 0)

        def resolve_product_id(ref: str):
            ref_l = ref.lower().strip()
            for p in last_products:
                if p.get("product_id") == ref:
                    return ref
            for p in last_products:
                if ref_l and ref_l in p.get("name", "").lower():
                    return p.get("product_id")
            return None

        async def handle_search_products(params):
            query = params.arguments.get("query", "")
            args = {"query": query}
            if params.arguments.get("category"):
                args["category"] = params.arguments["category"]
            if params.arguments.get("limit"):
                args["limit"] = params.arguments["limit"]
            try:
                data_obj = await gateway.call_tool("search_products", args)
            except Exception as e:
                logger.error(f"search_products failed: {e}")
                await params.result_callback("Sorry, I had trouble reaching the store just now.")
                return
            products = data_obj.get("products", []) if isinstance(data_obj, dict) else []
            last_products[:] = products
            await emit("search_products", {"products": products})
            if products:
                top = products[0]
                spec = " and it's on special" if top.get("special") else ""
                more = f", plus {len(products) - 1} more" if len(products) > 1 else ""
                await params.result_callback(
                    f"Top match is {top['name']} at {_dollars(display_price(top))}{spec}{more}. It's on screen."
                )
            else:
                await params.result_callback(f"I couldn't find anything for {query}.")

        async def handle_get_product_variants(params):
            name = params.arguments.get("product_name", "")
            try:
                data_obj = await gateway.call_tool("search_products", {"query": name, "limit": 8})
            except Exception as e:
                logger.error(f"get_product_variants failed: {e}")
                await params.result_callback("Sorry, I couldn't pull up the options just now.")
                return
            variants = data_obj.get("products", []) if isinstance(data_obj, dict) else []
            last_products[:] = variants
            await emit("get_product_variants", {"variants": variants})
            if variants:
                cheapest = min(variants, key=display_price)
                await params.result_callback(
                    f"There are {len(variants)} options for {name}. Cheapest is {cheapest['name']} "
                    f"at {_dollars(display_price(cheapest))}. Full comparison's on screen."
                )
            else:
                await params.result_callback(f"I couldn't find variants for {name}.")

        async def handle_add_to_cart(params):
            ref = params.arguments.get("product", "")
            qty = int(params.arguments.get("qty", 1) or 1)
            pid = resolve_product_id(ref)
            if not pid:
                try:
                    d = await gateway.call_tool("search_products", {"query": ref, "limit": 1})
                    ps = d.get("products", []) if isinstance(d, dict) else []
                    if ps:
                        pid = ps[0]["product_id"]
                        last_products[:] = ps
                except Exception:
                    pass
            if not pid:
                await params.result_callback(f"I couldn't find {ref} to add.")
                return
            try:
                data_obj = await gateway.call_tool(
                    "add_to_cart", {"session_id": session_id, "product_id": pid, "qty": qty}
                )
            except Exception as e:
                logger.error(f"add_to_cart failed: {e}")
                await params.result_callback("Sorry, I couldn't add that to your cart just now.")
                return
            cart = data_obj.get("cart", data_obj) if isinstance(data_obj, dict) else {}
            await emit("add_to_cart", {"cart": cart})
            n = len(cart.get("items", []))
            await params.result_callback(f"Added. You've got {n} item{'s' if n != 1 else ''} in your cart now. Anything else?")

        async def handle_get_cart(params):
            try:
                data_obj = await gateway.call_tool("get_cart", {"session_id": session_id})
            except Exception as e:
                logger.error(f"get_cart failed: {e}")
                await params.result_callback("Sorry, I couldn't read your cart just now.")
                return
            cart = data_obj.get("cart", data_obj) if isinstance(data_obj, dict) else {}
            await emit("get_cart", {"cart": cart})
            items = cart.get("items", [])
            if items:
                await params.result_callback(
                    f"You have {len(items)} item{'s' if len(items) != 1 else ''}, "
                    f"subtotal {_dollars(cart.get('subtotal_cents', 0))}."
                )
            else:
                await params.result_callback("Your cart is empty so far.")

        async def handle_remove_from_cart(params):
            ref = params.arguments.get("product", "")
            ref_l = ref.lower().strip()
            raw_qty = params.arguments.get("qty")

            # Prefer resolving against the live cart (removal targets cart contents).
            pid = None
            try:
                cur = await gateway.call_tool("get_cart", {"session_id": session_id})
                cur_items = (cur.get("cart", {}) if isinstance(cur, dict) else {}).get("items", [])
                for it in cur_items:
                    if it.get("product_id") == ref or (ref_l and ref_l in it.get("name", "").lower()):
                        pid = it.get("product_id")
                        break
            except Exception:
                pass
            if not pid:
                pid = resolve_product_id(ref)
            if not pid:
                await params.result_callback(f"I couldn't find {ref} in your cart to remove.")
                return

            args = {"session_id": session_id, "product_id": pid}
            if isinstance(raw_qty, int) and raw_qty > 0:
                args["qty"] = raw_qty
            try:
                data_obj = await gateway.call_tool("remove_from_cart", args)
            except Exception as e:
                logger.error(f"remove_from_cart failed: {e}")
                await params.result_callback("Sorry, I couldn't update your cart just now.")
                return
            cart = data_obj.get("cart", data_obj) if isinstance(data_obj, dict) else {}
            await emit("remove_from_cart", {"cart": cart})
            n = len(cart.get("items", []))
            await params.result_callback(
                f"Done — {n} item{'s' if n != 1 else ''} left in your cart."
            )

        async def handle_create_order(params):
            args = {"session_id": session_id}
            # pickup_time must be a real timestamp for the DB. Shoppers usually say
            # something vague ("this weekend", "tomorrow arvo"), so only pass it
            # through if it's a valid ISO datetime; otherwise omit it (the order
            # still places fine) and just acknowledge the request in speech.
            raw_pt = params.arguments.get("pickup_time")
            if raw_pt:
                s = str(raw_pt).strip()
                try:
                    datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
                    args["pickup_time"] = s
                except ValueError:
                    logger.info(f"Ignoring non-ISO pickup_time {s!r}; placing order without it.")
            try:
                data_obj = await gateway.call_tool("create_order", args)
            except Exception as e:
                logger.error(f"create_order failed: {e}")
                if "empty_cart" in str(e) or "cart is empty" in str(e).lower():
                    await params.result_callback(
                        "Your cart's actually empty right now — the list is what you still "
                        "need, the cart is what we order. Want me to add your items to the "
                        "cart first, then place the order?"
                    )
                else:
                    await params.result_callback("Sorry, I couldn't place the order just now.")
                return
            order = data_obj.get("order", data_obj) if isinstance(data_obj, dict) else {}
            code = order.get("pickup_code", "")
            await emit("create_order", {"order": order})
            await params.result_callback(
                f"Order placed. Total {_dollars(order.get('total_cents', 0))}, pickup code {code}."
            )

        async def handle_get_offers(params):
            args = {}
            if params.arguments.get("category"):
                args["category"] = params.arguments["category"]
            if params.arguments.get("limit"):
                args["limit"] = params.arguments["limit"]
            try:
                data_obj = await gateway.call_tool("get_offers", args)
            except Exception as e:
                logger.error(f"get_offers failed: {e}")
                await params.result_callback("Sorry, I couldn't pull up the specials just now.")
                return
            offers = data_obj.get("offers", []) if isinstance(data_obj, dict) else []
            await emit("get_offers", {"offers": offers})
            if offers:
                top = offers[0]
                save = top.get("savings_cents")
                save_txt = f", save {_dollars(save)}" if save else ""
                await params.result_callback(
                    f"Top deal: {top['name']} at {_dollars(top.get('special_price_cents') or top.get('price_cents'))}{save_txt}. "
                    f"{len(offers)} specials on screen."
                )
            else:
                await params.result_callback("I couldn't find any specials right now.")

        async def handle_get_grocery_list(params):
            try:
                data_obj = await gateway.call_tool("get_grocery_list", {"user_id": DEMO_USER_ID})
            except Exception as e:
                logger.error(f"get_grocery_list failed: {e}")
                await params.result_callback("Sorry, I couldn't read your list just now.")
                return
            glist = data_obj.get("list", data_obj) if isinstance(data_obj, dict) else {}
            items = [i for i in glist.get("items", []) if i.get("status") == "active"]
            await emit("get_grocery_list", {"list": glist})
            if items:
                names = ", ".join(i.get("name") or i.get("raw_text") for i in items[:5])
                await params.result_callback(f"You still need {len(items)} item{'s' if len(items) != 1 else ''}: {names}.")
            else:
                await params.result_callback("Your grocery list is empty.")

        async def handle_update_grocery_list(params):
            add = params.arguments.get("add") or []
            mark_have = params.arguments.get("mark_have") or []
            remove = params.arguments.get("remove") or []

            gw_add = [{"raw_text": str(s)} for s in add if str(s).strip()]
            gw_update, gw_remove = [], []

            # Resolve mark_have / remove names -> item_ids against the current list.
            if mark_have or remove:
                try:
                    cur = await gateway.call_tool("get_grocery_list", {"user_id": DEMO_USER_ID})
                    cur_items = (cur.get("list", {}) if isinstance(cur, dict) else {}).get("items", [])
                except Exception:
                    cur_items = []

                def find_ids(names):
                    ids = []
                    for n in names:
                        nl = str(n).lower().strip()
                        for it in cur_items:
                            text = (it.get("name") or it.get("raw_text") or "").lower()
                            if nl and nl in text and it.get("status") != "removed":
                                ids.append(it.get("item_id"))
                                break
                    return ids

                gw_update = [{"item_id": iid, "status": "have"} for iid in find_ids(mark_have)]
                gw_remove = find_ids(remove)

            payload = {"user_id": DEMO_USER_ID}
            if gw_add:
                payload["add"] = gw_add
            if gw_update:
                payload["update"] = gw_update
            if gw_remove:
                payload["remove"] = gw_remove
            if len(payload) == 1:
                await params.result_callback("Tell me what to add to or change on your list.")
                return

            try:
                data_obj = await gateway.call_tool("update_grocery_list", payload)
            except Exception as e:
                logger.error(f"update_grocery_list failed: {e}")
                await params.result_callback("Sorry, I couldn't update your list just now.")
                return
            glist = data_obj.get("list", data_obj) if isinstance(data_obj, dict) else {}
            await emit("get_grocery_list", {"list": glist})
            active = [i for i in glist.get("items", []) if i.get("status") == "active"]
            await params.result_callback(f"Updated your list — {len(active)} item{'s' if len(active) != 1 else ''} to go.")

        async def handle_check_relevant_changes(params):
            try:
                data_obj = await gateway.call_tool("check_relevant_changes", {"user_id": DEMO_USER_ID})
            except Exception as e:
                logger.error(f"check_relevant_changes failed: {e}")
                await params.result_callback("I couldn't check for changes just now.")
                return
            changes = data_obj.get("changes", []) if isinstance(data_obj, dict) else []
            await emit("check_relevant_changes", {"changes": changes})
            if changes:
                msg = "; ".join(c.get("message", "") for c in changes[:3] if c.get("message"))
                await params.result_callback(msg or f"{len(changes)} updates on your list.")
            else:
                await params.result_callback("Nothing's changed on your list — no new specials or stockouts.")

        llm.register_function("search_products", handle_search_products)
        llm.register_function("get_product_variants", handle_get_product_variants)
        llm.register_function("add_to_cart", handle_add_to_cart)
        llm.register_function("get_cart", handle_get_cart)
        llm.register_function("remove_from_cart", handle_remove_from_cart)
        llm.register_function("create_order", handle_create_order)
        llm.register_function("get_offers", handle_get_offers)
        llm.register_function("get_grocery_list", handle_get_grocery_list)
        llm.register_function("update_grocery_list", handle_update_grocery_list)
        llm.register_function("check_relevant_changes", handle_check_relevant_changes)

        # --- AgentCore Memory turn capture (no-op when memory disabled) ---
        # The aggregators emit the finalized transcript for each side. We latch the
        # user's finalized text, then on the assistant's turn-stopped write BOTH as
        # one event (fire-and-forget). Text lives in message.content (pipecat 1.3.x);
        # read it duck-typed and fall back to the context tail if it's ever empty.
        last_user_text = {"v": ""}

        def _latest_text_for(role: str) -> str:
            for m in reversed(context.messages):
                if (m.get("role") if isinstance(m, dict) else None) == role:
                    c = m.get("content")
                    if isinstance(c, str):
                        return c
                    if isinstance(c, list):
                        return " ".join(
                            b.get("text", "") for b in c if isinstance(b, dict)
                        ).strip()
            return ""

        @context_aggregator.user().event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(aggregator, strategy, message):
            text = (getattr(message, "content", "") or "").strip()
            last_user_text["v"] = text or _latest_text_for("user")

        @context_aggregator.assistant().event_handler("on_assistant_turn_stopped")
        async def on_assistant_turn_stopped(aggregator, message):
            assistant_text = (getattr(message, "content", "") or "").strip()
            if not assistant_text:
                assistant_text = _latest_text_for("assistant")
            user_text = last_user_text["v"]
            last_user_text["v"] = ""
            task = mem.save_turn_bg(user_id, session_id, user_text, assistant_text)
            if task is not None:
                last_save_task["t"] = task

        # Room URL surfacing for the avatar path: TavusTransport creates the Tavus
        # room dynamically, so capture its URL on connect and surface it to the
        # frontend via the invocation response (see the runner tail below).
        room_ready = asyncio.Event()
        room_holder = {"url": None}

        if avatar_on:
            @transport.event_handler("on_connected")
            async def on_connected(transport, data):
                room_name = (data or {}).get("callConfig", {}).get("roomName")
                if room_name:
                    room_holder["url"] = f"https://tavus.daily.co/{room_name}"
                    logger.info(f"Tavus conversation room: {room_holder['url']}")
                    room_ready.set()

        async def on_participant_ready(transport, participant):
            participant_joined["v"] = True
            if avatar_on:
                logger.info("Participant joined; giving the avatar a moment to initialize...")
                await asyncio.sleep(3)
            logger.info("Participant joined — checking grocery list + relevant changes")

            change_msgs, list_count = [], 0
            try:
                ch = await gateway.call_tool("check_relevant_changes", {"user_id": DEMO_USER_ID})
                changes = ch.get("changes", []) if isinstance(ch, dict) else []
                if changes:
                    await emit("check_relevant_changes", {"changes": changes})
                    change_msgs = [c.get("message", "") for c in changes[:3] if c.get("message")]
            except Exception as e:
                logger.warning(f"opening check_relevant_changes failed: {e}")
            try:
                gl = await gateway.call_tool("get_grocery_list", {"user_id": DEMO_USER_ID})
                glist = gl.get("list", {}) if isinstance(gl, dict) else {}
                await emit("get_grocery_list", {"list": glist})
                list_count = len([i for i in glist.get("items", []) if i.get("status") == "active"])
            except Exception as e:
                logger.warning(f"opening get_grocery_list failed: {e}")

            if change_msgs:
                instruction = (
                    "Greet the shopper warmly and briefly as Aisle (one short sentence), then "
                    "casually flag these updates on their grocery list: " + "; ".join(change_msgs)
                    + ". Keep it upbeat, then ask how you can help today."
                )
            elif list_count:
                instruction = (
                    f"Greet the shopper warmly and briefly as Aisle. They have {list_count} item(s) "
                    "on their grocery list — sound pleased to see them and offer to review the list, "
                    "hunt for specials, or keep shopping."
                )
            else:
                instruction = f"Greet the shopper warmly and briefly as Aisle: {CUSTOM_GREETING}"

            logger.info("Sending greeting")
            messages.append({"role": "system", "content": instruction})
            await worker.queue_frames([LLMRunFrame()])

        # TavusTransport fires on_client_connected for the human (the avatar is
        # filtered out); DailyTransport (audio-only) fires on_first_participant_joined.
        _ready_event = "on_client_connected" if avatar_on else "on_first_participant_joined"
        transport.event_handler(_ready_event)(on_participant_ready)

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Client disconnected")
            # Best-effort: let the final turn's memory save finish (bounded 2s) so
            # the last exchange isn't dropped. Never blocks longer than that.
            pending = last_save_task["t"]
            if pending is not None and not pending.done():
                try:
                    await asyncio.wait_for(asyncio.shield(pending), timeout=2.0)
                except Exception:
                    pass
            await worker.cancel()

        async def no_join_watchdog():
            import asyncio
            await asyncio.sleep(NO_JOIN_TIMEOUT_SECS)
            if not participant_joined["v"]:
                logger.info(
                    f"No participant joined within {NO_JOIN_TIMEOUT_SECS}s; "
                    "leaving the room to stop consuming minutes."
                )
                await worker.cancel()

        runner = WorkerRunner(handle_sigint=False)
        task_id = app.add_async_task("voice_agent")
        watchdog = asyncio.create_task(no_join_watchdog())
        await runner.add_workers(worker)
        runner_task = asyncio.create_task(runner.run())
        try:
            # Surface the room URL to the frontend (returned by the Lambda /start).
            # Avatar path: TavusTransport creates the room dynamically — wait for
            # on_connected. Audio-only: it's the room the bot was told to join.
            if avatar_on:
                try:
                    await asyncio.wait_for(room_ready.wait(), timeout=25)
                    yield {"status": "ready", "room_url": room_holder["url"]}
                except asyncio.TimeoutError:
                    logger.warning("Tavus room URL not ready within 25s")
                    yield {"status": "ready"}
            else:
                yield {"status": "ready", "room_url": getattr(transport, "room_url", None)}
            await runner_task
        finally:
            watchdog.cancel()
        app.complete_async_task(task_id)

    yield {"status": "completed"}


# =============================================================================
# Entry points
# =============================================================================


@app.entrypoint
async def agentcore_bot(payload, context):
    """Bot entry point for Amazon Bedrock AgentCore Runtime."""
    logger.info(f"Received trigger payload: {payload}")
    user_id = payload.get("user_id")
    session_id = payload.get("session_id")

    if _avatar_enabled():
        # Avatar on: TavusTransport creates its own Daily room and the bot, the
        # Tavus replica, and the user all join it. The room URL is created
        # dynamically and surfaced back to the frontend from run_bot.
        async with aiohttp.ClientSession() as http:
            transport = TavusTransport(
                bot_name="Aisle",
                session=http,
                api_key=os.getenv("TAVUS_API_KEY"),
                replica_id=os.getenv("TAVUS_REPLICA_ID"),
                params=_build_tavus_params(),
            )
            prepare_tavus_transport_for_agentcore(transport)
            async for result in run_bot(transport, user_id=user_id, session_id=session_id):
                yield result
        return

    # Audio-only: join the Daily room provided in the invocation payload.
    room_url = payload.get("room_url")
    if not room_url:
        logger.error("No room_url in trigger payload")
        yield {"status": "error", "message": "room_url not provided in payload"}
        return

    transport = DailyTransport(
        room_url,
        payload.get("token"),
        "Aisle",
        _build_daily_params(),
    )
    prepare_daily_transport_for_agentcore(transport)

    async for result in run_bot(transport, user_id=user_id, session_id=session_id):
        yield result


async def bot(runner_args: RunnerArguments):
    """Bot entry point for local development (pipecat runner)."""
    room_url = os.getenv("DAILY_ROOM_URL")
    if not room_url:
        raise ValueError("DAILY_ROOM_URL environment variable is not set")

    transport = DailyTransport(
        room_url,
        None,
        "Aisle",
        _build_daily_params(),
    )

    # Local dev: identify as the demo user so long-term memory is exercised.
    async for _ in run_bot(transport, user_id=os.getenv("DEMO_USER_ID")):
        pass


if __name__ == "__main__":
    if os.getenv("PIPECAT_LOCAL_DEV") == "1":
        from pipecat.runner.run import main

        main()
    else:
        app.run()
