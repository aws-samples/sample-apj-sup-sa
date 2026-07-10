# `search_products` — example calls for the runtime / agent prompt

Reference for whoever implements the AgentCore Runtime + system prompt. It maps
what a shopper **says** → the `search_products` tool call the agent should make.

The design goal: **the LLM does the natural-language → params translation.** The
runtime implementer's job is to (a) ensure the tool schema exposes these params
(already done in the Gateway target) and (b) write a system prompt that pushes
the model to *use the filters* and to *expand vague/conceptual queries into
concrete product words* before searching.

> The §1–§8 examples below were run against the live catalog and each returns
> results. They are catalog-dependent — if the inventory is reseeded, re-verify.

## Tool surface

`query` (**required**, free text) plus optional:

| Param | Type | Notes |
|---|---|---|
| `category` | string | substring match, e.g. `"dairy"`, `"bakery"` |
| `limit` | int | 1–50, default 10 |
| `dietary_tags` | string[] | product must have **ALL**. Vocab: `vegetarian, vegan, gluten_free, low_sugar, low_salt, high_protein, halal, organic, kosher` |
| `exclude_allergens` | string[] | product must have **NONE**. Vocab: `milk, gluten, wheat, soy, fish, sulphites, egg, peanut, sesame, shellfish` |
| `min_price_cents` / `max_price_cents` | int | **effective** price (special price if on special) |
| `quality_tier` | string | `value` \| `standard` \| `premium` |
| `in_stock_only` | bool | hide out-of-stock |
| `on_special_only` | bool | only items currently on special |
| `sort` | string | `relevance` (default) \| `price_asc` \| `price_desc` \| `savings_desc` |
| `mode` | string | `auto` (default) \| `lexical` \| `semantic`. See §9. |

## Examples by capability

### 1. Plain find / multi-word
- "Do you have Tim Tams?" → `{"query":"tim tam"}`
- "I'm looking for full cream milk" → `{"query":"full cream milk"}`
- "Find me dairy milk chocolate" → `{"query":"dairy milk chocolate"}`

  Words are matched independently against product name + brand, so order and
  extra words don't break it.

### 2. Dietary needs (`dietary_tags` = must have ALL)
- "What gluten-free pasta do you have?" → `{"query":"pasta","dietary_tags":["gluten_free"]}`
- "Halal bread options?" → `{"query":"bread","dietary_tags":["halal"]}`

### 3. Allergen avoidance (`exclude_allergens` = must have NONE)
- "Bread, but I'm allergic to milk" → `{"query":"bread","exclude_allergens":["milk"]}`
- "Biscuits with no nuts or sesame" → `{"query":"biscuits","exclude_allergens":["peanut","sesame"]}`

### 4. Price (effective price — respects specials)
- "Cheese under $5" → `{"query":"cheese","max_price_cents":500,"sort":"price_asc"}`
- "What's the cheapest milk?" → `{"query":"milk","sort":"price_asc"}`
- "Pasta sauce between $2 and $4" → `{"query":"pasta sauce","min_price_cents":200,"max_price_cents":400}`

### 5. Specials / savings
- "What chocolate is on special?" → `{"query":"chocolate","on_special_only":true}`
- "Best deals on Coke right now" → `{"query":"cola","on_special_only":true,"sort":"savings_desc"}`

  Note: `query` matches product **name/brand**, not category — say the product
  ("cola"), not the category ("drinks").

### 6. Quality tier (`value | standard | premium`)
- "Cheapest home-brand rice" → `{"query":"rice","quality_tier":"value","sort":"price_asc"}`
- "Cheapest olive oil" → `{"query":"olive oil","quality_tier":"value","sort":"price_asc"}`

### 7. Category narrowing + stock
- "Yoghurt in the dairy section" → `{"query":"yoghurt","category":"dairy"}`
- "In-stock pasta" → `{"query":"pasta","in_stock_only":true}`

### 8. Combined (the payoff — one call answers a rich question)
- "Cheapest gluten-free pasta under $5 with no milk"
  → `{"query":"pasta","dietary_tags":["gluten_free"],"exclude_allergens":["milk"],"max_price_cents":500,"sort":"price_asc"}`
- "Chocolate that's on special, biggest saving first"
  → `{"query":"chocolate","on_special_only":true,"sort":"savings_desc"}`

### 9. Vague / conceptual — semantic search (just pass the words)
The tool embeds the query (Bedrock Cohere Embed v3) and matches by *meaning*, so
requests whose words don't appear in any product name still resolve. `mode=auto`
(default) tries keywords first and falls back to semantic automatically — the
agent does **not** need to expand concepts itself.
- "Something fizzy to drink" → `{"query":"something fizzy to drink"}`
  (returns sparkling waters, sodas, energy drinks)
- "Taco night" → `{"query":"taco night"}` (tortillas, taco kits, salsa)
- "Healthy breakfast" → `{"query":"healthy breakfast"}` (mueslis, protein cereals)
- Force it if needed: `{"query":"something to settle my stomach","mode":"semantic"}`
- Semantic composes with filters: `{"query":"healthy breakfast","dietary_tags":["vegan"],"mode":"semantic"}`

The response includes `search_mode` (`lexical` | `semantic`) so you can see which
path answered.

## Three things the runtime/prompt MUST get right

1. **Compose filters into ONE call — don't fetch-then-filter.** A weak prompt
   makes the model call `{"query":"pasta"}` then reason over the list for
   "gluten-free under $5". The right behavior is a single call with the filters
   set. Put an explicit instruction + a couple of the §8 examples in the system
   prompt.

2. **Just pass the shopper's words — semantic search handles vague queries.**
   With `mode=auto` (default) the tool falls back to meaning-based search when
   keywords miss, so "something fizzy" / "taco night" / "healthy breakfast" work
   without the model pre-translating them into product terms. (Pure-keyword
   behavior is still available via `mode=lexical` if ever needed.) Precise
   lookups like "tim tam" stay on the fast keyword path automatically.

3. **Results are answer-complete.** Each returned `Product` carries
   `price_cents`, `allergens`, `dietary_tags`, `quality_tier`, `in_stock`, and a
   `special` object when on special. Follow-ups like "is it on special?" / "does
   it have nuts?" are answerable from the **existing result** — the model
   usually should NOT re-search to answer them.
