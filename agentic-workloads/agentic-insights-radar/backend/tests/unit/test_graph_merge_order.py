# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""A merge whose redirect lands on an edge an earlier move of the same merge already made (code review r73, r74).

test_r70 in test_graph_repository reaches this only when the record ids happen to order the plain move first, so
the "already there" branch was covered on some runs and not others. Here the order is fixed both ways."""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from insights_radar.core.clock import FixedClock
from insights_radar.graph.model import Node
from insights_radar.graph.registry import EdgeType, NodeType
from insights_radar.graph.repository import GraphRepository
from tests.unit.test_graph_repository import _three


@pytest.fixture
def repo(graph_table: Any, clock: FixedClock) -> GraphRepository:
    return GraphRepository(graph_table, clock)


@pytest.mark.parametrize("redirect_last", [True, False])
def test_a_redirect_onto_an_edge_already_made_is_counted_once(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch, redirect_last: bool
) -> None:
    waiting, waiting_into, _ = _three(repo, f"Order {redirect_last}")
    dup = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title=f"Order Dup {redirect_last}"), "u")[0]
    kept = repo.upsert_entity(Node(id="", type=NodeType.ORGANIZATION, title=f"Order Kept {redirect_last}"), "u")[0]
    repo.link(dup.id, EdgeType.PARTNERS_WITH, waiting.id, "u")

    def throttled(node_id: str) -> int:
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "BatchWriteItem")

    monkeypatch.setattr(repo, "delete", throttled)
    with pytest.raises(ClientError):
        repo.merge_entity(waiting.id, waiting_into.id, "u", apply=True)  # names moved, then stopped
    monkeypatch.undo()
    plan = repo._merge_plan

    def ordered(*a: Any, **kw: Any) -> Any:
        report, moves, others = plan(*a, **kw)
        # The move to the half-merged entity is the one redirected; put it after (or before) the plain copy.
        moves = sorted(moves, key=lambda m: (m[0].target == waiting.id) == redirect_last)
        return report, moves, others

    monkeypatch.setattr(repo, "_merge_plan", ordered)
    report = repo.merge_entity(dup.id, kept.id, "u", apply=True)
    assert report["applied"] and not report["dropped"]
    assert report["edges"] == report["moved"] + len(report["dropped"]) + len(report["redirected"])
    assert [e.target for e in repo.edges(kept.id)].count(waiting_into.id) == 1
