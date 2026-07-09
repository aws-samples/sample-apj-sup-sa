"""Streamlit session state management.

This module manages the Streamlit session state including:
- Chat message history
- Current agent instance
- Model selection and switching
"""

from typing import List, Dict, Any, Optional
import streamlit as st

from .config import AppConfig


class SessionManager:
    """Manage Streamlit session state for the chat application."""

    def __init__(self, config: AppConfig):
        """Initialize SessionManager with application configuration.
        
        Args:
            config: AppConfig instance containing agent factory and other settings
        """
        self.config = config
        self._initialize_session_state()

    def _initialize_session_state(self) -> None:
        """Initialize session state if not already present."""
        if "messages" not in st.session_state:
            st.session_state.messages = []

        if "agent" not in st.session_state:
            st.session_state.agent = None

        if "current_model" not in st.session_state:
            st.session_state.current_model = None

    @property
    def messages(self) -> List[Dict[str, Any]]:
        """Get the chat messages from session state."""
        return st.session_state.messages

    @property
    def agent(self) -> Optional[Any]:
        """Get the current agent from session state."""
        return st.session_state.agent

    @property
    def current_model(self) -> Optional[str]:
        """Get the current model from session state."""
        return st.session_state.current_model

    def add_message(self, role: str, content: Any) -> None:
        """Add a message to the chat history."""
        st.session_state.messages.append({"role": role, "content": content})

    def handle_model_change(self, selected_model: str) -> bool:
        """Handle model selection changes. Returns True if model was changed.
        
        Args:
            selected_model: The model ID to switch to
            
        Returns:
            bool: True if model was changed, False otherwise
            
        Note:
            If agent creation fails, displays error in Streamlit UI and keeps
            the previous agent (if any) active.
        """
        # First time initialization
        if st.session_state.current_model is None:
            try:
                st.session_state.current_model = selected_model
                st.session_state.agent = self.config.create_agent(selected_model)
                return True
            except Exception as e:
                # Clear the model since initialization failed
                st.session_state.current_model = None
                st.session_state.agent = None
                self._display_agent_error(e, selected_model)
                return False

        # Model changed - reset session
        if st.session_state.current_model != selected_model:
            # Store previous agent in case new one fails
            previous_model = st.session_state.current_model
            previous_agent = st.session_state.agent
            
            try:
                st.session_state.current_model = selected_model
                st.session_state.agent = self.config.create_agent(selected_model)
                st.session_state.messages = []
                st.rerun()
                return True
            except Exception as e:
                # Restore previous agent on failure
                st.session_state.current_model = previous_model
                st.session_state.agent = previous_agent
                self._display_agent_error(e, selected_model)
                return False

        return False
    
    def _display_agent_error(self, error: Exception, model_id: str) -> None:
        """Display a user-friendly error message for agent creation failures.
        
        Args:
            error: The exception that occurred
            model_id: The model ID that failed to initialize
        """
        error_type = type(error).__name__
        error_msg = str(error)
        
        # Create user-friendly error message based on error type
        if isinstance(error, TypeError):
            st.error(
                f"⚠️ **Agent Configuration Error**\n\n"
                f"The agent factory function is not properly configured.\n\n"
                f"**Details:** {error_msg}\n\n"
                f"**Solution:** Ensure your agent_factory is a callable function "
                f"that accepts a model_id parameter."
            )
        elif isinstance(error, RuntimeError):
            st.error(
                f"⚠️ **Agent Compatibility Error**\n\n"
                f"The agent created for model `{model_id}` is not compatible "
                f"with the Streamlit frontend.\n\n"
                f"**Details:** {error_msg}\n\n"
                f"**Solution:** Ensure your agent implements the required interface:\n"
                f"- `stream_response(user_input)` method\n"
                f"- `get_ui_state()` method\n"
                f"- `event_registry` attribute"
            )
        elif isinstance(error, ValueError):
            st.error(
                f"⚠️ **Agent Creation Failed**\n\n"
                f"Failed to create agent for model `{model_id}`.\n\n"
                f"**Details:** {error_msg}\n\n"
                f"**Solution:** Check your agent factory implementation and "
                f"ensure it can successfully create agents with the provided model_id."
            )
        else:
            # Generic error message for unexpected errors
            st.error(
                f"⚠️ **Unexpected Error**\n\n"
                f"An unexpected error occurred while creating the agent.\n\n"
                f"**Error Type:** {error_type}\n"
                f"**Details:** {error_msg}\n\n"
                f"**Model:** {model_id}"
            )

    def get_agent_ui_state(self):
        """Get the UI state from the current agent."""
        if self.agent:
            return self.agent.get_ui_state()
        return None

    def clear_messages(self) -> None:
        """Clear all chat messages."""
        st.session_state.messages = []