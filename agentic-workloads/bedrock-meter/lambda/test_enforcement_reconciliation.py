import os
import pathlib
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-northeast-2')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from botocore.exceptions import ClientError

from common import enforcement


class FakeTable:
    def __init__(self, reblock: bool = False):
        self.blocked = True
        self.reblock = reblock

    def update_item(self, **kwargs):
        expression = kwargs['UpdateExpression']
        if expression == 'SET blocked = :f':
            self.blocked = False
            return {}
        if expression == 'SET blocked = :t':
            self.blocked = True
            return {}
        if expression.startswith('REMOVE'):
            if self.reblock:
                self.blocked = True
            if self.blocked:
                raise ClientError(
                    {'Error': {'Code': 'ConditionalCheckFailedException'}},
                    'UpdateItem',
                )
            return {}
        raise AssertionError(expression)


class TestUnblockReconciliation(unittest.TestCase):
    @patch.object(enforcement, 'ensure_deny_policy')
    @patch.object(enforcement, 'remove_deny_policy', return_value=True)
    def test_successful_unblock(self, remove, ensure):
        result = enforcement.unblock_with_reconciliation(FakeTable(), 'arn:aws:iam::123456789012:user/alice')
        self.assertEqual(result, {'unblocked': True, 'policy_removed': True})
        ensure.assert_not_called()

    @patch.object(enforcement, 'ensure_deny_policy')
    @patch.object(enforcement, 'remove_deny_policy', return_value=True)
    def test_concurrent_reblock_restores_policy(self, remove, ensure):
        arn = 'arn:aws:iam::123456789012:user/alice'
        result = enforcement.unblock_with_reconciliation(FakeTable(reblock=True), arn)
        self.assertFalse(result['unblocked'])
        ensure.assert_called_once_with(arn)

    @patch.object(enforcement, 'remove_deny_policy', side_effect=RuntimeError('iam down'))
    def test_remove_failure_restores_blocked_state(self, remove):
        table = FakeTable()
        with self.assertRaises(RuntimeError):
            enforcement.unblock_with_reconciliation(
                table, 'arn:aws:iam::123456789012:user/alice'
            )
        self.assertTrue(table.blocked)


if __name__ == '__main__':
    unittest.main()
