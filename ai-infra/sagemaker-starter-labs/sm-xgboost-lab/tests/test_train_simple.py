from __future__ import annotations

from sagemaker_xgboost_lab.train_simple import SIMPLE_HYPERPARAMETERS


def test_simple_training_uses_one_readable_cpu_xgboost_configuration() -> None:
    assert SIMPLE_HYPERPARAMETERS["objective"] == "binary:logistic"
    assert SIMPLE_HYPERPARAMETERS["eval_metric"] == "auc"
    assert SIMPLE_HYPERPARAMETERS["tree_method"] == "hist"
    assert SIMPLE_HYPERPARAMETERS["num_round"] == 300
    assert SIMPLE_HYPERPARAMETERS["max_depth"] == 5
