# Schema Analysis - Detailed Steps

## Purpose
Analyze database schema design for structural issues, normalization problems, and type safety.

---

## Step 1: Locate Schema Definitions

Based on detected ORM/database:

| ORM/Tool | Schema Location | How to Parse |
|----------|----------------|-------------|
| Prisma | `prisma/schema.prisma` | Parse model blocks |
| TypeORM | `src/**/*.entity.ts` | Parse @Entity decorators |
| Sequelize | `src/models/*.js` | Parse define() calls |
| SQLAlchemy | `models/*.py` | Parse class(Base) definitions |
| Hibernate | `src/**/*.java` with @Entity | Parse JPA annotations |
| ActiveRecord | `db/schema.rb` or `db/migrate/*.rb` | Parse create_table blocks |
| Django | `*/models.py` | Parse Model class fields |
| Knex | `migrations/*.js` | Parse createTable calls |
| Raw SQL | `*.sql`, `migrations/*.sql` | Parse CREATE TABLE |
| Mongoose | `src/models/*.js` | Parse Schema definitions |

---

## Step 2: Extract Schema Structure

For each table/collection/model:
- Table name
- Columns with types
- Primary keys
- Foreign keys and relationships
- Indexes (declared at schema level)
- Constraints (NOT NULL, UNIQUE, CHECK, DEFAULT)

---

## Step 3: Run Schema Health Checks

### Relational Database Checks

| Check | Rule | Severity | Finding |
|-------|------|----------|---------|
| Primary Key | Every table has a PK | 🔴 Critical | Table `{t}` missing PK |
| Foreign Keys | References have FK constraints | 🟠 High | `{col}` references `{table}` without FK |
| NOT NULL | Required business fields are NOT NULL | 🟡 Medium | `{col}` allows NULL but is always required |
| Data Types | Appropriate types for data | 🟡 Medium | `{col}` uses VARCHAR(255) for boolean-like data |
| Wide Tables | Tables under 30 columns | 🟡 Medium | Table `{t}` has {n} columns — consider splitting |
| Naming | Consistent naming convention | 🔵 Info | Mixed camelCase and snake_case |
| Timestamps | audit columns present | 🔵 Info | Missing created_at/updated_at on `{t}` |
| Soft Delete | Consistent approach | 🔵 Info | Some tables use deleted_at, others hard delete |

### NoSQL Database Checks (MongoDB/DynamoDB)

| Check | Rule | Severity |
|-------|------|----------|
| Document Size | Documents under 16MB (Mongo) | 🔴 Critical |
| Unbounded Arrays | Arrays should have max size | 🟠 High |
| Access Patterns | Schema matches query patterns | 🟡 Medium |
| Denormalization | Appropriate for read patterns | 🟡 Medium |
| Partition Key | Proper key selection (DynamoDB) | 🔴 Critical |

---

## Step 4: Normalization Assessment

Assess normalization level:

| Level | Criteria | Status |
|-------|----------|--------|
| 1NF | Atomic values, no repeating groups | {pass/fail} |
| 2NF | No partial dependencies | {pass/fail} |
| 3NF | No transitive dependencies | {pass/fail} |

Flag:
- Under-normalized: Repeated data that causes update anomalies
- Over-normalized: Excessive JOINs for basic CRUD operations

---

## Step 5: Generate Report

```markdown
## 🗄️ Schema Analysis Report

### Summary
| Metric | Value |
|--------|-------|
| Tables/Collections | {n} |
| Total Columns | {n} |
| Missing PKs | {n} |
| Missing FKs | {n} |
| Missing NOT NULLs | {n} |
| Schema Health Score | {score}/100 |

### Critical Findings
{table of critical issues}

### Recommendations
{specific ALTER TABLE or migration recommendations}
```

---

## Completion
Output to `dep-analysis-docs/analysis/schema-analysis.md`
Proceed to Query Pattern Detection.
