"""Exercise bootstrap against a ready managed App without calling AWS."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("version", ["3.10.1", "3.16.0", "3.17.0"])
def test_bootstrap_reuses_ready_app_after_version_update(tmp_path, version):
    aws = tmp_path / "aws"
    aws.write_text(
        """#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
query = args[args.index("--query") + 1] if "--query" in args else ""
operation = args[:2]
if operation == ["sts", "get-caller-identity"]:
    print("123456789012")
elif operation == ["cloudformation", "deploy"]:
    assert "--no-fail-on-empty-changeset" in args
elif operation == ["cloudformation", "describe-stacks"]:
    if "StackStatus" in query:
        print("UPDATE_COMPLETE")
    elif "CreateArtifactBucket" in query:
        print("true")
    elif "SageMakerExecutionRoleArn" in query:
        print("arn:aws:iam::123456789012:role/training")
    elif "MlflowAppRoleArn" in query:
        print("arn:aws:iam::123456789012:role/mlflow")
    else:
        raise AssertionError(args)
elif operation == ["sagemaker", "list-mlflow-apps"]:
    print("arn:aws:sagemaker:ap-northeast-2:123456789012:mlflow-app/test")
elif operation == ["sagemaker", "describe-mlflow-app"]:
    print({
        "Status": "Updated",
        "RoleArn": "arn:aws:iam::123456789012:role/mlflow",
        "ArtifactStoreUri": (
            "s3://sagemaker-xgboost-lab-123456789012/sagemaker-xgboost-lab/mlflow/"
        ),
        "ModelRegistrationMode": "AutoModelRegistrationEnabled",
        "MlflowVersion": os.environ["TEST_MLFLOW_VERSION"],
    }[query])
else:
    raise AssertionError(args)
"""
    )
    aws.chmod(0o755)
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", "scripts/bootstrap-infra.sh"],
        cwd=project,
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "TEST_MLFLOW_VERSION": version,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert f"MLflow App version: {version}" in result.stdout
    assert "Infrastructure ready." in result.stdout
