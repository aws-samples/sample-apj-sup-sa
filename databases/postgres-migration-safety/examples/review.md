# Example review

What the skill produces for [`risky-migration.sql`](risky-migration.sql), given this context from the user: Aurora PostgreSQL 16; `orders` has about 25M rows (11 GB with indexes) and about 150 writes/s; `users` has 2M rows; ECS rolling deploys; `prisma migrate deploy` runs as a pre-deploy task.

---

## Migration review: `prisma/migrations/20261117093000_orders_v2/migration.sql`

**Verdict:** BLOCK. As written, this takes `orders` offline (reads and writes) for the length of an 11 GB table rewrite, blocks writes to `users`, and breaks the running app version mid-deploy.
**Assumed:** Prisma ORM 7.x. On 7.4+ each statement runs separately, so the file isn't atomic. On older versions, the file runs as one implicit transaction that holds every lock until the last statement finishes. The verdict is BLOCK either way.

### Findings

1. **BLOCK** `ADD COLUMN "public_id" UUID NOT NULL DEFAULT gen_random_uuid()` (line 8)
   - `gen_random_uuid()` is volatile, so PostgreSQL must **rewrite the whole table** to fill every row, holding ACCESS EXCLUSIVE. No reads or writes on `orders` until it finishes.
   - At 11 GB, expect many minutes of failed checkouts, and about 11 GB of extra storage during the rewrite.
   - Fix: add it nullable, `SET DEFAULT` for new rows, backfill, then NOT NULL via a CHECK (steps 1, 6–8).

2. **BLOCK** `ALTER COLUMN "total" SET DATA TYPE DECIMAL(12,2)` (line 10)
   - `double precision` → `numeric` is a **rewrite** (it's part of the same statement, so the same outage as finding 1).
   - Also a behavior change: values are rounded to 2 decimal places, and anything ≥ 10,000,000,000 fails the whole migration.
   - Fix: new `total_amount numeric(12,2)` column, dual-write, backfill, switch reads, then drop `total` in a later release.

3. **WARN** `ADD COLUMN "status" TEXT NOT NULL DEFAULT 'pending'` (line 9)
   - Locking: a constant default is instant on PG11+, so it's safe once split out of the rewriting statement and run under `lock_timeout`.
   - Correctness: **all 25M historical orders become `pending`**. If a worker, report, or UI acts on pending orders, that's an incident. Decide the right value for existing rows and backfill it.

4. **BLOCK** `RENAME COLUMN "user_id" TO "customer_id"` (line 13)
   - Instant for the database, but during the rolling deploy the old ECS tasks are still running. Prisma lists every column by name in its `SELECT` and `RETURNING` clauses, so **every** `orders` query from an old task fails with `column "user_id" does not exist`, not only queries that use the field. A deploy rollback fails the same way in reverse.
   - Fix: don't rename the column. Rename the field in Prisma only: `customerId BigInt @map("user_id")`.

5. **BLOCK** `CREATE UNIQUE INDEX "orders_public_id_key"` (line 16) and `CREATE INDEX "orders_customer_id_idx"` (line 19)
   - Plain `CREATE INDEX` takes a SHARE lock, which **blocks every insert, update, and delete** on `orders` for each 25M-row build.
   - Fix: `CREATE INDEX CONCURRENTLY`, each in its own migration file containing only that statement.

6. **BLOCK** `ADD CONSTRAINT "orders_customer_id_fkey" FOREIGN KEY ... REFERENCES "users"` (line 22)
   - Validates all 25M rows while holding SHARE ROW EXCLUSIVE on **both** `orders` and `users`. Signups and profile updates are blocked too.
   - The key is new, so orphaned `user_id` values may exist. If they do, the migration fails at the very end, after the whole outage. On Prisma 7.4+ the earlier statements (the rewrite and the rename) stay applied. Prisma marks the migration failed, which blocks later deploys until someone runs `prisma migrate resolve`.
   - `ON DELETE RESTRICT` is new behavior: deleting a user who has orders will start failing. Check the account-deletion path.
   - Fix: run the orphan check below, add the key `NOT VALID` (instant, still enforced for new rows), then `VALIDATE CONSTRAINT` in a separate migration (reads and writes continue).

7. **WARN** No `lock_timeout` anywhere in the file
   - The first `ALTER` waits behind any long-running query on `orders`, and every new query queues behind it, so the outage can start before the rewrite does.

### Safe plan

| Step | Change | When | Rollback |
| --- | --- | --- | --- |
| 1 | Migration: add `public_id` (nullable) + default, `status`, `total_amount` (decide the backfill value for historical `status` first) | Release 1, before deploy | `DROP COLUMN` each |
| 2 | Migration: `CREATE INDEX CONCURRENTLY orders_user_id_idx` (alone in its file) | Release 1, before deploy | `DROP INDEX CONCURRENTLY` |
| 3 | Migration: FK `NOT VALID` | Release 1, before deploy | `DROP CONSTRAINT` |
| 4 | Migration: `VALIDATE CONSTRAINT` | Release 1, before deploy | n/a (no change on failure) |
| 5 | Deploy app v2: Prisma schema below; writes `totalAmount` alongside `total` | Release 1 | Redeploy v1 (schema is compatible) |
| 6 | Batched backfill job for `public_id` and `total_amount` | After v2 is fully rolled out | Idempotent; just stop it |
| 7 | Migration: `CREATE UNIQUE INDEX CONCURRENTLY orders_public_id_key` (alone) | Release 2 | `DROP INDEX CONCURRENTLY` |
| 8 | Migrations: NOT NULL for `public_id` and `total_amount` via CHECK → VALIDATE → SET NOT NULL | Release 2 | `DROP NOT NULL` |
| 9 | Deploy app v3: reads `totalAmount` | Release 2 | Redeploy v2 |
| 10 | Deploy app v4 without `total`, then migration `DROP COLUMN "total"` | Release 3, after deploy | Restore from snapshot (data loss) |

**Step 1** `prisma/migrations/20261117093000_orders_v2_columns/migration.sql`

```sql
SET lock_timeout = '5s';
ALTER TABLE "orders" ADD COLUMN "public_id" UUID;
ALTER TABLE "orders" ALTER COLUMN "public_id" SET DEFAULT gen_random_uuid();
ALTER TABLE "orders" ADD COLUMN "status" TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE "orders" ADD COLUMN "total_amount" DECIMAL(12,2);
```

**Step 2** `prisma/migrations/20261117093100_orders_user_id_idx/migration.sql`. Create it with `prisma migrate dev --create-only`; the file must contain only this statement:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS "orders_user_id_idx" ON "orders"("user_id");
```

**Steps 3 and 4** (two separate migration files). Run the orphan check first (read-only, on a reader); it must return 0 before step 4:

```sql
SELECT count(*) FROM orders o
WHERE o.user_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id);
```

```sql
SET lock_timeout = '5s';
ALTER TABLE "orders" ADD CONSTRAINT "orders_user_id_fkey"
  FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE NOT VALID;
```

```sql
ALTER TABLE "orders" VALIDATE CONSTRAINT "orders_user_id_fkey";
```

**Step 5** Prisma schema. The names match the SQL above, so `prisma migrate dev` sees no drift.

```prisma
model Order {
  id          BigInt   @id @default(autoincrement())
  customerId  BigInt   @map("user_id")
  customer    User     @relation(fields: [customerId], references: [id])
  publicId    String?  @default(dbgenerated("gen_random_uuid()")) @map("public_id") @db.Uuid
  status      String   @default("pending")
  total       Float
  totalAmount Decimal? @map("total_amount") @db.Decimal(12, 2)

  @@index([customerId])
  @@map("orders")
}
```

**Step 6** Backfill. Use the batched procedure from `references/safe-patterns.md` with this update:

```sql
UPDATE orders
SET    public_id    = coalesce(public_id, gen_random_uuid()),
       total_amount = coalesce(total_amount, round(total::numeric, 2))
WHERE  id >= next_id AND id < next_id + batch_size
  AND  (public_id IS NULL OR total_amount IS NULL);
```

Watch `AuroraReplicaLag` and write latency while it runs.

**Step 8** Three migrations each for `public_id` and `total_amount`: add `CHECK (... IS NOT NULL) NOT VALID`; `VALIDATE`; then `SET NOT NULL` and drop the CHECK. Then make both fields required in the Prisma schema (`publicId String @unique ...`, `totalAmount Decimal ...`).

### Data model notes (advisory)

- `status` is free text. Add `CHECK (status IN ('pending', 'paid', 'shipped', 'cancelled')) NOT VALID` and validate it, or use a lookup table, so typos can't reach the database.
- Confirm `orders.id` and `users.id` are `bigint`. If either is `int`/`serial`, check sequence headroom now (`references/preflight-queries.md`).
- Recommend adding [Squawk](https://github.com/sbdchd/squawk) to CI for `prisma/migrations/**/*.sql`. It flags most of these patterns automatically on every PR.
