#!/usr/bin/env bash
# Verifies the lock / rewrite / scan claims in references/lock-matrix.md
# against a throwaway PostgreSQL container.
#
# - No ports are published: psql runs inside the container over its local
#   Unix socket, so the database is never reachable from the network.
# - The container is removed on exit.
#
# Usage:
#   tests/verify-lock-matrix.sh [major-version ...]     # default: 17
#   tests/verify-lock-matrix.sh 14 17 18
#   ROWS=500000 tests/verify-lock-matrix.sh 17          # bigger fixture table
#
# Output: one line per case —
#   <case> | locks held (or waited for) | table rewritten? | scan/debug notes | error
# then the result of tests/safe-patterns-smoke.sql and of every SQL block in
# references/preflight-queries.md (run read-only). Case lines are compared with
# tests/expected-output.txt; any difference means lock-matrix.md needs review
# for that PostgreSQL version. Exits non-zero on any failure or difference.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FAILED=0

VERSIONS=("$@")
[ ${#VERSIONS[@]} -eq 0 ] && VERSIONS=(17)
ROWS="${ROWS:-100000}"
CONTAINER=""

cleanup() {
  # run_version runs in a subshell, so remove by name pattern ($$ is the same there).
  docker ps -aq --filter "name=pgmig-verify-.*-$$" | xargs docker rm -f >/dev/null 2>&1 || true
}
trap cleanup EXIT

psql_in() { docker exec -i "$CONTAINER" psql -X -q -A -t -U postgres -d lab "$@" 2>&1; }

summarize() {
  # $1 = case name, stdin = raw psql output
  local name="$1" raw locks rewrite notes err
  raw="$(cat)"
  locks="$(grep -E '^(LOCKS|WAITS) ' <<<"$raw" | head -1 | sed -E 's/^(LOCKS|WAITS) //' || true)"
  grep -q '^WAITS ' <<<"$raw" && locks="waits for: ${locks}"
  rewrite="$(grep -E '^REWRITE ' <<<"$raw" | head -1 | sed 's/^REWRITE //' || true)"
  notes="$(grep -E 'DEBUG: +(rewriting table|verifying table|existing constraints|partition constraint|building index)|^NOTE ' <<<"$raw" \
          | grep -v pg_toast | sed -E 's/^.*DEBUG: +//; s/^NOTE //; s/ (serially|with request for [0-9]+ parallel workers)$//' \
          | paste -sd ';' - || true)"
  err="$(grep -E 'ERROR: ' <<<"$raw" | grep -v 'current transaction is aborted' | head -1 | sed -E 's/^.*ERROR: +//' || true)"
  printf '%-34s | %-48s | %-7s | %s | %s\n' "$name" "${locks:--}" "${rewrite:--}" "${notes:--}" "${err:-}"
}

# In-transaction case: run <setup> (committed), then BEGIN; <stmt>; report the
# locks this backend holds and whether t/q got a new relfilenode; ROLLBACK;
# then <cleanup>. DEBUG1 is enabled only around <stmt>.
tx_case() {
  local name="$1" setup="$2" stmt="$3" cleanup_sql="${4:-}"
  psql_in <<SQL | summarize "$name"
SET client_min_messages = warning;
$setup
SELECT 't'::regclass::oid AS oid_t, pg_relation_filenode('t') AS fn_t,
       'q'::regclass::oid AS oid_q, pg_relation_filenode('q') AS fn_q \gset
BEGIN;
SET LOCAL client_min_messages = debug1;
$stmt
SET LOCAL client_min_messages = warning;
-- strongest lock held on each user table
SELECT 'LOCKS ' || coalesce(string_agg(relname || '=' || replace(mode, 'Lock', ''), ' ' ORDER BY relname), '-')
FROM (
  SELECT DISTINCT ON (c.relname) c.relname, l.mode
  FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
  WHERE l.pid = pg_backend_pid() AND l.granted AND l.locktype = 'relation'
    AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
    AND c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'm')
  ORDER BY c.relname, array_position(ARRAY['AccessExclusiveLock', 'ExclusiveLock', 'ShareRowExclusiveLock',
           'ShareLock', 'ShareUpdateExclusiveLock', 'RowExclusiveLock', 'RowShareLock', 'AccessShareLock'], l.mode)
) s;
SELECT 'REWRITE ' || CASE WHEN pg_relation_filenode(:oid_t) <> :fn_t OR pg_relation_filenode(:oid_q) <> :fn_q
                          THEN 'yes' ELSE 'no' END;
ROLLBACK;
SET client_min_messages = warning;
$cleanup_sql
SQL
}

# Non-transactional case (CONCURRENTLY etc.): hold ACCESS EXCLUSIVE on the
# target from this session (<hold> is a LOCK TABLE, or any statement that takes
# ACCESS EXCLUSIVE), send <stmt> from a second session via dblink, and report
# the lock mode the second session waits for, i.e. the lock <stmt> needs.
wait_case() {
  local name="$1" hold="$2" stmt="$3" cleanup_sql="${4:-}"
  psql_in <<SQL | summarize "$name"
SET client_min_messages = warning;
SELECT dblink_connect('b', 'dbname=lab user=postgres');
SELECT pid AS bpid FROM dblink('b', 'SELECT pg_backend_pid()') AS r(pid int) \gset
BEGIN;
$hold;
SELECT dblink_send_query('b', \$q\$$stmt\$q\$);
SELECT pg_sleep(0.5);
SELECT 'WAITS ' || coalesce(string_agg(c.relname || '=' || replace(l.mode, 'Lock', ''), ' ' ORDER BY c.relname), '-')
FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
WHERE l.pid = :bpid AND NOT l.granted;
COMMIT;
-- Poll with short statements: CONCURRENTLY operations wait for every older
-- snapshot, so blocking on dblink_get_result here would deadlock with them.
$(for _ in $(seq 1 300); do echo "SELECT CASE WHEN dblink_is_busy('b') = 1 THEN pg_sleep(0.1) END;"; done)
SET client_min_messages = notice;
SELECT 'NOTE result: ' || x FROM dblink_get_result('b', false) AS r(x text);
SELECT 1 FROM dblink_get_result('b', false) AS r(x text);
SET client_min_messages = warning;
SELECT dblink_disconnect('b');
$cleanup_sql
SQL
}

# Send <sql> as a single psql -c string (one simple-query message) and report
# the error it raises, if any.
expect_error() {
  local name="$1" sql="$2" out
  out="$(docker exec -i "$CONTAINER" psql -X -q -U postgres -d lab -c "$sql" 2>&1 || true)"
  printf '%-34s | %s\n' "$name" "$(grep -E 'ERROR' <<<"$out" | sed -E 's/^.*ERROR: +//' || echo 'no error')"
}

fixtures() {
  docker exec -i "$CONTAINER" psql -X -q -U postgres -d postgres -c 'CREATE DATABASE lab' >/dev/null
  psql_in -v rows="$ROWS" >/dev/null <<'SQL'
SET client_min_messages = warning;
CREATE EXTENSION dblink;
CREATE TABLE p (id bigint PRIMARY KEY);
INSERT INTO p SELECT generate_series(1, 1000);
CREATE TABLE t (
  id    bigint PRIMARY KEY,
  p_id  bigint NOT NULL,
  v     int NOT NULL,
  w     int,                -- nullable, but contains no NULLs
  s     varchar(50),
  n     numeric(10,2),
  ts    timestamp,
  email text
);
INSERT INTO t
SELECT g, (g % 1000) + 1, g, g, 'x' || g, g / 100.0,
       timestamp '2026-01-01' + g * interval '1 second', 'u' || g || '@example.com'
FROM generate_series(1, :rows) g;
CREATE INDEX t_v_idx ON t (v);
-- t_bad: same shape plus one row that violates every constraint we add,
-- so "did it scan?" is proven by an error.
CREATE TABLE t_bad (LIKE t);
INSERT INTO t_bad SELECT * FROM t LIMIT 1000;
INSERT INTO t_bad VALUES (-1, 999999, -1, NULL, 'bad', 0, now(), 'bad@example.com');
-- q: unique nullable column for PRIMARY KEY USING INDEX
CREATE TABLE q (k bigint, x int);
INSERT INTO q SELECT g, g FROM generate_series(1, :rows) g;
CREATE UNIQUE INDEX q_k_uidx ON q (k);
CREATE TYPE mood AS ENUM ('ok', 'sad');
CREATE MATERIALIZED VIEW mv AS SELECT p_id, count(*) AS c FROM t GROUP BY p_id;
CREATE UNIQUE INDEX mv_p_id ON mv (p_id);
CREATE TABLE parted (id bigint, created date) PARTITION BY RANGE (created);
CREATE TABLE parted_2026 PARTITION OF parted FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE parted_2027 (id bigint, created date);
INSERT INTO parted_2027 SELECT g, date '2027-06-01' FROM generate_series(1, 1000) g;
CREATE FUNCTION trg_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
ANALYZE;
SQL
}

run_version() {
  local v="$1"
  CONTAINER="pgmig-verify-$v-$$"
  echo "=== PostgreSQL $v (fixture rows: $ROWS) ==="
  # The image requires a superuser password to initialise; generate a random
  # one that is never printed or stored. No -p flag: nothing is published.
  docker run -d --rm --name "$CONTAINER" -e POSTGRES_PASSWORD="$(openssl rand -hex 24)" "postgres:$v" >/dev/null
  for _ in $(seq 1 60); do
    if docker logs "$CONTAINER" 2>&1 | grep -q 'PostgreSQL init process complete' \
       && docker exec "$CONTAINER" pg_isready -U postgres -q 2>/dev/null; then break; fi
    sleep 1
  done
  docker exec "$CONTAINER" psql -X -At -U postgres -c 'SHOW server_version'
  fixtures
  printf '%-34s | %-48s | %-7s | %s | %s\n' "case" "locks" "rewrite" "scan/debug notes" "error"

  # --- ADD COLUMN ---
  tx_case add_column_nullable            "" "ALTER TABLE t ADD COLUMN c int;"
  tx_case add_column_const_default_nn    "" "ALTER TABLE t ADD COLUMN c int NOT NULL DEFAULT 0;"
  tx_case add_column_default_now         "" "ALTER TABLE t ADD COLUMN c timestamptz DEFAULT now();"
  tx_case add_column_default_uuid        "" "ALTER TABLE t ADD COLUMN c uuid DEFAULT gen_random_uuid();"
  tx_case add_column_default_clock_ts    "" "ALTER TABLE t ADD COLUMN c timestamptz DEFAULT clock_timestamp();"
  tx_case add_column_serial              "" "ALTER TABLE t ADD COLUMN c bigserial;"
  tx_case add_column_identity            "" "ALTER TABLE t ADD COLUMN c bigint GENERATED ALWAYS AS IDENTITY;"
  tx_case add_column_stored_generated    "" "ALTER TABLE t ADD COLUMN c text GENERATED ALWAYS AS (lower(email)) STORED;"
  tx_case add_column_inline_fk           "" "ALTER TABLE t ADD COLUMN c bigint REFERENCES p (id);"
  tx_case add_column_inline_fk_default   "" "ALTER TABLE t_bad ADD COLUMN c bigint DEFAULT 999999 REFERENCES p (id);"

  # --- NOT NULL / DEFAULT ---
  tx_case set_not_null                   "" "ALTER TABLE t ALTER COLUMN w SET NOT NULL;"
  tx_case set_not_null_violating         "" "ALTER TABLE t_bad ALTER COLUMN w SET NOT NULL;"
  tx_case set_not_null_after_check \
    "ALTER TABLE t ADD CONSTRAINT w_nn CHECK (w IS NOT NULL) NOT VALID; ALTER TABLE t VALIDATE CONSTRAINT w_nn;" \
    "ALTER TABLE t ALTER COLUMN w SET NOT NULL;" \
    "ALTER TABLE t DROP CONSTRAINT w_nn;"
  tx_case drop_not_null                  "" "ALTER TABLE t ALTER COLUMN v DROP NOT NULL;"
  tx_case set_default                    "" "ALTER TABLE t ALTER COLUMN w SET DEFAULT 0;"
  tx_case add_nullable_then_volatile_default "" "ALTER TABLE t ADD COLUMN c uuid; ALTER TABLE t ALTER COLUMN c SET DEFAULT gen_random_uuid();"

  # --- CHECK constraints ---
  tx_case add_check                      "" "ALTER TABLE t ADD CONSTRAINT v_pos CHECK (v > 0);"
  tx_case add_check_violating            "" "ALTER TABLE t_bad ADD CONSTRAINT v_pos CHECK (v > 0);"
  tx_case add_check_not_valid_violating  "" "ALTER TABLE t_bad ADD CONSTRAINT v_pos CHECK (v > 0) NOT VALID;"
  tx_case validate_check \
    "ALTER TABLE t ADD CONSTRAINT v_pos CHECK (v > 0) NOT VALID;" \
    "ALTER TABLE t VALIDATE CONSTRAINT v_pos;" \
    "ALTER TABLE t DROP CONSTRAINT v_pos;"

  # --- FOREIGN KEYS ---
  tx_case add_fk                         "" "ALTER TABLE t ADD CONSTRAINT t_p_fk FOREIGN KEY (p_id) REFERENCES p (id);"
  tx_case add_fk_violating               "" "ALTER TABLE t_bad ADD CONSTRAINT t_p_fk FOREIGN KEY (p_id) REFERENCES p (id);"
  tx_case add_fk_not_valid_violating     "" "ALTER TABLE t_bad ADD CONSTRAINT t_p_fk FOREIGN KEY (p_id) REFERENCES p (id) NOT VALID;"
  tx_case validate_fk \
    "ALTER TABLE t ADD CONSTRAINT t_p_fk FOREIGN KEY (p_id) REFERENCES p (id) NOT VALID;" \
    "ALTER TABLE t VALIDATE CONSTRAINT t_p_fk;" \
    "ALTER TABLE t DROP CONSTRAINT t_p_fk;"

  # --- INDEXES / UNIQUE / PK ---
  tx_case create_index                   "" "CREATE INDEX t_w_idx ON t (w);"
  tx_case create_unique_index            "" "CREATE UNIQUE INDEX t_email_uidx ON t (email);"
  tx_case add_unique_constraint          "" "ALTER TABLE t ADD CONSTRAINT t_email_key UNIQUE (email);"
  tx_case add_unique_using_index \
    "CREATE UNIQUE INDEX t_email_uidx ON t (email);" \
    "ALTER TABLE t ADD CONSTRAINT t_email_key UNIQUE USING INDEX t_email_uidx;" \
    "DROP INDEX IF EXISTS t_email_uidx;"
  tx_case add_pk_using_index_nullable    "" "ALTER TABLE q ADD PRIMARY KEY USING INDEX q_k_uidx;"
  tx_case drop_index                     "" "DROP INDEX t_v_idx;"
  tx_case reindex_index                  "" "REINDEX INDEX t_v_idx;"
  tx_case drop_constraint \
    "ALTER TABLE t ADD CONSTRAINT v_pos CHECK (v > 0) NOT VALID;" \
    "ALTER TABLE t DROP CONSTRAINT v_pos;" \
    "ALTER TABLE t DROP CONSTRAINT IF EXISTS v_pos;"
  tx_case cic_in_transaction             "" "CREATE INDEX CONCURRENTLY t_w_idx ON t (w);"

  # --- TYPE CHANGES ---
  tx_case type_int_to_bigint             "" "ALTER TABLE t ALTER COLUMN v TYPE bigint;"
  tx_case type_varchar_widen             "" "ALTER TABLE t ALTER COLUMN s TYPE varchar(100);"
  tx_case type_varchar_to_text           "" "ALTER TABLE t ALTER COLUMN s TYPE text;"
  tx_case type_varchar_narrow            "" "ALTER TABLE t ALTER COLUMN s TYPE varchar(20);"
  tx_case type_text_to_varchar           "" "ALTER TABLE t ALTER COLUMN email TYPE varchar(100);"
  tx_case type_numeric_more_precision    "" "ALTER TABLE t ALTER COLUMN n TYPE numeric(12,2);"
  tx_case type_numeric_more_scale        "" "ALTER TABLE t ALTER COLUMN n TYPE numeric(12,4);"
  tx_case type_ts_to_tstz_utc            "" "SET LOCAL TimeZone = 'UTC'; ALTER TABLE t ALTER COLUMN ts TYPE timestamptz;"
  tx_case type_ts_to_tstz_non_utc        "" "SET LOCAL TimeZone = 'Asia/Tokyo'; ALTER TABLE t ALTER COLUMN ts TYPE timestamptz;"

  # --- RENAME / DROP / TRUNCATE ---
  tx_case rename_column                  "" "ALTER TABLE t RENAME COLUMN w TO w2;"
  tx_case rename_table                   "" "ALTER TABLE t RENAME TO t_renamed;"
  tx_case drop_column                    "" "ALTER TABLE t DROP COLUMN w;"
  tx_case truncate                       "" "TRUNCATE t_bad;"

  # --- MISC ---
  tx_case create_trigger                 "" "CREATE TRIGGER trg BEFORE UPDATE ON t FOR EACH ROW EXECUTE FUNCTION trg_fn();"
  tx_case set_autovacuum_reloption       "" "ALTER TABLE t SET (autovacuum_vacuum_scale_factor = 0.01);"
  tx_case set_statistics                 "" "ALTER TABLE t ALTER COLUMN v SET STATISTICS 500;"
  tx_case refresh_mv                     "" "REFRESH MATERIALIZED VIEW mv;"
  tx_case enum_add_value                 "" "ALTER TYPE mood ADD VALUE 'happy';"
  tx_case enum_add_value_then_use        "" "ALTER TYPE mood ADD VALUE 'happy'; SELECT 'happy'::mood;"
  tx_case attach_partition               "" "ALTER TABLE parted ATTACH PARTITION parted_2027 FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');"
  tx_case detach_partition               "" "ALTER TABLE parted DETACH PARTITION parted_2026;"
  tx_case attach_partition_with_check \
    "ALTER TABLE parted_2027 ADD CONSTRAINT p27 CHECK (created IS NOT NULL AND created >= '2027-01-01' AND created < '2028-01-01');" \
    "ALTER TABLE parted ATTACH PARTITION parted_2027 FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');" \
    "ALTER TABLE parted_2027 DROP CONSTRAINT p27;"

  # --- CONCURRENT / NON-TRANSACTIONAL (lock observed while waiting) ---
  local lock_t="LOCK TABLE t IN ACCESS EXCLUSIVE MODE"
  wait_case create_index_concurrently "$lock_t" "CREATE INDEX CONCURRENTLY t_w_cic ON t (w)" "DROP INDEX IF EXISTS t_w_cic;"
  wait_case drop_index_concurrently   "$lock_t" "DROP INDEX CONCURRENTLY t_v_idx" "CREATE INDEX IF NOT EXISTS t_v_idx ON t (v);"
  wait_case reindex_concurrently      "$lock_t" "REINDEX INDEX CONCURRENTLY t_v_idx"
  wait_case refresh_mv_concurrently   "REFRESH MATERIALIZED VIEW mv" "REFRESH MATERIALIZED VIEW CONCURRENTLY mv"
  if [ "${v%%.*}" -ge 14 ]; then
    wait_case detach_partition_concurrently "LOCK TABLE parted IN ACCESS EXCLUSIVE MODE" \
      "ALTER TABLE parted DETACH PARTITION parted_2026 CONCURRENTLY" \
      "ALTER TABLE parted ATTACH PARTITION parted_2026 FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');"
  fi

  # --- Multi-statement string (how some tools send a whole migration file) ---
  expect_error cic_in_multi_statement_string "SELECT 1; CREATE INDEX CONCURRENTLY t_w_multi ON t (w);"
  psql_in -c "DROP INDEX IF EXISTS t_w_multi;" >/dev/null
  expect_error cic_on_partitioned_parent "CREATE INDEX CONCURRENTLY parted_id_idx ON parted (id);"
  # Supported alternative: invalid parent index, build per partition, attach.
  local out
  out="$(psql_in -v ON_ERROR_STOP=1 <<'SQL'
SET client_min_messages = warning;
CREATE INDEX parted_id_idx ON ONLY parted (id);
CREATE INDEX CONCURRENTLY parted_2026_id_idx ON parted_2026 (id);
ALTER INDEX parted_id_idx ATTACH PARTITION parted_2026_id_idx;
SELECT 'parent index valid after attach: ' || indisvalid FROM pg_index WHERE indexrelid = 'parted_id_idx'::regclass;
DROP INDEX parted_id_idx;
SQL
)" && printf '%-34s | %s\n' partitioned_index_on_only "$(grep 'parent index' <<<"$out")" \
   || printf '%-34s | FAILED: %s\n' partitioned_index_on_only "$out"

  # --- A failed CONCURRENTLY build leaves an INVALID index; IF NOT EXISTS then skips it ---
  psql_in -c "CREATE TABLE dup (x int); INSERT INTO dup VALUES (1), (1);" >/dev/null
  expect_error cic_unique_on_duplicates "CREATE UNIQUE INDEX CONCURRENTLY dup_x_key ON dup (x);"
  out="$(psql_in -c "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS dup_x_key ON dup (x);" \
                 -c "SELECT 'index valid after IF NOT EXISTS retry: ' || indisvalid FROM pg_index WHERE indexrelid = 'dup_x_key'::regclass;")"
  printf '%-34s | %s; %s\n' cic_retry_if_not_exists \
    "$(grep -oE 'relation "dup_x_key" already exists, skipping' <<<"$out" || echo 'no skip notice')" \
    "$(grep -oE 'index valid after IF NOT EXISTS retry: [a-z]+' <<<"$out")"
  psql_in -c "DROP TABLE dup;" >/dev/null

  # --- Lock queue: a "fast" ALTER behind one open transaction blocks new readers ---
  psql_in <<'SQL' | grep 'plain SELECT' | sed 's/^/lock_queue                         | /'
SET client_min_messages = warning;
SELECT dblink_connect('a', 'dbname=lab user=postgres');
SELECT dblink_connect('b', 'dbname=lab user=postgres');
SELECT dblink_connect('c', 'dbname=lab user=postgres');
SELECT dblink_exec('a', 'BEGIN');
SELECT * FROM dblink('a', 'SELECT count(*) FROM t WHERE id = 1') AS r(n bigint);
SELECT dblink_send_query('b', 'ALTER TABLE t ADD COLUMN queued int');
SELECT pg_sleep(0.3);
SELECT dblink_send_query('c', 'SELECT count(*) FROM t WHERE id = 1');
SELECT pg_sleep(0.3);
SELECT 'plain SELECT is ' || CASE WHEN wait_event_type = 'Lock' THEN 'BLOCKED behind the queued ALTER' ELSE 'not blocked' END
FROM pg_stat_activity WHERE query = 'SELECT count(*) FROM t WHERE id = 1' AND pid <> pg_backend_pid() AND state = 'active';
SELECT dblink_exec('a', 'ROLLBACK');
SELECT * FROM dblink_get_result('b') AS r(x text);
SELECT * FROM dblink_get_result('c') AS r(n bigint);
SELECT dblink_disconnect('a'); SELECT dblink_disconnect('b'); SELECT dblink_disconnect('c');
ALTER TABLE t DROP COLUMN IF EXISTS queued;
SQL

  # --- lock_timeout turns the queue into a fast, retryable failure ---
  psql_in <<'SQL' | grep -E 'ERROR' | sed -E 's/^.*ERROR: +//; s/^/lock_timeout                       | /'
SET client_min_messages = warning;
SELECT dblink_connect('a', 'dbname=lab user=postgres');
SELECT dblink_exec('a', 'BEGIN');
SELECT * FROM dblink('a', 'SELECT count(*) FROM t WHERE id = 1') AS r(n bigint);
SET lock_timeout = '200ms';
ALTER TABLE t ADD COLUMN c int;
RESET lock_timeout;
SELECT dblink_exec('a', 'ROLLBACK');
SELECT dblink_disconnect('a');
SQL

  # --- Recipes in references/safe-patterns.md ---
  docker exec "$CONTAINER" psql -X -q -U postgres -d postgres -c 'CREATE DATABASE smoke' >/dev/null
  if out="$(docker exec -i "$CONTAINER" psql -X -q -A -t -v ON_ERROR_STOP=1 -U postgres -d smoke \
            < "$HERE/safe-patterns-smoke.sql" 2>&1)"; then
    printf '%-34s | %s\n' safe_patterns_smoke "$(grep PASSED <<<"$out")"
  else
    printf '%-34s | FAILED\n%s\n' safe_patterns_smoke "$out"; FAILED=1
  fi

  # --- Every sql block in references/preflight-queries.md, forced read-only ---
  local blocks n=0 ok=0
  blocks="$(mktemp -d)"
  awk -v dir="$blocks" '/^```sql$/ {f = 1; n++; next} /^```$/ {f = 0} f {print > (dir "/" n ".sql")}' \
    "$HERE/../references/preflight-queries.md"
  for b in "$blocks"/*.sql; do
    n=$((n + 1))
    if out="$( { echo "SET default_transaction_read_only = on;"; cat "$b"; } \
               | docker exec -i "$CONTAINER" psql -X -q -A -t -v ON_ERROR_STOP=1 -U postgres -d smoke 2>&1)"; then
      ok=$((ok + 1))
    else
      printf '%-34s | FAILED block %s: %s\n' preflight_queries "$(basename "$b")" "$out"; FAILED=1
    fi
  done
  rm -rf "$blocks"
  printf '%-34s | %s/%s blocks ran read-only without error\n' preflight_queries "$ok" "$n"

  cleanup
  CONTAINER=""
  echo
}

for v in "${VERSIONS[@]}"; do
  out="$(run_version "$v")"
  echo "$out"
  grep -q 'FAILED' <<<"$out" && FAILED=1
  if [ -f "$HERE/expected-output.txt" ]; then
    # Compare case lines only (skip the banner, version, and header lines).
    if diff <(sed -n '4,$p' <<<"$out" | sed '/^$/d') "$HERE/expected-output.txt" >/dev/null; then
      echo "PostgreSQL $v: all cases match tests/expected-output.txt"
    else
      echo "PostgreSQL $v: DIFFERS from tests/expected-output.txt; review references/lock-matrix.md for this version:"
      diff <(sed -n '4,$p' <<<"$out" | sed '/^$/d') "$HERE/expected-output.txt" || true
      FAILED=1
    fi
  fi
  echo
done
exit "$FAILED"
