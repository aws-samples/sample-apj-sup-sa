"""
In-memory data store for Tasty Bites.
Manages menu, carts, orders, and profiles.
For hackathon demo — production would use DynamoDB.
"""

import time
from typing import Optional

from .config import DEMO_PHONE_NUMBER

# Menu data with images (using Unsplash for free food images)
MENU = [
    # Indian
    {"id": 1, "name": "Butter Chicken", "description": "Creamy tomato curry with tender tandoori chicken pieces", "price": 379, "category": "Indian", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1603894584373-5ac82b2ae398?w=400&h=300&fit=crop"},
    {"id": 2, "name": "Paneer Tikka Masala", "description": "Grilled cottage cheese in rich spiced tomato gravy", "price": 329, "category": "Indian", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1631452180519-c014fe946bc7?w=400&h=300&fit=crop"},
    {"id": 3, "name": "Chicken Biryani", "description": "Fragrant basmati rice layered with spiced chicken and saffron", "price": 349, "category": "Indian", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1563379091339-03b21ab4a4f8?w=400&h=300&fit=crop"},
    {"id": 4, "name": "Dal Makhani", "description": "Slow-cooked black lentils in creamy butter gravy", "price": 249, "category": "Indian", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1546833999-b9f581a1996d?w=400&h=300&fit=crop"},
    {"id": 5, "name": "Veg Biryani", "description": "Aromatic basmati rice with seasonal vegetables and whole spices", "price": 299, "category": "Indian", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1589302168068-964664d93dc0?w=400&h=300&fit=crop"},
    {"id": 6, "name": "Garlic Naan", "description": "Fresh baked naan brushed with garlic butter", "price": 79, "category": "Indian", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1599487488170-d11ec9c172f0?w=400&h=300&fit=crop"},

    # Italian
    {"id": 7, "name": "Margherita Pizza", "description": "Classic pizza with fresh mozzarella, tomato sauce, and basil", "price": 349, "category": "Italian", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1574071318508-1cdbab80d002?w=400&h=300&fit=crop"},
    {"id": 8, "name": "Pepperoni Pizza", "description": "Loaded with spicy pepperoni, mozzarella, and marinara", "price": 429, "category": "Italian", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1628840042765-356cda07504e?w=400&h=300&fit=crop"},
    {"id": 9, "name": "Pasta Alfredo", "description": "Creamy parmesan sauce with fettuccine and grilled chicken", "price": 389, "category": "Italian", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1645112411341-6c4fd023714a?w=400&h=300&fit=crop"},
    {"id": 10, "name": "Mushroom Risotto", "description": "Creamy arborio rice with wild mushrooms and parmesan", "price": 369, "category": "Italian", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1476124369491-e7addf5db371?w=400&h=300&fit=crop"},

    # Chinese
    {"id": 11, "name": "Kung Pao Chicken", "description": "Spicy stir-fried chicken with peanuts and chillies", "price": 359, "category": "Chinese", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1525755662778-989d0524087e?w=400&h=300&fit=crop"},
    {"id": 12, "name": "Veg Hakka Noodles", "description": "Wok-tossed noodles with crisp vegetables and soy sauce", "price": 259, "category": "Chinese", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1585032226651-759b368d7246?w=400&h=300&fit=crop"},
    {"id": 13, "name": "Chicken Manchurian", "description": "Crispy fried chicken in tangy Indo-Chinese gravy", "price": 329, "category": "Chinese", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1569058242567-93de6f36f8eb?w=400&h=300&fit=crop"},
    {"id": 14, "name": "Dim Sum Basket", "description": "Steamed dumplings with mixed vegetable filling", "price": 289, "category": "Chinese", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1496116218417-1a781b1c416c?w=400&h=300&fit=crop"},

    # Japanese
    {"id": 15, "name": "Salmon Sushi Roll", "description": "Fresh salmon, avocado, and cucumber maki roll (8 pcs)", "price": 499, "category": "Japanese", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1579871494447-9811cf80d66c?w=400&h=300&fit=crop"},
    {"id": 16, "name": "Chicken Ramen", "description": "Rich tonkotsu broth with noodles, egg, and chashu pork", "price": 429, "category": "Japanese", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1569718212165-3a8278d5f624?w=400&h=300&fit=crop"},
    {"id": 17, "name": "Edamame", "description": "Steamed soybeans with sea salt", "price": 179, "category": "Japanese", "dietary_flag": "vegan", "available": True, "image": "https://images.unsplash.com/photo-1564093497595-593b96d80571?w=400&h=300&fit=crop"},

    # Mexican
    {"id": 18, "name": "Chicken Burrito Bowl", "description": "Grilled chicken, rice, beans, guac, and pico de gallo", "price": 399, "category": "Mexican", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1626700051175-6818013e1d4f?w=400&h=300&fit=crop"},
    {"id": 19, "name": "Loaded Nachos", "description": "Crispy tortilla chips with cheese, jalapeños, and salsa", "price": 279, "category": "Mexican", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1513456852971-30c0b8199d4d?w=400&h=300&fit=crop"},
    {"id": 20, "name": "Vegan Tacos", "description": "Soft tortillas with black beans, corn, avocado, and lime", "price": 319, "category": "Mexican", "dietary_flag": "vegan", "available": True, "image": "https://images.unsplash.com/photo-1551504734-5ee1c4a1479b?w=400&h=300&fit=crop"},

    # Thai
    {"id": 21, "name": "Pad Thai", "description": "Stir-fried rice noodles with shrimp, tofu, and peanuts", "price": 359, "category": "Thai", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1559314809-0d155014e29e?w=400&h=300&fit=crop"},
    {"id": 22, "name": "Green Curry", "description": "Coconut milk curry with Thai basil and vegetables", "price": 339, "category": "Thai", "dietary_flag": "vegan", "available": True, "image": "https://images.unsplash.com/photo-1455619452474-d2be8b1e70cd?w=400&h=300&fit=crop"},
    {"id": 23, "name": "Tom Yum Soup", "description": "Spicy and sour soup with mushrooms and lemongrass", "price": 249, "category": "Thai", "dietary_flag": "vegan", "available": True, "image": "https://images.unsplash.com/photo-1548943487-a2e4e43b4853?w=400&h=300&fit=crop"},

    # Burgers
    {"id": 24, "name": "Classic Smash Burger", "description": "Double smashed patty with cheese, lettuce, and special sauce", "price": 349, "category": "Burgers", "dietary_flag": "non-veg", "available": True, "image": "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?w=400&h=300&fit=crop"},
    {"id": 25, "name": "Veggie Burger", "description": "Crispy black bean patty with avocado and chipotle mayo", "price": 299, "category": "Burgers", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1520072959219-c595e6cdc202?w=400&h=300&fit=crop"},

    # Desserts
    {"id": 26, "name": "Chocolate Brownie", "description": "Warm fudge brownie with vanilla ice cream and chocolate sauce", "price": 219, "category": "Desserts", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1564355808539-22fda35bed7e?w=400&h=300&fit=crop"},
    {"id": 27, "name": "Gulab Jamun", "description": "Soft milk dumplings soaked in rose-cardamom syrup (4 pcs)", "price": 149, "category": "Desserts", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1666190050267-a9152e14e352?w=400&h=300&fit=crop"},
    {"id": 28, "name": "Mango Cheesecake", "description": "Creamy New York cheesecake with fresh mango coulis", "price": 249, "category": "Desserts", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1533134242443-d4fd215305ad?w=400&h=300&fit=crop"},

    # Beverages
    {"id": 29, "name": "Mango Lassi", "description": "Creamy yogurt smoothie with Alphonso mango", "price": 129, "category": "Beverages", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1527661591475-527312dd65f5?w=400&h=300&fit=crop"},
    {"id": 30, "name": "Masala Chai", "description": "Spiced Indian tea with ginger and cardamom", "price": 79, "category": "Beverages", "dietary_flag": "veg", "available": True, "image": "https://images.unsplash.com/photo-1597318181409-cf64d0b5d8a2?w=400&h=300&fit=crop"},
]

# Per-user carts: {phone: [{"item_id": int, "quantity": int}]}
_carts: dict[str, list] = {}

# Orders: [{order_id, phone, items, total, status, created_at}]
_orders: list[dict] = []
_order_counter = 1000

# Profiles: {phone: {name, address, dietary_preference}}
_profiles: dict[str, dict] = {
    DEMO_PHONE_NUMBER: {
        "phone_number": DEMO_PHONE_NUMBER,
        "name": "Demo User",
        "address": "123 Example Street, Demo City 000000",
        "dietary_preference": "non-veg",
    }
}


def get_menu(dietary_flag: Optional[str] = None, category: Optional[str] = None) -> list:
    items = [i for i in MENU if i["available"]]
    if dietary_flag:
        items = [i for i in items if i.get("dietary_flag") == dietary_flag]
    if category:
        items = [i for i in items if i.get("category", "").lower() == category.lower()]
    return items


def get_menu_item(item_id: int) -> Optional[dict]:
    for i in MENU:
        if i["id"] == item_id:
            return i
    return None


def get_cart(phone: str) -> dict:
    cart_items = _carts.get(phone, [])
    items_with_details = []
    total = 0
    for ci in cart_items:
        item = get_menu_item(ci["item_id"])
        if item:
            subtotal = item["price"] * ci["quantity"]
            total += subtotal
            items_with_details.append({
                "item_id": ci["item_id"],
                "name": item["name"],
                "quantity": ci["quantity"],
                "unit_price": item["price"],
                "subtotal": subtotal,
                "image": item.get("image", ""),
            })
    return {"items": items_with_details, "total": total}


def add_to_cart(phone: str, item_id: int, quantity: int = 1) -> dict:
    if phone not in _carts:
        _carts[phone] = []
    # Check if item already in cart
    for ci in _carts[phone]:
        if ci["item_id"] == item_id:
            ci["quantity"] += quantity
            return get_cart(phone)
    _carts[phone].append({"item_id": item_id, "quantity": quantity})
    return get_cart(phone)


def clear_cart(phone: str):
    _carts[phone] = []


def place_order(phone: str, payment_status: str = "cash-on-delivery") -> Optional[dict]:
    global _order_counter
    cart = get_cart(phone)
    if not cart["items"]:
        return None

    _order_counter += 1
    order = {
        "order_id": _order_counter,
        "phone": phone,
        "items": cart["items"],
        "total": cart["total"],
        "status": "placed",
        "payment_status": payment_status,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _orders.append(order)
    clear_cart(phone)
    return order


def get_current_order(phone: str) -> Optional[dict]:
    user_orders = [o for o in _orders if o["phone"] == phone and o["status"] != "delivered"]
    if user_orders:
        return user_orders[-1]
    return None


def get_order(order_id: int) -> Optional[dict]:
    for o in _orders:
        if o["order_id"] == order_id:
            return o
    return None


def get_all_orders() -> list:
    return list(reversed(_orders))


def update_order_status(order_id: int, status: str) -> Optional[dict]:
    for o in _orders:
        if o["order_id"] == order_id:
            o["status"] = status
            return o
    return None


def get_profile(phone: str) -> dict:
    return _profiles.get(phone, {"phone_number": phone, "name": "", "address": "", "dietary_preference": ""})


def update_profile(phone: str, data: dict) -> dict:
    if phone not in _profiles:
        _profiles[phone] = {"phone_number": phone, "name": "", "address": "", "dietary_preference": ""}
    _profiles[phone].update(data)
    return _profiles[phone]
