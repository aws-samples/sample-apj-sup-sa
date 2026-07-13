"""Swarm 이벤트 어댑터 테스트

Requirements:
- 1.5: 작업 진행 상황 확인 시 현재 어떤 에이전트가 작업 중인지 상태 표시
- 5.3: 기존 이벤트 시스템과 호환되는 콜백 제공
"""

import queue
import pytest
from typing import Any, Dict, List

from agents.multi_agent.event_adapter import (
    SwarmEventAdapter,
    SwarmEventHandler,
    StreamlitSwarmUIHandler,
    SwarmEventType,
    StreamlitEventType,
    AgentStatusInfo,
    SwarmEventAdapterState,
)
from agents.events.registry import EventRegistry


class TestSwarmEventAdapter:
    """SwarmEventAdapter 테스트"""
    
    def setup_method(self):
        """테스트 설정"""
        self.event_queue = queue.Queue()
        self.adapter = SwarmEventAdapter(event_queue=self.event_queue)
    
    def test_init(self):
        """어댑터 초기화 테스트"""
        assert self.adapter.event_queue is not None
        assert self.adapter.state is not None
        assert self.adapter.state.current_agent is None
        assert len(self.adapter.state.agent_history) == 0
    
    def test_reset(self):
        """어댑터 리셋 테스트"""
        # 상태 변경
        self.adapter.state.current_agent = "test_agent"
        self.adapter.state.agent_history.append("test_agent")
        self.event_queue.put({"test": "event"})
        
        # 리셋
        self.adapter.reset()
        
        # 상태 확인
        assert self.adapter.state.current_agent is None
        assert len(self.adapter.state.agent_history) == 0
        assert self.event_queue.empty()
    
    def test_convert_node_start_event(self):
        """에이전트 시작 이벤트 변환 테스트 (Requirements 1.5)"""
        swarm_event = {
            "type": "multiagent_node_start",
            "node_id": "lead_agent",
            "node_type": "agent"
        }
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.AGENT_STATUS.value
        assert converted["agent"] == "lead_agent"
        assert converted["status"] == "working"
        assert "message" in converted
        assert self.adapter.state.current_agent == "lead_agent"
        assert "lead_agent" in self.adapter.state.agent_history
    
    def test_convert_node_stream_data_event(self):
        """에이전트 스트리밍 데이터 이벤트 변환 테스트"""
        swarm_event = {
            "type": "multiagent_node_stream",
            "node_id": "sql_agent",
            "event": {"data": "SELECT * FROM users"}
        }
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.TEXT_DELTA.value
        assert converted["data"] == "SELECT * FROM users"
        assert converted["agent"] == "sql_agent"
    
    def test_convert_node_stop_event(self):
        """에이전트 종료 이벤트 변환 테스트 (Requirements 1.5)"""
        # 먼저 에이전트 시작
        self.adapter.convert_event({
            "type": "multiagent_node_start",
            "node_id": "data_expert"
        })
        
        # 에이전트 종료
        swarm_event = {
            "type": "multiagent_node_stop",
            "node_id": "data_expert",
            "node_result": {"status": "success"}
        }
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.AGENT_STATUS.value
        assert converted["agent"] == "data_expert"
        assert converted["status"] == "completed"
    
    def test_convert_handoff_event(self):
        """에이전트 전환 이벤트 변환 테스트 (Requirements 1.5)"""
        # 먼저 lead_agent 시작
        self.adapter.convert_event({
            "type": "multiagent_node_start",
            "node_id": "lead_agent"
        })
        
        # handoff 이벤트
        swarm_event = {
            "type": "multiagent_handoff",
            "from_node_ids": ["lead_agent"],
            "to_node_ids": ["data_expert"],
            "message": "데이터 탐색 요청"
        }
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.AGENT_HANDOFF.value
        assert converted["from_agent"] == "lead_agent"
        assert converted["to_agent"] == "data_expert"
        assert self.adapter.state.current_agent == "data_expert"
        assert "data_expert" in self.adapter.state.agent_history
    
    def test_convert_result_event(self):
        """최종 결과 이벤트 변환 테스트"""
        swarm_event = {
            "type": "multiagent_result",
            "result": {"status": "COMPLETED", "data": "결과 데이터"}
        }
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.COMPLETE.value
        assert converted["result"] is not None
        assert self.adapter.state.is_completed is True
    
    def test_convert_data_event(self):
        """텍스트 데이터 이벤트 변환 테스트"""
        swarm_event = {"data": "Hello, World!"}
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.TEXT_DELTA.value
        assert converted["data"] == "Hello, World!"
        assert self.adapter.state.accumulated_text == "Hello, World!"
    
    def test_convert_tool_use_event(self):
        """도구 사용 이벤트 변환 테스트"""
        swarm_event = {
            "current_tool_use": {
                "toolUseId": "tool_123",
                "name": "manage_aws_athena_query_executions",
                "input": {"action": "start_query"}
            }
        }
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.TOOL_CALL.value
        assert converted["tool_name"] == "manage_aws_athena_query_executions"
        assert converted["status"] == "running"
        assert "tool_123" in self.adapter.state.tool_calls
    
    def test_convert_tool_result_event(self):
        """도구 결과 이벤트 변환 테스트"""
        # 먼저 도구 호출
        self.adapter.convert_event({
            "current_tool_use": {
                "toolUseId": "tool_456",
                "name": "test_tool",
                "input": {}
            }
        })
        
        # 도구 결과
        swarm_event = {
            "tool_result": {
                "toolUseId": "tool_456",
                "content": "도구 실행 결과",
                "status": "success"
            }
        }
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.TOOL_RESULT.value
        assert converted["result"] == "도구 실행 결과"
        assert self.adapter.state.tool_calls["tool_456"]["status"] == "completed"
    
    def test_convert_force_stop_event(self):
        """강제 중단 이벤트 변환 테스트"""
        self.adapter.state.current_agent = "sql_agent"
        self.adapter.state.agent_statuses["sql_agent"] = AgentStatusInfo(
            agent_name="sql_agent",
            status="working",
            message="작업 중"
        )
        
        swarm_event = {
            "type": "force_stop",
            "force_stop_reason": "Timeout"
        }
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.FORCE_STOP.value
        assert converted["reason"] == "Timeout"
        assert self.adapter.state.is_completed is True
        assert self.adapter.state.agent_statuses["sql_agent"].status == "error"
    
    def test_convert_reasoning_event(self):
        """추론 이벤트 변환 테스트"""
        swarm_event = {"reasoningText": "분석 중입니다..."}
        
        converted = self.adapter.convert_event(swarm_event)
        
        assert converted["type"] == StreamlitEventType.REASONING.value
        assert converted["reasoningText"] == "분석 중입니다..."
    
    def test_process_event_adds_to_queue(self):
        """process_event가 큐에 이벤트를 추가하는지 테스트 (Requirements 5.3)"""
        swarm_event = {"data": "테스트 데이터"}
        
        self.adapter.process_event(swarm_event)
        
        assert not self.event_queue.empty()
        queued_event = self.event_queue.get()
        assert queued_event["type"] == StreamlitEventType.TEXT_DELTA.value
    
    def test_process_event_calls_external_callback(self):
        """process_event가 외부 콜백을 호출하는지 테스트 (Requirements 5.3)"""
        callback_events = []
        
        def callback(**kwargs):
            callback_events.append(kwargs)
        
        self.adapter.external_callback = callback
        self.adapter.process_event({"data": "콜백 테스트"})
        
        assert len(callback_events) == 1
        assert callback_events[0]["type"] == StreamlitEventType.TEXT_DELTA.value
    
    def test_process_event_with_registry(self):
        """process_event가 이벤트 레지스트리와 통합되는지 테스트 (Requirements 5.3)"""
        registry = EventRegistry()
        adapter = SwarmEventAdapter(
            event_queue=self.event_queue,
            event_registry=registry
        )
        
        # 이벤트 처리
        adapter.process_event({"data": "레지스트리 테스트"})
        
        # 큐에 추가되었는지 확인
        assert not self.event_queue.empty()
    
    def test_get_current_status(self):
        """현재 상태 반환 테스트 (Requirements 1.5)"""
        # 에이전트 시작
        self.adapter.convert_event({
            "type": "multiagent_node_start",
            "node_id": "lead_agent"
        })
        
        status = self.adapter.get_current_status()
        
        assert status["current_agent"] == "lead_agent"
        assert "lead_agent" in status["agent_history"]
        assert "lead_agent" in status["agent_statuses"]
        assert status["is_completed"] is False
    
    def test_get_agent_progress(self):
        """에이전트 진행 상황 반환 테스트 (Requirements 1.5)"""
        # 여러 에이전트 시작
        self.adapter.convert_event({
            "type": "multiagent_node_start",
            "node_id": "lead_agent"
        })
        self.adapter.convert_event({
            "type": "multiagent_handoff",
            "from_node_ids": ["lead_agent"],
            "to_node_ids": ["data_expert"]
        })
        
        progress = self.adapter.get_agent_progress()
        
        assert len(progress) == 2
        assert progress[0]["agent"] == "lead_agent"
        assert progress[1]["agent"] == "data_expert"
        assert progress[1]["is_current"] is True
    
    def test_agent_display_names(self):
        """에이전트 표시 이름 테스트"""
        self.adapter.convert_event({
            "type": "multiagent_node_start",
            "node_id": "data_expert"
        })

        status = self.adapter.get_current_status()

        assert "Data Expert" in status["current_agent_display_name"]


class TestSwarmEventHandler:
    """SwarmEventHandler 테스트"""
    
    def setup_method(self):
        """테스트 설정"""
        self.event_queue = queue.Queue()
        self.adapter = SwarmEventAdapter(event_queue=self.event_queue)
        self.handler = SwarmEventHandler(self.adapter)
    
    def test_priority(self):
        """핸들러 우선순위 테스트"""
        assert self.handler.priority == 5
    
    def test_can_handle_swarm_events(self):
        """Swarm 이벤트 처리 가능 여부 테스트"""
        assert self.handler.can_handle("multiagent_node_start") is True
        assert self.handler.can_handle("multiagent_handoff") is True
        assert self.handler.can_handle("agent_status") is True
        assert self.handler.can_handle("data") is False
    
    def test_handle_agent_status_event(self):
        """에이전트 상태 이벤트 처리 테스트"""
        event = {
            "type": "agent_status",
            "agent": "lead_agent",
            "status": "working"
        }
        
        result = self.handler.handle(event)
        
        assert result is not None
        assert result["swarm_event_processed"] is True


class TestStreamlitSwarmUIHandler:
    """StreamlitSwarmUIHandler 테스트"""
    
    def setup_method(self):
        """테스트 설정"""
        self.event_queue = queue.Queue()
        self.adapter = SwarmEventAdapter(event_queue=self.event_queue)
        self.handler = StreamlitSwarmUIHandler(self.adapter)
    
    def test_priority(self):
        """핸들러 우선순위 테스트"""
        assert self.handler.priority == 8
    
    def test_can_handle_ui_events(self):
        """UI 이벤트 처리 가능 여부 테스트"""
        assert self.handler.can_handle("agent_status") is True
        assert self.handler.can_handle("agent_handoff") is True
        assert self.handler.can_handle("data") is False
    
    def test_handle_returns_ui_updated(self):
        """핸들러가 UI 업데이트 결과를 반환하는지 테스트"""
        event = {
            "type": "agent_status",
            "agent": "lead_agent",
            "status": "working",
            "message": "작업 중"
        }
        
        result = self.handler.handle(event)
        
        assert result is not None
        assert result["ui_updated"] is True


class TestEventAdapterIntegration:
    """이벤트 어댑터 통합 테스트"""
    
    def test_full_workflow(self):
        """전체 워크플로우 테스트 (Requirements 1.5, 5.3)"""
        event_queue = queue.Queue()
        registry = EventRegistry()
        adapter = SwarmEventAdapter(
            event_queue=event_queue,
            event_registry=registry
        )
        
        # 핸들러 등록
        handler = SwarmEventHandler(adapter)
        registry.register(handler)
        
        # 워크플로우 시뮬레이션
        events = [
            {"type": "multiagent_node_start", "node_id": "lead_agent"},
            {"type": "multiagent_node_stream", "node_id": "lead_agent", "event": {"data": "분석 중..."}},
            {"type": "multiagent_handoff", "from_node_ids": ["lead_agent"], "to_node_ids": ["data_expert"]},
            {"type": "multiagent_node_stream", "node_id": "data_expert", "event": {"data": "테이블 탐색 중..."}},
            {"type": "multiagent_handoff", "from_node_ids": ["data_expert"], "to_node_ids": ["sql_agent"]},
            {"type": "multiagent_node_stream", "node_id": "sql_agent", "event": {"data": "SELECT * FROM users"}},
            {"type": "multiagent_result", "result": {"status": "COMPLETED"}}
        ]
        
        for event in events:
            adapter.process_event(event)
        
        # 상태 확인
        status = adapter.get_current_status()
        assert status["is_completed"] is True
        assert len(status["agent_history"]) == 3
        
        # 큐에 이벤트가 추가되었는지 확인
        queued_count = 0
        while not event_queue.empty():
            event_queue.get()
            queued_count += 1
        assert queued_count == len(events)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
