#!/usr/bin/env python3
"""Scrape ONLY current specials from woolworths.com.au into specials.json.

Unlike scrape_woolworths.py (which searches all products with IsSpecial:False and
captures specials incidentally — ~282), this queries the search API with
**IsSpecial:True** per term, so nearly every result is on special. Far more
specials per call (e.g. "pasta": 0 incidental -> 19 on-special).

Dedup is by Woolworths Stockcode -> the SAME deterministic product_id
(uuid5(NS, stockcode)) the main scraper uses, so a special links to its product
row by product_id. The matching `special_id` is uuid5(NS, "special-<stockcode>").

IMPORTANT: this writes specials.json only. It does NOT touch products.json. The
loader (load_specials_only.py / the live refresh) inserts ONLY specials whose
product_id already exists in the catalogue — specials for products not in our
catalogue are skipped (reported), so products/embeddings/category fixes are
untouched.

Re-runnable; network-dependent (prices/specials reflect the moment it ran).
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

# Same namespace as scrape_woolworths.py so product_id/special_id line up.
NS = uuid.UUID("a15e0000-0000-4000-8000-000000000001")

# Broadened term list (promo-heavy aisles + staples) to widen specials coverage.
TERMS = [
    "milk", "cheese", "yoghurt", "butter", "eggs", "cream",
    "bread", "wraps", "bakery",
    "pasta", "rice", "noodles", "cereal", "muesli", "oats",
    "chicken", "beef", "mince", "bacon", "sausages", "ham", "pork", "lamb",
    "salmon", "tuna", "seafood",
    "apples", "bananas", "tomatoes", "potatoes", "salad", "vegetables", "fruit",
    "chocolate", "biscuits", "chips", "crackers", "nuts", "lollies", "snacks",
    "ice cream", "frozen", "pizza", "frozen vegetables",
    "coffee", "tea", "juice", "soft drink", "cola", "lemonade", "water",
    "energy drink", "kombucha", "sparkling water",
    "olive oil", "pasta sauce", "canned tomatoes", "baked beans", "soup",
    "mayonnaise", "sauce", "spices", "honey", "jam", "peanut butter",
    "toilet paper", "paper towel", "dishwashing", "laundry", "cleaning",
    "shampoo", "conditioner", "body wash", "toothpaste", "deodorant", "soap",
    "vitamins", "sunscreen",
    "nappies", "baby food", "dog food", "cat food",
    "chocolate block", "confectionery", "drinks",
]
PAGE_SIZE = 36       # IsSpecial:True returns mostly specials; grab a deep page
REQUEST_DELAY = 0.35


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    s.get(BASE + "/", headers={"Accept": "text/html"}, timeout=30)  # prime anti-bot cookies
    return s


def search_specials(s: requests.Session, term: str) -> list[dict]:
    body = {
        "SearchTerm": term,
        "PageSize": PAGE_SIZE,
        "PageNumber": 1,
        "SortType": "TraderRelevance",
        "Location": "/shop/search/products",
        "IsSpecial": True,   # <-- the lever: ask for on-special products
    }
    r = s.post(
        SEARCH_URL, json=body,
        headers={"Content-Type": "application/json", "Origin": BASE,
                 "Referer": f"{BASE}/shop/search/products?searchTerm={quote(term)}"},
        timeout=30,
    )
    r.raise_for_status()
    out = []
    for group in r.json().get("Products") or []:
        for p in group.get("Products") or []:
            if p.get("Stockcode"):
                out.append(p)
    return out


def _special_from(p: dict) -> dict | None:
    stockcode = p["Stockcode"]
    price = p.get("Price") or 0.0
    was = p.get("WasPrice") or price
    # Only keep genuine discounts (IsSpecial:True can include member/promo rows).
    if not (bool(p.get("IsOnSpecial")) or (was and price and was > price)):
        return None
    price_cents = round(price * 100)
    was_cents = round(was * 100)
    if was_cents <= price_cents:
        return None
    if p.get("IsHalfPrice"):
        stype = "half_price"
    elif p.get("IsEdrSpecial"):
        stype = "member_price"
    else:
        stype = "special"
    return {
        "special_id": str(uuid.uuid5(NS, f"special-{stockcode}")),
        "product_id": str(uuid.uuid5(NS, str(stockcode))),
        "special_price_cents": price_cents,
        "was_price_cents": was_cents,
        "savings_cents": max(0, was_cents - price_cents),
        "special_type": stype,
    }


def main() -> None:
    s = make_session()
    by_product: dict[str, dict] = {}  # product_id -> special (dedup across terms)
    print(f"Scraping specials (IsSpecial:True) across {len(TERMS)} terms...")
    for term in TERMS:
        try:
            hits = search_specials(s, term)
            kept = 0
            for p in hits:
                sp = _special_from(p)
                if sp:
                    by_product.setdefault(sp["product_id"], sp)  # dedup by product
                    kept += 1
            print(f"  {term:<18} +{kept:>2} on-special  (unique total: {len(by_product)})")
        except Exception as e:  # noqa: BLE001
            print(f"  ! '{term}' failed: {e}")
        time.sleep(REQUEST_DELAY)

    specials = list(by_product.values())
    (OUT_DIR / "specials.json").write_text(json.dumps(specials, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(specials)} unique specials to {OUT_DIR/'specials.json'}")


if __name__ == "__main__":
    main()
