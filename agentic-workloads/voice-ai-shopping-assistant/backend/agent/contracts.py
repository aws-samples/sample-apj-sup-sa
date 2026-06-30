"""Wire payload + tool-result shapes for the Aisle agent.

Documents the shapes exchanged over the WebSocket and the AgentCore Gateway
tool results. Bump `CONTRACT_VERSION` on any breaking field change.

Rules:
- All JSON wire fields are snake_case.
- IDs are string UUIDv4. Money is integer cents (`*_cents`), never float.
- Timestamps are ISO-8601 UTC strings.
- Every WS event and tool payload carries `v` (== CONTRACT_VERSION).

There is ONE assistant (no `mode`); the user chooses fulfillment mid-conversation
(online delivery / click & collect, or just keep the list to shop in-store).

The AgentCore Gateway TOOL surface (ToolName, TOOL_RESULT_KEYS, and the tool
result shapes) reflects the deployed tools — cart-based ordering
(add_to_cart/get_cart/remove_from_cart/create_order) plus get_order_status,
grocery list, offers, and changes.

These dataclasses describe shapes; serialize with dataclasses.asdict(). They are
intentionally dependency-free so any silo can import them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

CONTRACT_VERSION = 3

# ---------------------------------------------------------------------------
# Enums (lowercase snake strings on the wire)
# ---------------------------------------------------------------------------
Role = Literal["user", "agent"]
AgentState = Literal["listening", "thinking", "speaking", "idle"]
QualityTier = Literal["value", "standard", "premium"]
Fulfillment = Literal["delivery", "click_and_collect"]
OrderStatus = Literal[
    "draft", "submitted", "ready_for_pickup",
    # async fulfillment lifecycle (Phase 2):
    "paid",                          # payment proof obtained, queued for fulfillment
    "placing",                       # browser worker is placing the order
    "placed",                        # order successfully placed at the storefront
    "declined_insufficient_funds",   # wallet balance can't cover the total
    "browser_blocked",               # storefront blocked the automated browser
    "failed",                        # other fulfillment error
]
# Live UC2 ordering progress steps (place_order drives the browser; surfaced via
# OrderProgressEvent on the WebSocket).
OrderStep = Literal[
    "resolving",
    "searching",
    "adding",
    "reviewing",
    "paying",
    "placed",
    "failed",
]
GroceryItemStatus = Literal["active", "have", "out_of_stock", "removed"]

# The AgentCore Gateway tools this project owns + deploys. UC3 recipe grounding
# reuses search_products (no separate resolver tool).
ToolName = Literal[
    "search_products",
    "get_product_variants",
    "get_recipe",
    "suggest_recipes",
    "add_to_cart",
    "get_cart",
    "remove_from_cart",
    "create_order",
    "get_order_status",
    "get_grocery_list",
    "update_grocery_list",
    "get_offers",
    "check_relevant_changes",
]

# Personalisation (UC4) is NOT a wire object — dietary prefs / preferred brands
# live in AgentCore Memory (long-term per user_id), applied by the agent as
# search_products filters. No UserProfile payload / profile tool.

# ---------------------------------------------------------------------------
# Domain objects (shared by the tools and the UI)
# ---------------------------------------------------------------------------
@dataclass
class Product:
    product_id: str
    name: str
    brand: str
    category: str
    aisle: str
    price_cents: int
    unit: str
    allergens: list[str] = field(default_factory=list)
    dietary_tags: list[str] = field(default_factory=list)
    quality_tier: QualityTier = "standard"
    in_stock: bool = True
    image_url: Optional[str] = None


@dataclass
class RecipeIngredient:
    product_id: str
    name: str
    qty: float
    unit: str


@dataclass
class Recipe:
    recipe_id: str
    name: str
    servings: int
    steps: list[str] = field(default_factory=list)
    ingredients: list[RecipeIngredient] = field(default_factory=list)


@dataclass
class RecipeSummary:
    recipe_id: str
    name: str
    servings: int


@dataclass
class CartItem:
    product_id: str
    name: str
    qty: int
    price_cents: int


@dataclass
class Cart:
    cart_id: str
    session_id: str
    items: list[CartItem] = field(default_factory=list)
    subtotal_cents: int = 0


@dataclass
class Order:
    order_id: str
    session_id: str
    status: OrderStatus
    pickup_code: str
    pickup_time: Optional[str]  # ISO-8601 UTC
    total_cents: int
    created_at: str  # ISO-8601 UTC
    # payment + async-fulfillment audit (additive, Phase 2). Optional so older
    # callers and the synchronous create_order response stay valid.
    payment_id: Optional[str] = None
    browser_session_id: Optional[str] = None
    status_detail: Optional[str] = None
    updated_at: Optional[str] = None  # ISO-8601 UTC


# ---------------------------------------------------------------------------
# Order observability (Phase 2) — the cart/buying lifecycle surfaced to the UI.
#   get_order_status returns an OrderStatusDetail: the order plus its event
#   timeline, payment audit trail, and captured browser artifacts.
# ---------------------------------------------------------------------------
@dataclass
class OrderEvent:
    event_id: str
    event_type: str          # e.g. "order_created", "payment_processed",
                             # "balance_checked", "item_added", "reached_checkout",
                             # "declined_insufficient_funds", "order_placed", ...
    created_at: str          # ISO-8601 UTC
    payload: dict = field(default_factory=dict)  # step-specific detail


@dataclass
class PaymentAudit:
    payment_id: Optional[str] = None       # AgentCore processPaymentId
    amount_cents: Optional[int] = None     # charged amount (token micropayment)
    status: Optional[str] = None           # e.g. "PROOF_GENERATED"
    session_budget_remaining: Optional[str] = None  # e.g. "0.99" USD
    network: Optional[str] = None          # e.g. "base-sepolia"
    wallet_balance: Optional[str] = None   # live USDC balance at fulfillment time


@dataclass
class OrderArtifact:
    artifact_id: str
    kind: Literal["screenshot", "live_view", "dom", "log"]
    label: str
    created_at: str          # ISO-8601 UTC
    url: Optional[str] = None  # presigned S3 URL (screenshot/dom/log) or stream URL


@dataclass
class OrderStatusDetail:
    order: Order
    timeline: list[OrderEvent] = field(default_factory=list)
    payment: Optional[PaymentAudit] = None
    artifacts: list[OrderArtifact] = field(default_factory=list)


# OrderPreview — the basket the agent assembled, shown for confirmation at the
# "reviewing" step of place_order (UC2 browser ordering). Not a gateway tool
# result; carried on OrderProgressEvent below.
@dataclass
class OrderItem:
    name: str
    qty: int
    price_cents: int
    product_id: Optional[str] = None


@dataclass
class OrderPreview:
    items: list[OrderItem]
    subtotal_cents: int
    fulfillment: Fulfillment


# ---------------------------------------------------------------------------
# UC1 — persistent per-user grocery list
# ---------------------------------------------------------------------------
@dataclass
class GroceryItem:
    item_id: str
    raw_text: str                       # what the shopper said, e.g. "bread"
    status: GroceryItemStatus = "active"
    product_id: Optional[str] = None    # resolved match (via search_products), if any
    name: Optional[str] = None          # resolved product name snapshot
    qty: float = 1
    unit: Optional[str] = None


@dataclass
class GroceryList:
    user_id: str
    items: list[GroceryItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# UC5 — offers + relevant changes
# ---------------------------------------------------------------------------
@dataclass
class Offer:
    product_id: str
    name: str
    brand: str
    category: str
    aisle: str
    unit: str
    price_cents: int                    # shelf price
    special_price_cents: int
    was_price_cents: int
    savings_cents: int
    pct_below_usual: int                # e.g. 30 -> "30% below its usual price"
    special_type: str
    image_url: Optional[str] = None


@dataclass
class OfferGroup:
    # One per query term in get_offers SEARCH mode. `matched` is False when we
    # searched but found nothing on special for the term (so the agent can say
    # so honestly rather than imply a deal); `offers` is then empty.
    query: str
    offers: list[Offer] = field(default_factory=list)
    matched: bool = False


@dataclass
class RelevantChange:
    kind: Literal["on_special", "out_of_stock"]
    item_id: str                        # the grocery_items row this relates to
    product_id: str
    name: str
    special_price_cents: Optional[int] = None
    was_price_cents: Optional[int] = None
    savings_cents: Optional[int] = None
    special_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Session Broker HTTP response
#   GET {VITE_SESSION_API_URL}/session
# ---------------------------------------------------------------------------
@dataclass
class SessionResponse:
    user_id: str          # the known demo user — assistant "remembers me" from turn one
    session_id: str
    ws_url: str           # SigV4 pre-signed wss:// URL
    expires_in: int = 300
    v: int = CONTRACT_VERSION


# ---------------------------------------------------------------------------
# WebSocket text events Audio is binary, NOT wrapped in JSON:
#   uplink  = PCM16 mono 16 kHz
#   downlink= PCM   mono 24 kHz
# ---------------------------------------------------------------------------
# Browser -> Agent
@dataclass
class InitMessage:
    session_id: str
    user_id: str
    type: Literal["init"] = "init"
    v: int = CONTRACT_VERSION


@dataclass
class UserActionMessage:
    # confirm_order / cancel_order gate the real payment
    action: Literal["mute", "unmute", "end", "confirm_order", "cancel_order"]
    type: Literal["user_action"] = "user_action"
    v: int = CONTRACT_VERSION


# Agent -> Browser
@dataclass
class TranscriptEvent:
    role: Role
    text: str
    final: bool
    type: Literal["transcript"] = "transcript"
    v: int = CONTRACT_VERSION


@dataclass
class AgentStateEvent:
    state: AgentState
    type: Literal["agent_state"] = "agent_state"
    v: int = CONTRACT_VERSION


@dataclass
class ToolResultEvent:
    tool: ToolName
    data: dict  # shape (e.g. {"products": [...]}, {"cart": {...}})
    type: Literal["tool_result"] = "tool_result"
    v: int = CONTRACT_VERSION


@dataclass
class OrderProgressEvent:
    """Live UC2 ordering progress as the agent drives the storefront browser.

    place_order-style fulfillment blocks at the "reviewing" step until a
    confirm_order / cancel_order UserActionMessage (or spoken yes/no) arrives.
    Only the "reviewing" event carries `preview`.
    """
    order_id: str
    step: OrderStep
    message: str
    item: Optional[str] = None       # the list item currently being handled, if any
    preview: Optional[OrderPreview] = None  # present only on step == "reviewing"
    type: Literal["order_progress"] = "order_progress"
    v: int = CONTRACT_VERSION


@dataclass
class ErrorEvent:
    code: str
    message: str
    type: Literal["error"] = "error"
    v: int = CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Tool I/O contract
#
# Verified against AWS docs + the live gateway: AgentCore Gateway delivers the
# tool arguments DIRECTLY as the Lambda `event` (e.g. {"query": "milk"}), NOT
# wrapped in {"arguments": {...}}.
# The prefixed tool name is in context.client_context.custom
# ['bedrockAgentCoreToolName'] as "<target_name>___<tool_name>".
# The Lambda returns {"data": {...}} on success or {"error": {...}} on failure.
# Tool handlers should read args from the bare event (tolerating a wrapped form).
# Keys below document the `data` payload each tool returns.
# ---------------------------------------------------------------------------
TOOL_RESULT_KEYS: dict[str, str] = {
    "search_products": "products",        # -> {"products": [Product]}
    "get_product_variants": "variants",   # -> {"variants": [Product]}
    "get_recipe": "recipe",               # -> {"recipe": Recipe}
    "suggest_recipes": "recipes",         # -> {"recipes": [RecipeSummary]}
    "add_to_cart": "cart",                # -> {"cart": Cart}
    "get_cart": "cart",                   # -> {"cart": Cart}
    "remove_from_cart": "cart",           # -> {"cart": Cart}
    "create_order": "order",              # -> {"order": Order}
    "get_order_status": "order_status",   # -> {"order_status": OrderStatusDetail}
    "get_grocery_list": "list",           # -> {"list": GroceryList}
    "update_grocery_list": "list",        # -> {"list": GroceryList}
    # get_offers: browse mode -> {"offers": [Offer]}; search mode (queries[]) ->
    # {"results": [OfferGroup], "offers": [Offer] (flattened, deduped)}.
    "get_offers": "offers",
    "check_relevant_changes": "changes",  # -> {"changes": [RelevantChange]}
}
