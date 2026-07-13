"""UI component management and utilities for Streamlit.

This module consolidates UI-related functionality including:
- UIManager: Main UI component rendering
- MessageRenderer: Assistant message rendering
- PlaceholderManager: Streamlit placeholder management
- ErrorHandler: Error display and handling
"""

from typing import List, Dict, Any, Tuple
import streamlit as st

from .config import AppConfig
from agents.events.ui.utils import parse_model_response
from agents.events.ui.messages import render_chain_of_thought
from agents.events.ui.tools import render_tool_calls


class UIManager:
    """Manage Streamlit UI components and rendering."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.message_renderer = MessageRenderer()

    def setup_page(self) -> None:
        """Configure the Streamlit page."""
        st.set_page_config(**self.config.page_config)

    def render_sidebar(self) -> str:
        """Render the sidebar and return selected model."""
        with st.sidebar:
            st.header(self.config.sidebar_header)

            selected_model = st.selectbox(
                "Select Model:",
                self.config.available_models,
                index=self.config.get_default_model_index()
            )

            return selected_model

    def render_header(self, current_model: str) -> None:
        """Render the main page header."""
        st.title(self.config.app_title)
        st.caption(f"Current model: {current_model}")

    def render_chat_history(self, messages: List[Dict[str, Any]]) -> None:
        """Render the chat history."""
        for message in messages:
            with st.chat_message(message["role"]):
                if message["role"] == "user":
                    st.markdown(message["content"])
                else:
                    self.message_renderer.render_assistant_message(message["content"])

    def get_user_input(self, session_manager=None) -> str:
        """Get user input from chat input widget or show loading state."""
        if session_manager is None:
            # Fallback to original behavior if no session_manager provided
            return st.chat_input(self.config.chat_input_placeholder)
        
        # Check agent status
        if session_manager.agent is None:
            if session_manager.current_model is None:
                # No model selected yet
                st.info("🔧 모델을 선택해주세요")
            else:
                # Model selected but agent not initialized (loading or failed)
                st.info("🔄 모델 초기화 중...")
            return None
        else:
            # Agent is ready, show normal input
            return st.chat_input(self.config.chat_input_placeholder)

    def create_chat_container(self):
        """Create and return a chat message container."""
        return st.chat_message("assistant")


class MessageRenderer:
    """Handle rendering of assistant messages in Streamlit."""

    @staticmethod
    def render_assistant_message(content: Any) -> None:
        """Render the assistant message using the helper utilities."""
        if isinstance(content, dict) and "text" in content:
            # Render intermediate outputs as expanders first
            intermediate_outputs = content.get("intermediate_outputs", [])
            for output in intermediate_outputs:
                with st.expander(output["label"], expanded=False):
                    st.markdown(output["text"])
                if output.get("done_text"):
                    st.markdown(output["done_text"])

            text = content.get("text", "")
            tool_calls = content.get("tool_calls") or []
            chain_of_thought = content.get("chain_of_thought")

            if tool_calls:
                render_tool_calls(tool_calls)
            if chain_of_thought:
                render_chain_of_thought(chain_of_thought)
            if text:
                st.markdown(text)
            elif not tool_calls and not chain_of_thought and not intermediate_outputs:
                st.markdown("*Response is empty.*")
            return

        # Fallback for string content
        text, chain_of_thought = parse_model_response(str(content))
        if chain_of_thought:
            render_chain_of_thought(chain_of_thought)
        if text:
            st.markdown(text)
        elif not chain_of_thought:
            st.markdown("*Response is empty.*")


class PlaceholderManager:
    """Manage Streamlit placeholders for the chat interface."""

    @staticmethod
    def create_chat_placeholders(message_container) -> Tuple[Any, Any, Any, Any, Any]:
        """Create dedicated placeholders inside a single container."""
        intermediate_container = message_container.container()
        status_placeholder = message_container.empty()
        tool_placeholder = message_container.empty()
        chain_placeholder = message_container.empty()
        response_placeholder = message_container.empty()

        return status_placeholder, tool_placeholder, chain_placeholder, response_placeholder, intermediate_container

    @staticmethod
    def setup_ui_handler_placeholders(agent, status_ph, tool_ph, chain_ph, response_ph) -> None:
        """Inject placeholders into the UI handler instance."""
        for handler in agent.event_registry._handlers:
            if hasattr(handler, "set_placeholders"):
                handler.set_placeholders(
                    status_ph,
                    tool_ph,
                    chain_ph,
                    response_ph,
                )
                break


class ErrorHandler:
    """Handle and display errors in the Streamlit interface."""

    @staticmethod
    def handle_streaming_error(error: Exception, status_placeholder, response_placeholder,
                             chain_placeholder) -> Dict[str, Any]:
        """Handle streaming errors and return error message for session."""
        # Clear other placeholders and show error
        status_placeholder.empty()
        chain_placeholder.empty()

        error_message = f"Error: {error}"
        response_placeholder.markdown(f":red[{error_message}]")

        # Return assistant message format for session storage
        return {
            "text": error_message,
            "chain_of_thought": None,
            "tool_calls": [],
        }

    @staticmethod
    def handle_handler_errors(results: List[Dict[str, Any]], status_placeholder) -> None:
        """Handle handler errors during event processing."""
        for result in results:
            if "handler_error" in result:
                error_info = result["handler_error"]
                status_placeholder.markdown(
                    f":red[{error_info['handler']} error: {error_info['error_message']}]"
                )

    @staticmethod
    def display_handler_error(handler_error: Exception, status_placeholder) -> None:
        """Display a general handler error."""
        status_placeholder.markdown(f":red[Handler error: {handler_error}]")
