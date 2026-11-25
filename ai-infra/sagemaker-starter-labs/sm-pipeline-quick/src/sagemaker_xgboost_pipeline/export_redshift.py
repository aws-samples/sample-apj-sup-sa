"""Export assumed Redshift source views as immutable Parquet snapshots for one Pipeline run."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import boto3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workgroup-name", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--unload-role-arn", required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--label-maturity-date", required=True)
    parser.add_argument("--raw-prefix", required=True)
    parser.add_argument("--output-dir", default="/opt/ml/processing/output/export")
    return parser.parse_args()


def wait_for_statement(client: object, statement_id: str) -> None:
    while True:
        status = client.describe_statement(Id=statement_id)
        if status["Status"] == "FINISHED":
            return
        if status["Status"] in {"FAILED", "ABORTED"}:
            raise RuntimeError(f"Redshift UNLOAD failed: {status.get('Error', status['Status'])}")
        time.sleep(2)


def unload_sql(query: str, destination: str, unload_role_arn: str) -> str:
    escaped_query = query.replace("'", "''")
    return (
        f"UNLOAD ('{escaped_query}') TO '{destination}' IAM_ROLE '{unload_role_arn}' "
        "FORMAT AS PARQUET MANIFEST VERBOSE;"
    )


def main() -> None:
    args = parse_args()
    as_of_date = date.fromisoformat(args.as_of_date)
    label_maturity_date = date.fromisoformat(args.label_maturity_date)
    if label_maturity_date > as_of_date:
        raise ValueError("label_maturity_date must be on or before as_of_date.")

    # Replace these three views with approved, PII-minimized source views.
    # The application view must only expose labels whose performance window has matured.
    queries = {
        "applications": f"""
            SELECT application_id, seller_id, observation_date, advance_amount, requested_limit,
                   default_payment_next_month
            FROM analytics.ml_seller_applications
            WHERE observation_date <= DATE '{label_maturity_date.isoformat()}'
        """,
        "seller_profiles": """
            SELECT seller_id, industry_code, business_age_days, registered_region
            FROM analytics.ml_seller_profiles
        """,
        "settlements": f"""
            SELECT seller_id, settlement_date, scheduled_amount, settled_amount, days_late
            FROM analytics.ml_daily_settlements
            WHERE settlement_date <= DATE '{as_of_date.isoformat()}'
        """,
    }

    client = boto3.client("redshift-data")
    raw_prefix = args.raw_prefix.rstrip("/")
    statement_ids: dict[str, str] = {}
    for dataset, query in queries.items():
        destination = f"{raw_prefix}/{dataset}/part_"
        response = client.execute_statement(
            WorkgroupName=args.workgroup_name,
            Database=args.database,
            Sql=unload_sql(query, destination, args.unload_role_arn),
        )
        statement_ids[dataset] = response["Id"]
        wait_for_statement(client, response["Id"])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "export-summary.json").write_text(
        json.dumps(
            {
                "raw_prefix": raw_prefix,
                "as_of_date": as_of_date.isoformat(),
                "label_maturity_date": label_maturity_date.isoformat(),
                "statement_ids": statement_ids,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
