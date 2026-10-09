# Migration Health Check - Detailed Steps

## Purpose
Verify database migration safety, ordering, and rollback capability.

---

## Step 1: Locate Migration Files

| Tool | Migration Path | Detection |
|------|---------------|-----------|
| Prisma | `prisma/migrations/` | Timestamped folders with migration.sql |
| Alembic | `alembic/versions/` | Python files with upgrade()/downgrade() |
| Flyway | `db/migration/` | V{n}__{name}.sql |
| Knex | `migrations/` | Timestamped JS files |
| Rails | `db/migrate/` | Timestamped Ruby files |
| Django | `*/migrations/` | Python files with operations list |
| Liquibase | `db/changelog/` | XML/YAML/SQL changesets |
| TypeORM | `src/migrations/` | TypeScript files |

---

## Step 2: Analyze Migration Safety

| Check | Detection | Severity |
|-------|-----------|----------|
| DROP TABLE without backup | `DROP TABLE` in migration | 🔴 Critical |
| DROP COLUMN on production table | `ALTER TABLE ... DROP COLUMN` | 🟠 High |
| Non-reversible migration | Missing down/rollback function | 🟡 Medium |
| Large data migration | Data transform on >100K rows | 🟡 Medium |
| Lock-inducing operation | ALTER TABLE on busy table | 🟡 Medium |
| Missing index for new FK | ADD FOREIGN KEY without index | 🟡 Medium |
| Mixed schema + data | Schema and data changes in one migration | 🔵 Info |

---

## Step 3: Pending Migration Detection

```bash
# Check for unapplied migrations
# Prisma
npx prisma migrate status 2>/dev/null

# Alembic
alembic current 2>/dev/null
alembic heads 2>/dev/null

# Rails
rails db:migrate:status 2>/dev/null

# Knex
npx knex migrate:status 2>/dev/null
```

---

## Step 4: Generate Report

```markdown
## 🔄 Migration Health Report

### Summary
| Metric | Value |
|--------|-------|
| Total Migrations | {n} |
| Pending (unapplied) | {n} |
| Destructive Operations | {n} |
| Missing Rollbacks | {n} |

### Safety Concerns
| # | Migration | Operation | Risk | Mitigation |
|---|-----------|-----------|------|-----------|
| 1 | {name} | DROP COLUMN age | 🔴 | Add default, deploy, then drop |

### Best Practices Assessment
- [ ] All migrations have rollback/down functions
- [ ] Destructive ops use safe patterns (add new → migrate data → drop old)
- [ ] Large data migrations are batched
- [ ] No schema + data mixed in single migration
```
