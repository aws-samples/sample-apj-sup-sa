"""Exercise bootstrap decisions with a stateful fake AWS CLI, never real AWS."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bootstrap(tmp_path):
    state = tmp_path / "state.json"
    calls = tmp_path / "calls.jsonl"
    aws = tmp_path / "aws"
    aws.write_text(
        f"#!{sys.executable}\n" + r'''
import json
import os
import sys
from pathlib import Path

path = Path(os.environ["FAKE_STATE"])
s = json.loads(path.read_text())
args = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps(args) + "\n")
query = args[args.index("--query") + 1] if "--query" in args else ""
op = args[:2]
region = args[args.index("--region") + 1] if "--region" in args else "ap-northeast-2"
bucket = s.get("bucket", "sagemaker-xgboost-lab-123456789012")
role = "arn:aws:iam::123456789012:role/mlflow"
arn = "arn:aws:sagemaker:ap-northeast-2:123456789012:mlflow-app/test"
if op == ["sts", "get-caller-identity"]:
    print("123456789012")
elif op == ["cloudformation", "describe-stacks"]:
    if not s.get("stack", True):
        sys.stderr.write("ValidationError: Stack does not exist\n")
        sys.exit(254)
    if "StackStatus" in query:
        print("UPDATE_COMPLETE")
    elif "CreateArtifactBucket" in query:
        print(s.get("create_bucket", "true"))
    elif "ArtifactBucketName" in query:
        print(bucket)
    elif "SageMakerExecutionRoleArn" in query:
        print("arn:aws:iam::123456789012:role/training")
    elif "MlflowAppRoleArn" in query:
        print(role)
    else:
        raise AssertionError(args)
elif op == ["s3api", "get-bucket-location"]:
    assert args[args.index("--expected-bucket-owner") + 1] == "123456789012"
    if s.get("bucket_error"):
        sys.stderr.write(s["bucket_error"] + "\n")
        sys.exit(254)
    print(s.get("bucket_region", region))
elif op == ["cloudformation", "deploy"]:
    assert "--no-fail-on-empty-changeset" in args
    s["bucket"] = next(a.split("=", 1)[1] for a in args if a.startswith("BucketName="))
    s["stack"] = True
elif op == ["sagemaker", "list-mlflow-apps"]:
    print(arn if s.get("app", True) else "None")
elif op == ["sagemaker", "describe-mlflow-app"]:
    print({
        "Status": s.get("app_status", "Updated"),
        "RoleArn": s.get("app_role", role),
        "ArtifactStoreUri": s.get("app_uri", f"s3://{bucket}/sagemaker-xgboost-lab/mlflow/"),
        "ModelRegistrationMode": "AutoModelRegistrationEnabled",
        "MlflowVersion": s.get("version", "3.16.0"),
    }[query])
elif op == ["sagemaker", "delete-mlflow-app"]:
    s["app"] = False
elif op == ["sagemaker", "create-mlflow-app"]:
    s["app"] = True
    s["app_status"] = "Created"
    s.pop("app_role", None)
    s.pop("app_uri", None)
    print(arn)
else:
    raise AssertionError(args)
path.write_text(json.dumps(s))
'''
    )
    aws.chmod(0o755)

    def run(settings=None, *options, region="ap-northeast-2"):
        state.write_text(json.dumps(settings or {}))
        calls.write_text("")
        result = subprocess.run(
            ["bash", "scripts/bootstrap-infra.sh", *options],
            cwd=PROJECT,
            env={
                **os.environ,
                "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
                "AWS_REGION": region,
                "STACK_NAME": "sagemaker-xgboost-lab",
                "MLFLOW_APP_NAME": "sagemaker-xgboost-lab",
                "FAKE_STATE": str(state),
                "FAKE_CALLS": str(calls),
            },
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result, [json.loads(line) for line in calls.read_text().splitlines()]

    return run


def operations(calls):
    return [tuple(call[:2]) for call in calls]


@pytest.mark.parametrize("version", ["3.10.1", "3.16.0", "3.17.0"])
def test_bootstrap_reuses_ready_app_and_legacy_bucket(bootstrap, version):
    result, calls = bootstrap({"version": version})
    assert result.returncode == 0, result.stderr
    assert f"MLflow App version: {version}" in result.stdout
    deploy = next(c for c in calls if c[:2] == ["cloudformation", "deploy"])
    assert "BucketName=sagemaker-xgboost-lab-123456789012" in deploy
    assert ("sagemaker", "delete-mlflow-app") not in operations(calls)


@pytest.mark.parametrize("region", ["ap-northeast-1", "ap-southeast-2"])
def test_new_stack_uses_regional_bucket(bootstrap, region):
    result, calls = bootstrap(
        {"stack": False, "app": False, "bucket_error": "NoSuchBucket"}, region=region
    )
    assert result.returncode == 0, result.stderr
    deploy = next(c for c in calls if c[:2] == ["cloudformation", "deploy"])
    assert f"BucketName=sagemaker-xgboost-lab-123456789012-{region}" in deploy
    assert "CreateArtifactBucket=true" in deploy


def test_reuses_existing_regional_bucket_without_ownership(bootstrap):
    result, calls = bootstrap({"stack": False, "app": False})
    assert result.returncode == 0, result.stderr
    deploy = next(c for c in calls if c[:2] == ["cloudformation", "deploy"])
    assert "CreateArtifactBucket=false" in deploy


@pytest.mark.parametrize(
    "settings",
    [
        {"bucket_region": "ap-southeast-2"},
        {"bucket_error": "AccessDenied"},
        {"bucket_error": "NoSuchBucket"},  # A missing stack-managed bucket is drift.
    ],
)
def test_bucket_errors_stop_before_deploy(bootstrap, settings):
    result, calls = bootstrap(settings)
    assert result.returncode != 0
    assert ("cloudformation", "deploy") not in operations(calls)
    assert ("sagemaker", "delete-mlflow-app") not in operations(calls)


@pytest.mark.parametrize("location,region", [("None", "us-east-1"), ("EU", "eu-west-1")])
def test_normalizes_legacy_location_values(bootstrap, location, region):
    result, _ = bootstrap({"bucket_region": location}, region=region)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "settings",
    [
        {"app_role": "arn:aws:iam::123456789012:role/another-team"},
        {"app_uri": "s3://another-bucket/mlflow/"},
        {"app_status": "CreateFailed"},
        {"app_status": "UpdateFailed"},
        {"app_status": "DeleteFailed"},
    ],
)
def test_app_replacement_requires_explicit_flag(bootstrap, settings):
    result, calls = bootstrap(settings)
    assert result.returncode != 0
    assert "--replace-mlflow-app" in result.stderr
    assert ("sagemaker", "delete-mlflow-app") not in operations(calls)
    assert ("sagemaker", "create-mlflow-app") not in operations(calls)

    result, calls = bootstrap(settings, "--replace-mlflow-app")
    assert result.returncode == 0, result.stderr
    assert operations(calls).count(("sagemaker", "delete-mlflow-app")) == 1
    assert operations(calls).count(("sagemaker", "create-mlflow-app")) == 1
