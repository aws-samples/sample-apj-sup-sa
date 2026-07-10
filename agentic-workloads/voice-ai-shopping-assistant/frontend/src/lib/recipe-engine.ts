/**
 * Client-side recipe suggestion. There is no deployed recipe tool/table, so the
 * frontend derives meal ideas from what the shopper is actually engaging with:
 * the items on their grocery LIST plus recently SEARCHED products. As they add
 * things by voice, the Recipes tab updates live.
 *
 * This is a lightweight keyword matcher over a small recipe knowledge base — not
 * an LLM call. If a real suggest_recipes tool ships later, swap this for a
 * tool_result in the event-adapter and the panel is unchanged.
 */

export interface SuggestedRecipe {
  name: string;
  servings: number;
  tags: string[];
  /** which of the user's items this recipe uses (drives the "uses your … " hint) */
  matched: string[];
  ingredients: string[];
}

interface RecipeDef {
  name: string;
  servings: number;
  tags: string[];
  /** lowercased ingredient keywords; a hit on any counts as a match */
  keys: string[];
  ingredients: string[];
}

// Compact knowledge base of everyday recipes keyed by common grocery staples.
const RECIPES: RecipeDef[] = [
  {
    name: "Spaghetti aglio e olio",
    servings: 4,
    tags: ["vegetarian", "20 min"],
    keys: ["pasta", "spaghetti", "garlic", "olive oil", "chilli", "parmesan"],
    ingredients: ["spaghetti", "garlic", "olive oil", "chilli flakes", "parsley", "parmesan"],
  },
  {
    name: "Spinach & ricotta pasta",
    servings: 4,
    tags: ["vegetarian", "30 min"],
    keys: ["pasta", "spaghetti", "spinach", "ricotta", "cheese"],
    ingredients: ["pasta", "baby spinach", "ricotta", "garlic", "olive oil"],
  },
  {
    name: "Veggie frittata",
    servings: 4,
    tags: ["vegetarian", "gluten free"],
    keys: ["egg", "eggs", "spinach", "cheese", "cheddar", "onion", "potato"],
    ingredients: ["eggs", "baby spinach", "cheddar", "onion"],
  },
  {
    name: "Scrambled eggs on toast",
    servings: 2,
    tags: ["vegetarian", "10 min"],
    keys: ["egg", "eggs", "bread", "butter", "milk"],
    ingredients: ["eggs", "bread", "butter", "milk"],
  },
  {
    name: "Banana oat smoothie",
    servings: 2,
    tags: ["vegetarian", "5 min", "uses up milk"],
    keys: ["banana", "milk", "oat", "oats", "yoghurt", "honey"],
    ingredients: ["banana", "milk", "rolled oats", "honey"],
  },
  {
    name: "Chicken & veg stir-fry",
    servings: 4,
    tags: ["30 min", "high protein"],
    keys: ["chicken", "rice", "broccoli", "capsicum", "carrot", "soy", "noodle", "noodles"],
    ingredients: ["chicken", "mixed vegetables", "soy sauce", "rice"],
  },
  {
    name: "Tomato & basil soup",
    servings: 4,
    tags: ["vegetarian", "gluten free"],
    keys: ["tomato", "tomatoes", "basil", "onion", "garlic", "bread"],
    ingredients: ["tomatoes", "onion", "garlic", "basil", "crusty bread"],
  },
  {
    name: "Greek salad",
    servings: 4,
    tags: ["vegetarian", "no cook"],
    keys: ["cucumber", "tomato", "tomatoes", "feta", "olive", "olives", "onion"],
    ingredients: ["cucumber", "tomato", "feta", "olives", "red onion", "olive oil"],
  },
  {
    name: "Beef tacos",
    servings: 4,
    tags: ["30 min"],
    keys: ["beef", "mince", "tortilla", "cheese", "tomato", "lettuce", "avocado", "capsicum"],
    ingredients: ["beef mince", "tortillas", "cheese", "tomato", "lettuce"],
  },
  {
    name: "Overnight oats",
    servings: 2,
    tags: ["vegetarian", "no cook", "uses up milk"],
    keys: ["oat", "oats", "milk", "yoghurt", "banana", "berry", "berries", "honey"],
    ingredients: ["rolled oats", "milk", "yoghurt", "berries", "honey"],
  },
  {
    name: "Cheese & veg toastie",
    servings: 1,
    tags: ["vegetarian", "10 min"],
    keys: ["bread", "cheese", "cheddar", "tomato", "butter", "ham", "spinach"],
    ingredients: ["bread", "cheese", "tomato", "butter"],
  },
  {
    name: "Fish & salad",
    servings: 2,
    tags: ["gluten free", "high protein"],
    keys: ["fish", "salmon", "lemon", "salad", "cucumber", "tomato", "potato"],
    ingredients: ["fish fillets", "lemon", "salad greens", "olive oil"],
  },
];

/** Normalise a product/list name into matchable tokens (null-safe). */
function tokens(name: string | null | undefined): string[] {
  if (!name) return [];
  return String(name).toLowerCase().replace(/[^a-z\s]/g, " ").split(/\s+/).filter(Boolean);
}

/**
 * Suggest recipes from the user's current items (list names + searched product
 * names). Recipes are ranked by how many distinct user items they use.
 */
export function suggestRecipes(itemNames: string[], limit = 4): SuggestedRecipe[] {
  if (itemNames.length === 0) return [];

  // Build a set of tokens present across the user's items.
  const userTokens = new Set<string>();
  const tokenToName = new Map<string, string>();
  for (const n of itemNames) {
    for (const t of tokens(n)) {
      userTokens.add(t);
      if (!tokenToName.has(t)) tokenToName.set(t, n);
    }
  }

  const scored = RECIPES.map((r) => {
    const matchedNames = new Set<string>();
    for (const key of r.keys) {
      // a recipe key matches if any of its words appears in the user's tokens
      for (const kw of key.split(/\s+/)) {
        if (userTokens.has(kw)) {
          matchedNames.add(tokenToName.get(kw) ?? kw);
        }
      }
    }
    return { r, matched: [...matchedNames] };
  })
    .filter((x) => x.matched.length > 0)
    .sort((a, b) => b.matched.length - a.matched.length);

  return scored.slice(0, limit).map(({ r, matched }) => ({
    name: r.name,
    servings: r.servings,
    tags: r.tags,
    matched,
    ingredients: r.ingredients,
  }));
}
