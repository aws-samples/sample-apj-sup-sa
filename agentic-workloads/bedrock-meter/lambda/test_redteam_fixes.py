"""Unit tests for the red-team review fixes (#3 audit boundary, #4 midnight dup, #5 limit:0).

The TS-side fixes (#1 SigV4 space encoding and #6 viewer-downgrade) are covered by their own
checks; this file pins the Python-side ones against regression.

Run: python3 -m unittest test_redteam_fixes   (from repo root or lambda/)
"""
import importlib.util
import os
import pathlib
import sys
import unittest

LAMBDA_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(LAMBDA_DIR))

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-northeast-2')
for _k in ('USAGE_TABLE', 'CONFIG_TABLE', 'AUDIT_TABLE', 'DEDUP_TABLE'):
    os.environ.setdefault(_k, 'dummy')
os.environ.setdefault('SNS_TOPIC_ARN', 'arn:aws:sns:ap-northeast-2:1:dummy')
for _k in ('ATHENA_DATABASE', 'ATHENA_TABLE', 'ATHENA_WORKGROUP'):
    os.environ.setdefault(_k, 'dummy')
os.environ.setdefault('ACCOUNT_ID', '803936485311')


def _load(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, LAMBDA_DIR / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = _load('api_idx_rt', 'api-handler/index.py')
agg = _load('agg_idx_rt', 'usage-aggregator/index.py')

TARGET_ARN = 'arn:aws:iam::803936485311:user/alice'


class TestLimitZeroRejected(unittest.TestCase):
    """#5: limit 0 must be rejected — enforcement treats <=0 as 'unenforced', so 0 would
    silently disable the scope rather than block all usage."""

    def setUp(self):
        # The hierarchy check at the end of _validate_limit_body reads effective config; stub it
        # so valid-input cases stay pure units (no DDB). The 0/negative cases raise before this.
        self._orig = api.get_effective_config
        api.get_effective_config = lambda arn: {}

    def tearDown(self):
        api.get_effective_config = self._orig

    def test_zero_daily_rejected(self):
        with self.assertRaises(ValueError) as cm:
            api._validate_limit_body({'dailyTokenLimit': 0}, TARGET_ARN)
        self.assertIn('disable enforcement', str(cm.exception))

    def test_zero_weekly_and_monthly_rejected(self):
        for k in ('weeklyTokenLimit', 'monthlyTokenLimit'):
            with self.assertRaises(ValueError):
                api._validate_limit_body({k: 0}, TARGET_ARN)

    def test_one_is_accepted(self):
        # 1 is the smallest enforceable limit; should pass validation (no raise).
        api._validate_limit_body(
            {'dailyTokenLimit': 1, 'weeklyTokenLimit': 1, 'monthlyTokenLimit': 1},
            TARGET_ARN,
        )

    def test_negative_still_rejected(self):
        with self.assertRaises(ValueError):
            api._validate_limit_body({'dailyTokenLimit': -5}, TARGET_ARN)

    def test_admin_may_change_own_limit_or_exemption(self):
        api._validate_limit_body({'dailyTokenLimit': 2_000_000}, TARGET_ARN)
        api._validate_limit_body({'exempt': True}, TARGET_ARN)


class TestAuditUpperBoundSK(unittest.TestCase):
    """#3: the `to` upper-bound SK must sort ABOVE every same-day stored SK, which carries the
    '+00:00' offset (offset '+' sorts above '#'). The fixed bound is '{date}T\\uffff'."""

    def _stored_sk(self, ts: str, event_id: str = 'abc') -> str:
        return f'{ts}#{event_id}'

    def test_bound_covers_end_of_day_record(self):
        # The buggy old bound '...999999#ZZ' (no offset) failed this.
        hi = f'2026-06-29T￿'
        end_of_day = self._stored_sk('2026-06-29T23:59:59.999999+00:00', 'zzzzzzzz')
        start_of_day = self._stored_sk('2026-06-29T00:00:00.000000+00:00', '0')
        self.assertGreater(hi, end_of_day)   # inclusive of the last record of the day
        self.assertGreater(hi, start_of_day)

    def test_bound_excludes_next_day(self):
        hi = f'2026-06-29T￿'
        next_day = self._stored_sk('2026-06-30T00:00:00.000000+00:00', '0')
        self.assertLess(hi, next_day)        # does not bleed into 2026-06-30

    def test_old_buggy_bound_would_have_excluded(self):
        # Regression guard: demonstrate the old bound dropped the boundary record.
        old_hi = '2026-06-29T23:59:59.999999#ZZ'
        end_of_day = self._stored_sk('2026-06-29T23:59:59.999999+00:00', 'a')
        self.assertLess(old_hi, end_of_day)  # old bound < record → record wrongly excluded


class TestClaimIngestionDay(unittest.TestCase):
    """#4: on a duplicate redelivery, the threshold recheck must use the ORIGINAL ingestion day
    (from the dedup claim's claimedAt), not today, so a midnight-crossing retry still blocks."""

    def setUp(self):
        self._orig = agg.dedup_table

    def tearDown(self):
        agg.dedup_table = self._orig

    def test_returns_claim_day_not_today(self):
        class FakeTable:
            def get_item(self, **kw):
                return {'Item': {'claimedAt': '2026-06-29T23:59:00.000000+00:00'}}
        agg.dedup_table = FakeTable()
        self.assertEqual(agg.claim_ingestion_day('u', 'b', fallback_day='2026-06-30'), '2026-06-29')

    def test_falls_back_when_claim_missing(self):
        class FakeTable:
            def get_item(self, **kw):
                return {}
        agg.dedup_table = FakeTable()
        self.assertEqual(agg.claim_ingestion_day('u', 'b', fallback_day='2026-06-30'), '2026-06-30')

    def test_falls_back_on_client_error(self):
        class FakeTable:
            def get_item(self, **kw):
                raise agg.ClientError({'Error': {'Code': 'X'}}, 'GetItem')
        agg.dedup_table = FakeTable()
        self.assertEqual(agg.claim_ingestion_day('u', 'b', fallback_day='2026-06-30'), '2026-06-30')


if __name__ == '__main__':
    unittest.main()
