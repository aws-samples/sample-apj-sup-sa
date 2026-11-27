from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import boto3
from botocore.client import BaseClient

from sagemaker_xgboost_pipeline.config import PIPELINE_PREFIX, Resources


def _fake_aws_call(self, operation: str, parameters: dict) -> dict:
    if operation == "GetCallerIdentity":
        return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/testing"}
    if operation == "GetRole":
        return {
            "Role": {"Arn": "arn:aws:iam::123456789012:role/test-role", "RoleName": "test-role"}
        }
    if operation == "SimulatePrincipalPolicy":
        return {
            "EvaluationResults": [
                {"EvalActionName": action, "EvalDecision": "allowed", "EvalResourceName": "*"}
                for action in parameters["ActionNames"]
            ]
        }
    if operation == "PutObject":
        assert parameters["Key"].startswith(f"{PIPELINE_PREFIX}/")
        return {"ETag": "testing"}
    raise AssertionError(f"Unexpected AWS API: {operation}")


def _check_definition() -> None:
    environment = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "ap-northeast-2",
        "AWS_EC2_METADATA_DISABLED": "true",
        "S3_BUCKET": "test-bucket",
        "SAGEMAKER_EXECUTION_ROLE_ARN": "arn:aws:iam::123456789012:role/test-role",
        "PROCESSING_IMAGE_URI": "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/test:latest",
        "INPUT_DATA_S3_URI": "s3://test-bucket/uci-input/",
        "MLFLOW_APP_ARN": "arn:aws:sagemaker:ap-northeast-2:123456789012:mlflow-app/test",
    }
    with (
        patch.dict(os.environ, environment, clear=True),
        patch.object(BaseClient, "_make_api_call", _fake_aws_call),
    ):
        from sagemaker.core.helper.session_helper import Session
        from sagemaker.core.s3 import S3Uploader
        from sagemaker.core.workflow.pipeline_context import PipelineSession

        from sagemaker_xgboost_pipeline import pipeline as module

        resources = Resources.from_environment()
        session = PipelineSession(
            boto_session=boto3.Session(region_name=resources.region),
            default_bucket=resources.bucket,
            default_bucket_prefix=PIPELINE_PREFIX,
            sagemaker_config={
                "SchemaVersion": "1.0",
                "SageMaker": {"PythonSDK": {"Modules": {"TelemetryOptOut": True}}},
            },
        )
        with (
            patch.object(module, "PipelineSession", return_value=session),
            patch.object(Session, "default_bucket", return_value=resources.bucket),
            patch.object(
                Session,
                "upload_data",
                return_value=f"s3://test-bucket/{PIPELINE_PREFIX}/code/source.tar.gz",
            ),
            patch.object(
                S3Uploader,
                "upload",
                return_value=f"s3://test-bucket/{PIPELINE_PREFIX}/code/source.tar.gz",
            ),
        ):
            definition = json.loads(module.build_pipeline(resources).definition())

        assert definition["Parameters"] == [
            {
                "Name": "InputDataS3Uri",
                "Type": "String",
                "DefaultValue": "s3://test-bucket/uci-input/",
            },
            {"Name": "MinAuc", "Type": "Float", "DefaultValue": 0.70},
        ]
        steps = definition["Steps"]
        assert [step["Name"] for step in steps] == [
            "PrepareData",
            "TrainModel",
            "EvaluateModel",
            "CheckQuality",
        ]
        prepare, training, evaluation, quality = [step["Arguments"] for step in steps]
        assert prepare["ProcessingInputs"][0]["S3Input"]["S3Uri"] == {
            "Get": "Parameters.InputDataS3Uri"
        }
        channels = {channel["ChannelName"]: channel for channel in training["InputDataConfig"]}
        assert {"train", "validation", "metadata"} <= channels.keys()
        assert "test" not in channels
        assert all(
            "Steps.PrepareData" in json.dumps(channels[name])
            for name in ("train", "validation", "metadata")
        )
        inputs = {item["InputName"]: item for item in evaluation["ProcessingInputs"]}
        assert "Steps.PrepareData" in json.dumps(inputs["test"])
        assert inputs["model"]["S3Input"]["S3Uri"] == {
            "Get": "Steps.TrainModel.ModelArtifacts.S3ModelArtifacts"
        }
        condition = quality["Conditions"][0]
        assert condition["Type"] == "GreaterThanOrEqualTo"
        assert condition["RightValue"] == {"Get": "Parameters.MinAuc"}
        assert "binary_classification_metrics.auc.value" in json.dumps(condition["LeftValue"])
        assert quality["ElseSteps"] == []
        registration = quality["IfSteps"][0]
        assert registration["Name"] == "RegisterModel"
        assert registration["Arguments"]["ModelApprovalStatus"] == "PendingManualApproval"


def test_real_sdk_definition_connects_data_training_evaluation_and_both_branches() -> None:
    # Isolate the SDK's native dependencies from those loaded by the XGBoost tests.
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    _check_definition()
