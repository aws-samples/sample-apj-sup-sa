"""RAG Agent 에러 핸들링 테스트

작업 2.6: 검색 결과 처리 및 에러 핸들링 검증
- 빈 결과 처리 및 대안 제안
- 검색 실패 시 에러 처리
- 타임아웃 처리
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from agents.multi_agent.rag_agent import RAGAgent, SearchResult


class TestRAGAgentErrorHandling:
    """RAG Agent 에러 핸들링 테스트"""
    
    @pytest.fixture
    def rag_agent(self):
        """RAG Agent 인스턴스 생성"""
        agent = RAGAgent(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            opensearch_endpoint="https://test.opensearch.com",
            opensearch_index="test_index",
            opensearch_username="test_user",
            opensearch_password="test_pass"
        )
        # 테스트를 위해 RAG 활성화
        agent._rag_enabled = True
        return agent
    
    def test_empty_results_handling(self, rag_agent):
        """빈 결과 처리 및 대안 제안 테스트 (Requirements 1.4)"""
        query = "존재하지 않는 테이블"
        
        # 빈 결과 처리
        result = rag_agent.handle_empty_results(query)
        
        assert result["empty_results"] is True
        assert result["original_query"] == query
        assert "suggestions" in result
        assert len(result["suggestions"]) > 0
        assert "message" in result
        
        # 대안 제안 확인
        suggestions = result["suggestions"]
        assert isinstance(suggestions, list)
        for suggestion in suggestions:
            assert "type" in suggestion
            assert "suggestion" in suggestion
            assert "reason" in suggestion
    
    def test_alternative_suggestions_with_filters(self, rag_agent):
        """필터가 있을 때 대안 제안 테스트 (Requirements 1.4)"""
        query = "사용자 테이블"
        filters = {"database": "test_db", "table_name": "users"}
        
        suggestions = rag_agent.get_alternative_suggestions(query, filters)
        
        assert len(suggestions) > 0
        # 필터 제거 제안이 포함되어야 함
        filter_removal_suggestions = [
            s for s in suggestions if s["type"] == "remove_filters"
        ]
        assert len(filter_removal_suggestions) > 0
    
    def test_alternative_suggestions_types(self, rag_agent):
        """다양한 대안 제안 타입 테스트 (Requirements 1.4)"""
        query = "학생 성적 데이터"
        
        suggestions = rag_agent.get_alternative_suggestions(query)
        
        # 다양한 제안 타입 확인
        suggestion_types = {s["type"] for s in suggestions}
        
        # 최소한 2가지 이상의 제안 타입이 있어야 함
        assert len(suggestion_types) >= 2
        
        # 가능한 제안 타입들
        valid_types = {
            "split_keywords",
            "add_keyword",
            "wildcard",
            "browse_all",
            "remove_filters"
        }
        assert suggestion_types.issubset(valid_types)
    
    def test_search_failure_error_handling(self, rag_agent):
        """검색 실패 시 에러 처리 테스트 (Requirements 1.4)"""
        # OpenSearch 클라이언트 모킹
        mock_client = Mock()
        mock_client.search.side_effect = Exception("Connection failed")
        rag_agent._opensearch_client = mock_client
        
        # 임베딩 생성 모킹
        with patch.object(rag_agent, 'generate_embedding', return_value=[0.1] * 1024):
            results, error = rag_agent.search_documents("test query")
        
        # 에러가 반환되어야 함
        assert error is not None
        assert "실패" in error or "failed" in error.lower()
        assert len(results) == 0
    
    def test_rag_disabled_error_handling(self, rag_agent):
        """RAG 비활성화 상태 에러 처리 테스트 (Requirements 3.5)"""
        # RAG 비활성화
        rag_agent._rag_enabled = False
        
        results, error = rag_agent.search_documents("test query")
        
        # 에러 메시지가 반환되어야 함
        assert error is not None
        assert "비활성화" in error
        assert len(results) == 0
    
    def test_embedding_failure_error_handling(self, rag_agent):
        """임베딩 생성 실패 시 에러 처리 테스트 (Requirements 1.4)"""
        # 임베딩 생성 실패 모킹
        with patch.object(rag_agent, 'generate_embedding', return_value=None):
            results, error = rag_agent.search_documents("test query")
        
        # 에러 메시지가 반환되어야 함
        assert error is not None
        assert "임베딩" in error
        assert len(results) == 0
    
    def test_search_and_extract_with_errors(self, rag_agent):
        """통합 검색에서 에러 처리 테스트 (Requirements 1.4, 3.5)"""
        # 검색 실패 모킹
        with patch.object(
            rag_agent,
            'search_documents',
            return_value=([], "검색 실패")
        ):
            result = rag_agent.search_and_extract("test query")
        
        # 에러가 기록되어야 함
        assert result["success"] is False
        assert len(result["errors"]) > 0
        assert any("검색 실패" in err for err in result["errors"])
    
    def test_search_and_extract_partial_success(self, rag_agent):
        """부분 성공 시나리오 테스트 (Requirements 3.5)"""
        # 스키마 검색은 성공, 도메인 검색은 실패
        def mock_search(query, doc_type="schema", **kwargs):
            if doc_type == "schema":
                return [SearchResult(
                    content="test content",
                    score=0.9,
                    metadata={"table_name": "test_table"},
                    source="test_source"
                )], None
            else:
                return [], "도메인 검색 실패"
        
        with patch.object(rag_agent, 'search_documents', side_effect=mock_search):
            result = rag_agent.search_and_extract("test query")
        
        # 부분 성공 - 스키마 결과는 있지만 에러도 있음
        assert len(result["schema_results"]) > 0
        assert len(result["errors"]) > 0
        assert result["success"] is False  # 에러가 있으므로 전체적으로는 실패
    
    def test_non_retryable_error_detection(self, rag_agent):
        """재시도 불가능한 에러 감지 테스트 (Requirements 1.4)"""
        # 재시도 불가능한 에러들
        non_retryable_errors = [
            Exception("IndexNotFoundException"),
            Exception("AuthenticationException"),
            Exception("AuthorizationException"),
            Exception("illegal_argument_exception"),
        ]
        
        for error in non_retryable_errors:
            is_non_retryable = rag_agent._is_search_non_retryable_error(error)
            assert is_non_retryable is True, f"Expected {error} to be non-retryable"
        
        # 재시도 가능한 에러들
        retryable_errors = [
            Exception("Connection timeout"),
            Exception("Temporary network error"),
            Exception("Service unavailable"),
        ]
        
        for error in retryable_errors:
            is_non_retryable = rag_agent._is_search_non_retryable_error(error)
            assert is_non_retryable is False, f"Expected {error} to be retryable"
    
    def test_graceful_degradation(self, rag_agent):
        """Graceful degradation 테스트 (Requirements 3.5)"""
        # RAG 비활성화
        rag_agent.disable_rag("테스트 목적")
        
        assert rag_agent.is_rag_enabled() is False
        
        # 검색 시도 - 에러 없이 빈 결과 반환
        result = rag_agent.search_and_extract("test query")
        
        assert result["success"] is False
        assert result["rag_enabled"] is False
        assert len(result["errors"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
