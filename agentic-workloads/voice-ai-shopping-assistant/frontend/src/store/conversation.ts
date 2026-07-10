import { create } from "zustand";
import type {
  LiveProduct,
  LiveCart,
  LiveCartItem,
  LiveOrder,
  LiveOffer,
  LiveGroceryList,
  LiveListItem,
} from "../types/contracts.live";
import { SEED } from "../mocks/seed";
import type { MockProfile } from "../mocks/seed";

export type ConnectionState =
  | "idle"
  | "connecting"
  | "connected"
  | "ended"
  | "error";

// Derived locally — the live agent does not emit agent_state (see event-adapter).
export type AgentState = "idle" | "listening" | "thinking" | "speaking";

export type TabKey =
  | "list"
  | "cart"
  | "products"
  | "recipes"
  | "offers"
  | "profile";

export interface TranscriptTurn {
  id: number;
  role: "user" | "agent";
  text: string;
  final: boolean;
}

interface ConversationState {
  // connection / voice
  connection: ConnectionState;
  errorMessage: string | null;
  agentState: AgentState;
  micMuted: boolean;
  agentAudioLevel: number;
  micLevel: number;
  agentVideoStream: MediaStream | null; // Tavus avatar video track, when enabled

  // transcript
  transcript: TranscriptTurn[];

  // LIVE data
  list: LiveListItem[]; // UC1 — everything lands here first
  products: LiveProduct[];
  variants: LiveProduct[];
  cart: LiveCart | null; // curated subset moved from the list
  order: LiveOrder | null;
  offers: LiveOffer[]; // LIVE — get_offers (specials DB)

  // which list items have been moved to the cart (by product_id or name key)
  movedToCart: Record<string, boolean>;

  // MOCK panel (seeded — no live agent tool yet)
  profile: MockProfile;

  // UI
  activeTab: TabKey;
  tabActivity: Record<TabKey, boolean>;

  // actions
  setConnection: (s: ConnectionState, msg?: string) => void;
  setAgentState: (s: AgentState) => void;
  setMicMuted: (m: boolean) => void;
  setLevels: (mic: number, agent: number) => void;
  setAgentVideo: (s: MediaStream | null) => void;
  appendTranscript: (role: "user" | "agent", text: string, final: boolean) => void;
  setList: (list: LiveGroceryList) => void;
  setProducts: (p: LiveProduct[]) => void;
  setVariants: (v: LiveProduct[]) => void;
  setCart: (c: LiveCart) => void;
  setOrder: (o: LiveOrder) => void;
  setOffers: (o: LiveOffer[]) => void;
  moveItemToCart: (item: LiveListItem) => void;
  setActiveTab: (t: TabKey) => void;
  clearTabActivity: (t: TabKey) => void;
  reset: () => void;
}

let turnCounter = 0;

const noActivity: Record<TabKey, boolean> = {
  list: false,
  cart: false,
  products: false,
  recipes: false,
  offers: false,
  profile: false,
};

/**
 * Display label for a list item. The live tool may return name=null (raw_text
 * not yet resolved to a product), so fall back to raw_text, then a placeholder.
 */
export function itemLabel(i: {
  name?: string | null;
  raw_text?: string | null;
}): string {
  return (i.name ?? i.raw_text ?? "item").toString();
}

/** Stable key for matching a list item against the cart (null-safe). */
export function itemKey(i: {
  product_id?: string | null;
  name?: string | null;
  raw_text?: string | null;
}): string {
  return i.product_id ?? `name:${itemLabel(i).toLowerCase()}`;
}

/** Coerce the live tool's qty (number or string "1") to a number. */
export function itemQty(i: { qty?: number | string }): number {
  const n = typeof i.qty === "string" ? parseFloat(i.qty) : i.qty;
  return Number.isFinite(n) && (n as number) > 0 ? (n as number) : 1;
}

export const useConversation = create<ConversationState>((set, get) => ({
  connection: "idle",
  errorMessage: null,
  agentState: "idle",
  micMuted: false,
  agentAudioLevel: 0,
  micLevel: 0,
  agentVideoStream: null,

  transcript: [],

  list: [],
  products: [],
  variants: [],
  cart: null,
  order: null,
  offers: [],
  movedToCart: {},

  profile: SEED.profile,

  activeTab: "list", // the list is the primary panel
  tabActivity: { ...noActivity },

  setConnection: (s, msg) =>
    set({ connection: s, errorMessage: s === "error" ? msg ?? "error" : null }),
  setAgentState: (s) => set({ agentState: s }),
  setMicMuted: (m) => set({ micMuted: m }),
  setLevels: (mic, agent) => set({ micLevel: mic, agentAudioLevel: agent }),
  setAgentVideo: (s) => set({ agentVideoStream: s }),

  appendTranscript: (role, text, final) => {
    const turns = get().transcript;
    const last = turns[turns.length - 1];
    if (last && last.role === role && !last.final) {
      const updated = [...turns];
      updated[updated.length - 1] = { ...last, text, final };
      set({ transcript: updated });
    } else {
      set({
        transcript: [...turns, { id: ++turnCounter, role, text, final }].slice(-50),
      });
    }
  },

  // Everything the shopper adds by voice lands on the LIST first.
  setList: (list) =>
    set((st) => ({
      list: (list.items ?? []).filter((i) => i.status !== "removed"),
      activeTab: "list",
      tabActivity: { ...st.tabActivity, list: true },
    })),

  setProducts: (p) =>
    set((st) => ({
      products: p,
      activeTab: "products",
      tabActivity: { ...st.tabActivity, products: true },
    })),
  setVariants: (v) =>
    set((st) => ({
      variants: v,
      activeTab: "products",
      tabActivity: { ...st.tabActivity, products: true },
    })),

  setCart: (c) =>
    set((st) => {
      // Keep the moved-to-cart marks in sync with the authoritative cart.
      const moved: Record<string, boolean> = {};
      for (const it of c.items ?? []) moved[itemKey(it)] = true;
      return {
        cart: c,
        movedToCart: moved,
        activeTab: "cart",
        tabActivity: { ...st.tabActivity, cart: true },
      };
    }),
  setOrder: (o) =>
    set((st) => ({
      order: o,
      activeTab: "cart",
      tabActivity: { ...st.tabActivity, cart: true },
    })),

  // LIVE — get_offers (specials DB). "What's on special?" surfaces the Offers tab.
  setOffers: (o) =>
    set((st) => ({
      offers: o,
      activeTab: "offers",
      tabActivity: { ...st.tabActivity, offers: true },
    })),

  // Human curation: move one list item into the cart (client-side until the
  // agent/tool path confirms it). Optimistic — reconciled when a get_cart /
  // add_to_cart tool_result arrives.
  moveItemToCart: (item) =>
    set((st) => {
      const key = itemKey(item);
      if (st.movedToCart[key]) return st;
      const newItem: LiveCartItem = {
        product_id: item.product_id ?? key,
        name: itemLabel(item),
        qty: itemQty(item),
        price_cents: 0, // unknown until the cart tool prices it
      };
      const items = [...(st.cart?.items ?? []), newItem];
      const subtotal = items.reduce((s, i) => s + i.price_cents * i.qty, 0);
      return {
        cart: {
          ...(st.cart ?? {}),
          items,
          subtotal_cents: subtotal,
        },
        movedToCart: { ...st.movedToCart, [key]: true },
        tabActivity: { ...st.tabActivity, cart: true },
      };
    }),

  setActiveTab: (t) =>
    set((st) => ({ activeTab: t, tabActivity: { ...st.tabActivity, [t]: false } })),
  clearTabActivity: (t) =>
    set((st) => ({ tabActivity: { ...st.tabActivity, [t]: false } })),

  reset: () =>
    set({
      connection: "idle",
      errorMessage: null,
      agentState: "idle",
      micMuted: false,
      agentAudioLevel: 0,
      micLevel: 0,
      agentVideoStream: null,
      transcript: [],
      list: [],
      products: [],
      variants: [],
      cart: null,
      order: null,
      offers: [],
      movedToCart: {},
      activeTab: "list",
      tabActivity: { ...noActivity },
    }),
}));
