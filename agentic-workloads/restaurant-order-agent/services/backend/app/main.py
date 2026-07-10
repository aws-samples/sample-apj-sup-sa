"""
Tasty Bites Backend API.
Self-contained backend with:
- SNS OTP authentication (demo mode: OTP returned in response)
- In-memory menu, cart, orders, profile store
- Kitchen dashboard endpoints
- Chat endpoint (Claude via Bedrock)
"""

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .auth import send_otp, verify_otp, decode_token
from .config import CORS_ALLOWED_ORIGINS
from .store import (
    get_menu, get_menu_item, get_cart, add_to_cart,
    place_order, get_current_order, get_order, get_all_orders,
    update_order_status, get_profile, update_profile,
)
from .chat import chat_with_agent
from .voice_proxy import get_voice_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Tasty Bites API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Auth dependency ---

async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Extract and validate JWT from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


# --- Request Models ---

class OTPRequest(BaseModel):
    phone_number: str

class OTPVerify(BaseModel):
    phone_number: str
    otp_code: str

class AddToCartRequest(BaseModel):
    menu_item_id: int
    quantity: int = 1

class PlaceOrderRequest(BaseModel):
    payment_status: str = "cash-on-delivery"

class UpdateProfileRequest(BaseModel):
    dietary_preference: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None

class UpdateOrderStatusRequest(BaseModel):
    status: str

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


# --- Auth Endpoints ---

@app.post("/api/auth/request-otp")
async def request_otp_endpoint(body: OTPRequest):
    """Send OTP via AWS SNS (demo: OTP included in response)."""
    return send_otp(body.phone_number)


@app.post("/api/auth/verify-otp")
async def verify_otp_endpoint(body: OTPVerify):
    """Verify OTP and return JWT."""
    result = verify_otp(body.phone_number, body.otp_code)
    if not result:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    return result


# --- Menu Endpoints (public) ---

@app.get("/api/menu")
async def menu_endpoint(dietary_flag: Optional[str] = None, category: Optional[str] = None):
    """Get restaurant menu."""
    return get_menu(dietary_flag, category)


@app.get("/api/menu/{item_id}")
async def menu_item_endpoint(item_id: int):
    """Get single menu item."""
    item = get_menu_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


# --- Cart Endpoints (authenticated) ---

@app.get("/api/cart")
async def cart_endpoint(user: dict = Depends(get_current_user)):
    """Get current cart."""
    return get_cart(user["sub"])


@app.post("/api/cart/add")
async def add_to_cart_endpoint(body: AddToCartRequest, user: dict = Depends(get_current_user)):
    """Add item to cart."""
    if not get_menu_item(body.menu_item_id):
        raise HTTPException(status_code=404, detail="Menu item not found")
    return add_to_cart(user["sub"], body.menu_item_id, body.quantity)


# --- Order Endpoints (authenticated) ---

@app.post("/api/orders")
async def place_order_endpoint(body: PlaceOrderRequest, user: dict = Depends(get_current_user)):
    """Place order from cart."""
    order = place_order(user["sub"], body.payment_status)
    if not order:
        raise HTTPException(status_code=400, detail="Cart is empty")
    return order


@app.get("/api/orders/current")
async def current_order_endpoint(user: dict = Depends(get_current_user)):
    """Get current active order."""
    order = get_current_order(user["sub"])
    if not order:
        raise HTTPException(status_code=404, detail="No active order")
    return order


@app.get("/api/orders/{order_id}")
async def get_order_endpoint(order_id: int, user: dict = Depends(get_current_user)):
    """Get order by ID."""
    order = get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# --- Profile Endpoints (authenticated) ---

@app.get("/api/profile")
async def profile_endpoint(user: dict = Depends(get_current_user)):
    """Get customer profile."""
    return get_profile(user["sub"])


@app.put("/api/profile")
async def update_profile_endpoint(body: UpdateProfileRequest, user: dict = Depends(get_current_user)):
    """Update customer profile."""
    data = body.model_dump(exclude_none=True)
    return update_profile(user["sub"], data)


# --- Kitchen Endpoints (no auth for hackathon) ---

@app.get("/api/kitchen/orders")
async def kitchen_orders_endpoint():
    """Get all orders for kitchen view."""
    return get_all_orders()


@app.patch("/api/kitchen/orders/{order_id}/status")
async def kitchen_update_status(order_id: int, body: UpdateOrderStatusRequest):
    """Update order status from kitchen."""
    order = update_order_status(order_id, body.status)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# --- Chat Endpoint ---

@app.post("/api/chat")
async def chat_endpoint(body: ChatRequest, authorization: Optional[str] = Header(None)):
    """Text chat with Claude agent (tool calling). Maintains conversation memory per session."""
    session_token = None
    if authorization and authorization.startswith("Bearer "):
        session_token = authorization.split(" ", 1)[1]
    try:
        result = await chat_with_agent(body.message, session_token=session_token, session_id=body.session_id)
        return result
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {"response": "Sorry, I'm having trouble right now. Please try again.", "session_id": None}


# --- Voice Token Endpoint ---

@app.get("/api/voice/token")
async def voice_token_endpoint():
    """
    Returns a pre-signed WebSocket URL for the AgentCore voice agent.
    The browser uses this URL to connect directly to AgentCore (SigV4 embedded in URL).
    Token is cached and auto-refreshed via Lambda when near expiry.
    """
    try:
        token_data = get_voice_token()
        return token_data
    except Exception as e:
        logger.error(f"Voice token error: {e}")
        raise HTTPException(status_code=503, detail=f"Voice agent unavailable: {str(e)}")


# --- Health ---

@app.get("/api/health")
async def health():
    return {"status": "healthy", "service": "tasty-bites-backend"}
