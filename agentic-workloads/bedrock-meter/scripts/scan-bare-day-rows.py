#!/usr/bin/env python3
"""Scan UsageTable for "bare" DAY rows — pre-cache-split rows that have `inputTokens` but none of
the split fields (rawInputTokens/cacheReadTokens/cacheWriteTokens).

The enforcement/console readers sum ONLY the split fields (the legacy inputTokens->raw fallback
was removed). That is safe iff no bare rows exist: a bare row would otherwise have its input side
counted as 0, understating week/month credits and weakening block/unblock. The current aggregator
writes BOTH inputTokens and the split fields, so it cannot create new bare rows; this script
confirms none survive from before the cache-split cutover.

Run before deploying the fallback removal to a NEW environment, or after a long aggregator outage:
    USAGE_TABLE=BedrockMeterUsageTable AWS_REGION=ap-northeast-2 python3 scripts/scan-bare-day-rows.py
Exit code 0 = clean (0 bare rows). Exit code 1 = bare rows found (restore the fallback first).
"""
import os
import sys

import boto3

USAGE_TABLE = os.environ.get('USAGE_TABLE', 'BedrockMeterUsageTable')
REGION = os.environ.get('AWS_REGION', 'ap-northeast-2')


def main() -> int:
    table = boto3.resource('dynamodb', region_name=REGION).Table(USAGE_TABLE)
    total = bare = 0
    examples: list[str] = []
    kwargs: dict = {
        'FilterExpression': 'begins_with(SK, :d)',
        'ExpressionAttributeValues': {':d': 'DAY#'},
        'ProjectionExpression': 'UserARN, SK, inputTokens, rawInputTokens, cacheReadTokens, cacheWriteTokens',
    }
    while True:
        resp = table.scan(**kwargs)
        for item in resp.get('Items', []):
            total += 1
            split = any(int(item.get(k, 0)) for k in ('rawInputTokens', 'cacheReadTokens', 'cacheWriteTokens'))
            if not split and int(item.get('inputTokens', 0)):
                bare += 1
                if len(examples) < 10:
                    examples.append(f"{item['UserARN']} / {item['SK']} (inputTokens={item.get('inputTokens')})")
        lek = resp.get('LastEvaluatedKey')
        if not lek:
            break
        kwargs['ExclusiveStartKey'] = lek

    print(f'{USAGE_TABLE} ({REGION}): {total} DAY rows, {bare} bare (inputTokens without split fields)')
    for e in examples:
        print('  ', e)
    if bare:
        print('BARE ROWS FOUND — restore the legacy-inputTokens fallback before relying on split-only reads.')
        return 1
    print('clean — split-only reads are safe.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
