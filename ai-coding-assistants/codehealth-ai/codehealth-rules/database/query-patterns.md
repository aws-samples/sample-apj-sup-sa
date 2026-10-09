# Query Pattern Detection - Detailed Steps

## Purpose
Scan codebase for database query patterns and identify performance/security issues.

---

## Step 1: Locate Query Sites

Based on detected tech stack, find all locations where DB queries are made:

### ORM Query Patterns to Search

| ORM | Pattern to Find | File Pattern |
|-----|-----------------|-------------|
| Prisma | `prisma.{model}.find*`, `prisma.$queryRaw` | `**/*.ts` |
| TypeORM | `repository.find*`, `createQueryBuilder`, `query()` | `**/*.ts` |
| Sequelize | `Model.find*`, `sequelize.query()` | `**/*.{js,ts}` |
| SQLAlchemy | `session.query()`, `session.execute()`, `select()` | `**/*.py` |
| Django ORM | `.objects.filter()`, `.objects.get()`, `raw()` | `**/*.py` |
| Hibernate | `@Query`, `createQuery`, `createNativeQuery` | `**/*.java` |
| ActiveRecord | `.where()`, `.find_by()`, `.find_by_sql()` | `**/*.rb` |
| Knex | `knex('table').select()`, `knex.raw()` | `**/*.{js,ts}` |
| Mongoose | `Model.find()`, `Model.aggregate()` | `**/*.{js,ts}` |

### Raw SQL Patterns

```bash
# Search for raw SQL in codebase
grep -rn "SELECT\|INSERT\|UPDATE\|DELETE\|CREATE\|ALTER\|DROP" --include="*.{ts,js,py,java,rb,go,rs}" .
grep -rn "query\|execute\|raw\|sql" --include="*.{ts,js,py,java,rb,go}" .
```

---

## Step 2: N+1 Query Detection

The most common and impactful anti-pattern. Detect by finding:

### Pattern: Loop containing database call

```
# Pseudocode detector logic:
FOR each code block containing a loop (for, forEach, map, while):
  IF loop body contains a database call (find, query, fetch):
    FLAG as N+1 candidate
    SEVERITY = based on:
      - Is this in a request handler? → 🔴 Critical
      - Is this in a background job? → 🟡 Medium
      - Is the collection typically small (<10)? → 🔵 Info
```

### Examples by Framework:

**Prisma N+1:**
```typescript
// BAD: N+1
const users = await prisma.user.findMany();
for (const user of users) {
  const posts = await prisma.post.findMany({ where: { userId: user.id } });
}

// GOOD: Eager loading
const users = await prisma.user.findMany({ include: { posts: true } });
```

**Django N+1:**
```python
# BAD: N+1
for book in Book.objects.all():
    print(book.author.name)  # Lazy load per iteration

# GOOD: select_related
for book in Book.objects.select_related('author').all():
    print(book.author.name)
```

**SQLAlchemy N+1:**
```python
# BAD: N+1
users = session.query(User).all()
for user in users:
    print(user.orders)  # Lazy load per iteration

# GOOD: joinedload
users = session.query(User).options(joinedload(User.orders)).all()
```

---

## Step 3: Unbounded Query Detection

Find queries that fetch unlimited results:

| Pattern | Risk | Detection |
|---------|------|-----------|
| `findMany()` without `take` | Memory + timeout | High if table > 1000 rows |
| `.all()` without `.limit()` | Memory + timeout | High if table > 1000 rows |
| `SELECT *` without `LIMIT` | Memory + timeout | High |
| Missing pagination on list APIs | Client timeout | High |

---

## Step 4: SQL Injection Detection

Find queries built with string concatenation:

```bash
# String interpolation in queries (dangerous)
grep -rn "f\".*SELECT\|f\".*INSERT\|f\".*UPDATE\|f\".*DELETE" --include="*.py" .
grep -rn '`.*\$\{.*SELECT\|`.*\$\{.*INSERT' --include="*.{ts,js}" .
grep -rn "\"SELECT.*\" \+ \|\"INSERT.*\" \+" --include="*.{java,cs}" .
```

**Always flag as 🔴 Critical** — cross-reference with Phase 2 security scan.

---

## Step 5: Query Complexity Analysis

For complex queries, assess:

| Metric | Threshold | Severity |
|--------|-----------|----------|
| JOIN count | > 4 tables | 🟡 Medium (review needed) |
| Subquery depth | > 2 levels nested | 🟡 Medium |
| DISTINCT usage | Any | 🔵 Info (may indicate bad joins) |
| OR chains | > 5 conditions | 🟡 Medium (index unfriendly) |
| LIKE with leading % | Any | 🟡 Medium (full scan) |
| Functions on indexed columns | Any | 🟠 High (prevents index use) |

---

## Step 6: Generate Findings Report

```markdown
## 🔍 Query Pattern Analysis Report

### Summary
| Finding Type | Count | Critical | High | Medium |
|-------------|-------|----------|------|--------|
| N+1 Queries | {n} | {n} | {n} | {n} |
| Unbounded Queries | {n} | — | {n} | {n} |
| SQL Injection Risk | {n} | {n} | — | — |
| Complex Queries | {n} | — | {n} | {n} |
| Missing Pagination | {n} | — | {n} | — |

### Detailed Findings

#### 🔴 N+1 Query: {file}:{line}
**Code:**
\```{language}
{code snippet}
\```
**Impact:** {estimated additional queries per request}
**Fix:**
\```{language}
{fixed code}
\```

---
```

---

## Step 7: Present for Approval

This is an approval gate. Present findings and ask user if they want to:
- A) Continue to Index Analysis
- B) View more detail on specific findings
- C) Skip remaining database analysis

---

## Completion
Output to `dep-analysis-docs/analysis/query-patterns.md`
