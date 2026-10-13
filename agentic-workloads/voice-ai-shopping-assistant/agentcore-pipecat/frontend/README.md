# Aisle frontend (Daily)

A minimal Vite + React web client for the Aisle voice grocery agent. It calls
`/start` (API Gateway) to launch the bot, joins the same **Daily** room for
real-time audio, and renders the bot's data-channel events as UI:

- `search_products` / `get_product_variants` → **product grid** (image, price, special badge)
- `add_to_cart` / `get_cart` → **cart panel**
- `create_order` → **order confirmation**
- `transcript` → bottom transcript bar

Reused from the reference `tavus-pipecat-example/frontend`: the Daily client and
the `app-message` (bot data) handling pattern. The iframe overlay was replaced
with image-based product cards.

## Run

```bash
cd agentcore-pipecat/frontend
cp .env.example .env        # VITE_START_URL = the API Gateway /start URL
npm install
npm run dev                 # http://localhost:5173
```

Click **Start shopping**, allow the mic, and talk:
- "Find me some milk." → product images appear
- "What pasta is on special?"
- "Add the cheapest one to my cart." → "Place a pickup order."

## Notes
- `/start` is the **API Gateway** endpoint (not a public Lambda Function URL).
- Audio-only today; when the Tavus avatar is enabled on the agent, its video
  track renders automatically in the agent tile.
- For a public deploy, build (`npm run build`) → host `dist/` on S3/CloudFront.
