# Preflight queries (read-only)

Use these to size a migration's risk. Every query reads catalogs or statistics views only. Nothing here takes a lock that blocks your application. `tests/verify-lock-matrix.sh` runs every block in this file against PostgreSQL 14 and 17.

Run them as a read-only role, against a read replica if you have one. Table sizes and catalog state match the primary, but activity (`pg_stat_activity`, `pg_locks`) is per instance, so run the activity queries on the primary. Start each session with a short timeout:

```sql
SET statement_timeout = '10s';
SET default_transaction_read_only = on;
```

> Replace `'orders', 'users'` with the tables your migration touches.

## Version

```sql
SELECT current_setting('server_version') AS version,
       current_setting('server_version_num')::int / 10000 AS major;
```

## Size and activity of the tables being migrated

```sql
SELECT s.relname                                         AS table_name,
       c.reltuples::bigint                               AS est_rows,     -- -1 means never analyzed (PG14+)
       pg_size_pretty(pg_total_relation_size(c.oid))     AS total_size,   -- table + indexes + TOAST
       pg_size_pretty(pg_relation_size(c.oid))           AS heap_size,
       s.n_tup_ins + s.n_tup_upd + s.n_tup_del           AS writes_since_stats_reset,
       s.seq_scan, s.idx_scan,
       s.n_dead_tup,
       s.last_autovacuum, s.last_autoanalyze
FROM pg_stat_user_tables s
JOIN pg_class c ON c.oid = s.relid
WHERE s.relname IN ('orders', 'users')
ORDER BY pg_total_relation_size(c.oid) DESC;
```

To get a **write rate**, run it twice, 60 seconds apart, and divide the difference in `writes_since_stats_reset` by 60.

A rewrite needs free storage roughly equal to `total_size`. On RDS, check the `FreeStorageSpace` CloudWatch metric (Aurora storage grows automatically).

## Long-running and idle-in-transaction sessions

Any of these can make your migration's lock request queue, and everything else queue behind it. Run this on the **primary** just before you migrate.

```sql
SELECT pid, usename, application_name, client_addr, state,
       now() - xact_start  AS xact_age,
       now() - query_start AS query_age,
       left(query, 100)    AS query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
  AND pid <> pg_backend_pid()
  AND now() - xact_start > interval '30 seconds'
ORDER BY xact_start;
```

`state = 'idle in transaction'` with a large `xact_age` is the classic migration blocker: an app or console session that ran `BEGIN` and never finished.

## Who is blocking whom (while a migration is stuck)

```sql
SELECT a.pid,
       pg_blocking_pids(a.pid)  AS blocked_by,
       a.wait_event_type,
       now() - a.query_start    AS waiting_for,
       left(a.query, 100)       AS query
FROM pg_stat_activity a
WHERE cardinality(pg_blocking_pids(a.pid)) > 0
ORDER BY a.query_start;
```

## Locks currently held or requested on a table

```sql
SELECT l.pid, l.mode, l.granted, a.state,
       now() - a.query_start AS query_age,
       left(a.query, 80)     AS query
FROM pg_locks l
JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'relation'
  AND l.relation = 'orders'::regclass
ORDER BY l.granted DESC, a.query_start;
```

## Invalid indexes (left behind by a failed CREATE INDEX CONCURRENTLY)

```sql
SELECT i.indexrelid::regclass AS index_name,
       i.indrelid::regclass   AS table_name,
       pg_size_pretty(pg_relation_size(i.indexrelid)) AS size
FROM pg_index i
WHERE NOT i.indisvalid;
```

Drop them with `DROP INDEX CONCURRENTLY` and retry the build. Note that the parent index of a partitioned table is also invalid until every partition's index is attached.

## Constraints that were added NOT VALID and never validated

```sql
SELECT conrelid::regclass AS table_name, conname, contype,
       pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE NOT convalidated
ORDER BY 1, 2;
```

## Foreign keys without a supporting index

Without an index on the referencing columns, every `DELETE` or key `UPDATE` on the parent table scans the child table.

```sql
SELECT c.conrelid::regclass   AS table_name,
       c.conname              AS foreign_key,
       pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
WHERE c.contype = 'f'
  AND NOT EXISTS (
    SELECT 1
    FROM pg_index i
    WHERE i.indrelid = c.conrelid
      AND i.indpred IS NULL
      AND (string_to_array(i.indkey::text, ' ')::int2[])[1:cardinality(c.conkey)] @> c.conkey
  )
ORDER BY 1, 2;
```

## Primary keys that will run out (int / smallint)

```sql
SELECT c.conrelid::regclass AS table_name,
       a.attname            AS column_name,
       format_type(a.atttypid, a.atttypmod) AS type
FROM pg_constraint c
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
WHERE c.contype = 'p'
  AND a.atttypid IN ('int2'::regtype, 'int4'::regtype)
ORDER BY 1;
```

Sequence headroom. `max_value` is the sequence's limit, but an `int` column overflows at 2,147,483,647 even when the sequence is `bigint`:

```sql
SELECT schemaname, sequencename, data_type, last_value, max_value,
       round(100.0 * last_value / max_value, 2) AS pct_of_sequence_max,
       round(100.0 * last_value / 2147483647, 2) AS pct_of_int_max
FROM pg_sequences
WHERE last_value IS NOT NULL
ORDER BY last_value DESC
LIMIT 20;
```

## Replication lag (before and during a backfill)

On the primary, for RDS read replicas and any streaming replica:

```sql
SELECT application_name, client_addr, state, write_lag, flush_lag, replay_lag
FROM pg_stat_replication;
```

Aurora readers don't appear here. Use the `AuroraReplicaLag` CloudWatch metric instead. For RDS read replicas, the `ReplicaLag` metric shows the same thing from the replica's side.

## Database-wide size, for snapshot and clone planning

```sql
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;
```
