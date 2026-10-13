"""Run an Omni SQL query (logs/traces) from a .sql file and print the rows.

Usage:
    python common/omni_query.py path/to/query.sql [--var service=payments] [--max-rows 50]

`{name}` placeholders in the file are replaced from --var. Full-line `--` comments
are stripped, so a file can document itself. Only SQL is supported here, because
the ad hoc query API parses every query as SQL. Check PromQL in the Omni UI
(Explore) or through an alert rule.
"""

import argparse
import os
import pathlib
import re
import sys
import time

import boto3

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.env_file import load_env


def read_query(path, variables):
    lines = [l for l in pathlib.Path(path).read_text().splitlines() if not l.strip().startswith("--")]
    sql = "\n".join(lines).strip().rstrip(";")
    for key, value in variables.items():
        sql = sql.replace("{" + key + "}", value)
    missing = set(re.findall(r"\{([a-z_]+)\}", sql))
    if missing:
        sys.exit(f"Missing --var for: {', '.join(sorted(missing))}")
    return sql


def run(sql, max_rows, timeout_s=300):
    client = boto3.client("cloudwatchomni", region_name=os.environ["AWS_REGION"])
    session_id = client.start_telemetry_query_session(sessionName="omni-samples")["sessionId"]
    deadline = time.monotonic() + timeout_s
    try:
        query_id = client.start_telemetry_query(queryString=sql, sessionId=session_id)["queryId"]
        while True:
            result = client.get_telemetry_query_results(queryId=query_id, maxResults=max_rows)
            if result["status"] != "Running":
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"Query timed out after {timeout_s}s")
            time.sleep(1)
        if result["status"] != "Complete":
            sys.exit(f"Query {result['status']}")
        return result.get("rows", []), result.get("statistics", {})
    finally:
        client.stop_telemetry_query_session(sessionId=session_id)


def print_rows(rows):
    if not rows:
        print("(0 rows. If you expected data, check the time window, Region, and field names with EXPLAIN (ANALYZE_FIELDS))")
        return
    cols = list(dict.fromkeys(k for row in rows for k in row))
    widths = {c: min(60, max(len(c), *(len(str(r.get(c, ""))) for r in rows))) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for row in rows:
        print("  ".join(str(row.get(c, ""))[: widths[c]].ljust(widths[c]) for c in cols))


def main():
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("sql_file")
    parser.add_argument("--var", action="append", default=[], help="placeholder value, key=value")
    parser.add_argument("--max-rows", type=int, default=50)
    parser.add_argument("--show-sql", action="store_true")
    args = parser.parse_args()

    variables = dict(v.split("=", 1) for v in args.var)
    sql = read_query(args.sql_file, variables)
    if args.show_sql:
        print(sql, "\n")
    rows, stats = run(sql, args.max_rows)
    print_rows(rows)
    if stats:
        print(f"\nscanned={stats.get('recordsScanned')} matched={stats.get('recordsMatched')}")


if __name__ == "__main__":
    main()
