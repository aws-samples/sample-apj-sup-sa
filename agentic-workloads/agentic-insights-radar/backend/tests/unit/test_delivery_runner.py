# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Digest delivery runner helpers: the SES subscriber tag, the queue message, the shared reader rule (scan cycle 1)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from insights_radar.graph.model import Node
from insights_radar.graph.registry import NodeType
from insights_radar.graph.search import nothing_hidden
from insights_radar.identity.principal import Principal, PrincipalKind, Role
from insights_radar.insights.delivery_runner import (
    MAX_REQUEUES,
    TAG_MAX,
    Queued,
    parse_message,
    subject_of_tag,
    subscriber_tag,
)
from insights_radar.insights.digest import custom_lists, reader_view
from insights_radar.modules.model import PRIVATE_OUTPUT_TYPES

UUID = "3f6e2a1c-9b7d-4e0a-8c55-0d4b1e2f3a4b"


@pytest.mark.parametrize(
    ("subject", "tag"),
    [
        (UUID, UUID),  # a user pool subject goes as it is
        ("local:alice", "local_3aalice"),
        ("a_b", "a_5fb"),  # _ itself is escaped, so the tag reads back one way only
        ("a.b@x+y", "a_2eb_40x_2by"),
    ],
)
def test_the_subscriber_tag_round_trips(subject: str, tag: str) -> None:
    assert subscriber_tag(subject) == tag
    assert subject_of_tag(tag) == subject


@pytest.mark.parametrize("subject", ["", "caf\u00e9", ":" * (TAG_MAX // 3 + 1)])
def test_a_subject_that_cannot_be_a_tag_has_none(subject: str) -> None:
    assert subscriber_tag(subject) is None


@pytest.mark.parametrize(
    "tag",
    ["", "has space", "_zz", "_4", "a_5Fb", "local_alice", "x" * (TAG_MAX + 1), "_20", "a" * 201],
)
def test_tags_subscriber_tag_never_writes_name_no_subject(tag: str) -> None:
    assert subject_of_tag(tag) is None


def body(**kw: Any) -> str:
    return json.dumps({"subject": "s-1", "id": "0123456789ab", **kw})


def test_a_message_names_its_time_claim_and_requeues() -> None:
    q = parse_message(body(due="2026-10-03T08:00:00.000Z", claim="m-1", requeues=3))
    assert q == Queued("s-1", "0123456789ab", "2026-10-03T08:00:00.000Z", "m-1", 3)
    assert json.loads(q.body("m-1")) == {
        "subject": "s-1",
        "id": "0123456789ab",
        "due": "2026-10-03T08:00:00.000Z",
        "claim": "m-1",
        "requeues": 4,
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"due": 5, "claim": None},
        {"due": "has space", "claim": "x" * 65},
        {"due": "", "claim": ["m"]},
        {"requeues": True},
        {"requeues": -2},
        {"requeues": "3"},
    ],
)
def test_unreadable_parts_of_a_message_are_left_out(extra: dict[str, Any]) -> None:
    assert parse_message(body(**extra)) == Queued("s-1", "0123456789ab")


def test_requeues_are_capped() -> None:
    q = parse_message(body(requeues=10**6))
    assert q is not None and q.requeues == MAX_REQUEUES


def _node(t: NodeType, **kw: Any) -> Node:
    return Node(id="", type=t, title="t", **kw)


def test_one_reader_rule_for_the_app_and_the_deliveries() -> None:
    writers = lambda: {"product-writer"}  # noqa: E731
    viewer = reader_view(Principal(kind=PrincipalKind.USER, subject="v", role=Role.VIEWER), writers)
    assert viewer.unseen == PRIVATE_OUTPUT_TYPES
    assert viewer.hidden(_node(NodeType.GAP)) and viewer.hidden(_node(NodeType.SIGNAL, private=True))
    assert not viewer.hidden(_node(NodeType.SIGNAL))
    editor = reader_view(Principal(kind=PrincipalKind.USER, subject="e", role=Role.EDITOR), writers)
    assert editor.hidden is nothing_hidden and editor.unseen == frozenset()


def test_custom_lists_are_the_modules_with_an_insight_kind() -> None:
    mods = [
        SimpleNamespace(insight_kind="tariff_item", nav_label="Tariffs"),
        SimpleNamespace(insight_kind=None, nav_label="Signals"),
    ]
    assert custom_lists(mods) == {"tariff_item": "Tariffs"}
