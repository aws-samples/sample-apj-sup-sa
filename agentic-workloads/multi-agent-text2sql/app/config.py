"""Application configuration settings.

This module provides centralized configuration management including:
- Streamlit page configuration
- Available AI models and default selection
- UI settings (titles, placeholders)
- Agent factory configuration and validation
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable

from .env_loader import EnvLoader


@dataclass
class AppConfig:
    """Central configuration for the Streamlit application."""

    # Streamlit page configuration
    page_config: Dict[str, Any] = None

    # Available models
    available_models: List[str] = None

    # Default model
    default_model: str = "openai.gpt-oss-120b-1:0"

    # UI settings
    app_title: str = "🤖 Amazon Bedrock Agent Chat"
    sidebar_header: str = "🔧 Model Settings"
    chat_input_placeholder: str = "Ask me anything..."

    # Agent factory configuration
    agent_factory: Optional[Callable[[str], Any]] = None
    agent_factory_kwargs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Load environment variables
        env = EnvLoader()

        if self.page_config is None:
            self.page_config = {
                "page_title": "Strands Agent Chat",
                "page_icon": "🤖",
                "layout": "wide",
            }

        if self.available_models is None:
            self.available_models = [
                "openai.gpt-oss-120b-1:0",
                "us.amazon.nova-pro-v1:0",
                "us.amazon.nova-premier-v1:0",
                "us.anthropic.claude-sonnet-4-20250514-v1:0",
                "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            ]

        # Override default model from environment if specified
        env_default_model = env.get('DEFAULT_MODEL')
        if env_default_model and env_default_model in self.available_models:
            self.default_model = env_default_model

    def get_default_model_index(self) -> int:
        """Get the index of the default model in the available models list."""
        try:
            return self.available_models.index(self.default_model)
        except ValueError:
            return 0

    def get_default_agent_factory(self) -> Callable[[str], Any]:
        """Return the default StrandsAgent factory function for backward compatibility."""
        from agents.strands_agent import StrandsAgent
        
        def default_factory(model_id: str) -> StrandsAgent:
            return StrandsAgent(model_id=model_id)
        
        return default_factory

    def create_agent(self, model_id: str) -> Any:
        """Create an agent instance using the configured factory or default to StrandsAgent.
        
        Args:
            model_id: The model identifier to use for agent creation
            
        Returns:
            An agent instance compatible with the Streamlit frontend
            
        Raises:
            TypeError: If agent_factory is not callable
            ValueError: If agent creation fails or agent is incompatible
            RuntimeError: If agent lacks required methods or attributes
        """
        # Validate agent factory is callable
        if self.agent_factory is None:
            # Use default StrandsAgent factory for backward compatibility
            factory = self.get_default_agent_factory()
        else:
            if not callable(self.agent_factory):
                raise TypeError(
                    f"agent_factory must be callable, got {type(self.agent_factory).__name__}"
                )
            factory = self.agent_factory
        
        # Create agent with factory, passing any additional kwargs
        agent_instance = None
        try:
            if self.agent_factory_kwargs:
                # If factory accepts kwargs, pass them along
                try:
                    agent_instance = factory(model_id, **self.agent_factory_kwargs)
                except TypeError as e:
                    # Factory doesn't accept kwargs, just pass model_id
                    if "unexpected keyword argument" in str(e) or "got an unexpected" in str(e):
                        agent_instance = factory(model_id)
                    else:
                        raise ValueError(
                            f"Agent factory failed with TypeError: {str(e)}"
                        ) from e
            else:
                agent_instance = factory(model_id)
        except Exception as e:
            if isinstance(e, (TypeError, ValueError, RuntimeError)):
                raise
            raise ValueError(
                f"Agent factory raised unexpected exception: {type(e).__name__}: {str(e)}"
            ) from e
        
        # Validate the created agent has required interface
        self._validate_agent_interface(agent_instance)
        
        # Register the StreamlitUIHandler for UI event processing
        self._register_ui_handler(agent_instance)
        
        return agent_instance
    
    def _validate_agent_interface(self, agent_instance: Any) -> None:
        """Validate that an agent instance has the required interface.
        
        Args:
            agent_instance: The agent instance to validate
            
        Raises:
            RuntimeError: If agent lacks required methods or attributes
        """
        required_methods = ['stream_response', 'get_ui_state']
        required_attributes = ['event_registry']
        
        missing_methods = []
        missing_attributes = []
        
        # Check for required methods
        for method_name in required_methods:
            if not hasattr(agent_instance, method_name):
                missing_methods.append(method_name)
            elif not callable(getattr(agent_instance, method_name)):
                raise RuntimeError(
                    f"Agent has attribute '{method_name}' but it is not callable. "
                    f"The Streamlit frontend requires '{method_name}' to be a method."
                )
        
        # Check for required attributes
        for attr_name in required_attributes:
            if not hasattr(agent_instance, attr_name):
                missing_attributes.append(attr_name)
        
        # Raise error if anything is missing
        if missing_methods or missing_attributes:
            error_parts = []
            if missing_methods:
                error_parts.append(f"missing methods: {', '.join(missing_methods)}")
            if missing_attributes:
                error_parts.append(f"missing attributes: {', '.join(missing_attributes)}")
            
            raise RuntimeError(
                f"Agent instance is incompatible with Streamlit frontend - "
                f"{'; '.join(error_parts)}. "
                f"Required interface: stream_response(), get_ui_state(), event_registry attribute."
            )
    
    def _register_ui_handler(self, agent_instance: Any) -> None:
        """Register the StreamlitUIHandler with the agent's event registry.
        
        Args:
            agent_instance: The agent instance to register the UI handler with
        """
        from app.events.handlers import StreamlitUIHandler
        
        # Get the UI state from the agent
        ui_state = agent_instance.get_ui_state()
        
        # Create and register the StreamlitUIHandler
        ui_handler = StreamlitUIHandler(ui_state)
        agent_instance.event_registry.register(ui_handler)