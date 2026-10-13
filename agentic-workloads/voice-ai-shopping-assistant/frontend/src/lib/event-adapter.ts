/**
 * Event adapter — the single place that knows the *deployed* (Daily app-message)
 * wire format. Normalizes raw events into store actions so components never see
 * wire shapes and a future /ws agent can be swapped in by rewriting only this
 * file + the transport client.
 *
 * Data model: everything the shopper says lands on the LIST
 * first; the human then moves items LIST → CART; the cart is checked out.
 *   update_grocery_list / get_grocery_list → list
 *   add_to_cart / get_cart                 → cart (curated subset)
 *   create_order                           → order
 *   search_products / get_product_variants → products / variants
 */
import { useConversation } from "../store/conversation";
import type {
  LiveServerEvent,
  LiveToolResultEvent,
  LiveGroceryList,
} from "../types/contracts.live";

function applyToolResult(ev: LiveToolResultEvent) {
  const store = useConversation.getState();
  const { tool, data } = ev;
  switch (tool) {
    case "update_grocery_list":
    case "get_grocery_list":
      if ("list" in data) store.setList(data.list);
      break;
    case "search_products":
      if ("products" in data) store.setProducts(data.products);
      break;
    case "get_product_variants":
      if ("variants" in data) store.setVariants(data.variants);
      break;
    case "get_offers":
      if ("offers" in data) store.setOffers(data.offers);
      break;
    case "add_to_cart":
    case "get_cart":
      if ("cart" in data) store.setCart(data.cart);
      break;
    case "create_order":
      if ("order" in data) store.setOrder(data.order);
      break;
  }
}

export function handleAgentMessage(raw: unknown) {
  if (!raw || typeof raw !== "object") return;
  const ev = raw as Partial<LiveServerEvent> & { type?: string };
  if (!ev.type) return;

  const store = useConversation.getState();

  switch (ev.type) {
    case "transcript": {
      const t = ev as Extract<LiveServerEvent, { type: "transcript" }>;
      if (typeof t.text !== "string") return;
      store.appendTranscript(t.role === "agent" ? "agent" : "user", t.text, !!t.final);
      // Derive agent_state (live agent doesn't emit it).
      if (t.role === "agent") store.setAgentState("speaking");
      else if (t.final) store.setAgentState("thinking");
      break;
    }
    case "tool_result":
      applyToolResult(ev as LiveToolResultEvent);
      break;
    default:
      break;
  }
}

/** Exposed for tests / mock injection. */
export type { LiveGroceryList };
