"""Custom resource: apply schema.sql and create least-privilege DB roles via the RDS Data API.

* ``bench_writer``: SELECT/INSERT/UPDATE/DELETE on the benchmark tables (worker).
* ``bench_reader``: SELECT only (API Lambda).

Passwords come from Secrets Manager. They are generated alphanumeric by CDK and
validated here (no quote, backslash or control characters, which CDK and the rotation
exclude) because PostgreSQL DDL cannot take bind parameters.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import boto3


def _with_resume_retry(call, attempts: int = 6):
    """Retry while an auto-paused Aurora Serverless v2 cluster resumes (DatabaseResumingException)."""
    for i in range(attempts):
        try:
            return call()
        except Exception as e:  # noqa: BLE001 - re-raised unless it is the resume signal
            code = getattr(e, "response", {}).get("Error", {}).get("Code")
            if code != "DatabaseResumingException" or i == attempts - 1:
                raise
            time.sleep(min(2**i, 15))


rds = boto3.client("rds-data")
sm = boto3.client("secretsmanager")

CLUSTER = os.environ["CLUSTER_ARN"]
ADMIN = os.environ["ADMIN_SECRET_ARN"]
DB = os.environ["DB_NAME"]
USERS = {
    "bench_writer": (os.environ["WRITER_SECRET_ARN"], "SELECT, INSERT, UPDATE, DELETE"),
    "bench_reader": (os.environ["READER_SECRET_ARN"], "SELECT"),
}
# Printable ASCII except ' and \ : safe inside a standard-conforming SQL string literal.
_SAFE = re.compile(r"^[\x21-\x26\x28-\x5b\x5d-\x7e]{16,128}$")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _sql(statement: str) -> None:
    _with_resume_retry(lambda: rds.execute_statement(resourceArn=CLUSTER, secretArn=ADMIN, database=DB, sql=statement))


def _statements(text: str) -> list[str]:
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def _secret(arn: str) -> dict:
    return json.loads(sm.get_secret_value(SecretId=arn)["SecretString"])


def apply() -> None:
    for stmt in _statements((Path(__file__).parent / "schema.sql").read_text()):
        _sql(stmt)
    for user, (arn, privileges) in USERS.items():
        pw = _secret(arn)["password"]
        if not _SAFE.match(pw) or not _IDENT.match(user):
            raise ValueError(f"refusing unsafe credentials for {user}")
        create = f"CREATE ROLE {user} LOGIN PASSWORD '{pw}'"  # nosec B608 - user/pw regex-validated; DDL takes no binds
        exists = f"SELECT FROM pg_roles WHERE rolname = '{user}'"  # nosec B608 - user regex-validated
        _sql(f"DO $$ BEGIN IF NOT EXISTS ({exists}) THEN {create}; END IF; END $$")
        _sql(f"GRANT CONNECT ON DATABASE {DB} TO {user}")
        _sql(f"GRANT USAGE ON SCHEMA public TO {user}")
        _sql(f"GRANT {privileges} ON ALL TABLES IN SCHEMA public TO {user}")
        if user == "bench_writer":
            _sql(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {user}")


def handler(event, _context):
    if event["RequestType"] in ("Create", "Update"):
        apply()
    return {"PhysicalResourceId": "bench-schema"}
