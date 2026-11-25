"""Unit tests for the usage-aggregator hot-path transaction after the RollupTable write removal.

Pins two invariants that survive dropping the WEEK/MONTH rollup ADDs:
  - The transaction is now exactly 2 items (dedup claim + DAY ADD) — no rollup items.
  - The dedup claim SK is date-INDEPENDENT (`BATCH#<sha16>`, no date prefix), so a redelivery
    that crosses quota-calendar midnight collides on the SAME claim and never re-applies the DAY ADD
    (invariant I1). A range-sum read does not heal a double-counted DAY row, so this claim is
    the only double-count guard now that rollups are gone.

Run: python3 -m unittest test_aggregator_dedup   (from the lambda/ dir)
"""
import base64
import gzip
import importlib.util
import json
import os
import pathlib
import sys
import unittest
from unittest.mock import patch

# Resolve relative to this file so the suite runs from any cwd (repo root or lambda/).
LAMBDA_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(LAMBDA_DIR))

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-northeast-2')
for _k in ('USAGE_TABLE', 'CONFIG_TABLE', 'DEDUP_TABLE'):
    os.environ.setdefault(_k, 'dummy')
os.environ.setdefault('SNS_TOPIC_ARN', 'arn:aws:sns:ap-northeast-2:1:dummy')


def _load(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, LAMBDA_DIR / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agg = _load('agg_idx', 'usage-aggregator/index.py')


def _make_event(arn: str = 'arn:aws:iam::1:user/alice') -> dict:
    msg = json.dumps({
        'identity': {'arn': arn},
        'modelId': 'anthropic.claude',
        'input': {'inputTokenCount': 100, 'cacheReadInputTokenCount': 880, 'cacheWriteInputTokenCount': 20},
        'output': {'outputTokenCount': 50},
    })
    payload = {'logEvents': [{'id': '1', 'message': msg}]}
    raw = gzip.compress(json.dumps(payload).encode())
    return {'awslogs': {'data': base64.b64encode(raw).decode()}}


class _ClaimStore:
    """Minimal stand-in for transact_write_items enforcing the dedup ConditionExpression
    on item 0 (attribute_not_exists(claimedAt)) keyed by (UserARN, SK)."""

    def __init__(self) -> None:
        self.claimed: set[tuple[str, str]] = set()
        self.calls: list[list[dict]] = []

    def transact_write_items(self, TransactItems):  # noqa: N803 (boto3 kwarg name)
        self.calls.append(TransactItems)
        claim = TransactItems[0]['Put']
        key = (claim['Item']['UserARN']['S'], claim['Item']['SK']['S'])
        if key in self.claimed:
            raise agg.ClientError(
                {'Error': {'Code': 'TransactionCanceledException'},
                 'CancellationReasons': [{'Code': 'ConditionalCheckFailed'}, {'Code': 'None'}]},
                'TransactWriteItems',
            )
        self.claimed.add(key)


class _ConflictThenSuccessStore(_ClaimStore):
    def __init__(self, conflicts: int) -> None:
        super().__init__()
        self.conflicts = conflicts

    def transact_write_items(self, TransactItems):  # noqa: N803
        if self.conflicts > 0:
            self.conflicts -= 1
            self.calls.append(TransactItems)
            raise agg.ClientError(
                {
                    'Error': {'Code': 'TransactionCanceledException'},
                    'CancellationReasons': [{'Code': 'None'}, {'Code': 'TransactionConflict'}],
                },
                'TransactWriteItems',
            )
        super().transact_write_items(TransactItems)


class TestTransactionShape(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _ClaimStore()
        self._orig = agg.ddb_client
        agg.ddb_client = self.store

    def tearDown(self) -> None:
        agg.ddb_client = self._orig

    def test_two_item_transaction(self):
        applied = agg.claim_and_update('arn:aws:iam::1:user/alice', '2026-06-29', 'deadbeef', 100, 880, 20, 50, 1)
        self.assertTrue(applied)
        self.assertEqual(len(self.store.calls), 1)
        items = self.store.calls[0]
        self.assertEqual(len(items), 2, 'transaction must be exactly [dedup claim, DAY ADD]')
        self.assertIn('Put', items[0])
        self.assertIn('Update', items[1])
        self.assertEqual(items[1]['Update']['Key']['SK']['S'], 'DAY#2026-06-29')

    def test_day_add_payload_maps_each_component(self):
        # Guard against attribute swaps / a wrong inputTokens sum in the DAY ADD expression.
        agg.claim_and_update('arn:aws:iam::1:user/alice', '2026-06-29', 'd1', 100, 880, 20, 50, 3)
        update = self.store.calls[0][1]['Update']
        vals = update['ExpressionAttributeValues']
        # inputTokens is the enforcement sum raw+cacheRead+cacheWrite = 100+880+20 = 1000.
        self.assertEqual(vals[':inp']['N'], '1000')
        self.assertEqual(vals[':raw']['N'], '100')
        self.assertEqual(vals[':cr']['N'], '880')
        self.assertEqual(vals[':cw']['N'], '20')
        self.assertEqual(vals[':out']['N'], '50')
        self.assertEqual(vals[':n']['N'], '3')
        self.assertIn('ADD inputTokens :inp', update['UpdateExpression'])

    def test_policy_version_isolates_timezone_counter_namespace(self):
        agg.claim_and_update(
            'arn:aws:iam::1:user/alice',
            '2026-08-06',
            'tz-v2',
            1, 0, 0, 0, 1,
            policy_version=2,
            time_zone='Asia/Seoul',
        )
        claim = self.store.calls[0][0]['Put']['Item']
        update = self.store.calls[0][1]['Update']
        self.assertEqual(claim['quotaDay']['S'], '2026-08-06')
        self.assertEqual(claim['quotaTimeZone']['S'], 'Asia/Seoul')
        self.assertEqual(claim['quotaPolicyVersion']['N'], '2')
        self.assertEqual(update['Key']['SK']['S'], 'DAY#V2#2026-08-06')
        self.assertEqual(update['ExpressionAttributeValues'][':tz']['S'], 'Asia/Seoul')

    def test_dedup_sk_has_no_date_prefix(self):
        agg.claim_and_update('arn:aws:iam::1:user/alice', '2026-06-29', 'cafef00d', 100, 880, 20, 50, 1)
        sk = self.store.calls[0][0]['Put']['Item']['SK']['S']
        self.assertEqual(sk, 'BATCH#cafef00d', 'dedup SK must be date-independent (invariant I1)')

    def test_midnight_crossing_redelivery_collides(self):
        # Same batch_id, but the retry lands on the NEXT quota-calendar day.
        a1 = agg.claim_and_update('arn:aws:iam::1:user/alice', '2026-06-29', 'b1', 100, 880, 20, 50, 1)
        a2 = agg.claim_and_update('arn:aws:iam::1:user/alice', '2026-06-30', 'b1', 100, 880, 20, 50, 1)
        self.assertTrue(a1)
        self.assertFalse(a2, 'midnight-crossing redelivery must collide on the same claim, not re-apply')
        # Both calls used the SAME dedup SK despite the different DAY key.
        sk1 = self.store.calls[0][0]['Put']['Item']['SK']['S']
        sk2 = self.store.calls[1][0]['Put']['Item']['SK']['S']
        self.assertEqual(sk1, sk2)

    def test_distinct_batches_both_apply(self):
        a1 = agg.claim_and_update('arn:aws:iam::1:user/alice', '2026-06-29', 'aaa', 1, 0, 0, 0, 1)
        a2 = agg.claim_and_update('arn:aws:iam::1:user/alice', '2026-06-29', 'bbb', 1, 0, 0, 0, 1)
        self.assertTrue(a1)
        self.assertTrue(a2)

    def test_transaction_conflict_retries_then_applies_once(self):
        self.store = _ConflictThenSuccessStore(conflicts=2)
        agg.ddb_client = self.store

        with patch.object(agg.time, 'sleep') as sleep:
            applied = agg.claim_and_update(
                'arn:aws:iam::1:user/alice', '2026-06-29', 'retry', 1, 0, 0, 0, 1,
            )

        self.assertTrue(applied)
        self.assertEqual(len(self.store.calls), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(len(self.store.claimed), 1)

    def test_transaction_conflict_raises_after_retry_budget(self):
        self.store = _ConflictThenSuccessStore(conflicts=10)
        agg.ddb_client = self.store

        with (
            patch.object(agg, 'DDB_TRANSACTION_MAX_ATTEMPTS', 3),
            patch.object(agg.time, 'sleep') as sleep,
            self.assertRaises(agg.ClientError),
        ):
            agg.claim_and_update(
                'arn:aws:iam::1:user/alice', '2026-06-29', 'exhausted', 1, 0, 0, 0, 1,
            )

        self.assertEqual(len(self.store.calls), 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == '__main__':
    unittest.main()
