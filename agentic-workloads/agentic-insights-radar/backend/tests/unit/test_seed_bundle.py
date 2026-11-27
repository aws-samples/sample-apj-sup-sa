# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

import pytest

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ValidationError
from insights_radar.modules.repository import ModuleRepository
from insights_radar.profile.repository import ProfileRepository
from insights_radar.seed.bundle import Seeder, parse_bundle

LONG = "You are the ${list_label} module of ${app_name}. " + "Track public rule changes. " * 12
BUNDLE: dict[str, Any] = {
    "version": 1,
    "description": "test",
    "profile": {
        "app_name": "Trade Radar",
        "lists": {"signals": {"label": "Regulatory changes", "item": "Regulatory change"}, "gaps": {"visible": False}},
    },
    "modules": [
        {
            "slug": "signal",
            "instructions": LONG,
            "prompt_mode": "replace",
            "interval_minutes": 120,
            "attribute_schema": [
                {
                    "key": "urgency",
                    "label": "Urgency",
                    "type": "enum",
                    "required": True,
                    "filter": True,
                    "values": [{"value": "act_now", "label": "Act now", "rank": 1}],
                }
            ],
            "input_types": ["fact", "event"],
        },
        {"slug": "gap", "enabled": False},
    ],
    "custom_modules": [
        {
            "slug": "watch",
            "name": "Watch",
            "insight_kind": "watch_item",
            "insight_label": "Watchlist",
            "grants": ["kg_read", "kg_write_output"],
        },
    ],
    "sources": [
        {
            "module": "scout",
            "name": "Federal Register",
            "type": "public_api",
            "address": "https://www.federalregister.gov/api/v1/documents.json",
            "instruction": "BIS and USTR only",
        },
    ],
}


@pytest.fixture
def parts(ops_table: Any, clock: FixedClock) -> tuple[Seeder, ModuleRepository, ProfileRepository]:
    modules = ModuleRepository(ops_table, clock)
    modules.seed_builtins()
    profile = ProfileRepository(ops_table, clock)
    return Seeder(ops_table, modules, profile), modules, profile


def test_a_bundle_sets_up_a_deployment(parts: tuple[Seeder, ModuleRepository, ProfileRepository]) -> None:
    seeder, modules, profile = parts
    result = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert result["profile"] == "applied" and result["errors"] == []
    # The built-in gap module is already off, as the bundle wants: nothing to write.
    assert result["modules"] == {"gap": "unchanged", "signal": "applied", "watch": "created"}
    assert result["sources_added"] == 1
    sig = modules.get("signal")
    assert sig.prompt_mode == "replace" and sig.interval_minutes == 120 and sig.attribute_schema.field("urgency")
    assert [t.value for t in sig.input_types] == ["fact", "event"]
    assert modules.get("gap").enabled is False
    assert modules.get("watch").insight_label == "Watchlist"
    assert profile.get().list_profile("signals").label == "Regulatory changes"  # type: ignore[union-attr]


def test_reapplying_changes_nothing_and_admin_edits_win(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, modules, profile = parts
    bundle = parse_bundle(BUNDLE)
    seeder.apply(bundle)
    before = (modules.get("signal").version, profile.get().version)
    again = seeder.apply(bundle).to_public()
    # Unchanged since it was applied in full: nothing is written, so no version moves.
    assert again["profile"] == "unchanged" and again["modules"] == {} and again["sources_added"] == 0
    assert (modules.get("signal").version, profile.get().version) == before
    # An administrator edits the module and the profile; the next deploy keeps both.
    sig = modules.get("signal")
    modules.update("signal", {"interval_minutes": 60}, sig.version)
    p = profile.get()
    profile.put({"app_name": "Renamed"}, "user:a", expected_version=p.version)
    assert seeder.apply(bundle).to_public()["profile"] == "unchanged"
    changed = parse_bundle({**BUNDLE, "description": "a new release of the bundle"})
    kept = seeder.apply(changed).to_public()
    assert kept["sources_skipped"] == 1 and kept["errors"] == []
    assert kept["modules"]["signal"] == "kept (edited by an administrator)"
    assert kept["profile"] == "kept (edited by an administrator)"
    assert modules.get("signal").interval_minutes == 60 and profile.get().app_name == "Renamed"


def test_problems_while_applying_are_reported_not_raised(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, modules, _ = parts
    bad = {
        "version": 1,
        "modules": [
            {"slug": "nope"},
            {"slug": "trend", "prompt_mode": "replace", "instructions": "short"},
            {"slug": "content"},
        ],
        "custom_modules": [
            {"slug": "BAD SLUG", "name": "x", "insight_kind": "k_k"},
            {"slug": "signal", "name": "Signals", "insight_kind": "x_x", "interval_minutes": 90},
        ],
        "sources": [
            {"module": "signal", "name": "S", "type": "rss", "address": "https://e.example.com/feed"},
            {"module": "ghost", "name": "G", "type": "rss", "address": "https://g.example.com/feed"},
        ],
    }
    result = seeder.apply(parse_bundle(bad)).to_public()
    joined = "\n".join(result["errors"])
    assert "module nope does not exist" in joined
    assert "module trend:" in joined and "at least 200" in joined
    assert "custom module BAD SLUG" in joined
    assert "source 'S' for signal" in joined and "source 'G' for ghost" in joined
    assert result["modules"]["signal"] == "applied"  # an existing slug is overridden, not created
    assert modules.get("signal").interval_minutes == 90
    assert "content" not in result["modules"]  # nothing to change


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ([], "must be a JSON object"),
        ({"version": 2}, "version must be 1"),
        ({"version": 1, "colour": 1}, "unknown fields"),
        ({"version": 1, "profile": {"app_name": ""}}, "profile:"),
        ({"version": 1, "modules": "x"}, "modules must be a list"),
        ({"version": 1, "modules": [{}]}, "modules[0] needs a slug"),
        ({"version": 1, "modules": [{"slug": "signal", "grants": ["web_fetch"]}]}, "may not set: grants"),
        (
            {"version": 1, "modules": [{"slug": "signal", "attribute_schema": [{"key": "x"}]}]},
            "modules[0].attribute_schema",
        ),
        ({"version": 1, "modules": [{"slug": "signal", "input_types": ["nope"]}]}, "input_types"),
        ({"version": 1, "modules": [{"slug": "signal", "input_types": 5}]}, "input_types"),
        ({"version": 1, "custom_modules": [{"slug": "a"}]}, "needs name"),
        (
            {"version": 1, "custom_modules": [{"slug": "a", "name": "A", "insight_kind": "a_a", "grants": ["fly"]}]},
            "unknown grant",
        ),
        (
            {"version": 1, "sources": [{"module": "scout", "name": "n", "type": "ftp", "address": "x", "colour": 1}]},
            "type must be",
        ),
        (
            {"version": 1, "sources": [{"module": "scout", "name": "n", "type": "ftp", "address": "x", "colour": 1}]},
            "unknown fields",
        ),
        ({"version": 1, "sources": [{"name": "n"}]}, "needs module"),
    ],
)
def test_invalid_bundles_are_refused(raw: Any, fragment: str) -> None:
    with pytest.raises(ValidationError) as e:
        parse_bundle(raw)
    errors = e.value.details.get("errors", [e.value.message])
    assert any(fragment in err for err in errors), errors


def test_a_profile_less_bundle_leaves_the_profile_alone(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, _, profile = parts
    assert seeder.apply(parse_bundle({"version": 1})).to_public()["profile"] == "unchanged"
    assert profile.get().version == 0


def test_every_bundle_in_the_repository_is_valid() -> None:
    import json
    from pathlib import Path

    seeds = Path(__file__).resolve().parents[3] / "deployment" / "seeds"
    bundles = sorted(seeds.glob("*/bundle.json"))
    assert bundles, "deployment/seeds holds at least the example bundle"
    for path in bundles:
        parse_bundle(json.loads(path.read_text("utf-8")))


def test_a_bundle_added_later_keeps_modules_an_administrator_already_edited(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, modules, _ = parts
    sig = modules.get("signal")
    modules.update("signal", {"interval_minutes": 45}, sig.version)
    out = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert out["modules"]["signal"] == "kept (edited by an administrator)"
    assert modules.get("signal").interval_minutes == 45


def test_what_was_written_is_recorded_when_a_step_fails(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository], monkeypatch: pytest.MonkeyPatch
) -> None:
    seeder, modules, profile = parts
    bundle = parse_bundle(BUNDLE)

    def broken(*a: object, **k: object) -> None:
        raise RuntimeError("table unavailable")

    monkeypatch.setattr(modules, "add_source", broken)
    with pytest.raises(RuntimeError):
        seeder.apply(bundle)
    monkeypatch.undo()
    # The profile the failed run wrote is already the bundle's: the retry leaves it and finishes the rest.
    retried = seeder.apply(parse_bundle({**BUNDLE, "description": "retry"})).to_public()
    assert retried["profile"] == "unchanged" and retried["sources_added"] == 1
    assert profile.get().version == 1


def test_a_profile_conflict_is_reported(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository], monkeypatch: pytest.MonkeyPatch
) -> None:
    from insights_radar.core.errors import ConflictError

    seeder, _, profile = parts

    def conflict(*a: object, **k: object) -> None:
        raise ConflictError("the profile was changed by someone else")

    monkeypatch.setattr(profile, "put", conflict)
    out = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert out["errors"] == ["profile: the profile was changed by someone else"]
    # Reported, and not retried by every deploy (an unchanged retry would only bump versions); the next
    # release of the bundle applies it.
    monkeypatch.undo()
    assert seeder.apply(parse_bundle(BUNDLE)).to_public()["profile"] == "unchanged"
    assert seeder.apply(parse_bundle({**BUNDLE, "description": "v2"})).to_public()["profile"] == "applied"


@pytest.mark.parametrize(
    ("module", "message"),
    [
        ({"slug": "gap", "enabled": "false"}, "modules[0].enabled must be true or false"),
        ({"slug": "signal", "interval_minutes": "60"}, "modules[0].interval_minutes must be a whole number"),
        ({"slug": "signal", "daily_token_budget": True}, "modules[0].daily_token_budget must be a whole number"),
        ({"slug": "signal", "instructions": 5}, "modules[0].instructions must be text"),
    ],
)
def test_module_fields_must_have_their_json_type(module: dict[str, object], message: str) -> None:
    from insights_radar.core.errors import ValidationError

    with pytest.raises(ValidationError) as err:
        parse_bundle({"version": 1, "modules": [module]})
    assert message in err.value.details["errors"]


def test_a_deploy_migration_of_a_built_in_module_is_not_an_administrator_edit(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    """Versions also move when a release migrates a built-in module: the fields are compared instead."""
    seeder, modules, _ = parts
    seeder.apply(parse_bundle(BUNDLE))
    sig = modules.get("signal")
    modules.update("signal", {"description": "a release changed this"}, sig.version)  # not a bundle field
    before = modules.get("signal").version
    out = seeder.apply(parse_bundle({**BUNDLE, "description": "v2"})).to_public()
    # Still the bundle's, and already as it wants: neither "kept" nor rewritten.
    assert out["modules"]["signal"] == "unchanged" and modules.get("signal").version == before


def test_a_new_release_may_set_more_fields_and_deleted_sources_stay_deleted(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, modules, _ = parts
    seeder.apply(parse_bundle(BUNDLE))
    signal = next(m for m in BUNDLE["modules"] if m["slug"] == "signal")
    grown = {
        **BUNDLE,
        "modules": [{**signal, "interval_minutes": 777}] + [m for m in BUNDLE["modules"] if m["slug"] != "signal"],
    }
    out = seeder.apply(parse_bundle(grown)).to_public()
    assert out["modules"]["signal"] == "applied" and modules.get("signal").interval_minutes == 777
    # An administrator deletes the bundle's source; the next release does not bring it back.
    src = BUNDLE["sources"][0]
    sid = next(x.id for x in modules.list_sources(src["module"]) if x.address == src["address"])
    modules.delete_source(src["module"], sid)
    again = seeder.apply(parse_bundle({**grown, "description": "v3"})).to_public()
    assert again["sources_added"] == 0 and not any(
        x.address == src["address"] for x in modules.list_sources(src["module"])
    )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({"enabled": "false"}, "sources[0].enabled must be true or false"),
        ({"instruction": {"x": 1}}, "sources[0].instruction must be text"),
    ],
)
def test_source_fields_must_have_their_json_type(source: dict[str, object], message: str) -> None:
    from insights_radar.core.errors import ValidationError

    base = {"module": "scout", "name": "S", "type": "rss", "address": "https://e.example.com/feed"}
    with pytest.raises(ValidationError) as err:
        parse_bundle({"version": 1, "sources": [{**base, **source}]})
    assert message in err.value.details["errors"]


def test_a_custom_module_takes_new_fields_only_if_the_bundle_created_it(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, modules, _ = parts
    seeder.apply(parse_bundle(BUNDLE))
    watch = BUNDLE["custom_modules"][0]
    grown = {**BUNDLE, "custom_modules": [{**watch, "interval_minutes": 900}], "description": "v2"}
    # A field the bundle never wrote on a module it created is still the bundle's to set.
    assert seeder.apply(parse_bundle(grown)).to_public()["modules"]["watch"] == "applied"
    assert modules.get("watch").interval_minutes == 900


def test_a_new_field_does_not_overwrite_an_admin_edit_on_a_created_module(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, modules, _ = parts
    seeder.apply(parse_bundle(BUNDLE))
    w = modules.get("watch")
    modules.update("watch", {"interval_minutes": 60}, w.version)
    watch = BUNDLE["custom_modules"][0]
    grown = {**BUNDLE, "custom_modules": [{**watch, "interval_minutes": 900}], "description": "v2"}
    assert seeder.apply(parse_bundle(grown)).to_public()["modules"]["watch"] == "kept (edited by an administrator)"
    assert modules.get("watch").interval_minutes == 60


def test_a_source_an_administrator_already_added_is_left_alone(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    from insights_radar.modules.model import SourceType

    seeder, modules, _ = parts
    src = BUNDLE["sources"][0]
    modules.add_source(src["module"], "Mine", SourceType(src["type"]), src["address"], instruction="keep")
    out = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert out["sources_added"] == 0 and out["sources_skipped"] == 1 and out["errors"] == []
    mine = [x for x in modules.list_sources(src["module"]) if x.address == src["address"]]
    assert [x.name for x in mine] == ["Mine"]


def test_a_custom_module_an_administrator_made_first_is_theirs(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    from insights_radar.graph.registry import NodeType
    from insights_radar.modules.model import ModuleDefinition, ModuleTemplate, ToolGrant

    seeder, modules, _ = parts
    mine = ModuleDefinition(
        slug="watch",
        name="My watch",
        template=ModuleTemplate.CUSTOM,
        output_kind=NodeType.INSIGHT,
        grants=frozenset({ToolGrant.KG_READ, ToolGrant.KG_WRITE_OUTPUT}),
        insight_kind="watch_item",
        insight_label="Mine",
    )
    modules.create(mine, "user:a")
    out = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert out["modules"]["watch"] == "kept (edited by an administrator)"
    assert modules.get("watch").insight_label == "Mine"


def test_a_new_release_that_keeps_the_profile_does_not_rewrite_it(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, _, profile = parts
    seeder.apply(parse_bundle(BUNDLE))
    before = profile.get().version
    out = seeder.apply(parse_bundle({**BUNDLE, "description": "a release that changes only modules"})).to_public()
    assert out["profile"] == "unchanged" and profile.get().version == before


def test_a_default_profile_bundle_still_replaces_a_stored_profile_that_fails_validation(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, _, profile = parts
    seeder.apply(parse_bundle(BUNDLE))  # the seed wrote version 1
    # The item it wrote no longer validates (a later release tightened a rule): it reads as the defaults,
    # under version 1, which is still the seed's own version.
    profile._t.update_item(
        Key={"pk": "PROFILE", "sk": "CURRENT"},
        UpdateExpression="SET doc = :d",
        ExpressionAttributeValues={":d": {"app_name": 7}},
    )
    assert profile.get(cached=False).stored_invalid and profile.get(cached=False).version == 1
    out = seeder.apply(parse_bundle({**BUNDLE, "profile": {}})).to_public()
    assert out["profile"] == "applied"
    stored = profile.get(cached=False)
    assert not stored.stored_invalid and stored.version == 2


def test_a_retired_source_is_turned_off_once_and_only_if_the_bundle_added_it(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    from insights_radar.modules.model import SourceType

    seeder, modules, _ = parts
    seeder.apply(parse_bundle(BUNDLE))
    fr = BUNDLE["sources"][0]["address"]
    mine = modules.add_source("scout", "Admin's own", SourceType.RSS, "https://admin.example.com/feed")
    retire = {
        **BUNDLE,
        "description": "v2",
        "retired_sources": [
            {"module": "scout", "address": fr},
            {"module": "scout", "address": mine.address},  # not the bundle's: left alone
            {"module": "nope", "address": "https://x.example.com/"},  # never added either
        ],
    }
    out = seeder.apply(parse_bundle(retire)).to_public()
    assert out["sources_retired"] == 1 and out["errors"] == []
    by_address = {s.address: s for s in modules.list_sources("scout")}
    assert not by_address[fr].enabled and by_address[mine.address].enabled
    # An administrator turns it back on: the next release does not turn it off again.
    modules.update_source("scout", by_address[fr].id, {"enabled": True})
    again = seeder.apply(parse_bundle({**retire, "description": "v3"})).to_public()
    assert again["sources_retired"] == 0 and {s.address: s for s in modules.list_sources("scout")}[fr].enabled


def test_a_retired_source_that_is_already_off_or_whose_module_is_gone_is_skipped(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, modules, _ = parts
    off = {"module": "scout", "name": "Off", "type": "rss", "address": "https://off.example.com/feed", "enabled": False}
    gone = {"module": "watch", "name": "W", "type": "rss", "address": "https://w.example.com/feed"}
    seeder.apply(parse_bundle({**BUNDLE, "sources": [*BUNDLE["sources"], off, gone]}))
    modules.delete("watch")
    retired = [{"module": s["module"], "address": s["address"]} for s in (off, gone)]
    out = seeder.apply(
        parse_bundle({**BUNDLE, "description": "v2", "custom_modules": [], "retired_sources": retired})
    ).to_public()
    assert out["sources_retired"] == 0 and out["errors"] == []


@pytest.mark.parametrize(
    "bad",
    [[{"module": "scout"}], [{"module": "scout", "address": 3}], [{"module": "scout", "address": "a", "x": 1}], "no"],
)
def test_retired_sources_are_validated(bad: Any) -> None:
    with pytest.raises(ValidationError):
        parse_bundle({**BUNDLE, "retired_sources": bad})


def test_an_admin_edit_the_bundle_now_agrees_with_does_not_hold_back_its_other_fields(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, modules, _ = parts
    seeder.apply(parse_bundle(BUNDLE))
    sig = modules.get("signal")
    modules.update("signal", {"interval_minutes": 60}, sig.version)  # by hand
    signal = {**BUNDLE["modules"][0], "interval_minutes": 60, "daily_token_budget": 900_000}
    out = seeder.apply(parse_bundle({**BUNDLE, "description": "v2", "modules": [signal, BUNDLE["modules"][1]]}))
    assert out.to_public()["modules"]["signal"] == "applied"
    assert modules.get("signal").daily_token_budget == 900_000 and modules.get("signal").interval_minutes == 60
    # A disagreement still keeps the administrator's module as it is.
    modules.update("signal", {"interval_minutes": 30}, modules.get("signal").version)
    signal = {**signal, "daily_token_budget": 800_000}
    out = seeder.apply(parse_bundle({**BUNDLE, "description": "v3", "modules": [signal, BUNDLE["modules"][1]]}))
    assert out.to_public()["modules"]["signal"] == "kept (edited by an administrator)"


def test_a_source_an_administrator_already_had_is_never_retired(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    from insights_radar.modules.model import SourceType

    seeder, modules, _ = parts
    mine = modules.add_source("scout", "Mine", SourceType.RSS, "https://agency.example.com/rss")
    listed = {"module": "scout", "name": "Agency", "type": "rss", "address": mine.address}
    first = seeder.apply(parse_bundle({**BUNDLE, "sources": [*BUNDLE["sources"], listed]})).to_public()
    assert first["sources_skipped"] == 1
    retire = {"module": "scout", "address": mine.address}
    out = seeder.apply(
        parse_bundle(
            {**BUNDLE, "description": "v2", "sources": [*BUNDLE["sources"], listed], "retired_sources": [retire]}
        )
    )
    assert out.to_public()["sources_retired"] == 0 and modules.get_source("scout", mine.id).enabled


def test_a_retirement_that_failed_is_tried_again(parts: tuple[Seeder, ModuleRepository, ProfileRepository]) -> None:
    from insights_radar.core.errors import ConflictError

    seeder, modules, _ = parts
    seeder.apply(parse_bundle(BUNDLE))
    retire = {
        **BUNDLE,
        "description": "v2",
        "retired_sources": [{"module": "scout", "address": BUNDLE["sources"][0]["address"]}],
    }
    real = modules.disable_source

    def busy(*_: Any, **__: Any) -> Any:
        raise ConflictError("busy")

    modules.disable_source = busy  # type: ignore[method-assign]
    failed = seeder.apply(parse_bundle(retire)).to_public()  # reported, and the rest of the seed goes on
    assert failed["sources_retired"] == 0 and "busy" in failed["errors"][0]
    modules.disable_source = real  # type: ignore[method-assign]
    assert seeder.apply(parse_bundle({**retire, "description": "v3"})).to_public()["sources_retired"] == 1


def test_a_source_stored_under_older_rules_can_still_be_retired(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository], ops_table: Any
) -> None:
    import pytest as _pytest

    from insights_radar.core.errors import NotFoundError

    seeder, modules, _ = parts
    seeder.apply(parse_bundle(BUNDLE))
    (src,) = modules.list_sources("scout")
    # A stored field the validator now refuses (as if a rule had tightened since it was saved).
    ops_table.update_item(
        Key={"pk": "MODULE#scout", "sk": f"SRC#{src.id}"},
        UpdateExpression="SET #n = :n",
        ExpressionAttributeNames={"#n": "name"},
        ExpressionAttributeValues={":n": "x" * 500},
    )
    retire = {**BUNDLE, "description": "v2", "retired_sources": [{"module": "scout", "address": src.address}]}
    assert seeder.apply(parse_bundle(retire)).to_public()["sources_retired"] == 1
    assert not modules.get_source("scout", src.id).enabled
    with _pytest.raises(NotFoundError):
        modules.disable_source("scout", "nope000000")


def test_disabling_a_source_raises_other_table_errors(clock: FixedClock) -> None:
    from botocore.exceptions import ClientError

    class Table:
        def update_item(self, **_: Any) -> None:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")

    with pytest.raises(ClientError):
        ModuleRepository(Table(), clock).disable_source("scout", "s1")


def test_kept_unknown_fields_do_not_make_the_seed_rewrite(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    """Code review r19: the comparison included kept unknown fields, so every changed bundle bumped the profile."""
    seeder, _, profile = parts
    seeder.apply(parse_bundle(BUNDLE))
    t = profile._t
    item = t.get_item(Key={"pk": "PROFILE", "sk": "CURRENT"})["Item"]
    t.put_item(Item={**item, "doc": {**item["doc"], "footer_md": "from a newer release"}})
    before = profile.get().version
    extra = {"module": "scout", "name": "Extra", "type": "rss", "address": "https://x.example.com/feed"}
    changed = parse_bundle({**BUNDLE, "sources": [*BUNDLE["sources"], extra]})
    assert seeder.apply(changed).to_public()["profile"] == "unchanged"
    assert profile.get().version == before


def test_a_throttled_profile_save_does_not_stop_the_rest_of_the_seed(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    """Code review r21: put() re-raises a failed read; the seed caught only ConflictError, so nothing else applied."""
    from unittest.mock import patch

    from botocore.exceptions import ClientError

    seeder, modules, profile = parts
    err = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "GetItem")
    with patch.object(profile, "put", side_effect=err):
        result = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert result["errors"] == ["profile: ProvisionedThroughputExceededException"]
    assert modules.get("watch") is not None and result["sources_added"] >= 1


def test_a_dropped_connection_on_the_profile_save_does_not_stop_the_seed(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    from unittest.mock import patch

    from botocore.exceptions import ReadTimeoutError

    seeder, _, profile = parts
    with patch.object(profile, "put", side_effect=ReadTimeoutError(endpoint_url="https://ddb")):
        result = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert result["errors"] == ["profile: ReadTimeoutError"] and result["sources_added"] >= 1


def test_a_passing_profile_failure_leaves_the_bundle_to_be_tried_again(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    """Code review r23: the error was recorded but the digest stored, so the profile was never tried again."""
    from unittest.mock import patch

    from botocore.exceptions import ReadTimeoutError

    seeder, _, profile = parts
    with patch.object(profile, "put", side_effect=ReadTimeoutError(endpoint_url="https://ddb")):
        first = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert first["errors"] == ["profile: ReadTimeoutError"]
    again = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert again["profile"] == "applied" and profile.get().app_name == BUNDLE["profile"]["app_name"]


def test_a_save_whose_reply_was_lost_still_counts_as_the_seeds(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    """Code review r23: the write landed but the seed recorded no version, so it read its own write as an admin's."""
    seeder, _, profile = parts
    profile.put(BUNDLE["profile"], "system:seed", None)  # landed; the seed never heard back
    changed = parse_bundle({**BUNDLE, "profile": {**BUNDLE["profile"], "tagline": "New tagline"}})
    assert seeder.apply(changed).to_public()["profile"] == "applied"
    assert profile.get().tagline == "New tagline"


def test_an_admin_profile_with_an_unreadable_version_is_kept(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    seeder, _, profile = parts
    item = {"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": "Admin name"}, "version": "7", "updated_by": "user:a"}
    profile._t.put_item(Item=item)
    assert seeder.apply(parse_bundle(BUNDLE)).to_public()["profile"] == "kept (edited by an administrator)"
    assert profile.get().app_name == "Admin name"


def test_a_failed_ownership_read_is_retried_and_never_overwrites(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    """Code review r24: get() returned the defaults on a failed read, which looked like "never written"."""
    from unittest.mock import patch

    from botocore.exceptions import ClientError

    seeder, _, profile = parts
    item = {"pk": "PROFILE", "sk": "CURRENT", "doc": {"app_name": "Admin name"}, "version": "7", "updated_by": "user:a"}
    profile._t.put_item(Item=item)
    err = ClientError({"Error": {"Code": "ThrottlingException"}}, "GetItem")
    with patch.object(profile, "get_for_write", side_effect=err):
        first = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert first["errors"] == ["profile: ThrottlingException"] and profile.get().app_name == "Admin name"
    # Not marked applied, so the next deploy decides again, on a real read, and keeps the admin's profile.
    assert seeder.apply(parse_bundle(BUNDLE)).to_public()["profile"] == "kept (edited by an administrator)"


def test_a_permanent_profile_error_is_applied_once_its_cause_is_fixed(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    """Code review r27: a permanent save failure stored the digest, so after the fix the same release reported the
    profile "unchanged" and never applied it. Any failure now leaves the bundle to be tried again."""
    from unittest.mock import patch

    from botocore.exceptions import ClientError

    seeder, _, profile = parts
    err = ClientError({"Error": {"Code": "AccessDeniedException"}}, "PutItem")
    with patch.object(profile, "put", side_effect=err):
        first = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert first["errors"] == ["profile: AccessDeniedException"]
    assert seeder.apply(parse_bundle(BUNDLE)).to_public()["profile"] == "applied"  # the cause was fixed


def test_a_failure_of_the_read_inside_put_is_tried_again(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    """Code review r27: put() reads first; a permanent failure of that read marked a profile it never read applied."""
    from unittest.mock import patch

    from botocore.exceptions import ClientError

    seeder, _, profile = parts
    real = profile._t.get_item
    reads = {"n": 0}

    def profile_reads(**kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("Key") == {"pk": "PROFILE", "sk": "CURRENT"}:
            reads["n"] += 1
            if reads["n"] == 2:  # get_for_write() is the first, put()'s own read the second
                raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetItem")
        return real(**kwargs)

    with patch.object(profile._t, "get_item", side_effect=profile_reads):
        first = seeder.apply(parse_bundle(BUNDLE)).to_public()
    assert first["errors"] == ["profile: AccessDeniedException"]
    assert seeder.apply(parse_bundle(BUNDLE)).to_public()["profile"] == "applied"


def test_a_failed_read_and_a_codeless_5xx_are_tried_again(
    parts: tuple[Seeder, ModuleRepository, ProfileRepository],
) -> None:
    """Code review r25: a permanent read error and a 5xx without a code were marked applied and never retried."""
    from unittest.mock import patch

    from botocore.exceptions import ClientError

    seeder, _, profile = parts
    denied = ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetItem")
    with patch.object(profile, "get_for_write", side_effect=denied):
        assert seeder.apply(parse_bundle(BUNDLE)).to_public()["errors"] == ["profile: AccessDeniedException"]
    bare = ClientError({"Error": {}, "ResponseMetadata": {"HTTPStatusCode": 503}}, "PutItem")
    with patch.object(profile, "put", side_effect=bare):
        assert seeder.apply(parse_bundle(BUNDLE)).to_public()["errors"] == ["profile: error"]
    assert seeder.apply(parse_bundle(BUNDLE)).to_public()["profile"] == "applied"
