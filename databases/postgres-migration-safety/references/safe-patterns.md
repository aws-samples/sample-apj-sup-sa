# Safe migration patterns

Zero-downtime recipes for the risky operations in `lock-matrix.md`. Examples use `users` and `orders`; swap in your own names. Every SQL block in this file is exercised by `tests/safe-patterns-smoke.sql` (PostgreSQL 14 and 17).

**Contents:** [Guardrails](#guardrails-use-on-every-migration) · [Add a column](#add-a-column) · [Add NOT NULL](#add-not-null-to-an-existing-column) · [Add a foreign key](#add-a-foreign-key) · [Add a CHECK](#add-a-check-constraint) · [Add an index](#add-an-index) · [Add UNIQUE or PRIMARY KEY](#add-a-unique-constraint-or-primary-key) · [Drop an index](#drop-an-index) · [Rename a column](#rename-a-column) · [Rename a table](#rename-a-table) · [Drop a column](#drop-a-column) · [Drop a table](#drop-a-table) · [Change a type](#change-a-column-type) · [int to bigint](#int-to-bigint-on-a-big-table) · [Backfills](#backfill-data) · [Enums](#enums) · [Materialized views](#materialized-views) · [Partitions](#partitioned-tables) · [Deploy ordering](#deploy-ordering-expand-and-contract) · [Automate it](#guardrails-worth-automating)

## Guardrails (use on every migration)

```sql
-- At the top of any migration that touches a busy table.
SET lock_timeout = '5s';        -- give up instead of queueing behind a long query (and blocking everyone behind you)
SET statement_timeout = '60s';  -- for statements that should be instant; leave it unset for CONCURRENTLY builds and backfills
```

- **Retry on lock timeout.** The error is `canceling statement due to lock timeout` (SQLSTATE `55P03`). If the migration ran in a transaction, nothing was applied, so re-run it, ideally with backoff. If it didn't, make statements idempotent (`IF NOT EXISTS`, `IF EXISTS`) so a re-run is safe.
- **Inside a transaction, `SET LOCAL`** scopes the setting to that transaction. Plain `SET` persists for the session, and migration tools often reuse one session for every pending migration.
- **Set defaults on the migration role** so nobody forgets:
  ```sql
  ALTER ROLE app_migrator SET lock_timeout = '5s';
  ```
- **Check for long transactions before you start.** See `preflight-queries.md`. A single `idle in transaction` session can block a migration indefinitely.
- **One risky statement per migration.** Locks are held until commit, so one slow statement in a multi-statement transaction makes every lock in it slow.
- **Take a snapshot before destructive changes**, and know your point-in-time-recovery window.

## Add a column

Instant (catalog-only) on PostgreSQL 11+ when there's no default, or when the default is a constant or a stable expression like `now()`:

```sql
ALTER TABLE orders ADD COLUMN status text NOT NULL DEFAULT 'pending';  -- instant
ALTER TABLE orders ADD COLUMN notes text;                              -- instant
```

A default also applies to **every existing row**. With `DEFAULT now()`, they all get the migration time, which is rarely right for `created_at`. With `DEFAULT 'pending'`, every historical order becomes pending, so any worker or report that acts on pending orders will pick them up. Decide the correct value for existing rows and backfill it.

**Volatile default, identity, serial, or generated column: avoid the rewrite.** Add the column empty, set the default for new rows, then backfill old rows:

```sql
-- Migration 1 (instant)
ALTER TABLE orders ADD COLUMN public_id uuid;
ALTER TABLE orders ALTER COLUMN public_id SET DEFAULT gen_random_uuid();  -- applies to new rows only, no rewrite
-- Then backfill existing rows in batches (see "Backfill data"),
-- then add NOT NULL with the CHECK pattern below.
```

To add a stored generated column to a big table without a rewrite, use a plain column kept up to date by a trigger (or by the app), then backfill it.

## Add NOT NULL to an existing column

`SET NOT NULL` scans the whole table while holding ACCESS EXCLUSIVE. On PostgreSQL 12+ it skips the scan when a validated `CHECK (col IS NOT NULL)` already proves there are no NULLs:

```sql
-- Migration 1: instant; enforced for new writes immediately
ALTER TABLE orders ADD CONSTRAINT orders_public_id_not_null CHECK (public_id IS NOT NULL) NOT VALID;

-- Migration 2 (separate transaction): scans, but reads and writes continue
ALTER TABLE orders VALIDATE CONSTRAINT orders_public_id_not_null;

-- Migration 3: instant; uses the validated CHECK instead of scanning
ALTER TABLE orders ALTER COLUMN public_id SET NOT NULL;
ALTER TABLE orders DROP CONSTRAINT orders_public_id_not_null;
```

Backfill any NULLs before migration 2, or `VALIDATE` will fail. Migrations 1 and 2 must be separate transactions. Otherwise migration 1's ACCESS EXCLUSIVE lock is held throughout migration 2's scan.

## Add a foreign key

A validated `ADD FOREIGN KEY` scans the referencing table while holding SHARE ROW EXCLUSIVE on **both** tables, so writes to `users` stop too. Split it:

```sql
-- Index the referencing column first, so deletes and updates on users don't scan orders.
CREATE INDEX CONCURRENTLY IF NOT EXISTS orders_user_id_idx ON orders (user_id);   -- own migration, no transaction

-- Migration: brief lock on both tables; enforced for new writes immediately
ALTER TABLE orders ADD CONSTRAINT orders_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES users (id) NOT VALID;

-- Separate migration: scans orders, but reads and writes on both tables continue
ALTER TABLE orders VALIDATE CONSTRAINT orders_user_id_fkey;
```

Before validating, check for orphaned rows. Otherwise `VALIDATE` fails at the end of its full scan:

```sql
SELECT count(*) FROM orders o
WHERE o.user_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id);
```

A new `ON DELETE RESTRICT` (the default) also changes app behavior: deleting a parent row that still has children starts failing. Check the account-deletion path.

Adding a **new** column with an inline reference is instant, because an all-NULL column needs no validation. It still locks both tables briefly, so use `lock_timeout`:

```sql
ALTER TABLE orders ADD COLUMN coupon_owner_id bigint REFERENCES users (id);
```

## Add a CHECK constraint

```sql
ALTER TABLE orders ADD CONSTRAINT orders_total_non_negative CHECK (total >= 0) NOT VALID;  -- instant
ALTER TABLE orders VALIDATE CONSTRAINT orders_total_non_negative;                         -- separate transaction
```

To change an existing CHECK, add the new one as `NOT VALID`, validate it, then drop the old one.

## Add an index

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS orders_created_at_idx ON orders (created_at);
```

- **It can't run inside a transaction block.** That includes a migration tool's wrapping transaction and a file sent to the server as one multi-statement string. Put it in its own migration with nothing else in it, and turn the tool's transaction off for that migration (`orm-guide.md`).
- **It's slower than a plain `CREATE INDEX`:** two table scans, and it waits for every transaction already running. Long transactions stall it.
- **It waits for, or cancels, autovacuum on the table.** Run only one concurrent build per table at a time.
- **If it fails** (duplicate values for a unique index, deadlock, or cancellation), it leaves an **INVALID** index that slows writes but is never used for reads. Find it with the invalid-index query in `preflight-queries.md`, then drop it and retry:
  ```sql
  DROP INDEX CONCURRENTLY IF EXISTS orders_created_at_idx;
  ```
  **Don't rely on `IF NOT EXISTS` to retry.** It only checks the name, so it silently skips over the INVALID leftover and reports success.

## Add a UNIQUE constraint or PRIMARY KEY

`ADD CONSTRAINT ... UNIQUE` builds the index while holding ACCESS EXCLUSIVE, so even reads are blocked for the whole build. Build the index concurrently first, then attach it instantly:

```sql
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS users_email_key_idx ON users (email);  -- own migration
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key_idx;  -- instant; renames the index
```

For `PRIMARY KEY USING INDEX`, PostgreSQL also makes the columns NOT NULL, and it scans to do that if they're nullable. Run the [NOT NULL pattern](#add-not-null-to-an-existing-column) first.

## Drop an index

```sql
DROP INDEX CONCURRENTLY IF EXISTS orders_created_at_idx;  -- own migration, no transaction
```

This doesn't work for indexes that back a constraint. Use `ALTER TABLE ... DROP CONSTRAINT` (a brief ACCESS EXCLUSIVE) instead.

## Rename a column

The rename itself is instant, but **any running code that uses the old name breaks immediately**, including old tasks or pods still serving traffic during a rolling deploy.

**Best option: don't rename the database column.** Rename it only in code: Prisma `@map("old_name")`, Django `db_column="old_name"`, Rails `alias_attribute :new_name, :old_name`, Drizzle `newName: text("old_name")`, TypeORM `@Column({ name: "old_name" })`.

**If the database name must change**, use expand and contract:

1. Migration: `ALTER TABLE users ADD COLUMN display_name text;`
2. Deploy code that **writes both** columns and still reads the old one.
3. Backfill: `UPDATE ... SET display_name = full_name WHERE display_name IS NULL` in batches.
4. Deploy code that **reads the new** column (and still writes both).
5. Deploy code that writes only the new column. Tell the ORM to ignore the old column.
6. Migration: `ALTER TABLE users DROP COLUMN full_name;`

If other writers you don't control touch the table (scripts, other services), add a trigger during steps 2–4 that copies the old column to the new one. This works only while the old column is the source of truth:

```sql
CREATE OR REPLACE FUNCTION users_copy_full_name() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.display_name := NEW.full_name;
  RETURN NEW;
END $$;

CREATE TRIGGER users_copy_full_name
  BEFORE INSERT OR UPDATE OF full_name ON users
  FOR EACH ROW EXECUTE FUNCTION users_copy_full_name();
-- Drop the trigger before step 5.
```

## Rename a table

Rename the table and leave a view with the old name behind. A view over a single table with no aggregates is automatically updatable, so old code keeps reading and writing through it:

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE customers RENAME TO accounts;
CREATE VIEW customers AS SELECT * FROM accounts;
COMMIT;
-- Once no code uses the old name:
DROP VIEW customers;
```

`SELECT *` in a view is expanded when the view is created, so columns added to `accounts` later won't appear in `customers`. Drop the view before your next schema change to the table.

## Drop a column

1. Deploy code that no longer reads or writes the column. Rails caches column lists, so add `self.ignored_columns += ["legacy_flag"]` first. Django and Prisma select columns by name, so remove the field from the model or schema in this deploy, but keep the drop for the next one.
2. Snapshot if there's any chance you'll want the data back.
3. Migration, instant:
   ```sql
   SET lock_timeout = '5s';
   ALTER TABLE orders DROP COLUMN IF EXISTS legacy_flag;
   ```

The disk space comes back gradually as rows are updated, or all at once after a full rewrite. You don't need to do anything.

## Drop a table

1. Confirm nothing reads it. Rename it first and wait one release, so stray readers fail loudly and the change is easy to undo:
   ```sql
   ALTER TABLE legacy_events RENAME TO legacy_events_deprecated_20261117;
   ```
2. Snapshot.
3. `DROP TABLE legacy_events_deprecated_20261117;`. If other tables reference it, `CASCADE` drops their foreign keys too, which also locks those tables.

## Change a column type

These changes are instant (no rewrite):

- `varchar(n)` to a larger `varchar(m)`, or to `text` or unconstrained `varchar`
- `numeric(p,s)` to `numeric(p2,s)` with p2 > p and the **same** scale, or to unconstrained `numeric`
- `timestamp` to `timestamptz`, PostgreSQL 12+, **only** when the session `TimeZone` is `UTC`. This reinterprets existing values as UTC, so be sure they were stored as UTC:
  ```sql
  BEGIN;
  SET LOCAL lock_timeout = '5s';
  SET LOCAL TimeZone = 'UTC';
  ALTER TABLE orders ALTER COLUMN placed_at TYPE timestamptz;
  COMMIT;
  ```

These changes rewrite the whole table and rebuild every index while holding ACCESS EXCLUSIVE:

- `int` to `bigint`
- `text` to `varchar(n)`, or narrowing a `varchar`
- changing a `numeric` scale, or `double precision` to `numeric`
- `timestamp` to `timestamptz` under a non-UTC time zone
- anything with `USING`

**Safe pattern for a rewrite type change** (here, converting a float `total` to `numeric(12,2)` money):

1. Migration: `ALTER TABLE orders ADD COLUMN total_amount numeric(12,2);`
2. Deploy code that writes both columns, or add a sync trigger as in [Rename a column](#rename-a-column).
3. Backfill `total_amount` from `total` in batches.
4. Add NOT NULL or CHECK constraints with the NOT VALID pattern.
5. Deploy code that reads `total_amount`.
6. Stop writing `total`, then drop it.

## int to bigint on a big table

Prevention is far cheaper: use `bigint` for every primary key and foreign key from day one. Check your headroom with the sequence query in `preflight-queries.md`. If you're past roughly 50% of 2,147,483,647, plan this now.

The safe conversion is a multi-step project. Rehearse it on a clone at production size:

```sql
-- 1. New column + sync trigger for new and changed rows
ALTER TABLE orders ADD COLUMN id_new bigint;
CREATE OR REPLACE FUNCTION orders_sync_id_new() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.id_new := NEW.id; RETURN NEW; END $$;
CREATE TRIGGER orders_sync_id_new BEFORE INSERT OR UPDATE ON orders
  FOR EACH ROW EXECUTE FUNCTION orders_sync_id_new();

-- 2. Backfill id_new in batches (see "Backfill data"), then:
CREATE UNIQUE INDEX CONCURRENTLY orders_id_new_key ON orders (id_new);   -- own migration
ALTER TABLE orders ADD CONSTRAINT orders_id_new_not_null CHECK (id_new IS NOT NULL) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_id_new_not_null;            -- separate transaction

-- 3. Swap in one short transaction
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE orders DROP CONSTRAINT orders_pkey;
ALTER TABLE orders ALTER COLUMN id_new SET NOT NULL;                     -- instant (CHECK proves it)
ALTER TABLE orders DROP CONSTRAINT orders_id_new_not_null;
ALTER TABLE orders ADD CONSTRAINT orders_pkey PRIMARY KEY USING INDEX orders_id_new_key;
ALTER SEQUENCE orders_id_seq AS bigint OWNED BY orders.id_new;
ALTER TABLE orders ALTER COLUMN id_new SET DEFAULT nextval('orders_id_seq');
ALTER TABLE orders ALTER COLUMN id DROP DEFAULT;
ALTER TABLE orders ALTER COLUMN id DROP NOT NULL;                         -- new inserts leave it NULL
DROP TRIGGER orders_sync_id_new ON orders;
ALTER TABLE orders RENAME COLUMN id TO id_old;
ALTER TABLE orders RENAME COLUMN id_new TO id;
COMMIT;
-- 4. Later: ALTER TABLE orders DROP COLUMN id_old;
```

Foreign keys that reference `orders.id` must be dropped before the swap and re-added afterward, using the NOT VALID pattern on the converted `bigint` columns. Referencing columns typically need the same conversion. If the key is an identity column rather than `serial`, adapt the sequence steps. This is where a clone rehearsal earns its keep.

## Backfill data

Never backfill a big table in one `UPDATE` inside the migration. That holds row locks on every touched row, keeps one huge transaction open (which blocks vacuum and bloats the table), and generates a burst of WAL that can cause replica lag.

Do it in batches, outside the migration, committing each batch:

```sql
-- PostgreSQL 11+: a procedure can COMMIT between batches. Run it with CALL, outside any transaction block.
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
```

- Batch by primary-key **range**, not `LIMIT` plus `WHERE col IS NULL`, which rescans the table on every batch unless that condition is indexed.
- Keep each batch under about a second. Raise the batch size if the backfill is fast and the database is calm.
- Watch while it runs: CPU, write latency, replica lag (`ReplicaLag` for RDS read replicas, `AuroraReplicaLag` for Aurora readers), and `CPUCreditBalance` on burstable `db.t*` instances.
- An application-side job (a script, a queue worker, or a task in your framework) works just as well. The rules are the same: small batches, a commit per batch, idempotent updates.

## Enums

```sql
ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'refunded';   -- instant, no table lock
```

On PostgreSQL 12+ this can run inside a transaction, but the new value can't be **used** until that transaction commits (`unsafe use of new value`). Put the code or backfill that uses it in a later migration. You can rename values (`RENAME VALUE`, instant), but you can't remove them without creating a new type and rewriting every column that uses it. For sets that change, prefer `text` plus a CHECK constraint, or a lookup table.

## Materialized views

```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY daily_revenue;   -- reads continue; requires a unique index on the view
```

A plain `REFRESH` takes ACCESS EXCLUSIVE, so reads of the view block until it finishes.

## Partitioned tables

`CREATE INDEX CONCURRENTLY` doesn't work on a partitioned parent. Build the index on each partition instead:

```sql
CREATE INDEX IF NOT EXISTS events_created_at_idx ON ONLY events (created_at);   -- instant; parent index starts INVALID
CREATE INDEX CONCURRENTLY IF NOT EXISTS events_2026_created_at_idx ON events_2026 (created_at);   -- one per partition
ALTER INDEX events_created_at_idx ATTACH PARTITION events_2026_created_at_idx;
-- The parent index becomes valid once every partition's index is attached.
```

- `ATTACH PARTITION` scans the new partition, holding ACCESS EXCLUSIVE on it, unless a CHECK constraint already proves the rows fit. Add a matching CHECK, `NOT VALID` plus `VALIDATE`, before attaching.
- `DETACH PARTITION ... CONCURRENTLY` (PostgreSQL 14+) takes SHARE UPDATE EXCLUSIVE instead of ACCESS EXCLUSIVE on the parent. It can't run in a transaction block.
- Dropping an old time partition is the fast way to delete old data, compared with a mass `DELETE`.

## Deploy ordering (expand and contract)

During a rolling deploy, old and new code run at the same time. Every migration must work with **both**.

| Change | Run it | Why |
| --- | --- | --- |
| Add table, nullable column, column with default, index, NOT VALID constraint | **Before** deploying the code that uses it | Old code ignores it |
| Validate constraint, backfill | Any time after it's added | Online |
| Add NOT NULL or a strict CHECK on a column that old code might leave NULL or invalid | **After** every writer sets the value | Otherwise old code's inserts fail |
| Rename column or table | Don't. Use expand and contract | Old code breaks instantly |
| Drop column or table | **After** deploying code that no longer uses it | Old code breaks instantly |
| Narrow a type or tighten a length | After all writers comply, via a new column | Old writes would fail |

If your pipeline runs migrations as a step before the deploy (common with ECS, EKS, App Runner, and Lambda), destructive changes need **two releases**: one to stop using the column, and one to drop it.

## Guardrails worth automating

- **Lint migrations in CI.** [Squawk](https://github.com/sbdchd/squawk) lints PostgreSQL SQL files from any tool that produces SQL: Prisma, Drizzle, Flyway, golang-migrate, Supabase, or raw SQL. Rails: [strong_migrations](https://github.com/ankane/strong_migrations). Django: [django-migration-linter](https://github.com/3YOURMIND/django-migration-linter), or the [django-pg-zero-downtime-migrations](https://github.com/tbicr/django-pg-zero-downtime-migrations) backend.
- **Set `lock_timeout` on the migration role** (see the top of this file).
- **Rehearse big migrations at production size.** An [Aurora clone](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Managing.Clone.html) is copy-on-write and quick to create. For RDS, restore a snapshot. Time each step.
- **Snapshot before destructive migrations**, and confirm your automated-backup retention period covers how long it might take you to notice a problem.
- **Keep migrations a separate pipeline step** from the app deploy, so before-deploy and after-deploy ordering is explicit.
