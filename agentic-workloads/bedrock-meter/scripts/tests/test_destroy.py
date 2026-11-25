"""Run the uninstall shell script against a fake AWS CLI; never change live AWS."""
import ast
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import quote

SCRIPTS = Path(__file__).resolve().parents[1]
ACCOUNT, REGION = "123456789012", "us-west-2"
STACK_ID = f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/BedrockMeterCoreStack/test-id"
DENY = {"Version": "2012-10-17", "Statement": [{
    "Sid": "AutoBlockBedrockQuotaExceeded",
    "Effect": "Deny",
    "Action": ["bedrock:Invoke*", "bedrock:Converse*", "bedrock:RetrieveAndGenerate*", "bedrock:InvokeAgent*"],
    "Resource": "*",
}]}

FAKE_AWS = r'''
import json, os, pathlib, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("aws-cli/2.42.0")
    sys.exit(0)
with open(os.environ["CALLS"], "a") as f:
    f.write(json.dumps(args) + "\n")
state_path = pathlib.Path(os.environ["STATE"])
s = json.loads(state_path.read_text())
service, op = args[:2]
def arg(key):
    return args[args.index(key)+1]
def error(code):
    print(f"An error occurred ({code}) when calling {op}", file=sys.stderr)
    sys.exit(1)
def output(value=None):
    state_path.write_text(json.dumps(s))
    if value is not None: print(json.dumps(value))
    sys.exit(0)
if s.get("fail") == service + "/" + op: error("AccessDenied")
if service == "sts":
    print("123456789012"); sys.exit(0)
if service == "configure":
    print("us-west-2"); sys.exit(0)
if service == "cloudformation":
    if op == "describe-stacks":
        if s.get("missing_stack"):
            print("(ValidationError) Stack with id BedrockMeterCoreStack does not exist", file=sys.stderr)
            sys.exit(1)
        output({"Stacks":[{"StackId":s["stack_id"],"StackStatus":s.get("status","CREATE_COMPLETE"),
                          "Outputs":[{"OutputKey":"UsageTableName","OutputValue":"Usage"}],
                          "Parameters":[{"ParameterKey":"BootstrapVersion","ParameterValue":"existing"}],
                          "Tags":[{"Key":"Team","Value":"Meter"}]}]})
    if op == "get-template": output({"TemplateBody": s["template"]})
    if op == "list-stack-resources": output({"StackResourceSummaries": s["inventory"]})
    if op == "update-stack":
        assert json.loads(pathlib.Path(arg("--parameters").removeprefix("file://")).read_text()) == [{"ParameterKey":"BootstrapVersion","UsePreviousValue":True}]
        assert json.loads(pathlib.Path(arg("--tags").removeprefix("file://")).read_text()) == [{"Key":"Team","Value":"Meter"}]
        if "--template-body" in args:
            new=json.loads(pathlib.Path(arg("--template-body").removeprefix("file://")).read_text())
        else:
            assert "/meter-results/" in arg("--template-url")
            new=s["staged_template"]
        assert new["Resources"].keys() == s["template"]["Resources"].keys()
        for key, old in s["template"]["Resources"].items():
            assert {k:v for k,v in old.items() if k not in ("DeletionPolicy","UpdateReplacePolicy")} == {k:v for k,v in new["Resources"][key].items() if k not in ("DeletionPolicy","UpdateReplacePolicy")}
        if s.get("pretend_no_update"):
            print("(ValidationError) No updates are to be performed",file=sys.stderr);sys.exit(1)
        s["template"]=new; s["retention_updated"]=True; output({"StackId":s["stack_id"]})
    if op == "delete-stack":
        assert arg("--stack-name") == s["stack_id"]
        for key in ["Archive", "Invocation", "LoggingRole", "LoggingRolePolicy", "ArchivePolicy", "LoggingConfig"]:
            assert s["template"]["Resources"][key]["DeletionPolicy"] == "Retain", key
        s["delete_started"] = True; output()
    if op == "wait":
        if args[2] == "stack-update-complete":
            assert s["retention_updated"];output()
        assert s["delete_started"]
        s["stack_deleted"] = True
        if s.get("replace_table"): s["ids"]["Usage"] = "replacement"
        output()
if service == "bedrock":
    if op == "get-model-invocation-logging-configuration": output({"loggingConfig":s["logging"]} if s["logging"] else {})
    if op == "delete-model-invocation-logging-configuration":
        raise AssertionError("Uninstall must not change Bedrock invocation logging")
if service == "dynamodb":
    name=arg("--table-name")
    if op == "describe-table":
        if name not in s["exists"]: error("ResourceNotFoundException")
        output({"Table":{"TableId":s["ids"][name]}})
    if op == "scan":
        assert s["stack_deleted"]
        assert "--no-paginate" not in args  # CLI collects all scan pages.
        output({"Items":[{"UserARN":{"S":arn}} for arn in s["principals"]]})
    if op == "delete-table":
        s["exists"].remove(name); output()
    if op == "wait":
        assert name not in s["exists"]; output()
if service == "iam":
    if op == "get-role":
        name=arg("--role-name")
        if name not in s["exists"]: error("NoSuchEntity")
        output({"Role":{"RoleId":s["ids"][name]}})
    if op in ("get-user-policy","get-role-policy","delete-user-policy","delete-role-policy"):
        kind = "user" if "user" in op else "role"
        name=arg("--"+kind+"-name")
        assert arg("--policy-name") == "AutoBlocked-BedrockMeter-QuotaExceeded"
        if name not in s["policies"]: error("NoSuchEntity")
        if op.startswith("get-"): output({"PolicyDocument":s["policies"][name]})
        assert s["stack_deleted"]
        del s["policies"][name]; output()
    if op == "delete-role":
        name=arg("--role-name")
        assert name == "MeterLoggingRole"  # never delete a metered principal
        s["exists"].remove(name); output()
if service == "logs":
    if op == "describe-log-groups":
        name=arg("--log-group-name-prefix")
        output({"logGroups":[{"logGroupName":name,"creationTime":123}] if name in s["exists"] else []})
    if op == "delete-log-group":
        s["exists"].remove(arg("--log-group-name")); output()
if service == "cognito-idp":
    name=arg("--user-pool-id")
    if op == "describe-user-pool":
        output({"UserPool":{"Arn":"arn:aws:cognito-idp:us-west-2:123456789012:userpool/"+name}})
    if op == "delete-user-pool": s["exists"].remove(name); output()
if service == "s3api":
    payload=None
    for flag in ("--delete","--cli-input-json"):
        if flag in args:
            payload=json.loads(pathlib.Path(arg(flag).removeprefix("file://")).read_text())
            s["payloads"].append(payload)
    bucket=payload["Bucket"] if op == "abort-multipart-upload" else arg("--bucket")
    owner=payload["ExpectedBucketOwner"] if op == "abort-multipart-upload" else arg("--expected-bucket-owner")
    assert owner == "123456789012"
    assert bucket in ("meter-archive", "meter-results")  # no access to outside buckets
    objects=s["objects"][bucket]
    if op == "put-object":
        assert bucket == "meter-results"
        assert arg("--if-none-match") == "*"
        s["staged_template"]=json.loads(pathlib.Path(arg("--body")).read_text())
        s["staged_key"]=arg("--key")
        output({} if s.get("no_stage_version") else {"VersionId":"template-v1"})
    if op == "delete-object":
        assert bucket == "meter-results" and arg("--key") == s["staged_key"]
        if not s.get("no_stage_version"): assert arg("--version-id") == "template-v1"
        s["stage_removed"]=True;output()
    if op == "get-bucket-tagging":
        output({"TagSet":[{"Key":"aws:cloudformation:stack-id",
                           "Value":"foreign" if s.get("foreign_bucket") else s["stack_id"]}]})
    if op == "list-multipart-uploads": output({"Uploads":objects["uploads"]})
    if op == "abort-multipart-upload":
        assert payload["Key"] == "upload\n"
        objects["uploads"]=[]; output()
    if op == "list-object-versions":
        output({"Versions":objects["versions"][:2],"DeleteMarkers":objects["markers"]})
    if op == "delete-objects":
        if s.get("s3_errors"): output({"Errors":[{"Key":"locked","Code":"AccessDenied"}]})
        for item in payload["Objects"]:
            for items in (objects["versions"],objects["markers"]):
                if item in items:items.remove(item)
        output({})
    if op == "delete-bucket":
        assert bucket == "meter-results"
        assert not objects["uploads"] and not objects["versions"] and not objects["markers"]
        s["exists"].remove(bucket); output()
raise AssertionError(args)
'''


def initial_state():
    resources, inventory, ids = {}, [], {}
    for logical, kind, name in [
        ("Usage", "AWS::DynamoDB::Table", "Usage"),
        ("Config", "AWS::DynamoDB::Table", "Config"),
        ("Audit", "AWS::DynamoDB::Table", "Audit"),
        ("Archive", "AWS::S3::Bucket", "meter-archive"),
        ("Results", "AWS::S3::Bucket", "meter-results"),
        ("Invocation", "AWS::Logs::LogGroup", "/aws/bedrock/modelinvocations"),
        ("LoggingRole", "AWS::IAM::Role", "MeterLoggingRole"),
        ("Pool", "AWS::Cognito::UserPool", "us-west-2_test"),
    ]:
        resources[logical] = {"Type": kind, "DeletionPolicy": "Retain"}
        inventory.append({"LogicalResourceId": logical, "ResourceType": kind, "PhysicalResourceId": name})
        ids[name] = f"id-{logical}"
    resources["LoggingRolePolicy"] = {"Type":"AWS::IAM::Policy", "Properties": {
        "Roles":[{"Ref":"LoggingRole"}], "PolicyName":"logging-policy"}}
    resources["ArchivePolicy"] = {"Type":"AWS::S3::BucketPolicy", "Properties":{"Bucket":{"Ref":"Archive"}}}
    resources["Worker"] = {"Type": "AWS::Lambda::Function", "Properties": {
        "Timeout": 2, "Environment": {"Variables": {"USAGE_TABLE": "Usage"}},
    }}
    expected = {
        "cloudWatchConfig": {"logGroupName": "/aws/bedrock/modelinvocations",
                            "roleArn": {"Fn::GetAtt": ["LoggingRole", "Arn"]}},
        "s3Config": {"bucketName": {"Ref": "Archive"}, "keyPrefix": "bedrock-logs/"},
        "textDataDeliveryEnabled": True, "imageDataDeliveryEnabled": False, "embeddingDataDeliveryEnabled": False,
    }
    resources["LoggingConfig"] = {"Type": "AWS::CloudFormation::CustomResource", "Properties": {"LoggingConfig": expected}}
    current = copy.deepcopy(expected)
    current["cloudWatchConfig"]["roleArn"] = f"arn:aws:iam::{ACCOUNT}:role/MeterLoggingRole"
    current["s3Config"]["bucketName"] = "meter-archive"
    current["audioDataDeliveryEnabled"] = False
    current["videoDataDeliveryEnabled"] = False
    return {
        "stack_id": STACK_ID, "template": {"Resources": resources}, "inventory": inventory,
        "ids": ids, "exists": list(ids), "logging": current,
        "policies": {"alice": copy.deepcopy(DENY), "developer": quote(json.dumps(DENY))},
        "principals": [f"arn:aws:iam::{ACCOUNT}:user/team/alice"] * 2
                      + [f"arn:aws:iam::{ACCOUNT}:role/team/developer", "arn:aws:iam::999999999999:user/foreign"],
        "objects": {bucket: {
            "uploads": [{"Key": "upload\n", "UploadId": "u1"}],
            "versions": [{"Key": "file\n", "VersionId": "null"}, {"Key": 'quoted"key', "VersionId": "v2"},
                         {"Key": 'quoted"key', "VersionId": "v1"}],
            "markers": [{"Key": 'quoted"key', "VersionId": "marker"}],
        } for bucket in ("meter-archive", "meter-results")}, "payloads": [],
    }


@unittest.skipUnless(shutil.which("jq"), "jq is required")
class DestroyTests(unittest.TestCase):
    def run_script(self, choice, modify=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = initial_state()
            if modify:
                modify(state)
            state_path = root / "state.json"
            state_path.write_text(json.dumps(state))
            cli = root / "aws"
            cli.write_text(f"#!{sys.executable}\n{FAKE_AWS}")
            cli.chmod(0o755)
            sleep = root / "sleep"
            sleep.write_text("#!/bin/sh\nexit 0\n")
            sleep.chmod(0o755)
            calls = root / "calls"
            env = {
                **{k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "CDK_"))},
                "PATH": f"{root}:{os.environ['PATH']}", "AWS_REGION": REGION,
                "AWS_PROFILE": "test-profile", "STATE": str(state_path), "CALLS": str(calls),
                "TMPDIR": str(root),
            }
            result = subprocess.run([os.environ.get("BASH_FOR_TESTS", "bash"), str(SCRIPTS / "destroy-all.sh")],
                                    input=choice + "\n", capture_output=True, text=True, env=env, timeout=30)
            requests = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            self.assertFalse(list(root.glob("bedrock-meter-uninstall.*")), "temporary files must be removed")
            return result, requests, json.loads(state_path.read_text())

    @staticmethod
    def writes(calls):
        return [a for a in calls if a[1].startswith(("delete-", "put-", "update-", "abort-"))]

    def test_one_choice_preserves_data_and_cleans_owned_quota_policies(self):
        result, calls, state = self.run_script("1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("Select [1/2, Enter to cancel]"), 1)
        self.assertEqual(set(state["exists"]), set(initial_state()["exists"]))
        self.assertEqual(state["policies"], {})
        self.assertEqual(state["logging"], initial_state()["logging"])
        self.assertFalse(any(a[0] in ("dynamodb", "s3api", "logs", "cognito-idp") for a in self.writes(calls)))

    def test_full_removal_includes_versions_markers_and_uploads(self):
        result, calls, state = self.run_script("2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(state["exists"]), {"meter-archive", "/aws/bedrock/modelinvocations", "MeterLoggingRole"})
        self.assertEqual(state["logging"], initial_state()["logging"])
        self.assertEqual(state["objects"]["meter-archive"], initial_state()["objects"]["meter-archive"])
        self.assertEqual(state["policies"], {})
        self.assertIn("Meter stack and all listed Meter data resources deleted.", result.stdout)
        self.assertFalse(any(a[1] == "delete-user" for a in calls))
        self.assertFalse(any("foreign" in str(a) for a in calls))
        objects = [o for payload in state["payloads"] for o in payload.get("Objects", [])]
        self.assertIn({"Key": "file\n", "VersionId": "null"}, objects)
        self.assertIn({"Key": 'quoted"key', "VersionId": "marker"}, objects)
        self.assertGreaterEqual(sum(a[1] == "list-object-versions" for a in calls), 3)

    def test_cancel_and_invalid_choice_are_read_only(self):
        for choice in ("", "yes", "3"):
            result, calls, _ = self.run_script(choice)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.writes(calls), [])
            self.assertIn("Cancelled", result.stdout)

    def test_partial_object_failure_reports_remaining_bucket(self):
        result, _, state = self.run_script("2", lambda s: s.update(s3_errors=True))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(set(state["exists"]), {"meter-results", "meter-archive", "/aws/bedrock/modelinvocations", "MeterLoggingRole"})
        self.assertIn("meter-results", result.stderr)
        self.assertNotIn("Meter stack and all listed Meter data resources deleted.", result.stdout)

    def test_foreign_bucket_is_not_modified(self):
        result, calls, _ = self.run_script("2", lambda s: s.update(foreign_bucket=True))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.writes(calls), [])

    def test_unmanaged_video_logging_does_not_block_uninstall(self):
        for choice in ("1", "2"):
            result, calls, state = self.run_script(
                choice, lambda s: s["logging"].update(videoDataDeliveryEnabled=True),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(state["logging"]["videoDataDeliveryEnabled"])
            self.assertIn("meter-archive", state["exists"])
            self.assertIn("/aws/bedrock/modelinvocations", state["exists"])
            self.assertIn("MeterLoggingRole", state["exists"])
            self.assertFalse(any(a[0] == "bedrock" for a in self.writes(calls)))

    def test_custom_logging_options_are_untouched(self):
        def change(s):
            s["logging"].update(audioDataDeliveryEnabled=True, unrecognizedSetting=True)
        result, calls, state = self.run_script("2", change)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(state["logging"]["unrecognizedSetting"])
        self.assertFalse(any(a[0] == "bedrock" for a in self.writes(calls)))

    def test_existing_policy_retention_skips_stack_update(self):
        def change(s):
            for key in ["Archive", "Invocation", "LoggingRole", "LoggingRolePolicy", "ArchivePolicy", "LoggingConfig"]:
                s["template"]["Resources"][key].update(DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
        result, calls, _ = self.run_script("2", change)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(a[1] in ("update-stack", "put-object") for a in calls))

    def test_large_template_staged_only_in_meter_bucket_and_removed(self):
        for no_version in (False, True):
            def change(s):
                s["template"]["Metadata"] = {"Padding": "x" * 52000}
                s["no_stage_version"] = no_version
            result, calls, state = self.run_script("1", change)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(state["stage_removed"])
            self.assertTrue(any(a[1] == "put-object" for a in calls))
            self.assertTrue(any(a[1] == "update-stack" and "--template-url" in a for a in calls))
            self.assertEqual(state["objects"], initial_state()["objects"])

    def test_failed_retention_update_never_starts_stack_deletion(self):
        for change in [lambda s: s.update(fail="cloudformation/update-stack"),
                       lambda s: s.update(pretend_no_update=True)]:
            result, calls, _ = self.run_script("2", change)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(a[1] in ("delete-stack", "delete-table", "delete-bucket") for a in calls))

    def test_current_logging_destinations_are_also_protected(self):
        def change(s):
            s["logging"]["s3Config"]["bucketName"] = "meter-results"
        result, calls, state = self.run_script("2", change)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("meter-results", state["exists"])
        self.assertIn("meter-archive", state["exists"])
        self.assertFalse(any(a[1] == "delete-bucket" for a in calls))

    def test_unrelated_logging_is_preserved(self):
        config = {"s3Config": {"bucketName": "unrelated"}, "textDataDeliveryEnabled": True}
        result, calls, state = self.run_script("2", lambda s: s.update(logging=config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["logging"], config)
        self.assertFalse(any(a[1] == "delete-model-invocation-logging-configuration" for a in calls))

    def test_changed_iam_policy_prevents_purging_data(self):
        def change(state):
            state["policies"]["alice"]["Statement"][0]["Action"] = ["s3:*"]
        result, calls, state = self.run_script("2", change)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage", state["exists"])
        self.assertIn("alice", state["policies"])
        self.assertFalse(any(a[1] == "delete-table" for a in calls))

    def test_stack_wait_failure_prevents_purging_data(self):
        result, calls, _ = self.run_script("2", lambda s: s.update(fail="cloudformation/wait"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(a[1] in ("scan", "delete-table", "delete-bucket") for a in calls))

    def test_replaced_usage_table_stops_cleanup(self):
        result, calls, _ = self.run_script("2", lambda s: s.update(replace_table=True))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(a[1] in ("scan", "delete-table", "delete-bucket") for a in calls))

    def test_missing_stack_is_not_a_name_based_resource_search(self):
        result, calls, _ = self.run_script("2", lambda s: s.update(missing_stack=True))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Select [", result.stdout)
        self.assertEqual(self.writes(calls), [])

    def test_access_denied_and_in_progress_are_not_treated_as_absence(self):
        for change in [lambda s: s.update(fail="cloudformation/describe-stacks"),
                       lambda s: s.update(status="UPDATE_IN_PROGRESS")]:
            result, calls, _ = self.run_script("2", change)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.writes(calls), [])

    def test_policy_document_matches_enforcement(self):
        source = ast.parse((SCRIPTS.parent / "lambda/common/enforcement.py").read_text())
        statement = next(n for n in source.body if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "DENY_POLICY_DOC" for t in n.targets))
        self.assertEqual(ast.literal_eval(statement.value.args[0]), DENY)
        shell = (SCRIPTS / "destroy-all.sh").read_text()
        document = shell.split("<<'POLICY'\n", 1)[1].split("\nPOLICY", 1)[0]
        self.assertEqual(json.loads(document), DENY)

    def test_help_needs_no_python_or_node_at_runtime(self):
        result = subprocess.run(["bash", str(SCRIPTS / "destroy-all.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Python", result.stdout)
        self.assertIn("AWS CLI v2 and jq", result.stdout)


if __name__ == "__main__":
    unittest.main()
