"""Unit tests for DAY-row component extraction after the legacy-inputTokens fallback removal.

Background: a "bare" DAY row (only `inputTokens`, no rawInputTokens/cacheReadTokens/
cacheWriteTokens) can ONLY be produced by pre-cache-split aggregator code. Two facts make the
removed `if not (raw|cr|cw): raw = inputTokens` fallback dead code, not an assumption:
  1. The current aggregator ADDs BOTH `inputTokens` AND the split fields on every write
     (usage-aggregator claim_and_update), so no new bare row can be created.
  2. The cache-split cutover predates the 90-day DAY TTL, so any pre-split bare rows have aged
     out. Verified by a full live scan (scripts/scan-bare-day-rows.py → 0 bare rows, 2026-06-29).
Before re-deploying to a NEW environment (or after a long aggregator outage), re-run that scan;
if it reports >0 bare rows, restore the fallback before removing it. These tests pin the
post-removal contract: readers sum the split components only; a bare-`inputTokens` row's input
side contributes nothing (outputTokens, a separate field, is still counted).

The handler modules live in hyphenated dirs and create boto3 clients + read env at import,
so we load them by path under dummy env (no AWS calls happen for these pure functions).

Run: python3 -m unittest test_day_components   (from the lambda/ dir)
"""
import importlib.util
import os
import pathlib
import sys
import unittest
from datetime import date

# Resolve everything relative to this file so the suite runs from any cwd (repo root or lambda/).
LAMBDA_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(LAMBDA_DIR))  # so handler modules' `from common...` imports resolve

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-northeast-2')
for _k in ('USAGE_TABLE', 'CONFIG_TABLE', 'DEDUP_TABLE', 'AUDIT_TABLE'):
    os.environ.setdefault(_k, 'dummy')
os.environ.setdefault('SNS_TOPIC_ARN', 'arn:aws:sns:ap-northeast-2:1:dummy')
for _k in ('ATHENA_DATABASE', 'ATHENA_TABLE', 'ATHENA_WORKGROUP'):
    os.environ.setdefault(_k, 'dummy')
os.environ.setdefault('ATHENA_OUTPUT', 's3://dummy/')
os.environ.setdefault('ACCOUNT_ID', '1')


def _load(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, LAMBDA_DIR / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pqc = _load('pqc_idx', 'periodic-quota-check/index.py')
api = _load('api_idx', 'api-handler/index.py')

SPLIT_ROW = {
    'rawInputTokens': 100, 'cacheReadTokens': 880, 'cacheWriteTokens': 20, 'outputTokens': 50,
}
# Pre cache-split shape (now extinct): only the collapsed `inputTokens` on the input side.
# `outputTokens` was always a separate field, so it is still counted — only the bare
# `inputTokens` is no longer back-mapped to raw.
LEGACY_ROW = {'inputTokens': 1000, 'outputTokens': 50}
EMPTY_ROW: dict = {}


class TestPeriodicDayComponents(unittest.TestCase):
    def test_split_row_summed(self):
        self.assertEqual(pqc._day_components(SPLIT_ROW), (100, 880, 20, 50))

    def test_legacy_input_no_longer_back_mapped(self):
        # The removed fallback used to map inputTokens -> raw; now the bare input is ignored
        # (such rows are extinct). outputTokens is a separate field and still counted.
        self.assertEqual(pqc._day_components(LEGACY_ROW), (0, 0, 0, 50))

    def test_empty_row(self):
        self.assertEqual(pqc._day_components(EMPTY_ROW), (0, 0, 0, 0))

    def test_scan_ignores_prior_quota_policy_namespace(self):
        class FakeUsageTable:
            def scan(self, **kwargs):
                return {
                    'Items': [
                        {'UserARN': 'u', 'SK': 'DAY#2026-08-05', **SPLIT_ROW},
                        {'UserARN': 'u', 'SK': 'DAY#V2#2026-08-05', **SPLIT_ROW},
                    ]
                }

        original = pqc.usage_table
        pqc.usage_table = FakeUsageTable()
        try:
            week, month, day = pqc.scan_usage_by_user(
                date(2026, 8, 5),
                date(2026, 8, 3),
                date(2026, 8, 1),
                {'cache_read': 0.1, 'cache_write': 1.25, 'output': 5.0},
                policy_version=2,
            )
        finally:
            pqc.usage_table = original

        self.assertEqual(day['u'], 463)
        self.assertEqual(week['u'], 463)
        self.assertEqual(month['u'], 463)


class TestApiSumDayComponents(unittest.TestCase):
    def test_split_rows_summed(self):
        self.assertEqual(api.sum_day_components([SPLIT_ROW, SPLIT_ROW]), (200, 1760, 40, 100))

    def test_legacy_input_no_longer_back_mapped(self):
        self.assertEqual(api.sum_day_components([LEGACY_ROW]), (0, 0, 0, 50))


class TestApiAccumulateComponents(unittest.TestCase):
    def test_accumulate_split(self):
        target = [0, 0, 0, 0]
        api._accumulate_components(target, SPLIT_ROW)
        api._accumulate_components(target, SPLIT_ROW)
        self.assertEqual(target, [200, 1760, 40, 100])

    def test_accumulate_legacy_input_no_longer_back_mapped(self):
        target = [0, 0, 0, 0]
        api._accumulate_components(target, LEGACY_ROW)
        self.assertEqual(target, [0, 0, 0, 50])


class TestApiDayTotalWeighting(unittest.TestCase):
    def test_day_total_uses_split_components(self):
        # Seed weights: cache_read 0.1, cache_write 1.25, output 5, raw 1.
        weights = {'cache_read': 0.1, 'cache_write': 1.25, 'output': 5.0}
        # 100*1 + 880*0.1 + 20*1.25 + 50*5 = 100 + 88 + 25 + 250 = 463
        self.assertEqual(api._day_total(SPLIT_ROW, weights), 463)

    def test_day_total_legacy_input_excluded(self):
        weights = {'cache_read': 0.1, 'cache_write': 1.25, 'output': 5.0}
        # output 50 -> 250; legacy inputTokens no longer counted toward raw credits.
        self.assertEqual(api._day_total(LEGACY_ROW, weights), 250)


class TestLatestActivityAcrossQuotaPolicies(unittest.TestCase):
    def test_keeps_last_activity_when_active_policy_has_no_rows(self):
        class FakeUsageTable:
            def __init__(self):
                self.calls = 0

            def query(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {
                        'Items': [
                            {'lastUpdated': '2026-08-05T01:00:00+00:00'},
                            {'lastUpdated': '2026-08-05T03:00:00+00:00'},
                        ],
                        'LastEvaluatedKey': {'UserARN': 'u', 'SK': 'DAY#2026-08-05'},
                    }
                return {'Items': [{'lastUpdated': '2026-08-05T02:00:00+00:00'}]}

        original = api.usage_table
        api.usage_table = FakeUsageTable()
        try:
            self.assertEqual(api.get_latest_activity('u'), '2026-08-05T03:00:00+00:00')
        finally:
            api.usage_table = original


if __name__ == '__main__':
    unittest.main()
