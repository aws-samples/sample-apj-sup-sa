"""Strands Agent integration with event-driven streaming.

This module provides the StrandsAgent class that integrates the Strands
Agent framework with the Streamlit frontend through an event-driven
architecture. It handles:
- Agent initialization and configuration
- Event streaming and queue management
- Event handler registration and coordination
- Tool definitions (calculator, weather)
"""

import queue
import threading
import time
from typing import Any, Dict, Generator

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.events.registry import EventRegistry, EventType
from agents.events.lifecycle import (
    DebugHandler,
    LifecycleHandler,
    LoggingHandler,
    ReasoningHandler,
)
from agents.events.ui import StreamlitUIState

@tool
def calculator(expression: str) -> str:
    """Perform basic arithmetic calculations"""
    try:
        if any(char in expression for char in ['import', 'exec', 'eval', '__']):
            return "Error: Invalid expression"
        return str(eval(expression))
    except Exception as e:
        return f"Error: {str(e)}"

@tool  
def weather(location: str) -> str:
    """Get weather information for a location"""
    return f"Weather in {location}: Sunny, 22°C (Mock data)"

class StrandsAgent:
    def __init__(self, region: str = "us-west-2", model_id: str = "openai.gpt-oss-20b-1:0"):
        self.event_queue = queue.Queue()
        self.event_registry = EventRegistry()
        self.ui_state = StreamlitUIState()

        # Register event handlers and build the agent instance
        self._setup_handlers()

        # Create model - disable thinking for now due to tool use constraints
        # Anthropic requires thinking blocks in conversation history after tool use
        # Current Strands version (1.9.1) doesn't handle this automatically
        self.agent = Agent(
            model=model_id,
            tools=[calculator, weather],
            callback_handler=self._callback_handler
        )
    
    def _setup_handlers(self):
        """Register core handlers (non-UI handlers)."""
        self.event_registry.register(LifecycleHandler())
        self.event_registry.register(ReasoningHandler())
        self.event_registry.register(LoggingHandler(log_level="INFO"))
        self.event_registry.register(DebugHandler(debug_enabled=False))
    
    def enable_debug_mode(self, enabled: bool = True):
        """Toggle the debug handler."""
        for handler in self.event_registry._handlers:
            if isinstance(handler, DebugHandler):
                handler.debug_enabled = enabled
                break
    
    def _classify_event(self, event_data: Dict[str, Any]) -> list:
        """Return the raw event; placeholder for future routing."""
        return [event_data]

    def _callback_handler(self, **kwargs):
        """Handle streaming events from Strands Agent"""
        # Only enqueue events; processing happens on the main thread
        self.event_queue.put(kwargs)

    def drain_events(self):
        """Clear remaining events after streaming ends."""
        drained_count = 0
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
                drained_count += 1
            except queue.Empty:
                break

    def stream_response(self, user_input: str) -> Generator[Dict[str, Any], None, None]:
        """Stream response using Strands Agent with handler system"""
        
        # Clear any stale events before starting a new stream
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break

        # UI state is already reset by app.py before calling stream_response
        # Don't reset again here to preserve placeholder settings
        # self.ui_state.reset()  # Commented out to preserve placeholders

        # But we do need to reset the COT manager's internal state
        ui_handler = None
        for handler in self.event_registry._handlers:
            if hasattr(handler, 'reset_for_new_conversation'):
                handler.reset_for_new_conversation()
                break

        def run_agent():
            try:
                result = self.agent(user_input)
                self.event_queue.put({"type": "complete", "result": result})
            except Exception as e:
                self.event_queue.put({"type": "force_stop", "reason": str(e)})
        
        # Run the agent call in a background thread
        thread = threading.Thread(target=run_agent)
        thread.start()

        try:
            # Emit start event at the beginning
            yield {"type": "start"}
            
            # Yield events as they arrive from the queue
            start_time = time.time()

            while True:
                try:
                    event = self.event_queue.get(timeout=1.0)
                    
                    # Preserve the legacy event structure for downstream consumers
                    yield event

                    # Stop once the agent reports completion or a forced stop
                    if event.get("type") in ("complete", "force_stop"):
                        break

                except queue.Empty:
                    elapsed = time.time() - start_time

                    if not thread.is_alive():
                        break

                    # Abort if the agent is unresponsive for too long
                    if elapsed > 60:
                        timeout_event = {"type": "force_stop", "reason": "Timeout"}
                        yield timeout_event
                        break

                    continue

        finally:
            # Ensure the background thread and queue are cleaned up
            thread.join()
            self.drain_events()

    def get_ui_state(self) -> StreamlitUIState:
        """Expose the current UI state."""
        return self.ui_state
