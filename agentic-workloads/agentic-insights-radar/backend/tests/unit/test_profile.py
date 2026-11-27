# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ConflictError, ValidationError
from insights_radar.profile.model import (
    DEFAULT_APP_NAME,
    DEFAULT_LISTS,
    DEFAULT_TAGLINE,
    Profile,
    parse_profile,
)
from insights_radar.profile.repository import ProfileRepository


def _errors(raw: Any) -> list[str]:
    with pytest.raises(ValidationError) as e:
        parse_profile(raw)
    return list(e.value.details.get("errors", [])) or [e.value.message]


# ---- model -------------------------------------------------------------------------


def test_empty_profile_is_the_default_deployment() -> None:
    p = parse_profile({})
    assert p.app_name == DEFAULT_APP_NAME and p.tagline == DEFAULT_TAGLINE and p.about_md == ""
    pub = p.to_public()
    assert pub["lists"]["signals"]["label"] == "Signals"
    assert set(pub["lists"]) == set(DEFAULT_LISTS)
    assert p.is_visible("gaps") and p.is_visible("insight:anything")
    assert p.list_profile("insight:anything") is None


def test_relabel_and_hide_builtins_keeps_unset_fields() -> None:
    p = parse_profile(
        {
            "app_name": "  Trade Radar ",
            "tagline": "Rule changes that move freight",
            "about_md": "# About",
            "lists": {
                "signals": {"label": "Regulatory changes", "item": "Regulatory change"},
                "gaps": {"visible": False},
            },
        }
    )
    assert p.app_name == "Trade Radar"
    sig = p.list_profile("signals")
    assert sig is not None and sig.label == "Regulatory changes" and sig.help == DEFAULT_LISTS["signals"].help
    assert sig.order == DEFAULT_LISTS["signals"].order
    assert not p.is_visible("gaps")
    assert p.to_item()["lists"].keys() == {"signals", "gaps"}
    assert p.to_public()["lists"]["trends"]["label"] == "Trends"


def test_stored_profile_keeps_only_the_fields_it_set() -> None:
    p = parse_profile(
        {"lists": {"gaps": {"visible": False}, "signals": {"label": "Changes", "order": 3}, "trends": {}}}
    )
    stored = p.to_item()["lists"]
    assert stored == {
        "gaps": {"visible": False},
        "signals": {"label": "Changes", "order": 3},
        "trends": {"visible": True},
    }
    again = parse_profile(p.to_item())
    assert again.to_item() == p.to_item()
    assert again.list_profile("gaps") == p.list_profile("gaps")


def test_custom_insight_list_needs_names() -> None:
    p = parse_profile({"lists": {"insight:rule_change": {"label": "Rules", "item": "Rule", "help": "h", "order": 7}}})
    lp = p.list_profile("insight:rule_change")
    assert lp is not None and lp.visible and lp.order == 7
    assert any("needs a label" in e for e in _errors({"lists": {"insight:rule_change": {"label": "Rules"}}}))


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ([], "profile must be an object"),
        ({"colour": "red"}, "unknown fields"),
        ({"app_name": ""}, "app_name must be 1-60"),
        ({"app_name": 5}, "app_name must be text"),
        ({"app_name": "a\x07b"}, "control characters"),
        ({"tagline": "x" * 161}, "tagline must be 0-160"),
        ({"about_md": 3}, "about_md must be text"),
        ({"about_md": "x" * 4001}, "about_md must be text"),
        ({"lists": []}, "lists must be an object"),
        ({"lists": {f"insight:k{i:02d}": {"label": "L", "item": "I"} for i in range(31)}}, "at most 30"),
        ({"lists": {"widgets": {}}}, "is not a list"),
        ({"lists": {"insight:Bad Kind": {"label": "L", "item": "I"}}}, "is not a list"),
        ({"lists": {"signals": "x"}}, "must be an object"),
        ({"lists": {"signals": {"colour": 1}}}, "unknown fields: colour"),
        ({"lists": {"signals": {"visible": "no"}}}, "visible must be true or false"),
        ({"lists": {"signals": {"order": True}}}, "order must be a whole number"),
        ({"lists": {"signals": {"order": 100}}}, "order must be a whole number"),
        ({"lists": {"signals": {"label": ""}}}, "label must be 1-40"),
        ({"lists": {"signals": {"help": "x" * 401}}}, "help must be 0-400"),
    ],
)
def test_invalid_profiles_are_refused(raw: Any, fragment: str) -> None:
    assert any(fragment in e for e in _errors(raw)), _errors(raw)


def test_non_string_list_key_is_refused() -> None:
    assert any("is not a list" in e for e in _errors({"lists": {1: {}}}))


# ---- repository --------------------------------------------------------------------


@pytest.fixture
def repo(ops_table: Any, clock: FixedClock) -> ProfileRepository:
    return ProfileRepository(ops_table, clock)


def test_absent_item_reads_as_default(repo: ProfileRepository) -> None:
    p = repo.get()
    assert p == Profile() and p.version == 0


def test_put_then_get_round_trips_with_meta(repo: ProfileRepository) -> None:
    saved = repo.put({"app_name": "Trade Radar"}, "user:a", expected_version=0)
    assert saved.version == 1 and saved.updated_by == "user:a" and saved.updated_at
    fresh = repo.get()
    assert fresh.app_name == "Trade Radar" and fresh.version == 1
    again = repo.put({"app_name": "Trade Radar 2"}, "user:b", expected_version=1)
    assert again.version == 2


def test_lists_round_trip_through_dynamodb(repo: ProfileRepository) -> None:
    repo.put({"lists": {"signals": {"label": "Rules", "item": "Rule", "order": 3}}}, "seed", expected_version=None)
    lp = repo.get().list_profile("signals")
    assert lp is not None and lp.label == "Rules" and lp.order == 3 and isinstance(lp.order, int)


def test_stale_version_conflicts(repo: ProfileRepository) -> None:
    repo.put({}, "seed", expected_version=None)
    with pytest.raises(ConflictError) as e:
        repo.put({"app_name": "X"}, "user:a", expected_version=0)
    assert e.value.details["current_version"] == 1
    with pytest.raises(ConflictError):
        repo.put({"app_name": "X"}, "user:a", expected_version=5)


def test_unconditional_put_for_seed(repo: ProfileRepository) -> None:
    repo.put({"app_name": "A"}, "seed", expected_version=None)
    assert repo.put({"app_name": "B"}, "seed", expected_version=None).version == 2


def test_invalid_put_is_refused_and_nothing_written(repo: ProfileRepository) -> None:
    with pytest.raises(ValidationError):
        repo.put({"app_name": ""}, "user:a", expected_version=0)
    assert repo.get().version == 0


def test_cache_serves_within_window_and_refreshes_after(
    repo: ProfileRepository, ops_table: Any, clock: FixedClock
) -> None:
    repo.put({"app_name": "A"}, "seed", expected_version=None)
    assert repo.get(cached=True).app_name == "A"
    ops_table.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": "B"}, "version": 9})
    assert repo.get(cached=True).app_name == "A"
    clock.advance(timedelta(seconds=ProfileRepository.CACHE_SECONDS + 1))
    assert repo.get(cached=True).app_name == "B"


def test_corrupt_item_falls_back_to_default(repo: ProfileRepository, ops_table: Any) -> None:
    ops_table.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": ""}, "version": 3})
    assert repo.get().app_name == DEFAULT_APP_NAME
    # ...under its stored version, so it can be replaced (expected_version 0 would mean "create").
    assert repo.get().version == 3
    assert repo.put({"app_name": "Fixed"}, "user:a", 3).version == 4
    ops_table.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": ""}, "version": 3})
    ops_table.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "version": 4})
    assert repo.get().version == 4


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


def test_unreadable_table_falls_back_to_default(clock: FixedClock) -> None:
    t = MagicMock()
    t.get_item.side_effect = _client_error("ProvisionedThroughputExceededException")
    assert ProfileRepository(t, clock).get() == Profile()


def test_other_write_errors_propagate(clock: FixedClock) -> None:
    t = MagicMock()
    t.get_item.return_value = {}
    t.put_item.side_effect = _client_error("AccessDeniedException")
    with pytest.raises(ClientError):
        ProfileRepository(t, clock).put({}, "seed", expected_version=None)


def test_default_clock_is_system_clock() -> None:
    t = MagicMock()
    t.get_item.return_value = {}
    assert ProfileRepository(t).get().version == 0


def test_a_custom_list_without_an_order_comes_after_the_built_in_lists() -> None:
    p = parse_profile({"lists": {"insight:watch": {"label": "Watchlist", "item": "Watch item"}}})
    assert p.list_profile("insight:watch").order == 100  # type: ignore[union-attr]
    assert p.to_item()["lists"]["insight:watch"] == {"label": "Watchlist", "item": "Watch item"}


def test_sections_default_on_and_a_deployment_can_turn_products_off() -> None:
    plain = Profile()
    assert plain.section_visible("products") and plain.to_public()["sections"] == {
        "products": True,
        "radars": True,
        "sample_data": True,
    }
    assert "sections" not in plain.to_item() and "radars_help" not in plain.to_item()
    trade = parse_profile({"sections": {"products": False}, "radars_help": "  Trade measures by how soon they bite.  "})
    assert not trade.section_visible("products") and trade.to_public()["sections"] == {
        "products": False,
        "radars": True,
        "sample_data": True,
    }
    assert trade.to_public()["radars_help"] == "Trade measures by how soon they bite."
    assert trade.to_item()["sections"] == {"products": False} and parse_profile(trade.to_item()) == trade


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"sections": []}, "sections must be an object"),
        ({"sections": {"graph": False}}, "is not a section"),
        ({"sections": {"products": "off"}}, "sections.products must be true or false"),
        ({"radars_help": "x" * 401}, "radars_help must be 0-400"),
        ({"radars_help": 3}, "radars_help must be text"),
    ],
)
def test_invalid_sections_and_radar_help_are_refused(raw: Any, fragment: str) -> None:
    with pytest.raises(ValidationError) as err:
        parse_profile(raw)
    assert any(fragment in e for e in err.value.details["errors"])


def test_sections_round_trip_through_dynamodb(repo: ProfileRepository) -> None:
    repo.put({"sections": {"products": False}, "radars_help": "Rules by urgency."}, "seed", expected_version=None)
    got = repo.get()
    assert not got.section_visible("products") and got.radars_help == "Rules by urgency."


def test_a_deployment_can_turn_radars_off() -> None:
    trade = parse_profile({"sections": {"products": False, "radars": False}})
    assert not trade.section_visible("radars") and trade.to_public()["sections"] == {
        "products": False,
        "radars": False,
        "sample_data": True,
    }
    assert parse_profile(trade.to_item()) == trade


def test_a_stored_profile_with_a_section_this_code_does_not_know_still_reads() -> None:
    """Code review r17: code rolled back to before a section existed kept the newer seed's profile unreadable."""
    import pytest as _pytest

    from insights_radar.core.errors import ValidationError

    stored = parse_profile({"app_name": "Example", "sections": {"radars": False, "later_section": False}}, stored=True)
    assert stored.app_name == "Example" and not stored.section_visible("radars")
    with _pytest.raises(ValidationError):
        parse_profile({"sections": {"later_section": False}})  # the seed and the admin API stay strict


def test_the_strict_read_never_falls_back_to_the_defaults(clock: FixedClock) -> None:
    """Code review r17: the sample-data gate read a throttled or unreadable profile as 'everything on'."""
    from insights_radar.core.errors import ConflictError

    t = MagicMock()
    t.get_item.side_effect = _client_error("ProvisionedThroughputExceededException")
    with pytest.raises(ClientError):
        ProfileRepository(t, clock).get_strict()
    t = MagicMock()
    t.get_item.return_value = {"Item": {"doc": {"app_name": 7}, "version": 3}}
    with pytest.raises(ConflictError):
        ProfileRepository(t, clock).get_strict()
    t.get_item.return_value = {}
    assert ProfileRepository(t, clock).get_strict() == Profile()
    t.get_item.return_value = {"Item": {"doc": {"sections": {"sample_data": False}}, "version": 3}}
    assert not ProfileRepository(t, clock).get_strict().section_visible("sample_data")


def test_what_a_newer_release_stored_survives_old_code_and_an_admin_save(repo: ProfileRepository) -> None:
    """Code review r18: rolled-back code dropped unknown sections and fields, and the next admin save erased them."""
    newer = {"app_name": "Example", "footer_md": "x", "sections": {"radars": False, "later_section": False}}
    repo._t.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": newer, "version": 4})
    read = repo.get()
    assert read.app_name == "Example" and not read.stored_invalid and not read.section_visible("radars")
    saved = repo.put({"app_name": "Example", "tagline": "edited", "sections": {"radars": False}}, "admin", 4)
    stored = repo._t.get_item(Key={"pk": "PROFILE", "sk": "CURRENT"})["Item"]["doc"]
    assert stored["footer_md"] == "x" and stored["sections"] == {"later_section": False, "radars": False}
    assert stored["tagline"] == "edited" and saved.version == 5


def test_kept_fractions_write_back_and_an_admin_can_drop_unknown_fields(repo: ProfileRepository) -> None:
    """Code review r19: a kept fraction came back as a float (put_item refused it); no request removed a kept field."""
    from decimal import Decimal

    newer = {"app_name": "Example", "score_floor": Decimal("0.5"), "footer_md": "x", "sections": {"later": False}}
    repo._t.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": newer, "version": 2})
    read = repo.get()
    assert read.unknown_keys() == ["footer_md", "score_floor", "sections.later"]
    assert "footer_md" not in read.to_known_item() and read.to_item()["footer_md"] == "x"
    repo.put({"app_name": "Example"}, "admin", 2)
    doc = repo._t.get_item(Key={"pk": "PROFILE", "sk": "CURRENT"})["Item"]["doc"]
    assert doc["score_floor"] == Decimal("0.5") and doc["sections"] == {"later": False}
    saved = repo.put({"app_name": "Example"}, "admin", 3, frozenset({"footer_md", "sections.later"}))
    assert saved.unknown_keys() == ["score_floor"]
    doc = repo._t.get_item(Key={"pk": "PROFILE", "sk": "CURRENT"})["Item"]["doc"]
    assert "footer_md" not in doc and "sections" not in doc
    with pytest.raises(ValidationError) as e:
        repo.put({"app_name": "Example"}, "admin", 4, frozenset({"tagline"}))
    assert e.value.details == {"unknown": ["tagline"]}


def test_the_strict_read_refuses_an_item_without_a_doc(clock: FixedClock) -> None:
    """Code review r19: an item with no doc parsed as the defaults, so the sample-data gate opened."""
    t = MagicMock()
    for item in ({"version": 3}, {"doc": "x", "version": 3}):
        t.get_item.return_value = {"Item": item}
        with pytest.raises(ConflictError):
            ProfileRepository(t, clock).get_strict()
    t.get_item.return_value = {"Item": {"version": 3}}
    read = ProfileRepository(t, clock).get()
    assert read.version == 3 and read.stored_invalid  # the seed and the admin can repair it (code review r20)


def test_a_save_never_writes_over_a_profile_it_could_not_read(clock: FixedClock) -> None:
    """Code review r20: a throttled read fell back to the defaults, so the save dropped the kept fields."""
    t = MagicMock()
    t.get_item.side_effect = _client_error("ProvisionedThroughputExceededException")
    with pytest.raises(ClientError):
        ProfileRepository(t, clock).put({"app_name": "Example"}, "admin", 7)
    t.put_item.assert_not_called()


def test_a_stale_editor_is_told_to_reload_before_drop_names_are_checked(repo: ProfileRepository) -> None:
    repo._t.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": "Example"}, "version": 5})
    with pytest.raises(ConflictError) as e:
        repo.put({"app_name": "Example"}, "admin", 4, frozenset({"footer_md"}))
    assert e.value.details == {"current_version": 5}


def test_kept_numbers_are_written_back_exactly_and_long_names_can_be_dropped(repo: ProfileRepository) -> None:
    from decimal import Decimal

    long_key = "k" * 120
    exact = Decimal("0.123456789012345678901")
    doc = {"app_name": "Example", "score": exact, "sections": {long_key: False}}
    repo._t.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": doc, "version": 1})
    repo.put({"app_name": "Example"}, "admin", 1)
    stored = repo._t.get_item(Key={"pk": "PROFILE", "sk": "CURRENT"})["Item"]["doc"]
    assert stored["score"] == exact
    saved = repo.put({"app_name": "Example"}, "admin", 2, frozenset({f"sections.{long_key}"}))
    assert saved.unknown_keys() == ["score"]


def test_a_save_that_races_another_is_refused(clock: FixedClock) -> None:
    """The version matched when read, but another save landed before the write: the condition refuses it."""
    t = MagicMock()
    t.get_item.return_value = {"Item": {"doc": {"app_name": "Example"}, "version": 5}}
    t.put_item.side_effect = _client_error("ConditionalCheckFailedException")
    with pytest.raises(ConflictError) as e:
        ProfileRepository(t, clock).put({"app_name": "Example"}, "admin", 5)
    assert e.value.details == {"current_version": 5}


@pytest.mark.parametrize("bad", ["x", "5", Decimal("2.5"), True, None, Decimal(-1), Decimal(10**13)])
def test_an_item_with_an_unreadable_version_is_repairable(repo: ProfileRepository, bad: Any) -> None:
    """Code reviews r21, r22: a version that is not a whole number raised on every read, or read as a number the
    save could never match; it reads as version 0 (kept fields intact) and a save replaces it."""
    doc = {"app_name": "Example", "footer_md": "x"}
    repo._t.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": doc, "version": bad})
    read = repo.get()
    assert read.stored_invalid and read.version == 0 and read.app_name == "Example"
    assert read.unknown_keys() == ["footer_md"]
    with pytest.raises(ConflictError):
        repo.get_strict()
    assert repo.put({"app_name": "Fixed"}, "admin", 0).version == 1
    stored = repo._t.get_item(Key={"pk": "PROFILE", "sk": "CURRENT"})["Item"]
    assert stored["version"] == 1 and stored["doc"]["footer_md"] == "x"
    with pytest.raises(ConflictError):  # a real item is still protected from "create only"
        repo.put({"app_name": "Again"}, "admin", 0)


def test_an_item_without_a_version_is_matched_as_such(repo: ProfileRepository) -> None:
    repo._t.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": "Example"}})
    assert repo.get().version == 0 and not repo.get().stored_invalid
    assert repo.put({"app_name": "Fixed"}, "admin", 0).version == 1


def test_a_dropped_connection_on_read_falls_back_like_a_failed_read(clock: FixedClock) -> None:
    """Code review r23: only ClientError fell back; a timeout escaped get() and /api/config."""
    from botocore.exceptions import EndpointConnectionError

    t = MagicMock()
    t.get_item.side_effect = EndpointConnectionError(endpoint_url="https://ddb")
    assert ProfileRepository(t, clock).get() == Profile()
    with pytest.raises(EndpointConnectionError):
        ProfileRepository(t, clock).put({"app_name": "Example"}, "admin", 1)


def test_a_configuration_fault_is_not_hidden_behind_the_built_in_text(clock: FixedClock) -> None:
    """Code review r24: catching every BotoCoreError turned a missing credential into the default profile."""
    from botocore.exceptions import NoCredentialsError

    t = MagicMock()
    t.get_item.side_effect = NoCredentialsError()
    with pytest.raises(NoCredentialsError):
        ProfileRepository(t, clock).get()


def test_the_version_count_starts_again_at_the_top_of_the_range(repo: ProfileRepository) -> None:
    from insights_radar.profile.repository import VERSION_MAX

    repo._t.put_item(Item={"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": "Example"}, "version": VERSION_MAX})
    assert repo.get().version == VERSION_MAX
    saved = repo.put({"app_name": "Example"}, "admin", VERSION_MAX)
    assert saved.version == 1 and repo.get().version == 1 and not repo.get().stored_invalid


@pytest.mark.parametrize(
    ("name", "starts"),
    [
        ("a" * 60, "a" * 60),  # fits exactly: kept whole
        ("a" * 61, "a" * 60),
        ("\U0001f600" * 30, "\U0001f600" * 5),  # 12 characters each once escaped: five fit in 60
        ('x"' * 40, 'x"' * 20),  # a quote is 2 once escaped
        ("ab" + "\u0e20" * 20, "ab" + "\u0e20" * 9),  # cut on a whole character at the boundary
    ],
)
def test_audit_names_are_cut_on_whole_characters_as_written(name: str, starts: str) -> None:
    import json

    from insights_radar.api.routes import _audit_name

    shown = _audit_name(name)
    assert shown.startswith(starts) and len(json.dumps(shown)) - 2 <= 60 + 16
    if len(json.dumps(name)) - 2 <= 60:
        assert shown == name
    else:
        assert shown[len(starts) :].startswith("...#") and len(shown) == len(starts) + 16
