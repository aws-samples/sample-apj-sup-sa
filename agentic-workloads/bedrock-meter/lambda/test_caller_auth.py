"""Unit tests for caller-identity auth after adding role-caller admin support (#6).

The console BFF signs Core calls with its own execution-ROLE creds (assumed-role ARN) instead
of IAM-user choichul keys. Core must accept that role as an ADMIN CALLER — matched on the role
NAME (the session suffix is non-deterministic) — WITHOUT weakening the enforcement TARGET path,
which is still IAM-user-only (validate_arn rejects sts:: ARNs).

Run: python3 -m unittest test_caller_auth   (from the lambda/ dir)
"""
import importlib.util
import os
import pathlib
import sys
import unittest

# Resolve relative to this file so the suite runs from any cwd (repo root or lambda/).
LAMBDA_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(LAMBDA_DIR))

from common.principals import assumed_role_name, validate_arn, UnsupportedPrincipalError

os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-northeast-2')
for _k in ('USAGE_TABLE', 'CONFIG_TABLE', 'AUDIT_TABLE'):
    os.environ.setdefault(_k, 'dummy')
os.environ.setdefault('SNS_TOPIC_ARN', 'arn:aws:sns:ap-northeast-2:1:dummy')
for _k in ('ATHENA_DATABASE', 'ATHENA_TABLE', 'ATHENA_WORKGROUP'):
    os.environ.setdefault(_k, 'dummy')
os.environ.setdefault('ACCOUNT_ID', '803936485311')
os.environ.setdefault('ADMIN_USERNAMES', 'choichul')
os.environ.setdefault('ADMIN_ROLE_NAMES', 'BedrockMeterConsoleBffRole')


def _load(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, LAMBDA_DIR / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = _load('api_idx', 'api-handler/index.py')

ROLE_ARN = 'arn:aws:sts::803936485311:assumed-role/BedrockMeterConsoleBffRole/abc123session'
OTHER_ROLE_ARN = 'arn:aws:sts::803936485311:assumed-role/SomeOtherRole/sess'
ADMIN_USER_ARN = 'arn:aws:iam::803936485311:user/choichul'
PLAIN_USER_ARN = 'arn:aws:iam::803936485311:user/alice'


class TestAssumedRoleNameParser(unittest.TestCase):
    def test_extracts_role_name_ignoring_session(self):
        self.assertEqual(assumed_role_name(ROLE_ARN), 'BedrockMeterConsoleBffRole')

    def test_session_suffix_variants_same_role(self):
        a = assumed_role_name('arn:aws:sts::803936485311:assumed-role/R/session-one')
        b = assumed_role_name('arn:aws:sts::803936485311:assumed-role/R/i-0deadbeef')
        self.assertEqual(a, b)
        self.assertEqual(a, 'R')

    def test_non_role_inputs_return_none(self):
        self.assertIsNone(assumed_role_name(ADMIN_USER_ARN))
        self.assertIsNone(assumed_role_name(None))
        self.assertIsNone(assumed_role_name('garbage'))


class TestIsAdmin(unittest.TestCase):
    def test_allowed_role_is_admin(self):
        self.assertTrue(api.is_admin(ROLE_ARN))

    def test_non_allowed_role_is_not_admin(self):
        self.assertFalse(api.is_admin(OTHER_ROLE_ARN))

    def test_admin_iam_user_still_admin(self):
        self.assertTrue(api.is_admin(ADMIN_USER_ARN))

    def test_plain_iam_user_not_admin(self):
        self.assertFalse(api.is_admin(PLAIN_USER_ARN))

    def test_none_not_admin(self):
        self.assertFalse(api.is_admin(None))


class TestTargetPathUnchanged(unittest.TestCase):
    """Enforcement TARGET resolution must still reject assumed-role ARNs."""

    def test_validate_arn_rejects_role(self):
        with self.assertRaises(UnsupportedPrincipalError):
            validate_arn(ROLE_ARN)

    def test_caller_username_none_for_role(self):
        # The audit field helper must yield None (not raise) for a role caller.
        self.assertIsNone(api.caller_username(ROLE_ARN))
        self.assertEqual(api._caller_audit_fields(ROLE_ARN)['caller_username'], None)
        self.assertEqual(api._caller_audit_fields(ROLE_ARN)['caller_arn'], ROLE_ARN)


class TestOperatorIdentity(unittest.TestCase):
    """Operator headers are trusted ONLY from the admin BFF role — never spoofable by others."""

    def _event(self, **headers):
        return {'headers': {k.lower(): v for k, v in headers.items()}}

    OP_HEADERS = {
        'X-Bedrock-Meter-Operator-Email': 'human@example.com',
        'X-Bedrock-Meter-Operator-Username': 'human',
    }

    def test_trusted_from_admin_bff_role(self):
        op = api.operator_identity(self._event(**self.OP_HEADERS), ROLE_ARN)
        self.assertEqual(op, {'operator_email': 'human@example.com', 'operator_username': 'human'})

    def test_ignored_from_non_admin_role(self):
        # A different (non-allowlisted) role must NOT be able to set the audit actor.
        self.assertEqual(api.operator_identity(self._event(**self.OP_HEADERS), OTHER_ROLE_ARN), {})

    def test_ignored_from_iam_user_caller(self):
        # Direct IAM-user admin: caller ARN already IS the operator → no header trust.
        self.assertEqual(api.operator_identity(self._event(**self.OP_HEADERS), ADMIN_USER_ARN), {})

    def test_ignored_when_no_caller(self):
        self.assertEqual(api.operator_identity(self._event(**self.OP_HEADERS), None), {})

    def test_audit_actor_prefers_operator(self):
        # _caller_audit_fields surfaces operator fields so the audit-writer picks them as actor.
        op = api.operator_identity(self._event(**self.OP_HEADERS), ROLE_ARN)
        fields = api._caller_audit_fields(ROLE_ARN, op)
        self.assertEqual(fields['operator_email'], 'human@example.com')
        self.assertEqual(fields['caller_arn'], ROLE_ARN)  # proxy still recorded


class TestReevalCarriesOperator(unittest.TestCase):
    """A console limit raise that auto-unblocks must attribute the cascading RESOLVED event to
    the real operator, not the proxying BFF role (the LIMIT_CHANGED event already does)."""

    def setUp(self):
        self._orig = {
            'get_state': api.get_state,
            'get_effective_config': api.get_effective_config,
            'scope': api._scope_credit_totals,
            'reeval': api.reevaluate_and_unblock,
        }
        api.get_state = lambda arn: {'blocked': True}            # target is blocked
        self.cfg_reads = 0
        def counting_get_cfg(arn):
            self.cfg_reads += 1
            return {}
        api.get_effective_config = counting_get_cfg             # not exempt; counts re-reads
        self.captured = {}
        def fake_scope(arn, cfg):
            self.captured['cfg'] = cfg                           # capture the cfg used for re-eval
            return {}
        api._scope_credit_totals = fake_scope                    # all scopes under limit → unblock
        def fake_reeval(*a, **kw):
            self.captured['extra'] = kw.get('extra')
            return {'unblocked': True}
        api.reevaluate_and_unblock = fake_reeval

    def tearDown(self):
        api.get_state = self._orig['get_state']
        api.get_effective_config = self._orig['get_effective_config']
        api._scope_credit_totals = self._orig['scope']
        api.reevaluate_and_unblock = self._orig['reeval']

    def test_resolved_event_carries_operator(self):
        operator = {'operator_email': 'human@example.com', 'operator_username': 'human'}
        api._reeval_if_blocked('alice', PLAIN_USER_ARN, ROLE_ARN, operator)
        extra = self.captured['extra']
        self.assertEqual(extra['operator_email'], 'human@example.com')  # not the BFF role
        self.assertEqual(extra['caller_arn'], ROLE_ARN)                 # proxy still recorded

    def test_resolved_event_without_operator_falls_back_to_caller(self):
        # Direct IAM-user admin (no operator) → caller fields stand, as before.
        api._reeval_if_blocked('alice', PLAIN_USER_ARN, ADMIN_USER_ARN, None)
        extra = self.captured['extra']
        self.assertNotIn('operator_email', extra)
        self.assertEqual(extra['caller_username'], 'choichul')

    def test_passed_cfg_skips_stale_reread(self):
        # P2: when the caller passes the post-write cfg, _reeval_if_blocked must NOT re-read
        # (an eventually-consistent get_effective_config could return the pre-write limits) and
        # must evaluate against the passed cfg.
        fresh = {'dailyTokenLimit': 999, 'weeklyTokenLimit': 999, 'monthlyTokenLimit': 999}
        api._reeval_if_blocked('alice', PLAIN_USER_ARN, ROLE_ARN, None, cfg=fresh)
        self.assertEqual(self.cfg_reads, 0)                 # no stale re-read
        self.assertEqual(self.captured['cfg'], fresh)       # evaluated against the new limits

    def test_no_cfg_reads_config(self):
        # The daily/manual paths pass no cfg → it reads (backward-compatible).
        api._reeval_if_blocked('alice', PLAIN_USER_ARN, ROLE_ARN, None)
        self.assertEqual(self.cfg_reads, 1)


if __name__ == '__main__':
    unittest.main()
