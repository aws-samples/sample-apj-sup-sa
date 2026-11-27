from datetime import datetime, timezone
import unittest

from common.dates import (
    active_day_from_sk,
    day_sk,
    day_sk_prefix,
    quota_policy,
    quota_today,
    validate_time_zone,
)


class TestQuotaClock(unittest.TestCase):
    def test_local_day_uses_configured_zone(self):
        instant = datetime(2026, 8, 5, 15, 30, tzinfo=timezone.utc)
        self.assertEqual(quota_today('UTC', instant).isoformat(), '2026-08-05')
        self.assertEqual(quota_today('Asia/Seoul', instant).isoformat(), '2026-08-06')
        self.assertEqual(quota_today('America/Los_Angeles', instant).isoformat(), '2026-08-05')

    def test_invalid_zone_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_time_zone('Asia/Not-A-Place')

    def test_policy_defaults_preserve_legacy_utc_namespace(self):
        self.assertEqual(quota_policy({}), ('UTC', 1))
        self.assertEqual(day_sk('2026-08-05', 1), 'DAY#2026-08-05')

    def test_changed_policy_has_isolated_namespace(self):
        self.assertEqual(day_sk_prefix(2), 'DAY#V2#')
        self.assertEqual(day_sk('2026-08-06', 2), 'DAY#V2#2026-08-06')
        self.assertEqual(active_day_from_sk('DAY#V2#2026-08-06', 2), '2026-08-06')
        self.assertIsNone(active_day_from_sk('DAY#2026-08-06', 2))
        self.assertIsNone(active_day_from_sk('DAY#V3#2026-08-06', 2))

    def test_bad_policy_version_is_rejected(self):
        with self.assertRaises(ValueError):
            quota_policy({'quotaTimeZoneVersion': 0})


if __name__ == '__main__':
    unittest.main()
