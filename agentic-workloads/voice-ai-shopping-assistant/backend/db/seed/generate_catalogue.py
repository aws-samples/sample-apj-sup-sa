#!/usr/bin/env python3
"""Generate a fully synthetic grocery catalogue for the Aisle demo.

This replaces the earlier site-scraping seed scripts. Nothing here touches a real
retailer: every brand, product, price, and special is invented, and images are
left null (the UI renders a clean placeholder card when image_url is absent).

Output (snake_case, mirrors backend/agent/contracts.py and seed_loader.py):
  products.json  -> [Product]   (product_id stable per (brand, name))
  specials.json  -> [Special]   (product_id FK -> products.product_id)

Deterministic: a fixed RNG seed means re-running produces the same catalogue, so
product_id / special_id linkage stays stable across runs and the committed JSON
only changes when this generator changes.

Usage: python backend/db/seed/generate_catalogue.py
Not invoked at deploy time — the committed JSON is what seed_loader.py loads.

INTEGRATING A REAL CATALOGUE: replace this generator with your own product
source (a retailer API, a product feed, your own DB export). Emit the same
products.json / specials.json shape (the `Product` fields in
backend/agent/contracts.py; see seed_loader.py for how they're loaded and
embedded for semantic search). Mind the licensing/terms of any third-party
product data or images you ingest — do not commit scraped third-party data or
hotlink a retailer's image CDN.
"""
from __future__ import annotations

import json
import random
import uuid
from pathlib import Path

OUT_DIR = Path(__file__).parent

# Stable namespace so a (brand, name) pair always maps to the same product_id
# across runs (keeps products.json <-> specials.json linkage deterministic).
NS = uuid.UUID("a15e0000-0000-4000-8000-000000000001")
RNG = random.Random(20240601)  # fixed seed -> reproducible catalogue

# Fictional brands grouped by quality tier. All invented for the demo; any
# resemblance to a real brand is coincidental.
VALUE_BRANDS = ["Aisle Value", "Everyday Basics", "Pantry Essentials", "Thrift Larder"]
STANDARD_BRANDS = [
    "Harvest Lane", "Riverbend", "Sunny Meadow", "Copper Pot", "Maple & Oat",
    "Coastline", "Green Fork", "Hearthstone", "Brookfield", "Two Rivers",
    "Golden Field", "Stillwater", "Cedar Grove", "Honest Crumb", "Vista",
]
PREMIUM_BRANDS = [
    "Maison Lutece", "Artisan Reserve", "Heritage Select", "Bellavita",
    "The Gilded Spoon", "Nordstrom Larder", "Cuvée Noir",
]

# Catalogue definition. Each category lists aisles and product lines. A product
# line expands into one product per (brand sample x variant). Tier weights bias
# which brand pool each line draws from. Price ranges are in cents.
#   line: (base_name, unit, price_lo, price_hi, [variants], allergens, dietary, n_brands)
CATALOGUE: dict[str, dict] = {
    "dairy": {
        "aisles": ["milk & cream", "cheese", "yoghurt", "butter & spreads"],
        "lines": [
            ("Full Cream Milk", "2L", 280, 420, ["", "Lactose Free", "Light"], ["milk"], ["vegetarian", "gluten_free"], 4),
            ("Greek Yoghurt", "1kg", 450, 850, ["Natural", "Vanilla", "Honey"], ["milk"], ["vegetarian", "high_protein"], 4),
            ("Block Cheese", "500g", 600, 1400, ["Tasty", "Light", "Vintage"], ["milk"], ["vegetarian", "gluten_free"], 5),
            ("Salted Butter", "250g", 350, 800, ["Salted", "Unsalted"], ["milk"], ["vegetarian", "gluten_free"], 4),
            ("Pure Cream", "300ml", 200, 420, ["Thickened", "Pouring"], ["milk"], ["vegetarian", "gluten_free"], 3),
        ],
    },
    "fruit & vegetables": {
        "aisles": ["fresh fruit", "fresh vegetables", "salads & herbs"],
        "lines": [
            ("Royal Gala Apples", "1kg", 350, 650, [""], [], ["vegan", "gluten_free"], 3),
            ("Cavendish Bananas", "1kg", 300, 500, [""], [], ["vegan", "gluten_free"], 2),
            ("Truss Tomatoes", "500g", 400, 700, [""], [], ["vegan", "gluten_free"], 2),
            ("Baby Spinach", "120g", 250, 450, ["", "Organic"], [], ["vegan", "gluten_free", "organic"], 3),
            ("Brushed Potatoes", "2kg", 350, 700, [""], [], ["vegan", "gluten_free"], 2),
            ("Avocado", "each", 100, 350, [""], [], ["vegan", "gluten_free"], 2),
            ("Carrots", "1kg", 150, 350, [""], [], ["vegan", "gluten_free"], 2),
        ],
    },
    "bakery": {
        "aisles": ["bread", "wraps & rolls", "sweet bakery"],
        "lines": [
            ("White Sandwich Loaf", "700g", 250, 500, ["White", "Wholemeal", "Multigrain"], ["wheat", "gluten"], ["vegetarian"], 4),
            ("Wraps", "8 pack", 250, 450, ["Plain", "Wholemeal"], ["wheat", "gluten"], ["vegetarian"], 3),
            ("Dinner Rolls", "6 pack", 200, 400, [""], ["wheat", "gluten"], ["vegetarian"], 3),
            ("Croissants", "4 pack", 350, 700, [""], ["wheat", "gluten", "milk"], ["vegetarian"], 3),
        ],
    },
    "rice, noodles & pasta": {
        "aisles": ["pasta", "rice", "noodles"],
        "lines": [
            ("Spaghetti", "500g", 120, 350, ["", "Wholemeal", "Gluten Free"], ["wheat", "gluten"], ["vegan"], 4),
            ("Penne", "500g", 120, 350, ["", "Gluten Free"], ["wheat", "gluten"], ["vegan"], 4),
            ("Basmati Rice", "1kg", 300, 700, [""], [], ["vegan", "gluten_free"], 3),
            ("Hokkien Noodles", "440g", 200, 400, [""], ["wheat", "gluten"], ["vegan"], 2),
        ],
    },
    "canned & packet food": {
        "aisles": ["canned vegetables", "canned fish", "soups & beans"],
        "lines": [
            ("Diced Tomatoes", "400g", 80, 250, [""], [], ["vegan", "gluten_free"], 4),
            ("Tuna in Springwater", "95g", 120, 350, ["", "Olive Oil", "Chilli"], ["fish"], ["high_protein", "gluten_free"], 4),
            ("Baked Beans", "420g", 100, 300, [""], [], ["vegan", "gluten_free"], 3),
            ("Chickpeas", "400g", 90, 250, [""], [], ["vegan", "gluten_free"], 3),
            ("Lentil Soup", "500g", 250, 550, [""], [], ["vegan", "gluten_free"], 3),
        ],
    },
    "meat": {
        "aisles": ["poultry", "beef & lamb", "pork & bacon"],
        "lines": [
            ("Chicken Breast Fillets", "500g", 600, 1100, [""], [], ["high_protein", "gluten_free"], 3),
            ("Beef Mince", "500g", 600, 1200, ["Regular", "Premium"], [], ["high_protein", "gluten_free"], 3),
            ("Pork Sausages", "500g", 450, 850, [""], ["sulphites"], ["high_protein"], 3),
            ("Streaky Bacon", "250g", 400, 800, [""], [], ["high_protein", "gluten_free"], 3),
        ],
    },
    "seafood": {
        "aisles": ["fresh seafood", "frozen seafood"],
        "lines": [
            ("Salmon Fillets", "300g", 800, 1600, [""], ["fish"], ["high_protein", "gluten_free"], 3),
            ("Raw Prawns", "500g", 900, 1800, [""], ["shellfish"], ["high_protein", "gluten_free"], 2),
            ("Crumbed Fish Fillets", "425g", 500, 900, [""], ["fish", "wheat", "gluten"], [], 2),
        ],
    },
    "frozen food": {
        "aisles": ["frozen vegetables", "frozen meals", "ice cream"],
        "lines": [
            ("Frozen Peas", "1kg", 200, 450, [""], [], ["vegan", "gluten_free"], 3),
            ("Mixed Vegetables", "1kg", 250, 500, [""], [], ["vegan", "gluten_free"], 3),
            ("Margherita Pizza", "450g", 350, 800, [""], ["wheat", "gluten", "milk"], ["vegetarian"], 3),
            ("Vanilla Ice Cream", "2L", 450, 1100, ["Vanilla", "Chocolate", "Cookies & Cream"], ["milk"], ["vegetarian"], 4),
        ],
    },
    "drinks": {
        "aisles": ["soft drinks", "juice", "water", "coffee & tea"],
        "lines": [
            ("Cola", "1.25L", 150, 400, ["Regular", "No Sugar"], [], ["vegan"], 3),
            ("Orange Juice", "2L", 350, 650, ["", "No Added Sugar"], [], ["vegan", "gluten_free"], 3),
            ("Sparkling Water", "1L", 100, 350, ["Lime", "Lemon", "Natural"], [], ["vegan", "gluten_free", "sugar_free"], 3),
            ("Ground Coffee", "1kg", 800, 2400, ["Espresso", "Decaf"], [], ["vegan", "gluten_free"], 4),
            ("English Breakfast Tea", "100 bags", 300, 700, [""], [], ["vegan", "gluten_free"], 3),
        ],
    },
    "biscuits & snacks": {
        "aisles": ["chips & crackers", "sweet biscuits", "nuts & bars"],
        "lines": [
            ("Potato Chips", "175g", 200, 500, ["Original", "Salt & Vinegar", "BBQ"], [], ["vegetarian", "gluten_free"], 4),
            ("Chocolate Biscuits", "200g", 200, 550, [""], ["wheat", "gluten", "milk", "soy"], ["vegetarian"], 4),
            ("Water Crackers", "250g", 180, 400, [""], ["wheat", "gluten"], ["vegan"], 3),
            ("Roasted Almonds", "200g", 400, 900, ["Salted", "Unsalted"], ["tree_nuts"], ["vegan", "gluten_free", "high_protein"], 3),
            ("Muesli Bars", "6 pack", 250, 600, ["", "Nut Free"], ["wheat", "gluten"], ["vegetarian"], 3),
        ],
    },
    "breakfast foods": {
        "aisles": ["cereal", "oats & muesli"],
        "lines": [
            ("Corn Flakes", "500g", 250, 600, [""], [], ["vegetarian"], 3),
            ("Rolled Oats", "1kg", 200, 500, ["", "Quick"], ["gluten"], ["vegan"], 3),
            ("Toasted Muesli", "750g", 400, 900, ["", "Gluten Free"], ["tree_nuts"], ["vegetarian"], 3),
        ],
    },
    "cooking, seasoning & gravy": {
        "aisles": ["oils & vinegar", "herbs & spices", "sauces & pastes"],
        "lines": [
            ("Extra Virgin Olive Oil", "750ml", 500, 1600, [""], [], ["vegan", "gluten_free"], 4),
            ("Sea Salt Grinder", "120g", 150, 450, [""], [], ["vegan", "gluten_free"], 3),
            ("Cracked Black Pepper", "50g", 200, 600, [""], [], ["vegan", "gluten_free"], 3),
            ("Pasta Sauce", "500g", 200, 550, ["Tomato & Basil", "Bolognese"], [], ["vegan", "gluten_free"], 4),
            ("Soy Sauce", "250ml", 200, 500, ["", "Salt Reduced"], ["soy", "wheat", "gluten"], ["vegan"], 3),
            ("Curry Paste", "200g", 250, 600, ["Red", "Green", "Korma"], [], ["vegan", "gluten_free"], 3),
        ],
    },
    "condiments": {
        "aisles": ["table sauces", "spreads"],
        "lines": [
            ("Tomato Ketchup", "500ml", 200, 500, [""], [], ["vegan", "gluten_free"], 3),
            ("Whole Egg Mayonnaise", "440g", 250, 600, ["", "Light"], ["egg"], ["vegetarian", "gluten_free"], 3),
            ("Dijon Mustard", "200g", 200, 500, [""], [], ["vegan", "gluten_free"], 3),
        ],
    },
    "jams & spreads": {
        "aisles": ["jams & honey", "nut spreads"],
        "lines": [
            ("Strawberry Jam", "500g", 200, 550, [""], [], ["vegan", "gluten_free"], 3),
            ("Pure Honey", "500g", 400, 1100, [""], [], ["vegetarian", "gluten_free"], 3),
            ("Peanut Butter", "375g", 250, 700, ["Smooth", "Crunchy"], ["peanut"], ["vegan", "high_protein"], 3),
        ],
    },
    "international food": {
        "aisles": ["asian", "mexican", "mediterranean"],
        "lines": [
            ("Coconut Milk", "400ml", 120, 350, [""], [], ["vegan", "gluten_free"], 3),
            ("Taco Kit", "320g", 350, 700, [""], ["wheat", "gluten"], ["vegetarian"], 2),
            ("Hummus", "200g", 300, 600, ["Classic", "Beetroot"], ["sesame"], ["vegan", "gluten_free"], 3),
            ("Rice Paper", "200g", 200, 450, [""], [], ["vegan", "gluten_free"], 2),
        ],
    },
    "health foods": {
        "aisles": ["plant based", "wholefoods"],
        "lines": [
            ("Tofu", "300g", 250, 550, ["Firm", "Silken"], ["soy"], ["vegan", "gluten_free", "high_protein"], 3),
            ("Almond Milk", "1L", 200, 500, ["Unsweetened", "Vanilla"], ["tree_nuts"], ["vegan", "gluten_free", "dairy_free"], 3),
            ("Chia Seeds", "200g", 300, 700, [""], [], ["vegan", "gluten_free"], 3),
            ("Protein Powder", "1kg", 2500, 6500, ["Vanilla", "Chocolate"], ["milk"], ["high_protein", "gluten_free"], 3),
        ],
    },
    "household cleaning": {
        "aisles": ["laundry", "surface & dishwashing"],
        "lines": [
            ("Laundry Powder", "2kg", 600, 1800, ["", "Sensitive"], [], [], 3),
            ("Dishwashing Liquid", "900ml", 250, 700, ["Lemon", "Original"], [], [], 3),
            ("Surface Spray", "500ml", 300, 800, [""], [], [], 3),
        ],
    },
    "papergoods, wraps & bags": {
        "aisles": ["paper goods", "wraps & bags"],
        "lines": [
            ("Toilet Paper", "12 pack", 600, 1400, ["", "3 Ply"], [], [], 3),
            ("Paper Towel", "4 pack", 350, 800, [""], [], [], 3),
            ("Cling Wrap", "60m", 200, 500, [""], [], [], 2),
        ],
    },
    "toiletries": {
        "aisles": ["hair care", "oral care", "body care"],
        "lines": [
            ("Shampoo", "400ml", 300, 1200, ["Daily", "Volumising", "Repair"], [], [], 4),
            ("Toothpaste", "110g", 200, 700, ["Whitening", "Sensitive"], [], [], 3),
            ("Body Wash", "1L", 350, 900, ["Fresh", "Moisturising"], [], [], 3),
        ],
    },
    "baby": {
        "aisles": ["nappies", "baby food"],
        "lines": [
            ("Nappies", "44 pack", 1200, 2600, ["Crawler", "Walker", "Newborn"], [], [], 2),
            ("Baby Food Pouch", "120g", 100, 300, ["Apple", "Pumpkin", "Pear"], [], ["vegan", "gluten_free"], 3),
        ],
    },
    "pet care": {
        "aisles": ["dog", "cat"],
        "lines": [
            ("Dry Dog Food", "3kg", 1200, 3500, ["Beef", "Chicken"], [], [], 3),
            ("Cat Food Cans", "12 pack", 800, 1800, ["Tuna", "Chicken"], ["fish"], [], 3),
        ],
    },
}


def _brands_for_line(n: int) -> list[str]:
    """Sample brands across tiers for a product line, value/standard heavy."""
    pool = (
        RNG.sample(VALUE_BRANDS, k=min(1, len(VALUE_BRANDS)))
        + RNG.sample(STANDARD_BRANDS, k=min(max(n - 2, 1), len(STANDARD_BRANDS)))
        + RNG.sample(PREMIUM_BRANDS, k=1)
    )
    RNG.shuffle(pool)
    return pool[:n]


def _tier(brand: str) -> str:
    if brand in VALUE_BRANDS:
        return "value"
    if brand in PREMIUM_BRANDS:
        return "premium"
    return "standard"


def _price(lo: int, hi: int, tier: str) -> int:
    base = RNG.randint(lo, hi)
    if tier == "value":
        base = int(base * 0.85)
    elif tier == "premium":
        base = int(base * 1.25)
    # Round to a believable .x5 / .x9 ending.
    return max(50, (base // 10) * 10 + RNG.choice([5, 9]))


def product_id_for(brand: str, name: str) -> str:
    return str(uuid.uuid5(NS, f"{brand}|{name}"))


def build() -> tuple[list[dict], list[dict]]:
    products: list[dict] = []
    for category, spec in CATALOGUE.items():
        aisles = spec["aisles"]
        for (base, unit, lo, hi, variants, allergens, dietary, n_brands) in spec["lines"]:
            for brand in _brands_for_line(n_brands):
                tier = _tier(brand)
                for variant in variants:
                    name = f"{brand} {base}".strip()
                    if variant:
                        name = f"{name} {variant}"
                    aisle = RNG.choice(aisles)
                    products.append({
                        "product_id": product_id_for(brand, name),
                        "name": name,
                        "brand": brand,
                        "category": category,
                        "aisle": aisle,
                        "price_cents": _price(lo, hi, tier),
                        "unit": unit,
                        "allergens": list(allergens),
                        "dietary_tags": list(dietary),
                        "quality_tier": tier,
                        "in_stock": RNG.random() > 0.04,  # ~4% out of stock
                        "image_url": None,  # synthetic: no hotlinked images
                    })

    # Dedup defensively on product_id (same brand+name could recur).
    seen: dict[str, dict] = {}
    for p in products:
        seen.setdefault(p["product_id"], p)
    products = list(seen.values())

    # Specials: discount ~18% of in-stock products.
    specials: list[dict] = []
    for p in products:
        if not p["in_stock"] or RNG.random() > 0.18:
            continue
        was = p["price_cents"]
        discount = RNG.choice([0.10, 0.15, 0.20, 0.25, 0.33, 0.50])
        special_price = max(50, int(was * (1 - discount) // 10 * 10) + 5)
        if special_price >= was:
            continue
        if discount >= 0.5:
            stype = "half_price"
        elif RNG.random() > 0.6:
            stype = "member_price"
        else:
            stype = "special"
        specials.append({
            "special_id": str(uuid.uuid5(NS, f"special-{p['product_id']}")),
            "product_id": p["product_id"],
            "special_price_cents": special_price,
            "was_price_cents": was,
            "savings_cents": was - special_price,
            "special_type": stype,
        })

    return products, specials


def main() -> None:
    products, specials = build()
    (OUT_DIR / "products.json").write_text(
        json.dumps(products, indent=2, ensure_ascii=False))
    (OUT_DIR / "specials.json").write_text(
        json.dumps(specials, indent=2, ensure_ascii=False))
    print(f"Wrote {len(products)} synthetic products, {len(specials)} specials to {OUT_DIR}")


if __name__ == "__main__":
    main()
