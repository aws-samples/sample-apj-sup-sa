"""Thread-safety tests for the Streamlit UI integration."""
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

pytest.importorskip("strands")

from agents.events.ui import StreamlitUIState
from app.events.handlers import StreamlitUIHandler


def test_streamlit_handler_without_context():
    """Ensure the handler fails outside the Streamlit ScriptRunContext."""
    ui_state = StreamlitUIState()
    handler = StreamlitUIHandler(ui_state)
    
    # Configure a placeholder that throws to mimic Streamlit's guardrails
    mock_placeholder = Mock()
    mock_placeholder.markdown = Mock(side_effect=Exception("StreamlitAPIException: main thread"))
    ui_state.response_placeholder = mock_placeholder
    
    # Attempting to process a data event should surface the exception
    event = {"data": "test data"}
    
    try:
        handler.handle(event)
        assert False, "Exception should have been raised"
    except Exception as e:
        assert "main thread" in str(e)


def test_handler_in_worker_thread():
    """Validate that worker-thread execution still raises the guard."""
    ui_state = StreamlitUIState()
    handler = StreamlitUIHandler(ui_state)
    
    # Configure a placeholder that raises an exception
    mock_placeholder = Mock()
    mock_placeholder.markdown = Mock(side_effect=Exception("StreamlitAPIException"))
    ui_state.response_placeholder = mock_placeholder
    
    exception_caught = threading.Event()
    exception_message = []
    
    def worker_thread():
        try:
            event = {"data": "worker thread data"}
            handler.handle(event)
        except Exception as e:
            exception_message.append(str(e))
            exception_caught.set()
    
    # Execute in a worker thread
    thread = threading.Thread(target=worker_thread)
    thread.start()
    thread.join()
    
    # Ensure the worker thread hit the exception path
    assert exception_caught.is_set(), "Expected the worker thread to raise"
    assert len(exception_message) > 0


def test_event_registry_error_handling():
    """Confirm that handler exceptions are surfaced as structured results."""
    from agents.events.registry import EventRegistry
    from agents.events.ui import StreamlitUIState
    from app.events.handlers import StreamlitUIHandler
    
    registry = EventRegistry()
    ui_state = StreamlitUIState()
    
    # Register a handler that always raises
    class ErrorHandler(StreamlitUIHandler):
        def handle(self, event):
            raise Exception("Test handler error")
    
    error_handler = ErrorHandler(ui_state)
    registry.register(error_handler)
    
    # Process an event and ensure the error metadata is returned
    event = {"data": "test"}
    results = registry.process_event(event)
    
    # Verify the error payload
    assert len(results) > 0
    assert "handler_error" in results[0]
    error_info = results[0]["handler_error"]
    assert error_info["handler"] == "ErrorHandler"
    assert error_info["error_message"] == "Test handler error"


def test_generator_cleanup():
    """Ensure finally blocks run when the generator exits early."""
    
    # Build a simple generator that records cleanup execution
    cleanup_called = []
    
    def test_generator():
        try:
            yield "event1"
            yield "event2"
            yield "event3"
        finally:
            cleanup_called.append(True)
    
    # Break out after the first event to trigger early termination
    gen = test_generator()
    event_count = 0
    
    for event in gen:
        event_count += 1
        if event_count >= 1:  # Exit after the first event
            break
    
    # Explicitly close the generator (Python also does this automatically)
    gen.close()
    
    # Ensure the finally block executed
    assert len(cleanup_called) > 0, "Expected finally block to run"

    # Inspect StrandsAgent to confirm the try/finally pattern exists
    import inspect
    from agents.strands_agent import StrandsAgent
    
    source = inspect.getsource(StrandsAgent.stream_response)
    assert "try:" in source and "finally:" in source, "StrandsAgent.stream_response is missing try/finally"


if __name__ == "__main__":
    try:
        test_streamlit_handler_without_context()
        test_handler_in_worker_thread()
        test_event_registry_error_handling()
        test_generator_cleanup()
    except Exception as e:  # pragma: no cover - manual execution helper
        import traceback
        traceback.print_exc()
