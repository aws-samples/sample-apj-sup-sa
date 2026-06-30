/**
 * LIVE (deployed) contract — shapes verified by invoking the deployed Lambdas
 * (ap-southeast-2): SearchProducts / AddToCart / GetCart /
 * CreateOrder, plus the (incoming) update_grocery_list list tool.
 *
 * Data model: everything the shopper says lands on the
 * LIST first; items are then moved LIST → CART; the cart is checked out.
 *   update_grocery_list → list   (UC1 — the persistent list)
 *   add_to_cart/get_cart → cart   (the curated "ready to buy" subset)
 *   create_order        → order   (checkout, DB-backed, pickup_code + payment)
 *
 * This is NOT the AgentCore /ws v2 wire spec (see ./contracts.ts). The live agent
 * speaks over Daily app-messages. Everything here stays behind lib/event-adapter.ts
 * so a future /ws agent can be swapped in without touching components.
 */

// --- Product — flat shape returned by the live search_products Lambda ---
export interface LiveProduct {
  product_id: string;
  name: string;
  brand?: string;
  category?: string;
  aisle?: string;
  price_cents: number;
  unit?: string;
  allergens?: string[];
  dietary_tags?: string[];
  quality_tier?: string;
  in_stock?: boolean;
  image_url?: string | null;
}

// --- Cart (DB-backed, keyed by session_id) ---
export interface LiveCartItem {
  product_id: string;
  name: string;
  qty: number;
  price_cents: number;
}
export interface LiveCart {
  cart_id?: string;
  session_id?: string;
  items: LiveCartItem[];
  subtotal_cents: number;
}

// --- Order (create_order) ---
export interface LiveOrder {
  order_id: string;
  session_id?: string;
  status: string; // e.g. "submitted"
  pickup_code?: string | null;
  pickup_time?: string | null;
  total_cents: number;
  payment_id?: string | null;
  created_at?: string;
}

// --- Offers / specials (get_offers — joins products ⨝ specials, 282 live rows) ---
export interface LiveOffer {
  product_id: string;
  name: string;
  brand?: string;
  category?: string;
  aisle?: string;
  unit?: string;
  image_url?: string | null;
  price_cents: number; // = special_price_cents on the live tool
  special_price_cents: number;
  was_price_cents: number;
  savings_cents: number;
  pct_below_usual?: number;
  special_type?: string; // "special" | "half_price" | ...
}

// --- Grocery list (update_grocery_list) — designed to the frozen v2 GroceryList ---
export type ListItemStatus = "active" | "have" | "out_of_stock" | "removed";
export interface LiveListItem {
  item_id?: string;
  raw_text?: string;
  name?: string | null; // null until the agent resolves raw_text → a product
  product_id?: string | null;
  qty?: number | string; // the live tool returns qty as a string ("1")
  unit?: string | null;
  status?: ListItemStatus;
}
export interface LiveGroceryList {
  user_id?: string;
  items: LiveListItem[];
  updated_at?: string;
}

// --- Wire events (Daily app-message payloads) ---
export type LiveToolName =
  | "search_products"
  | "get_product_variants"
  | "get_offers"
  | "update_grocery_list"
  | "get_grocery_list"
  | "add_to_cart"
  | "get_cart"
  | "create_order";

export interface LiveTranscriptEvent {
  type: "transcript";
  role: "user" | "agent";
  text: string;
  final: boolean;
  v?: number; // absent on live transcripts
}

export interface LiveToolResultEvent {
  v: number;
  type: "tool_result";
  tool: LiveToolName;
  data:
    | { products: LiveProduct[] }
    | { variants: LiveProduct[] }
    | { offers: LiveOffer[] }
    | { list: LiveGroceryList }
    | { cart: LiveCart }
    | { order: LiveOrder };
}

export type LiveServerEvent = LiveTranscriptEvent | LiveToolResultEvent;

// --- Session start (deployed API Gateway POST) ---
export interface StartResponse {
  room_url: string;
  status: string; // "ok"
}
