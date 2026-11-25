from __future__ import annotations

from sagemaker_xgboost_lab.train import candidate_hyperparameters
from sagemaker_xgboost_lab.util import is_gpu_instance


def test_gpu_instance_uses_gpu_hist() -> None:
    assert is_gpu_instance("ml.g4dn.xlarge")
    assert is_gpu_instance("ml.g5.xlarge")
    assert not is_gpu_instance("ml.c6i.xlarge")
    assert candidate_hyperparameters("ml.g5.xlarge")["default"]["tree_method"] == "gpu_hist"
    assert candidate_hyperparameters("ml.c6i.xlarge")["default"]["tree_method"] == "hist"


def test_default_candidate_is_intentionally_smaller_than_tuned() -> None:
    candidates = candidate_hyperparameters("ml.c6i.xlarge")

    assert candidates["default"]["num_round"] == 10
    assert candidates["default"]["max_depth"] == 1
    assert candidates["default"]["eta"] == 0.1
    assert candidates["tuned"]["num_round"] > candidates["default"]["num_round"]
    assert candidates["tuned"]["max_depth"] > candidates["default"]["max_depth"]
