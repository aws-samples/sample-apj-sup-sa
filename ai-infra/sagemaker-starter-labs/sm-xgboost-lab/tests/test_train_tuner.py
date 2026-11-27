from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from sagemaker.core.parameter import ContinuousParameter, IntegerParameter
from sagemaker.train.tuner import HyperparameterTuner

import sagemaker_xgboost_lab.train_tuner as tuner_module
from sagemaker_xgboost_lab.train_tuner import (
    BOOSTER_EARLY_STOPPING_ROUNDS,
    HYPERPARAMETER_RANGES,
    MAX_BOOST_ROUNDS,
    TUNING_TAGS,
    log_trial_runs,
)


def test_tuning_uses_a_broad_xgboost_search_space() -> None:
    assert set(HYPERPARAMETER_RANGES) == {
        "eta",
        "colsample_bytree",
        "gamma",
        "lambda",
        "max_depth",
        "min_child_weight",
        "subsample",
    }
    assert isinstance(HYPERPARAMETER_RANGES["eta"], ContinuousParameter)
    assert HYPERPARAMETER_RANGES["eta"].scaling_type == "Logarithmic"
    assert HYPERPARAMETER_RANGES["eta"].min_value == 0.005
    assert HYPERPARAMETER_RANGES["eta"].max_value == 0.2
    assert isinstance(HYPERPARAMETER_RANGES["max_depth"], IntegerParameter)
    assert HYPERPARAMETER_RANGES["max_depth"].min_value == 2
    assert HYPERPARAMETER_RANGES["max_depth"].max_value == 10
    assert HYPERPARAMETER_RANGES["lambda"].scaling_type == "Logarithmic"
    assert MAX_BOOST_ROUNDS == 1200
    assert BOOSTER_EARLY_STOPPING_ROUNDS == 50


def test_logs_each_trial_as_a_compact_nested_mlflow_run(monkeypatch) -> None:
    class RecordingMlflow:
        def __init__(self) -> None:
            self.runs: list[dict[str, object]] = []
            self.current_run: dict[str, object] | None = None

        @contextmanager
        def start_run(self, **kwargs: object):
            run: dict[str, object] = {"kwargs": kwargs, "params": {}, "metrics": {}, "tags": {}}
            self.runs.append(run)
            self.current_run = run
            yield SimpleNamespace()
            self.current_run = None

        def log_params(self, params: dict[str, object]) -> None:
            assert self.current_run is not None
            self.current_run["params"] = params

        def log_metric(self, name: str, value: float) -> None:
            assert self.current_run is not None
            self.current_run["metrics"] = {name: value}

        def set_tags(self, tags: dict[str, str]) -> None:
            assert self.current_run is not None
            self.current_run["tags"] = tags

    mlflow = RecordingMlflow()
    monkeypatch.setattr(tuner_module, "mlflow", mlflow)

    log_trial_runs(
        trials=[
            {
                "training_job_name": "xgb-amt-001-a1b2c3",
                "status": "Completed",
                "objective_metric": 0.77,
                "hyperparameters": {"eta": "0.03", "max_depth": "5"},
            },
            {
                "training_job_name": "xgb-amt-002-d4e5f6",
                "status": "Failed",
                "objective_metric": None,
                "hyperparameters": {"eta": "0.1", "max_depth": "3"},
            },
        ],
        static_hyperparameters={"num_round": 1200},
        tuning_job_name="xgb-amt",
    )

    first, second = mlflow.runs
    assert first["kwargs"] == {"run_name": "trial-01-a1b2c3", "nested": True}
    assert first["params"] == {
        "xgboost.num_round": 1200,
        "xgboost.eta": "0.03",
        "xgboost.max_depth": "5",
        "amt.rank": 1,
    }
    assert first["metrics"] == {"validation-auc": 0.77}
    assert first["tags"]["sagemaker.training_job_name"] == "xgb-amt-001-a1b2c3"
    assert second["kwargs"] == {"run_name": "trial-02-d4e5f6", "nested": True}
    assert second["metrics"] == {}


def test_tuning_tags_use_the_dictionary_format_expected_by_sdk_v3() -> None:
    model_trainer = SimpleNamespace(
        tags=[],
        sagemaker_session=SimpleNamespace(
            settings=SimpleNamespace(include_jumpstart_tags=False)
        ),
    )
    tuner = HyperparameterTuner(
        model_trainer=model_trainer,
        objective_metric_name="validation:auc",
        hyperparameter_ranges=HYPERPARAMETER_RANGES,
        tags=TUNING_TAGS,
    )

    tuner._prepare_tags_for_tuning()

    assert tuner.tags == [
        {"Key": "Project", "Value": "sagemaker-xgboost-lab"},
        {"Key": "TrainingType", "Value": "AutomaticModelTuning"},
    ]
