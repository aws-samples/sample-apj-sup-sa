"""
MCP Proxy Server for Restaurant API.

Deployed on AgentCore Runtime as a lightweight MCP server that proxies
tool calls to the HTTP restaurant backend. This solves the HTTPS requirement
of AgentCore Gateway by accepting MCP calls over HTTPS and forwarding them
to the HTTP ELB.

Tools exposed (from the customer-tools OpenAPI spec):
- request_otp: Request SMS OTP
- verify_otp: Verify OTP and get session token
- list_menu: Browse available menu items
- get_menu_item: Get details of a single item
- get_cart: View current cart
- add_cart_item: Add item to cart
- place_order: Place an order
- get_current_order: Get active order
- get_order: Get order by ID
- get_delivery_status: Track order delivery
- get_profile: Get customer profile
- update_profile: Update profile fields
"""

import os
import json
import logging
import httpx

from bedrock_agentcore.runtime import BedrockAgentCoreApp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# MCP tool definitions for tools/list
MCP_TOOLS = [
    {"name": "request_otp", "description": "Request SMS OTP for a phone number", "inputSchema": {"type": "object", "properties": {"phone_number": {"type": "string", "description": "E.164 format phone number"}}, "required": ["phone_number"]}},
    {"name": "verify_otp", "description": "Verify OTP and get session token", "inputSchema": {"type": "object", "properties": {"phone_number": {"type": "string"}, "otp_code": {"type": "string", "description": "6-digit OTP code"}}, "required": ["phone_number", "otp_code"]}},
    {"name": "list_menu", "description": "List available menu items, optionally filtered by dietary flag", "inputSchema": {"type": "object", "properties": {"dietary_flag": {"type": "string", "description": "Optional filter: veg, vegan, or non-veg"}}}},
    {"name": "get_menu_item", "description": "Get details of a single menu item by ID", "inputSchema": {"type": "object", "properties": {"item_id": {"type": "integer"}}, "required": ["item_id"]}},
    {"name": "get_cart", "description": "Get current cart contents and total", "inputSchema": {"type": "object", "properties": {"session_token": {"type": "string"}}, "required": ["session_token"]}},
    {"name": "add_cart_item", "description": "Add a menu item to the cart", "inputSchema": {"type": "object", "properties": {"menu_item_id": {"type": "integer"}, "quantity": {"type": "integer", "minimum": 1}, "session_token": {"type": "string"}}, "required": ["menu_item_id", "quantity", "session_token"]}},
    {"name": "place_order", "description": "Place an order from the current cart", "inputSchema": {"type": "object", "properties": {"payment_status": {"type": "string", "enum": ["pending", "cash-on-delivery"]}, "session_token": {"type": "string"}}, "required": ["session_token"]}},
    {"name": "get_current_order", "description": "Get the current active order", "inputSchema": {"type": "object", "properties": {"session_token": {"type": "string"}}, "required": ["session_token"]}},
    {"name": "get_order", "description": "Get order details by ID", "inputSchema": {"type": "object", "properties": {"order_id": {"type": "integer"}, "session_token": {"type": "string"}}, "required": ["order_id", "session_token"]}},
    {"name": "get_delivery_status", "description": "Get delivery status of an order", "inputSchema": {"type": "object", "properties": {"order_id": {"type": "integer"}, "session_token": {"type": "string"}}, "required": ["order_id", "session_token"]}},
    {"name": "get_profile", "description": "Get customer profile and order history", "inputSchema": {"type": "object", "properties": {"session_token": {"type": "string"}}, "required": ["session_token"]}},
    {"name": "update_profile", "description": "Update customer profile fields", "inputSchema": {"type": "object", "properties": {"dietary_preference": {"type": "string"}, "usual_portion": {"type": "integer"}, "address": {"type": "string"}, "session_token": {"type": "string"}}, "required": ["session_token"]}},
]

RESTAURANT_API_BASE = os.environ.get("RESTAURANT_API_BASE", "")
RESTAURANT_API_KEY = os.environ.get("RESTAURANT_API_KEY", "")

app = BedrockAgentCoreApp()


@app.websocket
async def handle_mcp(websocket, context):
    """
    MCP protocol handler over WebSocket (JSON-RPC 2.0).
    Handles: initialize, tools/list, tools/call
    """
    await websocket.accept()
    
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            method = msg.get("method", "")
            msg_id = msg.get("id")
            params = msg.get("params", {})

            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "restaurant-mcp-proxy", "version": "1.0.0"}
                    }
                }

            elif method == "notifications/initialized":
                continue  # No response needed for notifications

            elif method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": MCP_TOOLS}
                }

            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = await execute_tool(tool_name, arguments)
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result)}]
                    }
                }

            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                }

            await websocket.send_text(json.dumps(response))

    except Exception as e:
        logger.info(f"WebSocket closed: {e}")


async def execute_tool(tool_name: str, args: dict) -> dict:
    """Execute a tool by calling the restaurant HTTP API."""
    session_token = args.pop("session_token", None)
    headers = {"Authorization": f"Bearer {session_token}"} if session_token else {}

    try:
        if tool_name == "request_otp":
            return await call_api("POST", "/auth/otp/request", {"phone_number": args.get("phone_number")})
        elif tool_name == "verify_otp":
            return await call_api("POST", "/auth/otp/verify", {"phone_number": args.get("phone_number"), "otp_code": args.get("otp_code")})
        elif tool_name == "list_menu":
            params = {}
            if args.get("dietary_flag"):
                params["dietary_flag"] = args["dietary_flag"]
            return await call_api("GET", "/menu", params)
        elif tool_name == "get_menu_item":
            return await call_api("GET", f"/menu/{args.get('item_id')}")
        elif tool_name == "get_cart":
            return await call_api("GET", "/cart", headers=headers)
        elif tool_name == "add_cart_item":
            return await call_api("POST", "/cart/items", {"menu_item_id": args.get("menu_item_id"), "quantity": args.get("quantity", 1)}, headers=headers)
        elif tool_name == "place_order":
            body = {}
            if args.get("payment_status"):
                body["payment_status"] = args["payment_status"]
            return await call_api("POST", "/orders", body, headers=headers)
        elif tool_name == "get_current_order":
            return await call_api("GET", "/orders/current", headers=headers)
        elif tool_name == "get_order":
            return await call_api("GET", f"/orders/{args.get('order_id')}", headers=headers)
        elif tool_name == "get_delivery_status":
            return await call_api("GET", f"/orders/{args.get('order_id')}/delivery-status", headers=headers)
        elif tool_name == "get_profile":
            return await call_api("GET", "/profile", headers=headers)
        elif tool_name == "update_profile":
            body = {k: v for k, v in args.items() if v is not None}
            return await call_api("PATCH", "/profile", body, headers=headers)
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {"error": str(e)}


async def call_api(method: str, path: str, body: dict = None, headers: dict = None) -> dict:
    """Make an HTTP call to the restaurant API."""
    url = f"{RESTAURANT_API_BASE}{path}"
    if headers is None:
        headers = {}
    headers["X-API-Key"] = RESTAURANT_API_KEY
    async with httpx.AsyncClient(timeout=15.0) as client:
        if method == "GET":
            resp = await client.get(url, params=body, headers=headers)
        elif method == "POST":
            resp = await client.post(url, json=body, headers=headers)
        elif method == "PATCH":
            resp = await client.patch(url, json=body, headers=headers)
        elif method == "PUT":
            resp = await client.put(url, json=body, headers=headers)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            return {"error": f"Unsupported method: {method}"}

        if resp.status_code >= 400:
            return {"error": resp.text, "status_code": resp.status_code}
        if resp.status_code == 204:
            return {"success": True}
        return resp.json()


def auth_header(session_token: str = None) -> dict:
    """Build auth header if session_token provided."""
    if session_token:
        return {"Authorization": f"Bearer {session_token}"}
    return {}


@app.entrypoint
async def handle_invocation(payload: dict) -> dict:
    """
    MCP-style tool invocation handler.
    Accepts: {"tool": "<tool_name>", "arguments": {...}}
    Returns: tool result from the restaurant API.
    """
    tool = payload.get("tool", "")
    args = payload.get("arguments", {})
    session_token = args.pop("session_token", None)
    headers = auth_header(session_token)

    try:
        if tool == "request_otp":
            result = await call_api("POST", "/auth/otp/request", {
                "phone_number": args.get("phone_number")
            })

        elif tool == "verify_otp":
            result = await call_api("POST", "/auth/otp/verify", {
                "phone_number": args.get("phone_number"),
                "otp_code": args.get("otp_code")
            })

        elif tool == "list_menu":
            params = {}
            if args.get("dietary_flag"):
                params["dietary_flag"] = args["dietary_flag"]
            result = await call_api("GET", "/menu", params)

        elif tool == "get_menu_item":
            item_id = args.get("item_id")
            result = await call_api("GET", f"/menu/{item_id}")

        elif tool == "get_cart":
            result = await call_api("GET", "/cart", headers=headers)

        elif tool == "add_cart_item":
            result = await call_api("POST", "/cart/items", {
                "menu_item_id": args.get("menu_item_id"),
                "quantity": args.get("quantity", 1)
            }, headers=headers)

        elif tool == "place_order":
            body = {}
            if args.get("payment_status"):
                body["payment_status"] = args["payment_status"]
            result = await call_api("POST", "/orders", body, headers=headers)

        elif tool == "get_current_order":
            result = await call_api("GET", "/orders/current", headers=headers)

        elif tool == "get_order":
            order_id = args.get("order_id")
            result = await call_api("GET", f"/orders/{order_id}", headers=headers)

        elif tool == "get_delivery_status":
            order_id = args.get("order_id")
            result = await call_api("GET", f"/orders/{order_id}/delivery-status", headers=headers)

        elif tool == "get_profile":
            result = await call_api("GET", "/profile", headers=headers)

        elif tool == "update_profile":
            body = {k: v for k, v in args.items() if v is not None}
            result = await call_api("PATCH", "/profile", body, headers=headers)

        elif tool == "list_tools":
            result = {
                "tools": [
                    {"name": "request_otp", "description": "Request SMS OTP for phone number", "parameters": {"phone_number": "string (E.164 format)"}},
                    {"name": "verify_otp", "description": "Verify OTP and get session token", "parameters": {"phone_number": "string", "otp_code": "string (6 digits)"}},
                    {"name": "list_menu", "description": "List available menu items", "parameters": {"dietary_flag": "optional: veg|vegan|non-veg"}},
                    {"name": "get_menu_item", "description": "Get menu item details", "parameters": {"item_id": "integer"}},
                    {"name": "get_cart", "description": "Get current cart", "parameters": {"session_token": "string"}},
                    {"name": "add_cart_item", "description": "Add item to cart", "parameters": {"menu_item_id": "integer", "quantity": "integer", "session_token": "string"}},
                    {"name": "place_order", "description": "Place order from cart", "parameters": {"payment_status": "optional: pending|cash-on-delivery", "session_token": "string"}},
                    {"name": "get_current_order", "description": "Get active order", "parameters": {"session_token": "string"}},
                    {"name": "get_order", "description": "Get order by ID", "parameters": {"order_id": "integer", "session_token": "string"}},
                    {"name": "get_delivery_status", "description": "Get delivery status", "parameters": {"order_id": "integer", "session_token": "string"}},
                    {"name": "get_profile", "description": "Get customer profile", "parameters": {"session_token": "string"}},
                    {"name": "update_profile", "description": "Update profile fields", "parameters": {"dietary_preference": "optional", "usual_portion": "optional int", "address": "optional string", "session_token": "string"}},
                ]
            }

        else:
            result = {"error": f"Unknown tool: {tool}", "available_tools": [
                "request_otp", "verify_otp", "list_menu", "get_menu_item",
                "get_cart", "add_cart_item", "place_order", "get_current_order",
                "get_order", "get_delivery_status", "get_profile", "update_profile",
                "list_tools"
            ]}

        return {"result": result, "status": "success"}

    except Exception as e:
        logger.error(f"Tool {tool} failed: {e}")
        return {"error": str(e), "status": "error"}
