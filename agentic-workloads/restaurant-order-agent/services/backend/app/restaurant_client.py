"""
HTTP client that proxies requests to the restaurant backend API.
"""

import httpx
import logging

from .config import RESTAURANT_API_BASE, RESTAURANT_API_KEY

logger = logging.getLogger(__name__)


class RestaurantAPIClient:
    """Async HTTP client for the restaurant backend."""

    def __init__(self):
        self.base_url = RESTAURANT_API_BASE
        self.api_key = RESTAURANT_API_KEY

    def _headers(self, session_token: str | None = None) -> dict:
        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        if session_token:
            headers["Authorization"] = f"Bearer {session_token}"
        return headers

    async def request_otp(self, phone_number: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{self.base_url}/auth/otp/request",
                json={"phone_number": phone_number},
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def verify_otp(self, phone_number: str, otp_code: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{self.base_url}/auth/otp/verify",
                json={"phone_number": phone_number, "otp_code": otp_code},
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def get_menu(self, dietary_flag: str | None = None) -> list:
        params = {}
        if dietary_flag:
            params["dietary_flag"] = dietary_flag
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.base_url}/menu",
                params=params,
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def get_menu_item(self, item_id: int) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.base_url}/menu/{item_id}",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def get_cart(self, session_token: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.base_url}/cart",
                headers=self._headers(session_token),
            )
            r.raise_for_status()
            return r.json()

    async def add_to_cart(self, session_token: str, menu_item_id: int, quantity: int = 1) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{self.base_url}/cart/items",
                json={"menu_item_id": menu_item_id, "quantity": quantity},
                headers=self._headers(session_token),
            )
            r.raise_for_status()
            return r.json()

    async def place_order(self, session_token: str, payment_status: str = "cash-on-delivery") -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{self.base_url}/orders",
                json={"payment_status": payment_status},
                headers=self._headers(session_token),
            )
            r.raise_for_status()
            return r.json()

    async def get_current_order(self, session_token: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.base_url}/orders/current",
                headers=self._headers(session_token),
            )
            r.raise_for_status()
            return r.json()

    async def get_order(self, session_token: str, order_id: int) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.base_url}/orders/{order_id}",
                headers=self._headers(session_token),
            )
            r.raise_for_status()
            return r.json()

    async def get_delivery_status(self, session_token: str, order_id: int) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.base_url}/orders/{order_id}/delivery-status",
                headers=self._headers(session_token),
            )
            r.raise_for_status()
            return r.json()

    async def get_profile(self, session_token: str) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.base_url}/profile",
                headers=self._headers(session_token),
            )
            r.raise_for_status()
            return r.json()

    async def update_profile(self, session_token: str, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.patch(
                f"{self.base_url}/profile",
                json=data,
                headers=self._headers(session_token),
            )
            r.raise_for_status()
            return r.json()

    # --- Kitchen endpoints ---

    async def get_all_orders(self) -> list:
        """Get all orders (kitchen view)."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self.base_url}/orders",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()

    async def update_order_status(self, order_id: int, status: str) -> dict:
        """Update order status (kitchen action)."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.patch(
                f"{self.base_url}/orders/{order_id}/status",
                json={"status": status},
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()


# Singleton
restaurant_client = RestaurantAPIClient()
