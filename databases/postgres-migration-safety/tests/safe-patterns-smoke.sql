-- Smoke test for references/safe-patterns.md.
-- Runs every recipe end to end, in autocommit mode (like a migration tool with
-- transactions disabled), and asserts the end state. Any failure aborts with a
-- non-zero exit (run with psql -v ON_ERROR_STOP=1). Invoked by
-- tests/verify-lock-matrix.sh against a scratch database.

SET client_min_messages = warning;

-- ---------------------------------------------------------------- fixtures --
CREATE ROLE app_migrator;
CREATE TYPE order_status AS ENUM ('pending', 'paid');
CREATE TABLE users (id bigserial PRIMARY KEY, email text NOT NULL, full_name text);
INSERT INTO users (email, full_name) SELECT 'u' || g || '@example.com', 'User ' || g FROM generate_series(1, 2000) g;
CREATE TABLE orders (
  id          serial PRIMARY KEY,            -- deliberately int, for the bigint recipe
  user_id     bigint NOT NULL,
  total       double precision NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  placed_at   timestamp NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
  legacy_flag boolean
);
INSERT INTO orders (user_id, total) SELECT (g % 2000) + 1, g / 10.0 FROM generate_series(1, 20000) g;
CREATE TABLE customers (id bigserial PRIMARY KEY, name text);
INSERT INTO customers (name) VALUES ('a'), ('b');
CREATE TABLE legacy_events (id bigint);
CREATE TABLE events (id bigint, created_at date) PARTITION BY RANGE (created_at);
CREATE TABLE events_2026 PARTITION OF events FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
INSERT INTO events SELECT g, date '2026-03-01' FROM generate_series(1, 1000) g;
CREATE MATERIALIZED VIEW daily_revenue AS
  SELECT created_at::date AS day, sum(total) AS revenue FROM orders GROUP BY 1;
CREATE UNIQUE INDEX daily_revenue_day ON daily_revenue (day);

CREATE FUNCTION assert(ok boolean, msg text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN IF ok IS NOT TRUE THEN RAISE EXCEPTION 'ASSERTION FAILED: %', msg; END IF; END $$;

SELECT pg_relation_filenode('orders') AS orders_fn \gset

-- -------------------------------------------------------------- guardrails --
SET lock_timeout = '5s';
SET statement_timeout = '60s';
ALTER ROLE app_migrator SET lock_timeout = '5s';
SELECT assert(EXISTS (SELECT 1 FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
                      WHERE r.rolname = 'app_migrator' AND 'lock_timeout=5s' = ANY (s.setconfig)),
              'role default lock_timeout');

-- -------------------------------------------------------------- add column --
ALTER TABLE orders ADD COLUMN status text NOT NULL DEFAULT 'pending';
ALTER TABLE orders ADD COLUMN notes text;
ALTER TABLE orders ADD COLUMN public_id uuid;
ALTER TABLE orders ALTER COLUMN public_id SET DEFAULT gen_random_uuid();
SELECT assert(pg_relation_filenode('orders') = :orders_fn, 'add column recipes must not rewrite');
INSERT INTO orders (user_id, total) VALUES (1, 1);
SELECT assert((SELECT public_id IS NOT NULL FROM orders ORDER BY id DESC LIMIT 1), 'new rows get the default');
SELECT assert((SELECT count(*) FROM orders WHERE public_id IS NULL) = 20000, 'old rows stay NULL until backfill');
RESET statement_timeout;

-- ---------------------------------------------------------------- backfill --
CREATE OR REPLACE PROCEDURE backfill_orders_public_id(batch_size int DEFAULT 5000, pause_seconds float DEFAULT 0.1)
LANGUAGE plpgsql AS $$
DECLARE
  next_id bigint;
  max_id  bigint;
BEGIN
  SELECT min(id), max(id) INTO next_id, max_id FROM orders;
  WHILE next_id <= max_id LOOP
    UPDATE orders
    SET    public_id = gen_random_uuid()
    WHERE  id >= next_id AND id < next_id + batch_size
      AND  public_id IS NULL;          -- idempotent: safe to stop and re-run
    COMMIT;
    PERFORM pg_sleep(pause_seconds);   -- leave headroom for production traffic
    next_id := next_id + batch_size;
  END LOOP;
END $$;
CALL backfill_orders_public_id();
DROP PROCEDURE backfill_orders_public_id(int, float);
SELECT assert((SELECT count(*) FROM orders WHERE public_id IS NULL) = 0, 'backfill filled every row');

-- --------------------------------------------------------------- not null --
ALTER TABLE orders ADD CONSTRAINT orders_public_id_not_null CHECK (public_id IS NOT NULL) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_public_id_not_null;
ALTER TABLE orders ALTER COLUMN public_id SET NOT NULL;
ALTER TABLE orders DROP CONSTRAINT orders_public_id_not_null;
SELECT assert((SELECT attnotnull FROM pg_attribute WHERE attrelid = 'orders'::regclass AND attname = 'public_id'),
              'public_id is NOT NULL');

-- ------------------------------------------------------------- foreign key --
CREATE INDEX CONCURRENTLY IF NOT EXISTS orders_user_id_idx ON orders (user_id);
ALTER TABLE orders ADD CONSTRAINT orders_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES users (id) NOT VALID;
SELECT assert((SELECT count(*) FROM orders o
               WHERE o.user_id IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id)) = 0, 'orphan check before VALIDATE');
ALTER TABLE orders VALIDATE CONSTRAINT orders_user_id_fkey;
ALTER TABLE orders ADD COLUMN coupon_owner_id bigint REFERENCES users (id);
SELECT assert((SELECT bool_and(convalidated) FROM pg_constraint WHERE conrelid = 'orders'::regclass AND contype = 'f'),
              'foreign keys validated');

-- ------------------------------------------------------------------ check --
ALTER TABLE orders ADD CONSTRAINT orders_total_non_negative CHECK (total >= 0) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_total_non_negative;

-- ------------------------------------------------------------------ index --
CREATE INDEX CONCURRENTLY IF NOT EXISTS orders_created_at_idx ON orders (created_at);
SELECT assert((SELECT indisvalid FROM pg_index WHERE indexrelid = 'orders_created_at_idx'::regclass), 'CIC index valid');
DROP INDEX CONCURRENTLY IF EXISTS orders_created_at_idx;

-- ----------------------------------------------------------------- unique --
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS users_email_key_idx ON users (email);
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key_idx;
SELECT assert(EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_email_key' AND contype = 'u'), 'unique constraint');

-- ---------------------------------------------------------- rename column --
ALTER TABLE users ADD COLUMN display_name text;
CREATE OR REPLACE FUNCTION users_copy_full_name() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.display_name := NEW.full_name;
  RETURN NEW;
END $$;
CREATE TRIGGER users_copy_full_name
  BEFORE INSERT OR UPDATE OF full_name ON users
  FOR EACH ROW EXECUTE FUNCTION users_copy_full_name();
UPDATE users SET display_name = full_name WHERE display_name IS NULL;
INSERT INTO users (email, full_name) VALUES ('new@example.com', 'New User');
SELECT assert((SELECT display_name = 'New User' FROM users WHERE email = 'new@example.com'), 'trigger copies on insert');
DROP TRIGGER users_copy_full_name ON users;
DROP FUNCTION users_copy_full_name();
ALTER TABLE users DROP COLUMN full_name;

-- ----------------------------------------------------------- rename table --
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE customers RENAME TO accounts;
CREATE VIEW customers AS SELECT * FROM accounts;
COMMIT;
INSERT INTO customers (name) VALUES ('via-view');
UPDATE customers SET name = 'updated-via-view' WHERE name = 'via-view';
SELECT assert((SELECT count(*) FROM accounts WHERE name = 'updated-via-view') = 1, 'old name is writable through the view');
DROP VIEW customers;

-- ------------------------------------------------------------ drop column --
SET lock_timeout = '5s';
ALTER TABLE orders DROP COLUMN IF EXISTS legacy_flag;

-- ------------------------------------------------------------- drop table --
ALTER TABLE legacy_events RENAME TO legacy_events_deprecated_20261117;
DROP TABLE legacy_events_deprecated_20261117;

-- ------------------------------------------------------------ type change --
SELECT pg_relation_filenode('orders') AS orders_fn \gset
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL TimeZone = 'UTC';
ALTER TABLE orders ALTER COLUMN placed_at TYPE timestamptz;
COMMIT;
SELECT assert(pg_relation_filenode('orders') = :orders_fn, 'timestamp -> timestamptz under UTC must not rewrite');
ALTER TABLE orders ADD COLUMN total_amount numeric(12,2);
UPDATE orders SET total_amount = round(total::numeric, 2) WHERE total_amount IS NULL;

-- ---------------------------------------------------------- int -> bigint --
ALTER TABLE orders ADD COLUMN id_new bigint;
CREATE OR REPLACE FUNCTION orders_sync_id_new() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.id_new := NEW.id; RETURN NEW; END $$;
CREATE TRIGGER orders_sync_id_new BEFORE INSERT OR UPDATE ON orders
  FOR EACH ROW EXECUTE FUNCTION orders_sync_id_new();
UPDATE orders SET id_new = id WHERE id_new IS NULL;   -- stands in for the batched backfill
CREATE UNIQUE INDEX CONCURRENTLY orders_id_new_key ON orders (id_new);
ALTER TABLE orders ADD CONSTRAINT orders_id_new_not_null CHECK (id_new IS NOT NULL) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_id_new_not_null;
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE orders DROP CONSTRAINT orders_pkey;
ALTER TABLE orders ALTER COLUMN id_new SET NOT NULL;
ALTER TABLE orders DROP CONSTRAINT orders_id_new_not_null;
ALTER TABLE orders ADD CONSTRAINT orders_pkey PRIMARY KEY USING INDEX orders_id_new_key;
ALTER SEQUENCE orders_id_seq AS bigint OWNED BY orders.id_new;
ALTER TABLE orders ALTER COLUMN id_new SET DEFAULT nextval('orders_id_seq');
ALTER TABLE orders ALTER COLUMN id DROP DEFAULT;
ALTER TABLE orders ALTER COLUMN id DROP NOT NULL;
DROP TRIGGER orders_sync_id_new ON orders;
ALTER TABLE orders RENAME COLUMN id TO id_old;
ALTER TABLE orders RENAME COLUMN id_new TO id;
COMMIT;
INSERT INTO orders (user_id, total) VALUES (1, 2);
SELECT assert((SELECT format_type(atttypid, atttypmod) FROM pg_attribute
               WHERE attrelid = 'orders'::regclass AND attname = 'id') = 'bigint', 'orders.id is bigint');
SELECT assert((SELECT data_type FROM pg_sequences WHERE sequencename = 'orders_id_seq') = 'bigint'::regtype, 'sequence is bigint');
SELECT assert((SELECT max(id) FROM orders) > 20000, 'inserts after the swap get new ids');
SELECT assert((SELECT count(*) FROM orders WHERE id IS NULL) = 0, 'no NULL ids');
ALTER TABLE orders DROP COLUMN id_old;
DROP FUNCTION orders_sync_id_new();

-- ------------------------------------------------------------------- enum --
ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'refunded';
SELECT assert('refunded'::order_status IS NOT NULL, 'enum value usable after commit');

-- ------------------------------------------------------ materialized view --
REFRESH MATERIALIZED VIEW CONCURRENTLY daily_revenue;

-- ------------------------------------------------------------- partitions --
CREATE INDEX IF NOT EXISTS events_created_at_idx ON ONLY events (created_at);
SELECT assert(NOT (SELECT indisvalid FROM pg_index WHERE indexrelid = 'events_created_at_idx'::regclass), 'parent index starts invalid');
CREATE INDEX CONCURRENTLY IF NOT EXISTS events_2026_created_at_idx ON events_2026 (created_at);
ALTER INDEX events_created_at_idx ATTACH PARTITION events_2026_created_at_idx;
SELECT assert((SELECT indisvalid FROM pg_index WHERE indexrelid = 'events_created_at_idx'::regclass), 'parent index valid after attach');

SELECT 'SAFE-PATTERNS SMOKE TEST PASSED';
