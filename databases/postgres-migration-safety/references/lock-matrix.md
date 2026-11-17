# Lock and rewrite matrix

What each common migration statement locks, and for how long. Every row marked ✔ was observed on PostgreSQL 14, 17, and 18 by `tests/verify-lock-matrix.sh`. Rows without a mark come from the PostgreSQL documentation.

## Reading this table

**Lock**: the strongest table lock the statement takes. What it blocks:

| Lock | Blocks plain `SELECT` | Blocks `INSERT`/`UPDATE`/`DELETE` | Blocks other DDL, `VACUUM`, `CREATE INDEX CONCURRENTLY` |
| --- | --- | --- | --- |
| `ACCESS EXCLUSIVE` | **yes** | **yes** | yes |
| `EXCLUSIVE` | no (`SELECT ... FOR UPDATE` is blocked) | **yes** | yes |
| `SHARE ROW EXCLUSIVE` | no | **yes** | yes |
| `SHARE` | no | **yes** | yes (but two `CREATE INDEX` builds can run together) |
| `SHARE UPDATE EXCLUSIVE` | no | no | yes |
| `ROW SHARE` | no | no | only `EXCLUSIVE` / `ACCESS EXCLUSIVE` |

**Duration**: how long the lock is held, once it's granted:

- **instant**: catalog change only. Milliseconds, regardless of table size.
- **scan**: reads the whole table to check existing rows. Scales with table size.
- **rewrite**: copies the whole table and rebuilds every index. Scales with table and index size, and needs about that much free disk.
- **build**: builds one index. Scales with table size.

**Every lock waits in a queue.** Even an *instant* `ACCESS EXCLUSIVE` must wait for every running query on the table, including plain `SELECT`s, to finish. While it waits, **every new query on the table queues behind it** ✔. That's how "a 2 ms ALTER" takes production down. Always set `lock_timeout` (✔ the statement then fails fast with `canceling statement due to lock timeout`) and retry.

Locks are held **until the transaction commits**, not until the statement finishes.

## ADD COLUMN

| Statement | Lock | Duration | Safer path |
| --- | --- | --- | --- |
| `ADD COLUMN c int` (nullable, no default) | ACCESS EXCLUSIVE | instant ✔ | Fine. Use `lock_timeout`. |
| `ADD COLUMN c int NOT NULL DEFAULT 0` (constant default) | ACCESS EXCLUSIVE | instant ✔ (PG11+) | Fine. Use `lock_timeout`. |
| `ADD COLUMN c timestamptz DEFAULT now()` (stable default) | ACCESS EXCLUSIVE | instant ✔ | Fine, but every existing row gets the same migration-time value. |
| `ADD COLUMN c uuid DEFAULT gen_random_uuid()` (volatile default; also `clock_timestamp()`, `random()`) | ACCESS EXCLUSIVE | **rewrite** ✔ | Add nullable, then `SET DEFAULT`, then backfill. |
| `ADD COLUMN c bigserial` / `serial` | ACCESS EXCLUSIVE | **rewrite** ✔ | Same as volatile default. |
| `ADD COLUMN c bigint GENERATED ALWAYS AS IDENTITY` | ACCESS EXCLUSIVE | **rewrite** ✔ | Same as volatile default. |
| `ADD COLUMN c text GENERATED ALWAYS AS (...) STORED` | ACCESS EXCLUSIVE | **rewrite** ✔ | Plain column maintained by a trigger or the app, plus a backfill. |
| `ADD COLUMN c bigint REFERENCES p (id)` (no default) | ACCESS EXCLUSIVE on table; SHARE ROW EXCLUSIVE on `p` | instant ✔ (an all-NULL column needs no FK check) | Fine. `lock_timeout` covers both tables. |
| `ADD COLUMN c bigint DEFAULT 42 REFERENCES p (id)` | as above | **scan** ✔ (the FK is checked) | Add without the default, then `SET DEFAULT`. |
| `ALTER COLUMN c SET DEFAULT ...` / `DROP DEFAULT` (including volatile) | ACCESS EXCLUSIVE | instant ✔ (affects future inserts only) | Fine. |

## NOT NULL

| Statement | Lock | Duration | Safer path |
| --- | --- | --- | --- |
| `ALTER COLUMN c SET NOT NULL` | ACCESS EXCLUSIVE | **scan** ✔ | `CHECK (c IS NOT NULL) NOT VALID` → `VALIDATE` → `SET NOT NULL` |
| `SET NOT NULL` when a validated `CHECK (c IS NOT NULL)` exists | ACCESS EXCLUSIVE | instant ✔ (PG12+) | This *is* the safe path. |
| `ALTER COLUMN c DROP NOT NULL` | ACCESS EXCLUSIVE | instant ✔ | Fine for the database. Check that old code tolerates NULLs. |

## CHECK and FOREIGN KEY constraints

| Statement | Lock | Duration | Safer path |
| --- | --- | --- | --- |
| `ADD CONSTRAINT ... CHECK (...)` | ACCESS EXCLUSIVE | **scan** ✔ | `NOT VALID`, then `VALIDATE` in a separate transaction. |
| `ADD CONSTRAINT ... CHECK (...) NOT VALID` | ACCESS EXCLUSIVE | instant ✔ (enforced for new rows immediately) | Safe path, step 1. |
| `ADD CONSTRAINT ... FOREIGN KEY ... REFERENCES p` | SHARE ROW EXCLUSIVE on **both** tables (writes blocked on both) | **scan** ✔ | `NOT VALID`, then `VALIDATE`. |
| `ADD CONSTRAINT ... FOREIGN KEY ... NOT VALID` | SHARE ROW EXCLUSIVE on both tables | instant ✔ (enforced for new rows) | Safe path, step 1. Use `lock_timeout`. |
| `VALIDATE CONSTRAINT` (CHECK) | SHARE UPDATE EXCLUSIVE | scan ✔, but **reads and writes continue** | Safe path, step 2. |
| `VALIDATE CONSTRAINT` (FK) | SHARE UPDATE EXCLUSIVE on table; ROW SHARE on referenced | scan ✔, but **reads and writes continue** | Safe path, step 2. |
| `DROP CONSTRAINT` | ACCESS EXCLUSIVE | instant ✔ | Fine. Use `lock_timeout`. |

## Indexes, UNIQUE, and PRIMARY KEY

| Statement | Lock | Duration | Safer path |
| --- | --- | --- | --- |
| `CREATE INDEX` / `CREATE UNIQUE INDEX` | SHARE (writes blocked) | **build** ✔ | `CREATE INDEX CONCURRENTLY` |
| `CREATE INDEX CONCURRENTLY` | SHARE UPDATE EXCLUSIVE ✔ | 2 scans, waits for running transactions | Can't run in a transaction block ✔, including a multi-statement string ✔. Can leave an INVALID index on failure. |
| `CREATE INDEX CONCURRENTLY IF NOT EXISTS` after a failed build | n/a | **skips the INVALID leftover and reports success** ✔ | Drop the invalid index first (`DROP INDEX CONCURRENTLY`), then rebuild. |
| `CREATE INDEX CONCURRENTLY` on a partitioned parent | n/a | **error** ✔ | `CREATE INDEX ... ON ONLY parent`, then `CONCURRENTLY` per partition, then `ALTER INDEX ... ATTACH PARTITION` ✔ |
| `ADD CONSTRAINT ... UNIQUE (...)` / `ADD PRIMARY KEY (...)` | **ACCESS EXCLUSIVE (reads blocked too)** | **build** ✔ | `CREATE UNIQUE INDEX CONCURRENTLY`, then `ADD CONSTRAINT ... USING INDEX` |
| `ADD CONSTRAINT ... UNIQUE USING INDEX idx` | ACCESS EXCLUSIVE | instant ✔ | Safe path, step 2. |
| `ADD PRIMARY KEY USING INDEX idx` on nullable columns | ACCESS EXCLUSIVE | **scan** ✔ (implicit SET NOT NULL) | Run the NOT NULL safe path first. |
| `DROP INDEX` | ACCESS EXCLUSIVE on the table ✔ | instant | `DROP INDEX CONCURRENTLY` |
| `DROP INDEX CONCURRENTLY` | SHARE UPDATE EXCLUSIVE ✔ | waits for running transactions | Can't run in a transaction block or on constraint-backed indexes. |
| `REINDEX INDEX idx` | SHARE on the table ✔ (writes blocked), ACCESS EXCLUSIVE on the index (queries that would use it block) | **build** ✔ | `REINDEX INDEX CONCURRENTLY` (PG12+) |
| `REINDEX INDEX CONCURRENTLY idx` | SHARE UPDATE EXCLUSIVE ✔ | build, waits for running transactions | Can't run in a transaction block. |

## Column type changes

All take ACCESS EXCLUSIVE.

| Change | Duration |
| --- | --- |
| `varchar(n)` → `varchar(m)`, m > n | instant ✔ |
| `varchar(n)` → `text` | instant ✔ |
| `numeric(p,s)` → `numeric(p2,s)`, p2 > p, same scale | instant ✔ |
| `timestamp` → `timestamptz` with session `TimeZone = 'UTC'` | instant ✔ (PG12+) |
| `timestamp` → `timestamptz` with any other `TimeZone` | **rewrite** ✔ |
| `int` → `bigint` | **rewrite** ✔ |
| `text` → `varchar(n)` | **rewrite** ✔ |
| `varchar(n)` → `varchar(m)`, m < n | **rewrite** ✔ |
| `numeric(p,s)` → different scale | **rewrite** ✔ |
| Anything with `USING`, or between unrelated types | **rewrite** |

Safer path for rewrites: new column, dual-write, backfill, switch reads, then drop the old column (`safe-patterns.md`).

## Rename, drop, truncate

| Statement | Lock | Duration | Risk |
| --- | --- | --- | --- |
| `RENAME COLUMN` / `RENAME TO` (table) | ACCESS EXCLUSIVE | instant ✔ | **Breaks deployed code instantly.** Use expand and contract, or rename only in the ORM. |
| `DROP COLUMN` | ACCESS EXCLUSIVE | instant ✔ (space reclaimed later) | Breaks deployed code that references it. **Data loss.** |
| `DROP TABLE` | ACCESS EXCLUSIVE | instant | **Data loss.** `CASCADE` also locks referencing tables. |
| `TRUNCATE` | ACCESS EXCLUSIVE | instant ✔ | **Data loss.** |

## Everything else

| Statement | Lock | Duration | Notes |
| --- | --- | --- | --- |
| `CREATE TRIGGER` | SHARE ROW EXCLUSIVE (writes blocked) | instant ✔ | Use `lock_timeout`. |
| `ALTER TABLE ... SET (autovacuum_...)` | SHARE UPDATE EXCLUSIVE | instant ✔ | Reads and writes continue. |
| `ALTER COLUMN ... SET STATISTICS` | SHARE UPDATE EXCLUSIVE | instant ✔ | Reads and writes continue. |
| `REFRESH MATERIALIZED VIEW mv` | ACCESS EXCLUSIVE on `mv` | full refresh ✔ | Readers of the view block. |
| `REFRESH MATERIALIZED VIEW CONCURRENTLY mv` | EXCLUSIVE on `mv` ✔ | full refresh | Readers continue. Needs a unique index on the view. |
| `ALTER TYPE e ADD VALUE 'x'` | no table lock ✔ | instant | In a transaction (PG12+), the new value can't be used until commit ✔. |
| `ATTACH PARTITION` | SHARE UPDATE EXCLUSIVE on parent, ACCESS EXCLUSIVE on the partition | **scan** of the partition ✔ | Instant ✔ if a CHECK constraint already proves the bounds. |
| `DETACH PARTITION` | ACCESS EXCLUSIVE on parent and partition ✔ | instant | Use `CONCURRENTLY` (PG14+). |
| `DETACH PARTITION ... CONCURRENTLY` | SHARE UPDATE EXCLUSIVE on parent ✔ | waits for running transactions | Can't run in a transaction block. |
| `VACUUM FULL`, `CLUSTER` | ACCESS EXCLUSIVE | **rewrite** | Not a migration step. To reclaim bloat online, use the `pg_repack` extension (available on RDS and Aurora PostgreSQL). |

## Autovacuum interaction

Statements that take SHARE UPDATE EXCLUSIVE or stronger (`CREATE INDEX CONCURRENTLY`, `VALIDATE CONSTRAINT`, every `ALTER TABLE`) conflict with a running `VACUUM` on the same table. A regular autovacuum cancels itself to let you through. An autovacuum running **to prevent transaction ID wraparound** won't, so your migration waits for it to finish. If a migration is stuck, check `pg_stat_activity` for `autovacuum: ... (to prevent wraparound)`.

## Further reading

- PostgreSQL documentation: [Explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html), [ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html) (each subcommand's notes state its lock level), and [CREATE INDEX: building indexes concurrently](https://www.postgresql.org/docs/current/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY).
