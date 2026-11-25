import importlib.util
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

spec = importlib.util.spec_from_file_location('api_overview', LAMBDA_DIR / 'api-handler' / 'index.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class TestOverviewPaging(unittest.TestCase):
    def test_status_summary_and_user_count_cover_all_rows(self):
        original = api._build_user_rows
        statuses = ['normal'] * 190 + ['warning'] * 7 + ['blocked'] * 5 + ['exempt'] * 3
        rows = [
            {
                'user_arn': f'arn:aws:iam::123456789012:user/u{i:03d}',
                'display_name': f'u{i:03d}',
                'status': status,
                'today_total_tokens': i,
                'week_total_tokens': i,
                'month_total_tokens': i,
                'last_updated': None,
            }
            for i, status in enumerate(statuses)
        ]
        api._build_user_rows = lambda: rows
        try:
            page = api.list_users_page(5)
        finally:
            api._build_user_rows = original

        self.assertEqual(len(page['rows']), 5)
        self.assertEqual(page['all_user_count'], 205)
        self.assertEqual(page['total_count'], 205)
        self.assertEqual(page['status_counts'], {
            'normal': 190,
            'warning': 7,
            'blocked': 5,
            'exempt': 3,
        })


if __name__ == '__main__':
    unittest.main()
