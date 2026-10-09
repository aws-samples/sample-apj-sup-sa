# Index Analysis - Detailed Steps

## Purpose
Identify missing indexes, unused indexes, and optimization opportunities.

---

## Step 1: Map Queries to Required Indexes

For each query identified in Query Pattern Detection:

1. Extract columns used in:
   - WHERE clauses (equality and range filters)
   - JOIN conditions (ON a.id = b.foreign_id)
   - ORDER BY clauses
   - GROUP BY clauses
2. Determine optimal index for each query
3. Compare against existing indexes (from schema/migrations)

---

## Step 2: Index Recommendation Rules

### Single-Column Indexes
- Every foreign key column should have an index
- Columns frequently used in WHERE with = operator
- Columns used in ORDER BY on large tables

### Composite Indexes
- Columns commonly used together in WHERE (e.g., `user_id + status`)
- Follow the "equality first, range last" rule for column ordering
- Most selective column first (highest cardinality)

### Covering Indexes
- If a query only needs columns that can all fit in an index
- Avoids table lookup entirely (index-only scan)

### Partial Indexes (PostgreSQL)
- WHERE clause targets subset of rows (e.g., `WHERE status = 'active'`)
- Only index the relevant rows, smaller + faster

---

## Step 3: Detect Unused Indexes

Check for indexes that provide no benefit:

| Signal | Detection |
|--------|-----------|
| Duplicate indexes | Same columns in same order |
| Redundant prefix | Index (a,b) makes index (a) redundant |
| Write-heavy table | More writes than reads on indexed columns |
| Never queried columns | Index on column not in any WHERE/JOIN |

---

## Step 4: Generate Index Recommendations

```markdown
## 📇 Index Analysis Report

### Missing Indexes (Recommended)
| # | Table | Columns | Query Impact | Type | SQL |
|---|-------|---------|-------------|------|-----|
| 1 | orders | (user_id, status) | N+1 fix in get_orders | Composite | `CREATE INDEX idx_orders_user_status ON orders(user_id, status);` |
| 2 | products | (category_id) | JOIN in product_list | FK | `CREATE INDEX idx_products_category ON products(category_id);` |

### Potentially Unused Indexes
| # | Table | Index | Reason | Action |
|---|-------|-------|--------|--------|
| 1 | users | idx_users_legacy_field | Column no longer queried | Consider DROP |

### Index Health Metrics
| Table | Rows (est) | Existing Indexes | Recommended | Index Ratio |
|-------|-----------|-----------------|-------------|-------------|
| {table} | {n} | {n} | +{n} | {read:write ratio} |

### Write Impact Assessment
Adding {n} indexes will increase write overhead by approximately {%}.
Recommended only if read:write ratio exceeds 10:1 for affected tables.
```

---

## Completion
Output to `dep-analysis-docs/analysis/index-analysis.md`
