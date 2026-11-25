from __future__ import annotations

import numpy as np
import xgboost as xgb

from sagemaker_xgboost_pipeline.evaluate import score


def test_score_uses_the_feature_names_stored_in_the_model() -> None:
    features = np.array(
        [[0.0, 1.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.8]],
        dtype=np.float32,
    )
    labels = np.array([0, 1, 0, 1], dtype=np.float32)
    model = xgb.train(
        {"objective": "binary:logistic"},
        xgb.DMatrix(features, label=labels, feature_names=["feature_a", "feature_b"]),
        num_boost_round=2,
    )

    metrics = score(model, features, labels)

    assert set(metrics) == {"roc_auc", "brier_score", "ks"}
