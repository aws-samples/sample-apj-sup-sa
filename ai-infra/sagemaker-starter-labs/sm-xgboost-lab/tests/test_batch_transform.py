from __future__ import annotations

from types import SimpleNamespace

import pytest

from sagemaker_xgboost_lab.batch_transform import (
    latest_approved_model_package,
    model_package_group_name,
)


def test_uses_the_mlflow_created_model_package_group_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_PACKAGE_GROUP_NAME", raising=False)
    client = SimpleNamespace(
        list_model_package_groups=lambda **_: {
            "ModelPackageGroupSummaryList": [
                {"ModelPackageGroupName": "sagemaker-xgboost-lab-4663e9"}
            ]
        }
    )

    assert model_package_group_name(client) == "sagemaker-xgboost-lab-4663e9"


def test_uses_an_explicit_model_package_group(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PACKAGE_GROUP_NAME", "chosen-group")

    assert model_package_group_name(SimpleNamespace()) == "chosen-group"


def test_returns_latest_approved_model_package() -> None:
    package_request: dict[str, object] = {}

    def list_model_packages(**kwargs: object) -> dict[str, object]:
        package_request.update(kwargs)
        return {
            "ModelPackageSummaryList": [{"ModelPackageArn": "package-arn"}]
        }

    client = SimpleNamespace(
        list_model_packages=list_model_packages,
        describe_model_package=lambda **_: {
            "InferenceSpecification": {
                "SupportedTransformInstanceTypes": ["ml.c5.xlarge"]
            },
        },
    )

    assert (
        latest_approved_model_package(
            client,
            "registered-model-4663e9",
            "ml.c5.xlarge",
        )
        == "package-arn"
    )
    assert package_request == {
        "ModelPackageGroupName": "registered-model-4663e9",
        "ModelApprovalStatus": "Approved",
        "SortBy": "CreationTime",
        "SortOrder": "Descending",
        "MaxResults": 100,
    }


def test_requires_an_approved_model_package() -> None:
    client = SimpleNamespace(
        list_model_packages=lambda **_: {"ModelPackageSummaryList": []},
    )

    with pytest.raises(RuntimeError, match="approved model version"):
        latest_approved_model_package(
            client,
            "registered-model-4663e9",
            "ml.c5.xlarge",
        )


def test_skips_an_incompatible_newer_approved_model() -> None:
    client = SimpleNamespace(
        list_model_packages=lambda **_: {
            "ModelPackageSummaryList": [
                {"ModelPackageArn": "newer-incompatible"},
                {"ModelPackageArn": "older-compatible"},
            ]
        },
        describe_model_package=lambda ModelPackageName: {
            "InferenceSpecification": {
                "SupportedTransformInstanceTypes": (
                    ["ml.c6i.large"]
                    if ModelPackageName == "newer-incompatible"
                    else ["ml.c5.xlarge"]
                )
            },
        },
    )

    assert (
        latest_approved_model_package(
            client,
            "registered-model-4663e9",
            "ml.c5.xlarge",
        )
        == "older-compatible"
    )
