"""UI component management for Streamlit."""

from typing import List, Dict, Any
import streamlit as st

from .config import AppConfig
from .utils.message_renderer import MessageRenderer


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

    def get_user_input(self) -> str:
        """Get user input from chat input widget."""
        return st.chat_input(self.config.chat_input_placeholder)

    def create_chat_container(self):
        """Create and return a chat message container."""
        return st.chat_message("assistant")