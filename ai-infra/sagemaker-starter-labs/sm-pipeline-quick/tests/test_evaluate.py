from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import numpy as np
import pytest
import xgboost as xgb

from sagemaker_xgboost_pipeline.evaluate import evaluate_model, load_model, score


@pytest.fixture
def model(tmp_path: Path) -> xgb.Booster:
    features = np.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.8]], dtype=np.float32)
    labels = np.array([0, 1, 0, 1], dtype=np.float32)
    return xgb.train(
        {"objective": "binary:logistic"},
        xgb.DMatrix(features, label=labels, feature_names=["feature_a", "feature_b"]),
        num_boost_round=2,
    )


def test_evaluation_writes_auc_property_file(tmp_path: Path, model: xgb.Booster) -> None:
    model_dir = tmp_path / "model"
    test_dir = tmp_path / "test"
    model_dir.mkdir()
    test_dir.mkdir()
    model_path = tmp_path / "xgboost-model.json"
    model.save_model(model_path)
    with tarfile.open(model_dir / "model.tar.gz", "w:gz") as archive:
        archive.add(model_path, arcname="xgboost-model.json")
        payload = b"unused"
        member = tarfile.TarInfo("../../unexpected.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    (test_dir / "test.csv").write_text("0,0,1\n1,1,0\n0,0.5,0.5\n1,0.2,0.8\n")
    output = tmp_path / "evaluation"
    report = evaluate_model(test_dir, model_dir, output)
    auc = report["binary_classification_metrics"]["auc"]["value"]
    assert 0 <= auc <= 1
    assert report["test_rows"] == 4
    assert json.loads((output / "evaluation.json").read_text()) == report
    assert not (tmp_path / "unexpected.txt").exists()


def test_auc_requires_both_labels(model: xgb.Booster) -> None:
    with pytest.raises(ValueError, match="labels 0 and 1"):
        score(model, np.zeros((2, 2)), np.zeros(2))


def test_missing_model_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "empty.tar.gz"
    with tarfile.open(archive_path, "w:gz"):
        pass
    with pytest.raises(ValueError, match="exactly one"):
        load_model(archive_path)
