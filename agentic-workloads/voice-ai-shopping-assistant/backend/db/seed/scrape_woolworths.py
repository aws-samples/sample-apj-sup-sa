#!/usr/bin/env python3
"""Scrape a realistic grocery catalogue from woolworths.com.au into seed JSON.

Two-phase, politely rate-limited:
  Phase A  search each category term -> core fields (price, was_price, brand, size,
           on-special flags). Dedup by Woolworths Stockcode.
  Phase B  hit the product-detail endpoint per unique stockcode -> allergens,
           dietary claims, department/aisle.

Bot protection: the API rejects cold POSTs, so we first GET the homepage to pick
up anti-bot cookies, then reuse that jar (HTTP/1.1) for every call.

Output (snake_case, mirrors backend/agent/contracts.py):
  products.json  -> [Product]            (product_id stable per stockcode)
  specials.json  -> [Special]            (product_id FK -> products.product_id)

Re-runnable: `python scrape_woolworths.py`. Network-dependent; prices/specials
reflect the moment it ran. Not invoked at deploy time — output JSON is committed.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests

OUT_DIR = Path(__file__).parent
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BASE = "https://www.woolworths.com.au"
SEARCH_URL = f"{BASE}/apis/ui/Search/products"
DETAIL_URL = f"{BASE}/apis/ui/product/detail"

# Stable namespace so a stockcode always maps to the same product_id across runs
# (keeps products.json <-> specials.json linkage deterministic).
NS = uuid.UUID("a15e0000-0000-4000-8000-000000000001")

# Category search terms -> breadth across the store. PageSize per term tunes volume.
# Expanded to broaden the catalogue and fill thin spots (mexican/condiments/herbs/
# snacks/household/health/baby/pet) rather than only deepen existing categories.
CATEGORY_TERMS = [
    # dairy & fridge
    "milk", "eggs", "butter", "cheese", "yoghurt", "cream", "margarine", "tofu",
    # bakery
    "bread", "wraps", "tortilla", "bread rolls", "croissant", "bagels", "muffins",
    # pantry staples
    "pasta", "rice", "noodles", "couscous", "quinoa", "flour", "sugar", "oats",
    "cereal", "muesli bars", "honey", "peanut butter", "jam", "vegemite",
    # meat & seafood
    "chicken breast", "beef mince", "bacon", "sausages", "salmon", "pork",
    "lamb", "ham", "prawns", "fish fillets",
    # produce
    "apples", "bananas", "tomatoes", "potatoes", "carrots", "onions", "lettuce",
    "broccoli", "spinach", "avocado", "berries", "grapes", "lemons", "mushrooms",
    # mexican / international (was a gap)
    "taco", "salsa", "taco seasoning", "refried beans", "curry paste",
    "coconut milk", "soy sauce", "noodle stir fry", "sushi", "hummus",
    # canned & jarred
    "olive oil", "pasta sauce", "canned tomatoes", "baked beans", "tuna",
    "chickpeas", "lentils", "stock", "mayonnaise", "tomato sauce",
    # condiments / herbs / spices (gap)
    "herbs", "spices", "pepper", "salt", "garlic", "mustard", "vinegar", "chilli",
    # snacks & confectionery
    "chocolate", "biscuits", "chips", "crackers", "nuts", "popcorn", "lollies",
    "muesli", "protein bars",
    # frozen
    "ice cream", "frozen vegetables", "pizza", "frozen berries", "fish fingers",
    "frozen chips", "frozen meals",
    # drinks
    "soft drink", "juice", "sparkling water", "cola", "lemonade", "energy drink",
    "iced tea", "kombucha", "coffee", "tea",
    # household
    "toilet paper", "paper towel", "dishwashing liquid", "laundry powder",
    "garbage bags", "cling wrap", "surface spray", "tissues",
    # health & personal care
    "shampoo", "toothpaste", "deodorant", "body wash", "hand soap", "vitamins",
    "pain relief", "sunscreen",
    # baby & pet (gap)
    "nappies", "baby food", "dog food", "cat food",
]
PAGE_SIZE = 12  # per term; ~110 terms x 12 -> ~1300 before dedup
REQUEST_DELAY = 0.35  # politeness between calls


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    # Prime anti-bot cookies.
    s.get(BASE + "/", headers={"Accept": "text/html"}, timeout=30)
    return s


def search_term(s: requests.Session, term: str) -> list[dict]:
    body = {
        "SearchTerm": term,
        "PageSize": PAGE_SIZE,
        "PageNumber": 1,
        "SortType": "TraderRelevance",
        "Location": "/shop/search/products",
        "IsSpecial": False,
    }
    r = s.post(
        SEARCH_URL,
        json=body,
        headers={
            "Content-Type": "application/json",
            "Origin": BASE,
            "Referer": f"{BASE}/shop/search/products?searchTerm={quote(term)}",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    out = []
    for group in data.get("Products") or []:
        for p in group.get("Products") or []:
            if p.get("Stockcode"):
                out.append(p)
    return out


def fetch_detail(s: requests.Session, stockcode: int) -> dict | None:
    try:
        r = s.get(
            f"{DETAIL_URL}/{stockcode}",
            headers={"Referer": f"{BASE}/shop/productdetails/{stockcode}"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 - best-effort enrichment
        print(f"  ! detail {stockcode} failed: {e}")
        return None


# --- field mapping helpers -------------------------------------------------
_ALLERGEN_CANON = {
    "milk": "milk", "egg": "egg", "eggs": "egg", "fish": "fish", "soy": "soy",
    "soya": "soy", "wheat": "wheat", "gluten": "gluten", "peanut": "peanut",
    "peanuts": "peanut", "nut": "tree_nuts", "tree nut": "tree_nuts",
    "tree nuts": "tree_nuts", "sesame": "sesame", "shellfish": "shellfish",
    "crustacean": "shellfish", "lupin": "lupin", "sulphite": "sulphites",
    "sulphites": "sulphites",
}
_DIETARY_CANON = {
    "vegetarian": "vegetarian", "vegan": "vegan", "gluten free": "gluten_free",
    "dairy free": "dairy_free", "lactose free": "lactose_free",
    "low salt": "low_salt", "low sugar": "low_sugar", "organic": "organic",
    "halal": "halal", "kosher": "kosher", "source of protein": "high_protein",
    "high protein": "high_protein", "keto": "keto", "sugar free": "sugar_free",
}


def _canon(value: str | None, table: dict[str, str]) -> list[str]:
    if not value:
        return []
    found: list[str] = []
    for part in str(value).replace(";", ",").split(","):
        key = part.strip().lower()
        canon = table.get(key)
        if canon and canon not in found:
            found.append(canon)
    return found


def parse_allergens(attrs: dict) -> list[str]:
    out = _canon(attrs.get("allergencontains"), _ALLERGEN_CANON)
    if str(attrs.get("containsgluten")).lower() == "true" and "gluten" not in out:
        out.append("gluten")
    if str(attrs.get("containsnuts")).lower() == "true" and "tree_nuts" not in out:
        out.append("tree_nuts")
    return out


def parse_dietary(attrs: dict) -> list[str]:
    out = _canon(attrs.get("lifestyleanddietarystatement"), _DIETARY_CANON)
    # allergystatement carries "Gluten Free,Soy Free,..." -> map the *Free claims.
    for part in str(attrs.get("allergystatement") or "").split(","):
        t = part.strip().lower()
        if t == "gluten free" and "gluten_free" not in out:
            out.append("gluten_free")
    return out


# Value brands (own-brand / budget) and premium signals for quality_tier heuristic.
_VALUE_BRANDS = {"woolworths", "essentials", "homebrand", "select", "macro value", "black & gold"}
_PREMIUM_BRANDS = {"maggie beer", "barossa", "the collective", "pepe saya", "vittoria",
                   "lurpak", "president", "ferrero", "lindt", "connoisseur"}


def quality_tier(brand: str, price_cents: int, cup_per_unit: float | None) -> str:
    b = (brand or "").strip().lower()
    if any(b.startswith(v) for v in _VALUE_BRANDS):
        return "value"
    if any(p in b for p in _PREMIUM_BRANDS):
        return "premium"
    return "standard"


def product_id_for(stockcode: int) -> str:
    return str(uuid.uuid5(NS, str(stockcode)))


def main() -> None:
    s = make_session()
    raw: dict[int, dict] = {}

    print(f"Phase A: searching {len(CATEGORY_TERMS)} category terms...")
    for term in CATEGORY_TERMS:
        try:
            hits = search_term(s, term)
            for p in hits:
                raw.setdefault(p["Stockcode"], p)
            print(f"  {term:<20} +{len(hits):>2}  (total unique: {len(raw)})")
        except Exception as e:  # noqa: BLE001
            print(f"  ! search '{term}' failed: {e}")
        time.sleep(REQUEST_DELAY)

    print(f"\nPhase B: enriching {len(raw)} products with detail (allergens)...")
    products: list[dict] = []
    specials: list[dict] = []
    for i, (stockcode, p) in enumerate(raw.items(), 1):
        detail = fetch_detail(s, stockcode)
        attrs, primary = {}, {}
        if detail:
            dp = detail.get("Product") or {}
            attrs = dp.get("AdditionalAttributes") or {}
            primary = detail.get("PrimaryCategory") or {}
        time.sleep(REQUEST_DELAY)

        pid = product_id_for(stockcode)
        price = p.get("Price") or 0.0
        was = p.get("WasPrice") or price
        price_cents = round(price * 100)
        brand = (p.get("Brand") or "").strip()

        category = (primary.get("Department") or p.get("Variety") or "grocery").strip().lower()
        aisle = (primary.get("Aisle") or attrs.get("sapsubcategoryname") or category).strip().lower()

        products.append({
            "product_id": pid,
            "name": (p.get("DisplayName") or p.get("Name") or "").strip(),
            "brand": brand or "Woolworths",
            "category": category,
            "aisle": aisle,
            "price_cents": price_cents,
            "unit": (p.get("PackageSize") or p.get("Unit") or "each").strip(),
            "allergens": parse_allergens(attrs),
            "dietary_tags": parse_dietary(attrs),
            "quality_tier": quality_tier(brand, price_cents, p.get("CupPrice")),
            "in_stock": bool(p.get("IsInStock", True)),
            "image_url": p.get("MediumImageFile") or p.get("LargeImageFile"),
        })

        # Specials: anything genuinely discounted right now.
        on_special = bool(p.get("IsOnSpecial")) or (was and price and was > price)
        if on_special:
            was_cents = round(was * 100)
            if p.get("IsHalfPrice"):
                stype = "half_price"
            elif p.get("IsEdrSpecial"):
                stype = "member_price"
            else:
                stype = "special"
            specials.append({
                "special_id": str(uuid.uuid5(NS, f"special-{stockcode}")),
                "product_id": pid,
                "special_price_cents": price_cents,
                "was_price_cents": was_cents,
                "savings_cents": max(0, was_cents - price_cents),
                "special_type": stype,
            })
        if i % 25 == 0:
            print(f"  ...{i}/{len(raw)} enriched ({len(specials)} on special)")

    (OUT_DIR / "products.json").write_text(json.dumps(products, indent=2, ensure_ascii=False))
    (OUT_DIR / "specials.json").write_text(json.dumps(specials, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(products)} products, {len(specials)} specials to {OUT_DIR}")


if __name__ == "__main__":
    main()
