/**
 * FROZEN CONTRACT — source of truth for all wire payloads (frontend side).
 *
 * This file and backend/agent/contracts.py MUST stay in lockstep. Any field
 * change is a coordinated two-file PR with a CONTRACT_VERSION bump (see SPEC §2).
 *
 * Rules (SPEC §2):
 * - All JSON wire fields are snake_case (NO camelCase — do not auto-transform).
 * - IDs are string UUIDv4. Money is integer cents (`*_cents`), never float.
 * - Timestamps are ISO-8601 UTC strings.
 * - Every WS event + tool payload carries `v` (=== CONTRACT_VERSION).
 *
 * There is ONE assistant (no `mode`); the user chooses fulfillment mid-conversation.
 *
 * NOTE (merge): the AgentCore Gateway TOOL surface (ToolName, ToolResultData and
 * the tool result shapes) is owned by this project and reflects the LIVE deployed
 * tools — cart-based ordering plus get_order_status, grocery list, offers, and
 * changes. The non-tool surface (WebSocket events incl. OrderProgressEvent)
 * follows the re-locked v2 spec from main. Personalisation (UC4) lives in
 * AgentCore Memory, not a UserProfile payload.
 *
 * Audio is sent/received as BINARY WS frames, never wrapped in JSON:
 *   uplink   = PCM16 mono 16 kHz
 *   downlink = PCM   mono 24 kHz
 */

export const CONTRACT_VERSION = 3 as const;

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------
export type Role = "user" | "agent";
export type AgentState = "listening" | "thinking" | "speaking" | "idle";
export type QualityTier = "value" | "standard" | "premium";
export type Fulfillment = "delivery" | "click_and_collect";
export type GroceryItemStatus = "active" | "have" | "out_of_stock" | "removed";
export type OrderStatus =
  | "draft"
  | "submitted"
  | "ready_for_pickup"
  // async fulfillment lifecycle (Phase 2):
  | "paid"
  | "placing"
  | "placed"
  | "declined_insufficient_funds"
  | "browser_blocked"
  | "failed";
// Live UC2 ordering progress steps (carried on OrderProgressEvent).
export type OrderStep =
  | "resolving"
  | "searching"
  | "adding"
  | "reviewing"
  | "paying"
  | "placed"
  | "failed";

// The AgentCore Gateway tools this project owns + deploys.
export type ToolName =
  | "search_products"
  | "get_product_variants"
  | "get_recipe"
  | "suggest_recipes"
  | "add_to_cart"
  | "get_cart"
  | "remove_from_cart"
  | "create_order"
  | "get_order_status"
  | "get_grocery_list"
  | "update_grocery_list"
  | "get_offers"
  | "check_relevant_changes";

// ---------------------------------------------------------------------------
// Domain objects (shared by tools §3.5 and UI §3.1)
// ---------------------------------------------------------------------------
export interface Product {
  product_id: string;
  name: string;
  brand: string;
  category: string;
  aisle: string;
  price_cents: number;
  unit: string;
  allergens: string[];
  dietary_tags: string[];
  quality_tier: QualityTier;
  in_stock: boolean;
  image_url: string | null;
}

export interface RecipeIngredient {
  product_id: string;
  name: string;
  qty: number;
  unit: string;
}

export interface Recipe {
  recipe_id: string;
  name: string;
  servings: number;
  steps: string[];
  ingredients: RecipeIngredient[];
}

export interface RecipeSummary {
  recipe_id: string;
  name: string;
  servings: number;
}

export interface CartItem {
  product_id: string;
  name: string;
  qty: number;
  price_cents: number;
}

export interface Cart {
  cart_id: string;
  session_id: string;
  items: CartItem[];
  subtotal_cents: number;
}

export interface Order {
  order_id: string;
  session_id: string;
  status: OrderStatus;
  pickup_code: string;
  pickup_time: string | null; // ISO-8601 UTC
  total_cents: number;
  created_at: string; // ISO-8601 UTC
  // payment + async-fulfillment audit (additive, Phase 2)
  payment_id?: string | null;
  browser_session_id?: string | null;
  status_detail?: string | null;
  updated_at?: string | null; // ISO-8601 UTC
}

// ---------------------------------------------------------------------------
// Order observability (Phase 2) — cart/buying lifecycle surfaced to the UI.
// get_order_status returns OrderStatusDetail: the order plus its event
// timeline, payment audit trail, and captured browser artifacts.
// ---------------------------------------------------------------------------
export interface OrderEvent {
  event_id: string;
  event_type: string;
  created_at: string; // ISO-8601 UTC
  payload: Record<string, unknown>;
}

export interface PaymentAudit {
  payment_id?: string | null;
  amount_cents?: number | null;
  status?: string | null;
  session_budget_remaining?: string | null;
  network?: string | null;
  wallet_balance?: string | null;
}

export interface OrderArtifact {
  artifact_id: string;
  kind: "screenshot" | "live_view" | "dom" | "log";
  label: string;
  created_at: string; // ISO-8601 UTC
  url?: string | null;
}

export interface OrderStatusDetail {
  order: Order;
  timeline: OrderEvent[];
  payment?: PaymentAudit | null;
  artifacts: OrderArtifact[];
}

// OrderPreview — basket the agent assembled, shown for confirmation at the
// "reviewing" step (carried on OrderProgressEvent). Not a gateway tool result.
export interface OrderItem {
  name: string;
  qty: number;
  price_cents: number;
  product_id?: string | null;
}

export interface OrderPreview {
  items: OrderItem[];
  subtotal_cents: number;
  fulfillment: Fulfillment;
}

// ---------------------------------------------------------------------------
// UC1 — persistent per-user grocery list
// ---------------------------------------------------------------------------
export interface GroceryItem {
  item_id: string;
  raw_text: string;
  status: GroceryItemStatus;
  product_id?: string | null; // resolved match (via search_products), if any
  name?: string | null;       // resolved product name snapshot
  qty: number;
  unit?: string | null;
}

export interface GroceryList {
  user_id: string;
  items: GroceryItem[];
}

// ---------------------------------------------------------------------------
// UC5 — offers + relevant changes
// ---------------------------------------------------------------------------
export interface Offer {
  product_id: string;
  name: string;
  brand: string;
  category: string;
  aisle: string;
  unit: string;
  price_cents: number; // shelf price
  special_price_cents: number;
  was_price_cents: number;
  savings_cents: number;
  pct_below_usual: number; // e.g. 30 -> "30% below its usual price"
  special_type: string;
  image_url?: string | null;
}

// One per query term in get_offers SEARCH mode. `matched` is false when we
// searched but found nothing on special for the term (offers then empty).
export interface OfferGroup {
  query: string;
  offers: Offer[];
  matched: boolean;
}

export interface RelevantChange {
  kind: "on_special" | "out_of_stock";
  item_id: string; // the grocery_items row this relates to
  product_id: string;
  name: string;
  special_price_cents?: number | null;
  was_price_cents?: number | null;
  savings_cents?: number | null;
  special_type?: string | null;
}

// ---------------------------------------------------------------------------
// Session Broker HTTP response (SPEC §3.1 / §3.2)
//   GET {VITE_SESSION_API_URL}/session
// ---------------------------------------------------------------------------
export interface SessionResponse {
  v: number;
  user_id: string; // the known demo user — assistant "remembers me" from turn one
  session_id: string;
  ws_url: string; // SigV4 pre-signed wss:// URL
  expires_in: number;
}

// ---------------------------------------------------------------------------
// WebSocket text events (SPEC §3.4)
// ---------------------------------------------------------------------------
// Browser -> Agent
export interface InitMessage {
  v: number;
  type: "init";
  session_id: string;
  user_id: string;
}

export interface UserActionMessage {
  v: number;
  type: "user_action";
  // confirm_order / cancel_order gate the real payment (SPEC §3.3, §5.1)
  action: "mute" | "unmute" | "end" | "confirm_order" | "cancel_order";
}

export type ClientMessage = InitMessage | UserActionMessage;

// Agent -> Browser
export interface TranscriptEvent {
  v: number;
  type: "transcript";
  role: Role;
  text: string;
  final: boolean;
}

export interface AgentStateEvent {
  v: number;
  type: "agent_state";
  state: AgentState;
}

// `data` is a discriminated map keyed by `tool` (see ToolResultData).
export interface ToolResultEvent {
  v: number;
  type: "tool_result";
  tool: ToolName;
  data: ToolResultData;
}

export interface OrderProgressEvent {
  v: number;
  type: "order_progress";
  order_id: string;
  step: OrderStep;
  message: string;
  item?: string | null; // the list item currently being handled, if any
  preview?: OrderPreview | null; // present only on step === "reviewing"
}

export interface ErrorEvent {
  v: number;
  type: "error";
  code: string;
  message: string;
}

export type ServerEvent =
  | TranscriptEvent
  | AgentStateEvent
  | ToolResultEvent
  | OrderProgressEvent
  | ErrorEvent;

// ---------------------------------------------------------------------------
// Tool result `data` shapes (SPEC §3.5). Frontend switches on `tool`.
// ---------------------------------------------------------------------------
export type ToolResultData =
  | { products: Product[] }              // search_products
  | { variants: Product[] }              // get_product_variants
  | { recipe: Recipe }                   // get_recipe
  | { recipes: RecipeSummary[] }         // suggest_recipes
  | { cart: Cart }                       // add_to_cart, get_cart, remove_from_cart
  | { order: Order }                     // create_order
  | { order_status: OrderStatusDetail }  // get_order_status
  | { list: GroceryList }                // get_grocery_list, update_grocery_list
  | { offers: Offer[] }                  // get_offers (browse mode)
  | { results: OfferGroup[]; offers: Offer[] } // get_offers (search mode: grouped + flattened)
  | { changes: RelevantChange[] };       // check_relevant_changes
