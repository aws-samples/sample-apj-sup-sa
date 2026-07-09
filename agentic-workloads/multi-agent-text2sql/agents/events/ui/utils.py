"""Utility helpers for rendering model output and tool payloads."""

from __future__ import annotations

import json
import re
from typing import Any, Tuple

import streamlit as st

THINKING_PATTERN = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)


def parse_model_response(raw_text: str | None) -> Tuple[str, str | None]:
    """Return displayable text and optional chain-of-thought content."""
    if not raw_text:
        return "", None
    match = THINKING_PATTERN.search(raw_text)
    chain_of_thought = match.group(1).strip() if match else None
    cleaned = THINKING_PATTERN.sub("", raw_text).strip()
    return cleaned, chain_of_thought


def strip_partial_thinking(raw_text: str) -> str:
    """Hide incomplete thinking tags during streaming."""
    if "<thinking>" in raw_text and "</thinking>" not in raw_text:
        return raw_text.split("<thinking>")[0]
    if "<thinking>" in raw_text and "</thinking>" in raw_text:
        cleaned, _ = parse_model_response(raw_text)
        return cleaned
    return raw_text


def normalize_tool_value(value: Any) -> Tuple[Any, bool]:
    """Normalize tool payloads into a display value and a JSON flag."""
    if value is None:
        return None, False
    if isinstance(value, (dict, list)):
        return value, True
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith("{") or candidate.startswith("["):
            try:
                return json.loads(value), True
            except json.JSONDecodeError:
                pass
        return value, False
    return value, False


def render_tool_value(value: Any, as_json: bool) -> None:
    """Render a tool payload using an appropriate Streamlit widget."""
    if value is None:
        return
    if as_json:
        st.json(value)
    elif isinstance(value, str):
        st.code(value)
    else:
        st.write(value)
