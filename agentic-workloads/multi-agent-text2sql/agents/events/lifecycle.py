"""Lifecycle and logging-related event handlers.

This module provides event handlers for:
- LifecycleHandler: Application lifecycle events
- ReasoningHandler: Reasoning process events
- LoggingHandler: Structured event logging
- DebugHandler: Debug information collection
"""
from typing import Any, Dict, Optional
import os

from .registry import EventHandler, EventType


class LifecycleHandler(EventHandler):
    """Handle lifecycle events emitted by the agent."""

    @property
    def priority(self) -> int:
        return 50  # Medium priority

    def can_handle(self, event_type: str) -> bool:
        """Handle lifecycle-related event types only."""
        lifecycle_events = {
            EventType.INIT_EVENT_LOOP.value,
            EventType.START_EVENT_LOOP.value,
            EventType.START.value,
            EventType.MESSAGE.value,
            EventType.EVENT.value,
            EventType.COMPLETE.value,
        }
        return event_type in lifecycle_events
    
    def handle(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a lifecycle event and return metadata.
        
        Handles both standard format events (with "type" field) and legacy events.
        Standard format events:
        - {"type": "start"} - Agent execution starting
        - {"type": "complete", "result": "..."} - Agent execution completed
        
        Legacy events are preserved and handled as before.
        """
        # Check for standard format first
        event_type = event.get("type")
        
        # If no type field, extract from legacy format
        if not event_type:
            event_type = next(iter(event.keys()))

        return {"lifecycle_processed": event_type}


class ReasoningHandler(EventHandler):
    """Handle reasoning-related events."""
    
    @property
    def priority(self) -> int:
        return 30  # Higher priority than lifecycle
    
    def can_handle(self, event_type: str) -> bool:
        """React only to reasoning events."""
        reasoning_events = {
            EventType.REASONING.value,
            EventType.REASONING_TEXT.value,
            EventType.REASONING_SIGNATURE.value,
            EventType.REDACTED_CONTENT.value,
        }
        return event_type in reasoning_events
    
    def handle(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Record the reasoning event."""
        event_type = next(iter(event.keys()))

        return {"reasoning_processed": event_type}


class LoggingHandler(EventHandler):
    """Structured logging handler for every event."""

    def __init__(self, log_level: str = "INFO"):
        self.debug_logging = self._get_debug_setting()

    @property
    def priority(self) -> int:
        return 80  # Lower priority so other handlers execute first

    def can_handle(self, event_type: str) -> bool:
        """Log every event regardless of type."""
        return True
    
    def _get_debug_setting(self) -> bool:
        """Get debug logging setting from environment variables."""
        # Check .env file first
        try:
            from pathlib import Path
            env_file = Path(".env")
            if env_file.exists():
                with open(env_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('DEBUG_LOGGING='):
                            value = line.split('=', 1)[1].strip().strip('"').strip("'")
                            return value.lower() in ('true', '1', 'yes', 'on')
        except Exception:
            pass

        # Fallback to system environment variable
        debug_env = os.environ.get('DEBUG_LOGGING', 'false')
        return debug_env.lower() in ('true', '1', 'yes', 'on')

    def handle(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Log the event with structured metadata."""
        # Only show debug events if DEBUG_LOGGING is enabled
        if self.debug_logging:
            print(f"\n🔍 EVENT: {event}")

        # Reasoning content detection removed for cleaner output

        # Complete response logging removed for cleaner output

        return None


class DebugHandler(EventHandler):
    """Simplified debugging handler that stores recent events."""
    
    def __init__(self, debug_enabled: bool = False):
        self.debug_enabled = debug_enabled
        self.event_log = []
    
    @property
    def priority(self) -> int:
        return 95  # Lowest priority
    
    def can_handle(self, event_type: str) -> bool:
        """Only process events when debug mode is enabled."""
        return self.debug_enabled
    
    def handle(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Collect lightweight debug information."""
        if not self.debug_enabled:
            return None
        
        event_type = next(iter(event.keys()))
        
        # Append a shallow copy for debugging purposes
        self.event_log.append({
            "event_type": event_type,
            "event_data": event.copy(),
        })

        # Limit to the latest 100 events
        if len(self.event_log) > 100:
            self.event_log.pop(0)

        return None
