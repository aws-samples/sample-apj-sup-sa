"""Synthetic retail catalogs and orders for the mock retail API tools.

All data here is entirely fabricated for this demo -- no customer data.
Each tenant (retail brand) has its own small, isolated dataset.
"""
from __future__ import annotations

CATALOGS: dict[str, dict] = {
    "northwind": {
        "brand": "Northwind Apparel Co.",
        "inventory": {
            "NW-JKT-001": {"name": "Northwind Trail Jacket", "category": "jacket", "price_usd": 129, "size_run": ["S", "M", "L", "XL"], "stock": 42},
            "NW-TEE-014": {"name": "Northwind Logo Tee", "category": "top", "price_usd": 24, "size_run": ["S", "M", "L", "XL", "XXL"], "stock": 310},
            "NW-BOOT-007": {"name": "Northwind Hiking Boot", "category": "footwear", "price_usd": 149, "size_run": ["8", "9", "10", "11"], "stock": 0},
        },
        "orders": {
            "NW-1001": {"status": "shipped", "items": ["NW-JKT-001"], "eta_days": 2},
            "NW-1002": {"status": "processing", "items": ["NW-TEE-014", "NW-TEE-014"], "eta_days": 5},
        },
    },
    "borealis": {
        "brand": "Borealis Outfitters",
        "inventory": {
            "BO-PARKA-009": {"name": "Borealis Aurora Parka", "category": "jacket", "price_usd": 349, "size_run": ["M", "L", "XL"], "stock": 18},
            "BO-JKT-014": {"name": "Borealis Ridge Shell Jacket (waterproof)", "category": "jacket", "price_usd": 219, "size_run": ["S", "M", "L", "XL"], "stock": 34},
            "BO-JKT-022": {"name": "Borealis Summit Down Jacket", "category": "jacket", "price_usd": 189, "size_run": ["M", "L"], "stock": 12},
            "BO-GLOVE-003": {"name": "Borealis Thermal Glove", "category": "accessory", "price_usd": 39, "size_run": ["S", "M", "L"], "stock": 220},
            "BO-PACK-021": {"name": "Borealis 40L Pack", "category": "bag", "price_usd": 129, "size_run": ["OneSize"], "stock": 65},
        },
        "orders": {
            "BO-2001": {"status": "delivered", "items": ["BO-PARKA-009"], "eta_days": 0},
            "BO-2002": {"status": "backordered", "items": ["BO-PACK-021"], "eta_days": 14},
        },
    },
    "cascade": {
        "brand": "Cascade Mart",
        "inventory": {
            "CM-COFFEE-100": {"name": "Cascade Dark Roast 1lb", "category": "grocery", "price_usd": 12, "size_run": ["OneSize"], "stock": 540},
            "CM-MUG-012": {"name": "Cascade Travel Mug", "category": "kitchen", "price_usd": 18, "size_run": ["OneSize"], "stock": 88},
            "CM-SNACK-033": {"name": "Cascade Trail Mix 6pk", "category": "grocery", "price_usd": 15, "size_run": ["OneSize"], "stock": 3},
        },
        "orders": {
            "CM-3001": {"status": "shipped", "items": ["CM-COFFEE-100", "CM-MUG-012"], "eta_days": 1},
        },
    },
}


def lookup_order(tenant_id: str, order_id: str) -> dict:
    catalog = CATALOGS.get(tenant_id, {})
    order = catalog.get("orders", {}).get(order_id)
    if not order:
        return {"found": False, "order_id": order_id}
    return {"found": True, "order_id": order_id, **order}


def check_inventory(tenant_id: str, sku: str) -> dict:
    catalog = CATALOGS.get(tenant_id, {})
    item = catalog.get("inventory", {}).get(sku)
    if not item:
        return {"found": False, "sku": sku}
    return {"found": True, "sku": sku, **item}


def search_products(tenant_id: str, query: str) -> dict:
    """Simple keyword search over this tenant's product catalog (synthetic
    data). Used by the 'search_products' shopping-assistant tool for
    product discovery."""
    catalog = CATALOGS.get(tenant_id, {})
    inventory = catalog.get("inventory", {})
    q = (query or "").lower().strip()
    # Naive singular forms so "jackets" / "gloves" match "jacket" / "glove".
    words = set()
    for word in q.split():
        words.add(word)
        if len(word) > 3 and word.endswith("s"):
            words.add(word[:-1])
    results = []
    for sku, item in inventory.items():
        haystack = f"{sku} {item.get('name', '')} {item.get('category', '')}".lower()
        if not q or q in haystack or any(word in haystack for word in words):
            results.append({"sku": sku, **item})
    return {"query": query, "results": results, "count": len(results)}
