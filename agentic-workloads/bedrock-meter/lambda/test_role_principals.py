import unittest
from unittest.mock import Mock, patch

from common import enforcement
from common.principals import (
    assumed_role_name,
    is_enforceable_principal,
    is_iam_role_arn,
    normalize_metering_principal,
)


SESSION_ARN = (
    'arn:aws:sts::123456789012:assumed-role/'
    'aws-reserved/sso.amazonaws.com/ap-northeast-2/AWSReservedSSO_Developer_abc/session-123'
)
ROLE_ARN = (
    'arn:aws:iam::123456789012:role/'
    'aws-reserved/sso.amazonaws.com/ap-northeast-2/AWSReservedSSO_Developer_abc'
)


class TestRolePrincipals(unittest.TestCase):
    def test_assumed_role_session_normalizes_to_stable_role(self):
        self.assertEqual(normalize_metering_principal(SESSION_ARN), ROLE_ARN)
        self.assertTrue(is_iam_role_arn(ROLE_ARN))
        self.assertEqual(assumed_role_name(SESSION_ARN), 'AWSReservedSSO_Developer_abc')

    def test_role_enforcement_is_opt_in(self):
        self.assertFalse(is_enforceable_principal(ROLE_ARN))
        self.assertTrue(is_enforceable_principal(ROLE_ARN, role_enforcement_enabled=True))

    def test_role_policy_uses_role_api(self):
        fake_iam = Mock()
        with patch.object(enforcement, 'iam', fake_iam):
            enforcement.attach_deny_policy(ROLE_ARN)
        fake_iam.put_role_policy.assert_called_once()
        fake_iam.put_user_policy.assert_not_called()
        self.assertEqual(
            fake_iam.put_role_policy.call_args.kwargs['RoleName'],
            'AWSReservedSSO_Developer_abc',
        )


if __name__ == '__main__':
    unittest.main()
