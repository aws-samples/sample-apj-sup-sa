# Data-model checklist (advisory)

Schema choices that are cheap to fix in a new table and expensive to fix in a big one. Report these as **advisory**, separate from migration risk, unless the user asks otherwise. For each item: what to look for, why it hurts later, and the fix.

## Types

1. **Money in `real` / `double precision` / `float`.** Binary floating point can't represent 0.10 exactly, so totals drift by cents. Use `numeric(12,2)` (or wider), or a `bigint` count of minor units (cents) with a currency column. ORM defaults to watch: Prisma `Float`, Django `FloatField`, Drizzle `doublePrecision`.
2. **`timestamp` without a time zone.** Values carry no offset, so servers or clients in different zones disagree on what they mean. Use `timestamptz`, which stores UTC and converts on display. Defaults to watch: through Prisma 7, `DateTime` maps to `timestamp(3)` (use `@db.Timestamptz(3)`). Prisma 8 defaults to `timestamptz(3)`, so on existing tables a plain `DateTime` now describes a different column type unless you write `DateTime(storage: timestamp)`. Drizzle `timestamp()` (use `{ withTimezone: true }`); Rails `datetime` (set `datetime_type = :timestamptz` on Rails 7+). Fixing it later is instant only if the existing values are UTC (`lock-matrix.md`).
3. **32-bit `int` / `serial` primary and foreign keys.** These overflow at 2,147,483,647 rows (or sequence values, including gaps from rollbacks and retries), and converting a big table later is a multi-step project. Use `bigint` (`bigint GENERATED ALWAYS AS IDENTITY` or `bigserial`) from day one, and use `bigint` for every column that references one.
4. **UUID primary keys.** Fine, but store them as `uuid` (16 bytes), not `text` (37+ bytes). Random UUIDv4 values scatter inserts across the index, which hurts on large, write-heavy tables. Time-ordered UUIDv7 avoids that: PostgreSQL 18 has a built-in `uuidv7()`, and on older versions you can generate them in the application.
5. **`varchar(255)` everywhere.** PostgreSQL has no performance benefit from a length limit, and changing it later can rewrite the table (`text` → `varchar(n)` does). Use `text`, and add `CHECK (char_length(col) <= n)` only where a real limit exists. A CHECK can be changed online with NOT VALID.
6. **Enums for sets that will change.** You can add enum values, but you can't remove them without rewriting every column that uses the type. Use an enum for truly fixed sets. Otherwise use `text` plus a CHECK constraint, or a lookup table with a foreign key.
7. **Nullable booleans.** They give you three states (`true` / `false` / unknown) that every query has to handle. Use `NOT NULL DEFAULT false` unless "unknown" is a real state.
8. **JSONB as the main data model.** Good for sparse, schemaless attributes. Bad for fields you filter, join, sort, or need constraints on. Promote those to real columns. If you query JSONB containment (`@>`), add a GIN index.

## Keys, constraints, and indexes

9. **Foreign key columns without an index.** PostgreSQL doesn't index the referencing column automatically, and neither do Prisma, Drizzle, or Knex. Every `DELETE` (or key `UPDATE`) on the parent scans the child table, and joins suffer. Add an index on each foreign key column you join or delete by. `preflight-queries.md` has a query that finds them.
10. **Missing foreign keys.** Without them, orphaned rows accumulate silently. Add them, with the NOT VALID pattern on existing tables.
11. **`ON DELETE CASCADE` on large trees.** Deleting one parent can delete millions of rows in a single transaction. Prefer `RESTRICT` plus explicit, batched cleanup for big child tables.
12. **Case-sensitive uniqueness on emails and usernames.** Without it, `Alice@x.com` and `alice@x.com` become two accounts. Use `CREATE UNIQUE INDEX ... ON users (lower(email))` and query with `lower(email) = lower($1)`, or use the `citext` extension.
13. **Soft deletes (`deleted_at`) with plain unique constraints.** A deleted row still blocks re-signup with the same email. Use a partial unique index (`... WHERE deleted_at IS NULL`). Every query must filter on `deleted_at`, and soft-deleted rows are still personal data.
14. **Missing `created_at` / `updated_at`.** You can't reconstruct them later, and they're invaluable for debugging and backfills. Add `created_at timestamptz NOT NULL DEFAULT now()`, and set `updated_at` in the app or with a trigger.

## Multi-tenant SaaS

15. **A `tenant_id` (or `account_id` / `org_id`) on every tenant-owned table**, `NOT NULL`, as the leading column of the main indexes, and included in unique constraints (`UNIQUE (tenant_id, email)`). Adding it to a big table later means an expand-and-contract migration plus a backfill.
16. **Cross-tenant references.** A row in tenant A can point at a row in tenant B if foreign keys reference `id` alone. Use composite foreign keys, `(tenant_id, user_id) REFERENCES users (tenant_id, id)`, which need a unique constraint on `users (tenant_id, id)`.
17. **Row-level security as a backstop.** RLS policies such as `USING (tenant_id = current_setting('app.tenant_id')::bigint)` stop a missing `WHERE` from leaking data. The table owner and superusers bypass RLS unless you `ALTER TABLE ... FORCE ROW LEVEL SECURITY`, so the app should connect as a non-owner role.

## Personal data and secrets

18. **Know where personal data lives.** Keep a list (or `COMMENT ON COLUMN`) of columns holding personal data: names, emails, phone numbers, addresses, IPs, free-text fields. You'll need it for deletion requests under privacy laws (for example GDPR, and APJ laws such as Australia's Privacy Act, Singapore's PDPA, Japan's APPI, and India's DPDP Act), and for security reviews. This is engineering hygiene, not legal advice.
19. **A deletion path.** Decide how a user's data is deleted or anonymized across every table, including soft-deleted rows, audit tables, and analytics copies.
20. **Secrets in plaintext.** Store API tokens and password-reset tokens as hashes, never raw. Hash passwords with a password-hashing algorithm (argon2, bcrypt, or scrypt) in the application.
21. **Encryption at rest.** On RDS and Aurora, storage encryption can only be enabled when the instance or cluster is created. Encrypting an existing unencrypted database means restoring from an encrypted copy of a snapshot. Turn it on at creation.

## Growth

22. **Append-only tables with no retention plan** (events, logs, audit). They grow forever, and mass `DELETE`s cause bloat. Partition by time, and drop old partitions (instant), or archive them to S3.
23. **Job and queue tables in PostgreSQL.** Workers that select the next job with a plain `SELECT ... FOR UPDATE` serialize on the same row. Use `FOR UPDATE SKIP LOCKED`, add a partial index on pending jobs, and delete or archive finished jobs.
24. **Quoted mixed-case identifiers** (`"User"`, `"createdAt"`, often from ORM defaults). Every hand-written query then needs the quotes. Map them to snake_case table and column names (Prisma `@@map` / `@map`).
