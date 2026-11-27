# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""The live end-to-end matrix keeps its size: at least 1,000 scenarios over at least 100 subfeatures, unique IDs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
import scenarios  # noqa: E402


def test_matrix_size_and_ids():
    s = scenarios.scenarios()
    assert len(s) >= 1000
    assert len({x.feature for x in s}) >= 100
    assert len({x.id for x in s}) == len(s)
    assert {x.idp for x in s} == {"cognito", "okta", "jumpcloud"}
    assert {x.kind for x in s} >= {"http", "proxy", "mcp", "dashboard", "opencode", "terraform"}
