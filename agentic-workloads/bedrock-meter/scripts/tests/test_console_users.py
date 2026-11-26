"""Exercise console tier changes with a fake AWS CLI; no AWS account is contacted.

Run from the repository root:
    python3 -m unittest discover -s scripts/tests -p 'test_*.py'
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
FAKE_AWS = r"""
import configparser
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("aws-cli/2.42.0")
    sys.exit(0)
with open(os.environ["FAKE_AWS_CALLS"], "a") as log:
    log.write(json.dumps(args) + "\n")
scenario = json.loads(os.environ["FAKE_AWS_SCENARIO"])
operation = args[1]
config = configparser.ConfigParser()
config.read(os.environ["AWS_CONFIG_FILE"])
error_format = os.environ.get("AWS_CLI_ERROR_FORMAT") or config.get(
    "default", "cli_error_format", fallback="legacy",
)
expected_format = scenario.get("expected_error_formats", {}).get(operation)
if expected_format is not None and error_format != expected_format:
    print(f"Unexpected error format for {operation}: {error_format}", file=sys.stderr)
    sys.exit(2)

def fail(code):
    if error_format == "json":
        message = json.dumps({"Code": code, "Message": "Simulated AWS error"})
    elif error_format == "yaml":
        message = f"Code: {code}\nMessage: Simulated AWS error"
    else:
        message = f"An error occurred ({code}) when calling {operation}"
    print(message, file=sys.stderr)
    sys.exit(1)

failure = scenario.get("failures", {}).get(operation)
if failure:
    fail(failure)

state_file = Path(os.environ["FAKE_AWS_STATE"])
groups = json.loads(state_file.read_text())
if operation == "get-caller-identity":
    print("123456789012")
elif operation == "get":
    print(scenario.get("profile_region", "ap-northeast-2"))
elif operation == "describe-stacks":
    print(scenario.get("pool_id", "ap-northeast-2_test"))
elif operation == "describe-user-pool":
    pool = args[args.index("--user-pool-id") + 1]
    region = args[args.index("--region") + 1]
    print(scenario.get("pool_arn", f"arn:aws:cognito-idp:{region}:123456789012:userpool/{pool}"))
elif operation == "admin-create-user":
    if scenario.get("existing", False):
        fail("UsernameExistsException")
elif operation == "admin-set-user-password":
    pass
elif operation == "admin-remove-user-from-group":
    group = args[args.index("--group-name") + 1]
    if not scenario.get("keep_old_group", False):
        groups = [name for name in groups if name != group]
elif operation == "admin-add-user-to-group":
    group = args[args.index("--group-name") + 1]
    if not scenario.get("skip_target_group", False):
        groups.append(group)
elif operation == "admin-list-groups-for-user":
    # Emulate the CLI's JSON projection after it has collected all API pages.
    print(scenario.get("group_output", json.dumps(groups)))
else:
    print(f"Unexpected AWS operation: {operation}", file=sys.stderr)
    sys.exit(2)
state_file.write_text(json.dumps(groups))
"""


@unittest.skipUnless(shutil.which("jq"), "jq is required by the console scripts")
class ConsoleUserTierTests(unittest.TestCase):
    def run_script(self, scenario=None, *, viewer=True, groups=None, env_overrides=None,
                   profile_error_format="legacy"):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            aws = directory / "aws"
            aws.write_text(f"#!{sys.executable}\n{FAKE_AWS}")
            aws.chmod(0o755)
            calls = directory / "calls.jsonl"
            state = directory / "groups.json"
            state.write_text(json.dumps(groups if groups is not None else ["console-admins"]))
            config = directory / "config"
            config.write_text(f"[default]\ncli_error_format = {profile_error_format}\n")
            settings = directory / "project.env"
            settings.write_text("# No AWS configuration in project settings.\n")
            env = {
                **{k: v for k, v in os.environ.items()
                   if not k.startswith(("AWS_", "CDK_", "BEDROCK_METER_"))},
                "PATH": f"{directory}{os.pathsep}{os.environ.get('PATH', '')}",
                "POOL_ID": "ap-northeast-2_test",
                "AWS_REGION": "ap-northeast-2",
                "BEDROCK_METER_DEPLOY_ENV_FILE": str(settings),
                "CONSOLE_ADMIN_GROUP": "console-admins",
                "CONSOLE_VIEWER_GROUP": "console-viewers",
                "AWS_CONFIG_FILE": str(config),
                "AWS_CLI_ERROR_FORMAT": "",
                "FAKE_AWS_CALLS": str(calls),
                "FAKE_AWS_STATE": str(state),
                "FAKE_AWS_SCENARIO": json.dumps(scenario or {}),
                **(env_overrides or {}),
            }
            wrapper = SCRIPTS / ("add-console-viewer.sh" if viewer else "add-console-admin.sh")
            result = subprocess.run(
                ["bash", str(wrapper), "test-user", "test@example.com", "TestOnly123!"],
                env=env, capture_output=True, text=True, timeout=10,
            )
            recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            return result, recorded, json.loads(state.read_text())

    def assert_failed(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Done.", result.stdout)

    def test_create_and_move_in_both_directions(self):
        for existing in (False, True):
            for viewer in (False, True):
                with self.subTest(existing=existing, viewer=viewer):
                    old = "console-admins" if viewer else "console-viewers"
                    target = "console-viewers" if viewer else "console-admins"
                    result, calls, groups = self.run_script(
                        {"existing": existing}, viewer=viewer,
                        groups=[old, "unrelated-group"],
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Done. Verified", result.stdout)
                    self.assertIn(target, groups)
                    self.assertNotIn(old, groups)
                    self.assertIn("unrelated-group", groups)
                    self.assertEqual(calls[-1][1], "admin-list-groups-for-user")

    def test_repeated_tier_assignment_succeeds(self):
        result, _, groups = self.run_script(
            {"existing": True}, groups=["console-viewers"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("console-admins", groups)

    def test_structured_error_settings_support_existing_user(self):
        for source in ("environment", "profile"):
            for error_format in ("json", "yaml"):
                with self.subTest(source=source, error_format=error_format):
                    formats = {
                        "admin-set-user-password": error_format,
                        "admin-remove-user-from-group": error_format,
                        "admin-add-user-to-group": error_format,
                        "admin-list-groups-for-user": error_format,
                    }
                    result, _, groups = self.run_script(
                        {"existing": True, "expected_error_formats": formats},
                        env_overrides={
                            "AWS_CLI_ERROR_FORMAT": error_format if source == "environment" else "",
                        },
                        profile_error_format=error_format,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Done. Verified", result.stdout)
                    self.assertEqual(groups, ["console-viewers"])

    def test_unexpected_errors_still_fail_with_structured_settings(self):
        for error_format in ("json", "yaml"):
            for operation in ("admin-create-user", "admin-remove-user-from-group"):
                for error in ("AccessDeniedException", "TooManyRequestsException", "TimeoutError"):
                    with self.subTest(error_format=error_format, operation=operation, error=error):
                        result, calls, groups = self.run_script(
                            {"existing": True, "failures": {operation: error}},
                            env_overrides={"AWS_CLI_ERROR_FORMAT": error_format},
                        )
                        self.assert_failed(result)
                        self.assertIn(error, result.stderr)
                        self.assertEqual(calls[-1][1], operation)
                        self.assertEqual(groups, ["console-admins"])

    def test_removal_failures_stop_before_addition(self):
        for error in ("AccessDeniedException", "TooManyRequestsException", "TimeoutError"):
            with self.subTest(error=error):
                result, calls, groups = self.run_script({
                    "existing": True,
                    "failures": {"admin-remove-user-from-group": error},
                })
                self.assert_failed(result)
                self.assertIn(error, result.stderr)
                self.assertEqual(calls[-1][1], "admin-remove-user-from-group")
                self.assertEqual(groups, ["console-admins"])

    def test_unexpected_create_failure_stops_before_password_or_groups(self):
        for error in ("AccessDeniedException", "TooManyRequestsException", "TimeoutError"):
            with self.subTest(error=error):
                result, calls, _ = self.run_script({
                    "failures": {"admin-create-user": error},
                })
                self.assert_failed(result)
                self.assertNotIn("user already exists", result.stdout)
                self.assertEqual([call[1] for call in calls if call[1].startswith("admin-")], ["admin-create-user"])

    def test_password_failure_stops_before_group_changes(self):
        result, calls, groups = self.run_script({
            "existing": True,
            "failures": {"admin-set-user-password": "AccessDeniedException"},
        })
        self.assert_failed(result)
        self.assertEqual(calls[-1][1], "admin-set-user-password")
        self.assertEqual(groups, ["console-admins"])

    def test_failed_addition_does_not_restore_admin(self):
        result, calls, groups = self.run_script({
            "existing": True,
            "failures": {"admin-add-user-to-group": "AccessDeniedException"},
        })
        self.assert_failed(result)
        self.assertEqual(calls[-1][1], "admin-add-user-to-group")
        self.assertEqual(groups, [])
        self.assertIn("partially applied", result.stderr)

    def test_group_query_failures_never_report_success(self):
        for error in ("AccessDeniedException", "TooManyRequestsException", "TimeoutError"):
            with self.subTest(error=error):
                result, calls, _ = self.run_script({
                    "failures": {"admin-list-groups-for-user": error},
                })
                self.assert_failed(result)
                self.assertEqual(calls[-1][1], "admin-list-groups-for-user")

    def test_admin_remaining_alongside_viewer_is_failure(self):
        result, _, groups = self.run_script({"keep_old_group": True})
        self.assert_failed(result)
        self.assertEqual(set(groups), {"console-admins", "console-viewers"})
        self.assertIn("tier change not confirmed", result.stderr)

    def test_missing_target_group_is_failure(self):
        result, _, _ = self.run_script({"skip_target_group": True})
        self.assert_failed(result)

    def test_invalid_or_incomplete_group_results_are_rejected(self):
        for output in (
            "not JSON", "null", "{}", '{"console-viewers":true}', "[]",
            '["console-viewers",42]', '["console-viewers-extra"]',
        ):
            with self.subTest(output=output):
                result, _, _ = self.run_script({"group_output": output})
                self.assert_failed(result)

    def test_verification_keeps_cli_pagination_and_checks_all_groups(self):
        many_groups = [f"other-{i}" for i in range(80)]
        for keep_old in (False, True):
            with self.subTest(keep_old=keep_old):
                result, calls, _ = self.run_script(
                    {"keep_old_group": keep_old}, groups=many_groups + ["console-admins"],
                )
                if keep_old:
                    self.assert_failed(result)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                query = calls[-1]
                self.assertEqual(query[query.index("--output") + 1], "json")
                self.assertEqual(query[query.index("--query") + 1], "Groups[].GroupName")
                self.assertNotIn("--no-paginate", query)
                self.assertNotIn("--max-items", query)

    def test_custom_group_names_are_matched_exactly(self):
        result, _, groups = self.run_script(
            groups=["custom-admins", "custom-viewers-extra"],
            env_overrides={
                "CONSOLE_ADMIN_GROUP": "custom-admins",
                "CONSOLE_VIEWER_GROUP": "custom-viewers",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(groups), {"custom-viewers", "custom-viewers-extra"})

    def test_non_seoul_region_and_legacy_inputs(self):
        result, calls, _ = self.run_script(env_overrides={
            "AWS_REGION": "us-west-2", "POOL_ID": "us-west-2_test",
            "AWS_DEFAULT_REGION": "eu-west-1", "REGION": "ap-northeast-2",
            "CDK_DEFAULT_REGION": "ap-northeast-2", "CDK_DEFAULT_ACCOUNT": "999999999999",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        for call in calls:
            if "--region" in call:
                self.assertEqual(call[call.index("--region") + 1], "us-west-2")

    def test_profile_region_without_environment_region(self):
        result, calls, _ = self.run_script(
            {"profile_region": "us-west-2"},
            env_overrides={"AWS_REGION": "", "AWS_DEFAULT_REGION": "",
                           "POOL_ID": "us-west-2_test", "AWS_PROFILE": "test"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[0], ["configure", "get", "region"])

    def test_pool_target_mismatch_stops_before_user_mutation(self):
        cases = [
            ({}, {"POOL_ID": "us-west-2_test"}),
            ({"pool_arn": "arn:aws:cognito-idp:ap-northeast-2:999999999999:userpool/ap-northeast-2_test"}, {}),
        ]
        for scenario, overrides in cases:
            result, calls, _ = self.run_script(scenario, env_overrides=overrides)
            self.assert_failed(result)
            self.assertFalse(any(call[1].startswith("admin-") for call in calls))

    def test_lookup_failure_does_not_fall_back_to_local_outputs(self):
        result, calls, _ = self.run_script(
            {"failures": {"describe-stacks": "AccessDeniedException"}},
            env_overrides={"POOL_ID": ""},
        )
        self.assert_failed(result)
        self.assertEqual(calls[-1][1], "describe-stacks")
        self.assertFalse(any(call[1].startswith("admin-") for call in calls))

    def test_live_stack_pool_resolution(self):
        result, calls, _ = self.run_script(env_overrides={"POOL_ID": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("describe-stacks", [call[1] for call in calls])
        self.assertIn("describe-user-pool", [call[1] for call in calls])

    def test_same_admin_and_viewer_group_is_rejected_before_aws_calls(self):
        result, calls, _ = self.run_script(env_overrides={
            "CONSOLE_ADMIN_GROUP": "same-group",
            "CONSOLE_VIEWER_GROUP": "same-group",
        })
        self.assert_failed(result)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
