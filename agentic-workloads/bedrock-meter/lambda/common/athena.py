import time
from typing import Any

import boto3

athena = boto3.client('athena')

DEFAULT_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 0.5


def run_query(
    sql: str,
    *,
    database: str,
    workgroup: str,
    parameters: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, str]]:
    """Run an Athena SQL query and return rows as list of dicts.

    Use `?` placeholders in `sql` and pass values via `parameters`. Each
    parameter must be a fully-quoted SQL literal (e.g. "'arn:...'", '5').
    """
    kwargs: dict[str, Any] = {
        'QueryString': sql,
        'QueryExecutionContext': {'Database': database},
        'WorkGroup': workgroup,
    }
    if parameters:
        kwargs['ExecutionParameters'] = parameters
    resp = athena.start_query_execution(**kwargs)
    qid = resp['QueryExecutionId']

    deadline = time.time() + timeout
    while time.time() < deadline:
        info = athena.get_query_execution(QueryExecutionId=qid)['QueryExecution']
        state = info['Status']['State']
        if state == 'SUCCEEDED':
            break
        if state in ('FAILED', 'CANCELLED'):
            reason = info['Status'].get('StateChangeReason', '')
            raise RuntimeError(f'Athena {qid} {state}: {reason}')
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        raise TimeoutError(f'Athena {qid} timed out after {timeout}s')

    rows: list[dict[str, str]] = []
    paginator = athena.get_paginator('get_query_results')
    header: list[str] | None = None
    for page in paginator.paginate(QueryExecutionId=qid):
        for r in page['ResultSet']['Rows']:
            values = [c.get('VarCharValue', '') for c in r['Data']]
            if header is None:
                header = values
                continue
            rows.append(dict(zip(header, values)))
    return rows
