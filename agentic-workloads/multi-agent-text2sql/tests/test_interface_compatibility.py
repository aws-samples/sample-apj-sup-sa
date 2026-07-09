"""인터페이스 호환성 테스트

Requirements:
- 5.1: 기존 MyCustomAgent 인터페이스의 stream_response 메서드 제공
- 5.2: 기존 get_ui_state 메서드 제공
- 5.3: 기존 이벤트 시스템과 호환되는 콜백 제공
- 5.4: 모든 에이전트의 디버그 정보 통합
- 5.5: MCP 클라이언트 접근 관리
"""

import pytest
from unittest.mock import MagicMock, patch
from typing import Generator, Dict, Any

from agents.events.ui import StreamlitUIState
from agents.events.registry import EventRegistry, EventHandler
from agents.events.lifecycle import DebugHandler


class TestStreamResponseInterface:
    """stream_response 인터페이스 테스트 (Requirements 5.1)"""
    
    def test_multi_agent_has_stream_response_method(self):
        """MultiAgentText2SQL이 stream_response 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'stream_response')
    
    def test_stream_response_returns_generator(self):
        """stream_response가 Generator를 반환하는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        import inspect
        
        # 메서드 시그니처 확인
        method = getattr(MultiAgentText2SQL, 'stream_response')
        hints = method.__annotations__
        
        # return 타입이 Generator인지 확인
        assert 'return' in hints
        # Generator[Dict[str, Any], None, None] 형태인지 확인
        return_type = hints['return']
        assert 'Generator' in str(return_type)


class TestGetUIStateInterface:
    """get_ui_state 인터페이스 테스트 (Requirements 5.2)"""
    
    def test_multi_agent_has_get_ui_state_method(self):
        """MultiAgentText2SQL이 get_ui_state 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'get_ui_state')
    
    def test_get_ui_state_returns_streamlit_ui_state(self):
        """get_ui_state가 StreamlitUIState를 반환하는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        
        # 메서드 시그니처 확인
        method = getattr(MultiAgentText2SQL, 'get_ui_state')
        hints = method.__annotations__
        
        assert 'return' in hints
        assert hints['return'] == StreamlitUIState


class TestEventSystemCompatibility:
    """이벤트 시스템 호환성 테스트 (Requirements 5.3)"""
    
    def test_multi_agent_has_event_registry(self):
        """MultiAgentText2SQL이 event_registry를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        
        # 클래스 인스턴스 생성 없이 __init__ 확인
        import inspect
        source = inspect.getsource(MultiAgentText2SQL.__init__)
        assert 'event_registry' in source
    
    def test_multi_agent_has_callback_handler_method(self):
        """MultiAgentText2SQL이 _callback_handler 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, '_create_callback_handler')
    
    def test_multi_agent_has_set_callback_handler_method(self):
        """MultiAgentText2SQL이 set_callback_handler 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'set_callback_handler')
    
    def test_multi_agent_has_remove_callback_handler_method(self):
        """MultiAgentText2SQL이 remove_callback_handler 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'remove_callback_handler')
    
    def test_multi_agent_has_get_event_registry_method(self):
        """MultiAgentText2SQL이 get_event_registry 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'get_event_registry')
    
    def test_multi_agent_has_register_event_handler_method(self):
        """MultiAgentText2SQL이 register_event_handler 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'register_event_handler')


class TestDebugModeIntegration:
    """디버그 모드 통합 테스트 (Requirements 5.4)"""
    
    def test_multi_agent_has_enable_debug_mode_method(self):
        """MultiAgentText2SQL이 enable_debug_mode 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'enable_debug_mode')
    
    def test_multi_agent_has_is_debug_enabled_method(self):
        """MultiAgentText2SQL이 is_debug_enabled 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'is_debug_enabled')
    
    def test_multi_agent_has_get_debug_info_method(self):
        """MultiAgentText2SQL이 get_debug_info 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'get_debug_info')


class TestMCPClientManagement:
    """MCP 클라이언트 접근 관리 테스트 (Requirements 5.5)"""
    
    def test_multi_agent_has_get_mcp_client_method(self):
        """MultiAgentText2SQL이 get_mcp_client 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'get_mcp_client')
    
    def test_multi_agent_has_is_mcp_client_active_method(self):
        """MultiAgentText2SQL이 is_mcp_client_active 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'is_mcp_client_active')


class TestInterfaceSignatureCompatibility:
    """인터페이스 시그니처 호환성 테스트"""
    
    def test_stream_response_signature_matches_my_custom_agent(self):
        """stream_response 시그니처가 MyCustomAgent와 일치하는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        from agents.my_custom_agent import MyCustomAgent
        import inspect
        
        multi_agent_sig = inspect.signature(MultiAgentText2SQL.stream_response)
        custom_agent_sig = inspect.signature(MyCustomAgent.stream_response)
        
        # 파라미터 이름 비교 (self 제외)
        multi_params = list(multi_agent_sig.parameters.keys())
        custom_params = list(custom_agent_sig.parameters.keys())
        
        assert multi_params == custom_params
    
    def test_get_ui_state_signature_matches_my_custom_agent(self):
        """get_ui_state 시그니처가 MyCustomAgent와 일치하는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        from agents.my_custom_agent import MyCustomAgent
        import inspect
        
        multi_agent_sig = inspect.signature(MultiAgentText2SQL.get_ui_state)
        custom_agent_sig = inspect.signature(MyCustomAgent.get_ui_state)
        
        # 파라미터 이름 비교 (self 제외)
        multi_params = list(multi_agent_sig.parameters.keys())
        custom_params = list(custom_agent_sig.parameters.keys())
        
        assert multi_params == custom_params
    
    def test_enable_debug_mode_signature_matches_my_custom_agent(self):
        """enable_debug_mode 시그니처가 MyCustomAgent와 일치하는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        from agents.my_custom_agent import MyCustomAgent
        import inspect
        
        multi_agent_sig = inspect.signature(MultiAgentText2SQL.enable_debug_mode)
        custom_agent_sig = inspect.signature(MyCustomAgent.enable_debug_mode)
        
        # 파라미터 이름 비교 (self 제외)
        multi_params = list(multi_agent_sig.parameters.keys())
        custom_params = list(custom_agent_sig.parameters.keys())
        
        assert multi_params == custom_params


class TestWorkflowStatusInterface:
    """워크플로우 상태 인터페이스 테스트"""
    
    def test_multi_agent_has_get_workflow_status_method(self):
        """MultiAgentText2SQL이 get_workflow_status 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'get_workflow_status')
    
    def test_multi_agent_has_get_analysis_context_method(self):
        """MultiAgentText2SQL이 get_analysis_context 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'get_analysis_context')
    
    def test_multi_agent_has_reset_context_method(self):
        """MultiAgentText2SQL이 reset_context 메서드를 가지는지 확인"""
        from agents.multi_agent.multi_agent_text2sql import MultiAgentText2SQL
        assert hasattr(MultiAgentText2SQL, 'reset_context')
