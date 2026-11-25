import importlib.util
import json
import os
import pathlib
import sys
import unittest

LAMBDA_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(LAMBDA_DIR))

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-northeast-2')
for key in ('USAGE_TABLE', 'CONFIG_TABLE', 'AUDIT_TABLE'):
    os.environ.setdefault(key, 'dummy')
os.environ.setdefault('SNS_TOPIC_ARN', 'arn:aws:sns:ap-northeast-2:123456789012:dummy')
for key in ('ATHENA_DATABASE', 'ATHENA_TABLE', 'ATHENA_WORKGROUP'):
    os.environ.setdefault(key, 'dummy')
os.environ.setdefault('ACCOUNT_ID', '123456789012')

spec = importlib.util.spec_from_file_location(
    'api_global_quota_settings',
    LAMBDA_DIR / 'api-handler' / 'index.py',
)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class FakeConfigTable:
    def __init__(self):
        self.item = {'ConfigKey': 'default'}
        self.updates = 0

    def get_item(self, **kwargs):
        return {'Item': dict(self.item)}

    def update_item(self, **kwargs):
        self.updates += 1
        values = kwargs['ExpressionAttributeValues']
        self.item['quotaTimeZone'] = values[':tz']
        self.item['quotaTimeZoneVersion'] = values[':next']
        return {}


class FakeSns:
    def __init__(self):
        self.messages = []

    def publish(self, **kwargs):
        self.messages.append(json.loads(kwargs['Message']))


class TestGlobalQuotaSettings(unittest.TestCase):
    def setUp(self):
        self.original_table = api.config_table
        self.original_sns = api.sns
        self.table = FakeConfigTable()
        self.sns = FakeSns()
        api.config_table = self.table
        api.sns = self.sns

    def tearDown(self):
        api.config_table = self.original_table
        api.sns = self.original_sns

    def test_existing_install_defaults_to_legacy_utc_policy(self):
        self.assertEqual(api.get_global_quota_policy(), {
            'mode': 'CALENDAR',
            'quotaTimeZone': 'UTC',
            'quotaTimeZoneVersion': 1,
        })

    def test_timezone_change_bumps_namespace_and_audits(self):
        result = api.update_global_quota_time_zone(
            'Asia/Seoul',
            'arn:aws:sts::123456789012:assumed-role/BedrockMeterConsoleBffRole/s',
            'Korean operating day',
            {'operator_email': 'ops@example.com'},
        )
        self.assertTrue(result['updated'])
        self.assertTrue(result['countersReset'])
        self.assertEqual(result['quotaTimeZoneVersion'], 2)
        self.assertEqual(self.table.item['quotaTimeZone'], 'Asia/Seoul')
        self.assertEqual(self.sns.messages[0]['event'], 'QUOTA_TIMEZONE_CHANGED')
        self.assertEqual(self.sns.messages[0]['reason'], 'Korean operating day')

    def test_same_timezone_is_noop(self):
        result = api.update_global_quota_time_zone('UTC', 'caller', 'same')
        self.assertFalse(result['updated'])
        self.assertEqual(self.table.updates, 0)
        self.assertEqual(self.sns.messages, [])

    def test_invalid_timezone_is_rejected(self):
        with self.assertRaises(ValueError):
            api.update_global_quota_time_zone('Mars/Olympus', 'caller', 'bad')


if __name__ == '__main__':
    unittest.main()
