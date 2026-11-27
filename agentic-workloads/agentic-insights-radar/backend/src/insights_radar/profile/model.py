# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The deployment profile: what this deployment calls itself and its lists, and which lists it shows.

Everything is optional. A field that is not set falls back to the built-in text below, so a deployment
without a profile reads exactly as before. The profile is public (it is served on the unauthenticated
``/api/config``), so it must never hold anything but display text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from insights_radar.core.errors import ValidationError
from insights_radar.graph.model import INSIGHT_KIND_RE
from insights_radar.graph.registry import NodeType

APP_NAME_MAX = 60
TAGLINE_MAX = 160
ABOUT_MAX = 4000
LABEL_MAX = 40
HELP_MAX = 400
ORDER_MAX = 99
# Where a custom list without an order sorts: after the built-in lists, as with no entry at all.
CUSTOM_ORDER = 100
MAX_LISTS = 30
# Parts of the app a deployment can turn off, each on unless the profile says otherwise. A deployment that does
# not describe a product of its own (for example, a trade-regulation radar) hides Products; its data and API stay.
# Parts of the app a deployment can turn off. Products and radars are hidden in the web app only (the API still
# answers); sample_data is refused by the API too, since loading it writes fictional data into the graph.
SECTIONS: tuple[str, ...] = ("products", "radars", "sample_data")

BUILTIN_LISTS: tuple[str, ...] = ("signals", "trends", "gaps", "content", "drafts")
# The built-in list each output type is shown in; a custom module's outputs are in "insight:<kind>".
LIST_OF: dict[NodeType, str] = {
    NodeType.SIGNAL: "signals",
    NodeType.TREND: "trends",
    NodeType.GAP: "gaps",
    NodeType.CONTENT_IDEA: "content",
    NodeType.CONTENT_DRAFT: "drafts",
}


def list_key(node_type: NodeType | None, insight_kind: str | None = None) -> str | None:
    """The profile key of the list a module's outputs, or a list request, belong to."""
    if insight_kind:
        return f"insight:{insight_kind}"
    return LIST_OF.get(node_type) if node_type is not None else None


_INSIGHT_LIST_RE = re.compile(r"^insight:(?P<kind>.+)$")
_ENTRY_FIELDS = frozenset({"label", "item", "help", "visible", "order"})
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ListProfile:
    """How one insight list is named and shown. ``label`` names the list, ``item`` one record in it."""

    label: str
    item: str
    help: str
    visible: bool = True
    order: int = 0
    # The fields the document set; the rest are defaults. Empty means all (a built-in default entry).
    set_keys: frozenset[str] = field(default=frozenset(), compare=False)

    def to_public(self) -> dict[str, Any]:
        return {"label": self.label, "item": self.item, "help": self.help, "visible": self.visible, "order": self.order}

    def to_stored(self) -> dict[str, Any]:
        """Only the fields the document set, so an entry that hides a list keeps the default text."""
        full = self.to_public()
        return {k: v for k, v in full.items() if k in self.set_keys} if self.set_keys else full


DEFAULT_APP_NAME = "Agentic Insights Radar"
DEFAULT_TAGLINE = "Insights from the sources you choose, written by agents and kept current."

DEFAULT_LISTS: dict[str, ListProfile] = {
    "signals": ListProfile(
        "Signals", "Signal", "Early movements worth knowing about, each backed by evidence.", True, 1
    ),
    "trends": ListProfile("Trends", "Trend", "Patterns that hold across many signals over weeks, not days.", True, 2),
    "gaps": ListProfile("Gaps", "Gap", "What the market asks for that your product does not do yet.", True, 3),
    "content": ListProfile(
        "Content ideas",
        "Content idea",
        "Things to publish: questions your product answers that nobody has written about yet.",
        True,
        4,
    ),
    "drafts": ListProfile(
        "Drafts", "Draft", "Full Markdown drafts written from a content idea, citing their sources.", True, 5
    ),
}


@dataclass(frozen=True, slots=True)
class Profile:
    app_name: str = DEFAULT_APP_NAME
    tagline: str = DEFAULT_TAGLINE
    about_md: str = ""
    lists: dict[str, ListProfile] = field(default_factory=dict)
    # Only the sections the document set: a later default reaches a deployment that never chose.
    sections: dict[str, bool] = field(default_factory=dict)
    # What a radar is for in this deployment, shown on Radars and in Getting started ("" = the built-in text,
    # which speaks of competitors, vendors and technologies).
    radars_help: str = ""
    version: int = 0
    updated_by: str | None = None
    updated_at: str | None = None
    # The stored document failed validation and the built-in text stands in for it: a seed that matches the
    # defaults must still write, or the broken item would stay forever (round 7 review).
    stored_invalid: bool = field(default=False, compare=False)
    # What a newer release stored that this code does not know (top-level fields, sections), kept as written and
    # written back, so code rolled back and an admin save in between lose nothing (code review r18).
    unknown: dict[str, Any] = field(default_factory=dict, compare=False)

    def list_profile(self, key: str) -> ListProfile | None:
        """The configured entry, else the built-in default, else None (a custom kind with no entry)."""
        return self.lists.get(key) or DEFAULT_LISTS.get(key)

    def is_visible(self, key: str) -> bool:
        lp = self.list_profile(key)
        return True if lp is None else lp.visible

    def section_visible(self, key: str) -> bool:
        return self.sections.get(key, True)

    def to_public(self) -> dict[str, Any]:
        merged = {**DEFAULT_LISTS, **self.lists}
        return {
            "app_name": self.app_name,
            "tagline": self.tagline,
            "about_md": self.about_md,
            # "set" names the fields a configured entry changed, so clients rename only those.
            "lists": {
                k: {**v.to_public(), **({"set": sorted(v.set_keys)} if k in self.lists else {})}
                for k, v in merged.items()
            },
            "sections": {k: self.section_visible(k) for k in SECTIONS},
            "radars_help": self.radars_help,
            "version": self.version,
        }

    def to_known_item(self) -> dict[str, Any]:
        """The stored form of what this code knows: only what was set, so a later change to a default reaches
        this deployment. What the admin API shows and accepts, and what the seed compares (code review r19)."""
        return {
            "app_name": self.app_name,
            "tagline": self.tagline,
            "about_md": self.about_md,
            "lists": {k: v.to_stored() for k, v in self.lists.items()},
            **({"sections": dict(self.sections)} if self.sections else {}),
            **({"radars_help": self.radars_help} if self.radars_help else {}),
        }

    def to_item(self) -> dict[str, Any]:
        """The whole stored form: the known fields plus what a newer release stored that this code does not know."""
        return self.to_known_item() | _merge_unknown(self.unknown, self.sections)

    def unknown_keys(self) -> list[str]:
        """The names of the kept unknown fields: ``field`` at the top level, ``sections.<key>`` for a section."""
        top = [k for k in self.unknown if k != "sections"]
        extra = self.unknown.get("sections")
        nested = [f"sections.{k}" for k in extra] if isinstance(extra, dict) else []
        return sorted(top + nested)

    def keeping_unknown(self, stored: Profile, drop: frozenset[str] = frozenset()) -> Profile:
        """This profile, carrying what ``stored`` holds that this code does not know (an admin save), less the
        names in ``drop``: the only way an admin removes a field a rolled-back release cannot edit (code review r19)."""
        stray = sorted(drop - set(stored.unknown_keys()))
        if stray:
            raise ValidationError("drop_unknown names fields that are not stored", {"unknown": stray})
        kept = {k: v for k, v in stored.unknown.items() if k != "sections" and k not in drop}
        extra = stored.unknown.get("sections")
        if isinstance(extra, dict):
            left = {k: v for k, v in extra.items() if f"sections.{k}" not in drop}
            if left:
                kept["sections"] = left
        return replace(self, unknown=kept)


def _text(value: Any, name: str, lo: int, hi: int, errors: list[str]) -> str:
    if not isinstance(value, str):
        errors.append(f"{name} must be text")
        return ""
    text = value.strip()
    if not lo <= len(text) <= hi:
        errors.append(f"{name} must be {lo}-{hi} characters")
    elif _CONTROL_RE.search(text):
        errors.append(f"{name} must not contain control characters")
    return text


def _list_key_ok(key: str) -> bool:
    if key in BUILTIN_LISTS:
        return True
    m = _INSIGHT_LIST_RE.fullmatch(key)
    return bool(m and INSIGHT_KIND_RE.fullmatch(m.group("kind")))


def _list_entry(key: str, raw: Any, errors: list[str]) -> ListProfile | None:
    if not isinstance(raw, dict):
        errors.append(f"lists.{key} must be an object")
        return None
    unknown = set(raw) - _ENTRY_FIELDS
    if unknown:
        errors.append(f"lists.{key} has unknown fields: {', '.join(sorted(unknown))}")
    base = DEFAULT_LISTS.get(key)
    label = _text(raw["label"], f"lists.{key}.label", 1, LABEL_MAX, errors) if "label" in raw else None
    item = _text(raw["item"], f"lists.{key}.item", 1, LABEL_MAX, errors) if "item" in raw else None
    help_ = _text(raw["help"], f"lists.{key}.help", 0, HELP_MAX, errors) if "help" in raw else None
    visible = raw.get("visible", True if base is None else base.visible)
    if not isinstance(visible, bool):
        errors.append(f"lists.{key}.visible must be true or false")
        visible = True
    order = raw.get("order", 0)
    if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= ORDER_MAX:
        errors.append(f"lists.{key}.order must be a whole number 0-{ORDER_MAX}")
        order = 0
    if "order" not in raw:
        order = CUSTOM_ORDER if base is None else base.order
    if base is None and (label is None or item is None):
        errors.append(f"lists.{key} needs a label and an item name")
    return ListProfile(
        label=label if label is not None else (base.label if base else ""),
        item=item if item is not None else (base.item if base else ""),
        help=help_ if help_ is not None else (base.help if base else ""),
        visible=visible,
        order=order,
        set_keys=frozenset(set(raw) & _ENTRY_FIELDS) or frozenset({"visible"}),
    )


def parse_profile(raw: Any, stored: bool = False) -> Profile:
    """Validate a profile document (from the seed bundle, the admin API or the stored item). ``stored``: a field
    or section this code does not know is kept aside, not refused, so code rolled back to before it was added
    still reads the profile a newer seed wrote (code reviews r17, r18); the seed and the admin API stay strict."""
    if not isinstance(raw, dict):
        raise ValidationError("profile must be an object")
    errors: list[str] = []
    unknown = set(raw) - {"app_name", "tagline", "about_md", "lists", "sections", "radars_help"}
    kept: dict[str, Any] = {k: raw[k] for k in sorted(unknown)} if stored else {}
    if unknown and not stored:
        errors.append(f"unknown fields: {', '.join(sorted(unknown))}")
    app_name = _text(raw["app_name"], "app_name", 1, APP_NAME_MAX, errors) if "app_name" in raw else DEFAULT_APP_NAME
    tagline = _text(raw["tagline"], "tagline", 0, TAGLINE_MAX, errors) if "tagline" in raw else DEFAULT_TAGLINE
    about = raw.get("about_md", "")
    if not isinstance(about, str) or len(about) > ABOUT_MAX:
        errors.append(f"about_md must be text of at most {ABOUT_MAX} characters")
        about = ""
    lists: dict[str, ListProfile] = {}
    raw_lists = raw.get("lists", {})
    if not isinstance(raw_lists, dict):
        errors.append("lists must be an object")
    elif len(raw_lists) > MAX_LISTS:
        errors.append(f"lists may hold at most {MAX_LISTS} entries")
    else:
        for key, entry in raw_lists.items():
            if not isinstance(key, str) or not _list_key_ok(key):
                errors.append(
                    f"lists key {str(key)[:60]!r} is not a list"
                    f" (use one of {', '.join(BUILTIN_LISTS)} or insight:<kind>)"
                )
                continue
            parsed = _list_entry(key, entry, errors)
            if parsed is not None:
                lists[key] = parsed
    sections: dict[str, bool] = {}
    raw_sections = raw.get("sections", {})
    if not isinstance(raw_sections, dict):
        errors.append("sections must be an object")
    else:
        for key, on in raw_sections.items():
            if key not in SECTIONS and stored:
                kept.setdefault("sections", {})[key] = on
                continue
            if key not in SECTIONS:
                errors.append(f"sections key {str(key)[:60]!r} is not a section (use one of {', '.join(SECTIONS)})")
            elif not isinstance(on, bool):
                errors.append(f"sections.{key} must be true or false")
            else:
                sections[key] = on
    radars_help = _text(raw["radars_help"], "radars_help", 0, HELP_MAX, errors) if "radars_help" in raw else ""
    if errors:
        raise ValidationError("profile is not valid", {"errors": errors})
    return Profile(
        app_name=app_name,
        tagline=tagline,
        about_md=about,
        lists=lists,
        sections=sections,
        radars_help=radars_help,
        unknown=kept,
    )


def _merge_unknown(unknown: dict[str, Any], sections: dict[str, bool]) -> dict[str, Any]:
    """The stored fields this code does not know, with unknown sections beside the known ones."""
    out = {k: v for k, v in unknown.items() if k != "sections"}
    extra = unknown.get("sections")
    if isinstance(extra, dict) and extra:
        out["sections"] = {**extra, **sections}
    return out


def with_meta(p: Profile, version: int, updated_by: str | None, updated_at: str | None) -> Profile:
    return replace(p, version=version, updated_by=updated_by, updated_at=updated_at)
