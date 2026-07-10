# Kitchen Dashboard — Tasty Bites

Restaurant-facing order management interface. Kitchen staff can view incoming orders and update their status as they progress through preparation and delivery.

## Features

- Real-time order feed (polls every 10 seconds)
- Filter by status: New, Confirmed, Preparing, Dispatched, Delivered
- One-click status advancement (e.g., "Mark as Preparing")
- Dark theme optimized for kitchen display screens

## Setup

```bash
cd apps/kitchen-dashboard
npm install
```

## Run

```bash
npm run dev
# Runs on http://localhost:5174
# Proxies /api to backend at http://localhost:8000
```

## Order Status Flow

```
placed → confirmed → preparing → dispatched → in_transit → delivered
```

Each status change is sent to the backend via `PATCH /api/kitchen/orders/{id}/status`.
