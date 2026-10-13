"""Helpers for working with Streamlit placeholders in the UI layer."""

from __future__ import annotations

from typing import Any, Optional

import streamlit as st


def create_placeholder(parent: Any) -> Optional[Any]:
    """Create an empty placeholder inside the given container."""
    if parent is None:
        return None
    with parent.container():
        return st.empty()


def safe_empty(placeholder: Any) -> None:
    """Safely clear a placeholder if it supports the empty() API."""
    if hasattr(placeholder, "empty"):
        placeholder.empty()


def safe_markdown(placeholder: Any, text: str) -> None:
    """Render markdown if the placeholder exposes markdown()."""
    if placeholder is None:
        return
    if hasattr(placeholder, "markdown"):
        placeholder.markdown(text)


def ensure_status(parent: Any, label: str, *, expanded: bool = False, state: str | None = None):
    """Create a Streamlit status widget in a container."""
    if parent is None:
        return None
    with parent.container():
        return st.status(label, expanded=expanded, state=state)
