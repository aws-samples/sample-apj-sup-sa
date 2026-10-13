"""State containers for the Streamlit UI handler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class PlaceholderState:
    """Top-level Streamlit placeholders shared by the managers."""

    status: Any = None
    tool: Any = None
    chain: Any = None
    response: Any = None
    message_container: Any = None


@dataclass
class ReasoningState:
    """Reasoning-specific UI state."""

    status: Any = None
    text: str = ""


@dataclass
class COTState:
    """Chain of Thought UI state."""

    status: Any = None
    text: str = ""


@dataclass
class ToolRenderState:
    """Bookkeeping for dynamically created tool placeholders."""

    placeholders: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageState:
    """State associated with streaming and final messages."""

    raw_response: str = ""
    filtered_response: str = ""
    final_message: Any = None
    force_stop_error: str | None = None
    assistant_appended: bool = False


def _empty_assistant_message() -> Dict[str, Any]:
    return {
        "text": "",
        "chain_of_thought": None,
        "tool_calls": [],
    }


@dataclass
class StreamlitUIState:
    """Root state object shared across UI managers."""

    placeholders: PlaceholderState = field(default_factory=PlaceholderState)
    reasoning: ReasoningState = field(default_factory=ReasoningState)
    cot: COTState = field(default_factory=COTState)
    tools: ToolRenderState = field(default_factory=ToolRenderState)
    message: MessageState = field(default_factory=MessageState)
    assistant_message: Dict[str, Any] = field(default_factory=_empty_assistant_message)
    tool_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def reset(self) -> None:
        """Reset per-stream state while keeping placeholder references."""
        self.reasoning = ReasoningState()
        self.cot = COTState()
        self.tools = ToolRenderState()
        self.message = MessageState()
        self.assistant_message = _empty_assistant_message()
        self.tool_map = {}

    # Convenience aliases for legacy attribute access ---------------------
    @property
    def status_placeholder(self) -> Any:
        return self.placeholders.status

    @status_placeholder.setter
    def status_placeholder(self, value: Any) -> None:
        self.placeholders.status = value

    @property
    def tool_placeholder(self) -> Any:
        return self.placeholders.tool

    @tool_placeholder.setter
    def tool_placeholder(self, value: Any) -> None:
        self.placeholders.tool = value

    @property
    def chain_placeholder(self) -> Any:
        return self.placeholders.chain

    @chain_placeholder.setter
    def chain_placeholder(self, value: Any) -> None:
        self.placeholders.chain = value

    @property
    def response_placeholder(self) -> Any:
        return self.placeholders.response

    @response_placeholder.setter
    def response_placeholder(self, value: Any) -> None:
        self.placeholders.response = value

    @property
    def message_container(self) -> Any:
        return self.placeholders.message_container

    @message_container.setter
    def message_container(self, value: Any) -> None:
        self.placeholders.message_container = value
