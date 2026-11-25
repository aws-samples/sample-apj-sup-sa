#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any


AWS_TARGET_REGION: str | None = None


def resolve_region() -> str:
    region = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION')
    if not region:
        result = subprocess.run(
            ['aws', 'configure', 'get', 'region'], capture_output=True, text=True,
        )
        if result.returncode:
            raise SystemExit(
                'Cannot resolve AWS region. Set AWS_REGION or configure your AWS profile.\n'
                + result.stderr.strip()
            )
        region = result.stdout.strip()
    if not re.fullmatch(r'[a-z][a-z0-9-]*-[0-9]+', region):
        raise SystemExit('Missing or invalid AWS region. Set AWS_REGION or configure your AWS profile.')
    return region


def aws_json(*args: str) -> dict[str, Any]:
    if not AWS_TARGET_REGION:
        raise RuntimeError('Resolve the AWS target before running retention operations.')
    result = subprocess.run(
        ['aws', *args, '--region', AWS_TARGET_REGION, '--output', 'json'],
        capture_output=True,
        text=True,
        env={**os.environ, 'AWS_PAGER': '', 'AWS_CLI_AUTO_PROMPT': 'off'},
    )
    if result.returncode:
        raise SystemExit(result.stderr.strip() or f'AWS CLI failed with exit code {result.returncode}.')
    output = result.stdout.strip()
    return json.loads(output) if output else {}


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def desired_expiry(timestamp: str, retention_days: int) -> int:
    return int((parse_timestamp(timestamp) + timedelta(days=retention_days)).timestamp())


def scan_table(
    table_name: str,
    projection_expression: str,
    expression_attribute_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    start_key: dict[str, Any] | None = None
    while True:
        args = [
            'dynamodb',
            'scan',
            '--table-name',
            table_name,
            '--projection-expression',
            projection_expression,
        ]
        if expression_attribute_names:
            args.extend([
                '--expression-attribute-names',
                json.dumps(expression_attribute_names, separators=(',', ':')),
            ])
        if start_key:
            args.extend([
                '--exclusive-start-key',
                json.dumps(start_key, separators=(',', ':')),
            ])
        page = aws_json(*args)
        items.extend(page.get('Items', []))
        start_key = page.get('LastEvaluatedKey')
        if not start_key:
            return items


def update_expiry(
    table_name: str,
    key: dict[str, Any],
    current_expiry: int,
    new_expiry: int,
) -> bool:
    if current_expiry >= new_expiry:
        return False
    aws_json(
        'dynamodb',
        'update-item',
        '--table-name',
        table_name,
        '--key',
        json.dumps(key, separators=(',', ':')),
        '--update-expression',
        'SET expiresAt = :expiry',
        '--condition-expression',
        'attribute_not_exists(expiresAt) OR expiresAt < :expiry',
        '--expression-attribute-values',
        json.dumps({':expiry': {'N': str(new_expiry)}}, separators=(',', ':')),
    )
    return True


def backfill_usage(table_name: str, retention_days: int) -> tuple[int, int]:
    items = scan_table(
        table_name,
        'UserARN, #sk, expiresAt, lastUpdated',
        {'#sk': 'SK'},
    )
    scanned = updated = 0
    for item in items:
        sk = item.get('SK', {}).get('S', '')
        timestamp = item.get('lastUpdated', {}).get('S')
        if not sk.startswith('DAY#') or not timestamp:
            continue
        scanned += 1
        expiry = desired_expiry(timestamp, retention_days)
        current = int(item.get('expiresAt', {}).get('N', '0'))
        if update_expiry(
            table_name,
            {'UserARN': item['UserARN'], 'SK': item['SK']},
            current,
            expiry,
        ):
            updated += 1
    return scanned, updated


def backfill_audit(table_name: str, retention_days: int) -> tuple[int, int]:
    items = scan_table(table_name, 'PK, #sk, expiresAt, #timestamp', {
        '#sk': 'SK',
        '#timestamp': 'timestamp',
    })
    scanned = updated = 0
    for item in items:
        timestamp = item.get('timestamp', {}).get('S')
        if not timestamp:
            continue
        scanned += 1
        expiry = desired_expiry(timestamp, retention_days)
        current = int(item.get('expiresAt', {}).get('N', '0'))
        if update_expiry(
            table_name,
            {'PK': item['PK'], 'SK': item['SK']},
            current,
            expiry,
        ):
            updated += 1
    return scanned, updated


def main() -> None:
    global AWS_TARGET_REGION
    parser = argparse.ArgumentParser()
    parser.add_argument('--usage-table', required=True)
    parser.add_argument('--audit-table', required=True)
    parser.add_argument('--usage-days', type=int, default=90)
    parser.add_argument('--audit-days', type=int, default=730)
    args = parser.parse_args()
    if args.usage_days < 1 or args.audit_days < 1:
        parser.error('Retention days must be positive.')

    AWS_TARGET_REGION = resolve_region()
    account = aws_json('sts', 'get-caller-identity').get('Account', '')
    if not re.fullmatch(r'[0-9]{12}', account):
        raise SystemExit('STS did not return a valid AWS account ID.')
    print(f'AWS target: account={account} region={AWS_TARGET_REGION}', flush=True)
    print(f'Tables: usage={args.usage_table} audit={args.audit_table}', flush=True)
    usage = backfill_usage(args.usage_table, args.usage_days)
    audit = backfill_audit(args.audit_table, args.audit_days)
    print(f'Usage DAY rows: scanned={usage[0]} extended={usage[1]}')
    print(f'Audit rows: scanned={audit[0]} extended={audit[1]}')


if __name__ == '__main__':
    main()
