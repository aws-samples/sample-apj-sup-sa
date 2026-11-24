# Database Optimization Plan - Detailed Steps

## Purpose
Generate actionable database optimization recommendations from Phase 3 findings.

---

## Step 1: Index Recommendations

For each missing index identified:

```markdown
### Index: {table}.{columns}

**Impact**: Improves query at {file}:{line} — estimated {x}ms → {y}ms
**Risk**: Low (new index, no data modification)

\```sql
-- Create index
CREATE INDEX CONCURRENTLY idx_{table}_{columns}
ON {table} ({column1}, {column2})
{WHERE clause if partial};

-- Verify usage after deployment
SELECT * FROM pg_stat_user_indexes WHERE indexrelname = 'idx_{table}_{columns}';
\```
```

---

## Step 2: Query Rewrites

For each problematic query:

```markdown
### Fix: N+1 in {file}:{function}

**Before (N+1 — {n} queries per request):**
\```{language}
{original code}
\```

**After (Single query with eager load):**
\```{language}
{optimized code}
\```

**Expected Improvement**: {n} DB calls → 1-2 calls ({percentage}% reduction)
```

---

## Step 3: Caching Strategy

If missing caching identified:

| Data Pattern | Cache Type | TTL | Invalidation |
|-------------|-----------|-----|-------------|
| User session | Redis/Memory | 15min | On logout/update |
| Reference data | Application memory | 1hr | On deployment |
| Expensive aggregation | Redis | 5min | On source data change |
| API response | HTTP cache headers | Varies | ETag/Last-Modified |

---

## Step 4: Connection Pool Tuning

If pool issues found:

```markdown
### Recommended Pool Configuration

\```{language}
{pool configuration code with comments explaining each value}
\```

**Rationale:**
- Max size: {n} (based on {deployment_model} with {instances} instances)
- Idle timeout: {n}ms (release unused connections after {seconds}s)
- Connection timeout: {n}ms (fail fast, don't hang)
```

---

## Step 5: Priority and Effort

| # | Optimization | Type | Impact | Effort | Priority |
|---|-------------|------|--------|--------|----------|
| 1 | Fix N+1 in {handler} | Query | -{x}ms latency | Low | P2 |
| 2 | Add index on {table} | Index | -{x}ms query time | Low | P2 |
| 3 | Add Redis cache for {data} | Architecture | -{n} DB calls/min | Medium | P3 |
| 4 | Tune connection pool | Config | -timeout errors | Low | P3 |

---

## Completion
Output to `dep-analysis-docs/optimization/database-plan.md`
