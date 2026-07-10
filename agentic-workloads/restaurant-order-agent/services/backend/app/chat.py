"""
Chat endpoint with conversation memory.
Maintains per-session message history so Claude remembers the full conversation.
"""

import json
import logging
import uuid
from typing import Optional

import boto3

from .config import AWS_REGION, CHAT_MODEL_ID, DEMO_PHONE_NUMBER

logger = logging.getLogger(__name__)

MODEL_ID = CHAT_MODEL_ID

SYSTEM_PROMPT = """You are a friendly restaurant voice assistant for Tasty Bites food delivery.
You help customers browse the menu, add items to their cart, place orders, and track deliveries.
Keep responses concise and conversational (they'll be spoken aloud).

AUTHENTICATION FLOW (MANDATORY before any cart/order action):
1. When a customer wants to order, say: "I'll need to verify your identity first. What's your phone number?"
2. Once they give the phone number, call request_otp with their number. Tell them: "I've sent a verification code to your phone. What's the 6-digit code?"
3. When they speak the OTP digits, call verify_otp with the phone and code.
4. On success, call get_profile with the phone number. Then say: "Welcome back [name]! I'll deliver to [address]. Is that correct?" Wait for confirmation before proceeding.
5. If the OTP is wrong, say "That code didn't work. Let me send another one." and call request_otp again.
6. NEVER reveal or read the OTP code aloud. The user must check their phone for it.

RULES:
- You can show the menu WITHOUT authentication (list_menu is public).
- NEVER call add_cart_item, place_order, get_cart, or get_current_order without authenticating first.
- Keep responses SHORT - they'll be spoken aloud. No more than 2-3 sentences per turn.
- When reading the menu, just name a few highlights per cuisine, don't list everything.
- After authentication, confirm the delivery address from get_profile.
- When they want to add something, find the item ID from the menu and use add_cart_item.
- Be helpful and guide them through the ordering process.
- NEVER use markdown formatting (no **, no *, no #, no bullet points). Responses are spoken aloud.
- Keep numbers as digits in text (401, not four hundred and one). They will be spoken correctly by TTS.
- For phone numbers, speak each digit separately, not as one large number.
- For prices, say "rupees three forty nine" not "₹349".
- For OTP codes, speak each digit: "one nine nine zero two seven" not "199027"."""

TOOLS = [
    {"name": "request_otp", "description": "Send OTP verification code to a phone number via SMS", "inputSchema": {"json": {"type": "object", "properties": {"phone_number": {"type": "string", "description": "Phone in E.164 format e.g. +14155552671"}}, "required": ["phone_number"]}}},
    {"name": "verify_otp", "description": "Verify the OTP code the customer provides", "inputSchema": {"json": {"type": "object", "properties": {"phone_number": {"type": "string", "description": "Phone number"}, "otp_code": {"type": "string", "description": "6-digit OTP code"}}, "required": ["phone_number", "otp_code"]}}},
    {"name": "list_menu", "description": "Get the restaurant menu", "inputSchema": {"json": {"type": "object", "properties": {"dietary_flag": {"type": "string", "description": "Filter: veg, vegan, or non-veg"}}}}},
    {"name": "get_menu_item", "description": "Get menu item details", "inputSchema": {"json": {"type": "object", "properties": {"item_id": {"type": "integer", "description": "Menu item ID"}}, "required": ["item_id"]}}},
    {"name": "add_cart_item", "description": "Add item to cart", "inputSchema": {"json": {"type": "object", "properties": {"menu_item_id": {"type": "integer", "description": "Menu item ID"}, "quantity": {"type": "integer", "description": "Quantity, default 1"}}, "required": ["menu_item_id"]}}},
    {"name": "get_cart", "description": "View current cart contents", "inputSchema": {"json": {"type": "object", "properties": {}}}},
    {"name": "place_order", "description": "Place order from cart", "inputSchema": {"json": {"type": "object", "properties": {"payment_status": {"type": "string", "description": "Payment method"}}}}},
    {"name": "get_current_order", "description": "Get active order status", "inputSchema": {"json": {"type": "object", "properties": {}}}},
    {"name": "get_delivery_status", "description": "Track delivery", "inputSchema": {"json": {"type": "object", "properties": {"order_id": {"type": "integer", "description": "Order ID"}}, "required": ["order_id"]}}},
    {"name": "get_profile", "description": "Get customer profile including delivery address", "inputSchema": {"json": {"type": "object", "properties": {"phone_number": {"type": "string", "description": "Phone number"}}}}},
]

# In-memory conversation history per session
_sessions: dict[str, list] = {}


def get_or_create_session(session_id: Optional[str]) -> tuple[str, list]:
    """Get existing session history or create a new one."""
    if session_id and session_id in _sessions:
        return session_id, _sessions[session_id]
    new_id = session_id or str(uuid.uuid4())
    _sessions[new_id] = []
    return new_id, _sessions[new_id]


async def execute_tool(tool_name: str, args: dict) -> dict:
    """Execute a tool against our own in-memory store."""
    from .store import get_menu, get_menu_item, get_cart, add_to_cart, place_order, get_current_order, get_order, get_profile
    from .auth import send_otp, verify_otp

    phone = DEMO_PHONE_NUMBER

    try:
        if tool_name == "request_otp":
            result = send_otp(args["phone_number"])
            return {"success": True, "message": f"OTP sent via SMS to {args['phone_number']}"}
        elif tool_name == "verify_otp":
            result = verify_otp(args["phone_number"], args["otp_code"])
            if result:
                return {"success": True, "message": "Verified successfully!", "phone_number": args["phone_number"]}
            else:
                return {"success": False, "message": "Invalid or expired OTP code. Try again."}
        elif tool_name == "list_menu":
            return get_menu(args.get("dietary_flag"))
        elif tool_name == "get_menu_item":
            item = get_menu_item(args["item_id"])
            return item if item else {"error": "Item not found"}
        elif tool_name == "add_cart_item":
            return add_to_cart(phone, args["menu_item_id"], args.get("quantity", 1))
        elif tool_name == "get_cart":
            return get_cart(phone)
        elif tool_name == "place_order":
            result = place_order(phone, args.get("payment_status", "cash-on-delivery"))
            return result if result else {"error": "Cart is empty"}
        elif tool_name == "get_current_order":
            order = get_current_order(phone)
            return order if order else {"message": "No active orders"}
        elif tool_name == "get_delivery_status":
            order = get_order(args["order_id"])
            return {"order_id": args["order_id"], "status": order["status"]} if order else {"error": "Order not found"}
        elif tool_name == "get_profile":
            p = args.get("phone_number", phone)
            return get_profile(p)
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {"error": str(e)}


async def chat_with_agent(message: str, session_token: Optional[str] = None, session_id: Optional[str] = None) -> dict:
    """
    Process a chat message through Claude with tool calling.
    Maintains full conversation history per session for memory.
    Returns: {"response": str, "session_id": str}
    """
    try:
        bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    except Exception as e:
        logger.error(f"Bedrock client error: {e}")
        return {"response": "I'm having trouble connecting. Please try again.", "session_id": session_id}

    # Get or create session with conversation history
    sid, history = get_or_create_session(session_id)

    # Add user message to history
    history.append({"role": "user", "content": [{"text": message}]})

    # Agentic loop
    max_turns = 8
    for _ in range(max_turns):
        try:
            response = bedrock.converse(
                modelId=MODEL_ID,
                system=[{"text": SYSTEM_PROMPT}],
                messages=history,
                toolConfig={"tools": [{"toolSpec": t} for t in TOOLS]},
            )
        except Exception as e:
            logger.error(f"Bedrock converse error: {e}")
            return {"response": "Sorry, I'm having trouble thinking right now.", "session_id": sid}

        output = response.get("output", {})
        message_content = output.get("message", {}).get("content", [])
        stop_reason = response.get("stopReason", "")

        # Add assistant response to history
        history.append({"role": "assistant", "content": message_content})

        if stop_reason == "end_turn":
            text_parts = [b["text"] for b in message_content if "text" in b]
            if text_parts:
                return {"response": " ".join(text_parts), "session_id": sid}
            # No text in end_turn — check if there's a tool_use we missed
            has_tool = any("toolUse" in b for b in message_content)
            if has_tool:
                # Treat as tool_use
                stop_reason = "tool_use"
            else:
                logger.warning(f"end_turn with no text, content: {message_content}")
                return {"response": "Could you repeat that? I didn't quite catch it.", "session_id": sid}

        if stop_reason == "tool_use":
            tool_results = []
            for block in message_content:
                if "toolUse" in block:
                    tool = block["toolUse"]
                    result = await execute_tool(tool["name"], tool.get("input", {}))
                    if isinstance(result, list):
                        result = {"items": result}
                    elif not isinstance(result, dict):
                        result = {"value": result}
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool["toolUseId"],
                            "content": [{"json": result}],
                        }
                    })
            history.append({"role": "user", "content": tool_results})
        else:
            text_parts = [b["text"] for b in message_content if "text" in b]
            return {"response": " ".join(text_parts) if text_parts else "I'm not sure how to help.", "session_id": sid}

    return {"response": "Let me simplify. What would you like to do?", "session_id": sid}
