from __future__ import annotations

import pytest

from sagemaker_xgboost_lab.config import load_aws_resources, load_training_resources


def test_training_resources_do_not_require_mlflow(monkeypatch) -> None:
    monkeypatch.setenv("SAGEMAKER_EXECUTION_ROLE_ARN", "role-arn")
    monkeypatch.setenv("S3_BUCKET", "artifact-bucket")
    monkeypatch.delenv("MLFLOW_APP_ARN", raising=False)

    resources = load_training_resources()

    assert resources.execution_role_arn == "role-arn"
    assert resources.bucket == "artifact-bucket"


def test_mlflow_resources_require_the_mlflow_app_arn(monkeypatch) -> None:
    monkeypatch.setenv("SAGEMAKER_EXECUTION_ROLE_ARN", "role-arn")
    monkeypatch.setenv("S3_BUCKET", "artifact-bucket")
    monkeypatch.delenv("MLFLOW_APP_ARN", raising=False)

    with pytest.raises(RuntimeError, match="MLFLOW_APP_ARN"):
        load_aws_resources()
