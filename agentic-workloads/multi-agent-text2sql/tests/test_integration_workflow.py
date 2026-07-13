"""전체 워크플로우 통합 테스트

Requirements:
- 1.3: 공유 컨텍스트를 통해 정보 전달
- 1.5: 현재 작업 중인 에이전트 상태 표시
"""

import pytest
import queue
from unittest.mock import patch
from typing import Dict, Any, List

from agents.multi_agent.shared_context import (
    AnalysisContext,
    TableInfo,
    ColumnInfo,
)
from agents.multi_agent.event_adapter import SwarmEventAdapter


class TestSharedContextPropagation:
    """공유 컨텍스트 전파 테스트 (Requirements 1.3)"""

    @pytest.fixture
    def context(self):
        return AnalysisContext(user_query="매출 분석")

    def test_context_business_intent_propagation(self, context):
        """비즈니스 의도가 컨텍스트에 전파되는지 확인"""
        context.business_intent = {
            "entity": "product",
            "metric": "revenue"
        }

        assert context.business_intent["entity"] == "product"
        assert context.business_intent["metric"] == "revenue"

    def test_context_table_info_propagation(self, context):
        """테이블 정보가 컨텍스트에 전파되는지 확인"""
        table = TableInfo(
            database="db",
            table="sales",
            columns=[ColumnInfo(name="id", type="string")],
            partition_keys=["date"],
            relevance_score=0.9
        )
        context.identified_tables = [table]

        assert len(context.identified_tables) == 1
        assert context.identified_tables[0].database == "db"

    def test_context_sql_propagation(self, context):
        """SQL 쿼리가 컨텍스트에 전파되는지 확인"""
        context.generated_sql = "SELECT * FROM sales"

        assert context.generated_sql == "SELECT * FROM sales"

    def test_context_results_propagation(self, context):
        """쿼리 결과가 컨텍스트에 전파되는지 확인"""
        context.results = [{"id": 1}, {"id": 2}]

        assert len(context.results) == 2

    def test_context_error_propagation(self, context):
        """에러 메시지가 컨텍스트에 전파되는지 확인"""
        context.add_error("테스트 에러")

        assert len(context.error_messages) == 1
        assert "테스트 에러" in context.error_messages


class TestEventAdapterIntegration:
    """이벤트 어댑터 통합 테스트"""

    @pytest.fixture
    def event_queue(self):
        return queue.Queue()

    @pytest.fixture
    def event_adapter(self, event_queue):
        from agents.events.registry import EventRegistry
        registry = EventRegistry()
        return SwarmEventAdapter(
            event_queue=event_queue,
            event_registry=registry
        )

    def test_event_conversion_for_node_start(self, event_adapter):
        """노드 시작 이벤트 변환"""
        swarm_event = {
            "type": "multiagent_node_start",
            "node_id": "data_expert"
        }

        converted = event_adapter.convert_event(swarm_event)

        assert converted is not None
        assert converted.get("type") == "agent_status"

    def test_event_conversion_for_handoff(self, event_adapter):
        """핸드오프 이벤트 변환"""
        swarm_event = {
            "type": "multiagent_handoff",
            "from_node_ids": ["router"],
            "to_node_ids": ["data_expert"]
        }

        converted = event_adapter.convert_event(swarm_event)

        assert converted is not None

    def test_workflow_status_tracking(self, event_adapter):
        """워크플로우 상태 추적"""
        event_adapter.process_event({
            "type": "multiagent_node_start",
            "node_id": "router"
        })

        status = event_adapter.get_current_status()

        assert status is not None
        assert "current_agent" in status


class TestPerformanceAndTimeout:
    """성능 및 타임아웃 테스트"""

    def test_graph_config_timeout_values(self):
        """Graph 타임아웃 설정 확인"""
        from agents.multi_agent.shared_context import GraphConfig
        config = GraphConfig()

        assert config.execution_timeout == 900.0
        assert config.node_timeout == 300.0

    def test_polling_configuration(self):
        """폴링 설정 확인"""
        from agents.multi_agent.constants import (
            POLLING_INTERVAL_SECONDS,
            MAX_POLLING_ATTEMPTS
        )

        assert POLLING_INTERVAL_SECONDS == 5
        assert MAX_POLLING_ATTEMPTS == 5
        assert POLLING_INTERVAL_SECONDS * MAX_POLLING_ATTEMPTS == 25

    def test_result_row_limit(self):
        """결과 행 수 제한 확인"""
        from agents.multi_agent.constants import MAX_QUERY_RESULTS

        assert MAX_QUERY_RESULTS == 1000


class TestRAGAgentIntegration:
    """RAG Agent 통합 테스트"""

    @pytest.fixture
    def rag_agent(self):
        """RAG Agent 인스턴스"""
        from agents.multi_agent.rag_agent import RAGAgent
        return RAGAgent(
            model_id="test-model",
            opensearch_endpoint="https://test.opensearch.com",
            opensearch_index="test_index",
            opensearch_username="test_user",
            opensearch_password="test_pass"
        )

    @pytest.fixture
    def sample_context_with_rag(self):
        """RAG 결과가 포함된 분석 컨텍스트"""
        context = AnalysisContext(user_query="지난달 매출 상위 5개 상품")
        context.rag_enabled = True
        return context

    def test_rag_agent_registration(self, rag_agent):
        """RAG Agent가 올바르게 초기화되는지 확인"""
        agent = rag_agent.get_agent()

        assert agent is not None
        assert agent.name == "rag_agent"
        assert agent.model is not None

    def test_rag_search_and_context_save(self, sample_context_with_rag, rag_agent):
        """RAG 검색 및 컨텍스트 저장 테스트"""
        from agents.multi_agent.vector_search import SearchResult

        mock_results = [
            SearchResult(
                content="Table: sales_transactions\nColumns: product_id, revenue",
                score=0.9,
                metadata={"table": "sales_transactions", "database": "analytics"},
                source="sales_transactions.md"
            )
        ]

        with patch.object(
            rag_agent._search_service,
            'search',
            return_value=(mock_results, None)
        ):
            result = rag_agent.search_and_extract(
                "매출 상품",
                context=sample_context_with_rag
            )

            assert result["success"] is True
            assert len(result["results"]) == 1
            assert len(sample_context_with_rag.rag_results) == 1
            assert sample_context_with_rag.rag_results[0]["metadata"]["table"] == "sales_transactions"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
