"""First-installation orchestration with fake AWS/npm/CDK; no live AWS calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
FAKE_TOOL = r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
scenario = json.loads(os.environ["SCENARIO"])
with open(os.environ["CALLS"], "a") as f:
    f.write(json.dumps({"tool": name, "args": args, "cwd": os.getcwd(),
                       "region": os.environ.get("AWS_REGION"),
                       "account": os.environ.get("CDK_DEFAULT_ACCOUNT"),
                       "profile": os.environ.get("AWS_PROFILE")}) + "\n")
root = pathlib.Path(os.environ["TEST_ROOT"])
if name == "node":
    if "process.versions.node" in args[-1]:
        sys.exit(1 if scenario.get("old_node") else 0)
    sys.exit(0)
if name == "aws":
    if args == ["--version"]:
        print("aws-cli/2.42.0")
    elif args[:3] == ["configure", "get", "region"]:
        if scenario.get("profile_error"):
            print("The config profile could not be found", file=sys.stderr)
            sys.exit(1)
        print(scenario.get("profile_region", ""))
    elif args[:2] == ["sts", "get-caller-identity"]:
        if scenario.get("sts_error"):
            print("ExpiredToken", file=sys.stderr)
            sys.exit(1)
        print("123456789012")
    elif args[:2] == ["cloudformation", "describe-stacks"]:
        if scenario.get("stack_error"):
            print(scenario["stack_error"], file=sys.stderr)
            sys.exit(1)
        if scenario.get("stack_state"):
            print(scenario["stack_state"])
        else:
            print("An error occurred (ValidationError) when calling the DescribeStacks operation: "
                  "Stack with id BedrockMeterCoreStack does not exist", file=sys.stderr)
            sys.exit(1)
    else:
        raise RuntimeError(args)
elif name == "npm":
    if scenario.get("npm_fail") == args[0]:
        sys.exit(7)
    if args[0] == "run":
        artifact = root / "client/app/bff-lambda/dist"
        (artifact / "public").mkdir(parents=True)
        (artifact / "index.cjs").write_text("built")
        (artifact / "public/index.html").write_text("built")
elif name == "cdk":
    assert (root / "client/app/bff-lambda/dist/public/index.html").exists(), "build before CDK"
    assert os.environ["CDK_DEFAULT_ACCOUNT"] == "123456789012"
    assert os.environ["CDK_DEFAULT_REGION"] == os.environ["AWS_REGION"]
    if scenario.get("cdk_fail") == args[0]:
        print("Simulated CDK failure", file=sys.stderr)
        sys.exit(8)
    if args[0] == "bootstrap":
        assert args[1] == "aws://123456789012/" + os.environ["AWS_REGION"]
    else:
        assert args[0] == "deploy"
        data = {"BedrockMeterCoreStack": {
            "ConsoleUrl": "https://example.invalid",
            "ConsoleUserPoolId": os.environ["AWS_REGION"] + "_test",
        }}
        if scenario.get("bad_output"):
            data = {}
        pathlib.Path(args[args.index("--outputs-file") + 1]).write_text(json.dumps(data))
else:
    raise RuntimeError(name)
'''


@unittest.skipUnless(shutil.which("jq"), "jq is required")
class InstallationTests(unittest.TestCase):
    def run_deploy(self, scenario=None, *, settings="", overrides=None, args=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "client/app").mkdir(parents=True)
            (root / "cdk/node_modules/.bin").mkdir(parents=True)
            for name in ["deploy-all.sh", "_aws-context.sh"]:
                shutil.copy2(SCRIPTS / name, root / "scripts" / name)
            (root / ".env.deploy.local").write_text(
                "BEDROCK_METER_QUOTA_TIME_ZONE=UTC\n" + settings,
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name in ["aws", "npm", "node", "cdk"]:
                path = bin_dir / name if name != "cdk" else root / "cdk/node_modules/.bin/cdk"
                path.write_text(f"#!{sys.executable}\n{FAKE_TOOL}")
                path.chmod(0o755)
            calls_file = root / "calls"
            env = {
                **{k: v for k, v in os.environ.items()
                   if not k.startswith(("AWS_", "CDK_", "BEDROCK_METER_", "CONSOLE_"))
                   and k not in ("REGION", "POOL_ID")},
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "AWS_REGION": "us-west-2",
                "AWS_CONFIG_FILE": str(root / "aws-config"),
                "AWS_SHARED_CREDENTIALS_FILE": str(root / "credentials"),
                "AWS_EC2_METADATA_DISABLED": "true",
                "SCENARIO": json.dumps(scenario or {}),
                "CALLS": str(calls_file), "TEST_ROOT": str(root),
                **(overrides or {}),
            }
            result = subprocess.run(
                ["bash", str(root / "scripts/deploy-all.sh"),
                 *(args if args is not None else ["--bootstrap"])],
                env=env, capture_output=True, text=True, timeout=15,
            )
            calls = [json.loads(s) for s in calls_file.read_text().splitlines()] if calls_file.exists() else []
            return result, calls

    def test_first_install_builds_before_bootstrap_and_deploy(self):
        result, calls = self.run_deploy(overrides={"AWS_PROFILE": "test-profile"})
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = [(c["tool"], c["args"][0]) for c in calls]
        self.assertEqual(actions.count(("npm", "ci")), 2)
        self.assertEqual(actions.count(("npm", "run")), 1)
        self.assertLess(actions.index(("npm", "run")), actions.index(("cdk", "bootstrap")))
        self.assertLess(actions.index(("cdk", "bootstrap")), actions.index(("cdk", "deploy")))
        self.assertIn("Infrastructure deployment complete", result.stdout)
        for call in calls:
            if call["tool"] == "cdk":
                self.assertEqual(call["region"], "us-west-2")
                self.assertEqual(call["account"], "123456789012")
                self.assertEqual(call["profile"], "test-profile")
        self.assertFalse(any("dynamodb" in c["args"] for c in calls))

    def test_redeploy_does_not_bootstrap(self):
        result, calls = self.run_deploy({"stack_state": "UPDATE_COMPLETE"}, args=[])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["args"][0] for c in calls if c["tool"] == "cdk"], ["deploy"])

    def test_environment_and_profile_region_precedence(self):
        cases = [
            ({"AWS_REGION": "eu-west-1", "AWS_DEFAULT_REGION": "us-east-1"}, "eu-west-1"),
            ({"AWS_REGION": "", "AWS_DEFAULT_REGION": "us-east-1"}, "us-east-1"),
            ({"AWS_REGION": "", "AWS_DEFAULT_REGION": "", "AWS_PROFILE": "test"}, "ap-northeast-2"),
        ]
        for overrides, expected in cases:
            with self.subTest(expected=expected):
                result, calls = self.run_deploy({"profile_region": "ap-northeast-2"}, overrides=overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual({c["region"] for c in calls if c["tool"] == "cdk"}, {expected})

    def test_legacy_settings_never_select_target(self):
        result, calls = self.run_deploy(
            settings="CDK_DEFAULT_ACCOUNT=999999999999\nCDK_DEFAULT_REGION=eu-west-1\nREGION=eu-west-1\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ignored", result.stderr)
        self.assertEqual({c["region"] for c in calls if c["tool"] == "cdk"}, {"us-west-2"})

    def test_project_file_cannot_change_aws_context(self):
        for setting in ["AWS_REGION=eu-west-1", "AWS_PROFILE=other", "AWS_ACCESS_KEY_ID=not-a-secret"]:
            with self.subTest(setting=setting):
                result, calls = self.run_deploy(settings=setting + "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("changes AWS_*", result.stderr)
                self.assertFalse(any(c["tool"] in ("aws", "cdk", "npm") for c in calls))

    def test_preflight_failures_stop_before_install_or_deploy(self):
        cases = [
            ({"profile_error": True}, {"AWS_REGION": "", "AWS_DEFAULT_REGION": ""}),
            ({}, {"AWS_REGION": "", "AWS_DEFAULT_REGION": ""}),
            ({"sts_error": True}, {}),
            ({"old_node": True}, {}),
            ({"stack_state": "ROLLBACK_COMPLETE"}, {}),
            ({"stack_state": "UPDATE_IN_PROGRESS"}, {}),
            ({"stack_error": "An error occurred (AccessDenied) when calling DescribeStacks"}, {}),
        ]
        for scenario, overrides in cases:
            with self.subTest(scenario=scenario):
                result, calls = self.run_deploy(scenario, overrides=overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("deployment complete", result.stdout)
                self.assertFalse(any(c["tool"] in ("npm", "cdk") for c in calls))

    def test_install_build_bootstrap_and_deploy_failures_are_not_success(self):
        for scenario in [{"npm_fail": "ci"}, {"npm_fail": "run"},
                         {"cdk_fail": "bootstrap"}, {"cdk_fail": "deploy"},
                         {"bad_output": True}]:
            with self.subTest(scenario=scenario):
                result, calls = self.run_deploy(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("deployment stopped during", result.stderr)
                self.assertNotIn("deployment complete", result.stdout)
                if scenario.get("npm_fail"):
                    self.assertFalse(any(c["tool"] == "cdk" for c in calls))
                if scenario.get("cdk_fail") == "bootstrap":
                    self.assertFalse(any(c["tool"] == "cdk" and c["args"][0] == "deploy" for c in calls))

    def test_unknown_option_is_rejected_without_tool_calls(self):
        result, calls = self.run_deploy(args=["--region", "us-west-2"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])


class BackfillTargetTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("backfill", SCRIPTS / "backfill-retention.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_env_then_profile_region(self):
        with patch.dict(os.environ, {"AWS_REGION": "us-west-2", "AWS_DEFAULT_REGION": "eu-west-1"}, clear=True):
            self.assertEqual(self.module.resolve_region(), "us-west-2")
        with patch.dict(os.environ, {}, clear=True), patch.object(self.module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "eu-west-1\n", "")
            self.assertEqual(self.module.resolve_region(), "eu-west-1")
            run.return_value = subprocess.CompletedProcess([], 1, "", "missing")
            with self.assertRaises(SystemExit):
                self.module.resolve_region()

    def test_all_calls_pin_resolved_region(self):
        self.module.AWS_TARGET_REGION = "us-west-2"
        with patch.object(self.module.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, '{"Items":[]}', "")
            self.module.aws_json("dynamodb", "scan", "--table-name", "usage")
            self.assertEqual(run.call_args.args[0][-4:], ["--region", "us-west-2", "--output", "json"])
            run.return_value = subprocess.CompletedProcess([], 1, "", "AccessDenied: no scan permission")
            with self.assertRaisesRegex(SystemExit, "AccessDenied"):
                self.module.aws_json("dynamodb", "scan", "--table-name", "usage")


if __name__ == "__main__":
    unittest.main()
