# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

from insights_radar.core.clock import FixedClock
from insights_radar.graph.registry import NodeType
from insights_radar.graph.repository import GraphRepository
from insights_radar.graph.sample import (
    ENTITIES,
    EVIDENCE,
    OUTPUTS,
    RADAR_DESCRIPTION,
    RADAR_ENTRIES,
    RADAR_NAME,
    RADAR_SLUG,
    SampleData,
)
from insights_radar.identity.principal import Principal, PrincipalKind, Role
from insights_radar.radars.service import MemoryBlobStore, RadarService

ADMIN = Principal(PrincipalKind.USER, "adm", Role.ADMIN, "admin@example.com")


def test_loads_a_linked_graph_and_radar_once(graph_table: Any, ops_table: Any, clock: FixedClock) -> None:
    graph = GraphRepository(graph_table, clock)
    radars = RadarService(ops_table, MemoryBlobStore(), clock)
    sample = SampleData(graph, radars)
    first = sample.load(ADMIN)
    assert first.nodes_created == len(ENTITIES) + len(EVIDENCE) + len(OUTPUTS)
    assert first.edges_created > 30
    assert first.radar == RADAR_SLUG and first.skipped == []
    entries = radars.entries(RADAR_SLUG)
    assert sorted(e.title for e in entries) == sorted(t for t, *_ in RADAR_ENTRIES)
    org = graph.find_entity(NodeType.ORGANIZATION, "Octank")
    assert org is not None and {e.node_id for e in entries} >= {org.id}
    gap = graph.get("gap/sample-15")
    assert gap.title == "AnyCompany CRM has no AI assistant"
    again = sample.load(ADMIN).to_public()
    assert again["nodes_created"] == 0 and again["edges_created"] == 0
    assert f"radar/{RADAR_SLUG}" in again["skipped"] and "gap/sample-15" in again["skipped"]  # type: ignore[operator]


def test_finishes_an_empty_sample_radar_and_ignores_a_claimed_address(
    graph_table: Any, ops_table: Any, clock: FixedClock
) -> None:
    graph = GraphRepository(graph_table, clock)
    radars = RadarService(ops_table, MemoryBlobStore(), clock)
    radars.create(ADMIN, RADAR_SLUG, RADAR_NAME, template="competitor_landscape", description=RADAR_DESCRIPTION)
    assert SampleData(graph, radars).load(ADMIN).radar == RADAR_SLUG  # entries were missing: saved now
    assert len(radars.entries(RADAR_SLUG)) == len(RADAR_ENTRIES)
    other = RadarService(ops_table, MemoryBlobStore(), clock)
    radars.delete(ADMIN, RADAR_SLUG)
    other.create(ADMIN, RADAR_SLUG, "Somebody else's radar")
    res = SampleData(graph, other).load(ADMIN)
    assert res.radar is None and any("address taken" in s for s in res.skipped)
