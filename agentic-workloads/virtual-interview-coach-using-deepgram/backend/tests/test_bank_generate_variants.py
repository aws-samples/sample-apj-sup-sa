"""Tests for bank.generate variant expansion (production-breadth depth, US4 / #39).

Pure tests: expand_slots/_slot_id touch neither Bedrock nor the DB. They prove that --variants adds
DISTINCT questions per (family,competency,difficulty) slot while keeping variant 0 byte-stable with
the original single-per-slot bank (so a re-run is additive, never a re-shuffle/duplicate)."""

from __future__ import annotations

from bank.generate import _slot_id, expand_slots, _build_gen_user_msg, _load_taxonomy


def test_variant0_id_matches_legacy_id():
    # The legacy (no-variant) id must equal variant 0 so re-running over a seeded bank is idempotent.
    legacy = _slot_id("software_engineering", "role_specific", "technical", "moderate")
    assert legacy == _slot_id("software_engineering", "role_specific", "technical", "moderate", 0)
    # Variants 1..N are distinct from variant 0 and from each other.
    ids = {_slot_id("software_engineering", "role_specific", "technical", "moderate", v) for v in range(4)}
    assert len(ids) == 4


def test_expand_slots_multiplies_by_variants():
    families = _load_taxonomy()
    one = expand_slots(families, only_family="software_engineering", variants=1)
    four = expand_slots(families, only_family="software_engineering", variants=4)
    assert len(four) == len(one) * 4
    # variant 0 rows in the 4-variant expansion are identical (same ids) to the 1-variant expansion.
    v0_ids = [s["id"] for s in four if s["variant"] == 0]
    assert v0_ids == [s["id"] for s in one]
    # every produced id is unique (no slot collisions across variants/difficulties)
    assert len({s["id"] for s in four}) == len(four)


def test_default_variants_is_one_slot_per_slot():
    families = _load_taxonomy()
    default = expand_slots(families)  # variants defaults to 1
    assert all(s["variant"] == 0 for s in default)


def test_variant_user_msg_pins_each_variant_to_a_distinct_theme():
    families = _load_taxonomy()
    se = expand_slots(families, "software_engineering", variants=4)
    moderate = [s for s in se if s["difficulty"] == "moderate"]
    v0 = next(s for s in moderate if s["variant"] == 0)
    themed = [s for s in moderate if s["variant"] >= 1]
    # variant 0 is free-form / legacy-stable: no theme, no distinctness instruction.
    assert v0["theme"] is None
    assert "must center on" not in _build_gen_user_msg(v0).lower()
    # variants >= 1 each pin to a DISTINCT theme so independent generations diverge.
    theme_set = {s["theme"] for s in themed}
    assert len(theme_set) == len(themed), "each variant should get a different theme"
    for s in themed:
        assert s["theme"] is not None
        assert s["theme"].lower() in _build_gen_user_msg(s).lower()
