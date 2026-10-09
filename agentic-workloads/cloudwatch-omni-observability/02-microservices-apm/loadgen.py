"""Steady synthetic traffic against the frontend: about 80% checkouts, 20% order lookups."""

import os
import random
import time

import httpx

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://frontend:8000")
RPS = float(os.environ.get("RPS", "2"))
CATALOG = ["mug", "t-shirt", "hoodie", "sticker", "notebook", "cap"]
KNOWN_ORDERS = ["ORD-1001", "ORD-1002", "ORD-1003", "ORD-0000"]  # ORD-0000 -> 404


def main():
    with httpx.Client(base_url=FRONTEND_URL, timeout=15) as client:
        while True:
            try:
                if random.random() < 0.8:  # nosec B311 - controls synthetic traffic mix
                    items = random.sample(CATALOG, k=random.randint(1, 3))  # nosec B311 - synthetic catalog data
                    r = client.post("/checkout", json={"items": items})
                else:
                    r = client.get(f"/orders/{random.choice(KNOWN_ORDERS)}")  # nosec B311 - synthetic lookup
                print(r.request.method, r.request.url.path, r.status_code, flush=True)
            except httpx.HTTPError as exc:
                print("request failed:", exc, flush=True)
            time.sleep(random.expovariate(RPS))


if __name__ == "__main__":
    main()
