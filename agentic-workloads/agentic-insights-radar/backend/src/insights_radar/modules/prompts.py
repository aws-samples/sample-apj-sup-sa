# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""A module run's system prompt, built in one place for the agent runtime and for the API's preview.

System prompt = the job description (the template for the module's kind, an administrator's override of
that template, or, in ``replace`` mode, the administrator's instructions) + the module's typed fields + the
shared partials (quality bar, writing rules, untrusted-content rules, run protocol). The shared partials are
appended whatever the module says, so no edit can remove the untrusted-content rules. Every administrator
text (instructions, an override, the product profile, a field schema) is defanged first: it is text the
model should read, not platform instructions.
"""

from __future__ import annotations

import re
from functools import cache
from importlib import resources
from string import Template
from typing import Any

from insights_radar.core.defang import Defang
from insights_radar.modules.attributes import parse_schema
from insights_radar.modules.model import INSTRUCTIONS_MAX, SYSTEM_PROMPT_MAX

TEMPLATES = ("scout", "signal", "trend", "gap", "content", "draft", "curator", "custom")
PARTIALS = {
    "scout": ("_untrusted", "_run"),
    "signal": ("_quality", "_writing", "_untrusted", "_run"),
    "trend": ("_quality", "_writing", "_untrusted", "_run"),
    "gap": ("_quality", "_writing", "_untrusted", "_run"),
    "content": ("_quality", "_writing", "_untrusted", "_run"),
    "draft": ("_untrusted",),
    "curator": ("_untrusted", "_run"),
    "custom": ("_quality", "_writing", "_untrusted", "_run"),
}
# What each slot of a template is filled with, for the preview's legend.
SLOTS = {
    "instructions": "the module's instructions",
    "product": "the attached product's profile (or a note that none is attached)",
    "module_name": "the module's name",
    "insight_kind": "the kind of insight a custom module publishes",
    "output_label": "what a custom module calls its insights",
    "app_name": "the deployment's name (profile)",
    "list_label": "the list the module publishes to (profile)",
    "item_label": "one entry of that list (profile)",
}
NO_PRODUCT = "No product profile is attached to this module."
DEFAULT_APP_NAME = "Agentic Insights Radar"
# The built-in list each template writes to, as it is named when no deployment profile renames it.
_BUILTIN_LIST = {"signal": "Signals", "trend": "Trends", "gap": "Gaps", "content": "Content ideas", "draft": "Drafts"}
MAX_INSTRUCTIONS = INSTRUCTIONS_MAX  # everything the API accepted reaches the model
MAX_PRODUCT = 6000


@cache
def read_template(name: str) -> str:
    """A packaged template or shared partial (``scout``, ``_untrusted``...)."""
    folder = resources.files("insights_radar.modules").joinpath("prompt_templates")
    return str(folder.joinpath(f"{name}.md").read_text("utf-8"))


def template_of(module: dict[str, Any]) -> str:
    template = str(module.get("template") or "custom")
    return template if template in TEMPLATES else "custom"


def _names_slot(text: str, slot: str) -> bool:
    """Whether a template names ``${slot}`` (or ``$slot``)."""
    return re.search(r"\$(\{" + slot + r"\}|" + slot + r"\b)", text) is not None


def same_text(a: str, b: str) -> bool:
    """Equal but for whitespace: an override that only re-wraps the default is the default."""
    return " ".join(a.split()) == " ".join(b.split())


def clean_admin_text(text: object, limit: int) -> str:
    """Defang multi-line admin text but keep its line structure (``neutralize`` flattens it)."""
    body = Defang.redact_markers(str(text or "")).strip()
    return body if len(body) <= limit else body[:limit] + "\n[truncated]"


def _fields_section(raw: object) -> str:
    """The module's typed output fields, generated from its schema. The labels, help and values are admin
    text, so the section is defanged like the instructions; a schema that no longer parses is left out
    (the write tool still enforces whatever the API stores)."""
    if not raw:
        return ""
    try:
        section = parse_schema(raw).prompt_section()
    except ValueError:
        return ""
    return "## Fields to fill\n\n" + clean_admin_text(section, MAX_INSTRUCTIONS) if section else ""


def _skills_section(raw: object) -> str:
    """The module's skills by name and description (CTR's SKILLS block). The descriptions are admin text,
    defanged like the instructions; the bodies are read with skill_load only when a task needs one."""
    if not isinstance(raw, list):
        return ""
    lines = [
        f"- {Defang.neutralize(s.get('name'), 64)}: {Defang.neutralize(s.get('description'), 1024)}"
        for s in raw
        if isinstance(s, dict) and s.get("name")
    ]
    if not lines:
        return ""
    head = (
        "## Skills\n\n"
        "Skills are instructions an administrator added for this module. When a task matches a skill's "
        "description, call skill_load with its name first and follow what it says; do not load one you do "
        "not need. Before you write text a person will read (a title, a summary, an insight, a draft), load "
        "the writing skills listed here."
    )
    return head + "\n\n" + "\n".join(lines)


class SystemPrompt:
    @staticmethod
    def build(module: dict[str, Any], product_block: str | None) -> str:
        template = template_of(module)
        instructions = clean_admin_text(module.get("instructions"), MAX_INSTRUCTIONS) or "Follow the defaults below."
        product = clean_admin_text(product_block, MAX_PRODUCT) if product_block else NO_PRODUCT
        raw_deployment = module.get("deployment")
        deployment: dict[str, Any] = raw_deployment if isinstance(raw_deployment, dict) else {}
        names = {
            "app_name": Defang.neutralize(deployment.get("app_name") or DEFAULT_APP_NAME, 60),
            "list_label": Defang.neutralize(
                deployment.get("list_label") or _BUILTIN_LIST.get(template, "Insights"), 40
            ),
            "item_label": Defang.neutralize(deployment.get("item_label") or "insight", 40),
        }
        values = {
            "instructions": instructions,
            "product": product,
            "module_name": Defang.neutralize(module.get("name") or module.get("slug") or "custom", 80),
            "insight_kind": Defang.neutralize(module.get("insight_kind") or "insight", 60),
            "output_label": Defang.neutralize(module.get("insight_label") or "insights", 80),
            **names,
        }
        if module.get("prompt_mode") == "replace" and module.get("instructions"):
            # The administrator's text is the whole job description; it may name the deployment and the
            # product with the same ${...} slots. The shared rules below are appended regardless.
            body = Template(instructions).safe_substitute(product=product, **names)
            # A replacement prompt that never names ${product} dropped the attached product profile, which
            # is what the Gaps and Content modules compare against (CTR HC-087): it is appended instead.
            if (
                product_block
                and _names_slot(read_template(template), "product")
                and not _names_slot(instructions, "product")
            ):
                body = f"{body.rstrip()}\n\nTHE PRODUCT.\n{product}"
        else:
            # An administrator's own version of the template (CTR's system-prompt editor) is admin text:
            # defanged like the instructions, with the same slots.
            override = clean_admin_text(module.get("system_prompt"), SYSTEM_PROMPT_MAX)
            body = Template(override or read_template(template)).safe_substitute(values)
            # An override that leaves out a slot must not drop what fills it: the module's instructions (which
            # the Configuration page still edits) and the attached product, as for replace mode (HC-087).
            if override and module.get("instructions") and not _names_slot(override, "instructions"):
                body = f"{body.rstrip()}\n\nYOUR FOCUS.\n{instructions}"
            # Only for a kind whose template uses the product (Scout, Gaps, Content, Drafts, custom): Signals,
            # Trends and the curator never had it.
            if (
                override
                and product_block
                and _names_slot(read_template(template), "product")
                and not _names_slot(override, "product")
            ):
                body = f"{body.rstrip()}\n\nTHE PRODUCT.\n{product}"
        parts = [body.strip()]
        fields = _fields_section(module.get("attribute_schema"))
        if fields:
            parts.append(fields)
        skills = _skills_section(module.get("skill_index"))
        if skills:
            parts.append(skills)
        parts += [read_template(p).strip() for p in PARTIALS[template]]
        return "\n\n".join(parts) + "\n"

    @staticmethod
    def preview(module: dict[str, Any], product_block: str | None) -> dict[str, Any]:
        """What an administrator sees of a module's prompt: the template it starts from, its override, the
        shared rules it cannot change, and the whole prompt a run would get now."""
        template = template_of(module)
        override = str(module.get("system_prompt") or "")
        mode = "replace" if module.get("prompt_mode") == "replace" and module.get("instructions") else "extend"
        return {
            "template": template,
            "mode": mode,
            "default": read_template(template),
            "override": override,
            "slots": [{"name": k, "means": v} for k, v in SLOTS.items() if "${" + k + "}" in read_template(template)],
            "partials": [{"name": p.lstrip("_"), "text": read_template(p)} for p in PARTIALS[template]],
            "effective": SystemPrompt.build(module, product_block),
            "max_override": SYSTEM_PROMPT_MAX,
        }
