# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError
from hypothesis import given, settings
from hypothesis import strategies as st

from insights_radar.core.clock import FixedClock, iso
from insights_radar.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
)
from insights_radar.identity.principal import Principal, PrincipalKind, Role
from insights_radar.radars.codecs import EntryCodec, RadarDiff
from insights_radar.radars.model import (
    ENTRY_KEY_RE,
    MAX_ENTRIES,
    TEMPLATES,
    Dimension,
    EntryValidator,
    RadarEntry,
    RadarSchema,
)
from insights_radar.radars.service import MemoryBlobStore, RadarService

ADMIN = Principal(PrincipalKind.USER, "adm", Role.ADMIN, "admin@example.com")
OWNER = Principal(PrincipalKind.USER, "own", Role.EDITOR, "owner@example.com")
EDIT = Principal(PrincipalKind.USER, "ed", Role.EDITOR, "Editor@Example.com")
OTHER = Principal(PrincipalKind.USER, "oth", Role.EDITOR, "other@example.com")
VIEW = Principal(PrincipalKind.USER, "vw", Role.VIEWER, "viewer@example.com")


@pytest.fixture
def svc(ops_table: Any, clock: FixedClock) -> RadarService:
    return RadarService(ops_table, MemoryBlobStore(), clock)


def entry(title: str, sector: str = "Direct", ring: str = "Leader", **kw: Any) -> RadarEntry:
    return RadarEntry(key=kw.pop("key", f"k-{title}"), title=title, sector=sector, ring=ring, **kw)


@pytest.fixture
def radar(svc: RadarService) -> Any:
    return svc.create(OWNER, "desk-competitors", "Desk competitors", template="competitor_landscape")


# ---- schema --------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(TEMPLATES))
def test_templates_valid(name: str) -> None:
    RadarSchema.validate(TEMPLATES[name][1])


@pytest.mark.parametrize(
    ("sectors", "rings", "markers", "msg"),
    [
        ([], ["a"], [], "sectors must have"),
        (["s"] * 1, [], [], "rings must have"),
        ([f"s{i}" for i in range(9)], ["r"], [], "sectors must have"),
        (["s"], [f"r{i}" for i in range(7)], [], "rings must have"),
        (["s"], ["r"], [f"m{i}" for i in range(7)], "markers must have"),
        (["s", "S "], ["r"], [], "duplicate sectors"),
        (["x" * 41], ["r"], [], "1-40"),
        ([""], ["r"], [], "1-40"),
    ],
)
def test_schema_validation(sectors: list[str], rings: list[str], markers: list[str], msg: str) -> None:
    raw = {"sectors": sectors, "rings": rings, "markers": markers}
    with pytest.raises(ValidationError) as exc:
        RadarSchema.from_public(raw)
    assert any(msg in e for e in exc.value.details["errors"])


def test_schema_colors_glyphs_labels() -> None:
    s = RadarSchema.from_public(
        {
            "sectors": [{"name": "A", "color": "#112233"}],
            "rings": ["R"],
            "markers": [{"name": "Hot", "glyph": "star"}],
            "sector_label": "Market",
        }
    )
    assert s.sectors[0].color == "#112233" and s.markers[0].glyph == "star" and s.sector_label == "Market"
    for bad in (
        {"sectors": [{"name": "A", "color": "red"}], "rings": ["R"]},
        {"sectors": ["A"], "rings": ["R"], "markers": [{"name": "m", "glyph": "heart"}]},
        {"sectors": ["A"], "rings": ["R"], "ring_label": "x" * 31},
    ):
        with pytest.raises(ValidationError):
            RadarSchema.from_public(bad)
    with pytest.raises(ValidationError):
        RadarSchema.from_public([])
    with pytest.raises(ValidationError):
        RadarSchema.from_public({"sectors": "A", "rings": ["R"]})
    with pytest.raises(ValidationError):
        Dimension.from_public({"color": "#000000"})
    assert RadarSchema.from_public(s.to_public()) == s


def test_tolerant_match_and_suggest() -> None:
    s = TEMPLATES["competitor_landscape"][1]
    assert s.match("sector", "  direct ") == "Direct" and s.match("ring", "LEADER") == "Leader"
    assert s.match("ring", "Lead") is None and s.match("ring", None) is None and s.match("marker", "") is None
    assert s.suggest("ring", "Leeder") == "Leader" and s.suggest("ring", "zzzzzzz") is None


# ---- entry validation --------------------------------------------------------------------


def test_entry_validation() -> None:
    s = TEMPLATES["competitor_landscape"][1]
    clean, unmatched, dup = EntryValidator.check(
        s,
        [
            entry("A", "direct", "leader", marker="gaining", description="<p>ok<script>x</script></p>"),
            entry("B", "Drect", "Leader", marker="Xyzzy"),
            entry("a", "Direct", "Leader"),
        ],
    )
    assert clean[0].sector == "Direct" and clean[0].marker == "Gaining" and clean[0].description == "<p>ok</p>"
    assert [(u.row, u.field, u.suggestion) for u in unmatched] == [(2, "sector", "Direct"), (2, "marker", None)]
    assert dup == [3]
    with pytest.raises(ValidationError, match="title"):
        EntryValidator.check(s, [entry("")])
    with pytest.raises(ValidationError, match="link"):
        EntryValidator.check(s, [entry("A", link="http://x.com")])
    with pytest.raises(ValidationError, match="at most"):
        EntryValidator.check(s, [entry(f"e{i}") for i in range(MAX_ENTRIES + 1)])
    with pytest.raises(ValidationError, match="description"):
        EntryValidator.check(s, [entry("A", description="x" * 4001)])


# ---- codecs ------------------------------------------------------------------------------


def _keys() -> Any:
    counter = iter(range(10_000))
    return lambda: f"gen-{next(counter)}"


def test_csv_aliases_and_formula_guard() -> None:
    text = "\ufeff" + 'Name,Quadrant,Ring,Status,Description\nLinak,Direct,Leader,Gaining,=HYPERLINK("x")\n'
    parsed = EntryCodec.parse_csv(text, _keys())
    assert parsed[0].title == "Linak" and parsed[0].sector == "Direct" and parsed[0].marker == "Gaining"
    out = EntryCodec.to_csv(parsed)
    assert "'=HYPERLINK" in out
    assert EntryCodec.parse_csv(out, _keys())[0].description == '=HYPERLINK("x")'


@pytest.mark.parametrize("value", ["'=", "''=1", "'+x", "'hello", "'", "=1", "-", "it's"])
def test_quotes_in_front_of_a_formula_survive_the_round_trip(value: str) -> None:
    entry = RadarEntry(key="k", title="t", sector="Direct", ring="Leader", description=value)
    out = EntryCodec.to_csv([entry])
    assert "\n=" not in out and ",=" not in out and ",+" not in out  # never a live formula
    assert EntryCodec.parse_csv(out, _keys())[0].description == value


@pytest.mark.parametrize("text", ["", "foo,bar\n1,2\n", "title\n\n,\n"])
def test_csv_rejects(text: str) -> None:
    with pytest.raises(ValidationError):
        EntryCodec.parse_csv(text, _keys())


@pytest.mark.parametrize("text", ["{", "[1]", '{"entries": 5}', '"x"', '[{"sector": "a"}]'])
def test_json_rejects(text: str) -> None:
    with pytest.raises(ValidationError):
        EntryCodec.parse_json(text, _keys())


def test_size_cap() -> None:
    with pytest.raises(PayloadTooLargeError):
        EntryCodec.parse_json("[" + " " * (5 * 1024 * 1024) + "]", _keys())


_TXT = (
    st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=30)
    .map(str.strip)
    .filter(bool)
)


@settings(max_examples=150)
@given(st.lists(st.tuples(_TXT, _TXT, _TXT, st.one_of(st.none(), _TXT), st.text(max_size=40)), max_size=8))
def test_codec_roundtrip(rows: list[tuple[str, str, str, str | None, str]]) -> None:
    entries = [
        RadarEntry(key=f"k{i}", title=t, sector=s, ring=r, marker=m, description=d)
        for i, (t, s, r, m, d) in enumerate(rows)
    ]
    assert EntryCodec.parse_json(EntryCodec.to_json(entries), _keys()) == entries
    via_csv = EntryCodec.parse_csv(EntryCodec.to_csv(entries), _keys())
    assert [e.to_public() for e in via_csv] == [e.to_public() for e in entries]


def test_diff() -> None:
    a = [entry("A", key="1"), entry("B", key="2"), entry("C", key="3", marker="Gaining")]
    b = [
        entry("A", key="1", ring="Niche"),
        entry("B2", key="2", sector="Adjacent"),
        entry("C", key="3"),
        entry("D", key="4"),
    ]
    d = RadarDiff.compare(a, b)
    assert [x["title"] for x in d["added"]] == ["D"] and d["removed"] == []
    assert d["moved"][0]["to"] == "Niche" and d["recategorised"][0]["to"] == "Adjacent"
    assert d["marker_changed"][0]["from"] == "Gaining" and d["edited"][0]["previous_title"] == "B"
    assert RadarDiff.compare(a, a) == {k: [] for k in d}
    assert [x["title"] for x in RadarDiff.compare(b, a)["removed"]] == ["D"]


# ---- service: create/acl ----------------------------------------------------------------------


def test_create_and_permissions(svc: RadarService, radar: Any) -> None:
    assert radar.current_version == 1 and radar.owner == "own" and radar.purpose.startswith("Who competes")
    with pytest.raises(ForbiddenError):
        svc.create(VIEW, "v-radar", "V")
    with pytest.raises(ConflictError):
        svc.create(OWNER, "desk-competitors", "Dup")
    with pytest.raises(ValidationError):
        svc.create(OWNER, "radars", "Reserved")
    with pytest.raises(ValidationError):
        svc.create(OWNER, "x", "X", template="nope")
    with pytest.raises(ValidationError):
        svc.create(OWNER, "x", "X", template="blank", schema=TEMPLATES["blank"][1])
    custom = svc.create(
        ADMIN,
        "segments",
        "Segments",
        schema=RadarSchema.from_public({"sectors": ["B2B", "B2C"], "rings": ["Now", "Later"]}),
    )
    assert custom.schema.sectors[0].name == "B2B"
    assert {r.slug for r in svc.list_all()} == {"desk-competitors", "segments"}
    assert svc.can_edit(ADMIN, radar) and svc.can_edit(OWNER, radar) and not svc.can_edit(EDIT, radar)
    updated = svc.set_access(OWNER, radar.slug, ["editor@example.com"], radar.version_lock)
    assert svc.can_edit(EDIT, updated) and not svc.can_admin(EDIT, updated) and not svc.can_edit(VIEW, updated)
    with pytest.raises(ForbiddenError):
        svc.set_access(EDIT, radar.slug, [], updated.version_lock)
    with pytest.raises(ValidationError):
        svc.set_access(OWNER, radar.slug, ["not-an-email"], updated.version_lock)
    with pytest.raises(ConflictError):
        svc.set_access(OWNER, radar.slug, [], radar.version_lock)
    moved = svc.set_access(ADMIN, radar.slug, [], updated.version_lock, new_owner=("oth", "Other@example.com"))
    assert moved.owner == "oth" and moved.owner_email == "other@example.com" and svc.can_admin(OTHER, moved)


def test_meta_update(svc: RadarService, radar: Any) -> None:
    r = svc.update_meta(OWNER, radar.slug, {"mode": "copilot", "name": " Rivals "}, radar.version_lock)
    assert r.mode.value == "copilot" and r.name == "Rivals"
    with pytest.raises(ValidationError):
        svc.update_meta(OWNER, radar.slug, {"mode": "turbo"}, r.version_lock)
    with pytest.raises(ValidationError):
        svc.update_meta(OWNER, radar.slug, {"owner": "x"}, r.version_lock)
    with pytest.raises(ValidationError):
        svc.update_meta(OWNER, radar.slug, {"auto_apply_days": 0}, r.version_lock)
    with pytest.raises(ConflictError):
        svc.update_meta(OWNER, radar.slug, {"name": "x"}, radar.version_lock)
    with pytest.raises(ForbiddenError):
        svc.update_meta(EDIT, radar.slug, {"name": "x"}, r.version_lock)


# ---- service: versions -----------------------------------------------------------------------


def test_save_versions_rollback_diff(svc: RadarService, radar: Any) -> None:
    r2 = svc.save(OWNER, radar.slug, [entry("Linak"), entry("Ergotron", "Adjacent", "Challenger")], 1)
    assert r2.current_version == 2 and r2.entry_count == 2
    with pytest.raises(ConflictError):
        svc.save(OWNER, radar.slug, [], 1)
    with pytest.raises(ConflictError) as dup:
        svc.save(OWNER, radar.slug, [entry("A", key="1"), entry("a", key="2")], 2)
    assert dup.value.details["code"] == "duplicate_title"
    with pytest.raises(ValidationError) as um:
        svc.save(OWNER, radar.slug, [entry("A", "Diret")], 2)
    assert um.value.details["unmatched"][0]["suggestion"] == "Direct"
    with pytest.raises(ValidationError, match="keys"):
        svc.save(OWNER, radar.slug, [entry("A", key="1"), entry("B", key="1")], 2)
    r3 = svc.save(OWNER, radar.slug, [entry("Linak", ring="Niche")], 2, note="demote")
    assert [v["version"] for v in svc.versions_page(radar.slug)[0]] == [3, 2, 1]
    assert svc.entries(radar.slug, 2)[1].title == "Ergotron"
    d = svc.diff(radar.slug, 2, 3)["changes"]
    assert d["moved"][0]["to"] == "Niche" and d["removed"][0]["title"] == "Ergotron"
    r4 = svc.rollback(OWNER, radar.slug, 2, r3.current_version)
    assert r4.current_version == 4 and len(svc.entries(radar.slug)) == 2
    with pytest.raises(NotFoundError):
        svc.entries(radar.slug, 99)
    with pytest.raises(ForbiddenError):
        svc.save(VIEW, radar.slug, [], 4)


def test_import_validate_export(svc: RadarService, radar: Any) -> None:
    csv_text = "title,sector,ring,marker\nLinak,Direct,Leader,Gaining\nFlexiSpot,Direct,Challenger,\n"
    report = svc.validate_import(OWNER, radar.slug, csv_text, "csv")
    assert report == {
        "rows": 2,
        "unmatched": [],
        "duplicate_rows": [],
        "duplicate_keys": [],
        "accepted": True,
        "empty": False,
        "before": 0,
        "removed": 0,
    }
    r = svc.import_entries(OWNER, radar.slug, csv_text, "csv", 1)
    keys = {e.title: e.key for e in svc.entries(radar.slug)}
    j = json.dumps(
        [
            {"title": "Linak", "sector": "Direct", "ring": "Niche"},
            {"title": "IKEA", "sector": "Substitutes", "ring": "Watch"},
        ]
    )
    r2 = svc.import_entries(OWNER, radar.slug, j, "json", r.current_version, mode="merge")
    after = {e.title: e for e in svc.entries(radar.slug)}
    assert set(after) == {"Linak", "FlexiSpot", "IKEA"} and after["Linak"].key == keys["Linak"]
    assert after["Linak"].ring == "Niche"
    r3 = svc.import_entries(OWNER, radar.slug, j, "json", r2.current_version)
    assert {e.title for e in svc.entries(radar.slug)} == {"Linak", "IKEA"}
    bad = "title,sector,ring\nX,Nowhere,Leader\n"
    assert not svc.validate_import(OWNER, radar.slug, bad, "csv")["accepted"]
    r4 = svc.import_entries(OWNER, radar.slug, bad, "csv", r3.current_version, allow_unmatched=True)
    assert r4.entry_count == 1
    with pytest.raises(ValidationError):
        svc.import_entries(OWNER, radar.slug, bad, "csv", r4.current_version, mode="upsert")
    with pytest.raises(ValidationError):
        svc.validate_import(OWNER, radar.slug, bad, "xml")
    assert svc.export(radar.slug, "json", 2).count('"title"') == 2
    assert svc.export(radar.slug, "csv").startswith("key,title,sector")
    with pytest.raises(ValidationError):
        svc.export(radar.slug, "yaml")
    tpl = svc.template_csv(radar.slug)
    assert tpl.count("\n") == 5 and "Example 4" in tpl


def test_schema_change_with_renames(svc: RadarService, radar: Any) -> None:
    svc.save(OWNER, radar.slug, [entry("Linak"), entry("Uplift", "Emerging", "Watch")], 1)
    new = RadarSchema.from_public(
        {"sectors": ["Direct rivals", "Adjacent", "Emerging"], "rings": ["Leader", "Challenger", "Niche", "Watch"]}
    )
    with pytest.raises(ValidationError, match="no longer match"):
        svc.update_schema(OWNER, radar.slug, new, 2)
    r = svc.update_schema(OWNER, radar.slug, new, 2, renames={"sector": {"Direct": "Direct rivals"}})
    assert r.current_version == 3 and svc.entries(radar.slug)[0].sector == "Direct rivals"
    smaller = RadarSchema.from_public({"sectors": ["Direct rivals"], "rings": ["Leader"]})
    r2 = svc.update_schema(OWNER, radar.slug, smaller, 3, drop_unmatched=True)
    assert r2.entry_count == 1 and svc.versions_page(radar.slug)[0][0]["note"].startswith("schema changed (1")
    with pytest.raises(ValidationError, match="different schema"):
        svc.rollback(OWNER, radar.slug, 2, r2.current_version)
    with pytest.raises(ValidationError):
        svc.update_schema(OWNER, radar.slug, smaller, 4, renames={"phase": {}})
    with pytest.raises(ForbiddenError):
        svc.update_schema(EDIT, radar.slug, smaller, 4)


def test_delete(svc: RadarService, radar: Any) -> None:
    svc.save(OWNER, radar.slug, [entry("Linak")], 1)
    with pytest.raises(ForbiddenError):
        svc.delete(EDIT, radar.slug)
    svc.delete(OWNER, radar.slug)
    with pytest.raises(NotFoundError):
        svc.get(radar.slug)
    # only the archived final version remains
    assert [k.split("/")[:3] for k in svc._blobs.objects] == [["deleted", "radars", radar.slug]]  # type: ignore[attr-defined]


# ---- suggestions + autopilot ------------------------------------------------------------------


def test_suggestions_lifecycle(svc: RadarService, radar: Any) -> None:
    svc.save(OWNER, radar.slug, [entry("Linak", key="lk")], 1)
    with pytest.raises(ForbiddenError, match="manual"):
        svc.propose(radar.slug, "module:curator", "r1", {"ring": "Niche"}, "Three reviews cite slower motors", "lk")
    svc.update_meta(OWNER, radar.slug, {"mode": "copilot"}, svc.get(radar.slug).version_lock)
    s = svc.propose(
        radar.slug, "module:curator", "r1", {"ring": "Challenger"}, "Lost share per two analyst notes", "lk"
    )
    assert s["base"]["ring"] == "Leader" and s["proposal"]["ring"] == "Challenger" and not s["is_new"]
    new = svc.propose(
        radar.slug,
        "module:curator",
        "r1",
        {"title": "Desky", "sector": "emerging", "ring": "watch"},
        "New entrant covered by three outlets",
    )
    assert new["is_new"] and new["proposal"]["sector"] == "Emerging"
    assert len(svc.suggestions(radar.slug)) == 2
    for bad_kw in (
        {"proposal": {"ring": "Nope"}, "entry_key": "lk"},
        {"proposal": {"ring": "Leader"}, "entry_key": "lk"},
        {"proposal": {"title": "Linak", "sector": "Direct", "ring": "Niche"}},
        {"proposal": {"title": "X"}},
        {"proposal": {"ring": "Niche"}, "entry_key": "missing"},
    ):
        with pytest.raises((ValidationError, ConflictError, NotFoundError)):
            svc.propose(
                radar.slug, "module:curator", "r1", bad_kw["proposal"], "reason long enough", bad_kw.get("entry_key")
            )  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="reason"):
        svc.propose(radar.slug, "m", "r", {"ring": "Niche"}, "short", "lk")
    r, _ = svc.accept(OWNER, radar.slug, "lk", edits={"description": "Edited by human"})
    assert svc.entries(radar.slug)[0].ring == "Challenger" and svc.entries(radar.slug)[0].description
    assert r.current_version == 3
    svc.discard(OWNER, radar.slug, new["entry_key"])
    assert svc.suggestions(radar.slug) == []
    with pytest.raises(NotFoundError):
        svc.discard(OWNER, radar.slug, new["entry_key"])
    with pytest.raises(ForbiddenError):
        svc.accept(VIEW, radar.slug, "lk")


def test_autopilot(svc: RadarService, radar: Any, clock: FixedClock) -> None:
    svc.save(OWNER, radar.slug, [entry("Linak", key="lk"), entry("Uplift", key="up")], 1)
    meta = {"mode": "autopilot", "auto_apply_days": 3, "auto_apply_agent": True}
    svc.update_meta(ADMIN, radar.slug, meta, svc.get(radar.slug).version_lock)
    ev = ["fact/1"]
    svc.propose(radar.slug, "module:curator", "r1", {"ring": "Niche"}, "Evidence from four reviews", "lk", ev)
    svc.propose(radar.slug, "module:curator", "r1", {"ring": "Watch"}, "Evidence from two analyst notes", "up", ev)
    svc.propose(
        radar.slug,
        "module:curator",
        "r1",
        {"title": "Desky", "sector": "Emerging", "ring": "Watch"},
        "New entrant in three outlets",
        evidence=ev,
    )
    assert svc.autopilot_tick() == {"applied": 0, "superseded": 0, "held": 0, "errors": 0}
    current = svc.entries(radar.slug)
    edited = [e if e.key != "up" else RadarEntry(**{**e.to_public(), "description": "human edit"}) for e in current]
    svc.save(OWNER, radar.slug, edited, svc.get(radar.slug).current_version)
    clock.advance(timedelta(days=4))
    assert svc.autopilot_tick() == {"applied": 2, "superseded": 1, "held": 0, "errors": 0}
    final = {e.title: e for e in svc.entries(radar.slug)}
    assert final["Linak"].ring == "Niche" and final["Uplift"].ring == "Leader" and "Desky" in final
    assert svc.suggestions(radar.slug) == []


def test_a_save_that_loses_a_race_leaves_the_winners_version_intact(svc: RadarService, radar: Any) -> None:
    stale = svc.get(radar.slug)  # both editors loaded the same version
    winner = [entry("Winner")]
    svc._write_version(stale, winner, "user:a", "first")
    with pytest.raises(ConflictError, match="changed by someone else"):
        svc._write_version(stale, [entry("Loser")], "user:b", "second")
    assert [e.title for e in svc.entries(radar.slug)] == ["Winner"]


def test_a_rename_racing_a_save_cannot_rewind_or_wedge_the_radar(svc: RadarService, radar: Any) -> None:
    before = svc.get(radar.slug)  # the rename and the save both read this
    saved = svc._write_version(before, [entry("Linak")], "user:a", "save")
    real_get = svc.get
    svc.get = lambda slug: before  # type: ignore[method-assign]  # the rename read before the save landed
    with pytest.raises(ConflictError):
        svc.update_meta(OWNER, radar.slug, {"name": "Renamed"}, before.version_lock)  # passes its own lock check
    svc.get = real_get  # type: ignore[method-assign]
    after = svc.get(radar.slug)
    assert after.current_version == saved.current_version and after.name == before.name
    # The other order: settings change first, then the stale save loses and releases its version number.
    renamed = svc.update_meta(OWNER, radar.slug, {"name": "Renamed"}, after.version_lock)
    with pytest.raises(ConflictError):
        svc._write_version(after, [entry("Stale")], "user:b", "late save")
    assert svc.get(radar.slug).name == "Renamed"
    fresh = svc.get(radar.slug)
    svc._write_version(fresh, [entry("Next")], "user:b", "retry works")  # not wedged on an orphan version
    assert svc.get(radar.slug).current_version == fresh.current_version + 1
    assert renamed.version_lock == fresh.version_lock


def test_proposals_are_checked_on_their_own(svc: RadarService, radar: Any) -> None:
    # An existing entry left outside the schema does not block an unrelated proposal.
    stale = [entry("Linak", key="lk"), entry("Gone", "Retired", "Leader", key="g"), entry("Twin", key="t1")]
    current = svc.get(radar.slug)
    svc._save(current, stale, current.current_version, "user:o", "legacy", True)
    svc.update_meta(OWNER, radar.slug, {"mode": "copilot"}, svc.get(radar.slug).version_lock)
    s = svc.propose(radar.slug, "module:curator", "r1", {"ring": "Niche"}, "Three reviews cite slower motors", "lk")
    assert s["proposal"]["ring"] == "Niche"
    with pytest.raises(ConflictError, match="title"):
        svc.propose(radar.slug, "m", "r", {"title": "TWIN", "sector": "Direct", "ring": "Niche"}, "reason long enough")


@pytest.mark.parametrize(
    "proposal",
    [{"ring": 3}, {"title": ["x"]}, {"ring": "Niche", "link": 5}, {"ring": "Niche", "description": {"a": 1}}],
)
def test_proposal_and_edit_field_types(svc: RadarService, radar: Any, proposal: dict[str, Any]) -> None:
    svc.save(OWNER, radar.slug, [entry("Linak", key="lk")], 1)
    svc.update_meta(OWNER, radar.slug, {"mode": "copilot"}, svc.get(radar.slug).version_lock)
    with pytest.raises(ValidationError, match="must be a string"):
        svc.propose(radar.slug, "m", "r", proposal, "reason long enough", "lk")
    svc.propose(radar.slug, "m", "r", {"ring": "Niche"}, "reason long enough", "lk")
    with pytest.raises(ValidationError, match="must be a string"):
        svc.accept(OWNER, radar.slug, "lk", edits=proposal)


@pytest.mark.parametrize(
    "changes",
    [{"name": 5}, {"purpose": None}, {"description": []}, {"auto_apply_days": "7"}, {"auto_apply_days": True}],
)
def test_meta_field_types(svc: RadarService, radar: Any, changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="radar is invalid"):
        svc.update_meta(OWNER, radar.slug, changes, radar.version_lock)


def test_merge_import_of_a_renamed_entry_updates_it(svc: RadarService, radar: Any) -> None:
    r = svc.save(OWNER, radar.slug, [entry("Linak", key="lk"), entry("Uplift", key="up")], 1)
    exported = svc.export(radar.slug, "csv")
    renamed = exported.replace("Linak", "Linak Group")
    r2 = svc.import_entries(OWNER, radar.slug, renamed, "csv", r.current_version, mode="merge")
    by_key = {e.key: e.title for e in svc.entries(radar.slug)}
    assert by_key == {"lk": "Linak Group", "up": "Uplift"} and r2.entry_count == 2


def test_open_suggestions_are_capped_and_an_open_one_is_never_replaced(
    svc: RadarService, radar: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import insights_radar.radars.service as mod

    monkeypatch.setattr(mod, "MAX_OPEN_SUGGESTIONS", 1)
    svc.save(OWNER, radar.slug, [entry("Linak", key="lk")], 1)
    svc.update_meta(OWNER, radar.slug, {"mode": "copilot"}, svc.get(radar.slug).version_lock)
    svc.propose(radar.slug, "m", "r", {"ring": "Niche"}, "reason long enough", "lk")
    with pytest.raises(ConflictError, match="open suggestions"):
        svc.propose(
            radar.slug, "m", "r", {"title": "Desky", "sector": "Emerging", "ring": "Watch"}, "reason long enough"
        )
    # B-058: a second proposal for an entry under review is refused, not silently swapped in
    with pytest.raises(ConflictError, match="already has an open suggestion"):
        svc.propose(radar.slug, "m", "r", {"ring": "Challenger"}, "reason long enough", "lk")
    assert svc.suggestions(radar.slug)[0]["proposal"]["ring"] == "Niche"


def test_queries_follow_every_page(svc: RadarService, radar: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from insights_radar.core.dynamo import Query

    calls: list[dict[str, Any]] = []
    real = svc._t.query

    def paged(**kw: Any) -> Any:
        calls.append(kw)
        res = real(**{**kw, "Limit": 1})  # force one item per page
        return res

    monkeypatch.setattr(svc._t, "query", paged)
    svc.save(OWNER, radar.slug, [entry("Linak", key="lk")], 1)
    from boto3.dynamodb.conditions import Key

    calls.clear()
    items = Query.all_items(svc._t, KeyConditionExpression=Key("pk").eq(f"RADAR#{radar.slug}"))
    assert len(items) >= 3 and len(calls) >= len(items)  # one item per page, every page followed
    assert Query.count(svc._t, KeyConditionExpression=Key("pk").eq(f"RADAR#{radar.slug}")) == len(items)


def test_a_failed_blob_write_gives_the_version_number_back(
    svc: RadarService, radar: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = svc.get(radar.slug)
    monkeypatch.setattr(svc._blobs, "put", lambda key, body: (_ for _ in ()).throw(OSError("s3 down")))
    with pytest.raises(OSError):
        svc._write_version(start, [entry("Lost")], "user:a", "first")
    monkeypatch.undo()
    saved = svc._write_version(start, [entry("Kept")], "user:a", "retry")  # not wedged on V#N
    assert saved.current_version == start.current_version + 1


def _orphan(svc: RadarService, slug: str, number: int, at: str) -> None:
    svc._t.put_item(Item={"pk": f"RADAR#{slug}", "sk": f"V#{number:06d}", "kind": "radar_version", "created_at": at})


def test_a_claim_left_by_a_save_that_died_is_taken_over_once_stale(
    svc: RadarService, radar: Any, clock: FixedClock
) -> None:
    start = svc.get(radar.slug)
    _orphan(svc, radar.slug, start.current_version + 1, iso(clock.now()))  # its Lambda timed out
    with pytest.raises(ConflictError):  # fresh: it could still be a save in progress
        svc._write_version(start, [entry("Early")], "user:a", "early")
    clock.advance(timedelta(minutes=3))
    saved = svc._write_version(start, [entry("Late")], "user:a", "late")
    assert saved.current_version == start.current_version + 1
    assert [e.title for e in svc.entries(radar.slug)] == ["Late"]


def test_taking_over_an_orphan_loses_to_a_concurrent_taker(
    svc: RadarService, radar: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = svc.get(radar.slug)
    number = start.current_version + 1
    _orphan(svc, radar.slug, number, iso(clock.now()))
    clock.advance(timedelta(minutes=3))
    real_get = svc._t.get_item

    def raced(**kw: Any) -> Any:
        held = real_get(**kw)
        _orphan(svc, radar.slug, number, iso(clock.now()))  # another save took it over first
        return held

    monkeypatch.setattr(svc._t, "get_item", raced)
    with pytest.raises(ConflictError):
        svc._write_version(start, [entry("Second")], "user:b", "second")


@pytest.mark.parametrize("call", [1, 2])
def test_other_dynamodb_errors_on_a_claim_are_raised(
    svc: RadarService, radar: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch, call: int
) -> None:
    start = svc.get(radar.slug)
    _orphan(svc, radar.slug, start.current_version + 1, "2000-01-01T00:00:00Z")  # an old orphan
    real_put = svc._t.put_item
    calls = {"n": 0}

    def put(**kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == call:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")
        return real_put(**kw)

    monkeypatch.setattr(svc._t, "put_item", put)
    with pytest.raises(ClientError):
        svc._write_version(start, [entry("X")], "user:a", "x")


def test_imported_keys_the_api_cannot_address_are_replaced() -> None:
    rows = [
        entry("A", key="Tech-12"),
        entry("B", key="Tech 12"),
        entry("C", key="k" * 80),
        entry("D", key="!!!"),
        entry("E", key="ok-1"),
    ]
    clean, _, _ = EntryValidator.check(TEMPLATES["competitor_landscape"][1], rows)
    assert [e.key for e in clean] == ["Tech-12", "Tech-12-2", "k" * 56, "entry-4", "ok-1"]
    assert all(ENTRY_KEY_RE.match(e.key) for e in clean)


def test_a_replacement_key_never_takes_a_later_rows_key() -> None:
    rows = [entry("A", key="Tech 12"), entry("B", key="other"), entry("C", key="Tech-12")]
    clean, _, _ = EntryValidator.check(TEMPLATES["competitor_landscape"][1], rows)
    assert [e.key for e in clean] == ["Tech-12-2", "other", "Tech-12"]  # unique, so the save is accepted


def test_a_landed_write_whose_reply_was_lost_keeps_the_version(
    svc: RadarService, radar: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = svc.get(radar.slug)
    real = svc._replace_radar

    def lost_reply(updated: Any, read: Any) -> None:
        real(updated, read)  # the write lands...
        raise ConflictError("radar changed")  # ...and the retry of the lost reply fails its condition

    monkeypatch.setattr(svc, "_replace_radar", lost_reply)
    saved = svc._write_version(start, [entry("Kept")], "user:a", "save")
    monkeypatch.undo()
    assert saved.current_version == start.current_version + 1
    assert [e.title for e in svc.entries(radar.slug)] == ["Kept"]  # V#N and its blob are still there


def test_an_unreadable_record_keeps_the_claim_rather_than_risk_a_committed_version(
    svc: RadarService, radar: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = svc.get(radar.slug)
    monkeypatch.setattr(svc._blobs, "put", lambda key, body: (_ for _ in ()).throw(OSError("s3 down")))
    monkeypatch.setattr(svc, "get", lambda slug: (_ for _ in ()).throw(OSError("dynamodb down")))
    with pytest.raises(OSError, match="s3 down"):  # the save still fails...
        svc._write_version(start, [entry("Unknown")], "user:a", "save")
    monkeypatch.undo()
    claim = svc._t.get_item(Key={"pk": f"RADAR#{radar.slug}", "sk": f"V#{start.current_version + 1:06d}"})
    assert "Item" in claim  # ...but the claim is kept for the orphan takeover instead of being deleted


def test_sub_sector_round_trips_and_accepts_tech_radar_headers() -> None:
    rows = EntryCodec.parse_csv("title,segment,sub-segment,ring\nAcme,Direct,CRM suites,Leader\n", lambda: "k1")
    assert rows[0].sub_sector == "CRM suites"
    assert rows[0].to_public()["sub_sector"] == "CRM suites"
    text = EntryCodec.to_csv(rows)
    assert text.splitlines()[0].endswith(",sub_sector")
    assert EntryCodec.parse_csv(text, lambda: "k2")[0] == rows[0]
    other = RadarEntry("k1", "Acme", "Direct", "Leader", sub_sector="Point tools")
    assert not other.same_content(rows[0])
    assert RadarDiff.compare([rows[0]], [other])["edited"][0]["key"] == "k1"


def test_sub_sector_is_trimmed_and_limited_on_every_write_path(svc: RadarService, radar: Any) -> None:
    clean, _, _ = EntryValidator.check(
        radar.schema, [entry("A", sub_sector="  CRM \t suites "), entry("B", sub_sector="   ")]
    )
    assert [e.sub_sector for e in clean] == ["CRM suites", None]
    with pytest.raises(ValidationError, match="sub-sector must be at most 120"):
        EntryValidator.check(radar.schema, [entry("C", sub_sector="x" * 121)])


def test_qa106_a_json_file_with_a_bom_parses_like_a_csv_one() -> None:
    text = '\ufeff[{"title": "Kafka", "sector": "General", "ring": "Now"}]'
    assert [e.title for e in EntryCodec.parse_json(text, _keys())] == ["Kafka"]
    assert [e.title for e in EntryCodec.parse_csv("\ufefftitle\nKafka\n", _keys())] == ["Kafka"]


class _Audit:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, dict[str, Any]]] = []

    def record_actor(self, actor: str, _ip: str, action: str, target: str, details: dict[str, Any]) -> None:
        self.rows.append((action, target, details))


def _three_due(svc: RadarService, radar: Any, clock: FixedClock) -> None:
    svc.save(OWNER, radar.slug, [entry("Linak", key="lk"), entry("Uplift", key="up")], 1)
    meta = {"mode": "autopilot", "auto_apply_days": 3, "auto_apply_agent": True}
    svc.update_meta(ADMIN, radar.slug, meta, svc.get(radar.slug).version_lock)
    ev = ["fact/1"]
    svc.propose(radar.slug, "module:curator", "r1", {"ring": "Niche"}, "Evidence from four reviews", "lk", ev)
    svc.propose(radar.slug, "module:curator", "r1", {"ring": "Watch"}, "Evidence from two analyst notes", "up", ev)
    svc.propose(
        radar.slug,
        "module:curator",
        "r1",
        {"title": "Desky", "sector": "Emerging", "ring": "Watch"},
        "New entrant in three outlets",
        evidence=ev,
    )
    clock.advance(timedelta(days=4))


def test_h019_autopilot_applies_a_ticks_proposals_as_one_version(
    svc: RadarService, radar: Any, clock: FixedClock
) -> None:
    _three_due(svc, radar, clock)
    before = svc.get(radar.slug).current_version
    audit = _Audit()
    assert svc.autopilot_tick(audit=audit)["applied"] == 3
    after = svc.get(radar.slug)
    assert after.current_version == before + 1
    assert [r[0] for r in audit.rows] == ["radar.autopilot_apply"] * 3
    assert {r[2]["version"] for r in audit.rows} == {after.current_version}
    final = {e.title: e for e in svc.entries(radar.slug)}
    assert final["Linak"].ring == "Niche" and final["Uplift"].ring == "Watch" and "Desky" in final
    assert svc.suggestions(radar.slug) == []


def test_h019_a_failed_batch_save_puts_every_proposal_back(
    svc: RadarService, radar: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _three_due(svc, radar, clock)
    before = svc.get(radar.slug).current_version

    def conflict(*_a: Any, **_k: Any) -> Any:
        raise ConflictError("the radar was changed by someone else")

    monkeypatch.setattr(svc, "_save", conflict)
    assert svc.autopilot_tick() == {"applied": 0, "superseded": 0, "held": 3, "errors": 0}
    assert len(svc.suggestions(radar.slug)) == 3

    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("bucket down")

    monkeypatch.setattr(svc, "_save", broken)
    assert svc.autopilot_tick()["errors"] == 1
    assert len(svc.suggestions(radar.slug)) == 3 and svc.get(radar.slug).current_version == before


def test_ha078_emptying_a_small_radar_needs_the_confirmation(svc: RadarService, radar: Any) -> None:
    svc.save(OWNER, radar.slug, [entry("Linak", key="lk"), entry("Uplift", key="up")], 1)
    version = svc.get(radar.slug).current_version
    with pytest.raises(ValidationError) as err:
        svc.save(OWNER, radar.slug, [], version)
    assert err.value.details == {"code": "shrinking_save", "before": 2, "after": 0, "removed": 2}
    assert len(svc.entries(radar.slug)) == 2
    assert svc.save(OWNER, radar.slug, [], version, allow_empty=True).entry_count == 0


def test_h019_accept_many_refuses_a_stale_one_and_restores_on_a_failed_save(
    svc: RadarService, radar: Any, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _three_due(svc, radar, clock)
    keys = [s["entry_key"] for s in svc.suggestions(radar.slug)]
    edited = [RadarEntry(**{**e.to_public(), "description": "human edit"}) for e in svc.entries(radar.slug)]
    svc.save(OWNER, radar.slug, edited, svc.get(radar.slug).current_version)
    with pytest.raises(ConflictError, match="changed since"):
        svc.accept_many(OWNER, radar.slug, keys)
    new = [s["entry_key"] for s in svc.suggestions(radar.slug) if s["is_new"]]

    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("bucket down")

    monkeypatch.setattr(svc, "_save", broken)
    with pytest.raises(RuntimeError):
        svc.accept_many(OWNER, radar.slug, new)
    assert len(svc.suggestions(radar.slug)) == 3


def test_hc089_open_new_proposals_count_toward_the_entry_cap(
    svc: RadarService, radar: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insights_radar.radars import service as radar_service

    monkeypatch.setattr(radar_service, "MAX_ENTRIES", 3)
    svc.save(OWNER, radar.slug, [entry("Linak", key="lk"), entry("Uplift", key="up")], 1)
    svc.update_meta(OWNER, radar.slug, {"mode": "assist"}, svc.get(radar.slug).version_lock)
    new = {"title": "Desky", "sector": "Emerging", "ring": "Watch"}
    svc.propose(radar.slug, "module:curator", "r1", new, "New entrant in three outlets")
    with pytest.raises(ConflictError) as err:
        svc.propose(radar.slug, "module:curator", "r1", {**new, "title": "Flexi"}, "New entrant in two outlets")
    assert err.value.details == {"code": "radar_full"}
    # A change to an existing entry is still allowed.
    svc.propose(radar.slug, "module:curator", "r1", {"ring": "Niche"}, "Evidence from four reviews", "lk")


def test_hcb055_a_stored_legacy_editor_does_not_block_the_next_access_change(
    svc: RadarService, radar: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import re

    from insights_radar.radars import service as radar_service

    monkeypatch.setattr(radar_service, "EMAIL_RE", re.compile(r".+"))
    svc.set_access(OWNER, radar.slug, ["legacy user"], svc.get(radar.slug).version_lock)
    monkeypatch.undo()
    r = svc.set_access(OWNER, radar.slug, ["legacy user", "new@example.com"], svc.get(radar.slug).version_lock)
    assert set(r.editors) == {"legacy user", "new@example.com"}
    with pytest.raises(ValidationError, match="must be an email address"):
        svc.set_access(OWNER, radar.slug, ["legacy user", "not an email"], r.version_lock)


def test_ha038_a_save_retried_after_it_committed_is_not_a_conflict(svc: RadarService, radar: Any) -> None:
    first = [entry("Linak", key="lk"), entry("Uplift", key="up")]
    saved = svc.save(OWNER, radar.slug, first, 1)
    again = svc.save(OWNER, radar.slug, first, 1)  # the reply was lost; the client sends the same save
    assert again.current_version == saved.current_version == 2
    with pytest.raises(ConflictError, match="someone else saved"):
        svc.save(OWNER, radar.slug, [entry("Linak", key="lk")], 1)  # a different list is a real conflict
    with pytest.raises(ConflictError, match="someone else saved"):
        svc.save(ADMIN, radar.slug, first, 1)  # and so is the same list from someone else


def test_hc097_a_near_duplicate_new_entry_is_refused(svc: RadarService, radar: Any) -> None:
    svc.save(OWNER, radar.slug, [entry("Bedrock AgentCore", key="ac")], 1)
    svc.update_meta(OWNER, radar.slug, {"mode": "assist"}, svc.get(radar.slug).version_lock)
    new = {"sector": "Emerging", "ring": "Watch"}
    with pytest.raises(ConflictError) as err:
        svc.propose(radar.slug, "module:curator", "r1", {**new, "title": "Amazon Bedrock AgentCore"}, "Two launches")
    assert err.value.details == {"code": "near_duplicate", "existing": "Bedrock AgentCore"}
    svc.propose(radar.slug, "module:curator", "r1", {**new, "title": "Strands Agents"}, "Three outlets this month")
    with pytest.raises(ConflictError, match="same thing"):  # an open new-entry proposal counts too
        svc.propose(radar.slug, "module:curator", "r1", {**new, "title": "AWS Strands-Agents"}, "Three outlets")
    svc.propose(radar.slug, "module:curator", "r1", {**new, "title": "Bedrock Guardrails"}, "Two analyst notes")


def test_hc097_near_duplicate_titles() -> None:
    from insights_radar.radars.service import _near_duplicate
    from insights_radar.radars.service import _significant_words as w

    assert _near_duplicate(w("Knowledge Bases"), w("knowledge base"))
    assert _near_duplicate(w("Strands Agents"), w("Strands Agents Evaluation"))
    assert _near_duplicate(w("OpenTelemetry agent observability"), w("Agent observability with OpenTelemetry tracing"))
    assert not _near_duplicate(w("Bedrock Guardrails"), w("Bedrock AgentCore"))
    assert not _near_duplicate(w("Glass"), w("Glass recycling"))  # one word is never enough
    assert not _near_duplicate(w("The"), w("the SDK")) and w("Access") == {"access"}  # "ss" stays


def test_ha044_an_old_name_suggests_what_a_schema_edit_renamed_it_to(svc: RadarService, radar: Any) -> None:
    svc.save(OWNER, radar.slug, [entry("Linak")], 1)
    new = RadarSchema.from_public(
        {"sectors": ["Head-on rivals", "Adjacent", "Emerging"], "rings": ["Leader", "Challenger", "Niche", "Watch"]}
    )
    svc.update_schema(OWNER, radar.slug, new, 2, renames={"sector": {"Direct": "Head-on rivals"}})
    # Too far for a spelling match: only the remembered rename can name it, and it survives a reload.
    report = svc.validate_import(OWNER, radar.slug, "title,sector,ring\nFlexi,direct,Leader\n", "csv")
    assert report["unmatched"][0]["suggestion"] == "Head-on rivals"
    again = RadarSchema.from_public(
        {
            "sectors": ["Head-on rivals", "Adjacent", "Emerging", "Direct"],
            "rings": ["Leader", "Challenger", "Niche", "Watch"],
        }
    )
    svc.update_schema(OWNER, radar.slug, again, svc.get(radar.slug).current_version)
    assert svc.get(radar.slug).extra.get("renamed", {}) == {}  # "Direct" is a live name again
    assert svc.validate_import(OWNER, radar.slug, "title,sector,ring\nFlexi,Direct,Leader\n", "csv")["accepted"]


def test_ha072_entry_history_names_ai_changes_and_reads_a_bounded_window(svc: RadarService, radar: Any) -> None:
    svc.update_meta(OWNER, radar.slug, {"mode": "assist"}, svc.get(radar.slug).version_lock)
    svc.save(OWNER, radar.slug, [entry("Linak", key="lk")], 1)
    svc.propose(radar.slug, "module:curator", "r1", {"sector": "Adjacent"}, "Three outlets moved it", "lk")
    svc.accept(OWNER, radar.slug, "lk")
    h = svc.entry_history(radar.slug, "lk")
    assert [e["change"] for e in h["events"]] == ["added", "changed"]
    moved = h["events"][1]
    assert moved["ai"] is True and moved["sector_from"] == "Direct" and "fields" not in moved
    # Only the newest versions are read: an entry gone before the window is not found.
    for v in range(3, 6):
        svc.save(OWNER, radar.slug, [entry(f"Other {v}", key="ot")], v)
    with pytest.raises(NotFoundError):
        svc.entry_history(radar.slug, "lk", depth=2)
    late = svc.entry_history(radar.slug, "lk", depth=4)  # versions 3 to 6: it is there, then removed
    assert [e["change"] for e in late["events"]] == ["added", "removed"] and late["current"] is False


def test_ha008_tag_write_failures(svc: RadarService, radar: Any, monkeypatch: Any) -> None:
    from botocore.exceptions import ClientError

    def boom(code: str) -> Any:
        def put_item(**_kw: Any) -> None:
            raise ClientError({"Error": {"Code": code, "Message": "x"}}, "PutItem")

        return put_item

    table = svc._t
    monkeypatch.setattr(table, "put_item", boom("ProvisionedThroughputExceededException"))
    with pytest.raises(ClientError):
        svc.tag_version(OWNER, radar.slug, 1, "Board")
    # The other tag went between the refusal and the read: the message still names this one.
    monkeypatch.setattr(table, "put_item", boom("ConditionalCheckFailedException"))
    with pytest.raises(ConflictError) as err:
        svc.tag_version(OWNER, radar.slug, 1, "Board")
    assert err.value.details["version"] is None
