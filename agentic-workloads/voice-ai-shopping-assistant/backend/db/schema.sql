-- Aisle — Aurora Serverless v2 (PostgreSQL) schema.
-- snake_case, plural tables. Money is integer cents.
--
-- Covers the full data model: products + specials (catalogue), grocery_items
-- (the persistent list), carts/cart_items, orders/order_events/order_artifacts/
-- order_items, and the checkout support tables (virtual_cards, merchants). The
-- products columns below match the `Product` shape in
-- backend/agent/contracts.py.

-- Idempotent (re-runnable by the seed loader on each deploy).
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- products — the live store inventory (mirrors the Product contract)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
  product_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  brand         text NOT NULL,
  category      text NOT NULL,
  aisle         text NOT NULL,
  price_cents   integer NOT NULL CHECK (price_cents >= 0),
  unit          text NOT NULL,
  allergens     text[] NOT NULL DEFAULT '{}',
  dietary_tags  text[] NOT NULL DEFAULT '{}',
  quality_tier  text NOT NULL DEFAULT 'standard'
                  CHECK (quality_tier IN ('value', 'standard', 'premium')),
  in_stock      boolean NOT NULL DEFAULT true,
  image_url     text
);

CREATE INDEX IF NOT EXISTS idx_products_name        ON products (name);
CREATE INDEX IF NOT EXISTS idx_products_category    ON products (category);
CREATE INDEX IF NOT EXISTS idx_products_allergens   ON products USING gin (allergens);
CREATE INDEX IF NOT EXISTS idx_products_dietary     ON products USING gin (dietary_tags);
-- Trigram index for fuzzy/substring name search (search_products tool).
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_products_name_trgm   ON products USING gin (name gin_trgm_ops);

-- Semantic search (search_products): pgvector embedding of the product text
-- (name + brand + category + aisle), generated at seed time via Bedrock Cohere
-- Embed English v3 (1024-dim, input_type=search_document). Lets conceptual
-- queries ("something fizzy", "taco night") match products whose names don't
-- contain those words. Nullable so a row is valid before its embedding is set.
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding vector(1024);
-- HNSW cosine index — fast approximate nearest-neighbour at 556..~1500 rows.
CREATE INDEX IF NOT EXISTS idx_products_embedding
  ON products USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- specials — current promotions, relationally mapped to products.
--   one row per product currently on special; product_id is a real FK so a
--   product and its special join trivially (search_products LEFT JOINs this).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS specials (
  special_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id           uuid NOT NULL
                         REFERENCES products (product_id) ON DELETE CASCADE,
  special_price_cents  integer NOT NULL CHECK (special_price_cents >= 0),
  was_price_cents      integer NOT NULL CHECK (was_price_cents >= 0),
  savings_cents        integer NOT NULL DEFAULT 0 CHECK (savings_cents >= 0),
  special_type         text NOT NULL DEFAULT 'special'
                         CHECK (special_type IN ('special', 'half_price', 'member_price')),
  starts_on            date,
  ends_on              date,
  -- a product is on at most one active special at a time (demo simplification)
  UNIQUE (product_id)
);

CREATE INDEX IF NOT EXISTS idx_specials_product ON specials (product_id);

-- ---------------------------------------------------------------------------
-- grocery_items — persistent per-user grocery list (UC1). Durable across
--   sessions (keyed by an opaque user_id, supplied by the runtime), unlike the
--   session cart. Holds the shopper's raw phrase ("bread") and, once the agent
--   resolves it (resolve_products), the matched product_id + name snapshot.
--   status tracks the item lifecycle: active | have | out_of_stock | removed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grocery_items (
  item_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     text NOT NULL,
  raw_text    text NOT NULL,                            -- what the shopper said
  product_id  uuid REFERENCES products (product_id),    -- resolved match, nullable
  name        text,                                     -- resolved product name snapshot
  qty         numeric NOT NULL DEFAULT 1 CHECK (qty > 0),
  unit        text,
  status      text NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'have', 'out_of_stock', 'removed')),
  added_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_grocery_items_user ON grocery_items (user_id, status);

-- ---------------------------------------------------------------------------
-- carts / cart_items — the shopper's working basket, keyed by session_id
--   One open cart per session; cart_items snapshot name + price
--   at add time so the cart total is stable even if a product's price changes.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS carts (
  cart_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id  text NOT NULL UNIQUE,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cart_items (
  cart_id      uuid NOT NULL REFERENCES carts (cart_id) ON DELETE CASCADE,
  product_id   uuid NOT NULL REFERENCES products (product_id),
  name         text NOT NULL,                 -- snapshot at add time
  qty          integer NOT NULL CHECK (qty > 0),
  price_cents  integer NOT NULL CHECK (price_cents >= 0),  -- snapshot at add time
  PRIMARY KEY (cart_id, product_id)            -- one line per product; re-add bumps qty
);

CREATE INDEX IF NOT EXISTS idx_cart_items_cart ON cart_items (cart_id);

-- ---------------------------------------------------------------------------
-- orders / order_items — a submitted order. order_items is an
--   additive line-item snapshot so an order can be reconstructed
--   for the frontend independently of the cart, which may change afterwards.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
  order_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    text NOT NULL,
  status        text NOT NULL DEFAULT 'submitted'
                  CHECK (status IN ('draft', 'submitted', 'ready_for_pickup',
                                    -- async fulfillment lifecycle (Phase 2):
                                    'paid', 'placing', 'placed',
                                    'declined_insufficient_funds',
                                    'browser_blocked', 'failed')),
  pickup_code   text NOT NULL,
  pickup_time   timestamptz,
  total_cents   integer NOT NULL CHECK (total_cents >= 0),
  -- payment audit (additive): proof id from AgentCore ProcessPayment, null if
  -- payments were flag-disabled for this order.
  payment_id    text,
  -- async fulfillment audit (additive): the AgentCore browser session that
  -- placed (or attempted) the order, and a human-readable status detail.
  browser_session_id text,
  status_detail      text,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  created_at    timestamptz NOT NULL DEFAULT now()
);
-- Additive migrations for clusters created before Phase 2 (schema is re-applied
-- on each deploy; ALTER ... IF NOT EXISTS keeps it idempotent). The status CHECK
-- above only applies to fresh tables, so older rows/inserts with the new states
-- are unblocked by widening the constraint here.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS browser_session_id text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS status_detail text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check CHECK (status IN (
  'draft', 'submitted', 'ready_for_pickup',
  'paid', 'placing', 'placed',
  'declined_insufficient_funds', 'browser_blocked', 'failed'));

CREATE INDEX IF NOT EXISTS idx_orders_session ON orders (session_id);

-- ---------------------------------------------------------------------------
-- order_events — the structured event stream backing order observability
--   (Phase 2). One append-only row per lifecycle step (order_created,
--   payment_processed, enqueued_for_fulfillment, fulfillment_started,
--   balance_checked, browser_session_started, item_added, reached_checkout,
--   declined_insufficient_funds, order_placed, browser_blocked, error, ...).
--   `payload` carries step-specific detail as JSON. The frontend reads these
--   (newest-last) to render a live status timeline.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_events (
  event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id    uuid NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
  event_type  text NOT NULL,
  payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events (order_id, created_at);

-- ---------------------------------------------------------------------------
-- order_artifacts — binary evidence captured during async fulfillment (Phase 2):
--   browser screenshots, the final decline screenshot, a live-view URL, etc.
--   The bytes live in S3 (ARTIFACTS_BUCKET); this row holds the pointer + label
--   so the frontend can list and display them per order.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_artifacts (
  artifact_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id     uuid NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
  kind         text NOT NULL DEFAULT 'screenshot'
                 CHECK (kind IN ('screenshot', 'live_view', 'dom', 'log')),
  label        text NOT NULL,
  s3_key       text,                  -- key in ARTIFACTS_BUCKET (screenshot/dom/log)
  url          text,                  -- direct URL (live_view stream), if applicable
  content_type text NOT NULL DEFAULT 'image/png',
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_order_artifacts_order ON order_artifacts (order_id, created_at);

-- ---------------------------------------------------------------------------
-- virtual_cards — the mock x402->card issuer bridge (Phase 2). When the async
--   worker obtains a real AgentCore x402 payment proof AND the wallet balance
--   covers the order, it "issues" a deterministic test virtual card here. The
--   x402 storefront's /pay endpoint validates the typed card against this table
--   (status active + amount covers total) to authorize the order — demonstrating
--   a crypto->card conversion end-to-end on TEST rails (no real issuer/funds).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS virtual_cards (
  card_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id      uuid NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
  pan           text NOT NULL,                 -- test card number (display: last4)
  exp           text NOT NULL,                 -- MM/YY
  cvc           text NOT NULL,
  funded_cents  integer NOT NULL CHECK (funded_cents >= 0),  -- amount the x402 proof funded
  payment_id    text,                          -- AgentCore processPaymentId backing it
  status        text NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'charged', 'void')),
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_virtual_cards_pan   ON virtual_cards (pan);
CREATE INDEX IF NOT EXISTS idx_virtual_cards_order ON virtual_cards (order_id);
-- Stripe Issuing: the real card id when the broker mints via Stripe (test mode).
ALTER TABLE virtual_cards ADD COLUMN IF NOT EXISTS stripe_card_id text;

-- ---------------------------------------------------------------------------
-- merchants — the router's source of truth (Phase 3). create_order looks up the
--   merchant and takes ONE pathway based on supports_x402:
--     supports_x402 = true  -> pay the merchant's x402 endpoint DIRECTLY via
--                              AgentCore Payments (no card, no browser).
--     supports_x402 = false -> AgentCore Browser drives the merchant's web
--                              checkout, paying with a Stripe-issued card.
--   This generalises the agent beyond grocery: any merchant declares how it can
--   be paid, and the agent picks the matching tool.
--   INTEGRATING A REAL MERCHANT: insert a row here pointing `endpoint` at the
--   real service. supports_x402=true expects the endpoint to return HTTP 402
--   with x402 requirements and settle the signed proof via a facilitator (the
--   bundled merchant_api/delivery_api are demo stand-ins that don't settle
--   on-chain). supports_x402=false routes through the browser pathway — see the
--   note in backend/tools/storefront/handler.py.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchants (
  merchant_id    text PRIMARY KEY,             -- stable slug, e.g. 'aisle-grocery'
  name           text NOT NULL,
  supports_x402  boolean NOT NULL DEFAULT false,
  endpoint       text NOT NULL,                -- base URL (IAM REST API) of the merchant
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_items (
  order_id     uuid NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
  product_id   uuid NOT NULL REFERENCES products (product_id),
  name         text NOT NULL,
  qty          integer NOT NULL CHECK (qty > 0),
  price_cents  integer NOT NULL CHECK (price_cents >= 0),
  PRIMARY KEY (order_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items (order_id);
