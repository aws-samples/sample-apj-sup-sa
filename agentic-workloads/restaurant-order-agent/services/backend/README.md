# Tasty Bites Backend

FastAPI backend that wraps the restaurant customer-tools API, adds SNS-based OTP authentication, and provides a chat endpoint powered by Claude via Bedrock.

## Setup

```bash
cd services/backend
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your actual keys
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

## Endpoints

### Auth
- `POST /api/auth/request-otp` — Send OTP via AWS SNS
- `POST /api/auth/verify-otp` — Verify OTP, returns JWT

### Menu (public)
- `GET /api/menu` — Get menu items
- `GET /api/menu/{item_id}` — Get single item

### Cart (authenticated)
- `GET /api/cart` — View cart
- `POST /api/cart/add` — Add item to cart

### Orders (authenticated)
- `POST /api/orders` — Place order
- `GET /api/orders/current` — Current active order
- `GET /api/orders/{id}/delivery-status` — Delivery tracking

### Kitchen (no auth)
- `GET /api/kitchen/orders` — All orders
- `PATCH /api/kitchen/orders/{id}/status` — Update order status

### Chat
- `POST /api/chat` — Text chat with Claude agent (tool calling)
