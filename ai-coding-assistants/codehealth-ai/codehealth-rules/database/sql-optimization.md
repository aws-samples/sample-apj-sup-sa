# SQL Query Optimization - Detailed Steps

## Purpose
Deep SQL query analysis: execution plan interpretation, query rewriting,
engine-specific tuning, and performance benchmarking.

---

## Step 1: Identify Expensive Queries

### From Application Code
Find queries that are:
- Called frequently (in hot request paths)
- Operating on large tables (>100K rows)
- Using complex JOINs, subqueries, or aggregations
- Reported as slow in logs or monitoring

### From Slow Query Logs (if accessible)
```sql
-- PostgreSQL: find slow queries
SELECT query, calls, mean_exec_time, total_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;

-- MySQL: find slow queries
SELECT * FROM mysql.slow_log ORDER BY query_time DESC LIMIT 20;
```

---

## Step 2: Execution Plan Analysis

For each expensive query, analyze the execution plan:

### PostgreSQL
```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {query};
-- Or for JSON output:
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query};
```

### MySQL
```sql
EXPLAIN ANALYZE {query};
-- Or traditional:
EXPLAIN {query};
```

### Key Plan Nodes to Flag

| Plan Node | Issue | Severity | Action |
|-----------|-------|----------|--------|
| Seq Scan on large table | Missing index | 🟠 High | Add index on filter columns |
| Nested Loop with inner Seq Scan | N+1 at DB level | 🔴 Critical | Add index or restructure JOIN |
| Hash Join with large build | Memory pressure | 🟡 Medium | Ensure smaller table is build side |
| Sort (external merge) | Spilling to disk | 🟠 High | Add index matching ORDER BY |
| Bitmap Heap Scan (lossy) | Index not selective enough | 🟡 Medium | More selective index or partial |
| Filter (rows removed) | Index not covering filter | 🟡 Medium | Add filtered columns to index |
| Aggregate on unindexed | Full scan for COUNT/SUM | 🟡 Medium | Covering index or materialized view |
| Gather (parallel) on simple query | Overhead for small result | 🔵 Info | May not need parallelism |

### Plan Cost Interpretation
```
Cost format: (startup_cost..total_cost)
- startup_cost: Time before first row returned
- total_cost: Time for all rows
- rows: Estimated row count (compare to actual!)
- width: Average row size in bytes

RED FLAGS:
- actual rows >> estimated rows → stale statistics (run ANALYZE)
- actual loops >> 1 on inner scan → N+1 at execution level
- large Sort/Hash memory usage → may need work_mem increase
```

---

## Step 3: Query Rewriting Techniques

### 3.1 Replace Correlated Subqueries with JOINs

```sql
-- ❌ SLOW: Correlated subquery (executes per row)
SELECT o.id, o.total,
  (SELECT COUNT(*) FROM order_items WHERE order_id = o.id) as item_count
FROM orders o
WHERE o.status = 'active';

-- ✅ FAST: LEFT JOIN with GROUP BY
SELECT o.id, o.total, COUNT(oi.id) as item_count
FROM orders o
LEFT JOIN order_items oi ON oi.order_id = o.id
WHERE o.status = 'active'
GROUP BY o.id, o.total;

-- ✅ FAST (alternative): Lateral join (PostgreSQL)
SELECT o.id, o.total, item_counts.cnt as item_count
FROM orders o
LEFT JOIN LATERAL (
  SELECT COUNT(*) as cnt FROM order_items WHERE order_id = o.id
) item_counts ON true
WHERE o.status = 'active';
```

### 3.2 Replace IN (subquery) with EXISTS or JOIN

```sql
-- ❌ SLOW: IN with subquery on large set
SELECT * FROM users
WHERE id IN (SELECT user_id FROM orders WHERE total > 1000);

-- ✅ FAST: EXISTS (short-circuits)
SELECT * FROM users u
WHERE EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id AND o.total > 1000);

-- ✅ FAST: JOIN (when you need order data too)
SELECT DISTINCT u.* FROM users u
JOIN orders o ON o.user_id = u.id
WHERE o.total > 1000;
```

### 3.3 Optimize OR Conditions

```sql
-- ❌ SLOW: OR prevents single index usage
SELECT * FROM products
WHERE category_id = 5 OR brand_id = 12 OR price < 10;

-- ✅ FAST: UNION ALL (each branch can use its own index)
SELECT * FROM products WHERE category_id = 5
UNION ALL
SELECT * FROM products WHERE brand_id = 12 AND category_id != 5
UNION ALL
SELECT * FROM products WHERE price < 10 AND category_id != 5 AND brand_id != 12;
```

### 3.4 Window Functions Instead of Self-Joins

```sql
-- ❌ SLOW: Self-join for running total
SELECT o1.id, o1.amount,
  (SELECT SUM(o2.amount) FROM orders o2 WHERE o2.id <= o1.id) as running_total
FROM orders o1;

-- ✅ FAST: Window function
SELECT id, amount,
  SUM(amount) OVER (ORDER BY id ROWS UNBOUNDED PRECEDING) as running_total
FROM orders;
```

### 3.5 Avoid Functions on Indexed Columns

```sql
-- ❌ SLOW: Function on column prevents index usage
SELECT * FROM orders WHERE YEAR(created_at) = 2026;
SELECT * FROM users WHERE LOWER(email) = 'user@example.com';

-- ✅ FAST: Rewrite to use index
SELECT * FROM orders WHERE created_at >= '2026-01-01' AND created_at < '2027-01-01';
SELECT * FROM users WHERE email = 'user@example.com';  -- with case-insensitive collation
-- Or create functional index:
CREATE INDEX idx_users_email_lower ON users (LOWER(email));
```

### 3.6 Optimize LIKE Patterns

```sql
-- ❌ SLOW: Leading wildcard = full table scan
SELECT * FROM products WHERE name LIKE '%widget%';

-- ✅ Options for text search:
-- 1. Trailing wildcard only (can use B-tree index):
SELECT * FROM products WHERE name LIKE 'widget%';

-- 2. Full-text search (PostgreSQL):
SELECT * FROM products WHERE to_tsvector('english', name) @@ to_tsquery('widget');
-- With GIN index: CREATE INDEX idx_products_name_fts ON products USING GIN(to_tsvector('english', name));

-- 3. Trigram index (PostgreSQL) for arbitrary LIKE:
CREATE EXTENSION pg_trgm;
CREATE INDEX idx_products_name_trgm ON products USING GIN(name gin_trgm_ops);
```

### 3.7 Pagination Optimization

```sql
-- ❌ SLOW: OFFSET-based pagination degrades on later pages
SELECT * FROM orders ORDER BY created_at DESC LIMIT 20 OFFSET 10000;
-- Must scan and discard 10,000 rows

-- ✅ FAST: Cursor-based (keyset) pagination
SELECT * FROM orders
WHERE created_at < '2026-06-20T10:00:00Z'  -- cursor from last item of previous page
ORDER BY created_at DESC
LIMIT 20;
-- Only scans 20 rows regardless of page depth
```

### 3.8 Batch Operations

```sql
-- ❌ SLOW: Individual inserts in a loop
INSERT INTO events (user_id, type, data) VALUES (1, 'click', '{}');
INSERT INTO events (user_id, type, data) VALUES (2, 'click', '{}');
-- (repeated 1000 times = 1000 round trips)

-- ✅ FAST: Batch insert
INSERT INTO events (user_id, type, data) VALUES
  (1, 'click', '{}'),
  (2, 'click', '{}'),
  -- ... batch of 1000 in single statement
  (1000, 'click', '{}');

-- ✅ FAST: COPY command (PostgreSQL, for large loads)
COPY events (user_id, type, data) FROM STDIN WITH CSV;
```

### 3.9 Conditional Aggregation Instead of Multiple Queries

```sql
-- ❌ SLOW: Multiple queries for dashboard stats
SELECT COUNT(*) FROM orders WHERE status = 'pending';
SELECT COUNT(*) FROM orders WHERE status = 'shipped';
SELECT COUNT(*) FROM orders WHERE status = 'delivered';
SELECT AVG(total) FROM orders WHERE created_at > NOW() - INTERVAL '30 days';

-- ✅ FAST: Single query with conditional aggregation
SELECT
  COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
  COUNT(*) FILTER (WHERE status = 'shipped') as shipped_count,
  COUNT(*) FILTER (WHERE status = 'delivered') as delivered_count,
  AVG(total) FILTER (WHERE created_at > NOW() - INTERVAL '30 days') as avg_recent_total
FROM orders;
```

### 3.10 Materialized Views for Expensive Aggregations

```sql
-- For dashboards/reports that query expensive aggregations repeatedly:
CREATE MATERIALIZED VIEW daily_order_stats AS
SELECT
  DATE(created_at) as day,
  COUNT(*) as order_count,
  SUM(total) as revenue,
  AVG(total) as avg_order_value
FROM orders
GROUP BY DATE(created_at);

-- Refresh periodically (not in hot path)
REFRESH MATERIALIZED VIEW CONCURRENTLY daily_order_stats;

-- Create index on materialized view
CREATE UNIQUE INDEX idx_daily_stats_day ON daily_order_stats(day);
```

---

## Step 4: Database-Engine-Specific Best Practices

### PostgreSQL Best Practices

| Practice | Why | How |
|----------|-----|-----|
| Use JSONB over JSON | Indexable, faster reads | `column JSONB` + GIN index |
| Partial indexes | Smaller, faster, targeted | `CREATE INDEX ... WHERE active = true` |
| Expression indexes | Index computed values | `CREATE INDEX ... ON (LOWER(email))` |
| BRIN indexes for time-series | Tiny index for ordered data | `CREATE INDEX ... USING BRIN(created_at)` |
| Use CTEs wisely | PostgreSQL 12+ inlines CTEs | But NOT MATERIALIZED if you need barrier |
| Connection pooling | PG forks per connection | Use PgBouncer or built-in pool |
| VACUUM and ANALYZE | Prevent bloat, update stats | Autovacuum config or manual |
| Use COPY for bulk inserts | 10-100x faster than INSERT | `COPY table FROM STDIN` |
| Avoid SELECT * | Wasteful I/O | Select only needed columns |
| Use EXPLAIN ANALYZE | Real execution metrics | Always test with realistic data |

### MySQL Best Practices

| Practice | Why | How |
|----------|-----|-----|
| Use InnoDB | ACID, row-level locking | Default since MySQL 5.5 |
| Covering indexes | Avoids table lookup | Include SELECT columns in index |
| Avoid SELECT * | Prevents covering index | Explicit column list |
| Use LIMIT with ORDER BY | Avoid filesort on full set | Early termination |
| Optimize GROUP BY | Uses index if columns match | Index on GROUP BY columns |
| Avoid NOT IN (subquery) | Poor optimization | Use LEFT JOIN ... IS NULL |
| Use STRAIGHT_JOIN carefully | Override optimizer join order | Only when you know better |
| Query cache (if enabled) | Avoids re-execution | But invalidates on writes |
| Use UNSIGNED for IDs | Double the range, saves space | `INT UNSIGNED AUTO_INCREMENT` |
| Batch deletes/updates | Avoid long-running locks | `DELETE ... LIMIT 1000` in loop |

### SQLite Best Practices

| Practice | Why | How |
|----------|-----|-----|
| WAL mode | Concurrent reads + writes | `PRAGMA journal_mode = WAL` |
| Batch in transactions | 100x faster than individual | Wrap multiple writes in BEGIN/COMMIT |
| Use INTEGER PRIMARY KEY | Auto-rowid alias, fastest | `id INTEGER PRIMARY KEY` |
| Avoid large BLOBs | Slows everything | Store files externally |
| VACUUM periodically | Reclaim space | `VACUUM` after bulk deletes |

---

## Step 5: ORM-Specific SQL Optimization

### Prisma Optimization Patterns

```typescript
// ❌ Over-fetching: loads all columns
const users = await prisma.user.findMany();

// ✅ Select only needed fields
const users = await prisma.user.findMany({
  select: { id: true, name: true, email: true },
});

// ❌ Multiple queries for related data
const user = await prisma.user.findUnique({ where: { id } });
const orders = await prisma.order.findMany({ where: { userId: id } });

// ✅ Single query with include
const user = await prisma.user.findUnique({
  where: { id },
  include: { orders: { take: 10, orderBy: { createdAt: 'desc' } } },
});

// ❌ Count without need for data
const users = await prisma.user.findMany({ where: { active: true } });
const count = users.length;

// ✅ Use count directly
const count = await prisma.user.count({ where: { active: true } });

// ✅ Use raw SQL for complex aggregations
const stats = await prisma.$queryRaw`
  SELECT status, COUNT(*), AVG(total)
  FROM orders
  WHERE created_at > ${thirtyDaysAgo}
  GROUP BY status
`;
```

### Django ORM Optimization Patterns

```python
# ❌ Lazy loading causes N+1
for order in Order.objects.all():
    print(order.customer.name)  # N extra queries

# ✅ select_related for FK (single JOIN)
for order in Order.objects.select_related('customer').all():
    print(order.customer.name)

# ✅ prefetch_related for M2M/reverse FK (2 queries total)
for customer in Customer.objects.prefetch_related('orders').all():
    print(customer.orders.count())

# ❌ Loading all fields when only need a few
users = User.objects.all()

# ✅ Only/Defer for field selection
users = User.objects.only('id', 'name', 'email')
# Or: users = User.objects.defer('large_bio_field', 'avatar_blob')

# ❌ Python-side filtering
all_orders = Order.objects.all()
active = [o for o in all_orders if o.status == 'active']

# ✅ Database-side filtering
active = Order.objects.filter(status='active')

# ✅ Use F() expressions for DB-side computation
from django.db.models import F
Order.objects.filter(total__gt=F('discount') * 2)

# ✅ Use annotate for aggregations
from django.db.models import Count, Avg
Customer.objects.annotate(
    order_count=Count('orders'),
    avg_order_value=Avg('orders__total')
)

# ✅ Use Subquery for correlated lookups
from django.db.models import Subquery, OuterRef
latest_order = Order.objects.filter(customer=OuterRef('pk')).order_by('-created_at')[:1]
Customer.objects.annotate(latest_order_date=Subquery(latest_order.values('created_at')))
```

### SQLAlchemy Optimization Patterns

```python
# ❌ Lazy loading
users = session.query(User).all()
for user in users:
    print(user.orders)  # N+1

# ✅ Eager loading options
from sqlalchemy.orm import joinedload, selectinload, subqueryload

# joinedload: single query with JOIN (good for to-one)
users = session.query(User).options(joinedload(User.profile)).all()

# selectinload: separate IN query (good for to-many)
users = session.query(User).options(selectinload(User.orders)).all()

# ✅ Use hybrid properties for computed columns
from sqlalchemy.ext.hybrid import hybrid_property

class Order(Base):
    @hybrid_property
    def is_overdue(self):
        return self.due_date < datetime.now()

    @is_overdue.expression
    def is_overdue(cls):
        return cls.due_date < func.now()  # Translates to SQL

# ✅ Use bulk operations
session.bulk_insert_mappings(User, [{'name': 'Alice'}, {'name': 'Bob'}])
session.bulk_update_mappings(User, [{'id': 1, 'active': False}])
```

### Hibernate/JPA Optimization Patterns

```java
// ❌ N+1 with default LAZY loading
List<Order> orders = em.createQuery("FROM Order", Order.class).getResultList();
for (Order o : orders) {
    o.getItems().size();  // N+1
}

// ✅ JOIN FETCH in JPQL
List<Order> orders = em.createQuery(
    "SELECT o FROM Order o JOIN FETCH o.items WHERE o.status = :status",
    Order.class
).setParameter("status", "ACTIVE").getResultList();

// ✅ Entity Graph for dynamic fetch planning
@NamedEntityGraph(name = "Order.withItems",
    attributeNodes = @NamedAttributeNode("items"))
public class Order { ... }

EntityGraph graph = em.getEntityGraph("Order.withItems");
List<Order> orders = em.createQuery("FROM Order", Order.class)
    .setHint("javax.persistence.fetchgraph", graph)
    .getResultList();

// ✅ DTO Projection (avoid loading full entities)
List<OrderSummary> summaries = em.createQuery(
    "SELECT NEW com.app.dto.OrderSummary(o.id, o.total, o.status) FROM Order o",
    OrderSummary.class
).getResultList();

// ✅ Batch size for collections
@OneToMany(mappedBy = "order")
@BatchSize(size = 25)  // Loads 25 at a time instead of 1
private List<OrderItem> items;
```

---

## Step 6: Query Optimization Checklist

For each identified slow query, verify:

- [ ] Execution plan reviewed (EXPLAIN ANALYZE)
- [ ] Indexes exist for all WHERE/JOIN/ORDER BY columns
- [ ] No functions applied to indexed columns in predicates
- [ ] No SELECT * (only needed columns selected)
- [ ] Pagination uses cursor-based (not OFFSET for deep pages)
- [ ] Aggregations use appropriate indexes or materialized views
- [ ] Subqueries replaced with JOINs where possible
- [ ] OR conditions restructured if preventing index use
- [ ] Statistics are up-to-date (ANALYZE run recently)
- [ ] Connection pool sized appropriately
- [ ] Query results cached if read-heavy and rarely changing
- [ ] Batch operations used instead of row-by-row processing

---

## Step 7: Generate SQL Optimization Report

```markdown
## ⚡ SQL Query Optimization Report

### Queries Analyzed: {n}
### Optimization Opportunities: {n}

### Top Optimizations by Impact
| # | Query Location | Current Cost | Optimized Cost | Technique | Effort |
|---|---------------|-------------|---------------|-----------|--------|
| 1 | {file}:{line} | {ms} | {ms} | {technique} | Low |
| 2 | {file}:{line} | {ms} | {ms} | {technique} | Medium |

### Rewritten Queries
For each optimization, provide:
- Original query (or ORM call)
- Execution plan summary (before)
- Optimized query
- Execution plan summary (after)
- Expected improvement

### Index Recommendations (from optimization)
{Additional indexes needed to support rewritten queries}

### Configuration Recommendations
| Setting | Current | Recommended | Impact |
|---------|---------|-------------|--------|
| work_mem | {val} | {val} | Avoid disk sorts |
| shared_buffers | {val} | {val} | Better caching |
| effective_cache_size | {val} | {val} | Better plan choices |
| random_page_cost | {val} | {val} | Prefer index scans |
```

---

## Completion
Output to `dep-analysis-docs/analysis/sql-optimization.md`
