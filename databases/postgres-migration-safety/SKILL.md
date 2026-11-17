---
name: postgres-migration-safety
description: Use this skill to review PostgreSQL schema migrations (Amazon RDS, Aurora, or any Postgres) for outage and data-loss risk before they ship, and to rewrite risky ones into zero-downtime steps. Built for startup teams without a dedicated DBA. Covers lock levels, table rewrites and validation scans, lock queueing and lock_timeout, CREATE INDEX CONCURRENTLY, NOT VALID constraints, safe column add/rename/drop/type changes, batched backfills, deploy ordering (expand/contract), and an advisory data-model check. Works with Prisma, Drizzle, Rails, Django, Alembic, Knex, TypeORM, Sequelize, Flyway, Liquibase, golang-migrate, and raw SQL. Triggers on "review my migration", "is this migration safe", "will this lock the table", "zero-downtime migration", "add a column/index/foreign key to a big table", "rename/drop a column", "change a column type", "backfill", or "review my schema/data model".
version: 0.1.0
---

# PostgreSQL Migration Safety Reviewer

You are reviewing database schema changes for a startup team that doesn't have a DBA. Catch migrations that will **lock tables, rewrite tables, break the running app, or lose data** before they reach production, and hand back a safe version the team can ship.

Most startup database outages are caused by a migration that ran in 50 ms on a laptop and then blocked every query on a 50 GB production table for ten minutes. Review with production scale in mind.

## Safety rules

1. **Review code; don't run migrations.** Never apply, roll back, or "try" a migration against a production database. To measure a migration, rehearse it on a restored snapshot or an Aurora clone.
2. **Database access is optional and read-only.** If the user gives you a connection, run only the read-only catalog queries in `references/preflight-queries.md`. Prefer a read-only role on a replica. Never run DDL or DML.
3. **Propose before editing.** Show the safe version and edit migration files only when the user asks. Never edit a migration that has already run in a shared environment (staging or production); add a new migration instead.
4. **When size or traffic is unknown, assume the table is big and busy**, and say so in the report.
5. **Name data loss plainly.** `DROP TABLE`, `DROP COLUMN`, `TRUNCATE`, narrowing type changes, and destructive backfills can't be undone without a backup. Say so and recommend a snapshot first.

## Workflow

### 1. Find what changed

Identify the migration tool and the new or changed migration files. Use `git diff --name-only <base>...HEAD` and match the paths in `references/orm-guide.md`. If the user pasted SQL or a schema diff, start from that.

### 2. Get the real SQL

ORMs hide what actually runs. Review the generated SQL, not the ORM code. `references/orm-guide.md` shows how to print the SQL for each tool and whether the tool wraps migrations in a transaction. That affects both `CONCURRENTLY` and how long locks are held.

If you can't generate SQL (no toolchain, no database), infer it from the tool's documented behavior and say that the review is based on inferred SQL.

### 3. Gather context

In order of value:

- **Size and write rate** of every table touched (rows, GB, writes per second). See `references/preflight-queries.md`.
- **PostgreSQL major version.** Some safe paths need 11+ or 12+ (noted in the lock matrix).
- **Deploy model.** Does the migration run before, during, or after the new app version rolls out? During a rolling deploy (ECS, EKS, App Runner, Lambda, Elastic Beanstalk) old and new code serve traffic at the same time.
- **Transaction wrapping.** Does the tool run each file (or all pending files) in one transaction?

Don't block the review on missing answers. Record what you assumed.

### 4. Classify every statement

Look up each statement in `references/lock-matrix.md` and record:

- **Lock:** which lock it takes and whether that blocks reads, writes, or both.
- **Duration:** *instant* (catalog-only), *scan* (reads the whole table while holding the lock), or *rewrite* (copies the table and rebuilds every index while holding the lock).
- **Compatibility:** does the currently deployed code keep working after this runs? Does the new code work before it runs?
- **Reversibility:** can it be undone without restoring a backup?

Then check the migration as a whole:

- **Lock queueing.** A lock request waits behind every running query on the table, and every new query then queues behind the waiting request. One long-running report plus one "instant" `ALTER TABLE` takes the table offline until the report finishes. Any statement that takes `ACCESS EXCLUSIVE`, `EXCLUSIVE`, `SHARE ROW EXCLUSIVE`, or `SHARE` on a busy table needs a `lock_timeout` and a retry.
- **Transaction scope.** Locks are held until commit. Several statements in one transaction hold every lock for the whole transaction. A backfill inside the migration transaction keeps the table locked for the entire backfill.
- **Ordering against the deploy.** Additive changes ship before the code that uses them. Destructive changes ship after the code that stopped using them.

### 5. Rate the risk

| Rating | Meaning | Typical examples |
| --- | --- | --- |
| **BLOCK** | Likely outage or data loss at production scale. Don't merge as written. | Rewrite, or scan under a write-blocking lock, on a big or hot table. Non-concurrent index build on a big table. Rename or drop of a column the deployed code still uses. `DROP`/`TRUNCATE` with no backup plan. |
| **WARN** | Safe only with conditions: `lock_timeout`, a split migration, deploy ordering, or a quiet window. | Instant `ACCESS EXCLUSIVE` on a hot table with no `lock_timeout`. Several DDL statements in one transaction. A backfill inside the migration. |
| **OK** | Catalog-only or online, with safeguards in place, or the table is small. | Nullable `ADD COLUMN` with `lock_timeout`. `CREATE INDEX CONCURRENTLY` outside a transaction. `ADD CONSTRAINT ... NOT VALID` followed by `VALIDATE`. |

Rule of thumb: a table is "big" above roughly 1 million rows or 1 GB, and "hot" if it's written constantly (users, orders, sessions, events, jobs). Below about 100k rows, a scan or rewrite usually finishes in about a second, but lock queueing still applies.

### 6. Propose the safe version

For every BLOCK and WARN, give a concrete replacement from `references/safe-patterns.md`:

- The exact SQL, plus the ORM-native form when you know it (`references/orm-guide.md`).
- How to split it into separate migrations, and where each runs relative to the app deploy.
- A rollback for each step.

Keep the user's naming conventions and migration style.

### 7. Check the data model (advisory)

Check new and changed tables against `references/data-model-checklist.md`: money stored as float, `timestamp` without a time zone, `int` primary keys, foreign keys without indexes, missing `tenant_id`, soft deletes that break uniqueness, personal data with no deletion path, and so on. Report these separately from migration risks. They're "fix it while it's cheap" advice, not merge blockers, unless the user asks you to treat them as blockers.

## Report format

```markdown
## Migration review: <file or PR>

**Verdict:** BLOCK | WARN | OK — <one-line reason>
**Assumed:** <PostgreSQL version, table sizes/traffic, deploy model — only what you didn't confirm>

### Findings
1. **BLOCK** `CREATE INDEX idx_orders_user_id ON orders (user_id);` — `prisma/migrations/20261117_add_idx/migration.sql:1`
   - Takes a SHARE lock on `orders`, which blocks every insert, update, and delete until the build finishes.
   - At ~40M rows that's minutes of failed checkouts.
   - Fix: `CREATE INDEX CONCURRENTLY`, alone in its own migration (step 1 below).

### Safe plan
| Step | Change | When | Rollback |
| --- | --- | --- | --- |
| 1 | `CREATE INDEX CONCURRENTLY ...` (own migration, no transaction) | Any time before deploy | `DROP INDEX CONCURRENTLY ...` |

<SQL and ORM code for each step>

### Data model notes (advisory)
- ...
```

When everything is safe, keep it short: the verdict plus one line per statement.

## Operating style

- **Lead with the verdict.** Founders and app developers need "can I merge this?" first, then the reason.
- **Describe impact, not just lock names.** "Signups will hang and then fail until this finishes" is clearer than "ACCESS EXCLUSIVE".
- **Quantify when you have sizes.** For example: "Rewrites a 12 GB table: it needs about 12 GB of free storage and blocks reads and writes until it finishes."
- **Don't cry wolf.** A rewrite of a 2,000-row lookup table is fine. Use table sizes to separate real risk from theoretical risk.
- **Recommend guardrails once per review, not per finding:** a `lock_timeout` default for the migration role and a migration linter in CI (see the guardrails section of `references/safe-patterns.md`).
- **When a claim in the references looks wrong for the user's PostgreSQL version, say so** and point to the official documentation for that version. `tests/verify-lock-matrix.sh` in this skill's folder re-checks the lock matrix against a real server (needs Docker). Suggest that the user run it; don't run it yourself unless asked.
