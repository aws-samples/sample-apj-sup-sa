/**
 * Seeded data for the one panel the deployed agent does not feed: the user
 * PROFILE (UC4). Display-only — a vision placeholder until a profile/user tool
 * exists. Marked "preview" in the UI.
 *
 * Everything else is now LIVE (DB-backed deployed tools): grocery LIST, CART,
 * PRODUCTS, OFFERS/specials (get_offers joins products ⨝ specials), ORDER.
 * Recipes are auto-derived client-side from the live list/products (no recipe
 * tool/table exists) — see lib/recipe-engine.ts.
 */

export interface MockProfile {
  display_name: string;
  dietary: string[];
  avoid_allergens: string[];
  preferred_brands: { category: string; brand: string }[];
}

export const SEED: {
  profile: MockProfile;
} = {
  profile: {
    display_name: "Demo Shopper",
    dietary: ["vegetarian"],
    avoid_allergens: ["peanuts"],
    preferred_brands: [
      { category: "milk", brand: "Harvest Lane" },
      { category: "pasta", brand: "Sunny Meadow" },
      { category: "bread", brand: "Honest Crumb" },
    ],
  },
};
