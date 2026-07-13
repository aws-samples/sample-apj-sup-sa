"""Tool usage rendering and management for the Streamlit UI."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import streamlit as st

from . import utils
from .placeholders import create_placeholder, safe_empty
from .state import StreamlitUIState


def render_tool_calls(tool_calls: Iterable[Dict[str, Any]]) -> None:
    """Render completed tool calls as simple status widgets."""
    for index, call in enumerate(tool_calls, start=1):
        title_name = call.get("name") or f"Tool {index}"

        with st.status(f"✅ Tool: {title_name}", state="complete", expanded=False):
            tool_use_id = call.get("tool_use_id")
            if tool_use_id:
                st.write(f"**Tool ID:** {tool_use_id}")

            input_value = call.get("input")
            if input_value is not None:
                st.write("**Input:**")
                utils.render_tool_value(input_value, call.get("input_is_json", False))

            result_value = call.get("result")
            if result_value is not None:
                st.write("**Result:**")
                utils.render_tool_value(result_value, call.get("result_is_json", False))


class ToolUIManager:
    """Handle tool invocation events and UI rendering."""

    def __init__(self, ui_state: StreamlitUIState):
        self.ui_state = ui_state

    # ------------------------------------------------------------------
    def can_handle(self, event: Dict[str, Any]) -> bool:
        keys = {"current_tool_use", "tool_result", "event", "force_stop"}
        return any(key in event for key in keys)

    def handle(self, event: Dict[str, Any]) -> None:
        if "current_tool_use" in event:
            self._handle_current_tool_use(event["current_tool_use"])
        if "tool_result" in event:
            self._handle_tool_result(event["tool_result"])
        if "event" in event:
            self._handle_progress_event(event["event"])
        if event.get("force_stop"):
            self.mark_force_stop()

    # ------------------------------------------------------------------
    def finalize(self) -> None:
        """Finalize tool status to complete state."""
        # Mark all tools as complete
        for tool_entry in self.ui_state.assistant_message.get("tool_calls", []):
            self._render_tool_entry(tool_entry, status="complete")

        # Keep placeholders for final display
        # Note: Don't clear placeholders here as they contain the final tool results

    def mark_force_stop(self) -> None:
        for tool_entry in self.ui_state.assistant_message.get("tool_calls", []):
            self._render_tool_entry(tool_entry, status="error")

    # ------------------------------------------------------------------
    def _handle_current_tool_use(self, tool_data: Dict[str, Any]) -> None:
        tool_id = tool_data.get("toolUseId") or tool_data.get("tool_use_id")
        if tool_id and tool_id in self.ui_state.tool_map:
            tool_entry = self.ui_state.tool_map[tool_id]
        else:
            input_value, input_is_json = utils.normalize_tool_value(
                tool_data.get("input") or tool_data.get("arguments")
            )
            tool_entry = {
                "name": tool_data.get("name") or f"Tool {len(self.ui_state.assistant_message['tool_calls']) + 1}",
                "tool_use_id": tool_id,
                "input": input_value,
                "input_is_json": input_is_json,
                "result": None,
                "result_is_json": False,
            }
            self.ui_state.assistant_message["tool_calls"].append(tool_entry)
            if tool_id:
                self.ui_state.tool_map[tool_id] = tool_entry

        self._render_tool_entry(tool_entry, status="running")

    def _handle_tool_result(self, payload: Any) -> None:
        tool_id = None
        display_payload = payload

        if isinstance(payload, dict):
            tool_id = payload.get("toolUseId") or payload.get("tool_use_id")
            if "output" in payload:
                display_payload = payload["output"]
            elif "content" in payload:
                display_payload = payload["content"]
            else:
                stripped = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"toolUseId", "tool_use_id"}
                }
                if stripped:
                    display_payload = stripped

        value, is_json = utils.normalize_tool_value(display_payload)

        if tool_id and tool_id in self.ui_state.tool_map:
            tool_entry = self.ui_state.tool_map[tool_id]
        elif self.ui_state.assistant_message["tool_calls"]:
            tool_entry = self.ui_state.assistant_message["tool_calls"][-1]
        else:
            tool_entry = {
                "name": "Tool",
                "tool_use_id": tool_id,
                "input": None,
                "input_is_json": False,
                "result": None,
                "result_is_json": False,
            }
            self.ui_state.assistant_message["tool_calls"].append(tool_entry)
            if tool_id:
                self.ui_state.tool_map[tool_id] = tool_entry

        tool_entry["result"] = value if value is not None else display_payload
        tool_entry["result_is_json"] = is_json or isinstance(display_payload, (dict, list))
        self._render_tool_entry(tool_entry, status="running")

    def _handle_progress_event(self, event_data: Dict[str, Any]) -> None:
        # Re-render any in-flight tool entries so the spinner remains visible.
        if not self.ui_state.assistant_message["tool_calls"]:
            return
        for tool_entry in self.ui_state.assistant_message["tool_calls"]:
            self._render_tool_entry(tool_entry, status="running")

    # ------------------------------------------------------------------
    def _render_tool_entry(self, tool_entry: Dict[str, Any], *, status: str) -> None:
        placeholder = self._ensure_tool_placeholder(tool_entry)
        if not placeholder:
            return

        status = status.lower()
        title_prefix = {
            "running": "🔧",
            "complete": "✅",
            "error": "❌",
        }.get(status, "🔧")
        title_name = tool_entry.get("name") or "Tool"
        label = f"{title_prefix} Tool: {title_name}"

        # Simple status without expander nesting
        placeholder.empty()
        with placeholder.container():
            if status == "running":
                status_widget = st.status(f"{label}...", state="running", expanded=False)
            elif status == "error":
                status_widget = st.status(f"{label} - Error", state="error", expanded=False)
            else:
                # Complete status - fill with content
                status_widget = st.status(label, state="complete", expanded=False)
                with status_widget:
                    tool_use_id = tool_entry.get("tool_use_id")
                    if tool_use_id:
                        st.write(f"**Tool ID:** {tool_use_id}")

                    input_value = tool_entry.get("input")
                    if input_value is not None:
                        st.write("**Input:**")
                        utils.render_tool_value(input_value, tool_entry.get("input_is_json", False))

                    result_value = tool_entry.get("result")
                    if result_value is not None:
                        st.write("**Result:**")
                        utils.render_tool_value(result_value, tool_entry.get("result_is_json", False))

    def _ensure_tool_placeholder(self, tool_entry: Dict[str, Any]) -> Optional[Any]:
        key = tool_entry.get("tool_use_id") or id(tool_entry)
        placeholders = self.ui_state.tools.placeholders
        placeholder = placeholders.get(key)
        if placeholder:
            return placeholder

        parent = self.ui_state.tool_placeholder or self.ui_state.status_placeholder
        if not parent:
            return None

        placeholder = create_placeholder(parent)
        if placeholder:
            placeholders[key] = placeholder
        return placeholder
