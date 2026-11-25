import importlib.util
import os
import pathlib
import sys
import unittest

LAMBDA_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(LAMBDA_DIR))

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-northeast-2')
os.environ.setdefault('USAGE_TABLE', 'dummy')
os.environ.setdefault('CONFIG_TABLE', 'dummy')
os.environ.setdefault('SNS_TOPIC_ARN', 'arn:aws:sns:ap-northeast-2:1:dummy')


def _load():
    spec = importlib.util.spec_from_file_location(
        'periodic_warning_test', LAMBDA_DIR / 'periodic-quota-check' / 'index.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pqc = _load()


class TestWarningDedup(unittest.TestCase):
    def setUp(self):
        self.originals = (
            pqc.mark_warned_state,
            pqc.publish_alert,
            pqc.mark_blocked_if_new,
        )
        self.marked: list[str] = []
        pqc.mark_warned_state = (
            lambda arn, scope, period: self.marked.append(f'{scope}#{period}')
        )
        pqc.publish_alert = lambda *args, **kwargs: None
        pqc.mark_blocked_if_new = lambda *args: False

    def tearDown(self):
        (
            pqc.mark_warned_state,
            pqc.publish_alert,
            pqc.mark_blocked_if_new,
        ) = self.originals

    def test_week_and_month_warning_are_independently_deduped(self):
        cfg = {
            'warningThresholdPct': 80,
            'weeklyTokenLimit': 500,
            'monthlyTokenLimit': 1000,
        }
        warned: dict[str, str] = {}
        for _ in range(4):
            pqc.evaluate_user(
                'arn:aws:iam::123456789012:user/alice',
                cfg,
                450,
                900,
                '2026-W30',
                '2026-07',
                warned,
            )
            if self.marked:
                scope = self.marked[-1].split('#', 1)[0]
                warned[scope] = self.marked[-1]

        self.assertEqual(self.marked, ['MONTH#2026-07', 'WEEK#2026-W30'])


if __name__ == '__main__':
    unittest.main()
