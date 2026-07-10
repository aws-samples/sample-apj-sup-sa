"""
Restaurant tool definitions and gateway client for the voice agent.

This module provides:
1. Tool schemas (for Claude's function calling)
2. A client that calls tools via the AgentCore MCP Proxy
"""

import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

# MCP Proxy endpoint (direct AgentCore runtime call - faster than going through gateway)
MCP_PROXY_BASE = os.environ.get("MCP_PROXY_URL", "")
RESTAURANT_API_KEY = os.environ.get("RESTAURANT_API_KEY", "")

# Tool definitions for Claude function calling
RESTAURANT_TOOLS = [
    {
        "name": "request_otp",
        "description": "Request a one-time password sent via SMS to the customer's phone number. Use this when the customer wants to log in or authenticate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone_number": {
                    "type": "string",
                    "description": "Customer phone number in E.164 format (e.g., +14155552671)"
                }
            },
            "required": ["phone_number"]
        }
    },
    {
        "name": "verify_otp",
        "description": "Verify the OTP code the customer provides. Returns a session token for authenticated actions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone_number": {
                    "type": "string",
                    "description": "Customer phone number in E.164 format"
                },
                "otp_code": {
                    "type": "string",
                    "description": "The 6-digit OTP code the customer received"
                }
            },
            "required": ["phone_number", "otp_code"]
        }
    },
    {
        "name": "list_menu",
        "description": "Get the restaurant menu. Can optionally filter by dietary preference (veg, vegan, non-veg).",
        "input_schema": {
            "type": "object",
            "properties": {
                "dietary_flag": {
                    "type": "string",
                    "enum": ["veg", "vegan", "non-veg"],
                    "description": "Optional filter for dietary preference"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_menu_item",
        "description": "Get details of a specific menu item by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "integer",
                    "description": "The menu item ID"
                }
            },
            "required": ["item_id"]
        }
    },
    {
        "name": "get_cart",
        "description": "View the current cart contents and total price.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "add_cart_item",
        "description": "Add a menu item to the cart. Requires the menu item ID and quantity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "menu_item_id": {
                    "type": "integer",
                    "description": "The menu item ID to add"
                },
                "quantity": {
                    "type": "integer",
                    "description": "How many to add (1-99)",
                    "default": 1
                }
            },
            "required": ["menu_item_id"]
        }
    },
    {
        "name": "place_order",
        "description": "Place an order from the current cart. Cart must not be empty and profile must have a delivery address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "payment_status": {
                    "type": "string",
                    "enum": ["pending", "cash-on-delivery"],
                    "default": "cash-on-delivery"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_current_order",
        "description": "Get the customer's current active order and its status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_order",
        "description": "Get details of a specific order by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The order ID"
                }
            },
            "required": ["order_id"]
        }
    },
    {
        "name": "get_delivery_status",
        "description": "Check the delivery status of an order (received, preparing, ready, out-for-delivery, delivered).",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The order ID to check"
                }
            },
            "required": ["order_id"]
        }
    },
    {
        "name": "get_profile",
        "description": "Get the customer's profile including name, dietary preference, address, and order history.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "update_profile",
        "description": "Update customer profile fields like dietary preference, usual portion size, or delivery address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dietary_preference": {
                    "type": "string",
                    "enum": ["veg", "vegan", "non-veg"],
                    "description": "Dietary preference"
                },
                "usual_portion": {
                    "type": "integer",
                    "description": "Usual portion size (1-99)"
                },
                "address": {
                    "type": "string",
                    "description": "Delivery address"
                },
                "customer_type": {
                    "type": "string",
                    "description": "Customer name or type"
                }
            },
            "required": []
        }
    },
]


class RestaurantToolClient:
    """
    Client that executes restaurant tool calls against the backend API.
    Maintains session state (auth token) per conversation.
    """

    def __init__(self):
        self.session_token: str | None = None
        self.customer_phone: str | None = None

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool and return the result as a string for the LLM."""
        # Inject session token for authenticated endpoints
        if self.session_token and tool_name in (
            "get_cart", "add_cart_item", "place_order",
            "get_current_order", "get_order", "get_delivery_status",
            "get_profile", "update_profile"
        ):
            arguments["session_token"] = self.session_token

        try:
            result = await self._call_api(tool_name, arguments)

            # Capture session token from verify_otp
            if tool_name == "verify_otp" and "session_token" in result:
                self.session_token = result["session_token"]
                logger.info(f"Session token acquired for customer {result.get('customer_id')}")

            # Track phone number
            if tool_name == "request_otp" and "phone_number" in arguments:
                self.customer_phone = arguments["phone_number"]

            return json.dumps(result, indent=2)

        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return json.dumps({"error": str(e)})

    async def _call_api(self, tool_name: str, arguments: dict) -> dict:
        """Call the restaurant API directly (bypassing gateway for speed)."""
        headers = {"X-API-Key": RESTAURANT_API_KEY, "Content-Type": "application/json"}

        # Add session auth header
        session_token = arguments.pop("session_token", None)
        if session_token:
            headers["Authorization"] = f"Bearer {session_token}"

        async with httpx.AsyncClient(timeout=15.0, base_url=MCP_PROXY_BASE) as client:
            if tool_name == "request_otp":
                r = await client.post("/auth/otp/request", json={"phone_number": arguments["phone_number"]}, headers=headers)
            elif tool_name == "verify_otp":
                r = await client.post("/auth/otp/verify", json=arguments, headers=headers)
            elif tool_name == "list_menu":
                params = {}
                if arguments.get("dietary_flag"):
                    params["dietary_flag"] = arguments["dietary_flag"]
                r = await client.get("/menu", params=params, headers=headers)
            elif tool_name == "get_menu_item":
                r = await client.get(f"/menu/{arguments['item_id']}", headers=headers)
            elif tool_name == "get_cart":
                r = await client.get("/cart", headers=headers)
            elif tool_name == "add_cart_item":
                r = await client.post("/cart/items", json={"menu_item_id": arguments["menu_item_id"], "quantity": arguments.get("quantity", 1)}, headers=headers)
            elif tool_name == "place_order":
                body = {"payment_status": arguments.get("payment_status", "cash-on-delivery")}
                r = await client.post("/orders", json=body, headers=headers)
            elif tool_name == "get_current_order":
                r = await client.get("/orders/current", headers=headers)
            elif tool_name == "get_order":
                r = await client.get(f"/orders/{arguments['order_id']}", headers=headers)
            elif tool_name == "get_delivery_status":
                r = await client.get(f"/orders/{arguments['order_id']}/delivery-status", headers=headers)
            elif tool_name == "get_profile":
                r = await client.get("/profile", headers=headers)
            elif tool_name == "update_profile":
                r = await client.patch("/profile", json=arguments, headers=headers)
            else:
                return {"error": f"Unknown tool: {tool_name}"}

            if r.status_code >= 400:
                return {"error": r.text, "status_code": r.status_code}
            if r.status_code == 204:
                return {"success": True}
            return r.json()
