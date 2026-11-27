# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from insights_radar.core.clock import FixedClock
from insights_radar.core.errors import ConflictError, NotFoundError, ValidationError
from insights_radar.modules.skills import (
    DEFAULT_SKILLS,
    MAX_SKILL_BYTES,
    MAX_SKILLS,
    SkillRepository,
    packaged_default,
    parse_skill,
)

GOOD = (
    "---\nname: rules-of-origin\ndescription: How to read a rules-of-origin notice.\n---\n\n"
    "# Rules of origin\n\nRead the HS chapter first.\n"
)


def test_a_skill_names_itself_and_says_when_to_use_it() -> None:
    assert parse_skill(GOOD) == ("rules-of-origin", "How to read a rules-of-origin notice.")
    assert parse_skill(GOOD, "rules-of-origin")[0] == "rules-of-origin"
    quoted = "---\r\nname: 'x'\r\ndescription: \"Say  it\n  twice\"\r\nmetadata:\n  version: 1\r\n---\r\nBody"
    assert parse_skill(quoted) == ("x", "Say it")


@pytest.mark.parametrize(
    ("content", "fragment"),
    [
        ("# no frontmatter", "starts with frontmatter"),
        ("---\nname: Bad Name\ndescription: d\n---\nbody", "lower-case letters"),
        ("---\nname: ok\n---\nbody", "description is required"),
        ("---\nname: ok\ndescription: " + "d" * 1025 + "\n---\nbody", "at most 1024"),
        ("---\nname: ok\ndescription: d\n---\n   ", "no instructions"),
        (GOOD + "x" * MAX_SKILL_BYTES, "at most 64 KiB"),
    ],
)
def test_a_skill_that_is_not_valid_says_why(content: str, fragment: str) -> None:
    with pytest.raises(ValidationError) as err:
        parse_skill(content)
    assert any(fragment in e for e in err.value.details["errors"])


def test_the_name_in_the_address_must_match() -> None:
    with pytest.raises(ValidationError) as err:
        parse_skill(GOOD, "other")
    assert "names 'rules-of-origin', not 'other'" in str(err.value.details)


def test_create_read_replace_and_delete(ops_table: Any, clock: FixedClock) -> None:
    repo = SkillRepository(ops_table, clock)
    made = repo.put("rules-of-origin", GOOD, "user:a", None)
    assert made.version == 1 and not made.default and made.size == len(GOOD.encode())
    assert repo.get("rules-of-origin").to_public()["content"] == GOOD
    with pytest.raises(ConflictError):
        repo.put("rules-of-origin", GOOD, "user:b", 0)  # someone else saved it first
    again = repo.put("rules-of-origin", GOOD.replace("first", "last"), "user:b", 1)
    assert again.version == 2 and again.updated_by == "user:b"
    assert [s.name for s in repo.list_all()] == ["rules-of-origin"]
    repo.delete("rules-of-origin")
    with pytest.raises(NotFoundError):
        repo.get("rules-of-origin")
    with pytest.raises(NotFoundError):
        repo.get("Not A Name")
    with pytest.raises(ConflictError):
        repo.put("rules-of-origin", GOOD, "user:b", 2)  # it was deleted meanwhile


def test_the_library_is_capped(ops_table: Any, clock: FixedClock) -> None:
    repo = SkillRepository(ops_table, clock)
    for i in range(MAX_SKILLS):
        repo.put(f"s{i}", GOOD.replace("rules-of-origin", f"s{i}"), "u", None)
    with pytest.raises(ValidationError):
        repo.put("one-more", GOOD.replace("rules-of-origin", "one-more"), "u", None)


def test_the_defaults_are_written_kept_when_edited_and_updated_when_not(ops_table: Any, clock: FixedClock) -> None:
    repo = SkillRepository(ops_table, clock)
    assert repo.seed_defaults() == list(DEFAULT_SKILLS)
    assert repo.seed_defaults() == []  # this version is already there
    humanizer = repo.get("humanizer")
    assert humanizer.default and not humanizer.to_summary()["edited"]
    assert "Siqi Chen" in packaged_default("humanizer")
    with pytest.raises(ValidationError):
        repo.delete("humanizer")
    # An administrator's edit is theirs: a deploy does not put the packaged text back.
    edited = humanizer.content + "\nOur house style: British spelling.\n"
    repo.put("humanizer", edited, "user:a", humanizer.version)
    assert repo.get("humanizer").to_summary()["edited"]
    assert repo.seed_defaults() == [] and repo.get("humanizer").content == edited
    # A default nobody edited follows a newer packaged version.
    slop = repo.get("no-ai-slop")
    ops_table.update_item(
        Key={"pk": "SKILL", "sk": "no-ai-slop"},
        UpdateExpression="SET content = :c, seeded_sha256 = :s",
        ExpressionAttributeValues={":c": "old", ":s": __import__("hashlib").sha256(b"old").hexdigest()},
    )
    assert repo.seed_defaults() == ["no-ai-slop"] and repo.get("no-ai-slop").content == slop.content


def test_a_default_saved_by_an_administrator_at_the_same_moment_is_kept(ops_table: Any, clock: FixedClock) -> None:
    class Racing(SkillRepository):
        def _write(self, skill: Any, expected: int | None) -> None:
            raise ConflictError("raced")

    assert Racing(ops_table, clock).seed_defaults() == []


def test_other_write_errors_are_raised(clock: FixedClock) -> None:
    class Table:
        def get_item(self, **_: Any) -> dict[str, Any]:
            return {}

        def query(self, **_: Any) -> dict[str, Any]:
            return {"Items": []}

        def put_item(self, **_: Any) -> None:
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")

    with pytest.raises(ClientError):
        SkillRepository(Table(), clock).put("rules-of-origin", GOOD, "u", None)


def test_the_library_is_read_page_by_page_and_indexed_in_the_modules_order(clock: FixedClock) -> None:
    pages = [
        {"Items": [{"pk": "SKILL", "sk": "b", "description": "B"}], "LastEvaluatedKey": {"k": 1}},
        {"Items": [{"pk": "SKILL", "sk": "a", "description": "A"}]},
    ]

    class Paged:
        def query(self, **_: Any) -> dict[str, Any]:
            return pages.pop(0)

    index = SkillRepository(Paged(), clock).index(("b", "gone", "a"))
    assert index == [{"name": "b", "description": "B"}, {"name": "a", "description": "A"}]


def test_a_save_that_loses_the_race_after_the_read_is_a_conflict(ops_table: Any, clock: FixedClock) -> None:
    repo = SkillRepository(ops_table, clock)
    first = repo.put("rules-of-origin", GOOD, "u", None)
    repo.put("rules-of-origin", GOOD + "v2\n", "u", 1)  # saved by someone else after our read

    class Stale(SkillRepository):
        def get(self, name: str) -> Any:
            return first

    with pytest.raises(ConflictError):
        Stale(ops_table, clock).put("rules-of-origin", GOOD + "mine\n", "me", 1)


def test_a_module_holds_at_most_ten_well_named_skills_and_a_bounded_template() -> None:
    from dataclasses import replace

    from insights_radar.modules.model import MAX_MODULE_SKILLS, SYSTEM_PROMPT_MAX, ModuleValidator
    from insights_radar.modules.templates import builtin_modules

    signal = next(m for m in builtin_modules() if m.slug == "signal")
    for bad, fragment in (
        (replace(signal, skills=tuple(f"s{i}" for i in range(MAX_MODULE_SKILLS + 1))), "at most 10 skills"),
        (replace(signal, skills=("Bad Name",)), "skill names are"),
        (replace(signal, system_prompt="x" * (SYSTEM_PROMPT_MAX + 1)), "system_prompt must be at most"),
    ):
        with pytest.raises(ValidationError) as err:
            ModuleValidator.validate(bad)
        assert any(fragment in e for e in err.value.details["errors"])
    assert ModuleValidator.validate(replace(signal, skills=("a", "a"))).skills == ("a",)  # each once


def test_offering_skills_gives_way_to_a_save_and_raises_other_errors(ops_table: Any, clock: FixedClock) -> None:
    from insights_radar.modules.repository import ModuleRepository

    modules = ModuleRepository(ops_table, clock)
    modules.seed_builtins()
    real = ops_table.put_item

    def racing(**kwargs: Any) -> Any:
        if kwargs.get("ConditionExpression") == "version = :v" and "skills" in str(kwargs.get("Item")):
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        return real(**kwargs)

    ops_table.put_item = racing
    try:
        assert modules.offer_skills(DEFAULT_SKILLS) == []
        ops_table.put_item = lambda **_: (_ for _ in ()).throw(
            ClientError({"Error": {"Code": "InternalServerError"}}, "PutItem")
        )
        with pytest.raises(ClientError):
            modules.offer_skills(DEFAULT_SKILLS)
    finally:
        ops_table.put_item = real
    assert set(modules.offer_skills(DEFAULT_SKILLS)) == {m.slug for m in modules.list_all() if m.builtin}
    assert modules.offer_skills(DEFAULT_SKILLS) == []  # each offered once


def test_a_stale_index_that_misses_an_offer_writes_nothing(ops_table: Any, clock: FixedClock) -> None:
    from insights_radar.modules.repository import ModuleRepository

    modules = ModuleRepository(ops_table, clock)
    modules.seed_builtins()
    stale = modules.list_all()  # read before the offer: says nothing was offered
    modules.offer_skills(DEFAULT_SKILLS)
    modules.list_all = lambda: stale  # type: ignore[method-assign]
    assert modules.offer_skills(DEFAULT_SKILLS) == []  # the consistent read shows it was
