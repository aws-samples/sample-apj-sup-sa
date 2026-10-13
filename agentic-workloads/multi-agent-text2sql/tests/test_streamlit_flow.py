"""Automated tests for the Streamlit event flow."""
from pathlib import Path
import sys

import pytest
from unittest.mock import Mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("strands")

from agents.strands_agent import StrandsAgent
from agents.events.ui import StreamlitUIState
from agents.events.ui import messages as messages_module
from agents.events.ui import placeholders as placeholders_module
from agents.events.ui import reasoning as reasoning_module
from agents.events.ui import tools as tools_module
from agents.events.ui import utils as utils_module
from app.events import handlers as ui_handlers_module
from app.events.handlers import StreamlitUIHandler


class MockPlaceholder:
    """Simple mock that mimics Streamlit placeholders."""

    def __init__(self, name):
        self.name = name
        self.content = ""
        self.markdown_calls = []
        self.empty_calls = 0

    def markdown(self, content):
        self.content = content
        self.markdown_calls.append(content)

    def empty(self):
        self.empty_calls += 1
        self.content = ""

    def container(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class MockExpander:
    """Mock replacement for st.expander supporting empty() calls."""

    def __init__(self, name):
        self.name = name
        self.children = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def empty(self):
        placeholder = MockPlaceholder(f"{self.name}-child-{len(self.children)}")
        self.children.append(placeholder)
        return placeholder

class TestStreamlitUIState:
    """Tests that validate StreamlitUIState behaviour."""
    
    def test_placeholder_persistence_after_reset(self):
        """reset() should keep placeholder references intact."""
        ui_state = StreamlitUIState()
        
        # Provide placeholder references
        response_placeholder = MockPlaceholder("response")
        tool_placeholder = MockPlaceholder("tool")
        ui_state.response_placeholder = response_placeholder
        ui_state.tool_placeholder = tool_placeholder
        
        # Seed message state
        ui_state.message.raw_response = "test response"
        ui_state.tool_map = {"tool1": "data"}
        
        # Call reset
        ui_state.reset()
        
        # Placeholders remain while state resets
        assert ui_state.response_placeholder is response_placeholder
        assert ui_state.tool_placeholder is tool_placeholder
        assert ui_state.message.raw_response == ""
        assert ui_state.tool_map == {}


class TestStreamlitUIHandler:
    """Tests around StreamlitUIHandler behaviour."""
    
    def setup_method(self):
        """Construct a handler with mock placeholders."""
        self.ui_state = StreamlitUIState()
        self.handler = StreamlitUIHandler(self.ui_state)
        
        # Construct mock placeholders
        self.response_placeholder = MockPlaceholder("response")
        self.tool_placeholder = MockPlaceholder("tool")
        self.status_placeholder = MockPlaceholder("status")
        self.chain_placeholder = MockPlaceholder("chain")
        
        self.handler.set_placeholders(
            self.status_placeholder,
            self.tool_placeholder,
            self.chain_placeholder,
            self.response_placeholder
        )
        # Provide a shared Streamlit mock for all UI modules
        streamlit_mock = Mock()

        def _make_expander(label, *args, **kwargs):
            return MockExpander(label)

        def _make_status(label, *args, **kwargs):
            status = Mock()
            status.update = Mock()
            status.label = label
            return status

        streamlit_mock.expander.side_effect = _make_expander
        streamlit_mock.status.side_effect = _make_status
        streamlit_mock.empty.side_effect = lambda *a, **k: MockPlaceholder("empty")
        streamlit_mock.json = Mock()
        streamlit_mock.code = Mock()
        streamlit_mock.write = Mock()
        streamlit_mock.markdown = Mock()

        ui_handlers_module.st = streamlit_mock
        reasoning_module.st = streamlit_mock
        tools_module.st = streamlit_mock
        messages_module.st = streamlit_mock
        utils_module.st = streamlit_mock
        placeholders_module.st = streamlit_mock

        self.streamlit_mock = streamlit_mock
    
    def test_can_handle_ui_events(self):
        """The handler should accept relevant event types."""
        assert self.handler.can_handle("data") == True
        assert self.handler.can_handle("reasoningText") == True
        assert self.handler.can_handle("current_tool_use") == True
        assert self.handler.can_handle("tool_result") == True
        assert self.handler.can_handle("result") == True
        assert self.handler.can_handle("force_stop") == True
        assert self.handler.can_handle("unknown_event") == False
    
    def test_handle_data_event(self):
        """Streaming data should update the response placeholder."""
        # COTUIManager buffers initial 20 characters to detect thinking blocks
        # Send enough data to exceed the buffer size
        test_data = "Hello World - this is a longer message to exceed buffer"
        event = {"data": test_data}
        self.handler.handle(event)
        
        # Ensure state was updated with raw response
        assert test_data in self.ui_state.message.raw_response
        # After buffer is flushed, placeholder should be updated
        assert len(self.response_placeholder.markdown_calls) > 0
        # The filtered response should contain the text (with cursor)
        assert test_data in self.response_placeholder.markdown_calls[-1]
    
    def test_handle_reasoning_event(self):
        """Reasoning events should append to the running text."""
        # Process a reasoning event
        event = {"reasoningText": "Thinking..."}
        self.handler.handle(event)

        # Reasoning text should accumulate
        assert "Thinking..." in self.ui_state.reasoning.text
        assert self.ui_state.reasoning.status is not None
        self.ui_state.reasoning.status.update.assert_called_with(
            label="🧠 Reasoning in progress…",
            state="running",
            expanded=True,
        )

    def test_tool_result_does_not_backfill_input(self):
        """tool_result 이벤트만으로는 입력이 채워지지 않는다."""
        self.handler.handle({
            "current_tool_use": {
                "toolUseId": "tool-1",
                "name": "calculator",
            }
        })

        self.handler.handle({
            "tool_result": {
                "toolUseId": "tool-1",
                "output": "2",
                "input": {"expression": "1+1"},
            }
        })

        entry = self.ui_state.tool_map["tool-1"]
        assert entry["input"] is None

    def test_handle_current_tool_use_with_arguments_field(self):
        """OpenAI/GPT models use 'arguments' instead of 'input' field."""
        # Test with 'arguments' field (OpenAI/GPT style)
        event = {
            "current_tool_use": {
                "tool_use_id": "tool-gpt-1",  # snake_case for OpenAI
                "name": "calculator",
                "arguments": '{"expression": "2+2"}',  # OpenAI uses 'arguments'
            }
        }
        self.handler.handle(event)

        # Verify the arguments field was processed correctly
        entry = self.ui_state.tool_map["tool-gpt-1"]
        assert entry["input"] is not None
        assert entry["input_is_json"] is True
        assert "expression" in str(entry["input"]) or "2+2" in str(entry["input"])

    def test_handle_current_tool_use_with_input_field(self):
        """Anthropic/Nova models use 'input' field."""
        # Test with 'input' field (Anthropic/Nova style)
        event = {
            "current_tool_use": {
                "toolUseId": "tool-nova-1",  # camelCase for Anthropic/Nova
                "name": "calculator",
                "input": {"expression": "3+3"},  # Anthropic/Nova uses 'input'
            }
        }
        self.handler.handle(event)

        # Verify the input field was processed correctly
        entry = self.ui_state.tool_map["tool-nova-1"]
        assert entry["input"] is not None
        assert entry["input_is_json"] is True
        assert "expression" in str(entry["input"]) or "3+3" in str(entry["input"])

    def test_handle_force_stop_event(self):
        """Force-stop events should store the error message."""
        event = {"force_stop": True, "force_stop_reason": "Test error"}
        self.handler.handle(event)
        
        # Verify the error was captured and rendered
        assert self.ui_state.message.force_stop_error == "Error: Test error"
        assert len(self.response_placeholder.markdown_calls) > 0
        assert "Test error" in self.response_placeholder.markdown_calls[-1]
    
    def test_placeholder_none_handling(self):
        """Handler should safely skip None placeholders without crashing."""
        # Drop the placeholder reference
        self.ui_state.response_placeholder = None
        
        # Send enough data to exceed COT buffer
        test_data = "Test data that exceeds the buffer size limit"
        event = {"data": test_data}
        result = self.handler.handle(event)
        
        # Handler should return None (meaning handled internally)
        assert result is None
        # Raw response is still updated even without placeholder
        # (placeholder only affects rendering, not state storage)
        assert test_data in self.ui_state.message.raw_response

    def test_metrics_backfills_input_when_tool_result_missing(self):
        """Agent metrics should backfill tool input when the result event lacks it."""

        class FakeToolMetrics:
            def __init__(self, tool):
                self.tool = tool

        class FakeMetrics:
            def __init__(self, tool_metrics):
                self.tool_metrics = tool_metrics

        class FakeResult:
            def __init__(self, metrics):
                self.metrics = metrics
                self.message = {"content": []}

        # Register tool invocation without input information
        self.handler.handle({
            "current_tool_use": {
                "toolUseId": "tool-2",
                "name": "calculator",
            }
        })

        tool_info = {
            "toolUseId": "tool-2",
            "name": "calculator",
            "input": {"expression": "2+2"},
        }
        metrics = FakeMetrics({"calculator": FakeToolMetrics(tool_info)})
        fake_result = FakeResult(metrics)

        self.handler.handle({"result": fake_result})

        entry = self.ui_state.tool_map["tool-2"]
        assert entry["input"] == {"expression": "2+2"}
        assert entry["input_is_json"] is True

    def test_finalize_updates_progress_blocks(self):
        """Finalization should complete status blocks and render content."""

        # Create a proper mock status that supports context manager
        class MockStatus:
            def __init__(self, label, **kwargs):
                self.label = label
                self.state = kwargs.get("state", "running")
                self.update = Mock()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        status_calls = []
        def _make_status(label, *args, **kwargs):
            status = MockStatus(label, **kwargs)
            status_calls.append({"label": label, "kwargs": kwargs})
            return status

        self.streamlit_mock.status.side_effect = _make_status

        # Simulate streaming events
        self.handler.handle({"reasoningText": "Reasoning chunk"})
        self.handler.handle({"current_tool_use": {"toolUseId": "tool-3", "name": "calculator"}})
        self.handler.handle({"tool_result": {"toolUseId": "tool-3", "output": "4"}})
        # Send enough data to exceed COT buffer
        self.handler.handle({"data": "Final answer with enough text to exceed buffer"})

        tool_info = {"toolUseId": "tool-3", "name": "calculator", "input": {"expression": "2+2"}}
        metrics = type("FakeMetrics", (), {"tool_metrics": {"calculator": type("FakeToolMetrics", (), {"tool": tool_info})()}})()
        fake_result = type("FakeResult", (), {"metrics": metrics, "message": {"content": [{"text": "Final answer"}]}})()

        self.handler.handle({"result": fake_result})
        self.handler.finalize_response()

        # Verify reasoning status was updated
        assert self.ui_state.reasoning.status is not None
        self.ui_state.reasoning.status.update.assert_called_with(
            label="🧠 Reasoning",
            state="complete",
            expanded=False,
        )

        # Verify tool status widgets were created (current implementation uses st.status, not st.expander)
        status_labels = [call["label"] for call in status_calls]
        # Tool status should include calculator tool
        tool_status_found = any("calculator" in label for label in status_labels)
        assert tool_status_found, f"Expected calculator tool status, got: {status_labels}"


class TestStrandsAgentIntegration:
    """Integration-style tests around StrandsAgent.
    
    Note: In the current architecture, StreamlitUIHandler is managed in the app/ layer,
    not in the agent layer. These tests verify the agent's core functionality without
    UI handler integration.
    """
    
    def setup_method(self):
        """Instantiate the agent for each test."""
        self.agent = StrandsAgent()
    
    def test_ui_state_persistence(self):
        """The agent should reuse the same UI state instance."""
        # Keep the original reference ID
        initial_ui_state = self.agent.get_ui_state()
        initial_id = id(initial_ui_state)
        
        # Set placeholders directly on ui_state (as app layer would do)
        response_placeholder = MockPlaceholder("response")
        self.agent.ui_state.response_placeholder = response_placeholder
        
        # Simulate the reset step of the stream
        self.agent.ui_state.reset()
        
        # The identity stays the same and placeholders remain after reset
        assert id(self.agent.get_ui_state()) == initial_id
        assert self.agent.get_ui_state().response_placeholder is response_placeholder
    
    def test_handler_registration(self):
        """Verify core (non-UI) handlers are registered.
        
        Note: StreamlitUIHandler is registered in the app/ layer, not here.
        The agent layer only registers non-UI handlers to maintain layer separation.
        """
        handler_types = [type(h).__name__ for h in self.agent.event_registry._handlers]
        
        # Confirm the expected non-UI handler types are present
        # StreamlitUIHandler is NOT registered here (it's in app/ layer)
        assert "LifecycleHandler" in handler_types
        assert "ReasoningHandler" in handler_types
        assert "LoggingHandler" in handler_types
        assert "DebugHandler" in handler_types
    
    def test_event_processing_flow(self):
        """Processing events through the registry should work without UI handler.
        
        Note: UI state updates happen through StreamlitUIHandler in app/ layer.
        This test verifies the event registry processes events without errors.
        """
        # Process a synthetic streaming event
        test_event = {"data": "Test streaming text"}
        results = self.agent.event_registry.process_event(test_event)
        
        # Event should be processed without errors
        # Results contain handler responses (non-UI handlers don't update ui_state)
        assert results is not None


class TestEventRegistry:
    """Tests for EventRegistry helpers."""
    
    def test_event_type_extraction(self):
        """Event type extraction should respect the priority order."""
        from agents.events.registry import EventRegistry
        
        registry = EventRegistry()
        
        # Validate extraction order
        assert registry._extract_event_type({"data": "text", "other": "value"}) == "data"
        assert registry._extract_event_type({"current_tool_use": {}, "event": {}}) == "current_tool_use"
        assert registry._extract_event_type({"unknown": "value"}) == "unknown"


if __name__ == "__main__":
    # Execute a lightweight manual test run
    import sys
    
    def run_tests():
        """Execute the test methods without pytest."""
        test_classes = [
            TestStreamlitUIState,
            TestStreamlitUIHandler, 
            TestStrandsAgentIntegration,
            TestEventRegistry
        ]
        
        total_tests = 0
        passed_tests = 0
        
        for test_class in test_classes:
            instance = test_class()

            # Run setup_method if the class defines one
            if hasattr(instance, 'setup_method'):
                instance.setup_method()

            # Execute every test_* method
            for method_name in dir(instance):
                if method_name.startswith('test_'):
                    total_tests += 1
                    try:
                        method = getattr(instance, method_name)
                        method()
                        passed_tests += 1
                    except Exception as e:
                        pass

        return passed_tests == total_tests
    
    success = run_tests()
    sys.exit(0 if success else 1)
