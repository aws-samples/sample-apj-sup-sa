# Data Access Anti-Pattern Detection - Detailed Steps

## Purpose
Detect architectural-level database access anti-patterns beyond individual queries.

---

## Step 1: Anti-Pattern Catalog

### N+1 Queries (Covered in query-patterns.md — cross-reference)
- Loop containing individual DB calls
- Lazy loading in iteration context

### Chatty Service Pattern
**Detection:** Count DB calls per request handler
```
FOR each request handler (route, controller method, resolver):
  COUNT database operations within the handler chain
  IF count > 5:
    FLAG as "Chatty Service" 🟠 High
  IF count > 10:
    FLAG as "Extremely Chatty" 🔴 Critical
```

**Fix:** Batch queries, use DataLoader pattern, or restructure to fewer round-trips.

### Read-Your-Writes Consistency
**Detection:** Write followed by immediate read of same data without ensuring consistency
```typescript
// BAD: Race condition possible
await db.user.update({ where: { id }, data: { name } });
const user = await db.user.findUnique({ where: { id } }); // May get stale data
```
**Fix:** Use returned value from write operation, or ensure read-after-write consistency.

### Missing Pagination
**Detection:** List endpoints that call `findMany`/`find` without `take`/`limit`
**Fix:** Add cursor or offset pagination with sensible defaults.

### Fat Repository / God Query Pattern
**Detection:** Single query method that:
- Has >20 lines of query building
- Accepts >5 parameters controlling query shape
- Uses excessive conditional query composition

**Fix:** Split into specific query methods per use case.

### Missing Transaction Boundaries
**Detection:** Multiple write operations that should be atomic:
```typescript
// BAD: Partial failure leaves inconsistent state
await db.order.create({ ... });
await db.inventory.update({ ... }); // If this fails, order exists without inventory reduction
await db.payment.create({ ... });
```
**Fix:** Wrap in transaction:
```typescript
await db.$transaction([
  db.order.create({ ... }),
  db.inventory.update({ ... }),
  db.payment.create({ ... }),
]);
```

### No Caching Layer
**Detection:**
- Same expensive query executed on every request
- No caching middleware or in-memory cache
- Reference data (countries, categories) fetched from DB per request

**Fix:** Add caching layer (Redis, in-memory) for frequently-read, rarely-changed data.

### Connection Leak
**Detection:**
- Manual connection creation without matching close/release
- Missing `finally` block on connection operations
- Connection used across async boundaries without pool

### Soft Delete Without Proper Handling
**Detection:**
- `deleted_at`/`is_deleted` column exists
- Queries do NOT consistently filter on it
- Some queries return deleted records unintentionally

---

## Step 2: Architectural Assessment

Beyond individual anti-patterns, assess the overall data access architecture:

| Aspect | Assessment | Score |
|--------|-----------|-------|
| Separation of Concerns | DB logic in controllers vs dedicated layer | {1-5} |
| Query Reusability | Shared query methods vs inline queries | {1-5} |
| Transaction Management | Explicit boundaries for multi-write ops | {1-5} |
| Error Handling | DB errors properly caught and translated | {1-5} |
| Testing | DB queries covered by integration tests | {1-5} |
| Observability | Query logging, slow query tracking | {1-5} |

---

## Step 3: Generate Report

```markdown
## 🏗️ Data Access Architecture Report

### Anti-Patterns Found
| # | Pattern | Severity | Location | Impact |
|---|---------|----------|----------|--------|
| 1 | N+1 Query | 🔴 | {file}:{line} | {n} extra queries/request |
| 2 | Chatty Service | 🟠 | {handler} | {n} DB calls/request |
| 3 | Missing Transaction | 🟠 | {file}:{line} | Data inconsistency risk |

### Architecture Score: {total}/30

### Recommended Improvements
1. {highest impact fix with code example}
2. {second highest impact fix}
3. {third fix}
```

---

## Completion
Output to `dep-analysis-docs/analysis/data-access-patterns.md`
