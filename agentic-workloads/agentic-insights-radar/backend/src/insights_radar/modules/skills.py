# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Skills: Markdown instructions an administrator curates, which a module's runs load when a task calls for them.

CTR's skill library, as data in the operational table. A skill is one Markdown document with a short
frontmatter (``name``, ``description``) and at most ``MAX_SKILL_BYTES``. A module lists the skills it may
use; its system prompt names each with its description, and the run reads a skill's body with the
``skill_load`` tool only when a task matches (progressive disclosure: a skill costs tokens only when used).

Default skills (``default_skills/``: humanizer and no-ai-slop) are written to the library on every deploy.
A default an administrator edited is left as they saved it, and each built-in is offered each default once
(``extra.skills_offered``): one an administrator detached stays detached.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, SystemClock, iso
from insights_radar.core.errors import ConflictError, NotFoundError, ValidationError
from insights_radar.graph.repository import _from_ddb, _to_ddb

MAX_SKILL_BYTES = 64 * 1024
MAX_SKILLS = 50  # in the library
MAX_MODULE_SKILLS = 10  # attached to one module
DESCRIPTION_MAX = 1024
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_FRONTMATTER = re.compile(r"\A---\r?\n(?P<head>.*?)\r?\n---\r?\n?", re.DOTALL)
DEFAULT_SKILLS = ("humanizer", "no-ai-slop")
OFFERED_KEY = "skills_offered"
_PK = "SKILL"


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    content: str
    default: bool = False
    updated_by: str = ""
    updated_at: str = ""
    version: int = 0
    # The packaged text the deploy last wrote, for a default: while the stored text is that one, a newer
    # packaged version replaces it; once an administrator edits it, it is theirs.
    seeded_sha256: str = ""

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def size(self) -> int:
        return len(self.content.encode("utf-8"))

    def to_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "size": self.size,
            "default": self.default,
            "edited": self.default and self.sha256 != self.seeded_sha256,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
            "version": self.version,
        }

    def to_public(self) -> dict[str, Any]:
        return {**self.to_summary(), "content": self.content}


def parse_skill(content: str, name: str | None = None) -> tuple[str, str]:
    """The skill's name and description from its frontmatter; every problem at once."""
    errors: list[str] = []
    if len(content.encode("utf-8")) > MAX_SKILL_BYTES:
        errors.append(f"a skill is at most {MAX_SKILL_BYTES // 1024} KiB")
    match = _FRONTMATTER.match(content)
    fields: dict[str, str] = {}
    if not match:
        errors.append("a skill starts with frontmatter: ---, a name: line, a description: line, ---")
    else:
        for line in match.group("head").splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() and not key.startswith((" ", "\t")):
                fields[key.strip()] = value.strip().strip('"').strip("'")
    found = fields.get("name", "")
    description = " ".join(fields.get("description", "").split())
    if match and not NAME.fullmatch(found):
        errors.append("name must be 1-64 lower-case letters, digits or hyphens")
    if name is not None and found and found != name:
        errors.append(f"the frontmatter names {found!r}, not {name!r}")
    if match and not description:
        errors.append("description is required: the model decides from it when to load the skill")
    elif len(description) > DESCRIPTION_MAX:
        errors.append(f"description must be at most {DESCRIPTION_MAX} characters")
    if match and not content[match.end() :].strip():
        errors.append("the skill has no instructions after its frontmatter")
    if errors:
        raise ValidationError("the skill is not valid", {"errors": errors})
    return found, description


def packaged_default(name: str) -> str:
    folder = resources.files("insights_radar.modules").joinpath("default_skills", name)
    return str(folder.joinpath("SKILL.md").read_text("utf-8"))


class SkillRepository:
    def __init__(self, table: Any, clock: Clock | None = None) -> None:
        self._t = table
        self._clock = clock or SystemClock()

    @staticmethod
    def _from_item(raw: dict[str, Any]) -> Skill:
        i = _from_ddb(raw)
        return Skill(
            name=str(i["sk"]),
            description=str(i.get("description", "")),
            content=str(i.get("content", "")),
            default=bool(i.get("default", False)),
            updated_by=str(i.get("updated_by", "")),
            updated_at=str(i.get("updated_at", "")),
            version=int(i.get("version", 0)),
            seeded_sha256=str(i.get("seeded_sha256", "")),
        )

    def list_all(self) -> list[Skill]:
        out: list[Skill] = []
        kwargs: dict[str, Any] = {"KeyConditionExpression": "pk = :p", "ExpressionAttributeValues": {":p": _PK}}
        while True:
            res = self._t.query(**kwargs)
            out += [self._from_item(i) for i in res.get("Items", [])]
            if not res.get("LastEvaluatedKey"):
                return sorted(out, key=lambda s: s.name)
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]

    def get(self, name: str) -> Skill:
        raw = (
            self._t.get_item(Key={"pk": _PK, "sk": name}, ConsistentRead=True).get("Item")
            if NAME.fullmatch(name)
            else None
        )
        if not raw:
            raise NotFoundError(f"skill {name!r} not found")
        return self._from_item(raw)

    def put(self, name: str, content: str, actor: str, expected_version: int | None) -> Skill:
        """Create (``expected_version`` None or 0) or replace (the version read) a skill."""
        _, description = parse_skill(content, name)
        current: Skill | None
        try:
            current = self.get(name)
        except NotFoundError:
            current = None
        if current is None and len(self.list_all()) >= MAX_SKILLS:
            raise ValidationError(f"at most {MAX_SKILLS} skills; delete one first")
        if current is not None and expected_version != current.version:
            raise ConflictError("the skill was changed by someone else; reload and retry")
        if current is None and expected_version:
            raise ConflictError("the skill was deleted by someone else; reload and retry")
        skill = Skill(
            name=name,
            description=description,
            content=content,
            default=current.default if current else False,
            updated_by=actor,
            updated_at=iso(self._clock.now()),
            version=(current.version if current else 0) + 1,
            seeded_sha256=current.seeded_sha256 if current else "",
        )
        self._write(skill, current.version if current else None)
        return skill

    def delete(self, name: str) -> Skill:
        skill = self.get(name)
        if skill.default:
            raise ValidationError("a default skill cannot be deleted; detach it from the modules instead")
        self._t.delete_item(Key={"pk": _PK, "sk": name})
        return skill

    def _write(self, skill: Skill, expected: int | None) -> None:
        item = {
            "pk": _PK,
            "sk": skill.name,
            "kind": "skill",
            "description": skill.description,
            "content": skill.content,
            "default": skill.default,
            "updated_by": skill.updated_by,
            "updated_at": skill.updated_at,
            "version": skill.version,
            "seeded_sha256": skill.seeded_sha256,
        }
        condition = "attribute_not_exists(pk)" if expected is None else "version = :v"
        values = {} if expected is None else {"ExpressionAttributeValues": {":v": expected}}
        try:
            self._t.put_item(Item=_to_ddb(item), ConditionExpression=condition, **values)
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflictError("the skill was changed by someone else; reload and retry") from err
            raise

    def seed_defaults(self) -> list[str]:
        """Write the packaged default skills: a missing one, or one still holding the text a deploy wrote
        before (a newer packaged version replaces it). One an administrator edited is theirs."""
        written: list[str] = []
        for name in DEFAULT_SKILLS:
            text = packaged_default(name)
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            _, description = parse_skill(text, name)
            try:
                current: Skill | None = self.get(name)
            except NotFoundError:
                current = None
            if current is not None and (current.sha256 != current.seeded_sha256 or current.seeded_sha256 == sha):
                continue  # edited by an administrator, or already this version
            skill = Skill(
                name,
                description,
                text,
                True,
                "system:seed",
                iso(self._clock.now()),
                (current.version if current else 0) + 1,
                sha,
            )
            try:
                self._write(skill, current.version if current else None)
            except ConflictError:
                continue  # an administrator saved it at this very moment: theirs
            written.append(name)
        return written

    def index(self, names: tuple[str, ...]) -> list[dict[str, str]]:
        """Name and description of each named skill that exists, in the module's order (for its prompt)."""
        by_name = {s.name: s for s in self.list_all()}
        return [{"name": n, "description": by_name[n].description} for n in names if n in by_name]
