"""Chain of Thought rendering for the Streamlit UI."""

from __future__ import annotations

from typing import Any, Dict, Optional
import re

import streamlit as st

from .state import StreamlitUIState


class COTUIManager:
    """Handle Chain of Thought (thinking blocks) during streaming."""

    def __init__(self, ui_state: StreamlitUIState):
        self.ui_state = ui_state
        self._thinking_pattern = re.compile(r'<thinking>(.*?)</thinking>', re.DOTALL)
        self._in_thinking = False
        self._thinking_buffer = ""
        self._initial_buffer = ""
        self._buffer_checked = False
        self._buffer_size = 20

    # ------------------------------------------------------------------
    def can_handle(self, event: Dict[str, Any]) -> bool:
        # Handle data events that might contain <thinking> blocks
        return "data" in event

    def handle(self, event: Dict[str, Any]) -> None:
        if "data" in event:
            data = event.get("data", "")
            self._process_thinking_data(data)

    # ------------------------------------------------------------------
    def _process_thinking_data(self, data: str) -> None:
        """Process data and detect thinking blocks character by character."""
        # The filtering logic now handles the thinking detection
        # This method is mainly for consistency, actual work is done in filter_thinking_from_data
        pass

    def _ensure_cot_status(self) -> None:
        """Create simple COT status container (no nesting)."""
        if self.ui_state.cot.status:
            return

        parent = self.ui_state.chain_placeholder or self.ui_state.status_placeholder
        if not parent:
            return

        with parent.container():
            status = st.status("🤔 Chain of Thought...", expanded=False)

        self.ui_state.cot.status = status

    # ------------------------------------------------------------------
    def finalize(self, chain_of_thought: Optional[str] = None) -> None:
        """Update COT status and content when streaming completes."""
        cot_state = self.ui_state.cot

        if cot_state.status:
            # Use extracted thinking content or fallback to chain_of_thought
            final_content = cot_state.text or chain_of_thought

            if final_content:
                with cot_state.status:
                    st.markdown(final_content)

                cot_state.status.update(
                    label="🤔 Chain of Thought",
                    state="complete",
                    expanded=False,
                )
            else:
                # No COT content, hide the status
                cot_state.status.update(
                    label="🤔 No reasoning captured",
                    state="complete",
                    expanded=False,
                )

    def mark_force_stop(self) -> None:
        """Mark COT as aborted."""
        if self.ui_state.cot.status:
            self.ui_state.cot.status.update(
                label="❌ Chain of Thought aborted",
                state="error",
                expanded=False,
            )

    # ------------------------------------------------------------------
    def filter_thinking_from_data(self, data: str) -> str:
        """Remove thinking blocks from streaming data for clean text output.

        This should be called BEFORE handle() to get the correct filtering.
        """
        if not data:
            return data

        # Buffer initial characters to detect thinking pattern
        if not self._buffer_checked:
            self._initial_buffer += data

            # If we haven't collected enough characters yet, don't output anything
            if len(self._initial_buffer) < self._buffer_size:
                return ""

            # Check if thinking pattern exists in the initial buffer
            if '<thinking>' in self._initial_buffer:
                self._in_thinking = True
                self._ensure_cot_status()
                # Find where thinking starts and return text before it (if any)
                thinking_start = self._initial_buffer.find('<thinking>')
                self._buffer_checked = True
                return self._initial_buffer[:thinking_start]
            else:
                # No thinking pattern, return the buffered data and continue normally
                self._buffer_checked = True
                return self._initial_buffer

        # After initial buffer check, handle normally
        if self._in_thinking:
            # Add to thinking buffer (include the initial buffer if it hasn't been processed)
            if not hasattr(self, '_initial_added_to_thinking'):
                # Add the part of initial buffer that contains <thinking>
                thinking_start = self._initial_buffer.find('<thinking>')
                self._thinking_buffer = self._initial_buffer[thinking_start:]
                self._initial_added_to_thinking = True

            self._thinking_buffer += data

            # Check if thinking ends in this chunk
            if '</thinking>' in self._thinking_buffer:
                self._in_thinking = False
                # Extract thinking content
                matches = self._thinking_pattern.findall(self._thinking_buffer)
                if matches:
                    self.ui_state.cot.text = matches[-1].strip()

                # Find where thinking ends and return text after it
                thinking_end = self._thinking_buffer.find('</thinking>') + len('</thinking>')
                after_thinking = self._thinking_buffer[thinking_end:]
                return after_thinking
            else:
                # Still inside thinking block, don't output anything
                return ""
        else:
            # Normal streaming after buffer check
            return data

    def has_cot_content(self) -> bool:
        """Check if there's any COT content to display."""
        return bool(self.ui_state.cot.text)

    def reset_for_new_conversation(self) -> None:
        """Reset COT state for a new conversation."""
        self._thinking_buffer = ""
        self._in_thinking = False
        self._initial_buffer = ""
        self._buffer_checked = False
        if hasattr(self, '_initial_added_to_thinking'):
            delattr(self, '_initial_added_to_thinking')
