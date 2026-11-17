# PostgreSQL Migration Safety Reviewer (Agent Skill)

An [agent skill](https://docs.claude.com/en/docs/claude-code/skills) that reviews PostgreSQL schema migrations **before they ship**, flags the ones that will lock tables, rewrite tables, break the running app, or lose data, and rewrites them into zero-downtime steps.

It's built for startup teams that run Amazon RDS for PostgreSQL or Aurora PostgreSQL (or any PostgreSQL 12+) **without a dedicated DBA**. It works on the migration code in your repository, so it needs no database access.

## Why

Most self-inflicted database outages at startups come from a migration that ran in milliseconds on a laptop:

- `CREATE INDEX` on a busy table blocks every write until the build finishes.
- `ADD COLUMN ... DEFAULT gen_random_uuid()` rewrites the whole table and blocks reads and writes.
- Validating a foreign key blocks writes on **both** tables.
- Renaming or dropping a column breaks the old app version that is still serving traffic during a rolling deploy.
- Even an "instant" `ALTER TABLE` queues behind one long-running query, and then every other query queues behind it.

The skill knows which statements do what (verified against real PostgreSQL servers; see [Verification](#verification)), what your ORM actually generates, and how to split a risky change into safe steps.

## What it does

1. Finds the new migrations in your diff: Prisma, Drizzle, Rails, Django, Alembic, Knex, TypeORM, Sequelize, Flyway, Liquibase, golang-migrate, dbmate, Supabase CLI, or raw SQL.
2. Reviews the **generated SQL**, not the ORM code.
3. Rates each statement **BLOCK / WARN / OK** by lock level, duration (instant, scan, or rewrite), compatibility with the code that's already deployed, and reversibility, scaled by table size and traffic.
4. Proposes a safe plan: exact SQL and ORM code, how to split it into migrations, where each step runs relative to the deploy, and a rollback for each.
5. Adds advisory data-model notes: money stored as float, `timestamp` without a time zone, `int` primary keys, unindexed foreign keys, missing `tenant_id`, personal data with no deletion path, and more.

See [`examples/review.md`](examples/review.md) for a full review of the Prisma migration in [`examples/risky-migration.sql`](examples/risky-migration.sql).

## Install

The skill is the folder containing `SKILL.md`. Copy it to wherever your agent loads skills from.

```bash
git clone --filter=blob:none --sparse https://github.com/aws-samples/sample-apj-sup-sa.git
cd sample-apj-sup-sa
git sparse-checkout set databases/postgres-migration-safety

# Claude Code, for all your projects:
mkdir -p ~/.claude/skills
cp -r databases/postgres-migration-safety ~/.claude/skills/

# ...or for one project (commit it so your whole team gets it):
mkdir -p /path/to/your-app/.claude/skills
cp -r databases/postgres-migration-safety /path/to/your-app/.claude/skills/

# Kiro: copy into your workspace's .kiro/skills/ directory instead.
```

## Use

Ask in plain language from your app's repository:

- "Review the migrations in this branch."
- "Is `prisma/migrations/20261117_add_status/migration.sql` safe to run on production?"
- "I need to rename `users.name` to `full_name` with zero downtime."
- "Add an index on `orders.created_at`. The table has 40M rows."
- "Review our schema for things we'll regret at scale."

The more context you give, the sharper the verdict: table sizes, write rates, PostgreSQL version, and how your deploy runs migrations. If you give the agent a **read-only** connection, it can measure table sizes and look for long-running transactions itself, using the queries in [`references/preflight-queries.md`](references/preflight-queries.md).

## What's inside

```
postgres-migration-safety/
├── SKILL.md                         # Workflow, safety rules, rating rubric, report format
├── references/
│   ├── lock-matrix.md               # What each statement locks, and for how long (verified)
│   ├── safe-patterns.md             # Zero-downtime recipes, backfills, deploy ordering
│   ├── orm-guide.md                 # Per-tool: where migrations live, how to see SQL, traps
│   ├── preflight-queries.md         # Read-only sizing and blocking queries
│   └── data-model-checklist.md      # Advisory schema checks for growing startups
├── examples/
│   ├── risky-migration.sql          # A realistic Prisma migration that would cause an outage
│   └── review.md                    # The skill's review and safe plan for it
└── tests/
    ├── verify-lock-matrix.sh        # Re-verifies the claims against real PostgreSQL in Docker
    ├── safe-patterns-smoke.sql      # Runs every recipe in safe-patterns.md end to end
    └── expected-output.txt          # Baseline observed on PostgreSQL 14, 17, and 18
```

## Verification

Lock behavior is easy to get subtly wrong from memory, so the claims here are tested, not just written down. `tests/verify-lock-matrix.sh` starts a throwaway PostgreSQL container (**no ports are published**; everything runs over the container's local socket), then:

- runs each statement in [`lock-matrix.md`](references/lock-matrix.md) and records the locks taken, whether the table was rewritten, and whether it scanned;
- observes the locks that `CONCURRENTLY` operations wait for, and demonstrates lock queueing and `lock_timeout`;
- runs every recipe in [`safe-patterns.md`](references/safe-patterns.md) end to end with assertions;
- runs every query in [`preflight-queries.md`](references/preflight-queries.md) in a read-only transaction;
- diffs the results against [`tests/expected-output.txt`](tests/expected-output.txt).

```bash
# Requires Docker. Takes about a minute per version.
./tests/verify-lock-matrix.sh 14 17 18
```

The current baseline is identical on PostgreSQL 14.25, 17.12, and 18.7. Run it again when a new PostgreSQL major version ships: any difference shows exactly which row of the matrix needs review.

## Complementary tools

The skill explains and plans. A linter in CI catches regressions on every PR. Use both:

- [Squawk](https://github.com/sbdchd/squawk) lints PostgreSQL SQL files from any tool.
- [strong_migrations](https://github.com/ankane/strong_migrations) for Rails.
- [django-migration-linter](https://github.com/3YOURMIND/django-migration-linter) or [django-pg-zero-downtime-migrations](https://github.com/tbicr/django-pg-zero-downtime-migrations) for Django.

## Limitations

- **PostgreSQL only.** For MySQL on Aurora or RDS, see [`aurora-mysql-upgrade-agents`](../aurora-mysql-upgrade-agents/) and [`rds-mysql-upgrade-agents`](../rds-mysql-upgrade-agents/) for upgrade readiness. Online schema change in MySQL is a different problem.
- **Advice, not a guarantee.** Durations depend on your data, hardware, and traffic. For anything large, rehearse on an [Aurora clone](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Managing.Clone.html) or a restored RDS snapshot first, and time it.
- The skill reviews and proposes. It never runs migrations, and it only edits migration files when you ask it to.

## Security

- The skill doesn't need database access. If you grant it, use a **read-only** role (ideally on a read replica), and supply credentials through your normal secret mechanism (environment variables, AWS Secrets Manager, or [IAM database authentication](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.IAMDBAuth.html)), never pasted into a prompt.
- The test harness uses a random, never-printed password for its container, and publishes no ports.

## License

This sample is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file in the repository root.
