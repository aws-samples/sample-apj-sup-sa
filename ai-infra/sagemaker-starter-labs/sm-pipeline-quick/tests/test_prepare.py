from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sagemaker_xgboost_pipeline.prepare import prepare_data


@pytest.fixture
def raw_input(tmp_path: Path) -> Path:
    directory = tmp_path / "input"
    directory.mkdir()
    columns = [f"feature_{index}" for index in range(23)]
    frame = pd.DataFrame(np.arange(200 * 23).reshape(200, 23), columns=columns)
    frame.insert(0, "label", [0, 1] * 100)
    frame.to_csv(directory / "data.csv", index=False)
    source = {
        "dataset_id": 350,
        "dataset_name": "Default of Credit Card Clients",
        "feature_names": columns,
        "sha256": hashlib.sha256((directory / "data.csv").read_bytes()).hexdigest(),
    }
    (directory / "source.json").write_text(json.dumps(source))
    return directory


def test_splits_are_disjoint_stratified_and_reproducible(raw_input: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    summary = prepare_data(raw_input, first)
    assert summary["split_rows"] == {"train": 140, "validation": 30, "test": 30}
    assert prepare_data(raw_input, second) == summary
    row_ids = []
    for split in ("train", "validation", "test"):
        path = first / split / f"{split}.csv"
        frame = pd.read_csv(path, header=None)
        assert frame.shape[1] == 24
        assert set(frame[0]) == {0, 1}
        assert frame[0].mean() == 0.5
        row_ids.append(set(frame[1]))
        assert path.read_bytes() == (second / split / f"{split}.csv").read_bytes()
    assert not (row_ids[0] & row_ids[1] or row_ids[0] & row_ids[2] or row_ids[1] & row_ids[2])
    assert len(set.union(*row_ids)) == 200
    assert (first / "metadata/data-summary.json").is_file()


def test_changed_snapshot_is_rejected(raw_input: Path, tmp_path: Path) -> None:
    with (raw_input / "data.csv").open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="verified UCI"):
        prepare_data(raw_input, tmp_path / "output")


@pytest.mark.parametrize("failure", ["columns", "label", "missing", "infinite"])
def test_invalid_input_is_rejected(raw_input: Path, tmp_path: Path, failure: str) -> None:
    path = raw_input / "data.csv"
    frame = pd.read_csv(path)
    if failure == "columns":
        frame = frame.rename(columns={"feature_0": "unexpected"})
    elif failure == "label":
        frame["label"] = 2
    elif failure == "missing":
        frame["feature_0"] = np.nan
    else:
        frame["feature_0"] = np.inf
    frame.to_csv(path, index=False)
    source_path = raw_input / "source.json"
    source = json.loads(source_path.read_text())
    source["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    source_path.write_text(json.dumps(source))
    with pytest.raises(ValueError):
        prepare_data(raw_input, tmp_path / "output")
