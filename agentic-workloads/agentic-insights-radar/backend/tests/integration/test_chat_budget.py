# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

import pytest

from insights_radar.api.app import Application
from insights_radar.core.clock import FixedClock
from tests.integration.harness import CALLER_ARN, CHAT_CALLER_ARN, Client, TestContext


@pytest.fixture
def api(graph_table: Any, ops_table: Any, clock: FixedClock) -> Client:
    return Client(Application(TestContext(graph_table, ops_table, clock)))


def _report(api: Client, arn: str | None, **usage: Any) -> tuple[int, Any]:
    """A turn as the chat runtime reports it: a small reservation, then the real usage (B-101: every
    report carries a turn id, so a retried report is never charged twice)."""
    import uuid

    if "turn_id" in usage or "reserve_tokens" in usage:  # the test drives the turn itself
        status, out, _ = api.call(
            "POST", "/agent/chat/usage", {"subject": "u-viewer", **usage}, role="anon", iam_arn=arn
        )
        return status, out
    turn = uuid.uuid4().hex
    hold = {"subject": "u-viewer", "turn_id": turn, "reserve_tokens": 1}
    api.call("POST", "/agent/chat/usage", hold, role="anon", iam_arn=arn)
    body = {"subject": "u-viewer", "turn_id": turn, **usage}
    status, out, _ = api.call("POST", "/agent/chat/usage", body, role="anon", iam_arn=arn)
    return status, out


def test_status_and_limits(api: Client) -> None:
    s, st, _ = api.call("GET", "/api/chat/status", role="viewer")
    assert s == 200 and st["allowed"] and st["genai_enabled"] and st["user"]["remaining"] == 300_000
    assert api.call("GET", "/api/chat/settings", role="editor")[0] == 403
    s, lim, _ = api.call("PUT", "/api/chat/settings", {"daily_tokens": 1000, "per_user_daily_tokens": 500})
    assert s == 200 and lim == {"daily_tokens": 1000, "per_user_daily_tokens": 500}
    assert api.call("GET", "/api/chat/settings")[1] == lim
    assert api.call("PUT", "/api/chat/settings", {"daily_tokens": 10, "per_user_daily_tokens": 500})[0] == 400
    assert api.call("PUT", "/api/chat/settings", {"daily_tokens": -1, "per_user_daily_tokens": 0})[0] == 400


def test_only_the_chat_runtime_reports_usage(api: Client) -> None:
    assert _report(api, None, input_tokens=10)[0] == 403
    assert _report(api, CALLER_ARN, input_tokens=10)[0] == 403  # the module runtime is not the chat runtime
    status = api.call("POST", "/agent/chat/usage", {"subject": "u", "input_tokens": 1}, role="viewer")[0]
    assert status == 403
    assert _report(api, CHAT_CALLER_ARN, input_tokens=-1)[0] == 400
    assert _report(api, CHAT_CALLER_ARN, input_tokens=10, extra=1)[0] == 400
    s, b = _report(api, CHAT_CALLER_ARN, input_tokens=100, output_tokens=20)
    assert s == 200 and b == {"weighted_tokens": 200}


def test_per_user_cap_closes_chat_for_that_user_only(api: Client) -> None:
    api.call("PUT", "/api/chat/settings", {"daily_tokens": 10_000, "per_user_daily_tokens": 1_000})
    _report(api, CHAT_CALLER_ARN, input_tokens=1_000)
    assert api.call("GET", "/api/chat/status", role="viewer")[1]["allowed"] is False
    assert api.call("GET", "/api/chat/status", role="editor")[1]["allowed"] is True


def test_a_report_without_a_turn_id_is_refused(api: Client) -> None:
    body = {"subject": "u-viewer", "input_tokens": 10}
    assert api.call("POST", "/agent/chat/usage", body, role="anon", iam_arn=CHAT_CALLER_ARN)[0] == 400


def test_implausible_reports_are_rejected(api: Client) -> None:
    assert _report(api, CHAT_CALLER_ARN, input_tokens=5_000_001)[0] == 400


def test_shared_cap_closes_chat_for_everyone(api: Client) -> None:
    api.call("PUT", "/api/chat/settings", {"daily_tokens": 1_000, "per_user_daily_tokens": 1_000})
    _report(api, CHAT_CALLER_ARN, input_tokens=1_000)
    assert api.call("GET", "/api/chat/status", role="editor")[1]["allowed"] is False


def test_charge_rejects_implausible_usage_directly(ops_table: Any, clock: FixedClock) -> None:
    from insights_radar.chat.budget import ChatBudget
    from insights_radar.core.errors import ValidationError
    from insights_radar.core.tokens import TokenUsage
    from insights_radar.modules.repository import ModuleRepository

    with pytest.raises(ValidationError):
        ChatBudget(ModuleRepository(ops_table, clock), ops_table).charge("u", TokenUsage(0, 6_000_000, 0, 0))


def _remaining(api: Client, role: str = "viewer") -> tuple[int, int]:
    st = api.call("GET", "/api/chat/status", role=role)[1]
    return st["shared"]["remaining"], st["user"]["remaining"]


def test_turns_reserve_their_ceiling_then_settle_once(api: Client) -> None:
    api.call("PUT", "/api/chat/settings", {"daily_tokens": 10_000, "per_user_daily_tokens": 5_000})
    s, b = _report(api, CHAT_CALLER_ARN, turn_id="turn-0001", reserve_tokens=3_000)
    assert s == 200 and b == {"reserved": 3_000} and _remaining(api) == (7_000, 2_000)
    # A parallel turn by the same user cannot also hold 3,000: the per-user ledger would go over.
    assert _report(api, CHAT_CALLER_ARN, turn_id="turn-0002", reserve_tokens=3_000)[1] == {"reserved": 0}
    assert _remaining(api) == (7_000, 2_000)  # the refused user hold gave back its shared hold
    s, b = _report(api, CHAT_CALLER_ARN, turn_id="turn-0001", input_tokens=100, output_tokens=20)
    assert s == 200 and b == {"weighted_tokens": 200} and _remaining(api) == (9_800, 4_800)
    # A retried report for the same turn is not charged again.
    assert _report(api, CHAT_CALLER_ARN, turn_id="turn-0001", input_tokens=100)[1] == {"weighted_tokens": 0}
    assert _remaining(api) == (9_800, 4_800)


def test_a_lost_report_leaves_the_hold_charged_and_holds_cap_the_shared_budget(api: Client) -> None:
    api.call("PUT", "/api/chat/settings", {"daily_tokens": 4_000, "per_user_daily_tokens": 4_000})
    assert _report(api, CHAT_CALLER_ARN, turn_id="turn-lost1", reserve_tokens=3_000)[1] == {"reserved": 3_000}
    other = {"subject": "u-editor", "turn_id": "turn-other", "reserve_tokens": 2_000}
    _, b, _ = api.call("POST", "/agent/chat/usage", other, role="anon", iam_arn=CHAT_CALLER_ARN)
    assert b == {"reserved": 0}  # another user is refused by the shared ledger
    assert _remaining(api) == (1_000, 1_000)  # never settled: still counts against both budgets
    # Another user cannot settle (and so release) this user's hold.
    forged = {"subject": "u-editor", "turn_id": "turn-lost1", "input_tokens": 1}
    assert api.call("POST", "/agent/chat/usage", forged, role="anon", iam_arn=CHAT_CALLER_ARN)[1] == {
        "weighted_tokens": 0
    }
    assert _remaining(api) == (1_000, 1_000)


@pytest.mark.parametrize(
    "body",
    [
        {"reserve_tokens": 100},
        {"turn_id": "short", "reserve_tokens": 100},
        {"turn_id": "turn/../x", "input_tokens": 1},
        {"turn_id": "turn-0001", "reserve_tokens": 0},
        {"turn_id": "turn-0001", "reserve_tokens": 1_000_001},
    ],
)
def test_reservation_input_is_validated(api: Client, body: dict[str, Any]) -> None:
    assert _report(api, CHAT_CALLER_ARN, **body)[0] == 400


def test_a_hold_is_released_on_the_day_it_was_made(ops_table: Any, clock: FixedClock) -> None:
    from datetime import timedelta

    from insights_radar.chat.budget import CHAT_SLUG, ChatBudget
    from insights_radar.core.tokens import TokenUsage
    from insights_radar.modules.repository import ModuleRepository

    modules = ModuleRepository(ops_table, clock)
    budget = ChatBudget(modules, ops_table, clock)
    assert budget.reserve("u", "turn-midnight", 500) == 500
    clock.advance(timedelta(days=1))
    budget.settle("u", "turn-midnight", TokenUsage(100, 0, 0, 0))
    assert modules.budget(CHAT_SLUG, 1_000).remaining == 900  # today: only the real usage
    clock.advance(timedelta(days=-1))
    assert modules.budget(CHAT_SLUG, 1_000).remaining == 1_000  # yesterday: the hold was given back
    assert modules.reserve(CHAT_SLUG, 2_000, 1_000) is None  # a hold larger than the limit is refused


def test_reserve_and_settle_surface_other_table_errors(ops_table: Any, clock: FixedClock) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.chat.budget import ChatBudget
    from insights_radar.core.tokens import TokenUsage
    from insights_radar.modules.repository import ModuleRepository

    class Throttled:
        def __getattr__(self, name: str) -> Any:
            if name in ("update_item", "delete_item"):

                def fail(**kw: Any) -> None:
                    raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, name)

                return fail
            return getattr(ops_table, name)

    table: Any = Throttled()
    with pytest.raises(ClientError):
        ModuleRepository(table, clock).reserve("chat", 10, 100)
    with pytest.raises(ClientError):
        ChatBudget(ModuleRepository(ops_table, clock), table, clock).settle("u", "turn-0001", TokenUsage(1, 0, 0, 0))


def test_a_give_back_that_overtakes_its_reserve_leaves_nothing_held(ops_table: Any, clock: FixedClock) -> None:
    from insights_radar.chat.budget import ChatBudget
    from insights_radar.core.tokens import TokenUsage
    from insights_radar.modules.repository import ModuleRepository

    modules = ModuleRepository(ops_table, clock)
    budget = ChatBudget(modules, ops_table, clock)
    before = budget.status("u")
    assert budget.settle("u", "turn-race1", TokenUsage()) == 0  # the client timed out and gave back first
    assert budget.reserve("u", "turn-race1", 5_000) == 0  # the slow reserve lands: it holds nothing
    assert budget.status("u") == before
    assert budget.settle("u", "turn-race1", TokenUsage()) == 0  # a second give-back is harmless


@pytest.mark.parametrize("op", ["put_item", "update_item"])
def test_other_errors_writing_turn_records_are_raised(ops_table: Any, clock: FixedClock, op: str) -> None:
    from botocore.exceptions import ClientError

    from insights_radar.chat.budget import ChatBudget
    from insights_radar.core.tokens import TokenUsage
    from insights_radar.modules.repository import ModuleRepository

    class Failing:
        def __getattr__(self, name: str) -> Any:
            if name == op:

                def fail(**kw: Any) -> None:
                    raise ClientError({"Error": {"Code": "InternalServerError"}}, name)

                return fail
            return getattr(ops_table, name)

    budget = ChatBudget(ModuleRepository(ops_table, clock), Failing(), clock)
    with pytest.raises(ClientError):
        if op == "put_item":
            budget.reserve("u", "turn-err01", 5_000)
        else:
            # r2 B2-047: settling is one conditional update, so this is where its errors come from.
            budget.settle("u", "turn-err02", TokenUsage())
